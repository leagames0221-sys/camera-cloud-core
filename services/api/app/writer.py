"""The writer of one stamp (same image as the API, a different start). It takes the two streams of traffic that grow with
the number of cameras and puts them on the stamp's queue instead of into the database one at a time:

  - MQTT state messages (it is the subscriber that the API itself was in the first version);
  - MediaMTX's closed-segment calls (a POST on the internal door; it answers as soon as the call is on the queue).

A second part of the same process (intake.read_loop) takes the queue in groups and writes each group to the stamp's
database. It holds no signing key and no secret of another stamp: it mounts only its own stamp's folder of the secrets
volume. Doors under /internal/ are never published; the gateway answers 404 for them.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import pathlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

import asyncpg
import valkey.asyncio as valkey
from fastapi import FastAPI, HTTPException, Request, Response

from . import intake
from .domain import utcnow
from .ingest import BadMessage, parse_state_message, run_ingest
from .segments import HookError, parse_hook_body

log = logging.getLogger("ccc.writer")


@dataclass
class WriterDeps:
    queue: intake.Queue
    hook_secret: str
    counters: intake.Counters = field(default_factory=intake.Counters)
    background: list = field(default_factory=list)   # async closers, run in reverse order
    sink: object | None = None      # the stamp's database writes (to look segments up before announcing them)
    announce: bool = False          # tell the motion worker about the segments of this stamp (only the first stamp does)


def create_app(deps_factory: Callable[[], Awaitable[WriterDeps]]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        deps = await deps_factory()
        app.state.deps = deps
        try:
            yield
        finally:
            for closer in reversed(deps.background):
                await closer()

    app = FastAPI(title="camera-cloud-core writer", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def check_secret(request: Request, d: WriterDeps) -> None:
        """The doors of the writer are on the compose network that the cameras are on too, so each asks for the stamp's
        closed-segment secret (the same reason the API does not leave MediaMTX's Control API open on that network)."""
        given = request.headers.get("x-hook-secret", "")
        if not hmac.compare_digest(given.encode(), d.hook_secret.encode()):
            raise HTTPException(403, "forbidden")

    @app.post("/internal/v1/segments")
    async def segment_done(request: Request):
        d: WriterDeps = request.app.state.deps
        check_secret(request, d)
        try:
            ev = parse_hook_body(await request.body())
        except HookError as e:
            d.counters.add("segment_rejected")
            raise HTTPException(400, str(e)) from None
        reason = intake.check_segment(ev)
        if reason:
            d.counters.add("segment_rejected")
            d.counters.add(f"segment_rejected_{reason}")
            raise HTTPException(400, reason)
        try:
            await d.queue.add(intake.SEGMENT_STREAM, intake.segment_fields(ev))
        except Exception:  # noqa: BLE001 - the queue is down: MediaMTX logs the failure; reconciliation fills the gap
            d.counters.add("segment_not_queued")
            raise HTTPException(503, "queue_unavailable") from None
        d.counters.add("segments_received")
        return Response(status_code=204)

    @app.post("/internal/v1/recorded")
    async def recorded(request: Request):
        """The reconciliation of the control plane tells about segments it added to the index (the closed-segment call was
        lost). Each is looked up in the index here and announced to the motion worker if it is there."""
        d: WriterDeps = request.app.state.deps
        check_secret(request, d)
        if not d.announce or d.sink is None:
            raise HTTPException(404, "not_announced_here")
        try:
            body = await request.json()
            keys = [(uuid.UUID(s["camera_id"]), datetime.fromisoformat(s["started_at"])) for s in body["segments"]]
            if not 0 < len(keys) <= 500 or any(k[1].tzinfo is None for k in keys):
                raise ValueError("count or zone")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "bad_request") from None
        rows = [intake.SegmentRow(uuid.UUID(int=0), k[0], k[1], 0, "") for k in keys]   # only camera and start are used
        if not await intake.announce_segments(d.sink, d.queue, d.counters, rows):
            raise HTTPException(503, "queue_unavailable")
        return Response(status_code=204)

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request):
        d: WriterDeps = request.app.state.deps
        try:
            await d.queue.r.ping()
        except Exception:  # noqa: BLE001
            raise HTTPException(503, "queue_unavailable") from None
        return {"status": "ok"}

    @app.get("/internal/v1/counters")
    async def counters(request: Request):
        d: WriterDeps = request.app.state.deps
        check_secret(request, d)
        lengths = {}
        for name in (intake.STATE_STREAM, intake.SEGMENT_STREAM, intake.DEAD_STREAM, intake.RECORDED_STREAM,
                     intake.EVENT_STREAM, "ccc:dead:motion", "ccc:dead:classify"):
            lengths[name] = await d.queue.r.xlen(name)
        return {"counters": d.counters.values, "stream_lengths": lengths}

    return app


async def _handle_state(d: WriterDeps, topic: str, payload: bytes) -> None:
    try:
        s = parse_state_message(topic, payload, utcnow())
    except BadMessage:
        d.counters.add("state_rejected")
        return
    await d.queue.add(intake.STATE_STREAM,
                      intake.state_fields(s.camera_id, s.state, s.reason, s.fps, s.bitrate_kbps))
    d.counters.add("states_received")


def _secret(directory: pathlib.Path, name: str) -> str:
    return (directory / name).read_text(encoding="utf-8").strip()


def event_cameras_from(env) -> Callable[[], set[uuid.UUID]]:
    """The cameras whose events the writer takes: the list the motion worker reads (EVENT_CAMERAS_FILE, mounted read only and
    read again for every group, so a camera added later counts without a restart). Without the setting no event is taken."""
    path = env.get("EVENT_CAMERAS_FILE", "")
    if not path:
        return lambda: set()
    return lambda: intake.read_camera_list(path)


async def build_deps() -> WriterDeps:
    e = os.environ
    stamp_id = int(e.get("STAMP_ID", "1"))
    secrets = pathlib.Path(e.get("SECRETS_DIR", "/run/secret"))
    client = valkey.Valkey(host=e.get("VALKEY_HOST", "valkey"), port=int(e.get("VALKEY_PORT", "6379")),
                           username=e.get("VALKEY_USER", "writer"), password=_secret(secrets, "valkey_password"),
                           decode_responses=True)
    queue = intake.Queue(client, consumer=f"writer-{stamp_id}")
    deps = WriterDeps(queue, _secret(secrets, "hook_secret"))
    # the password may be in a file of this stamp's folder instead of in the address
    password = _secret(secrets, e["DATABASE_PASSWORD_FILE"]) if e.get("DATABASE_PASSWORD_FILE") else None
    pool = await asyncpg.create_pool(e["DATABASE_URL"], password=password, min_size=1, max_size=4)
    sink = intake.Sink(pool, stamp_id)
    deps.sink = sink
    deps.announce = e.get("ANNOUNCE_RECORDED", "false").lower() in ("1", "true", "yes")   # the stamps of the motion worker
    event_cameras = event_cameras_from(e)
    stop = asyncio.Event()

    async def handler(topic: str, payload: bytes) -> None:
        await _handle_state(deps, topic, payload)

    tasks = [
        asyncio.create_task(intake.read_loop(sink, queue, deps.counters, stop, deps.announce, event_cameras)),
        # the client id differs from the first version's intake ("ccc-ingest"): a second client with the same id would
        # make the broker drop the first
        asyncio.create_task(run_ingest(None, e.get("MQTT_HOST", "mosquitto"), int(e.get("MQTT_PORT", "1883")),
                                       "ingest", _secret(secrets, "mqtt_ingest_password"),
                                       identifier=f"ccc-writer-{stamp_id}", handler=handler)),
    ]

    async def close_tasks() -> None:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close_resources() -> None:
        await client.aclose()
        await pool.close()

    deps.background.extend([close_resources, close_tasks])  # closers run in reverse: tasks stop first
    return deps


def build_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app(build_deps)
