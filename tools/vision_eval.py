r"""Measures the two workers against what is known, and prints the tables of docs/validation/vision_eval.md.

  segments   cut the check video into segments of 60 seconds, three ways (the cuts shifted by 0, 20 and 40 seconds); uses the
             ffmpeg of the camera image through `docker run`
               .venv\Scripts\python tools\vision_eval.py segments
  motion     run the motion code on those segments and compare, second by second, with the way the video was made
             (docs/validation/vision_truth.md); the numbers are what they are, the parameters are not changed to fit them
               .venv\Scripts\python tools\vision_eval.py motion
  card       run the classify code on the model card's picture and judge the result against the card's published output
  pictures   name the things in one picture a second of the moving part of the check video (no judgement) and time it
             (card and pictures need the GPU; run them in the vision image:)
               docker run --rm --gpus=all -e PYTHONPATH=/srv -v <repo>/services/vision/vision:/srv/vision:ro
                 -v <repo>/tools:/tools:ro -v <repo>/var/models/detr-resnet-50:/models/detr-resnet-50:ro
                 -v <repo>/var/check:/check:ro -v <repo>/var/bbb:/clip:ro ccc-vision:3.0 python /tools/vision_eval.py card
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "vision"))

LAP = 180
MOVING = (60, 120)            # seconds of the check video that move (docs/validation/vision_truth.md)
JOINS = (0, 60, 120, 180)     # where one part of the video meets the next (the last one is the turn to the first again)
END_WIDTH = 4                 # seconds not counted on each side of a join: GOP 2 + 1 (the author's decision, no model) + 15 frames at 15 fps (docs/adr/0011)
SHIFTS = (0, 20, 40)
CLIP_DIR = pathlib.Path(os.environ.get("CLIP_DIR", ROOT / "var" / "bbb"))
CAMERA_IMAGE = os.environ.get("CAMERA_IMAGE", "ccc-camera:3.0")

# The output of the model card for the check picture (Hugging Face, facebook/detr-resnet-50): name, score, box.
CARD = [("remote", 0.998, (40.16, 70.81, 175.55, 117.98)), ("remote", 0.996, (333.24, 72.55, 368.33, 187.66)),
        ("couch", 0.995, (-0.02, 1.15, 639.73, 473.76)), ("cat", 0.999, (13.24, 52.05, 314.02, 470.93)),
        ("cat", 0.999, (345.4, 23.85, 640.37, 368.72))]
# Equal to the card up to the decimals it prints, by numpy's rule for "equal up to desired precision" (numpy.testing,
# assert_almost_equal: abs(desired-actual) < 1.5 * 10**(-decimal)): scores have 3 decimals, box coordinates 2.
SCORE_DECIMALS = 3
BOX_DECIMALS = 2


def almost_equal(actual: float, desired: float, decimal: int) -> bool:
    return abs(desired - actual) < 1.5 * 10 ** (-decimal)


# ---------------------------------------------------------------- the true answer and the scoring

def is_motion(second: int) -> bool:
    return MOVING[0] <= second < MOVING[1]


def is_counted(second: int) -> bool:
    """False for the seconds next to a join; the true answer is not asked for them."""
    return all(not (j - END_WIDTH <= second < j + END_WIDTH) for j in JOINS)


def score_seconds(predicted: set[int]) -> dict:
    tp = fp = fn = tn = 0
    missed, false_alarm = [], []
    for s in range(LAP):
        if not is_counted(s):
            continue
        truth, said = is_motion(s), s in predicted
        if truth and said:
            tp += 1
        elif truth:
            fn += 1
            missed.append(s)
        elif said:
            fp += 1
            false_alarm.append(s)
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "counted": tp + fp + fn + tn,
            "recall": tp / (tp + fn) if tp + fn else None, "precision": tp / (tp + fp) if tp + fp else None,
            "missed": missed, "false_alarm": false_alarm}


def iou(a: tuple, b: tuple) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def judge_card(detections, expected=CARD) -> dict:
    """The names must be the same (as a set with counts); each detection is paired with the card's box of the same name that
    it overlaps most, and its score and its four box coordinates must equal the card's up to the decimals the card prints
    (numpy's rule, above; docs/adr/0011)."""
    left = list(detections)
    pairs = []
    for name, score, box in expected:
        cand = [(iou(d.box, box), i) for i, d in enumerate(left) if d.label == name]
        if not cand:
            pairs.append((name, None, None, None, None))
            continue
        best, i = max(cand)
        d = left.pop(i)
        same = almost_equal(d.score, score, SCORE_DECIMALS) and all(
            almost_equal(a, b, BOX_DECIMALS) for a, b in zip(d.box, box))
        pairs.append((name, d.score, best, same, max(abs(a - b) for a, b in zip(d.box, box))))
    extra = [d.label for d in left]
    ok = not extra and all(p[3] for p in pairs)
    return {"pass": ok, "pairs": pairs, "extra": extra}


# ---------------------------------------------------------------- segments and motion

def cut_times(shift: int) -> list[int]:
    return list(range(shift if shift else 60, LAP, 60))


def make_segments() -> None:
    for shift in SHIFTS:
        out = CLIP_DIR / "eval" / f"shift_{shift}"
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("seg_*.mp4"):
            old.unlink()
        times = ",".join(str(t) for t in cut_times(shift))
        cmd = ["docker", "run", "--rm", "--entrypoint", "ffmpeg", "-v", f"{CLIP_DIR}:/clip", CAMERA_IMAGE, "-y",
               "-loglevel", "error", "-i", "/clip/check_clip.mp4", "-c", "copy", "-f", "segment", "-segment_times", times,
               "-segment_format", "mp4", "-segment_format_options", "movflags=frag_keyframe+empty_moov+default_base_moof",
               "-reset_timestamps", "1", f"/clip/eval/shift_{shift}/seg_%03d.mp4"]
        print(" ".join(cmd))
        subprocess.run(cmd, check=True)


def run_motion() -> dict:
    from vision.motion_core import DEFAULT, motion_intervals, second_values
    from vision.video import open_frames

    results = {}
    for shift in SHIFTS:
        starts = [0] + cut_times(shift)
        files = sorted((CLIP_DIR / "eval" / f"shift_{shift}").glob("seg_*.mp4"))
        if len(files) != len(starts):
            raise SystemExit(f"shift {shift}: {len(files)} files, expected {len(starts)}; run `segments` first")
        predicted: set[int] = set()
        for start, f in zip(starts, files):
            fps, frames = open_frames(f)
            values = second_values(frames, fps, DEFAULT)
            for v in values:
                if v.share is not None and v.share > DEFAULT.motion_share:
                    predicted.add(start + v.second)
            _ = motion_intervals(values, DEFAULT)
        results[shift] = score_seconds(predicted)
    return results


def print_motion(results: dict) -> None:
    print("| Cuts shifted by | Counted seconds | Motion seconds found (TP) | Missed (FN) | False alarms (FP) | Recall | Precision |")
    print("|---|---|---|---|---|---|---|")
    for shift, r in results.items():
        rec = "n/a" if r["recall"] is None else f"{r['recall']:.3f}"
        pre = "n/a" if r["precision"] is None else f"{r['precision']:.3f}"
        print(f"| {shift} s | {r['counted']} | {r['tp']} | {r['fn']} | {r['fp']} | {rec} | {pre} |")
    for shift, r in results.items():
        print(f"shift {shift}: missed seconds {r['missed']}; false alarms {r['false_alarm']}")


# ---------------------------------------------------------------- classify

def load_detector():
    from vision.classify_core import DetrBackend, Detector
    t = time.perf_counter()
    backend = DetrBackend.load(os.environ.get("MODEL_DIR", "/models/detr-resnet-50"))
    return Detector(backend), time.perf_counter() - t


def run_card() -> None:
    import cv2
    detector, loaded = load_detector()
    img = cv2.imread(os.environ.get("CHECK_IMAGE", "/check/000000039769.jpg"))     # BGR, as the worker gets frames
    dets = detector.detect([img])[0]
    verdict = judge_card(dets)
    print(f"model loaded in {loaded:.1f} s; picture {img.shape[1]}x{img.shape[0]}")
    print("| Name | Card's score | Our score | Largest difference of a box coordinate (pixels) | Overlap (IoU) | Passes |")
    print("|---|---|---|---|---|---|")
    for (name, score, _), (n, s, o, ok, gap) in zip(CARD, verdict["pairs"]):
        print(f"| {name} | {score} | {'-' if s is None else f'{s:.4f}'} | {'-' if gap is None else f'{gap:.4f}'} | "
              f"{'-' if o is None else f'{o:.4f}'} | {ok} |")
    print(f"names not in the card's output: {verdict['extra']}")
    print("RESULT:", "PASS" if verdict["pass"] else "FAIL")


def run_pictures() -> None:
    from vision.classify_core import best_per_label, pick_frames
    from vision.video import open_frames
    detector, loaded = load_detector()
    fps, frames = open_frames(pathlib.Path(os.environ.get("CLIP", "/clip/check_clip.mp4")))
    picked = pick_frames(frames, fps, range(MOVING[0], MOVING[1]))
    imgs = [f for _, f in picked]
    detector.detect(imgs[:8])                                    # warm up (kernels, memory)
    t = time.perf_counter()
    results = detector.detect(imgs)                              # batches of 8, the way the worker does it
    batch_seconds = time.perf_counter() - t
    t = time.perf_counter()
    for im in imgs[:20]:
        detector.detect([im])
    single_seconds = (time.perf_counter() - t) / 20
    print(f"{len(imgs)} pictures; in batches of 8: {batch_seconds / len(imgs) * 1000:.1f} ms a picture "
          f"({batch_seconds:.2f} s in all); one at a time: {single_seconds * 1000:.1f} ms a picture; model loaded in {loaded:.1f} s")
    print("| Second of the video | Names and scores found (score above 0.9) |")
    print("|---|---|")
    none = 0
    for (t_, _), dets in zip(picked, results):
        best = best_per_label(dets)
        none += not best
        print(f"| {int(t_)} | {', '.join(f'{d.label} {d.score:.3f}' for d in best) or '(nothing above 0.9)'} |")
    print(f"pictures with nothing above 0.9: {none} of {len(imgs)}")
    names: dict[str, int] = {}
    for dets in results:
        for d in best_per_label(dets):
            names[d.label] = names.get(d.label, 0) + 1
    print("pictures a name was found in:", json.dumps(dict(sorted(names.items(), key=lambda kv: -kv[1])), ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["segments", "motion", "card", "pictures"])
    a = ap.parse_args()
    if a.what == "segments":
        make_segments()
    elif a.what == "motion":
        print_motion(run_motion())
    elif a.what == "card":
        run_card()
    else:
        run_pictures()
    return 0


if __name__ == "__main__":
    sys.exit(main())
