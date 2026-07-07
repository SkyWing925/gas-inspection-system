"""Frame source abstraction — unified interface for camera, video file, npz."""

import time
from abc import ABC, abstractmethod
from typing import Optional

import cv2
import numpy as np


class FrameSource(ABC):
    """Abstract frame source. All processing code depends on this interface only."""

    @abstractmethod
    def open(self) -> None:
        """Initialize the source. Call once before read()."""
        ...

    @abstractmethod
    def read(self) -> Optional[np.ndarray]:
        """Return next grayscale frame (H, W) uint8, or None at EOF."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release resources."""
        ...

    @property
    @abstractmethod
    def fps(self) -> float:
        """Frames per second. Returns 0 if unknown."""
        ...

    @property
    @abstractmethod
    def frame_count(self) -> Optional[int]:
        """Total frame count, or None if unknown (live camera)."""
        ...

    @property
    @abstractmethod
    def width(self) -> int:
        """Frame width in pixels."""
        ...

    @property
    @abstractmethod
    def height(self) -> int:
        """Frame height in pixels."""
        ...

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


class CameraSource(FrameSource):
    """Live camera via V4L2 (/dev/video0). Non-blocking, grayscale output."""

    def __init__(self, device: str = "/dev/video0",
                 width: int = 640, height: int = 480,
                 fps: float = 25.0):
        self.device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: Optional[cv2.VideoCapture] = None

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)
        # Warm up
        for _ in range(5):
            self._cap.read()

    def read(self) -> Optional[np.ndarray]:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok:
            return None
        # YUYV format: extract luma channel
        if frame.ndim == 3 and frame.shape[2] == 2:
            gray = frame[:, :, 0]
        elif frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        return gray

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()

    @property
    def fps(self) -> float:
        if self._cap is not None:
            return self._cap.get(cv2.CAP_PROP_FPS) or self._fps
        return self._fps

    @property
    def frame_count(self) -> Optional[int]:
        return None  # live camera: unknown

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height


class VideoFileSource(FrameSource):
    """Read .avi/.mp4 file via OpenCV VideoCapture."""

    def __init__(self, path: str):
        self.path = path
        self._cap: Optional[cv2.VideoCapture] = None

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self.path)

    def read(self) -> Optional[np.ndarray]:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok:
            return None
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        return gray

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()

    @property
    def fps(self) -> float:
        if self._cap is not None:
            return self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        return 25.0

    @property
    def frame_count(self) -> Optional[int]:
        if self._cap is not None:
            return int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        return None

    @property
    def width(self) -> int:
        if self._cap is not None:
            return int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        return 640

    @property
    def height(self) -> int:
        if self._cap is not None:
            return int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return 480


class NpzFileSource(FrameSource):
    """Load frames from .npz file (video3 format: data['frames'] array).

    Supports optional frame sampling via start_sec/step_sec/max_count,
    matching the old main_0.py extract_from_npz() behavior.
    """

    def __init__(self, path: str, fps: float = 25.0,
                 start_sec: float = 0.0, step_sec: float = 0.0,
                 max_count: int | None = None):
        self.path = path
        self._fps = fps
        self._start_sec = start_sec
        self._step_sec = step_sec
        self._max_count = max_count
        self._frames: Optional[np.ndarray] = None
        self._frame_indices: list = []
        self._index = 0

    def open(self) -> None:
        data = np.load(self.path, allow_pickle=True)
        all_frames = data["frames"]
        self._fps = float(data.get("fps", self._fps))
        total = len(all_frames)

        # Apply sampling (matching old extract_from_npz logic)
        if self._step_sec > 0:
            start_idx = int(self._start_sec * self._fps)
            step_idx = max(1, int(self._step_sec * self._fps))
            indices = list(range(start_idx, total, step_idx))
        else:
            start_idx = int(self._start_sec * self._fps)
            indices = list(range(start_idx, total))

        if self._max_count is not None:
            indices = indices[:self._max_count]

        self._frames = all_frames
        self._frame_indices = indices
        self._index = 0

    def read(self) -> Optional[np.ndarray]:
        if self._frames is None or self._index >= len(self._frame_indices):
            return None
        idx = self._frame_indices[self._index]
        frame = self._frames[idx]
        self._index += 1
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        return frame

    def close(self) -> None:
        self._frames = None
        self._frame_indices = []
        self._index = 0

    def seek(self, idx: int) -> None:
        """Seek to a specific frame index (within sampled indices)."""
        self._index = max(0, min(idx, len(self._frame_indices)))

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_count(self) -> Optional[int]:
        if self._frame_indices:
            return len(self._frame_indices)
        if self._frames is not None:
            return len(self._frames)
        return None

    @property
    def width(self) -> int:
        if self._frames is not None and len(self._frames) > 0:
            return self._frames.shape[2]
        return 640

    @property
    def height(self) -> int:
        if self._frames is not None and len(self._frames) > 0:
            return self._frames.shape[1]
        return 480
