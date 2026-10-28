"""Reading the frames of a segment file, from the first to the last, without seeking."""
from __future__ import annotations

from typing import Iterator

import numpy as np

from .common import Refused, finite_fps


def open_frames(path) -> tuple[float, Iterator[np.ndarray]]:
    """(frames per second, an iterator over the frames as BGR arrays). The file is closed when the iterator ends or is
    dropped. Raises Refused if the file cannot be opened as a video."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        cap.release()
        raise Refused("the file cannot be opened as a video")
    fps = finite_fps(cap.get(cv2.CAP_PROP_FPS))

    def frames() -> Iterator[np.ndarray]:
        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    return
                yield frame
        finally:
            cap.release()

    return fps, frames()
