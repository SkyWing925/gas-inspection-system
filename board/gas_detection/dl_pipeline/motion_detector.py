"""DL pipeline Layer 1: Motion detection for DL branch.

From video3's layer1_motion — frame differencing or background subtraction
to trigger ROI extraction and classification.
"""

import cv2
import numpy as np

from ..config.schema import DLMotionConfig


class MotionDetector:
    """Fast motion detection for DL branch gating.

    Two modes:
    - "frame_diff": absolute difference between consecutive frames
    - "bg_subtract": iterative median background subtraction (from video3)
    """

    def __init__(self, config: DLMotionConfig, background: np.ndarray | None = None):
        self.mode = config.mode
        self.diff_threshold = config.diff_threshold
        self.min_motion_pixels = config.min_motion_pixels
        self.morph_kernel = config.morph_kernel
        self.background = background
        self._prev_frame: np.ndarray | None = None

    def process(self, gray: np.ndarray) -> tuple[bool, np.ndarray]:
        """Return (has_motion: bool, motion_mask: np.ndarray uint8)."""
        h, w = gray.shape

        if self.mode == "bg_subtract" and self.background is not None:
            diff = cv2.absdiff(gray, self.background)
        elif self.mode == "frame_diff":
            if self._prev_frame is None:
                self._prev_frame = gray.copy()
                return False, np.zeros((h, w), dtype=np.uint8)
            diff = cv2.absdiff(gray, self._prev_frame)
            self._prev_frame = gray.copy()
        else:
            # bg_subtract with no background provided: fall back to frame_diff
            if self._prev_frame is None:
                self._prev_frame = gray.copy()
                return False, np.zeros((h, w), dtype=np.uint8)
            diff = cv2.absdiff(gray, self._prev_frame)
            self._prev_frame = gray.copy()

        # Threshold
        _, mask = cv2.threshold(diff, self.diff_threshold, 255, cv2.THRESH_BINARY)

        # Morphological close to fill gaps
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (self.morph_kernel, self.morph_kernel),
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        motion_pixels = np.count_nonzero(mask)
        has_motion = motion_pixels >= self.min_motion_pixels

        return has_motion, mask
