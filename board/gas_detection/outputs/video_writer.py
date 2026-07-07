"""Output: multiplexed video writer."""

import os
import cv2
import numpy as np


class VideoMultiplexer:
    """Write binary.avi, heatmap.avi, overlay.avi simultaneously."""

    def __init__(self, output_dir: str, fps: float, size: tuple[int, int]):
        os.makedirs(output_dir, exist_ok=True)
        w, h = size
        fourcc = cv2.VideoWriter_fourcc(*"XVID")

        self.writer_binary = cv2.VideoWriter(
            os.path.join(output_dir, "gas_detection_binary.avi"),
            fourcc, fps, (w, h), isColor=False,
        )
        self.writer_heatmap = cv2.VideoWriter(
            os.path.join(output_dir, "gas_detection_heatmap.avi"),
            fourcc, fps, (w, h), isColor=True,
        )
        self.writer_overlay = cv2.VideoWriter(
            os.path.join(output_dir, "gas_detection_overlay.avi"),
            fourcc, fps, (w, h), isColor=True,
        )

    def write(self, binary: np.ndarray, heatmap: np.ndarray,
              overlay: np.ndarray) -> None:
        """Write one frame to all three output streams."""
        self.writer_binary.write(binary)
        self.writer_heatmap.write(heatmap)
        self.writer_overlay.write(overlay)

    def close(self) -> None:
        """Release all writers."""
        self.writer_binary.release()
        self.writer_heatmap.release()
        self.writer_overlay.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
