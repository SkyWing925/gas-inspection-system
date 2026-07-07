"""CV pipeline: online temporal variance (replaces batch FFT from video2).

Uses Welford's online algorithm with exponential decay to compute
per-pixel running variance over a sliding window. Captures gas
turbulence (2-10 Hz band) without requiring all frames in memory.
"""

import numpy as np

from ..config.schema import TemporalVarianceConfig


class TemporalVariance:
    """Online per-pixel temporal variance via exponentially-weighted Welford.

    Replaces the batch FFT from video2. Gas turbulence produces
    intensity fluctuations that show as elevated temporal variance.
    Window = 32 frames (1.28s at 25fps) captures fluctuations >= 0.78 Hz.
    EMA alpha = 0.05 extends the effective window for lower frequencies.
    """

    def __init__(self, config: TemporalVarianceConfig):
        self.window = config.window
        self.alpha = config.ema_alpha

        self._running_mean: np.ndarray | None = None
        self._running_m2: np.ndarray | None = None
        self._frame_count = 0

    def process(self, gray: np.ndarray) -> np.ndarray:
        """Return normalized temporal variance map (H, W) float32 [0, 1]."""
        f = gray.astype(np.float32)

        if self._running_mean is None:
            self._running_mean = f.copy()
            self._running_m2 = np.zeros_like(f)
            self._frame_count = 1
            return np.zeros_like(f)

        self._frame_count += 1

        # Welford update with exponential decay
        delta = f - self._running_mean
        self._running_mean += self.alpha * delta
        delta2 = f - self._running_mean
        self._running_m2 += self.alpha * (delta * delta2 - self._running_m2)

        # Variance = M2 (approximately, after stabilization)
        variance = self._running_m2.copy()

        # Normalize to [0, 1]
        # Wait a few frames for stats to stabilize
        if self._frame_count < 10:
            return np.zeros_like(f)

        vmax = variance.max()
        if vmax > 1e-6:
            variance /= vmax

        return np.clip(variance, 0, 1).astype(np.float32)

    def reset(self) -> None:
        """Reset state for a new video."""
        self._running_mean = None
        self._running_m2 = None
        self._frame_count = 0
