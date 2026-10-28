"""MQTT intake: subscribes to cameras/+/state with the intake credentials and writes the latest state to the store."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

import aiomqtt

from .domain import CameraStatus, is_lower_uuid, utcnow
from .store import Store

log = logging.getLogger("ccc.ingest")

STATE_FILTER = "cameras/+/state"
STATES = ("online", "offline")


class BadMessage(ValueError):
    pass


def parse_state_message(topic: str, payload: bytes, now: datetime) -> CameraStatus:
    """cameras/<uuid>/state with a JSON body {"state": "online"|"offline", "reason"?, "fps"?, "bitrate"?}."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "cameras" or parts[2] != "state" or not is_lower_uuid(parts[1]):
        raise BadMessage("unexpected topic")
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise BadMessage("payload is not JSON") from e
    if not isinstance(body, dict) or body.get("state") not in STATES:
        raise BadMessage("state must be online or offline")

    def number(key: str) -> float | None:
        v = body.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v) if 0 <= v < 1e9 else None

    reason = body.get("reason")
    reason = reason[:40] if isinstance(reason, str) else None
    return CameraStatus(uuid.UUID(parts[1]), body["state"], reason, number("fps"), number("bitrate"), now, now)


async def handle_message(store: Store, topic: str, payload: bytes, now: datetime | None = None) -> bool:
    try:
        status = parse_state_message(topic, payload, now or utcnow())
    except BadMessage as e:
        log.warning("dropped a state message: %s", e)
        return False
    return await store.internal_apply_status(status)


async def run_ingest(store: Store | None, host: str, port: int, username: str, password: str, *,
                     identifier: str = "ccc-ingest", handler=None) -> None:
    """Runs forever; reconnects with a short back-off. Cancel the task to stop it. `handler(topic, payload)` is what
    happens to a message; the default writes it to the store at once (the first version), the writer of a stamp passes
    a handler that puts it on its queue."""
    if handler is None:
        async def handler(topic: str, payload: bytes) -> None:
            await handle_message(store, topic, payload)
    delay = 1.0
    while True:
        try:
            async with aiomqtt.Client(host, port, username=username, password=password, identifier=identifier,
                                      protocol=aiomqtt.ProtocolVersion.V5, keepalive=60) as c:
                await c.subscribe(STATE_FILTER, qos=1)
                log.info("intake subscribed to %s", STATE_FILTER)
                delay = 1.0
                async for m in c.messages:
                    await handler(str(m.topic), bytes(m.payload))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - keep the intake alive whatever the broker does
            log.warning("intake connection lost (%s); retrying in %.0fs", e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 15.0)
