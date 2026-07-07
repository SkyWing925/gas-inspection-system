"""Detection result data classes."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from datetime import datetime


@dataclass
class CVFrameResult:
    """Per-frame classical CV output."""
    frame_idx: int
    fused_map: "np.ndarray"           # (H, W) float32 clean fused heatmap
    binary_mask: "np.ndarray"         # (H, W) uint8 {0, 255}
    fused_max: float                  # max fused value
    fused_mean: float                 # mean fused value
    roi_count: int                    # number of candidate ROIs
    roi_boxes: List[Tuple[int, int, int, int]]  # (x, y, w, h) at CV resolution
    flow_pt_count: int = 0            # tracked optical-flow points inside ROIs (0 if no ROIs)


@dataclass
class DLFrameResult:
    """Per-frame DL classification output (only on sampled frames)."""
    frame_idx: int
    has_classification: bool
    class_name: str = "normal"        # "leak" | "normal"
    leak_prob: float = 0.0
    normal_prob: float = 0.0
    union_bbox: Optional[Tuple[int, int, int, int]] = None  # at original resolution


@dataclass
class FrameResult:
    """Combined per-frame result."""
    cv: Optional[CVFrameResult] = None
    dl: Optional[DLFrameResult] = None


@dataclass
class DetectionReport:
    """Final aggregated detection report for one location."""
    result: str                       # "normal" | "warning" | "danger"
    location: int
    cv: dict = field(default_factory=dict)
    dl: dict = field(default_factory=dict)
    summary: str = ""
    ts: str = field(default_factory=lambda: datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'))

    def to_dict(self) -> dict:
        return {
            "result": self.result,
            "location": self.location,
            "cv": self.cv,
            "dl": self.dl,
            "summary": self.summary,
            "ts": self.ts,
        }
