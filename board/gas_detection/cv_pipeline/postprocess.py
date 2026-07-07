"""CV pipeline: channel fusion and post-processing."""

import cv2
import numpy as np
from typing import List, Tuple

from ..config.schema import FusionConfig, CVPostProcessConfig, TemporalConfig


def fuse_channels(
    motion: np.ndarray,       # [0, 1] float32
    flow_mag: np.ndarray,     # [0, 1] float32
    divergence: np.ndarray,   # [0, 1] float32
    temporal_var: np.ndarray, # [0, 1] float32
    config: FusionConfig,
) -> np.ndarray:
    """Weighted fusion of four CV channels → (H, W) float32 [0, ~1+]."""
    fused = (
        config.w_motion * motion +
        config.w_magnitude * flow_mag +
        config.w_divergence * divergence +
        config.w_temporal_var * temporal_var
    )
    return fused.astype(np.float32)


class PostProcessor:
    """Post-processing: percentile threshold, morphological cleaning, ROI extraction."""

    def __init__(self, cv_config: CVPostProcessConfig, temporal_config: TemporalConfig):
        self.min_area = cv_config.min_component_area
        self.max_area_ratio = cv_config.max_area_ratio
        self.percentile = cv_config.binary_percentile
        self.morph_size = cv_config.morph_open_size
        self.roi_merge_dist = cv_config.roi_merge_dist
        self.roi_persistence = cv_config.roi_persistence
        self.heatmap_threshold = temporal_config.heatmap_intensity_threshold

        # ROI tracking state
        self._prev_rois: List[Tuple[int, int, int, int]] = []
        self._roi_miss_count: List[int] = []

    def binary_mask(self, fused_map: np.ndarray) -> np.ndarray:
        """Convert fused map to binary mask (H, W) uint8 {0, 255}."""
        thresh = np.percentile(fused_map, self.percentile)
        binary = (fused_map > thresh).astype(np.uint8) * 255

        # Morphological opening
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.morph_size, self.morph_size))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        return binary

    def clean_heatmap(self, fused_map: np.ndarray,
                      prev_clean: np.ndarray | None = None,
                      ema_alpha: float = 0.3) -> np.ndarray:
        """Clean fused map for heatmap visualization (float32)."""
        cleaned = fused_map.copy()
        cleaned[cleaned < self.heatmap_threshold] = 0

        # Morphological opening on binarized version to find noise
        binary = (cleaned > 0).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.morph_size, self.morph_size))
        binary_clean = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        # Remove small components
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            binary_clean, connectivity=8,
        )
        h, w = cleaned.shape
        max_area = int(self.max_area_ratio * h * w)
        component_mask = np.zeros_like(binary_clean)
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if self.min_area <= area <= max_area:
                component_mask[labels == i] = 255

        cleaned[component_mask == 0] = 0

        # Temporal EMA
        if prev_clean is not None:
            cleaned = ema_alpha * cleaned + (1.0 - ema_alpha) * prev_clean

        return cleaned.astype(np.float32)

    def extract_rois(self, fused_map: np.ndarray, h: int, w: int
                     ) -> List[Tuple[int, int, int, int]]:
        """Extract candidate ROIs from fused map (returns boxes at original scale)."""
        thresh = np.percentile(fused_map, self.percentile)
        binary = (fused_map > thresh).astype(np.uint8) * 255

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.morph_size, self.morph_size))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            binary, connectivity=8,
        )
        max_area = int(self.max_area_ratio * h * w)
        rois = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if self.min_area <= area <= max_area:
                x, y, bw, bh = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP], \
                               stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
                rois.append((x, y, bw, bh))

        # Merge overlapping ROIs
        rois = self._merge_rois(rois)

        # Persistence tracking
        rois = self._apply_persistence(rois)

        return rois

    def _merge_rois(self, rois: List[Tuple[int, int, int, int]]
                    ) -> List[Tuple[int, int, int, int]]:
        """Merge ROIs within merge_dist pixels."""
        if len(rois) <= 1:
            return rois

        merged = []
        used = [False] * len(rois)
        for i, (x1, y1, w1, h1) in enumerate(rois):
            if used[i]:
                continue
            mx1, my1, mx2, my2 = x1, y1, x1 + w1, y1 + h1
            for j, (x2, y2, w2, h2) in enumerate(rois):
                if i == j or used[j]:
                    continue
                # Check if ROIs are close
                cx1, cy1 = x1 + w1 / 2, y1 + h1 / 2
                cx2, cy2 = x2 + w2 / 2, y2 + h2 / 2
                if abs(cx1 - cx2) < self.roi_merge_dist and abs(cy1 - cy2) < self.roi_merge_dist:
                    mx1 = min(mx1, x2)
                    my1 = min(my1, y2)
                    mx2 = max(mx2, x2 + w2)
                    my2 = max(my2, y2 + h2)
                    used[j] = True
            used[i] = True
            merged.append((int(mx1), int(my1), int(mx2 - mx1), int(my2 - my1)))

        return merged

    def _apply_persistence(self, rois: List[Tuple[int, int, int, int]]
                           ) -> List[Tuple[int, int, int, int]]:
        """Keep ROIs alive for persistence frames if temporarily lost."""
        if not rois and self._prev_rois:
            # Check if any previous ROIs are still within persistence
            survived = []
            for i, (count, prev_roi) in enumerate(zip(self._roi_miss_count, self._prev_rois)):
                if count < self.roi_persistence:
                    survived.append(prev_roi)
                    self._roi_miss_count[i] = count + 1
            if survived:
                return survived

        # Update tracking state
        self._prev_rois = rois
        self._roi_miss_count = [0] * len(rois)
        return rois
