#!/usr/bin/env python3
"""run.py — unified gas detection CLI entry point.

Compatible with video3 main.py calling convention:
    python3 run.py --input inspect_p01.npz --loc 1 --output loc1_out

Also supports YAML config + CLI overrides:
    python3 run.py --config gas_detection/config/default.yaml --input data.npz

Usage modes:
    offline:  python3 run.py --input recording.npz --loc 1
    offline:  python3 run.py --input recording.avi --loc 1
    realtime: python3 run.py --mode realtime --duration 30 --loc 1
"""

import os
import sys
import time
import signal
import argparse
import logging

# Add parent to path for direct invocation
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gas_detection.config.schema import PipelineConfig
from gas_detection.core.pipeline import GasDetectionPipeline


def setup_logging(level: str = "INFO") -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="统一气体泄漏检测 — CV + DL 嵌入式管线 (RDK X5)",
    )
    # Input
    parser.add_argument("--input", "-i", default=None,
                        help="输入文件 (.npz / .avi)")
    parser.add_argument("--mode", default="offline",
                        choices=["offline", "realtime", "bg_subtract", "frame_diff"],
                        help="处理模式 (bg_subtract/frame_diff 等同 offline，兼容旧调用)")
    parser.add_argument("--duration", type=float, default=None,
                        help="实时模式录制时长(秒)")

    # Config
    parser.add_argument("--config", "-c", default=None,
                        help="YAML 配置文件路径")

    # Location & output
    parser.add_argument("--loc", "--location", type=int, default=1,
                        dest="location", help="巡检点编号 (默认 1)")
    parser.add_argument("--output", "-o", default=None,
                        help="输出目录")

    # Video3 compatibility
    parser.add_argument("--npz", default=None,
                        help="npz输入 (兼容video3的--npz参数)")
    parser.add_argument("--npz-start", type=float, default=2.0)
    parser.add_argument("--npz-step", type=float, default=0.2)
    parser.add_argument("--npz-count", type=int, default=None)
    parser.add_argument("--npz-fps", type=float, default=None)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--background", type=str, default=None)
    parser.add_argument("--frame-interval", type=int, default=5)

    # Overrides
    parser.add_argument("--no-video", action="store_true",
                        help="不保存视频输出")
    parser.add_argument("--no-dl", action="store_true",
                        help="禁用深度学习")
    parser.add_argument("--no-cv", action="store_true",
                        help="禁用经典CV")

    args = parser.parse_args()

    # Resolve input path: --npz takes precedence (video3 compat), then --input
    if args.npz:
        args.input = args.npz
    elif args.input is None and args.mode in ("offline", "bg_subtract", "frame_diff"):
        parser.error("离线模式需要 --input 或 --npz")

    # Normalize mode: bg_subtract/frame_diff are aliases for offline (backward compat)
    if args.mode in ("bg_subtract", "frame_diff"):
        args.mode = "offline"

    return args


def main():
    args = parse_args()
    setup_logging("INFO")
    logger = logging.getLogger("gas_detection")

    print("=" * 55)
    print("  气体泄漏检测 — 统一管线 (CV + DL)")
    print("=" * 55)

    # Load config
    if args.config and os.path.exists(args.config):
        config = PipelineConfig.from_yaml(args.config)
    else:
        # Default config
        default_yaml = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "gas_detection", "config", "default.yaml",
        )
        if os.path.exists(default_yaml):
            config = PipelineConfig.from_yaml(default_yaml)
        else:
            config = PipelineConfig()  # pure dataclass defaults

    # Apply CLI overrides
    if args.no_video:
        config.output.save_video = False
    if args.no_dl:
        config.dl.enabled = False
    if args.no_cv:
        config.cv.enabled = False
    if args.model:
        config.dl.model_path = args.model
    if args.frame_interval:
        config.dl.frame_interval = args.frame_interval

    # Determine output directory — default to E:\qiansai\locN_out
    if args.output:
        output_dir = args.output
    else:
        output_dir = os.path.join(
            r"E:\qiansai",
            f"loc{args.location}_out",
        )

    print(f"  输入:     {args.input or '(camera)'}")
    print(f"  模式:     {args.mode}")
    print(f"  位置:     {args.location}")
    print(f"  输出:     {output_dir}")
    print(f"  CV:       {'启用' if config.cv.enabled else '禁用'}")
    print(f"  DL:       {'启用' if config.dl.enabled else '禁用'}")
    print(f"  视频:     {'是' if config.output.save_video else '否'}")
    print()

    # Initialize pipeline
    pipeline = GasDetectionPipeline(config)

    # Run
    t0 = time.time()

    if args.mode == "offline":
        report = pipeline.run_offline(
            input_path=args.input,
            output_dir=output_dir,
            location=args.location,
        )
    else:
        report = pipeline.run_realtime(
            duration_sec=args.duration,
            output_dir=output_dir,
            location=args.location,
        )

    elapsed = time.time() - t0

    # Print result — format: loc, result, msg
    print()
    print(f"loc    = {report.location}")
    print(f"result = \"{report.result}\"")
    print(f"msg    = \"{report.summary}\"")
    print(f"(耗时:  {elapsed:.1f}s)")
    print()

    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *a: (print("\n中断"), sys.exit(0)))
    sys.exit(main())
