#!/usr/bin/env python3
"""热成像实时预览 + 网页按钮录制 — RDK X5 + MS210x"""
import cv2
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import threading
import time
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True

WIDTH, HEIGHT = 640, 480
PORT = 8080

lock = threading.Lock()
latest_frame = None
recording = False
record_buf = []

def cap_thread():
    global latest_frame, recording, record_buf
    cap = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
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

def save_frames(buf):
    """保存 npz + avi, 返回提示信息"""
    arr = np.array(buf, dtype=np.uint8)
    n, h, w = arr.shape
    ts = time.strftime("%Y%m%d_%H%M%S")

    fn_npz = os.path.join(SCRIPT_DIR, f"record_{ts}.npz")
    np.savez_compressed(fn_npz, frames=arr)
    mb_npz = os.path.getsize(fn_npz) / 1024 / 1024

    fn_avi = os.path.join(SCRIPT_DIR, f"record_{ts}.avi")
    vmin, vmax = arr.min(), arr.max()
    vis = ((arr.astype(np.float32) - vmin) / (vmax - vmin) * 255).clip(0, 255).astype(np.uint8)
    fourcc = cv2.VideoWriter_fourcc(*"XVID")
    wrt = cv2.VideoWriter(fn_avi, fourcc, 25, (w, h), isColor=False)
    for f in vis:
        wrt.write(f)
    wrt.release()
    mb_avi = os.path.getsize(fn_avi) / 1024 / 1024

    return f"{fn_npz}({mb_npz:.1f}MB) + {fn_avi}({mb_avi:.1f}MB)"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        global recording, record_buf
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{margin:0;background:#0d1117;text-align:center;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
img{{max-width:100vw;max-height:85vh}}
.btn{{display:inline-block;margin:8px 10px;padding:10px 28px;font-size:15px;font-weight:500;
  background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;cursor:pointer;
  transition:all 0.2s ease}}
.btn:hover{{background:#30363d;border-color:#484f58;color:#f0f6fc}}
.btn:active{{background:#484f58}}
.btn:disabled{{opacity:0.4;pointer-events:none}}
.btn-rec{{background:#1a3a2a;color:#7ee787;border-color:#238636}}
.btn-rec:hover{{background:#1f442f;color:#a5f3b4}}
.btn-rec.active{{background:#3d2100;color:#ffa657;border-color:#d29922;box-shadow:0 0 12px rgba(210,153,34,0.3)}}
#msg{{color:#7ee787;font-size:13px;margin:8px;letter-spacing:0.5px}}
</style></head><body>
<img src="/stream">
<div>
  <button class="btn" id="b1" onclick="timed(1)">1秒</button>
  <button class="btn" id="b10" onclick="timed(10)">10秒</button>
  <button class="btn" id="b30" onclick="timed(30)">30秒</button>
  <button class="btn" id="b60" onclick="timed(60)">60秒</button>
  <button class="btn btn-rec" id="btoggle" onclick="toggle_rec()">开始录制</button>
</div>
<div id="msg"></div>
<script>
var btnTimer=document.querySelectorAll('.btn:not(.btn-rec)');
var btnToggle=document.getElementById('btoggle');
var active=false;

function disableAll(){{
  btnTimer.forEach(function(b){{b.disabled=true}});
  btnToggle.disabled=true;
}}
function enableAll(){{
  btnTimer.forEach(function(b){{b.disabled=false}});
  btnToggle.disabled=false;
}}

function timed(d){{
  disableAll();
  var x=new XMLHttpRequest();
  x.open('GET','/record?d='+d);
  x.onload=function(){{enableAll(); document.getElementById('msg').innerText=x.responseText;}};
  x.onerror=function(){{enableAll();}};
  x.send();
}}

function toggle_rec(){{
  if(!active){{
    disableAll();
    btnToggle.disabled=false;
    btnToggle.innerText='停止录制';
    btnToggle.classList.add('active');
    var x=new XMLHttpRequest();
    x.open('GET','/record_start');
    x.onload=function(){{active=true;}};
    x.send();
  }}else{{
    btnToggle.disabled=true;
    btnToggle.innerText='保存中...';
    var x=new XMLHttpRequest();
    x.open('GET','/record_stop');
    x.onload=function(){{
      enableAll();
      btnToggle.innerText='开始录制';
      btnToggle.classList.remove('active');
      active=false;
      document.getElementById('msg').innerText=x.responseText;
    }};
    x.send();
  }}
}}
</script>
</body></html>""".encode())

        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            while True:
                with lock:
                    if latest_frame is None:
                        time.sleep(0.02)
                        continue
                    g = cv2.normalize(latest_frame, None, 0, 255, cv2.NORM_MINMAX)
                _, jpg = cv2.imencode(".jpg", g)
                try:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                    self.wfile.write(jpg.tobytes())
                    self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    break

        elif self.path.startswith("/record?") or self.path == "/record":
            qs = parse_qs(urlparse(self.path).query)
            dur = float(qs.get("d", ["1"])[0])
            if recording:
                self.send_error(409, "busy")
                return
            try:
                with lock:
                    recording = True
                    record_buf = []
                time.sleep(dur)
                with lock:
                    recording = False
                    buf = list(record_buf)
                    record_buf = []
                if not buf:
                    self.send_error(500, "no frames")
                    return
                msg = "录制完成: " + save_frames(buf)
                print(msg)
                self.send_response(200)
                self.send_header("Content-type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg.encode())
            except Exception as e:
                print(f"录制失败: {e}", file=sys.stderr)
                with lock:
                    recording = False
                    record_buf = []
                self.send_error(500, str(e))

        elif self.path == "/record_start":
            if recording:
                self.send_error(409, "busy")
                return
            with lock:
                recording = True
                record_buf = []
            print("开始录制(手动)")
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"started")

        elif self.path == "/record_stop":
            if not recording:
                self.send_error(409, "not recording")
                return
            try:
                with lock:
                    recording = False
                    buf = list(record_buf)
                    record_buf = []
                if not buf:
                    self.send_error(500, "no frames")
                    return
                msg = "录制完成: " + save_frames(buf)
                print(msg)
                self.send_response(200)
                self.send_header("Content-type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(msg.encode())
            except Exception as e:
                print(f"录制失败: {e}", file=sys.stderr)
                with lock:
                    recording = False
                    record_buf = []
                self.send_error(500, str(e))

if __name__ == "__main__":
    threading.Thread(target=cap_thread, daemon=True).start()
    srv = Server(("0.0.0.0", PORT), Handler)
    print(f"浏览器打开 http://192.168.127.10:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
