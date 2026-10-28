"""The motion worker (CPU). It reads `ccc:recorded` (a segment is in the index), looks at the segment with OpenCV background
subtraction, and for each run of seconds with movement it puts

  - an entry on `ccc:motion` (the classify worker takes it), and
  - a motion event on `ccc:events` (the writer takes it; the motion events do not need a GPU).

It only looks at the cameras listed in a file that is mounted read only (the simulated cameras of the test pattern move all
the time, so looking at them says nothing). It opens only files of the form <recordings>/<tenant>/<camera>/<segment name>.

  python -m vision.motion
"""
from __future__ import annotations

import logging
import os
import pathlib
import signal
import threading
from datetime import datetime, timedelta, timezone

from . import common as c
from .motion_core import DEFAULT, MotionParams, motion_intervals, second_values
from .video import open_frames

log = logging.getLogger("ccc.motion")


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def analyse_file(path, params: MotionParams = DEFAULT, opener=open_frames) -> list[tuple[int, int]]:
    """The motion intervals of one segment file: (first second, number of seconds)."""
    fps, frames = opener(path)
    return motion_intervals(second_values(frames, fps, params), params)


def make_handler(streams_out: c.Streams, root: pathlib.Path, allowed_file: pathlib.Path, counters: c.Counters,
                 params: MotionParams = DEFAULT, opener=open_frames):
    def handle(fields: dict) -> None:
        tenant, camera = c.parse_uuid(fields.get("tenant_id")), c.parse_uuid(fields.get("camera_id"))
        started = c.parse_time(fields.get("started_at"))
        if camera not in c.read_allowed(allowed_file):
            counters.add("camera_not_looked_at")        # confirmed without being looked at: not an error
            return
        path = c.segment_path(root, fields.get("tenant_id"), fields.get("camera_id"), fields.get("file_name"))
        if not path.is_file():
            raise c.Refused("the segment file is not there (deleted)")
        intervals = analyse_file(path, params, opener)
        counters.add("segments_looked_at")
        for first, seconds in intervals:
            at = started + timedelta(seconds=first)
            common = {"tenant_id": str(tenant), "camera_id": str(camera), "started_at": utc_iso(at),
                      "duration_ms": str(seconds * 1000)}
            streams_out.add(c.MOTION_STREAM, {**common, "segment_started_at": utc_iso(started),
                                               "file_name": fields["file_name"], "offset_s": str(first),
                                               "length_s": str(seconds)})
            streams_out.add(c.EVENT_STREAM, {**common, "kind": "motion", "label": "", "score": "", "model": ""})
            counters.add("motion_intervals")
    return handle


def build_client():
    import valkey
    e = os.environ
    with open(e.get("VALKEY_PASSWORD_FILE", "/run/secret/valkey_password"), encoding="utf-8") as f:
        password = f.read().strip()
    return valkey.Valkey(host=e.get("VALKEY_HOST", "valkey"), port=int(e.get("VALKEY_PORT", "6379")),
                         username=e.get("VALKEY_USER", "motion"), password=password, decode_responses=True,
                         socket_timeout=10)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    e = os.environ
    client = build_client()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    counters = c.Counters()
    reader = c.Streams(client, c.GROUP_MOTION, "motion-1", c.DEAD_STREAMS["motion"])
    writer = c.Streams(client, c.GROUP_MOTION, "motion-1", c.DEAD_STREAMS["motion"])
    handler = make_handler(writer, pathlib.Path(e.get("RECORDINGS_DIR", "/recordings")),
                           pathlib.Path(e.get("CAMERAS_FILE", "/run/vision/cameras.txt")), counters)
    log.info("motion worker started")
    c.run_loop(reader, c.RECORDED_STREAM, handler, counters, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
