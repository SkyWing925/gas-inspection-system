"""CV pipeline: sparse Lucas-Kanade optical flow + divergence grid.

Replaces Farneback dense OF from video2. LK on 200 Shi-Tomasi corners
gives 100x speedup while preserving gas motion signal.
"""

import cv2
import numpy as np

from ..config.schema import OpticalFlowConfig


class SparseOpticalFlow:
    """Sparse LK optical flow with divergence estimation on a coarse grid."""

    def __init__(self, config: OpticalFlowConfig, cv_scale: float = 0.5):
        self.max_corners = config.max_corners
        self.quality_level = config.quality_level
        self.min_distance = config.min_distance
        self.lk_winsize = tuple(config.lk_winsize)
        self.grid_w = config.flow_grid_w
        self.grid_h = config.flow_grid_h
        self.cv_scale = cv_scale

        self._prev_gray: np.ndarray | None = None
        self._prev_corners: np.ndarray | None = None

        # Expose tracked points and vectors for downstream modules
        self.tracked_positions: np.ndarray | None = None   # (N, 2) — (x, y)
        self.tracked_vectors: np.ndarray | None = None     # (N, 2) — (dx, dy)

    def process(self, gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (magnitude_map, divergence_map) as (H, W) float32 normalized [0,1]."""
        h, w = gray.shape

        if self._prev_gray is None:
            self._prev_gray = gray.copy()
            # Detect initial corners
            self._prev_corners = cv2.goodFeaturesToTrack(
                gray, maxCorners=self.max_corners,
                qualityLevel=self.quality_level,
                minDistance=self.min_distance,
            )
            return np.zeros((h, w), dtype=np.float32), np.zeros((h, w), dtype=np.float32)

        # Detect new corners and merge with tracked ones
        new_corners = cv2.goodFeaturesToTrack(
            gray, maxCorners=self.max_corners,
            qualityLevel=self.quality_level,
            minDistance=self.min_distance,
        )

        # If we have previous corners, track them with LK
        if self._prev_corners is not None and len(self._prev_corners) > 0:
            next_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                self._prev_gray, gray, self._prev_corners, None,
                winSize=self.lk_winsize,
                maxLevel=3,
            )
            # Keep only successfully tracked points
            valid = status.flatten() == 1
            tracked_prev = self._prev_corners[valid].reshape(-1, 2)
            tracked_next = next_pts[valid].reshape(-1, 2)

            if len(tracked_prev) > 0:
                # Flow vectors
                flow_vectors = tracked_next - tracked_prev  # (N, 2)

                # Expose tracked points/vectors for downstream modules
                self.tracked_positions = tracked_prev.astype(np.float32)
                self.tracked_vectors = flow_vectors.astype(np.float32)

                # Magnitude per point
                magnitudes = np.sqrt(flow_vectors[:, 0] ** 2 + flow_vectors[:, 1] ** 2)

                # Bin flow vectors into coarse grid for divergence
                mag_map, div_map = self._grid_interpolate(
                    tracked_prev, flow_vectors, magnitudes, h, w,
                )
            else:
                mag_map = np.zeros((h, w), dtype=np.float32)
                div_map = np.zeros((h, w), dtype=np.float32)
                self.tracked_positions = None
                self.tracked_vectors = None
        else:
            mag_map = np.zeros((h, w), dtype=np.float32)
            div_map = np.zeros((h, w), dtype=np.float32)
            self.tracked_positions = None
            self.tracked_vectors = None

        # Update state
        self._prev_gray = gray.copy()
        # Merge tracked and new corners for next iteration
        if self._prev_corners is not None and len(self._prev_corners) > 0 and len(tracked_prev) > 0:
            # Use tracked points + new corners (ensuring we don't exceed max_corners)
            all_corners = np.vstack([tracked_next, new_corners.reshape(-1, 2)]) if new_corners is not None else tracked_next
            if len(all_corners) > self.max_corners:
                idx = np.random.choice(len(all_corners), self.max_corners, replace=False)
                all_corners = all_corners[idx]
            self._prev_corners = all_corners.reshape(-1, 1, 2).astype(np.float32)
        else:
            self._prev_corners = new_corners

        return mag_map, div_map

    def _grid_interpolate(
        self,
        positions: np.ndarray,    # (N, 2) — (x, y) of tracked points
        flow_vecs: np.ndarray,    # (N, 2) — (dx, dy) flow vectors
        magnitudes: np.ndarray,   # (N,)  — flow magnitudes
        h: int, w: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Bin flow into coarse grid, compute divergence per cell, interpolate back."""
        gh, gw = self.grid_h, self.grid_w

        # Initialize grid accumulators
        mag_grid = np.zeros((gh, gw), dtype=np.float32)
        div_grid = np.zeros((gh, gw), dtype=np.float32)
        count_grid = np.zeros((gh, gw), dtype=np.int32)

        # Bin each point into grid cell
        for i in range(len(positions)):
            px, py = positions[i]
            gx = int(np.clip(px / w * gw, 0, gw - 1))
            gy = int(np.clip(py / h * gh, 0, gh - 1))

            mag_grid[gy, gx] += magnitudes[i]
            div_grid[gy, gx] += flow_vecs[i, 0] + flow_vecs[i, 1]  # du/dx + dv/dy ≈ dx + dy
            count_grid[gy, gx] += 1

        # Average per cell
        valid = count_grid > 0
        mag_grid[valid] /= count_grid[valid]
        div_grid[valid] /= count_grid[valid]

        # Upsample grid to image resolution
        mag_map = cv2.resize(mag_grid, (w, h), interpolation=cv2.INTER_LINEAR)
        div_map = cv2.resize(div_grid, (w, h), interpolation=cv2.INTER_LINEAR)

        # Normalize to [0, 1]
        mag_max = mag_map.max()
        if mag_max > 0:
            mag_map /= mag_max

        div_pos = np.maximum(div_map, 0)  # positive divergence only (gas expansion)
        div_max = div_pos.max()
        if div_max > 0:
            div_pos /= div_max

        return mag_map, div_pos
