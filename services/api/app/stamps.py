"""Stamps: copies of the parts that grow with the number of cameras (video server, broker, state queue, writer and the
database that holds state and the recording index). Every tenant is served by one stamp; the control plane (this API,
Keycloak, the gateway, the tenant and camera ledger) exists once.

The `stamps` table in the control database says where each stamp is and which addresses and token audience belong to
it. The rows are written at every start from a JSON file (a list of objects with the table's column names) or, without
a file, one row for stamp 1 from the settings. This module also finds the data store of a stamp: stamp 1 uses the control
database itself; the others have their own database, opened when first needed so that a stamp that is switched off does
not stop the service from starting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import uuid
from dataclasses import asdict, dataclass, fields
from typing import Awaitable, Callable

from .config import Settings
from .domain import CameraStatus, Segment

log = logging.getLogger("ccc.stamps")


class StampUnavailable(Exception):
    """The database of a stamp cannot be reached."""


@dataclass(frozen=True)
class StampInfo:
    id: int
    name: str
    database_dsn: str
    database_password_file: str | None
    mqtt_host: str
    mqtt_port: int
    mqtt_admin_password_file: str | None
    mediamtx_control_url: str
    public_hls_url: str
    public_playback_url: str
    public_rtsp_url: str
    public_mqtt_url: str
    jwt_audience: str

    @classmethod
    def from_settings(cls, s: Settings) -> "StampInfo":
        return cls(1, "stamp 1", "postgresql://ccc@postgres:5432/ccc", None, s.mqtt_host, s.mqtt_port, None,
                   s.mediamtx_control_url, s.public_hls_url, s.public_playback_url, s.public_rtsp_url,
                   s.public_mqtt_url, s.video_audience)


COLUMNS = [f.name for f in fields(StampInfo)]


def read_stamps_file(path: str | pathlib.Path) -> list[StampInfo]:
    items = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    out = []
    for it in items:
        unknown = set(it) - set(COLUMNS)
        if unknown:
            raise ValueError(f"unknown stamp setting(s): {sorted(unknown)}")
        out.append(StampInfo(**{"database_password_file": None, "mqtt_port": 1883, "mqtt_admin_password_file": None,
                                **it}))
    if len({i.id for i in out}) != len(out) or 1 not in {i.id for i in out}:
        raise ValueError("stamp ids must be unique and stamp 1 must be there")
    return out


def configured_stamps(s: Settings) -> list[StampInfo]:
    return read_stamps_file(s.stamps_file) if s.stamps_file else [StampInfo.from_settings(s)]


async def write_stamps(pool, infos: list[StampInfo]) -> None:
    cols = ", ".join(COLUMNS)
    marks = ", ".join(f"${i + 1}" for i in range(len(COLUMNS)))
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "id")
    async with pool.acquire() as con:
        for i in infos:
            d = asdict(i)
            await con.execute(f"INSERT INTO stamps ({cols}) VALUES ({marks}) ON CONFLICT (id) DO UPDATE SET {sets}",
                              *[d[c] for c in COLUMNS])


async def read_stamps(pool) -> dict[int, StampInfo]:
    rows = await pool.fetch(f"SELECT {', '.join(COLUMNS)} FROM stamps ORDER BY id")
    return {r["id"]: StampInfo(**dict(r)) for r in rows}


class StampRegistry:
    """The stamps by id, and the data store of each. `make_store(info)` opens a store for a stamp other than 1."""

    def __init__(self, infos: dict[int, StampInfo], control_store,
                 make_store: Callable[[StampInfo], Awaitable[object]]) -> None:
        self._infos = infos
        self._stores: dict[int, object] = {1: control_store}
        self._make = make_store
        self._lock = asyncio.Lock()   # two first calls must not open the same stamp twice

    def info(self, stamp_id: int) -> StampInfo:
        return self._infos[stamp_id]

    def ids(self) -> list[int]:
        return sorted(self._infos)

    async def store(self, stamp_id: int):
        if stamp_id not in self._stores:
            async with self._lock:
                if stamp_id not in self._stores:
                    try:
                        self._stores[stamp_id] = await self._make(self._infos[stamp_id])
                    except Exception as e:  # noqa: BLE001 - a stamp that is switched off is not an error of the caller
                        raise StampUnavailable(f"stamp {stamp_id}: {e}") from e
        return self._stores[stamp_id]


class RoutedStore:
    """The one place where a tenant is turned into a stamp. Tenant, membership and camera ledger calls go to the control
    database; calls for state and recording index go to the database of the tenant's stamp. A tenant never moves
    between stamps, so the answer is kept for the life of the process."""

    def __init__(self, control, registry: StampRegistry) -> None:
        self._c = control
        self._r = registry
        self._stamp_of: dict[uuid.UUID, int] = {}

    async def _stamp_store(self, tenant_id: uuid.UUID):
        sid = self._stamp_of.get(tenant_id)
        if sid is None:
            t = await self._c.get_tenant(tenant_id)
            if t is None:
                raise LookupError("unknown tenant")
            sid = self._stamp_of[tenant_id] = t.stamp_id
        return await self._r.store(sid)

    # ---- control database
    async def memberships(self, user_id):
        return await self._c.memberships(user_id)

    async def get_tenant(self, tenant_id):
        return await self._c.get_tenant(tenant_id)

    async def list_tenants(self, partner_id):
        return await self._c.list_tenants(partner_id)

    async def tenants_by_ids(self, ids):
        return await self._c.tenants_by_ids(ids)

    async def add_membership(self, m):
        return await self._c.add_membership(m)

    async def tenant_counts_by_stamp(self):
        return await self._c.tenant_counts_by_stamp()

    async def list_cameras(self, tenant_id):
        return await self._c.list_cameras(tenant_id)

    async def get_camera(self, tenant_id, camera_id):
        return await self._c.get_camera(tenant_id, camera_id)

    async def seed_partner(self, partner_id, name):
        return await self._c.seed_partner(partner_id, name)

    async def seed_tenant(self, tenant_id, partner_id, name, stamp_id=1):
        return await self._c.seed_tenant(tenant_id, partner_id, name, stamp_id)

    async def internal_camera(self, camera_id):
        return await self._c.internal_camera(camera_id)

    async def internal_all_cameras(self):
        return await self._c.internal_all_cameras()

    async def create_tenant(self, partner_id, name, tenant_id=None, stamp_id=1):
        return await self._c.create_tenant(partner_id, name, tenant_id, stamp_id)

    async def use_enroll_token(self, jti, expires_at):
        return await self._c.use_enroll_token(jti, expires_at)

    # The camera ledger is the control plane's. A stamp other than the first also keeps a copy (which cameras exist and
    # whose they are), because its writer checks a camera in the same statement that writes it.
    async def _mirror(self, tenant_id, camera_id, deleted):
        t = await self._c.get_tenant(tenant_id)
        if t is not None and t.stamp_id != 1:
            await (await self._r.store(t.stamp_id)).mirror_camera(camera_id, tenant_id, t.stamp_id, deleted)

    async def create_camera(self, tenant_id, name, secret_sha256, camera_id=None):
        cam = await self._c.create_camera(tenant_id, name, secret_sha256, camera_id)
        try:
            await self._mirror(tenant_id, cam.id, False)
        except Exception as e:  # noqa: BLE001 - without the copy the stamp would drop the camera's data: undo
            await self._c.delete_camera(tenant_id, cam.id)
            raise StampUnavailable(str(e)) from e
        return cam

    async def delete_camera(self, tenant_id, camera_id):
        done = await self._c.delete_camera(tenant_id, camera_id)
        try:
            await self._mirror(tenant_id, camera_id, True)
        except Exception as e:  # noqa: BLE001 - the copy is made right again at the next start
            log.warning("could not mark camera %s deleted in its stamp (%s)", camera_id, e)
        return done

    async def seed_camera(self, camera_id, tenant_id, name, secret_sha256):
        await self._c.seed_camera(camera_id, tenant_id, name, secret_sha256)
        try:
            await self._mirror(tenant_id, camera_id, False)
        except Exception as e:  # noqa: BLE001 - the stamp may be switched off; sync_stamp does it when it is up
            log.warning("could not copy camera %s into its stamp yet (%s)", camera_id, e)

    async def sync_stamp(self, stamp_id: int) -> int:
        """Open the stamp and copy every camera of the ledger that belongs to it. Raises StampUnavailable."""
        store = await self._r.store(stamp_id)
        cams = await self._c.cameras_of_stamp(stamp_id)
        for cid, tid, deleted in cams:
            await store.mirror_camera(cid, tid, stamp_id, deleted)
        return len(cams)

    # ---- the tenant's stamp
    async def statuses(self, tenant_id):
        return await (await self._stamp_store(tenant_id)).statuses(tenant_id)

    async def insert_segment(self, seg: Segment):
        return await (await self._stamp_store(seg.tenant_id)).insert_segment(seg)

    async def list_segments(self, tenant_id, camera_id, since=None, until=None):
        return await (await self._stamp_store(tenant_id)).list_segments(tenant_id, camera_id, since, until)

    async def known_segment_starts(self, tenant_id, camera_id):
        return await (await self._stamp_store(tenant_id)).known_segment_starts(tenant_id, camera_id)

    async def mark_segments_deleted(self, tenant_id, camera_id, starts):
        return await (await self._stamp_store(tenant_id)).mark_segments_deleted(tenant_id, camera_id, starts)

    async def list_events(self, tenant_id, camera_id, since, until, limit, offset=0):
        return await (await self._stamp_store(tenant_id)).list_events(tenant_id, camera_id, since, until, limit, offset)

    async def get_event(self, tenant_id, camera_id, event_id):
        return await (await self._stamp_store(tenant_id)).get_event(tenant_id, camera_id, event_id)

    async def internal_apply_status(self, status: CameraStatus):
        cam = await self._c.internal_camera(status.camera_id)
        if cam is None:
            return False
        return await (await self._stamp_store(cam.tenant_id)).internal_apply_status(status)
