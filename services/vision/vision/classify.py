"""The classify worker (GPU). It reads `ccc:motion` (an interval with movement), takes one picture for each second of the
interval, names what is in the pictures with DETR, and puts an event for each name on `ccc:events`.

It does not start without a GPU, and not unless the weights fill the whole model. The weights are read from a folder that is
mounted read only; the Hugging Face libraries are told not to go online (HF_HUB_OFFLINE=1 in the image).

  python -m vision.classify
"""
from __future__ import annotations

import logging
import os
import pathlib
import signal
import threading
from datetime import timedelta

from . import common as c
from .classify_core import MODEL_NAME, Detector, best_per_label, pick_frames
from .motion import build_client, utc_iso
from .video import open_frames

log = logging.getLogger("ccc.classify")

MAX_INTERVAL_SECONDS = 3600


def make_handler(streams_out: c.Streams, root: pathlib.Path, detector: Detector, counters: c.Counters,
                 opener=open_frames):
    def handle(fields: dict) -> None:
        tenant, camera = c.parse_uuid(fields.get("tenant_id")), c.parse_uuid(fields.get("camera_id"))
        segment_start = c.parse_time(fields.get("segment_started_at"))
        offset = c.parse_int(fields.get("offset_s"), 0, MAX_INTERVAL_SECONDS)
        length = c.parse_int(fields.get("length_s"), 1, MAX_INTERVAL_SECONDS)
        path = c.segment_path(root, fields.get("tenant_id"), fields.get("camera_id"), fields.get("file_name"))
        if not path.is_file():
            raise c.Refused("the segment file is not there (deleted)")
        fps, frames = opener(path)
        picked = pick_frames(frames, fps, range(offset, offset + length))
        counters.add("pictures", len(picked))
        results = detector.detect([f for _, f in picked])
        for (t, _), dets in zip(picked, results):
            at = segment_start + timedelta(milliseconds=round(t * 1000))
            for d in best_per_label(dets):
                streams_out.add(c.EVENT_STREAM, {
                    "tenant_id": str(tenant), "camera_id": str(camera), "kind": "object", "label": d.label,
                    "score": f"{d.score:.4f}", "started_at": utc_iso(at), "duration_ms": "0", "model": MODEL_NAME})
                counters.add("object_events")
    return handle


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from .classify_core import DetrBackend, ModelNotUsable

    e = os.environ
    try:
        backend = DetrBackend.load(e.get("MODEL_DIR", "/models/detr-resnet-50"))
    except ModelNotUsable as why:
        log.error("not starting: %s", why)
        return 2
    os.environ.setdefault("VALKEY_USER", "classify")
    client = build_client()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    counters = c.Counters()
    streams = c.Streams(client, c.GROUP_CLASSIFY, "classify-1", c.DEAD_STREAMS["classify"])
    handler = make_handler(streams, pathlib.Path(e.get("RECORDINGS_DIR", "/recordings")), Detector(backend), counters)
    log.info("classify worker started on the GPU")
    c.run_loop(streams, c.MOTION_STREAM, handler, counters, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
