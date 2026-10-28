"""The recording index: the hook receiver's parsing and the reconciliation against MediaMTX.

MediaMTX calls `runOnRecordSegmentComplete` with MTX_PATH, MTX_SEGMENT_PATH and MTX_SEGMENT_DURATION (its settings
file lists RTSP_PORT and, for a path name that is a regular expression, G1, G2 ... as well; none of them is the start
time), so the start time is read from the file name (recordPath ends in %Y-%m-%d_%H-%M-%S-%f). That pattern has no %z,
and MediaMTX reads and writes such a name in the local time zone of its process (`time.Local` in
internal/recordstore/path.go). The names are in UTC only because the MediaMTX container sets no TZ (observed:
`docker exec camera-cloud-core-mediamtx-1 sh -c 'echo "TZ=[$TZ]"; date'` printed `TZ=[]` and a UTC time).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from .config import RECONCILE_GRACE_SECONDS
from .domain import Segment, is_lower_uuid, video_path
from .store import Store

_FILE_RE = re.compile(r"(?P<d>\d{4}-\d{2}-\d{2})_(?P<t>\d{2}-\d{2}-\d{2})-(?P<f>\d{6})\.(?:mp4|ts)$")


class HookError(ValueError):
    pass


@dataclass(frozen=True)
class HookEvent:
    tenant_id: uuid.UUID
    camera_id: uuid.UUID
    started_at: datetime
    duration_ms: int | None
    file_name: str


def started_at_from_file(file_path: str) -> datetime:
    m = _FILE_RE.search(file_path)
    if not m:
        raise HookError("segment file name has no start time")
    try:
        return datetime.strptime(f"{m['d']} {m['t']} {m['f']}", "%Y-%m-%d %H-%M-%S %f").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise HookError("segment start time is not a date") from e


def parse_hook_body(body: bytes) -> HookEvent:
    """Parse the form body sent by the wget in mediamtx.yml. Raises HookError on anything unexpected."""
    try:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as e:
        raise HookError("body is not a form") from e
    try:
        path = form["path"][0]
        file_path = form["file"][0]
    except (KeyError, IndexError) as e:
        raise HookError("path or file is missing") from e
    parts = path.split("/")
    if len(parts) != 2 or not all(is_lower_uuid(p) for p in parts):
        raise HookError("path is not <tenant uuid>/<camera uuid>")
    # The file must live under the same path, so one camera cannot write another camera's index rows.
    if f"/{path}/" not in file_path.replace("\\", "/"):
        raise HookError("file is not inside the path's recording folder")
    duration_ms: int | None = None
    if form.get("duration"):
        try:
            d = float(form["duration"][0])
            if d < 0 or d > 24 * 3600:
                raise HookError("duration out of range")
            duration_ms = int(round(d * 1000))
        except ValueError as e:
            raise HookError("duration is not a number") from e
    return HookEvent(uuid.UUID(parts[0]), uuid.UUID(parts[1]), started_at_from_file(file_path), duration_ms,
                     file_path.rsplit("/", 1)[-1])


async def record_hook(store: Store, ev: HookEvent) -> str:
    """Return 'inserted', 'duplicate' or 'unknown_camera'. The tenant in the path must be the camera's tenant."""
    cam = await store.internal_camera(ev.camera_id)
    if cam is None or cam.tenant_id != ev.tenant_id:
        return "unknown_camera"
    ok = await store.insert_segment(Segment(ev.tenant_id, ev.camera_id, ev.started_at, ev.duration_ms,
                                            ev.file_name, "hook"))
    return "inserted" if ok else "duplicate"


@dataclass(frozen=True)
class ReconcilePlan:
    to_insert: list[datetime]
    to_mark_deleted: list[datetime]


def plan_reconcile(listed: list[datetime], known: set[datetime], now: datetime,
                   grace: int = RECONCILE_GRACE_SECONDS) -> ReconcilePlan:
    """Compare MediaMTX's list of segment start times with the index.

    A start that MediaMTX has and the index lacks is a gap (the hook was lost) and is inserted. A start the index has
    and MediaMTX no longer has was removed by the automatic deletion and is marked deleted. Segments younger than
    `grace` seconds are left alone because the open segment is listed before its hook fires.
    """
    limit = now - timedelta(seconds=grace)
    listed_set = set(listed)
    to_insert = sorted(s for s in listed_set if s not in known and s <= limit)
    to_mark = sorted(k for k in known if k not in listed_set and k <= limit)
    return ReconcilePlan(to_insert, to_mark)


def segment_file_name(started_at: datetime) -> str:
    u = started_at.astimezone(timezone.utc)
    return u.strftime("%Y-%m-%d_%H-%M-%S-") + f"{u.microsecond:06d}.mp4"


def announce_body(camera_id: uuid.UUID, starts: list[datetime]) -> dict:
    """What the reconciliation tells the writer about the segments it added: the camera and the start times. The writer looks
    each of them up in the index and announces only those it finds there."""
    return {"segments": [{"camera_id": str(camera_id), "started_at": s.astimezone(timezone.utc).isoformat()}
                         for s in starts]}


async def reconcile_camera(store: Store, mediamtx, tenant_id: uuid.UUID, camera_id: uuid.UUID, now: datetime) -> ReconcilePlan:
    """One camera: ask MediaMTX, apply the plan. `mediamtx.recording_starts(path)` returns a list of datetimes."""
    listed = await mediamtx.recording_starts(video_path(tenant_id, camera_id))
    known = await store.known_segment_starts(tenant_id, camera_id)
    plan = plan_reconcile(listed, known, now)
    for st in plan.to_insert:
        await store.insert_segment(Segment(tenant_id, camera_id, st, None, segment_file_name(st), "reconcile"))
    if plan.to_mark_deleted:
        await store.mark_segments_deleted(tenant_id, camera_id, plan.to_mark_deleted)
    return plan
