#!/usr/bin/env python3
"""vlm_check.py — VLM二次确认, AVI抽帧送Qwen-VL识别warning原因.

用法:
  python vlm_check.py -i video.avi
  python vlm_check.py -i video.avi -o loc1_out/vlm_result.json
"""

import os, sys, argparse, base64, json
import cv2
import numpy as np
import requests

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
API_KEY = "sk-ws-H.EMDDIXY.ziK2.MEYCIQCJmpUpkfgUTm6fRd0Gaq5T2qMFJNsAR782lU4wBfuLTAIhAJKa0RKr46AhOXy2WnGEvYBd50D1WH42WtNDJcxmSihC"

PROMPT = """你是一个热成像监控系统的辅助判断系统。
你会看到一张热成像灰度图，画面中可能有异常区域。

请识别图中出现了什么物体，用中文回答。常见物体包括:
人、手、手臂、脚、气体泄漏、烟雾、
阀门、管道、设备、车辆、动物

请只回答JSON格式，不要有其他内容:
{"object": "物体中文名"}

如果画面中没有明显可识别物体，返回:
{"object": "未知"}"""


def encode_frame(gray):
    vmin, vmax = gray.min(), gray.max()
    vis = ((gray.astype("float32") - vmin) / max(vmax - vmin, 1) * 255).clip(0, 255).astype("uint8")
    _, buf = cv2.imencode(".png", vis)
    return base64.b64encode(buf).decode()


def ask_vlm(gray, model="qwen-vl-max", api_key=None):
    key = api_key or API_KEY
    if not key:
        raise RuntimeError("API key 未设置")
    b64 = encode_frame(gray)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "请识别图中有什么物体。"}
            ]}
        ],
        "temperature": 0.1, "max_tokens": 150,
    }
    r = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def frames_from_npz(path, start=0.0, step=0.1, count=None):
    data = np.load(path)
    frames = data["frames"]
    fps = float(data.get("fps", 25))
    total = len(frames)
    step_idx = max(1, int(step * fps))
    start_idx = int(start * fps)
    indices = list(range(start_idx, total, step_idx))
    if count:
        indices = indices[:count]
    return [(frames[i], fps) for i in indices if 0 <= i < total]


def frames_from_avi(path, step=0.1, count=None):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step_frames = max(1, int(step * fps))
    frames = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret or (count and len(frames) >= count):
            break
        if idx % step_frames == 0:
            gray = frame[:, :, 0] if frame.ndim == 3 else frame
            frames.append((gray, fps))
        idx += 1
    cap.release()
    return frames


def parse(text):
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(l for l in t.split("\n") if not l.startswith("```"))
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return {"object": "解析失败"}


def main():
    p = argparse.ArgumentParser(description="VLM二次确认 — AVI抽帧识别物体")
    p.add_argument("--input", "-i", required=True, help="AVI视频路径")
    p.add_argument("--step", "-s", type=float, default=0.1, help="抽帧间隔秒")
    p.add_argument("--max-frames", "-n", type=int, default=10, help="最大送检帧数")
    p.add_argument("--output", "-o", default=None, help="输出JSON路径")
    p.add_argument("--api-key", "-k", default=None)
    p.add_argument("--model", default="qwen-vl-max")
    args = p.parse_args()

    if not os.path.exists(args.input):
        # AVI不存在, 从同名的npz生成
        npz_path = args.input.rsplit(".", 1)[0] + ".npz"
        if os.path.exists(npz_path):
            print(f"AVI不存在, 从npz生成...")
            data = np.load(npz_path)
            frames_arr = data["frames"]
            n, h, w = frames_arr.shape
            vmin, vmax = frames_arr.min(), frames_arr.max()
            vis = ((frames_arr.astype(np.float32) - vmin) / max(vmax - vmin, 1) * 255).clip(0, 255).astype(np.uint8)
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            wrt = cv2.VideoWriter(args.input, fourcc, 25, (w, h), isColor=False)
            for f in vis:
                wrt.write(f)
            wrt.release()
        else:
            print(f"文件不存在: {args.input}"); sys.exit(1)

    is_npz = args.input.endswith(".npz")
    if is_npz:
        frames = frames_from_npz(args.input, step=args.step, count=args.max_frames)
    else:
        frames = frames_from_avi(args.input, step=args.step, count=args.max_frames)

    if not frames:
        print("无帧可抽"); sys.exit(1)

    print(f"输入: {os.path.basename(args.input)} 抽{len(frames)}帧\n")

    counts = {}
    for i, (gray, _fps) in enumerate(frames):
        print(f"[{i+1}/{len(frames)}] ", end="", flush=True)
        try:
            resp = ask_vlm(gray, args.model, args.api_key)
            content = resp["choices"][0]["message"]["content"]
            r = parse(content)
            obj = r.get("object", "?")
            print(f"-> {obj}")
            if obj and obj != "未知" and obj != "解析失败":
                counts[obj] = counts.get(obj, 0) + 1
        except Exception as e:
            print(f"-> 出错: {e}")

    if counts:
        ranked = sorted(counts.items(), key=lambda x: -x[1])
        conclusion = ranked[0][0]
        print(f"\n统计: {', '.join(f'{k}*{v}' for k, v in ranked)}")
        print(f"结论: {conclusion}")
    else:
        conclusion = "未知"
        print("\n结论: 未知")

    result = {"conclusion": conclusion, "counts": counts}
    if args.output:
        d = os.path.dirname(args.output)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"已保存: {args.output}")


if __name__ == "__main__":
    main()
