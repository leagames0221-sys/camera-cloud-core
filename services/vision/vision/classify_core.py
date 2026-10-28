"""Naming the things in a picture with DETR (`facebook/detr-resnet-50`, revision `no_timm`; Apache-2.0).

Source of the way it is used: the model card's example. The picture is an RGB array (OpenCV gives BGR, the card's example
opens the picture with PIL, which gives RGB), the processor makes the model's input, and `post_process_object_detection`
keeps what has a score above 0.9 (the card: "let's only keep detections with score > 0.9").

torch and transformers are imported only when the model is loaded, so that everything else here can be tested without them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Protocol

import numpy as np

SCORE_MIN = 0.9             # a detection needs a score ABOVE this (the card's example value)
BATCH_SIZE = 1              # pictures given to the model at once: the transformers pipelines guide says "If you are latency constrained (live product doing inference), don't batch."; the worker reads recordings, so applying it is the author's reading (docs/adr/0011)
MODEL_COMMIT = "70120ba84d68ca1211e007c4fb61d0cd5424be54"   # revision `no_timm` of facebook/detr-resnet-50
MODEL_NAME = f"facebook/detr-resnet-50@{MODEL_COMMIT}"


class ModelNotUsable(Exception):
    """The worker must not run: no GPU, or the weights did not fill the model."""


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: tuple[float, float, float, float]      # x_min, y_min, x_max, y_max in pixels of the picture


def bgr_to_rgb(frame: np.ndarray) -> np.ndarray:
    """OpenCV's channel order (blue, green, red) to the model's (red, green, blue)."""
    return np.ascontiguousarray(frame[:, :, ::-1])


def filter_by_score(dets: Iterable[Detection], minimum: float = SCORE_MIN) -> list[Detection]:
    return [d for d in dets if d.score > minimum]


def best_per_label(dets: Iterable[Detection]) -> list[Detection]:
    """One detection for each name in a picture, the one with the highest score. The event's key has no box, so two cats in
    one picture would be one event anyway; this makes the choice (the surer one) instead of leaving it to arrival order."""
    best: dict[str, Detection] = {}
    for d in dets:
        if d.label not in best or d.score > best[d.label].score:
            best[d.label] = d
    return sorted(best.values(), key=lambda d: (-d.score, d.label))


def check_loading_info(info: dict | None) -> None:
    """`from_pretrained(..., output_loading_info=True)` says which weights the file lacked for the model and which it had
    for nothing, which had the wrong shape, and the errors met while loading (transformers 5.19: `LoadStateDictInfo.to_dict()`,
    the four keys below). Any of them stops the worker: a missing weight would silently stay random."""
    if not isinstance(info, dict):
        raise ModelNotUsable("the loader did not say what it loaded")
    missing, unexpected = sorted(info.get("missing_keys") or []), sorted(info.get("unexpected_keys") or [])
    mismatched = list(info.get("mismatched_keys") or [])      # a weight of the wrong shape is left random as well
    errors = [str(m) for m in (info.get("error_msgs") or [])]
    if missing or unexpected or mismatched or errors:
        raise ModelNotUsable(f"weights do not fit the model: {len(missing)} missing {missing[:3]}, "
                             f"{len(unexpected)} unexpected {unexpected[:3]}, {len(mismatched)} of the wrong shape, "
                             f"{len(errors)} errors {errors[:1]}")


def repaired_config(raw: dict) -> dict:
    """The `config.json` of revision `no_timm` has `"dilation": null`, where the library (transformers 5.19, DetrConfig) wants
    a true/false and says in its documentation that the default is False (the main revision's file says false). Only that
    value is replaced; the check of the weights (`check_loading_info`) is what shows whether the model is still the right one."""
    fixed = dict(raw)
    if fixed.get("dilation") is None:
        fixed["dilation"] = False
    return fixed


def require_cuda(torch_module) -> None:
    if not torch_module.cuda.is_available():
        raise ModelNotUsable("no CUDA device: the classify worker needs the GPU")


class Backend(Protocol):
    def infer(self, rgb_images: list[np.ndarray]) -> list[list[Detection]]: ...


class Detector:
    """Frames in (BGR, as OpenCV gives them), detections out. The picture is turned to RGB here and nowhere else."""

    def __init__(self, backend: Backend, batch_size: int = BATCH_SIZE) -> None:
        self.backend, self.batch_size = backend, batch_size

    def detect(self, frames_bgr: list[np.ndarray]) -> list[list[Detection]]:
        out: list[list[Detection]] = []
        for i in range(0, len(frames_bgr), self.batch_size):
            rgb = [bgr_to_rgb(f) for f in frames_bgr[i:i + self.batch_size]]
            out.extend(filter_by_score(d) for d in self.backend.infer(rgb))
        return out


class DetrBackend:
    """The real model on the GPU."""

    def __init__(self, processor, model, torch_module, device: str = "cuda") -> None:
        self.processor, self.model, self.torch, self.device = processor, model, torch_module, device

    @classmethod
    def load(cls, model_dir: str) -> "DetrBackend":
        import json
        import pathlib

        import torch
        from transformers import DetrConfig, DetrForObjectDetection, DetrImageProcessor

        require_cuda(torch)
        raw = json.loads((pathlib.Path(model_dir) / "config.json").read_text(encoding="utf-8"))
        processor = DetrImageProcessor.from_pretrained(model_dir)
        model, info = DetrForObjectDetection.from_pretrained(
            model_dir, config=DetrConfig.from_dict(repaired_config(raw)), output_loading_info=True)
        check_loading_info(info)
        return cls(processor, model.to("cuda").eval(), torch, "cuda")

    def infer(self, rgb_images: list[np.ndarray]) -> list[list[Detection]]:
        torch = self.torch
        inputs = self.processor(images=rgb_images, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        sizes = torch.tensor([im.shape[:2] for im in rgb_images], device=self.device)     # (height, width)
        results = self.processor.post_process_object_detection(outputs, target_sizes=sizes, threshold=SCORE_MIN)
        names = self.model.config.id2label
        return [[Detection(names[int(lab)], float(score), tuple(float(v) for v in box))
                 for score, lab, box in zip(r["scores"].tolist(), r["labels"].tolist(), r["boxes"].tolist())]
                for r in results]


def pick_frames(frames: Iterator[np.ndarray], fps: float, seconds: Iterable[int]) -> list[tuple[float, np.ndarray]]:
    """For each wanted second of the segment, the first frame whose time is that second or later. The frames are read from
    the start, in order, and the reading stops at the last wanted second. Returns (time of the frame in seconds, frame)."""
    wanted = sorted(set(seconds))
    picked: list[tuple[float, np.ndarray]] = []
    if not wanted:
        return picked
    i = 0
    for n, frame in enumerate(frames):
        t = n / fps
        if t + 1e-9 >= wanted[i]:
            picked.append((t, frame))
            i += 1
            while i < len(wanted) and t + 1e-9 >= wanted[i]:      # a gap in the frames: the same frame serves
                i += 1
            if i >= len(wanted):
                break
    return picked
