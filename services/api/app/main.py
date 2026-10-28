"""Production wiring: `uvicorn --factory app.main:build_app`."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import pathlib

import asyncpg
import httpx
from fastapi import FastAPI

from . import seed as seeding
from . import seed_data as sd
from .api import Deps, create_app
from .config import Settings
from .domain import utcnow
from .dynsec import DynsecClient
from .ingest import run_ingest
from .mediamtx import MediaMtxClient, MediaMtxPool
from .oidc import DeviceTokenVerifier, JwksCache, TokenVerifier, http_jwks_fetcher
from .pgstore import STAMP_MIGRATIONS_DIR, PgStore, migrate
from .segments import announce_body, reconcile_camera
from .stamps import (RoutedStore, StampInfo, StampRegistry, configured_stamps, read_stamps,
                     write_stamps)
from .throttle import TenantThrottle
from .video_jwt import KeyStore

log = logging.getLogger("ccc.main")

ANNOUNCE_STAMP = 1       # the stamp whose writer announces segments to the motion worker (its secret is the one the API holds)
DEVICE_CLIENT_ID = "ccc-device"
ENROLL_AUDIENCE = "ccc-enroll"


async def _connect(url: str) -> asyncpg.Pool:
    """The database container may still be starting; wait up to about a minute."""
    delay = 1.0
    for attempt in range(30):
        try:
            return await asyncpg.create_pool(url, min_size=1, max_size=8)
        except (OSError, asyncpg.PostgresError) as e:
            log.warning("database not ready (%s); retry %d", e, attempt + 1)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 5.0)
    raise RuntimeError("database did not become ready")


async def _provision(dyn: DynsecClient, stamp_id: int, camera_secrets: dict, ingest_password: str) -> None:
    """Roles, the intake user and the demo cameras' users in one stamp's broker (repeatable)."""
    delay = 1.0
    while True:
        try:
            await dyn.provision_base(ingest_password)
            for cid, secret in camera_secrets.items():
                await dyn.add_camera(cid, secret, force_password=True)
            log.info("stamp %d: MQTT roles and %d demo camera users are in place", stamp_id, len(camera_secrets))
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("stamp %d: MQTT provisioning not done yet (%s); retry in %.0fs", stamp_id, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15.0)


async def _open_stamp(store: RoutedStore, stamp_id: int) -> None:
    """Open a stamp's own database (creating its tables) and copy the camera ledger into it; keep trying until the
    stamp is up, so that its writer finds the tables."""
    delay = 1.0
    while True:
        try:
            n = await store.sync_stamp(stamp_id)
            log.info("stamp %d: database open, %d cameras copied", stamp_id, n)
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("stamp %d is not reachable yet (%s); retry in %.0fs", stamp_id, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15.0)


async def _reconcile_loop(deps: Deps) -> None:
    await asyncio.sleep(deps.settings.reconcile_interval)
    while True:
        try:
            for cam in await deps.store.internal_all_cameras():
                try:
                    tenant = await deps.store.get_tenant(cam.tenant_id)
                    mediamtx = deps.mediamtx.for_stamp(tenant.stamp_id)
                    plan = await reconcile_camera(deps.store, mediamtx, cam.tenant_id, cam.id, utcnow())
                    if plan.to_insert or plan.to_mark_deleted:
                        log.info("reconcile %s: +%d gaps, %d deleted", cam.id, len(plan.to_insert),
                                 len(plan.to_mark_deleted))
                    if plan.to_insert and deps.announce is not None and tenant.stamp_id == ANNOUNCE_STAMP:
                        await deps.announce(cam.id, plan.to_insert)
                except Exception as e:  # noqa: BLE001 - one camera must not stop the others
                    log.warning("reconcile %s failed: %s", cam.id, e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("reconcile round failed: %s", e)
        await asyncio.sleep(deps.settings.reconcile_interval)


def _cancel(task: asyncio.Task):
    async def closer() -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return closer


def _file(path: str | None, fallback) -> str | None:
    return pathlib.Path(path).read_text(encoding="utf-8").strip() if path else fallback()


async def build_deps(settings: Settings) -> Deps:
    pool = await _connect(settings.database_url)
    applied = await migrate(pool)
    log.info("migrations applied now: %s", applied or "none")
    control = PgStore(pool)
    # The stamps in the settings go into the stamps table; every data call then goes through RoutedStore, which is the
    # one place that turns a tenant into a stamp. Stamp 1 uses the control database itself.
    await write_stamps(pool, configured_stamps(settings))
    infos = await read_stamps(pool)
    stamp_pools: list[asyncpg.Pool] = []

    async def open_stamp_store(info: StampInfo):
        password = _file(info.database_password_file, lambda: None)
        p = await asyncpg.create_pool(info.database_dsn, password=password, min_size=1, max_size=4, timeout=10)
        try:
            await migrate(p, STAMP_MIGRATIONS_DIR)
        except Exception:
            await p.close()
            raise
        stamp_pools.append(p)
        return PgStore(p)

    registry = StampRegistry(infos, control, open_stamp_store)
    store = RoutedStore(control, registry)
    camera_secrets = await seeding.seed(store, settings)

    keys = KeyStore.from_file(settings.secrets_dir / "video_signing_key.pem")
    http = httpx.AsyncClient()
    jwks = JwksCache(http_jwks_fetcher(settings.oidc_jwks_url, http))
    verifier = TokenVerifier(jwks, settings.oidc_issuer, settings.oidc_client_id)
    # each stamp's MediaMTX is called with a five-minute token that carries that stamp's audience
    mediamtx = MediaMtxPool({
        sid: MediaMtxClient(dataclasses.replace(settings, mediamtx_control_url=i.mediamtx_control_url,
                                                video_audience=i.jwt_audience), keys, http)
        for sid, i in infos.items()})
    dynsec = {sid: DynsecClient(i.mqtt_host, i.mqtt_port, "admin",
                                _file(i.mqtt_admin_password_file, lambda: settings.secret("dynsec_admin_password")))
              for sid, i in infos.items()}
    hook_secret = settings.secret("hook_secret")

    async def announce(camera_id, starts) -> None:
        """Tell the first stamp's writer about segments the reconciliation added; a failure is only logged (the segments are
        in the index either way, and the next round of the reconciliation does not add them again, so they are not looked at)."""
        try:
            r = await http.post(f"{settings.announce_url}/internal/v1/recorded", json=announce_body(camera_id, starts),
                                headers={"X-Hook-Secret": hook_secret}, timeout=10)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("could not tell the writer about %d added segments of %s (%s)", len(starts), camera_id, e)

    deps = Deps(settings, store, verifier, keys, mediamtx, dynsec[1], hook_secret,
                throttle=TenantThrottle(settings.tenant_rate) if settings.tenant_rate > 0 else None,
                device_verifier=DeviceTokenVerifier(verifier, DEVICE_CLIENT_ID, ENROLL_AUDIENCE),
                dynsec_by_stamp=dynsec, stamps=registry,
                announce=announce if settings.announce_url else None)

    async def close_resources() -> None:
        await http.aclose()
        for p in stamp_pools:
            await p.close()
        await pool.close()

    if settings.background:
        stamp_of_tenant = {tid: 1 for tid, _ in sd.TENANTS} | {tid: st for tid, _, st in sd.EXTRA_TENANTS}
        coros = []
        for sid in infos:
            mine = {cid: camera_secrets[cid] for cid, tid, _ in sd.CAMERAS + sd.EXTRA_CAMERAS
                    if cid in camera_secrets and stamp_of_tenant[tid] == sid}
            ingest_pw = settings.secret("mqtt_ingest_password" if sid == 1 else f"stamp{sid}_mqtt_ingest_password")
            coros.append(_provision(dynsec[sid], sid, mine, ingest_pw))
            if sid != 1:
                coros.append(_open_stamp(store, sid))
        coros.append(_reconcile_loop(deps))
        if settings.ingest:  # in the later version the writer of each stamp does the intake
            coros.append(run_ingest(store, settings.mqtt_host, settings.mqtt_port, "ingest",
                                    settings.secret("mqtt_ingest_password")))
        for coro in coros:
            deps.background.append(_cancel(asyncio.create_task(coro)))
    deps.background.insert(0, close_resources)  # closed last: closers run in reverse order
    return deps


def build_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()

    async def factory() -> Deps:
        return await build_deps(settings)

    return create_app(settings, factory)
