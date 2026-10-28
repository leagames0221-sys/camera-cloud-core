"""The intake queue of one stamp and the batch writes behind it (ADR 0007).

MQTT state messages and MediaMTX's closed-segment calls are not written to the database one by one. The writer puts each
of them on a Valkey stream (XADD, then it answers at once) and a reader of the same writer takes them in groups of up to
125 or 50 milliseconds (whichever is first), writes a group to PostgreSQL in one statement, and only after the commit tells
Valkey it is done (XACK). An entry that was handed out but not confirmed within 30 seconds is taken again (XAUTOCLAIM);
one that has been handed out more than 5 times is moved to a stream of dead entries and confirmed, so a message that
cannot be written does not stay in the way. Writing the same entry twice gives the same result (an upsert that only
accepts newer data; an insert that does nothing when the row is there).

Two more streams belong to the workers that look at the recordings (docs/adr/0011): after a group of segments has been
committed, and before it is confirmed, the segments of the group that are in the index are announced on `ccc:recorded` (so a
crash in between announces them again: at least once); and the events those workers put on `ccc:events` are written to the
`events` table, each one only if its camera, its stamp and a segment that contains its time are in the index.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .segments import HookEvent

log = logging.getLogger("ccc.intake")

STATE_STREAM = "ccc:state"
SEGMENT_STREAM = "ccc:segments"
DEAD_STREAM = "ccc:dead"
RECORDED_STREAM = "ccc:recorded"   # written here: a segment is in the index (taken by the motion worker)
EVENT_STREAM = "ccc:events"        # written by the motion and classify workers, taken here
GROUP = "writers"
READ_STREAMS = (STATE_STREAM, SEGMENT_STREAM, EVENT_STREAM)

# Sources and the author's decisions: docs/adr/0007.
MAX_STREAM_LENGTH = 100_000   # XADD ... MAXLEN ~ (the author's design decision, docs/adr/0007)
BATCH_SIZE = 125              # Logstash's `pipeline.batch.size` default
BATCH_SECONDS = 0.05          # Logstash's `pipeline.batch.delay` default (50 ms); Logstash waits per event, here it is counted from the first entry
CLAIM_IDLE_MS = 30_000        # Azure Queue Storage's default visibility timeout (30 s)
CLAIM_EVERY_SECONDS = 30.0    # BullMQ's `stalledInterval` default (30000 ms)
MAX_DELIVERIES = 5            # Azure Functions' default maxDequeueCount: an entry handed out a 6th time goes to the dead stream
MAX_SEGMENT_SECONDS = 120     # twice the 60-second segment (the author's decision, docs/adr/0007); a longer or empty one is not indexed


@dataclass(frozen=True)
class StateRow:
    camera_id: uuid.UUID
    state: str
    reason: str | None
    fps: float | None
    bitrate_kbps: float | None
    seen_at: datetime


@dataclass(frozen=True)
class SegmentRow:
    tenant_id: uuid.UUID
    camera_id: uuid.UUID
    started_at: datetime
    duration_ms: int
    file_name: str


EVENT_KINDS = ("motion", "object")
MAX_LABEL_LENGTH = 100
# An event lies inside one segment (movement is not joined across segments, docs/adr/0011), so it is never longer than the
# longest segment the index accepts. INSERT_EVENTS checks the end against the segment itself.
MAX_EVENT_MS = 120_000
EVENT_END_SLACK_MS = 1000     # the last second of a segment may be counted whole by the motion worker


@dataclass(frozen=True)
class EventRow:
    tenant_id: uuid.UUID
    camera_id: uuid.UUID
    kind: str
    label: str            # '' for a motion event (a NULL would not make two rows equal in a UNIQUE key)
    started_at: datetime
    duration_ms: int
    score: float | None
    model: str


def entry_time(entry_id: str) -> datetime:
    """The time Valkey gave the entry when it took it in (the milliseconds in its id): the order of arrival."""
    return datetime.fromtimestamp(int(entry_id.split("-")[0]) / 1000, tz=timezone.utc)


def _num(v: str | None) -> float | None:
    return float(v) if v not in (None, "") else None


def state_row(entry_id: str, f: dict) -> StateRow:
    return StateRow(uuid.UUID(f["camera_id"]), f["state"], f.get("reason") or None, _num(f.get("fps")),
                    _num(f.get("bitrate")), entry_time(entry_id))


def segment_row(f: dict) -> SegmentRow:
    return SegmentRow(uuid.UUID(f["tenant_id"]), uuid.UUID(f["camera_id"]), datetime.fromisoformat(f["started_at"]),
                      int(f["duration_ms"]), f["file_name"])


def event_row(f: dict) -> EventRow:
    """An event from the stream. Raises ValueError/KeyError for anything the table would refuse (a row the table refuses would
    fail the whole group, again and again), so such an entry is counted and left out instead."""
    kind, label = f["kind"], f.get("label") or ""
    if kind not in EVENT_KINDS or len(label) > MAX_LABEL_LENGTH or (kind == "motion" and label):
        raise ValueError("not an event")
    started = datetime.fromisoformat(f["started_at"])
    if started.tzinfo is None:
        raise ValueError("a time without a zone")
    duration = int(f.get("duration_ms") or 0)
    if not 0 <= duration <= MAX_EVENT_MS:
        raise ValueError("duration out of range")
    score = _num(f.get("score"))
    if score is not None and not 0.0 <= score <= 1.0:
        raise ValueError("score out of range")
    return EventRow(uuid.UUID(f["tenant_id"]), uuid.UUID(f["camera_id"]), kind, label, started, duration, score,
                    (f.get("model") or "")[:200])


def read_camera_list(path) -> set[uuid.UUID]:
    """The cameras whose recordings are looked at (the list the motion worker reads, var/vision/cameras.txt): one camera id per
    line, `#` starts a comment. A file that is missing or cannot be read means none. The writer takes events only of these
    cameras, so a worker that was taken over cannot put events on any other camera, of any company."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    out: set[uuid.UUID] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        try:
            out.add(uuid.UUID(line))
        except ValueError:
            continue
    return out


def recorded_fields(r: SegmentRow, duration_ms: int | None) -> dict:
    """What the motion worker is told about a segment. The path is not in it: the worker builds the path from the ids and the
    file name, and only in the recordings folder."""
    return {"tenant_id": str(r.tenant_id), "camera_id": str(r.camera_id), "started_at": r.started_at.isoformat(),
            "duration_ms": "" if duration_ms is None else str(duration_ms), "file_name": r.file_name}


def newest_state_per_camera(rows: list[StateRow]) -> list[StateRow]:
    """One row per camera, the one that arrived last. A single INSERT ... ON CONFLICT DO UPDATE fails if it would touch
    a row twice, so the batch must not hold the same camera twice."""
    best: dict[uuid.UUID, StateRow] = {}
    for r in rows:
        if r.camera_id not in best or r.seen_at >= best[r.camera_id].seen_at:
            best[r.camera_id] = r
    return list(best.values())


def check_segment(ev: HookEvent) -> str | None:
    """None if the segment may enter the index, else the reason it may not: it needs a length above 0 and not above
    twice the segment length (a broken or unfinished file is not indexed). The start time is already known to be readable
    (parse_hook_body), and the path to be <tenant>/<camera>."""
    if ev.duration_ms is None or ev.duration_ms <= 0:
        return "no_length"
    if ev.duration_ms > MAX_SEGMENT_SECONDS * 1000:
        return "too_long"
    return None


def segment_fields(ev: HookEvent) -> dict:
    return {"tenant_id": str(ev.tenant_id), "camera_id": str(ev.camera_id), "started_at": ev.started_at.isoformat(),
            "duration_ms": str(ev.duration_ms), "file_name": ev.file_name}


def state_fields(camera_id: uuid.UUID, state: str, reason: str | None, fps: float | None, bitrate: float | None) -> dict:
    return {"camera_id": str(camera_id), "state": state, "reason": reason or "",
            "fps": "" if fps is None else repr(fps), "bitrate": "" if bitrate is None else repr(bitrate)}


class Counters:
    """Numbers shown at /internal/v1/counters; a count that should be 0 (dead, rejected) is how a problem is seen."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def add(self, name: str, n: int = 1) -> None:
        self.values[name] = self.values.get(name, 0) + n


# ---------------------------------------------------------------- database

UPSERT_STATES = """
INSERT INTO camera_status (camera_id, tenant_id, state, reason, fps, bitrate_kbps, changed_at, seen_at)
SELECT u.camera_id, c.tenant_id, u.state, u.reason, u.fps, u.bitrate, u.t, u.t
FROM unnest($1::uuid[], $2::text[], $3::text[], $4::float4[], $5::float4[], $6::timestamptz[])
     AS u(camera_id, state, reason, fps, bitrate, t)
JOIN cameras c ON c.id = u.camera_id AND c.deleted_at IS NULL AND c.stamp_id = $7
ON CONFLICT (camera_id) DO UPDATE SET
    changed_at = CASE WHEN camera_status.state = EXCLUDED.state THEN camera_status.changed_at ELSE EXCLUDED.changed_at END,
    state = EXCLUDED.state, reason = EXCLUDED.reason, fps = EXCLUDED.fps, bitrate_kbps = EXCLUDED.bitrate_kbps,
    seen_at = EXCLUDED.seen_at
WHERE camera_status.seen_at <= EXCLUDED.seen_at
"""

# One statement: the rows whose camera is known, not deleted and of this stamp are inserted; the answer is how many rows
# passed that check and how many of them were new (the others were there already).
INSERT_SEGMENTS = """
WITH ok AS (
    SELECT u.camera_id, u.tenant_id, u.started_at, u.duration_ms, u.file_name
    FROM unnest($1::uuid[], $2::uuid[], $3::timestamptz[], $4::int[], $5::text[])
         AS u(camera_id, tenant_id, started_at, duration_ms, file_name)
    JOIN cameras c ON c.id = u.camera_id AND c.tenant_id = u.tenant_id AND c.deleted_at IS NULL AND c.stamp_id = $6
), ins AS (
    INSERT INTO recording_segments (camera_id, tenant_id, started_at, duration_ms, file_name, source)
    SELECT camera_id, tenant_id, started_at, duration_ms, file_name, 'hook' FROM ok
    ON CONFLICT (camera_id, started_at) DO NOTHING
    RETURNING 1
)
SELECT (SELECT count(*) FROM ok) AS known, (SELECT count(*) FROM ins) AS written
"""


# The event is written only if its camera is known, not deleted and of this stamp, and a segment of that camera that is in the
# index (not deleted) contains its time and its end (with one second of slack for the last second of the segment). A segment
# whose length is not known (found by the reconciliation) counts as the longest one that is accepted. The answer: how many
# rows passed the checks and how many of them were new.
INSERT_EVENTS = """
WITH ok AS (
    SELECT u.camera_id, u.tenant_id, u.kind, u.label, u.started_at, u.duration_ms, u.score, u.model
    FROM unnest($1::uuid[], $2::uuid[], $3::text[], $4::text[], $5::timestamptz[], $6::int[], $7::float4[], $8::text[])
         AS u(camera_id, tenant_id, kind, label, started_at, duration_ms, score, model)
    JOIN cameras c ON c.id = u.camera_id AND c.tenant_id = u.tenant_id AND c.deleted_at IS NULL AND c.stamp_id = $9
    WHERE EXISTS (
        SELECT 1 FROM recording_segments s
        WHERE s.camera_id = u.camera_id AND s.tenant_id = u.tenant_id AND NOT s.deleted
          AND s.started_at <= u.started_at
          AND u.started_at < s.started_at + COALESCE(s.duration_ms, 120000) * interval '1 millisecond'
          AND u.started_at + u.duration_ms * interval '1 millisecond'
              <= s.started_at + (COALESCE(s.duration_ms, 120000) + 1000) * interval '1 millisecond')
), ins AS (
    INSERT INTO events (camera_id, tenant_id, kind, label, started_at, duration_ms, score, model)
    SELECT camera_id, tenant_id, kind, label, started_at, duration_ms, score, model FROM ok
    ON CONFLICT (camera_id, kind, label, started_at) DO NOTHING
    RETURNING 1
)
SELECT (SELECT count(*) FROM ok) AS known, (SELECT count(*) FROM ins) AS written
"""

# Which of these (camera, start) pairs are in the index now. What is announced comes from the index, not from the entry.
SELECT_INDEXED = """
SELECT s.camera_id, s.tenant_id, s.started_at, s.duration_ms, s.file_name
FROM unnest($1::uuid[], $2::timestamptz[]) AS u(camera_id, started_at)
JOIN recording_segments s ON s.camera_id = u.camera_id AND s.started_at = u.started_at AND NOT s.deleted
"""


class Sink:
    """The writes of one stamp's database. A camera that is not in this stamp's camera table (unknown, deleted, or in
    another stamp) is left out by the join in the same statement, so nothing is written for it."""

    def __init__(self, pool, stamp_id: int) -> None:
        self._pool, self._stamp = pool, stamp_id

    async def apply_states(self, rows: list[StateRow]) -> int:
        rows = newest_state_per_camera(rows)
        if not rows:
            return 0
        tag = await self._pool.execute(
            UPSERT_STATES, [r.camera_id for r in rows], [r.state for r in rows], [r.reason for r in rows],
            [r.fps for r in rows], [r.bitrate_kbps for r in rows], [r.seen_at for r in rows], self._stamp)
        return int(tag.split()[-1])

    async def apply_segments(self, rows: list[SegmentRow]) -> tuple[int, int]:
        """(rows written, rows left out because the camera is unknown, deleted or another stamp's)."""
        if not rows:
            return 0, 0
        r = await self._pool.fetchrow(
            INSERT_SEGMENTS, [r.camera_id for r in rows], [r.tenant_id for r in rows], [r.started_at for r in rows],
            [r.duration_ms for r in rows], [r.file_name for r in rows], self._stamp)
        return int(r["written"]), len(rows) - int(r["known"])

    async def apply_events(self, rows: list[EventRow]) -> tuple[int, int]:
        """(rows written, rows left out because a check failed)."""
        if not rows:
            return 0, 0
        r = await self._pool.fetchrow(
            INSERT_EVENTS, [r.camera_id for r in rows], [r.tenant_id for r in rows], [r.kind for r in rows],
            [r.label for r in rows], [r.started_at for r in rows], [r.duration_ms for r in rows],
            [r.score for r in rows], [r.model for r in rows], self._stamp)
        return int(r["written"]), len(rows) - int(r["known"])

    async def indexed_segments(self, keys: list[tuple[uuid.UUID, datetime]]) -> list[tuple[SegmentRow, int | None]]:
        """The segments among (camera, start) pairs that are in the index, as the index has them: (row, length or None)."""
        if not keys:
            return []
        found = await self._pool.fetch(SELECT_INDEXED, [k[0] for k in keys], [k[1] for k in keys])
        return [(SegmentRow(r["tenant_id"], r["camera_id"], r["started_at"], r["duration_ms"] or 0, r["file_name"]),
                 r["duration_ms"]) for r in found]


# ---------------------------------------------------------------- queue

class Queue:
    """The Valkey side: adding, taking in groups, confirming, taking again, moving to the dead stream."""

    def __init__(self, client, consumer: str) -> None:
        self.r = client
        self.consumer = consumer

    async def ensure_groups(self) -> None:
        for stream in READ_STREAMS:
            try:
                await self.r.xgroup_create(stream, GROUP, id="0", mkstream=True)
            except Exception as e:  # noqa: BLE001 - "BUSYGROUP" means it is already there
                if "BUSYGROUP" not in str(e):
                    raise

    async def add(self, stream: str, fields: dict) -> str:
        return await self.r.xadd(stream, fields, maxlen=MAX_STREAM_LENGTH, approximate=True)

    async def take(self, count: int, block_ms: int | None) -> dict[str, list[tuple[str, dict]]]:
        """New entries of the streams (those never handed out), at most `count` in all."""
        res = await self.r.xreadgroup(GROUP, self.consumer, {s: ">" for s in READ_STREAMS}, count=count, block=block_ms)
        return {name: entries for name, entries in (res or []) if entries}

    async def ack(self, stream: str, ids: list[str]) -> None:
        if ids:
            await self.r.xack(stream, GROUP, *ids)

    async def reclaim(self, stream: str) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
        """Entries not confirmed for 30 seconds, taken over by this consumer: (to process again, to give up on). An
        entry handed out for the 6th time or more is in the second list."""
        _, entries, _deleted = await self.r.xautoclaim(stream, GROUP, self.consumer, CLAIM_IDLE_MS, "0-0",
                                                       count=BATCH_SIZE)
        if not entries:
            return [], []
        pend = await self.r.xpending_range(stream, GROUP, entries[0][0], entries[-1][0], len(entries) * 10)
        times = {p["message_id"]: p["times_delivered"] for p in pend}
        again, dead = [], []
        for eid, f in entries:
            (dead if times.get(eid, 0) > MAX_DELIVERIES else again).append((eid, f))
        return again, dead

    async def bury(self, stream: str, entries: list[tuple[str, dict]]) -> None:
        """Keep a copy of what could not be written, then confirm it so it stops coming back."""
        for eid, f in entries:
            await self.r.xadd(DEAD_STREAM, {"stream": stream, "id": eid, "fields": json.dumps(f)},
                              maxlen=MAX_STREAM_LENGTH, approximate=True)
        await self.ack(stream, [eid for eid, _ in entries])


async def announce_segments(sink, queue, counters: Counters, rows: list[SegmentRow]) -> bool:
    """Tell the motion worker about every segment of the group that is in the index (also one that was there before: a group
    that is written again after a crash announces again). A failure is counted and does not stop the writing."""
    try:
        for seg, length in await sink.indexed_segments([(r.camera_id, r.started_at) for r in rows]):
            await queue.add(RECORDED_STREAM, recorded_fields(seg, length))
            counters.add("recorded_announced")
        return True
    except Exception as e:  # noqa: BLE001
        counters.add("recorded_not_announced", len(rows))
        log.warning("could not announce %d segments (%s)", len(rows), e)
        return False


async def write_group(sink: Sink, queue: Queue, counters: Counters,
                      by_stream: dict[str, list[tuple[str, dict]]], announce: bool = False,
                      event_cameras: Callable[[], set[uuid.UUID]] | None = None) -> None:
    """Write what was taken and confirm it after the database has committed (the order is the whole point). With `announce`
    the segments that are in the index are announced after the commit and before the confirmation. `event_cameras` gives the
    cameras whose events are taken (the writer always passes it; None, in tests of other parts, means every camera)."""
    states = by_stream.get(STATE_STREAM, [])
    segments = by_stream.get(SEGMENT_STREAM, [])
    if states:
        rows = []
        for eid, f in states:
            try:
                rows.append(state_row(eid, f))
            except (KeyError, ValueError):
                counters.add("state_unreadable")
        n = await sink.apply_states(rows)
        counters.add("state_rows_written", n)
        counters.add("state_entries_done", len(states))
        await queue.ack(STATE_STREAM, [eid for eid, _ in states])
    if segments:
        rows = []
        for eid, f in segments:
            try:
                rows.append(segment_row(f))
            except (KeyError, ValueError):
                counters.add("segment_unreadable")
        n, outside = await sink.apply_segments(rows)
        counters.add("segment_rows_written", n)
        if outside:   # the camera is unknown, deleted or another stamp's: not indexed, and counted
            counters.add("segment_not_this_stamp", outside)
        if announce and rows:
            await announce_segments(sink, queue, counters, rows)
        counters.add("segment_entries_done", len(segments))
        await queue.ack(SEGMENT_STREAM, [eid for eid, _ in segments])
    events = by_stream.get(EVENT_STREAM, [])
    if events:
        evs = []
        for eid, f in events:
            try:
                evs.append(event_row(f))
            except (KeyError, ValueError):
                counters.add("event_unreadable")
        if event_cameras is not None:
            looked_at = event_cameras()
            kept = [e for e in evs if e.camera_id in looked_at]
            if len(kept) < len(evs):    # a camera the workers do not look at: whoever wrote it, it is not taken
                counters.add("event_camera_not_looked_at", len(evs) - len(kept))
            evs = kept
        n, outside = await sink.apply_events(evs)
        counters.add("event_rows_written", n)
        if outside:    # the camera is unknown, deleted or another stamp's, or no segment in the index contains the time
            counters.add("event_rejected", outside)
        counters.add("event_entries_done", len(events))
        await queue.ack(EVENT_STREAM, [eid for eid, _ in events])


async def read_loop(sink: Sink, queue: Queue, counters: Counters, stop: asyncio.Event, announce: bool = False,
                    event_cameras: Callable[[], set[uuid.UUID]] | None = None) -> None:
    """Take a group (125 entries or 50 ms after the first, whichever comes first), write it, confirm it."""
    await queue.ensure_groups()
    last_claim = 0.0
    while not stop.is_set():
        try:
            group = await queue.take(BATCH_SIZE, 1000)
            if group:
                deadline = time.monotonic() + BATCH_SECONDS
                total = sum(len(v) for v in group.values())
                while total < BATCH_SIZE and time.monotonic() < deadline and not stop.is_set():
                    await asyncio.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
                    more = await queue.take(BATCH_SIZE - total, None)
                    for name, entries in more.items():
                        group.setdefault(name, []).extend(entries)
                        total += len(entries)
                await write_group(sink, queue, counters, group, announce, event_cameras)
                counters.add("groups_written")
            if time.monotonic() - last_claim >= CLAIM_EVERY_SECONDS:
                last_claim = time.monotonic()
                for stream in READ_STREAMS:
                    again, dead = await queue.reclaim(stream)
                    if again:
                        counters.add("reclaimed", len(again))
                        await write_group(sink, queue, counters, {stream: again}, announce, event_cameras)
                    if dead:
                        counters.add("dead", len(dead))
                        log.warning("moved %d entries of %s to the dead stream", len(dead), stream)
                        await queue.bury(stream, dead)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - whatever failed, the entries are still unconfirmed and come back
            counters.add("errors")
            log.warning("writing a group failed (%s); the entries stay unconfirmed", e)
            await asyncio.sleep(1.0)
