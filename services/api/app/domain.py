"""Plain data types and the one place where a video path is built."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

PARTNER_ADMIN = "partner_admin"
TENANT_ADMIN = "tenant_admin"
VIEWER = "viewer"
ROLES = (PARTNER_ADMIN, TENANT_ADMIN, VIEWER)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_lower_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s))


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def video_path(tenant_id: uuid.UUID, camera_id: uuid.UUID) -> str:
    """MediaMTX path name: <tenant uuid>/<camera uuid>, lower case."""
    return f"{str(tenant_id).lower()}/{str(camera_id).lower()}"


@dataclass(frozen=True)
class Tenant:
    id: uuid.UUID
    partner_id: uuid.UUID
    name: str
    stamp_id: int = 1  # which stamp (set of video, state and index parts) serves this tenant


@dataclass(frozen=True)
class Membership:
    user_id: uuid.UUID
    role: str
    partner_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Camera:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    secret_sha256: str


@dataclass(frozen=True)
class CameraStatus:
    camera_id: uuid.UUID
    state: str  # "online" | "offline"
    reason: str | None
    fps: float | None
    bitrate_kbps: float | None
    changed_at: datetime
    seen_at: datetime


@dataclass(frozen=True)
class Segment:
    tenant_id: uuid.UUID
    camera_id: uuid.UUID
    started_at: datetime
    duration_ms: int | None
    file_name: str
    source: str  # "hook" | "reconcile"
    deleted: bool = False


@dataclass(frozen=True)
class Event:
    """Something seen in the recordings of a camera: movement (kind "motion", label "") or a named thing (kind "object")."""
    id: uuid.UUID
    tenant_id: uuid.UUID
    camera_id: uuid.UUID
    kind: str
    label: str
    started_at: datetime
    duration_ms: int
    score: float | None
    model: str
