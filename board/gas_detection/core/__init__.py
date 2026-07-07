"""Core package — frame source, pipeline orchestrator, result types."""
from .frame_source import FrameSource, CameraSource, VideoFileSource, NpzFileSource
from .result import CVFrameResult, DLFrameResult, FrameResult, DetectionReport

__all__ = [
    "FrameSource", "CameraSource", "VideoFileSource", "NpzFileSource",
    "CVFrameResult", "DLFrameResult", "FrameResult", "DetectionReport",
]
