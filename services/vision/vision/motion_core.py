"""Finding the seconds with movement in one recording segment (OpenCV background subtraction; this is image processing, not
a learned model).

Source of the method: OpenCV's tutorial "How to Use Background Subtraction Methods" and its sample, which use
`BackgroundSubtractorMOG2` (history 500, varThreshold 16, detectShadows true are the defaults of
`createBackgroundSubtractorMOG2`). In its mask a value of 255 is foreground, 127 a shadow and 0 background.

The scene cut follows Frigate's motion detector (frigate/motion/improved_motion.py, v0.18.2), which recalibrates when
more than `lightning_threshold` (default 0.8) of the image moves: a frame in which more than S = 0.8 of the picture is
foreground is a scene cut, the subtractor is made again from that frame, and that frame with the next K-1 frames only
teach it.

T = 0.005 is the smallest share of changed pixels that raises an alarm in ZoneMinder's zone preset "Fast, medium
sensitivity" (the presets that use only the alarmed-pixels check; `MinAlarmPixels` 0.5 per cent, db/zm_create.sql.in):
a second whose value is above T is a "motion second".

Values that are the author's design decisions (no model was found; where it was looked is in
docs/adr/0011-motion-and-classification.md):
  - a new subtractor for every segment, and the first K = 15 frames of a subtractor only teach it;
  - the value of a second is the median of the foreground share of its judged frames.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np

FOREGROUND = 255      # in the mask of MOG2; 127 is a shadow and is not counted


@dataclass(frozen=True)
class MotionParams:
    history: int = 500
    var_threshold: float = 16.0
    detect_shadows: bool = True
    skip_frames: int = 15           # K: frames that only teach a new subtractor
    cut_share: float = 0.8          # S: more than this share foreground in one frame = a scene cut (Frigate's lightning_threshold)
    motion_share: float = 0.005     # T: a second whose value is above this is a motion second (ZoneMinder's preset)


DEFAULT = MotionParams()


def make_subtractor(p: MotionParams):
    import cv2     # imported here so that the rest can be used and tested without it
    return cv2.createBackgroundSubtractorMOG2(p.history, p.var_threshold, p.detect_shadows)


def foreground_share(mask: np.ndarray) -> float:
    """The share of the picture that is foreground. A shadow (127) is not foreground."""
    return float(np.count_nonzero(mask == FOREGROUND)) / mask.size


@dataclass(frozen=True)
class SecondValue:
    second: int                 # counted from the start of the segment
    share: float | None         # None: no judged frame in this second


def second_values(frames: Iterable[np.ndarray], fps: float, p: MotionParams = DEFAULT,
                  new_subtractor: Callable[[MotionParams], object] = make_subtractor) -> list[SecondValue]:
    """Read the frames of one segment in order (no seeking) and return, for each second that has frames, the median of the
    foreground share of the frames that were judged."""
    sub = new_subtractor(p)
    taught = 0                                  # frames the current subtractor has seen
    shares: dict[int, list[float]] = {}
    last_second = -1
    for n, frame in enumerate(frames):
        second = int(n / fps + 1e-9)
        last_second = second
        shares.setdefault(second, [])
        mask = sub.apply(frame)
        taught += 1
        if taught <= p.skip_frames:             # still learning: not judged
            continue
        share = foreground_share(mask)
        if share > p.cut_share:                 # a scene cut: start again from this frame, judge nothing for K frames
            sub = new_subtractor(p)
            sub.apply(frame)
            taught = 1
            continue
        shares[second].append(share)
    return [SecondValue(s, float(np.median(shares[s])) if shares.get(s) else None) for s in range(last_second + 1)]


def motion_intervals(values: list[SecondValue], p: MotionParams = DEFAULT) -> list[tuple[int, int]]:
    """(first second, number of seconds) for each run of consecutive motion seconds (value above T)."""
    runs: list[tuple[int, int]] = []
    start = None
    for v in values:
        moving = v.share is not None and v.share > p.motion_share
        if moving and start is None:
            start = v.second
        if not moving and start is not None:
            runs.append((start, v.second - start))
            start = None
    if start is not None:
        runs.append((start, values[-1].second + 1 - start))
    return runs
