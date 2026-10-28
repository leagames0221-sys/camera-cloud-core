r"""The trial on synthetic pictures behind the values K and T of the motion worker, and of what S (Frigate's value) means for
a cut (docs/adr/0011). It runs the worker's own
code (services/vision/vision/motion_core.py, OpenCV's MOG2 with its default arguments) on pictures made here from a fixed seed,
and prints the numbers the decision record quotes. It does not use the check video, so it cannot fit the values to its answer.

  .venv\Scripts\python tools\motion_trial.py

The pictures: 640 x 360 grey-scale noise (seed 0), blurred with a Gaussian of sigma 3, shown as BGR; 15 frames a second.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "vision"))

from vision.motion_core import DEFAULT, foreground_share, make_subtractor, second_values  # noqa: E402

W, H, FPS = 640, 360, 15


def picture(rng: np.random.Generator) -> np.ndarray:
    import cv2
    g = cv2.GaussianBlur(rng.integers(0, 256, (H, W), dtype=np.uint8), (0, 0), 3)
    g = cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX)          # the blur narrows the range; spread it again
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def noisy(base: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    if sigma == 0:
        return base.copy()
    n = rng.normal(0, sigma, base.shape[:2])[:, :, None]
    return np.clip(base.astype(np.float32) + n, 0, 255).astype(np.uint8)


def judged_shares(frames) -> list[float]:
    """The foreground share of every frame after the first K, with one subtractor (no cut handling)."""
    sub, out = make_subtractor(DEFAULT), []
    for i, f in enumerate(frames):
        share = foreground_share(sub.apply(f))
        if i >= DEFAULT.skip_frames:
            out.append(share)
    return out


def main() -> int:
    rng = np.random.default_rng(0)
    base = picture(rng)
    print(f"K={DEFAULT.skip_frames} frames, S={DEFAULT.cut_share}, T={DEFAULT.motion_share}; MOG2 history {DEFAULT.history}, "
          f"varThreshold {DEFAULT.var_threshold}, detectShadows {DEFAULT.detect_shadows}")

    print("\n1. a still picture with noise, 5 s: the largest share in a judged frame, and the second values")
    for sigma in (0, 2, 4, 6):
        frames = [noisy(base, sigma, rng) for _ in range(5 * FPS)]
        shares = judged_shares(frames)
        values = [v.share for v in second_values(frames, FPS) if v.share is not None]
        print(f"   noise {sigma} grey levels: largest {max(shares):.4%} in one frame; second values "
              f"{', '.join(f'{v:.4%}' for v in values)}")

    print("\n2. a square that moves 3 pixels a frame over the still picture, 5 s: the second values")
    for kind in ("one grey (black)", "textured (another noise picture)"):
        for part in (0.002, 0.01, 0.05):
            side = int(round((part * W * H) ** 0.5))
            patch = np.zeros((side, side, 3), np.uint8) if kind.startswith("one") else picture(rng)[:side, :side]
            frames = []
            for i in range(5 * FPS):
                f = base.copy()
                x = 10 + 3 * i
                f[100:100 + side, x:x + side] = patch
                frames.append(f)
            values = [v.share for v in second_values(frames, FPS) if v.share is not None]
            print(f"   {kind} square of {side * side / (W * H):.2%} of the picture: second values "
                  f"{', '.join(f'{v:.2%}' for v in values)}")

    print("\n3. a cut: the share of the first frame of an unrelated picture after 30 frames of another")
    for _ in range(3):
        a, b = picture(rng), picture(rng)
        sub = make_subtractor(DEFAULT)
        for _ in range(30):
            sub.apply(a)
        print(f"   {foreground_share(sub.apply(b)):.1%}")

    print("\n4. a slow brightening of one grey level in ten frames, 10 s: the largest share in a judged frame")
    frames = [np.clip(base.astype(np.int16) + i // 10, 0, 255).astype(np.uint8) for i in range(10 * FPS)]
    print(f"   {max(judged_shares(frames)):.4%}")

    print("\n5. a change that stays (a square of 5 % of the picture replaced) after 30 frames: frames until the share is at or")
    print("   below T again")
    side = int(round((0.05 * W * H) ** 0.5))
    changed = base.copy()
    changed[100:100 + side, 100:100 + side] = picture(rng)[:side, :side]
    sub = make_subtractor(DEFAULT)
    for _ in range(30):
        sub.apply(base)
    shares = [foreground_share(sub.apply(changed)) for _ in range(60)]
    after = next((i + 1 for i, s in enumerate(shares) if s <= DEFAULT.motion_share), None)
    print(f"   {after} frames (shares {', '.join(f'{s:.2%}' for s in shares[:10])} ...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
