"""CV pipeline: MOG2 background subtraction + adaptive frame differencing."""

import cv2
import numpy as np

from ..config.schema import MOG2Config, FrameDiffConfig


class MOG2Detector:
    """MOG2 background subtraction for thermal video (from video2, tuned for embedded)."""

    def __init__(self, config: MOG2Config):
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=config.history,
            varThreshold=config.var_threshold,
            detectShadows=config.detect_shadows,
        )

    def process(self, gray: np.ndarray) -> np.ndarray:
        """Return foreground mask (H, W) float32 [0, 255]."""
        fg = self.bg_subtractor.apply(gray)
        return fg.astype(np.float32)


class AdaptiveFrameDiffer:
    """Adaptive frame differencing with self-calibrating threshold.

    Instead of a fixed threshold (video3), this tracks running mean/std
    of the difference image to adapt to ambient temperature drift.
    """

    def __init__(self, config: FrameDiffConfig):
        self.base_threshold = config.base_threshold
        self.std_multiplier = config.std_multiplier
        self._prev_frame: np.ndarray | None = None
        self._running_mean = 0.0
        self._running_std = 1.0
        self._initialized = False

    def process(self, gray: np.ndarray) -> np.ndarray:
        """Return binary motion mask (H, W) uint8 {0, 255}."""
        if self._prev_frame is None:
            self._prev_frame = gray.astype(np.float32)
            return np.zeros_like(gray, dtype=np.uint8)

        diff = np.abs(gray.astype(np.float32) - self._prev_frame)
        self._prev_frame = gray.astype(np.float32)

        # Update running statistics of the difference image
        mean_diff = float(diff.mean())
        std_diff = float(diff.std())

        alpha = 0.1
        if not self._initialized:
            self._running_mean = mean_diff
            self._running_std = std_diff
            self._initialized = True
        else:
            self._running_mean = alpha * mean_diff + (1 - alpha) * self._running_mean
            self._running_std = alpha * std_diff + (1 - alpha) * self._running_std

        # Adaptive threshold
        thresh = max(self.base_threshold,
                     self._running_mean + self.std_multiplier * self._running_std)
        mask = (diff > thresh).astype(np.uint8) * 255
        return mask


class MotionDetector:
    """Combined motion detection: MOG2 + adaptive frame differencing (union)."""

    def __init__(self, mog2_config: MOG2Config, diff_config: FrameDiffConfig):
        self.mog2 = MOG2Detector(mog2_config)
        self.differ = AdaptiveFrameDiffer(diff_config)

    def process(self, gray: np.ndarray) -> np.ndarray:
        """Return combined motion map (H, W) float32 [0, 1], union of both detectors."""
        mog2_mask = self.mog2.process(gray)
        diff_mask = self.differ.process(gray)

        # Union: pixel is "moving" if either detector says so
        combined = np.maximum(mog2_mask / 255.0, diff_mask.astype(np.float32) / 255.0)
        return combined
