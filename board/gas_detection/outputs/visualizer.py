"""Output: visualization rendering."""

import cv2
import numpy as np
from typing import List, Tuple, Optional


class Visualizer:
    """Render detection results onto frames."""

    @staticmethod
    def heatmap_to_bgr(fused_clean: np.ndarray) -> np.ndarray:
        """Convert cleaned fused map to INFERNO heatmap BGR image."""
        fused_uint8 = np.clip(fused_clean * 255, 0, 255).astype(np.uint8)
        return cv2.applyColorMap(fused_uint8, cv2.COLORMAP_INFERNO)

    @staticmethod
    def blend_overlay(original: np.ndarray, heatmap: np.ndarray,
                      alpha: float = 0.5) -> np.ndarray:
        """Blend heatmap onto original frame."""
        orig_bgr = cv2.cvtColor(original, cv2.COLOR_GRAY2BGR)
        return cv2.addWeighted(orig_bgr, 1.0 - alpha, heatmap, alpha, 0)

    @staticmethod
    def draw_rois(frame_bgr: np.ndarray,
                  roi_boxes: List[Tuple[int, int, int, int]],
                  union_box: Optional[Tuple[int, int, int, int]] = None,
                  classify_result: Optional[dict] = None) -> np.ndarray:
        """Draw ROIs (red) and union bbox (blue) with classification label."""
        result = frame_bgr.copy()

        # Draw individual ROIs in red
        for (x, y, w, h) in roi_boxes:
            cv2.rectangle(result, (x, y), (x + w, y + h), (0, 0, 255), 2)

        # Draw union bbox in blue
        if union_box is not None:
            ux, uy, uw, uh = union_box
            if uw > 0 and uh > 0:
                cv2.rectangle(result, (ux, uy), (ux + uw, uy + uh), (255, 0, 0), 2)

        # Classification label
        if classify_result is not None:
            cl = classify_result.get("class", "?")
            prob = classify_result.get("leak_prob", 0.0) if cl == "leak" \
                   else classify_result.get("normal_prob", 0.0)
            color = (0, 0, 255) if cl == "leak" else (0, 255, 0)
            label = f"{cl} {prob:.2f}"
            if union_box is not None and union_box[2] > 0:
                ux, uy = union_box[0], union_box[1]
                cv2.putText(result, label, (ux, max(uy - 8, 15)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            else:
                cv2.putText(result, label, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        return result

    @staticmethod
    def draw_status(frame_bgr: np.ndarray, status: str) -> np.ndarray:
        """Draw detection status text at top-left."""
        colors = {"danger": (0, 0, 255), "warning": (0, 255, 255), "normal": (0, 255, 0)}
        color = colors.get(status, (255, 255, 255))
        cv2.putText(frame_bgr, status.upper(), (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        return frame_bgr

    @staticmethod
    def draw_leak_max(gray: np.ndarray, union_box: Tuple[int, int, int, int],
                      leak_prob: float) -> np.ndarray:
        """Draw the best leak frame with bounding box for saving."""
        ux, uy, uw, uh = union_box
        out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        if uw > 0 and uh > 0:
            cv2.rectangle(out, (ux, uy), (ux + uw, uy + uh), (0, 0, 255), 3)
        cv2.putText(out, f"LEAK {leak_prob:.2f}", (ux, max(uy - 10, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return out
