"""DL pipeline Layer 2: Gas region filtering.

From video3's layer2_filter — connected components + area/shape constraints
to extract candidate bounding boxes for classification.
"""

import cv2
import numpy as np
from typing import List, Tuple

from ..config.schema import DLROIConfig


class GasRegionFilter:
    """Filter motion mask to extract valid gas candidate ROIs."""

    def __init__(self, config: DLROIConfig):
        self.min_area = config.min_area
        self.max_area_ratio = config.max_area_ratio
        self.persistence_frames = config.persistence_frames

        # Persistence state
        self._prev_rois: List[Tuple[int, int, int, int]] = []
        self._miss_count: List[int] = []

    def process(self, motion_mask: np.ndarray
                ) -> tuple[bool, List[Tuple[int, int, int, int]], np.ndarray]:
        """Return (valid: bool, roi_boxes: list, filtered_mask: np.ndarray)."""
        h, w = motion_mask.shape
        max_area = int(self.max_area_ratio * h * w)

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            motion_mask, connectivity=8,
        )

        rois = []
        filtered_mask = np.zeros((h, w), dtype=np.uint8)
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if self.min_area <= area <= max_area:
                x, y, bw, bh = (stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                                stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
                rois.append((x, y, bw, bh))
                filtered_mask[labels == i] = 255

        # Apply persistence
        rois = self._apply_persistence(rois)

        valid = len(rois) > 0
        return valid, rois, filtered_mask

    def _apply_persistence(self, rois: List[Tuple[int, int, int, int]]
                           ) -> List[Tuple[int, int, int, int]]:
        """Keep ROIs alive briefly if temporarily lost."""
        if not rois and self._prev_rois:
            survived = []
            new_misses = []
            for miss, prev_roi in zip(self._miss_count, self._prev_rois):
                if miss < self.persistence_frames:
                    survived.append(prev_roi)
                    new_misses.append(miss + 1)
            if survived:
                self._miss_count = new_misses
                return survived

        self._prev_rois = rois
        self._miss_count = [0] * len(rois)
        return rois

    @staticmethod
    def union_bbox(roi_boxes: List[Tuple[int, int, int, int]]
                   ) -> Tuple[int, int, int, int]:
        """Compute the union bounding box of all ROIs."""
        if not roi_boxes:
            return (0, 0, 0, 0)
        x1 = min(b[0] for b in roi_boxes)
        y1 = min(b[1] for b in roi_boxes)
        x2 = max(b[0] + b[2] for b in roi_boxes)
        y2 = max(b[1] + b[3] for b in roi_boxes)
        return (x1, y1, x2 - x1, y2 - y1)
