"""CV pipeline package."""

from .motion import MOG2Detector, AdaptiveFrameDiffer, MotionDetector
from .optical_flow import SparseOpticalFlow
from .temporal_variance import TemporalVariance
from .postprocess import fuse_channels, PostProcessor

__all__ = [
    "MOG2Detector", "AdaptiveFrameDiffer", "MotionDetector",
    "SparseOpticalFlow", "TemporalVariance",
    "fuse_channels", "PostProcessor",
]
