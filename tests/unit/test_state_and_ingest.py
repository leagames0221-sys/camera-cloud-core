"""The rule for the camera state and the MQTT intake's parsing."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import seed_data as sd
from app.domain import CameraStatus
from app.ingest import BadMessage, handle_message, parse_state_message
from app.rules import derive_state
from app.store import MemoryStore
from helpers import cam_url, path_of

NOW = datetime(2026, 10, 24, 9, 0, 0, tzinfo=timezone.utc)


def st(state):
    return CameraStatus(sd.CAM_A1, state, None, None, None, NOW, NOW)


@pytest.mark.parametrize("status,ready,expected", [
    (st("online"), True, "recording"),
    (st("online"), False, "standby"),
    (st("offline"), True, "disconnected"),   # offline wins even if the path is still listed
    (st("offline"), False, "disconnected"),
    (None, True, "disconnected"),
    (None, False, "disconnected"),
])
def test_state_rule(status, ready, expected):
    assert derive_state(status, ready) == expected


def topic(cid=sd.CAM_A1):
    return f"cameras/{cid}/state"


def test_parse_online_with_numbers():
    s = parse_state_message(topic(), json.dumps({"state": "online", "fps": 15, "bitrate": 83.5}).encode(), NOW)
    assert (s.camera_id, s.state, s.fps, s.bitrate_kbps) == (sd.CAM_A1, "online", 15.0, 83.5)


def test_parse_offline_will_and_normal():
    s = parse_state_message(topic(), b'{"state":"offline","reason":"will"}', NOW)
    assert (s.state, s.reason) == ("offline", "will")
    s = parse_state_message(topic(), b'{"state":"offline","reason":"normal"}', NOW)
    assert s.reason == "normal"


@pytest.mark.parametrize("t,payload", [
    ("cameras/not-a-uuid/state", b'{"state":"online"}'),
    (f"cameras/{sd.CAM_A1}/other", b'{"state":"online"}'),
    (f"cameras/{sd.CAM_A1}/state/extra", b'{"state":"online"}'),
    (f"cameras/{str(sd.CAM_A1).upper()}/state", b'{"state":"online"}'),
    (topic(), b"online"),
    (topic(), b'{"state":"asleep"}'),
    (topic(), b"[1,2]"),
    (topic(), b"\xff\xfe"),
])
def test_bad_messages_are_refused(t, payload):
    with pytest.raises(BadMessage):
        parse_state_message(t, payload, NOW)


def test_non_numeric_or_negative_fps_is_dropped_not_stored():
    s = parse_state_message(topic(), b'{"state":"online","fps":"fast","bitrate":-1}', NOW)
    assert s.fps is None and s.bitrate_kbps is None
    s = parse_state_message(topic(), b'{"state":"online","fps":true}', NOW)
    assert s.fps is None


def test_unknown_camera_is_ignored(world):
    ok = asyncio.run(handle_message(world.store, topic(uuid.uuid4()), b'{"state":"online"}', NOW))
    assert ok is False


def test_state_since_changes_only_when_the_state_changes(world):
    async def go():
        await handle_message(world.store, topic(), b'{"state":"online"}', NOW)
        await handle_message(world.store, topic(), b'{"state":"online","fps":15}', NOW + timedelta(seconds=30))
        s1 = (await world.store.statuses(sd.TENANT_A))[sd.CAM_A1]
        await handle_message(world.store, topic(), b'{"state":"offline","reason":"will"}', NOW + timedelta(seconds=40))
        s2 = (await world.store.statuses(sd.TENANT_A))[sd.CAM_A1]
        return s1, s2

    s1, s2 = asyncio.run(go())
    assert s1.changed_at == NOW and s1.seen_at == NOW + timedelta(seconds=30)
    assert s2.state == "offline" and s2.changed_at == NOW + timedelta(seconds=40)


def test_camera_list_state_follows_status_and_path(client, world):
    h = world.auth(sd.USER_A_VIEWER)

    def states():
        r = client.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras", headers=h)
        return {c["id"]: c["state"] for c in r.json()}

    assert states() == {str(sd.CAM_A1): "disconnected", str(sd.CAM_A2): "disconnected"}
    asyncio.run(handle_message(world.store, topic(sd.CAM_A1), b'{"state":"online"}', NOW))
    assert states()[str(sd.CAM_A1)] == "standby"
    world.mediamtx.ready.add(path_of(sd.TENANT_A, sd.CAM_A1))
    assert states()[str(sd.CAM_A1)] == "recording"
    asyncio.run(handle_message(world.store, topic(sd.CAM_A1), b'{"state":"offline","reason":"will"}', NOW))
    assert states()[str(sd.CAM_A1)] == "disconnected"
    one = client.get(cam_url(sd.TENANT_A, sd.CAM_A1), headers=h).json()
    assert one["state"] == "disconnected"


def test_status_of_another_tenant_never_appears(client, world):
    asyncio.run(handle_message(world.store, topic(sd.CAM_B1), b'{"state":"online"}', NOW))
    r = client.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras", headers=world.auth(sd.USER_A_VIEWER))
    assert str(sd.CAM_B1) not in r.text
