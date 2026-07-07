"""DL pipeline Layer 3: MobileNetV2 gas leak classifier with cascade gating.

From video3's inline classification code, modularized with:
- CV is a SIGNAL DETECTOR, DL is the CLASSIFIER — CV is never trusted to classify.
- DL runs on frames with ROIs (frame-interval sampled), no per-frame signal threshold.
- Aggregate ROI count gates whether DL results are used (handled in ReportGenerator).
"""

import os
import cv2
import numpy as np
from typing import List, Tuple, Optional
from PIL import Image

from ..config.schema import DLConfig


class GasClassifier:
    """MobileNetV2 leak/no-leak classifier with cascade gating logic."""

    def __init__(self, config: DLConfig):
        self.image_size = config.image_size
        self.frame_interval = config.frame_interval
        self.skip_low = config.cascade.skip_low

        self._model = None
        self._transform = None
        self._model_path: str | None = None

    def load_model(self, model_path: str) -> None:
        """Lazy-load the MobileNetV2 model."""
        if self._model is not None and self._model_path == model_path:
            return

        import torch
        import torch.nn as nn
        from torchvision import transforms
        from torchvision.models import mobilenet_v2

        model = mobilenet_v2(weights=None)
        model.classifier[1] = nn.Linear(model.last_channel, 2)
        state = torch.load(model_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()
        self._model = model

        self._transform = transforms.Compose([
            transforms.Grayscale(num_output_channels=3),
            transforms.Resize((self.image_size, self.image_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
        ])
        self._model_path = model_path

    def should_classify(self, fused_max: float, roi_count: int,
                        frame_idx: int, gray: np.ndarray | None = None,
                        roi_boxes: List[Tuple[int, int, int, int]] | None = None
                        ) -> bool:
        """Decide whether to run DL on this frame.

        Always runs DL when significant signal is present (fused_max >= skip_low).
        CV is a detector, not a classifier — it cannot tell gas from humans.
        """
        if frame_idx % self.frame_interval != 0:
            return False
        if roi_count == 0:
            return False
        # Only skip if CV says "nothing there at all"
        if fused_max < self.skip_low:
            return False

        return True

    def classify(self, gray: np.ndarray, roi_boxes: List[Tuple[int, int, int, int]]
                 ) -> Tuple[dict, Optional[Tuple[int, int, int, int]]]:
        """Classify the union of roi_boxes as leak or normal.

        Args:
            gray: full-resolution grayscale frame (H, W) uint8
            roi_boxes: list of (x, y, w, h) at full resolution

        Returns:
            ({"class": "leak"|"normal", "leak_prob": float, "normal_prob": float},
             union_bbox (x, y, w, h) or None)
        """
        if self._model is None:
            return {"class": "normal", "leak_prob": 0.0, "normal_prob": 1.0}, None

        if not roi_boxes:
            return {"class": "normal", "leak_prob": 0.0, "normal_prob": 1.0}, None

        # Compute union bbox
        x1 = min(b[0] for b in roi_boxes)
        y1 = min(b[1] for b in roi_boxes)
        x2 = max(b[0] + b[2] for b in roi_boxes)
        y2 = max(b[1] + b[3] for b in roi_boxes)
        union_box = (x1, y1, x2 - x1, y2 - y1)

        # Crop and classify
        h_img, w_img = gray.shape
        cx1 = max(0, x1)
        cy1 = max(0, y1)
        cx2 = min(w_img, x2)
        cy2 = min(h_img, y2)
        crop = gray[cy1:cy2, cx1:cx2]

        import torch
        pil_img = Image.fromarray(crop)
        tensor = self._transform(pil_img).unsqueeze(0)
        with torch.no_grad():
            output = self._model(tensor)
            prob = torch.softmax(output, dim=1)[0]

        leak_p = prob[0].item()
        normal_p = prob[1].item()
        class_name = "leak" if leak_p > 0.5 else "normal"

        return {"class": class_name, "leak_prob": leak_p, "normal_prob": normal_p}, union_box
