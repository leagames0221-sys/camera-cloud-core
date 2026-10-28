"""Who may do what (the table in docs/requirements.md) and the rule that turns facts into a camera state."""
from __future__ import annotations

import uuid
from enum import Enum

from .domain import PARTNER_ADMIN, TENANT_ADMIN, VIEWER, CameraStatus, Membership, Tenant


class Op(str, Enum):
    TENANT_CREATE = "tenant_create"
    TENANT_LIST = "tenant_list"
    CAMERA_REGISTER = "camera_register"
    CAMERA_DELETE = "camera_delete"
    CAMERA_LIST = "camera_list"
    VIDEO_WATCH = "video_watch"
    MEMBER_ADD = "member_add"


# Operation x role. A partner admin never watches video.
ALLOWED: dict[Op, frozenset[str]] = {
    Op.TENANT_CREATE: frozenset({PARTNER_ADMIN}),
    Op.TENANT_LIST: frozenset({PARTNER_ADMIN}),
    Op.CAMERA_REGISTER: frozenset({TENANT_ADMIN}),
    Op.CAMERA_DELETE: frozenset({TENANT_ADMIN}),
    Op.CAMERA_LIST: frozenset({PARTNER_ADMIN, TENANT_ADMIN, VIEWER}),
    Op.VIDEO_WATCH: frozenset({TENANT_ADMIN, VIEWER}),
    Op.MEMBER_ADD: frozenset({PARTNER_ADMIN, TENANT_ADMIN}),
}

# Which roles each role may hand out (partner admin up to tenant admin, tenant admin up to viewer).
MAY_GRANT: dict[str, frozenset[str]] = {
    PARTNER_ADMIN: frozenset({TENANT_ADMIN, VIEWER}),
    TENANT_ADMIN: frozenset({VIEWER}),
}


class NotFound(Exception):
    """The thing is not visible to this user. Also used when it does not exist, so the two cannot be told apart."""


class Forbidden(Exception):
    """The user can see the thing but the role does not allow the operation."""


def roles_in_tenant(memberships: list[Membership], tenant: Tenant) -> set[str]:
    roles: set[str] = set()
    for m in memberships:
        if m.role == PARTNER_ADMIN:
            if m.partner_id == tenant.partner_id:
                roles.add(PARTNER_ADMIN)
        elif m.tenant_id == tenant.id:
            roles.add(m.role)
    return roles


def authorize_tenant(memberships: list[Membership], tenant: Tenant | None, op: Op) -> str:
    """Return the role that allows `op` on `tenant`. Raise NotFound if the tenant is not the user's, else Forbidden."""
    if tenant is None:
        raise NotFound()
    roles = roles_in_tenant(memberships, tenant)
    if not roles:
        raise NotFound()
    for r in sorted(roles):
        if r in ALLOWED[op]:
            return r
    raise Forbidden()


def partner_ids(memberships: list[Membership]) -> list[uuid.UUID]:
    return [m.partner_id for m in memberships if m.role == PARTNER_ADMIN and m.partner_id is not None]


def authorize_partner(memberships: list[Membership], op: Op, partner_id: uuid.UUID | None) -> uuid.UUID:
    """For operations that belong to a partner (tenant creation and listing)."""
    mine = partner_ids(memberships)
    if not mine:
        raise Forbidden()
    if partner_id is None:
        if len(mine) != 1:
            raise Forbidden()
        partner_id = mine[0]
    if partner_id not in mine:
        raise NotFound()
    if PARTNER_ADMIN not in ALLOWED[op]:
        raise Forbidden()
    return partner_id


def may_grant(role_held: str, role_to_grant: str) -> bool:
    return role_to_grant in MAY_GRANT.get(role_held, frozenset())


def derive_state(status: CameraStatus | None, path_ready: bool) -> str:
    """online + path ready = recording; online + no path = standby; offline or unknown = disconnected."""
    if status is None or status.state != "online":
        return "disconnected"
    return "recording" if path_ready else "standby"
