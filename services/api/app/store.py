"""The data layer. Every method that touches tenant data takes `tenant_id` as a required argument, and
there is no method that lists or reads cameras, segments or statuses across tenants except the clearly
named `internal_*` ones used by the MQTT intake and the hook receiver (which look the tenant up first).

Two implementations: PgStore (pgstore.py, the real one) and MemoryStore (below, for unit tests). Both
follow the same Store protocol.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from .domain import Camera, CameraStatus, Event, Membership, Segment, Tenant


class Store(Protocol):
    # ---- people and tenants
    async def memberships(self, user_id: uuid.UUID) -> list[Membership]: ...
    async def get_tenant(self, tenant_id: uuid.UUID) -> Tenant | None: ...
    async def list_tenants(self, partner_id: uuid.UUID) -> list[Tenant]: ...
    async def create_tenant(self, partner_id: uuid.UUID, name: str, tenant_id: uuid.UUID | None = None,
                            stamp_id: int = 1) -> Tenant: ...
    async def tenants_by_ids(self, ids: list[uuid.UUID]) -> list[Tenant]: ...
    async def tenant_counts_by_stamp(self) -> dict[int, int]: ...
    async def add_membership(self, m: Membership) -> bool: ...

    # ---- cameras (tenant scoped)
    async def list_cameras(self, tenant_id: uuid.UUID) -> list[Camera]: ...
    async def get_camera(self, tenant_id: uuid.UUID, camera_id: uuid.UUID) -> Camera | None: ...
    async def create_camera(self, tenant_id: uuid.UUID, name: str, secret_sha256: str,
                            camera_id: uuid.UUID | None = None) -> Camera: ...
    async def delete_camera(self, tenant_id: uuid.UUID, camera_id: uuid.UUID) -> bool: ...
    async def statuses(self, tenant_id: uuid.UUID) -> dict[uuid.UUID, CameraStatus]: ...

    # ---- recording index (tenant scoped)
    async def insert_segment(self, seg: Segment) -> bool: ...
    async def list_segments(self, tenant_id: uuid.UUID, camera_id: uuid.UUID,
                            since: datetime | None = None, until: datetime | None = None) -> list[Segment]: ...
    async def known_segment_starts(self, tenant_id: uuid.UUID, camera_id: uuid.UUID) -> set[datetime]: ...
    async def mark_segments_deleted(self, tenant_id: uuid.UUID, camera_id: uuid.UUID,
                                    starts: list[datetime]) -> int: ...

    # ---- events (tenant scoped; written by the stamp's writer, read here)
    async def list_events(self, tenant_id: uuid.UUID, camera_id: uuid.UUID, since: datetime | None, until: datetime,
                          limit: int, offset: int = 0) -> list[Event]: ...
    async def get_event(self, tenant_id: uuid.UUID, camera_id: uuid.UUID, event_id: uuid.UUID) -> Event | None: ...

    # ---- start-up seeding (idempotent)
    async def seed_partner(self, partner_id: uuid.UUID, name: str) -> None: ...
    async def seed_tenant(self, tenant_id: uuid.UUID, partner_id: uuid.UUID, name: str, stamp_id: int = 1) -> None: ...
    async def seed_camera(self, camera_id: uuid.UUID, tenant_id: uuid.UUID, name: str, secret_sha256: str) -> None: ...

    # ---- internal entry points (no tenant known yet; they resolve it from the camera id)
    async def internal_camera(self, camera_id: uuid.UUID) -> Camera | None: ...
    async def internal_all_cameras(self) -> list[Camera]: ...
    async def internal_apply_status(self, status: CameraStatus) -> bool: ...

    # ---- the camera ledger copy in a stamp's own database
    async def mirror_camera(self, camera_id: uuid.UUID, tenant_id: uuid.UUID, stamp_id: int, deleted: bool) -> None: ...
    async def cameras_of_stamp(self, stamp_id: int) -> list[tuple[uuid.UUID, uuid.UUID, bool]]: ...

    # ---- the camera registration token, usable once
    async def use_enroll_token(self, jti: str, expires_at: datetime) -> bool: ...


class MemoryStore:
    """Dictionary-backed Store. Behaves like PgStore for everything the unit tests need."""

    def __init__(self) -> None:
        self._partners: dict[uuid.UUID, str] = {}
        self._tenants: dict[uuid.UUID, Tenant] = {}
        self._members: list[Membership] = []
        self._cameras: dict[uuid.UUID, Camera] = {}
        self._status: dict[uuid.UUID, CameraStatus] = {}
        self._segments: dict[tuple[uuid.UUID, datetime], Segment] = {}
        self._events: dict[uuid.UUID, Event] = {}
        self.mirrored: dict[uuid.UUID, tuple[uuid.UUID, int, bool]] = {}   # what mirror_camera was told
        self._enroll_jti: set[str] = set()

    async def memberships(self, user_id):
        return [m for m in self._members if m.user_id == user_id]

    async def get_tenant(self, tenant_id):
        return self._tenants.get(tenant_id)

    async def list_tenants(self, partner_id):
        return sorted((t for t in self._tenants.values() if t.partner_id == partner_id), key=lambda t: t.name)

    async def create_tenant(self, partner_id, name, tenant_id=None, stamp_id=1):
        t = Tenant(tenant_id or uuid.uuid4(), partner_id, name, stamp_id)
        self._tenants[t.id] = t
        return t

    async def tenants_by_ids(self, ids):
        return [self._tenants[i] for i in ids if i in self._tenants]

    async def tenant_counts_by_stamp(self):
        out: dict[int, int] = {}
        for t in self._tenants.values():
            out[t.stamp_id] = out.get(t.stamp_id, 0) + 1
        return out

    async def add_membership(self, m):
        if m in self._members:
            return False
        self._members.append(m)
        return True

    async def list_cameras(self, tenant_id):
        return sorted((c for c in self._cameras.values() if c.tenant_id == tenant_id), key=lambda c: c.name)

    async def get_camera(self, tenant_id, camera_id):
        c = self._cameras.get(camera_id)
        return c if c is not None and c.tenant_id == tenant_id else None

    async def create_camera(self, tenant_id, name, secret_sha256, camera_id=None):
        c = Camera(camera_id or uuid.uuid4(), tenant_id, name, secret_sha256)
        self._cameras[c.id] = c
        return c

    async def delete_camera(self, tenant_id, camera_id):
        c = self._cameras.get(camera_id)
        if c is None or c.tenant_id != tenant_id:
            return False
        del self._cameras[camera_id]
        self._status.pop(camera_id, None)
        return True

    async def statuses(self, tenant_id):
        ids = {c.id for c in self._cameras.values() if c.tenant_id == tenant_id}
        return {k: v for k, v in self._status.items() if k in ids}

    async def insert_segment(self, seg):
        key = (seg.camera_id, seg.started_at)
        if key in self._segments:
            return False
        self._segments[key] = seg
        return True

    async def list_segments(self, tenant_id, camera_id, since=None, until=None):
        out = [s for s in self._segments.values()
               if s.tenant_id == tenant_id and s.camera_id == camera_id and not s.deleted
               and (since is None or s.started_at >= since) and (until is None or s.started_at < until)]
        return sorted(out, key=lambda s: s.started_at)

    async def known_segment_starts(self, tenant_id, camera_id):
        return {s.started_at for s in self._segments.values()
                if s.tenant_id == tenant_id and s.camera_id == camera_id and not s.deleted}

    async def mark_segments_deleted(self, tenant_id, camera_id, starts):
        n = 0
        for st in starts:
            s = self._segments.get((camera_id, st))
            if s is not None and s.tenant_id == tenant_id and not s.deleted:
                self._segments[(camera_id, st)] = Segment(**{**s.__dict__, "deleted": True})
                n += 1
        return n

    def add_event(self, ev: Event) -> None:
        """For the tests: the events are written by the stamp's writer, not through this interface."""
        self._events[ev.id] = ev

    async def list_events(self, tenant_id, camera_id, since, until, limit, offset=0):
        out = [e for e in self._events.values() if e.tenant_id == tenant_id and e.camera_id == camera_id
               and (since is None or since <= e.started_at) and e.started_at < until]
        return sorted(out, key=lambda e: (-e.started_at.timestamp(), e.kind, e.label))[offset:offset + limit]

    async def get_event(self, tenant_id, camera_id, event_id):
        e = self._events.get(event_id)
        return e if e is not None and e.tenant_id == tenant_id and e.camera_id == camera_id else None

    async def seed_partner(self, partner_id, name):
        self._partners[partner_id] = name

    async def seed_tenant(self, tenant_id, partner_id, name, stamp_id=1):
        self._tenants[tenant_id] = Tenant(tenant_id, partner_id, name, stamp_id)

    async def seed_camera(self, camera_id, tenant_id, name, secret_sha256):
        self._cameras[camera_id] = Camera(camera_id, tenant_id, name, secret_sha256)

    async def internal_camera(self, camera_id):
        return self._cameras.get(camera_id)

    async def internal_all_cameras(self):
        return list(self._cameras.values())

    async def internal_apply_status(self, status):
        if status.camera_id not in self._cameras:
            return False
        old = self._status.get(status.camera_id)
        if old is not None and old.state == status.state:
            status = CameraStatus(status.camera_id, status.state, status.reason, status.fps, status.bitrate_kbps,
                                  old.changed_at, status.seen_at)
        self._status[status.camera_id] = status
        return True

    async def mirror_camera(self, camera_id, tenant_id, stamp_id, deleted):
        self.mirrored[camera_id] = (tenant_id, stamp_id, deleted)

    async def cameras_of_stamp(self, stamp_id):
        out = []
        for c in self._cameras.values():
            t = self._tenants.get(c.tenant_id)
            if t is not None and t.stamp_id == stamp_id:
                out.append((c.id, c.tenant_id, False))
        return out

    async def use_enroll_token(self, jti, expires_at):
        if jti in self._enroll_jti:
            return False
        self._enroll_jti.add(jti)
        return True
