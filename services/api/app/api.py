"""HTTP routes. /api/v1/... is public (through the gateway); /internal/... and /jwks are for other containers only."""
from __future__ import annotations

import hmac
import logging
import math
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import rules
from .camera_secret import hash_secret, new_camera_secret, verify_secret
from .config import PLAYBACK_WINDOW_SECONDS, SEGMENT_SECONDS, Settings
from .domain import ROLES, TENANT_ADMIN, Camera, Membership, Tenant, utcnow, video_path
from .oidc import AuthError, DeviceTokenVerifier, TokenVerifier
from .rules import Forbidden, NotFound, Op
from .segments import HookError, parse_hook_body, record_hook
from .stamps import StampInfo, StampRegistry, StampUnavailable
from .store import Store
from .throttle import TenantThrottle
from .video_jwt import KeyStore

log = logging.getLogger("ccc.api")

API_PREFIX = "/api/v1"
EVENTS_PAGE = 100                        # events in one answer unless `limit` says otherwise: Frigate's `limit` default and the "default value is 100" of AWS Kinesis Video Streams' ListFragments (ADR 0004)
EVENTS_PAGE_MAX = 1000                   # the most one answer may hold: the maximum of that ListFragments' MaxResults
EVENT_LEAD_SECONDS = 5                   # playback of an event starts this long before it ...
EVENT_TAIL_SECONDS = 5                   # ... and ends this long after it: the length is the event's plus 10 seconds
MAX_SEGMENT_SECONDS = 120                # the longest segment the index accepts
HOOK_DOOR_STAMP = 1   # the stamp whose closed-segment secret the API's own internal door checks (init_secrets.py)


# ---------------------------------------------------------------- wiring

@dataclass
class Deps:
    settings: Settings
    store: Store
    verifier: TokenVerifier
    keys: KeyStore
    mediamtx: Any  # needs: async ready_paths() -> set[str]
    dynsec: Any  # needs: async add_camera(id, secret), async remove_camera(id)
    hook_secret: str
    now: Callable[[], datetime] = utcnow
    background: list = field(default_factory=list)
    stamps: StampRegistry | None = None      # None: one stamp made from the settings
    throttle: TenantThrottle | None = None   # None: no limit per tenant
    device_verifier: DeviceTokenVerifier | None = None   # None: cameras cannot register themselves
    dynsec_by_stamp: dict | None = None      # stamp id -> broker client; None: `dynsec` serves the only stamp
    announce: Callable[..., Awaitable[None]] | None = None   # tells the first stamp's writer of segments the reconciliation added

    async def ready_paths_for(self, tenant: Tenant) -> set[str]:
        """The video paths that are streaming on the tenant's stamp (only that stamp is asked)."""
        for_stamp = getattr(self.mediamtx, "for_stamp", None)
        return await (for_stamp(tenant.stamp_id) if for_stamp else self.mediamtx).ready_paths()

    def dynsec_for(self, tenant: Tenant):
        """The broker client of the tenant's stamp (a camera's broker user is made in its own stamp's broker)."""
        if self.dynsec_by_stamp is None:
            return self.dynsec
        return self.dynsec_by_stamp[tenant.stamp_id]

    def stamp_info(self, tenant: Tenant) -> StampInfo:
        """Where the tenant's video, broker and token audience are (the stamp the tenant belongs to)."""
        if self.stamps is None:
            return StampInfo.from_settings(self.settings)
        return self.stamps.info(tenant.stamp_id)


@dataclass
class Principal:
    user_id: uuid.UUID
    memberships: list[Membership]


def _deps(request: Request) -> Deps:
    return request.app.state.deps


async def current_principal(request: Request) -> Principal:
    deps = _deps(request)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = await deps.verifier.verify(token.strip())
    except AuthError:
        raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"}) from None
    return Principal(user_id, await deps.store.memberships(user_id))


def _uuid(s: str) -> uuid.UUID:
    """A path value that is not a UUID cannot name anything, so it is 'not found' like any other unknown id."""
    try:
        return uuid.UUID(s)
    except ValueError:
        raise NotFound() from None


async def _tenant(deps: Deps, p: Principal, tenant_id: str, op: Op) -> tuple[Tenant, str]:
    tenant = await deps.store.get_tenant(_uuid(tenant_id))
    role = rules.authorize_tenant(p.memberships, tenant, op)
    assert tenant is not None
    # Only a caller who belongs to the tenant and is allowed the operation uses up the tenant's calls.
    if deps.throttle is not None:
        wait = deps.throttle.take(tenant.id)
        if wait > 0:
            raise HTTPException(429, "too_many_requests", headers={"Retry-After": str(max(1, math.ceil(wait)))})
    return tenant, role


async def _camera(deps: Deps, tenant: Tenant, camera_id: str) -> Camera:
    cam = await deps.store.get_camera(tenant.id, _uuid(camera_id))
    if cam is None:
        raise NotFound()
    return cam


async def _stamp_for_new_tenant(deps: Deps, wanted: int | None) -> int:
    """The stamp the partner administrator asked for, or the one with the fewest tenants (the lower number if equal)."""
    known = deps.stamps.ids() if deps.stamps is not None else [1]
    if wanted is not None:
        if wanted not in known:
            raise HTTPException(422, "unknown_stamp")
        return wanted
    counts = await deps.store.tenant_counts_by_stamp()
    return min(known, key=lambda i: (counts.get(i, 0), i))


def _utc(dt: datetime) -> datetime:
    """A time without a zone in a query is taken as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _event_window(ev) -> tuple[datetime, datetime]:
    """The part of the recording shown for an event: it starts 5 seconds before the event and is the event's length plus
    10 seconds long (Frigate's default pre_capture and post_capture of 5 seconds each, ADR 0004)."""
    start = ev.started_at - timedelta(seconds=EVENT_LEAD_SECONDS)
    return start, start + timedelta(milliseconds=ev.duration_ms) + timedelta(seconds=EVENT_LEAD_SECONDS + EVENT_TAIL_SECONDS)


def _overlaps(seg, start: datetime, end: datetime) -> bool:
    length = seg.duration_ms / 1000 if seg.duration_ms else SEGMENT_SECONDS    # a segment of unknown length: the usual one
    return seg.started_at < end and seg.started_at + timedelta(seconds=length) > start


def _recorded(segs, start: datetime, end: datetime) -> bool:
    return any(_overlaps(s, start, end) for s in segs)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _epoch_iso(ts: int) -> str:
    return _iso(datetime.fromtimestamp(ts, tz=timezone.utc))


# ---------------------------------------------------------------- request / response models

class NewTenant(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    partner_id: uuid.UUID | None = None
    # Which stamp serves the tenant. Without it: the stamp with the fewest tenants (ADR 0006).
    # A whole number only (no `true`, no "2"); one that is not in the stamps table is refused with 422.
    stamp_id: int | None = Field(default=None, ge=1, strict=True)


class NewCamera(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class NewMember(BaseModel):
    user_id: uuid.UUID
    role: str


class CameraTokenRequest(BaseModel):
    camera_id: uuid.UUID
    secret: str = Field(min_length=1, max_length=200)


def _camera_view(c: Camera, status, ready: set[str]) -> dict:
    state = rules.derive_state(status, video_path(c.tenant_id, c.id) in ready)
    return {
        "id": str(c.id), "tenant_id": str(c.tenant_id), "name": c.name, "state": state,
        "fps": status.fps if status else None, "bitrate_kbps": status.bitrate_kbps if status else None,
        "reason": status.reason if status else None,
        "state_since": _iso(status.changed_at) if status else None,
        "last_seen": _iso(status.seen_at) if status else None,
    }


# ---------------------------------------------------------------- app

def create_app(settings: Settings, deps_factory: Callable[[], Awaitable[Deps]]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        deps = await deps_factory()
        app.state.deps = deps
        try:
            yield
        finally:
            for closer in reversed(deps.background):
                await closer()

    app = FastAPI(title="camera-cloud-core", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=f"{API_PREFIX}/openapi.json")

    @app.exception_handler(NotFound)
    async def _nf(_: Request, __: NotFound):
        return JSONResponse({"detail": "not_found"}, status_code=404)

    @app.exception_handler(StampUnavailable)
    async def _su(_: Request, __: StampUnavailable):
        return JSONResponse({"detail": "stamp_unavailable"}, status_code=502)

    @app.exception_handler(Forbidden)
    async def _fb(_: Request, __: Forbidden):
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    # ---- who am I
    @app.get(f"{API_PREFIX}/me")
    async def me(request: Request, p: Principal = Depends(current_principal)):
        deps = _deps(request)
        entries: list[dict] = []
        tenant_ids = [m.tenant_id for m in p.memberships if m.tenant_id is not None]
        for t in await deps.store.tenants_by_ids(tenant_ids):
            for m in p.memberships:
                if m.tenant_id == t.id:
                    entries.append({"role": m.role, "tenant_id": str(t.id), "tenant_name": t.name})
        for pid in rules.partner_ids(p.memberships):
            for t in await deps.store.list_tenants(pid):
                entries.append({"role": rules.PARTNER_ADMIN, "tenant_id": str(t.id), "tenant_name": t.name})
        return {"user_id": str(p.user_id), "entries": entries}

    # ---- tenants (partner admin)
    @app.get(f"{API_PREFIX}/tenants")
    async def list_tenants(request: Request, p: Principal = Depends(current_principal)):
        deps = _deps(request)
        pid = rules.authorize_partner(p.memberships, Op.TENANT_LIST, None)
        return [{"id": str(t.id), "name": t.name, "partner_id": str(t.partner_id)}
                for t in await deps.store.list_tenants(pid)]

    @app.post(f"{API_PREFIX}/tenants", status_code=201)
    async def create_tenant(body: NewTenant, request: Request, p: Principal = Depends(current_principal)):
        deps = _deps(request)
        pid = rules.authorize_partner(p.memberships, Op.TENANT_CREATE, body.partner_id)
        stamp_id = await _stamp_for_new_tenant(deps, body.stamp_id)
        t = await deps.store.create_tenant(pid, body.name.strip(), stamp_id=stamp_id)
        return {"id": str(t.id), "name": t.name, "partner_id": str(t.partner_id)}

    # ---- members
    @app.post(f"{API_PREFIX}/tenants/{{tenant_id}}/members", status_code=201)
    async def add_member(tenant_id: str, body: NewMember, request: Request, response: Response,
                         p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, role_held = await _tenant(deps, p, tenant_id, Op.MEMBER_ADD)
        if body.role not in ROLES or not rules.may_grant(role_held, body.role):
            raise Forbidden()
        created = await deps.store.add_membership(Membership(body.user_id, body.role, None, tenant.id))
        if not created:
            response.status_code = 200
        return {"user_id": str(body.user_id), "tenant_id": str(tenant.id), "role": body.role}

    # ---- cameras
    @app.get(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras")
    async def list_cameras(tenant_id: str, request: Request, p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.CAMERA_LIST)
        cams = await deps.store.list_cameras(tenant.id)
        statuses = await deps.store.statuses(tenant.id)
        ready = await deps.ready_paths_for(tenant)
        return [_camera_view(c, statuses.get(c.id), ready) for c in cams]

    async def new_camera(deps: Deps, tenant: Tenant, name: str) -> dict:
        """Register a camera in the tenant: the ledger row, the user in the tenant's stamp's broker, and the answer that
        holds the secret (shown once; only its hash is kept)."""
        secret = new_camera_secret()
        cam = await deps.store.create_camera(tenant.id, name.strip(), hash_secret(secret))
        try:
            await deps.dynsec_for(tenant).add_camera(cam.id, secret)
        except Exception:  # noqa: BLE001 - without an MQTT user the camera cannot report state; undo the row
            log.exception("could not create the MQTT user for camera %s", cam.id)
            await deps.store.delete_camera(tenant.id, cam.id)
            raise HTTPException(502, "mqtt_provisioning_failed") from None
        info = deps.stamp_info(tenant)
        return {
            "id": str(cam.id), "tenant_id": str(tenant.id), "name": cam.name,
            "credential_type": "secret", "secret": secret,
            "mediamtx_url": f"{info.public_rtsp_url}/{video_path(tenant.id, cam.id)}",
            "mqtt_url": info.public_mqtt_url,
            "mqtt_client_id": str(cam.id), "mqtt_topic": f"cameras/{cam.id}/state",
        }

    @app.post(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras", status_code=201)
    async def register_camera(tenant_id: str, body: NewCamera, request: Request,
                              p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.CAMERA_REGISTER)
        return await new_camera(deps, tenant, body.name)

    # ---- a camera registers itself with the access token of the device authorization grant (ADR 0009)
    @app.post(f"{API_PREFIX}/devices/enroll", status_code=201)
    async def enroll_device(body: NewCamera, request: Request):
        deps = _deps(request)
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if deps.device_verifier is None or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
        try:
            t = await deps.device_verifier.verify(token.strip())
        except AuthError:
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"}) from None
        # The token is used up before anything else is checked: a second use, also one at the same moment, is refused.
        if not await deps.store.use_enroll_token(t.jti, t.expires_at):
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
        admin_of = {m.tenant_id for m in await deps.store.memberships(t.user_id)
                    if m.role == TENANT_ADMIN and m.tenant_id is not None}
        if not admin_of:
            raise Forbidden()           # not a tenant administrator: a viewer or a partner administrator cannot do this
        if len(admin_of) > 1:
            raise HTTPException(409, "several_tenants")   # which tenant the camera is for is not known
        (tenant_id,) = admin_of
        tenant = await deps.store.get_tenant(tenant_id)
        assert tenant is not None
        if deps.throttle is not None:
            wait = deps.throttle.take(tenant.id)
            if wait > 0:
                raise HTTPException(429, "too_many_requests", headers={"Retry-After": str(max(1, math.ceil(wait)))})
        return await new_camera(deps, tenant, body.name)

    @app.get(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras/{{camera_id}}")
    async def get_camera(tenant_id: str, camera_id: str, request: Request, p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.CAMERA_LIST)
        cam = await _camera(deps, tenant, camera_id)
        statuses = await deps.store.statuses(tenant.id)
        return _camera_view(cam, statuses.get(cam.id), await deps.ready_paths_for(tenant))

    @app.delete(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras/{{camera_id}}", status_code=204)
    async def delete_camera(tenant_id: str, camera_id: str, request: Request,
                            p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.CAMERA_DELETE)
        cam = await _camera(deps, tenant, camera_id)
        await deps.store.delete_camera(tenant.id, cam.id)
        try:
            await deps.dynsec_for(tenant).remove_camera(cam.id)
        except Exception:  # noqa: BLE001
            log.exception("could not remove the MQTT user of camera %s", cam.id)
        return Response(status_code=204)

    # ---- recording index
    @app.get(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras/{{camera_id}}/segments")
    async def list_segments(tenant_id: str, camera_id: str, request: Request, minutes: int = 10,
                            p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.CAMERA_LIST)
        cam = await _camera(deps, tenant, camera_id)
        minutes = max(1, min(minutes, 24 * 60))
        since = deps.now() - timedelta(minutes=minutes)
        segs = await deps.store.list_segments(tenant.id, cam.id, since=since)
        return [{"started_at": _iso(s.started_at), "duration_ms": s.duration_ms, "source": s.source} for s in segs]

    # ---- video tokens
    @app.post(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras/{{camera_id}}/live")
    async def live(tenant_id: str, camera_id: str, request: Request, p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.VIDEO_WATCH)
        cam = await _camera(deps, tenant, camera_id)
        path = video_path(tenant.id, cam.id)
        info = deps.stamp_info(tenant)
        token, exp = deps.keys.issue(action="read", path=path, subject=str(p.user_id),
                                     issuer=deps.settings.video_issuer, audience=info.jwt_audience)
        return {"hls_url": f"{info.public_hls_url}/{path}/index.m3u8", "token": token,
                "expires_at": _epoch_iso(exp)}

    @app.post(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras/{{camera_id}}/playback")
    async def playback(tenant_id: str, camera_id: str, request: Request, p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.VIDEO_WATCH)
        cam = await _camera(deps, tenant, camera_id)
        now = deps.now()
        window_start = now - timedelta(seconds=PLAYBACK_WINDOW_SECONDS)
        # a segment that began up to one segment length before the window may still cover its start
        segs = await deps.store.list_segments(tenant.id, cam.id,
                                              since=window_start - timedelta(seconds=SEGMENT_SECONDS))
        if not segs:
            raise HTTPException(404, "no_recording")
        start = max(window_start, segs[0].started_at)
        duration = max(1.0, (now - start).total_seconds())
        path = video_path(tenant.id, cam.id)
        info = deps.stamp_info(tenant)
        token, exp = deps.keys.issue(action="playback", path=path, subject=str(p.user_id),
                                     issuer=deps.settings.video_issuer, audience=info.jwt_audience)
        q = urlencode({"path": path, "start": _iso(start), "duration": f"{duration:.1f}"})
        return {"url": f"{info.public_playback_url}/get?{q}", "token": token, "start": _iso(start),
                "duration_seconds": round(duration, 1), "expires_at": _epoch_iso(exp)}

    # ---- events: what the workers saw in the recordings (movement, named things)
    @app.get(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras/{{camera_id}}/events")
    async def list_events(tenant_id: str, camera_id: str, request: Request,
                          from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None,
                          limit: int = Query(default=EVENTS_PAGE, ge=1, le=EVENTS_PAGE_MAX),
                          offset: int = Query(default=0, ge=0),
                          p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.VIDEO_WATCH)
        cam = await _camera(deps, tenant, camera_id)
        until = _utc(to) if to is not None else deps.now()
        since = _utc(from_) if from_ is not None else None
        if since is not None and since >= until:
            raise HTTPException(422, "invalid_range")
        events = await deps.store.list_events(tenant.id, cam.id, since, until, limit, offset)   # newest first
        segs = []
        if events:      # only the part of the index that these events can fall in
            windows = [_event_window(e) for e in events]
            segs = await deps.store.list_segments(tenant.id, cam.id,
                                                  since=min(w[0] for w in windows) - timedelta(seconds=MAX_SEGMENT_SECONDS),
                                                  until=max(w[1] for w in windows))
        return [{"id": str(e.id), "kind": e.kind, "label": e.label, "score": None if e.score is None else round(e.score, 4), "model": e.model,
                 "started_at": _iso(e.started_at), "duration_ms": e.duration_ms,
                 "playable": _recorded(segs, *_event_window(e))} for e in events]

    @app.post(f"{API_PREFIX}/tenants/{{tenant_id}}/cameras/{{camera_id}}/events/{{event_id}}/playback")
    async def event_playback(tenant_id: str, camera_id: str, event_id: str, request: Request,
                             p: Principal = Depends(current_principal)):
        deps = _deps(request)
        tenant, _ = await _tenant(deps, p, tenant_id, Op.VIDEO_WATCH)
        cam = await _camera(deps, tenant, camera_id)
        ev = await deps.store.get_event(tenant.id, cam.id, _uuid(event_id))
        if ev is None:
            raise NotFound()
        wanted_start, wanted_end = _event_window(ev)
        segs = await deps.store.list_segments(tenant.id, cam.id,
                                              since=wanted_start - timedelta(seconds=MAX_SEGMENT_SECONDS),
                                              until=wanted_end)
        have = [s for s in segs if _overlaps(s, wanted_start, wanted_end)]
        if not have:      # events live longer than the recordings: this one's video is gone (MediaMTX deletes after a day)
            raise HTTPException(410, "recording_deleted")
        start = max(wanted_start, have[0].started_at)
        duration = max(1.0, (wanted_end - start).total_seconds())
        path = video_path(tenant.id, cam.id)
        info = deps.stamp_info(tenant)
        token, exp = deps.keys.issue(action="playback", path=path, subject=str(p.user_id),
                                     issuer=deps.settings.video_issuer, audience=info.jwt_audience)
        q = urlencode({"path": path, "start": _iso(start), "duration": f"{duration:.1f}"})
        return {"url": f"{info.public_playback_url}/get?{q}", "token": token, "start": _iso(start),
                "duration_seconds": round(duration, 1), "expires_at": _epoch_iso(exp)}

    # ---- a camera asks for its publish token (the camera has no user account)
    @app.post(f"{API_PREFIX}/camera/token")
    async def camera_token(body: CameraTokenRequest, request: Request):
        deps = _deps(request)
        cam = await deps.store.internal_camera(body.camera_id)
        # same answer for "no such camera" and "wrong secret"
        if cam is None or not verify_secret(body.secret, cam.secret_sha256):
            raise HTTPException(401, "unauthorized")
        path = video_path(cam.tenant_id, cam.id)
        tenant = await deps.store.get_tenant(cam.tenant_id)
        assert tenant is not None  # a camera row always has its tenant
        info = deps.stamp_info(tenant)
        token, exp = deps.keys.issue(action="publish", path=path, subject=str(cam.id),
                                     issuer=deps.settings.video_issuer, audience=info.jwt_audience)
        return {"token": token, "expires_at": _epoch_iso(exp), "path": path,
                "mediamtx_url": f"{info.public_rtsp_url}/{path}", "mqtt_url": info.public_mqtt_url}

    # ---- internal: only reachable from the compose network, never through the gateway
    @app.get("/jwks", include_in_schema=False)
    async def jwks(request: Request):
        return JSONResponse(_deps(request).keys.jwks(), headers={"Cache-Control": "max-age=300"})

    @app.post("/internal/v1/segments", include_in_schema=False)
    async def segment_done(request: Request):
        deps = _deps(request)
        given = request.headers.get("x-hook-secret", "")
        if not hmac.compare_digest(given.encode(), deps.hook_secret.encode()):
            raise HTTPException(403, "forbidden")
        try:
            ev = parse_hook_body(await request.body())
        except HookError as e:
            raise HTTPException(400, str(e)) from None
        # This door holds the first stamp's secret (the first version's door; in the later version the writer of each
        # stamp takes these calls). A camera of another stamp is not indexed here, so that the first stamp's secret
        # cannot write into another stamp's index.
        cam = await deps.store.internal_camera(ev.camera_id)
        tenant = await deps.store.get_tenant(cam.tenant_id) if cam is not None else None
        if tenant is None or tenant.stamp_id != HOOK_DOOR_STAMP:
            raise HTTPException(404, "unknown_camera")
        result = await record_hook(deps.store, ev)
        if result == "unknown_camera":
            raise HTTPException(404, "unknown_camera")
        return Response(status_code=204)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    return app
