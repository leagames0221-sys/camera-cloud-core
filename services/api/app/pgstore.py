"""PostgreSQL implementation of Store (asyncpg) and the numbered-SQL migration runner."""
from __future__ import annotations

import pathlib
import uuid
from datetime import datetime

import asyncpg

from .domain import Camera, CameraStatus, Event, Membership, Segment, Tenant

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
STAMP_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "stamp_migrations"   # a stamp's own database
_LOCK_KEY = 726_005_001  # arbitrary constant: one migrator at a time


def list_migrations(directory: pathlib.Path = MIGRATIONS_DIR) -> list[tuple[str, str]]:
    """(version, sql) sorted by the number in the file name, e.g. 0001_init.sql -> '0001_init'."""
    files = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    return [(f.stem, f.read_text(encoding="utf-8")) for f in files]


async def migrate(pool: asyncpg.Pool, directory: pathlib.Path = MIGRATIONS_DIR) -> list[str]:
    """Apply the migrations that are not in schema_migrations yet. Safe to call at every start and concurrently."""
    applied_now: list[str] = []
    async with pool.acquire() as con:
        await con.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        try:
            await con.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r["version"] for r in await con.fetch("SELECT version FROM schema_migrations")}
            for version, sql in list_migrations(directory):
                if version in done:
                    continue
                async with con.transaction():
                    await con.execute(sql)
                    await con.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
                applied_now.append(version)
        finally:
            await con.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
    return applied_now


def _tenant(r) -> Tenant:
    return Tenant(r["id"], r["partner_id"], r["name"], r["stamp_id"])


def _camera(r) -> Camera:
    return Camera(r["id"], r["tenant_id"], r["name"], r["secret_sha256"])


def _segment(r) -> Segment:
    return Segment(r["tenant_id"], r["camera_id"], r["started_at"], r["duration_ms"], r["file_name"], r["source"],
                   r["deleted"])


_EVENT_COLUMNS = "id, tenant_id, camera_id, kind, label, started_at, duration_ms, score, model"


def _event(r) -> Event:
    return Event(r["id"], r["tenant_id"], r["camera_id"], r["kind"], r["label"], r["started_at"], r["duration_ms"],
                 r["score"], r["model"])


class PgStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # ---- people and tenants
    async def memberships(self, user_id):
        rows = await self.pool.fetch(
            "SELECT user_id, role, partner_id, tenant_id FROM memberships WHERE user_id = $1", user_id)
        return [Membership(r["user_id"], r["role"], r["partner_id"], r["tenant_id"]) for r in rows]

    async def get_tenant(self, tenant_id):
        r = await self.pool.fetchrow("SELECT id, partner_id, name, stamp_id FROM tenants WHERE id = $1", tenant_id)
        return _tenant(r) if r else None

    async def list_tenants(self, partner_id):
        rows = await self.pool.fetch(
            "SELECT id, partner_id, name, stamp_id FROM tenants WHERE partner_id = $1 ORDER BY name", partner_id)
        return [_tenant(r) for r in rows]

    async def create_tenant(self, partner_id, name, tenant_id=None, stamp_id=1):
        tid = tenant_id or uuid.uuid4()
        r = await self.pool.fetchrow(
            "INSERT INTO tenants (id, partner_id, name, stamp_id) VALUES ($1, $2, $3, $4)"
            " RETURNING id, partner_id, name, stamp_id", tid, partner_id, name, stamp_id)
        return _tenant(r)

    async def tenants_by_ids(self, ids):
        if not ids:
            return []
        rows = await self.pool.fetch(
            "SELECT id, partner_id, name, stamp_id FROM tenants WHERE id = ANY($1::uuid[]) ORDER BY name", list(ids))
        return [_tenant(r) for r in rows]

    async def tenant_counts_by_stamp(self):
        rows = await self.pool.fetch("SELECT stamp_id, count(*) AS n FROM tenants GROUP BY stamp_id")
        return {r["stamp_id"]: r["n"] for r in rows}

    async def add_membership(self, m):
        tag = await self.pool.execute(
            "INSERT INTO memberships (user_id, role, partner_id, tenant_id) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT DO NOTHING", m.user_id, m.role, m.partner_id, m.tenant_id)
        return tag.endswith(" 1")

    # ---- cameras (tenant scoped)
    async def list_cameras(self, tenant_id):
        rows = await self.pool.fetch(
            "SELECT id, tenant_id, name, secret_sha256 FROM cameras"
            " WHERE tenant_id = $1 AND deleted_at IS NULL ORDER BY name, id", tenant_id)
        return [_camera(r) for r in rows]

    async def get_camera(self, tenant_id, camera_id):
        r = await self.pool.fetchrow(
            "SELECT id, tenant_id, name, secret_sha256 FROM cameras"
            " WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL", tenant_id, camera_id)
        return _camera(r) if r else None

    async def create_camera(self, tenant_id, name, secret_sha256, camera_id=None):
        cid = camera_id or uuid.uuid4()
        r = await self.pool.fetchrow(
            "INSERT INTO cameras (id, tenant_id, name, secret_sha256, stamp_id)"
            " SELECT $1::uuid, $2::uuid, $3::text, $4::text, stamp_id FROM tenants WHERE id = $2::uuid"
            " RETURNING id, tenant_id, name, secret_sha256", cid, tenant_id, name, secret_sha256)
        return _camera(r)

    async def delete_camera(self, tenant_id, camera_id):
        tag = await self.pool.execute(
            "UPDATE cameras SET deleted_at = now() WHERE tenant_id = $1 AND id = $2 AND deleted_at IS NULL",
            tenant_id, camera_id)
        return tag.endswith(" 1")

    async def statuses(self, tenant_id):
        rows = await self.pool.fetch(
            "SELECT s.camera_id, s.state, s.reason, s.fps, s.bitrate_kbps, s.changed_at, s.seen_at"
            " FROM camera_status s JOIN cameras c ON c.id = s.camera_id AND c.tenant_id = s.tenant_id"
            " WHERE s.tenant_id = $1 AND c.deleted_at IS NULL", tenant_id)
        return {r["camera_id"]: CameraStatus(r["camera_id"], r["state"], r["reason"], r["fps"], r["bitrate_kbps"],
                                             r["changed_at"], r["seen_at"]) for r in rows}

    # ---- recording index (tenant scoped)
    async def insert_segment(self, seg):
        tag = await self.pool.execute(
            "INSERT INTO recording_segments (camera_id, tenant_id, started_at, duration_ms, file_name, source)"
            " VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (camera_id, started_at) DO NOTHING",
            seg.camera_id, seg.tenant_id, seg.started_at, seg.duration_ms, seg.file_name, seg.source)
        return tag.endswith(" 1")

    async def list_segments(self, tenant_id, camera_id, since=None, until=None):
        rows = await self.pool.fetch(
            "SELECT camera_id, tenant_id, started_at, duration_ms, file_name, source, deleted"
            " FROM recording_segments WHERE tenant_id = $1 AND camera_id = $2 AND NOT deleted"
            " AND ($3::timestamptz IS NULL OR started_at >= $3) AND ($4::timestamptz IS NULL OR started_at < $4)"
            " ORDER BY started_at", tenant_id, camera_id, since, until)
        return [_segment(r) for r in rows]

    async def known_segment_starts(self, tenant_id, camera_id):
        rows = await self.pool.fetch(
            "SELECT started_at FROM recording_segments WHERE tenant_id = $1 AND camera_id = $2 AND NOT deleted",
            tenant_id, camera_id)
        return {r["started_at"] for r in rows}

    async def mark_segments_deleted(self, tenant_id, camera_id, starts):
        if not starts:
            return 0
        tag = await self.pool.execute(
            "UPDATE recording_segments SET deleted = true"
            " WHERE tenant_id = $1 AND camera_id = $2 AND started_at = ANY($3::timestamptz[]) AND NOT deleted",
            tenant_id, camera_id, list(starts))
        return int(tag.split()[-1])

    # ---- events (written by the stamp's writer)
    async def list_events(self, tenant_id, camera_id, since, until, limit, offset=0):
        rows = await self.pool.fetch(
            "SELECT " + _EVENT_COLUMNS + " FROM events WHERE tenant_id = $1 AND camera_id = $2"
            " AND ($3::timestamptz IS NULL OR started_at >= $3) AND started_at < $4"
            " ORDER BY started_at DESC, kind, label LIMIT $5 OFFSET $6",
            tenant_id, camera_id, since, until, limit, offset)
        return [_event(r) for r in rows]

    async def get_event(self, tenant_id, camera_id, event_id):
        r = await self.pool.fetchrow(
            "SELECT " + _EVENT_COLUMNS + " FROM events WHERE tenant_id = $1 AND camera_id = $2 AND id = $3",
            tenant_id, camera_id, event_id)
        return _event(r) if r else None

    # ---- start-up seeding
    async def seed_partner(self, partner_id, name):
        await self.pool.execute(
            "INSERT INTO partners (id, name) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
            partner_id, name)

    async def seed_tenant(self, tenant_id, partner_id, name, stamp_id=1):
        await self.pool.execute(
            "INSERT INTO tenants (id, partner_id, name, stamp_id) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name", tenant_id, partner_id, name, stamp_id)

    async def seed_camera(self, camera_id, tenant_id, name, secret_sha256):
        await self.pool.execute(
            "INSERT INTO cameras (id, tenant_id, name, secret_sha256, stamp_id)"
            " SELECT $1::uuid, $2::uuid, $3::text, $4::text, stamp_id FROM tenants WHERE id = $2::uuid"
            " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, secret_sha256 = EXCLUDED.secret_sha256,"
            " deleted_at = NULL", camera_id, tenant_id, name, secret_sha256)

    # ---- internal entry points
    async def internal_camera(self, camera_id):
        r = await self.pool.fetchrow(
            "SELECT id, tenant_id, name, secret_sha256 FROM cameras WHERE id = $1 AND deleted_at IS NULL", camera_id)
        return _camera(r) if r else None

    async def internal_all_cameras(self):
        rows = await self.pool.fetch(
            "SELECT id, tenant_id, name, secret_sha256 FROM cameras WHERE deleted_at IS NULL ORDER BY id")
        return [_camera(r) for r in rows]

    async def internal_apply_status(self, status: CameraStatus) -> bool:
        cam = await self.internal_camera(status.camera_id)
        if cam is None:
            return False
        await self.pool.execute(
            "INSERT INTO camera_status (camera_id, tenant_id, state, reason, fps, bitrate_kbps, changed_at, seen_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $7)"
            " ON CONFLICT (camera_id) DO UPDATE SET"
            "  changed_at = CASE WHEN camera_status.state = EXCLUDED.state THEN camera_status.changed_at"
            "                    ELSE EXCLUDED.changed_at END,"
            "  state = EXCLUDED.state, reason = EXCLUDED.reason, fps = EXCLUDED.fps,"
            "  bitrate_kbps = EXCLUDED.bitrate_kbps, seen_at = EXCLUDED.seen_at",
            status.camera_id, cam.tenant_id, status.state, status.reason, status.fps, status.bitrate_kbps,
            status.changed_at)
        return True

    # ---- the camera ledger copy in a stamp's own database (see stamp_migrations/0001_stamp_tables.sql)
    async def mirror_camera(self, camera_id, tenant_id, stamp_id, deleted):
        await self.pool.execute(
            "INSERT INTO cameras (id, tenant_id, stamp_id, deleted_at)"
            " VALUES ($1, $2, $3, CASE WHEN $4::boolean THEN now() ELSE NULL END)"
            " ON CONFLICT (id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at", camera_id, tenant_id, stamp_id, deleted)

    async def cameras_of_stamp(self, stamp_id):
        """(camera id, tenant id, deleted) of the control ledger, for copying into the stamp's database."""
        rows = await self.pool.fetch(
            "SELECT id, tenant_id, deleted_at IS NOT NULL AS deleted FROM cameras WHERE stamp_id = $1", stamp_id)
        return [(r["id"], r["tenant_id"], r["deleted"]) for r in rows]

    # ---- the camera registration token (device authorization grant), usable once
    async def use_enroll_token(self, jti, expires_at):
        """True the first time a token id is seen, False after (also for two calls at the same moment)."""
        await self.pool.execute("DELETE FROM enroll_tokens WHERE expires_at < now() - interval '1 hour'")
        tag = await self.pool.execute(
            "INSERT INTO enroll_tokens (jti, expires_at) VALUES ($1, $2) ON CONFLICT (jti) DO NOTHING", jti, expires_at)
        return tag.endswith(" 1")
