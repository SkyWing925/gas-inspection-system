#!/usr/bin/env python3
"""
main.py — 一键巡检

启动 → 连接相机+小车 → 按路径移动+定点拍摄 → 保存npz

用法:
  python3 main.py --route "f:0.5, s, t:-90, s, f:0.3, s"
  python3 main.py --route "f:0.5, s, f:0.3, s" --dry-run

命令:
  f:距离(m)  — 前进
  t:角度(°)  — 转弯 (正左负右)
  s          — 停车 + 拍摄1秒
"""

import os
import sys

# 抑制 OpenCV C++ 层日志
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
os.environ["OPENCV_IO_MAX_RETRIES"] = "0"

import json
import time
import math
import signal
import argparse
import subprocess
import shutil
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import cv2
import numpy as np

try:
    import serial
except ImportError:
    serial = None
    print("[WARN] pyserial 未安装, 小车控制不可用"); sys.exit(1)


# ============================================================
# 配置
# ============================================================
CAR_DEVICE = "/dev/ttyUSB1"
CAR_BAUD = 115200
CAM_DEVICE = "/dev/video0"
CAM_W, CAM_H = 640, 480
FPS = 25
DEFAULT_SPEED = 200
DEFAULT_RADIUS = 500
RECORD_SECS = 1.0
PREVIEW_PORT = 8080

# ============================================================
# 全局状态
# ============================================================
lock = threading.Lock()
latest_frame = None
recording = False
record_buf = []
saved_files = []
USE_HTTP_REC = False  # True=通过preview.py HTTP录制, False=直接采相机


# ============================================================
# 相机线程
# ============================================================
def camera_loop():
    global latest_frame, recording, record_buf
    cap = cv2.VideoCapture(CAM_DEVICE, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
    print(f"[CAM] {CAM_DEVICE} {CAM_W}x{CAM_H}")
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        if frame.ndim == 3 and frame.shape[2] == 2:
            gray = frame[:, :, 0]
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        with lock:
            latest_frame = gray
            if recording:
                record_buf.append(gray.copy())


# ============================================================
# HTTP 预览 (可选)
# ============================================================
class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path == "/":
            self._page()
        elif self.path == "/stream":
            self._stream()
        elif self.path == "/snap":
            self._snap()

    def _page(self):
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{margin:0;background:#0d1117;text-align:center;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}}
img{{max-width:100vw;max-height:85vh}}
.info{{color:#8b949e;font-size:13px;margin:6px}}
</style></head><body>
<img src="/stream">
<div class="info">已录: {len(saved_files)} 个 | <a href="/snap" style="color:#7ee787">手动抓拍</a></div>
</body></html>"""
        self._respond(html.encode(), "text/html")

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        while True:
            with lock:
                if latest_frame is None:
                    time.sleep(0.02); continue
                g = cv2.normalize(latest_frame, None, 0, 255, cv2.NORM_MINMAX)
            _, jpg = cv2.imencode(".jpg", g)
            try:
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                self.wfile.write(jpg.tobytes()); self.wfile.write(b"\r\n")
            except: break

    def _snap(self):
        with lock:
            if latest_frame is None:
                self._respond(b"no frame", "text/plain"); return
            g = latest_frame.copy()
        fn = f"snap_{time.strftime('%Y%m%d_%H%M%S')}.png"
        cv2.imwrite(fn, g)
        saved_files.append(fn)
        self._respond(f"ok {fn}".encode(), "text/plain")

    def _respond(self, data, ct):
        self.send_response(200)
        self.send_header("Content-type", ct)
        self.end_headers()
        self.wfile.write(data)


# ============================================================
# 小车控制
# ============================================================
def make_frame(vx=0, vy=0, vz=0):
    def s16(v):
        v = max(-32768, min(32767, int(v)))
        return [(v >> 8) & 0xFF, v & 0xFF]
    buf = bytearray(11)
    buf[0] = 0x7B; buf[1] = buf[2] = 0x00
    vx_b = s16(vx); buf[3], buf[4] = vx_b[0], vx_b[1]
    vy_b = s16(vy); buf[5], buf[6] = vy_b[0], vy_b[1]
    vz_b = s16(vz); buf[7], buf[8] = vz_b[0], vz_b[1]
    bcc = 0
    for i in range(9): bcc ^= buf[i]
    buf[9] = bcc & 0xFF; buf[10] = 0x7D
    return bytes(buf)


class Car:
    def __init__(self):
        self.ser = None

    def open(self):
        self.ser = serial.Serial(CAR_DEVICE, CAR_BAUD, timeout=0.1)
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        print(f"[CAR] {CAR_DEVICE}")

    def close(self):
        self._stop()
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _tx(self, vx=0, vy=0, vz=0):
        self.ser.write(make_frame(vx, vy, vz))
        self.ser.flush()

    def _stop(self, n=15):
        for _ in range(n):
            self._tx(0, 0, 0); time.sleep(0.03)

    def forward(self, dist_m, speed=DEFAULT_SPEED):
        dur = dist_m * 1000 / speed
        print(f"  [CAR] → 前进 {dist_m:.2f}m  {dur:.1f}s")
        t0 = time.time()
        while time.time() - t0 < dur:
            self._tx(speed, 0, 0); time.sleep(0.05)
        self._stop()

    def turn(self, deg, radius=DEFAULT_RADIUS, speed=DEFAULT_SPEED):
        sign = 1 if deg >= 0 else -1
        r = radius * sign
        arc = radius * math.radians(abs(deg))
        dur = arc / speed
        vz = int(speed * 1000 / r)
        print(f"  [CAR] {'左' if deg>=0 else '右'}转 {abs(deg):.0f}°  {dur:.1f}s")
        t0 = time.time()
        while time.time() - t0 < dur:
            self._tx(speed, 0, vz); time.sleep(0.05)
        self._stop()


# ============================================================
# 录制 (直接取相机缓冲, 不经过 HTTP)
# ============================================================
def record_http(secs, output_dir, label=""):
    """通过 preview.py HTTP 接口录制 (发完即忘, 然后去目录里找文件)"""
    import re
    url = f"http://localhost:{PREVIEW_PORT}/record?d={secs}"
    preview_dir = os.path.expanduser("~/mobilenet_test")

    # 记录录制前的文件列表
    before = set()
    for f in os.listdir(preview_dir):
        if f.startswith("record_") and f.endswith(".npz"):
            before.add(f)

    # 发 HTTP 请求 (不等响应, 后台线程)
    def _fire():
        try:
            urllib.request.urlopen(url, timeout=secs + 10)
        except Exception:
            pass
    threading.Thread(target=_fire, daemon=True).start()

    # 轮询等文件出现 + 大小稳定 (解决长录制文件未写完问题)
    waited = 0
    max_wait = secs + 30
    found_file = None
    while waited < max_wait:
        time.sleep(0.5)
        waited += 0.5
        after = set()
        for f in os.listdir(preview_dir):
            if f.startswith("record_") and f.endswith(".npz"):
                after.add(f)
        new_files = after - before
        if new_files:
            found_file = max(new_files)  # 最新的那个
            fpath = os.path.join(preview_dir, found_file)
            # 等文件大小稳定：连续两次采样一致才算写完
            s1 = os.path.getsize(fpath) if os.path.exists(fpath) else 0
            time.sleep(0.3)
            s2 = os.path.getsize(fpath) if os.path.exists(fpath) else 0
            if s1 == s2 and s1 > 0:
                break
    if not found_file:
        print(f"  [REC] HTTP: 未找到新npz文件 (preview_dir={preview_dir}, 等了{waited:.0f}s)")
        return None, None

    # 取最新的一个
    base = found_file.replace(".npz", "")
    src_npz = os.path.join(preview_dir, f"{base}.npz")
    src_avi = os.path.join(preview_dir, f"{base}.avi")

    ts = time.strftime("%Y%m%d_%H%M%S")
    dst_npz = os.path.join(output_dir, f"inspect_{label}_{ts}.npz")
    dst_avi = os.path.join(output_dir, f"inspect_{label}_{ts}.avi")

    for src, dst in [(src_npz, dst_npz), (src_avi, dst_avi)]:
        if os.path.exists(src):
            shutil.move(src, dst)
    print(f"  [REC] HTTP → {os.path.basename(dst_npz)}")
    return dst_npz, dst_avi


def record(secs, output_dir, label=""):
    global recording, record_buf, saved_files
    if USE_HTTP_REC:
        return record_http(secs, output_dir, label)
    with lock:
        recording = True
        record_buf = []
    time.sleep(secs)
    with lock:
        recording = False
        buf = list(record_buf)
        record_buf = []

    if not buf:
        print("  [REC] 无帧!")
        return None, None

    arr = np.array(buf, dtype=np.uint8)
    n, h, w = arr.shape
    ts = time.strftime("%Y%m%d_%H%M%S")
    base = f"inspect_{label}_{ts}" if label else f"inspect_{ts}"

    # npz (原始)
    fn_npz = os.path.join(output_dir, f"{base}.npz")
    np.savez_compressed(fn_npz, frames=arr, fps=FPS)
    mb_npz = os.path.getsize(fn_npz) / 1024 / 1024

    # avi (回放)
    fn_avi = os.path.join(output_dir, f"{base}.avi")
    vmin, vmax = arr.min(), arr.max()
    vis = ((arr.astype(np.float32) - vmin) / max(vmax - vmin, 1) * 255).clip(0, 255).astype(np.uint8)
    fourcc = cv2.VideoWriter_fourcc(*"XVID")
    wrt = cv2.VideoWriter(fn_avi, fourcc, 25, (w, h), isColor=False)
    for f in vis:
        wrt.write(f)
    wrt.release()
    mb_avi = os.path.getsize(fn_avi) / 1024 / 1024

    saved_files.extend([fn_npz, fn_avi])
    print(f"  [REC] {os.path.basename(fn_npz)} ({mb_npz:.1f}MB) + {os.path.basename(fn_avi)} ({mb_avi:.1f}MB)")
    return fn_npz, fn_avi


# ============================================================
# 路径解析 & 执行
# ============================================================
def parse_route(s):
    steps = []
    for p in s.split(","):
        p = p.strip()
        if not p: continue
        if ":" in p:
            c, v = p.split(":", 1)
            steps.append((c.strip().lower(), float(v.strip())))
        else:
            steps.append((p.strip().lower(), None))
    return steps


def run_route(route_str, speed, radius, output_dir):
    steps = parse_route(route_str)
    os.makedirs(output_dir, exist_ok=True)

    car = Car()
    car.open()

    stop_n = 0
    try:
        for i, (cmd, val) in enumerate(steps):
            print(f"\n[{i+1}/{len(steps)}] ", end="")

            if cmd == "f":
                car.forward(val or 1.0, speed)
            elif cmd == "t":
                car.turn(val or 90, radius, speed)
            elif cmd == "s":
                stop_n += 1
                rec_secs = val if val else RECORD_SECS
                car._stop()
                time.sleep(1.0)  # 拍摄前稳1秒
                record(rec_secs, output_dir, f"p{stop_n:02d}")
                time.sleep(1.0)  # 拍摄后稳1秒
            else:
                print(f"未知命令: {cmd}")
    finally:
        car.close()

    print(f"\n{'='*50}")
    print(f"完成! {stop_n}个拍摄点 → {output_dir}/")
    for f in sorted(os.listdir(output_dir)):
        print(f"  {f}")

    return output_dir, stop_n


# ============================================================
# 检测: 对每个 npz 运行 run.py
# ============================================================
def run_detection(output_dir, script_dir):
    """找到output_dir中所有npz (按文件名排序), 逐一检测 (各npz自建背景)"""
    npz_files = sorted([
        f for f in os.listdir(output_dir) if f.endswith(".npz")
    ])
    if not npz_files:
        print("\n[检测] 无npz文件")
        return

    print(f"\n{'='*50}")
    print(f"检测: {len(npz_files)} 个拍摄点")

    all_output = []

    for i, fn in enumerate(npz_files, 1):
        npz_path = os.path.join(output_dir, fn)
        loc_dir = os.path.join(output_dir, f"loc{i}_out")
        loc_dir_abs = os.path.abspath(loc_dir)

        cmd = [
            "python3", os.path.join(script_dir, "run.py"),
            "--npz", os.path.abspath(npz_path),
            "--mode", "bg_subtract",
            "--npz-start", "0",
            "--npz-step", "0.1",
            "--loc", str(i),
            "--output", loc_dir_abs,
        ]

        print(f"\n[loc={i}] {fn}")
        try:
            proc = subprocess.run(cmd, text=True,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  cwd=script_dir, timeout=120,
                                  env={**os.environ, "OPENCV_LOG_LEVEL": "ERROR",
                                       "OPENCV_IO_MAX_RETRIES": "0"})
        except subprocess.TimeoutExpired:
            print(f"  [FAIL] 超时")
            continue

        # 提取最后3行 (若失败则打印stderr)
        lines = [l for l in proc.stdout.strip().split("\n") if l.strip()]
        tail = lines[-3:] if len(lines) >= 3 else lines
        if proc.returncode != 0:
            err = proc.stderr.strip()
            if err:
                tail = [l for l in err.split("\n") if l.strip()][-5:]
                print(f"  [ERR] run.py返回码={proc.returncode}:")
                for l in tail:
                    print(f"  {l}")
        all_output.append((i, tail))

        for l in tail:
            print(f"  {l}")

        # VLM二次确认: warning时调vlm_check (avi不存在会自动从npz生成)
        is_warning = any('result = "warning"' in l for l in tail)
        if is_warning:
            avi_path = npz_path.rsplit(".", 1)[0] + ".avi"
            vlm_out = os.path.join(loc_dir_abs, "vlm_result.json")
            print(f"  [VLM] 二次确认...")
            vlm_cmd = [
                "python3", os.path.join(script_dir, "vlm_check.py"),
                "-i", os.path.abspath(avi_path),
                "-o", vlm_out,
                "--step", "0.1", "--max-frames", "10",
            ]
            try:
                vlm_proc = subprocess.run(vlm_cmd, text=True,
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE,
                                          cwd=script_dir, timeout=60)
                vlm_conclusion = ""
                for vl in vlm_proc.stdout.strip().split("\n"):
                    if vl.startswith("结论:"):
                        print(f"  [VLM] {vl}")
                        vlm_conclusion = vl.replace("结论:", "").strip()
                # 更新result.json的msg, 让dashboard直接读到VLM结果
                if vlm_conclusion:
                    result_json = os.path.join(loc_dir_abs, "result.json")
                    if os.path.exists(result_json):
                        with open(result_json) as f:
                            rj = json.load(f)
                        if vlm_conclusion == "未知":
                            rj["summary"] = f"CV检测到信号, VLM无法识别物体"
                        else:
                            rj["summary"] = f"VLM判断: {vlm_conclusion}"
                        with open(result_json, "w") as f:
                            json.dump(rj, f, ensure_ascii=False)
            except Exception as e:
                print(f"  [VLM] 失败: {e}")

        # 移动 leak_max.png
        leak_src = os.path.join(script_dir, "leak_max.png")
        if os.path.exists(leak_src):
            dst = os.path.join(output_dir, f"loc{i}_leak_max.png")
            shutil.move(leak_src, dst)

    # 合并输出
    print(f"\n{'='*40}")
    print("汇总检测结果")
    print(f"{'='*40}")
    for i, lines in all_output:
        for l in lines:
            print(l)


# ============================================================
# 入口
# ============================================================
def main():
    global RECORD_SECS
    script_dir = os.path.dirname(os.path.abspath(__file__))

    parser = argparse.ArgumentParser(description="一键巡检 + 检测")
    parser.add_argument("--route", required=True,
                        help='路径, 例: "f:0.5, s, t:90, s, f:0.3, s"')
    parser.add_argument("--speed", type=int, default=DEFAULT_SPEED)
    parser.add_argument("--radius", type=int, default=DEFAULT_RADIUS)
    parser.add_argument("--record-secs", type=float, default=RECORD_SECS)
    parser.add_argument("--output", default="inspections")
    parser.add_argument("--detect", action="store_true",
                        help="拍摄完成后自动运行检测")
    parser.add_argument("--no-preview", action="store_true",
                        help="不启动网页预览")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    RECORD_SECS = args.record_secs

    # 打印路径
    print("=" * 50)
    print("巡检路径:")
    sn = 0
    for cmd, val in parse_route(args.route):
        if cmd == "f":   print(f"  → 前进 {val:.2f}m")
        elif cmd == "t": print(f"  → {'左' if val>=0 else '右'}转 {abs(val):.0f}°")
        elif cmd == "s":
            sn += 1
            secs = val if val else RECORD_SECS
            print(f"  ■ 停车拍摄#{sn} (停1s→拍{secs}s→停1s)")
    print("=" * 50)

    if args.dry_run:
        print("(dry-run)")
        return

    # 检测 preview 端口
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_free = s.connect_ex(("127.0.0.1", PREVIEW_PORT)) != 0
    s.close()

    global USE_HTTP_REC
    if not port_free:
        USE_HTTP_REC = True
        print(f"[PREVIEW] 复用已有服务 (端口{PREVIEW_PORT})")
    else:
        # 启动相机 + HTTP预览
        threading.Thread(target=camera_loop, daemon=True).start()
        time.sleep(0.8)
        if not args.no_preview:
            srv = Server(("0.0.0.0", PREVIEW_PORT), Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            ip = socket.gethostbyname(socket.gethostname())
            print(f"[PREVIEW] http://{ip}:{PREVIEW_PORT}")

    # 跑巡检
    out_dir, _ = run_route(args.route, args.speed, args.radius, args.output)

    # 自动检测 (每个npz自建背景, 无需预先--background)
    if args.detect:
        run_detection(out_dir, script_dir)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *a: (print("\n中断"), sys.exit(0)))
    main()
