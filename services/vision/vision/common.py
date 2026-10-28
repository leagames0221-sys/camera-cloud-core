"""What the two workers share: the names of the streams, the checks on what is read from a stream, and the loop that takes
entries from a Valkey stream, handles them one at a time and confirms them (the rules are the ones of the stamp's writer,
services/api/app/intake.py: an entry not confirmed for 30 seconds is taken again; one that has been handed out more than 5
times is moved to the worker's own dead stream and confirmed). Each worker has a dead stream of its own and not the writer's
`ccc:dead`: an XADD may trim the stream it adds to (MAXLEN / MINID), which the queue's rules cannot forbid, so a worker that could
add to `ccc:dead` could also empty it.

Nothing here reads a path or a camera from a stream without checking it first: a stream is written by other processes.
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger("ccc.vision")

RECORDED_STREAM = "ccc:recorded"    # the writer: a segment is in the index
MOTION_STREAM = "ccc:motion"        # motion: an interval with movement in a segment
EVENT_STREAM = "ccc:events"         # motion and classify: events, taken by the writer
DEAD_STREAM = "ccc:dead"            # the writer's; the workers use their own (below)
DEAD_STREAMS = {"motion": "ccc:dead:motion", "classify": "ccc:dead:classify"}
GROUP_MOTION = "motion"
GROUP_CLASSIFY = "classify"

MAX_STREAM_LENGTH = 100_000         # the author's design decision (docs/adr/0007)
CLAIM_IDLE_MS = 30_000              # Azure Queue Storage's default visibility timeout (docs/adr/0007)
CLAIM_EVERY_SECONDS = 30.0          # BullMQ's `stalledInterval` default (30000 ms) (docs/adr/0007)
MAX_DELIVERIES = 5                  # Azure Functions' default maxDequeueCount: handed out a 6th time = moved to the dead stream


class Refused(Exception):
    """An entry that must not be processed (not a camera we look at, a path outside the recordings, a file that is gone).
    It is confirmed and counted, not tried again."""


# ---------------------------------------------------------------- what is read from a stream

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# the file name MediaMTX gives a segment: recordPath ./recordings/%path/%Y-%m-%d_%H-%M-%S-%f (+ .mp4 for fmp4)
_SEGMENT_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6}\.mp4$")


def parse_uuid(value: object) -> uuid.UUID:
    if not isinstance(value, str) or not _UUID_RE.match(value):
        raise Refused("not a lower case uuid")
    return uuid.UUID(value)


def segment_path(root: pathlib.Path, tenant_id: object, camera_id: object, file_name: object) -> pathlib.Path:
    """The file of a segment inside the recordings folder: <root>/<tenant>/<camera>/<file name>, and nothing else. A name
    with a slash, a `..`, a drive or an absolute path is refused before any file is touched."""
    tenant, camera = parse_uuid(tenant_id), parse_uuid(camera_id)
    if not isinstance(file_name, str) or not _SEGMENT_FILE_RE.match(file_name):
        raise Refused("not the name of a segment file")
    path = root / str(tenant) / str(camera) / file_name
    if path.parent.parent.parent != root:      # belt and braces: the parts above cannot have added a level
        raise Refused("outside the recordings")
    return path


def read_allowed(path: pathlib.Path | str) -> set[uuid.UUID]:
    """The cameras that are looked at: one camera id per line, `#` starts a comment. A missing file means none. The file is
    read for every entry (it is a few lines), so that a camera added later is picked up without a restart."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return set()
    out: set[uuid.UUID] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and _UUID_RE.match(line):
            out.add(uuid.UUID(line))
    return out


def parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise Refused("no time")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise Refused("not a time") from None
    if dt.tzinfo is None:
        raise Refused("a time without a zone")
    return dt.astimezone(timezone.utc)


def parse_int(value: object, low: int, high: int) -> int:
    try:
        n = int(value)       # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise Refused("not a number") from None
    if not low <= n <= high:
        raise Refused("number out of range")
    return n


def finite_fps(value: float | None, default: float = 15.0) -> float:
    return value if value is not None and math.isfinite(value) and value > 0 else default


# ---------------------------------------------------------------- numbers

class Counters:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def add(self, name: str, n: int = 1) -> None:
        self.values[name] = self.values.get(name, 0) + n


# ---------------------------------------------------------------- the stream

class Streams:
    """One worker's view of the queue: its consumer group and consumer name, and the five things it does with a stream."""

    def __init__(self, client, group: str, consumer: str, dead_stream: str = DEAD_STREAM) -> None:
        self.r, self.group, self.consumer, self.dead_stream = client, group, consumer, dead_stream

    def ensure_group(self, stream: str) -> None:
        try:
            self.r.xgroup_create(stream, self.group, id="0", mkstream=True)
        except Exception as e:  # noqa: BLE001 - "BUSYGROUP" means it is already there
            if "BUSYGROUP" not in str(e):
                raise

    def add(self, stream: str, fields: dict) -> str:
        return self.r.xadd(stream, fields, maxlen=MAX_STREAM_LENGTH, approximate=True)

    def take(self, stream: str, count: int, block_ms: int | None) -> list[tuple[str, dict]]:
        res = self.r.xreadgroup(self.group, self.consumer, {stream: ">"}, count=count, block=block_ms)
        return [entry for _name, entries in (res or []) for entry in entries]

    def ack(self, stream: str, ids: list[str]) -> None:
        if ids:
            self.r.xack(stream, self.group, *ids)

    def reclaim(self, stream: str) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
        """Entries not confirmed for 30 seconds, now this consumer's: (to handle again, to give up on). An entry handed out
        for the 6th time or more is in the second list."""
        _, entries, _deleted = self.r.xautoclaim(stream, self.group, self.consumer, CLAIM_IDLE_MS, "0-0", count=50)
        if not entries:
            return [], []
        pend = self.r.xpending_range(stream, self.group, entries[0][0], entries[-1][0], len(entries) * 10)
        times = {p["message_id"]: p["times_delivered"] for p in pend}
        again, dead = [], []
        for eid, f in entries:
            (dead if times.get(eid, 0) > MAX_DELIVERIES else again).append((eid, f))
        return again, dead

    def bury(self, stream: str, entries: list[tuple[str, dict]]) -> None:
        """Keep a copy of what could not be handled, then confirm it so it stops coming back."""
        for eid, f in entries:
            self.r.xadd(self.dead_stream, {"stream": stream, "id": eid, "fields": json.dumps(f)},
                        maxlen=MAX_STREAM_LENGTH, approximate=True)
        self.ack(stream, [eid for eid, _ in entries])


Handler = Callable[[dict], None]


def handle_entries(streams: Streams, stream: str, entries: list[tuple[str, dict]], handler: Handler,
                   counters: Counters) -> None:
    """Handle each entry and confirm it after the handler returned. A refused entry is confirmed and counted; an entry whose
    handler failed stays unconfirmed (it comes back after 30 seconds, up to the 5th time)."""
    for eid, fields in entries:
        try:
            handler(fields)
        except Refused as e:
            counters.add("refused")
            log.info("entry %s refused: %s", eid, e)
        except Exception as e:  # noqa: BLE001 - whatever failed, the entry is not confirmed and comes back
            counters.add("errors")
            log.warning("entry %s failed (%s); it stays unconfirmed", eid, e)
            continue
        streams.ack(stream, [eid])
        counters.add("handled")


def take_again(streams: Streams, stream: str, handler: Handler, counters: Counters) -> None:
    again, dead = streams.reclaim(stream)
    if again:
        counters.add("reclaimed", len(again))
        handle_entries(streams, stream, again, handler, counters)
    if dead:
        counters.add("dead", len(dead))
        log.warning("moved %d entries of %s to the dead stream", len(dead), stream)
        streams.bury(stream, dead)


def run_loop(streams: Streams, stream: str, handler: Handler, counters: Counters, stop: threading.Event,
             wait: Callable[[float], object] = time.sleep) -> None:
    """Take one entry at a time (the work on one is long), handle it, and every 30 seconds look for entries to take again."""
    streams.ensure_group(stream)
    last_claim = last_report = 0.0
    while not stop.is_set():
        try:
            handle_entries(streams, stream, streams.take(stream, 1, 1000), handler, counters)
            now = time.monotonic()
            if now - last_claim >= CLAIM_EVERY_SECONDS:
                last_claim = now
                take_again(streams, stream, handler, counters)
            if now - last_report >= 60.0:
                last_report = now
                log.info("counters %s", json.dumps(counters.values, sort_keys=True))
        except Exception as e:  # noqa: BLE001 - the queue is not reachable: wait and try again
            counters.add("queue_errors")
            log.warning("the queue failed (%s)", e)
            wait(1.0)
