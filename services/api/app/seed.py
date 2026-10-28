"""Demo data: 1 partner, 3 tenants (A, B, C; fictional), 4 cameras, 7 memberships. Safe to run at every start."""
from __future__ import annotations

import logging
import uuid

from . import seed_data as sd
from .camera_secret import hash_secret
from .config import Settings
from .domain import Membership
from .store import Store

log = logging.getLogger("ccc.seed")


def camera_secret_name(camera_id: uuid.UUID) -> str:
    return f"cameras/{camera_id}.secret"


async def seed(store: Store, settings: Settings) -> dict[uuid.UUID, str]:
    """Write the demo rows and return {camera id: secret} for the cameras whose secret file exists."""
    await store.seed_partner(sd.PARTNER_ID, sd.PARTNER_NAME)
    for tid, name in sd.TENANTS:
        await store.seed_tenant(tid, sd.PARTNER_ID, name)
    for tid, name, stamp_id in sd.EXTRA_TENANTS:
        await store.seed_tenant(tid, sd.PARTNER_ID, name, stamp_id)
    for user_id, role, partner_id, tenant_id in sd.MEMBERSHIPS + sd.EXTRA_MEMBERSHIPS:
        await store.add_membership(Membership(user_id, role, partner_id, tenant_id))
    secrets_by_camera: dict[uuid.UUID, str] = {}
    for cid, tid, name in sd.CAMERAS + sd.EXTRA_CAMERAS:
        try:
            secret = settings.secret(camera_secret_name(cid))
        except OSError:
            log.warning("no secret file for demo camera %s; it is not seeded", cid)
            continue
        await store.seed_camera(cid, tid, name, hash_secret(secret))
        secrets_by_camera[cid] = secret
    return secrets_by_camera
