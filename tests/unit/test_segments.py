"""The recording index: hook parsing, non-overlapping keys, and reconciliation against MediaMTX's real answer shape."""
import asyncio
import json
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import seed_data as sd
from app.domain import Segment
from app.mediamtx import parse_rfc3339, ready_paths, recording_starts
from app.segments import (HookError, parse_hook_body, plan_reconcile, reconcile_camera, record_hook,
                          started_at_from_file)
from conftest import HOOK_SECRET
from helpers import path_of

FIX = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
P_A1 = path_of(sd.TENANT_A, sd.CAM_A1)


def body(path=P_A1, file=None, duration="9.999999999"):
    file = file or f"./recordings/{path}/2026-10-24_08-07-45-067638.mp4"
    return f"path={path}&file={file}&duration={duration}".encode()


# ---- hook parsing (the body shape was captured from a real MediaMTX run, see docs/validation/poc.md)

def test_real_hook_body():
    ev = parse_hook_body(body())
    assert (ev.tenant_id, ev.camera_id) == (sd.TENANT_A, sd.CAM_A1)
    assert ev.started_at == datetime(2026, 10, 24, 8, 7, 45, 67638, tzinfo=timezone.utc)
    assert ev.duration_ms == 10000
    assert ev.file_name == "2026-10-24_08-07-45-067638.mp4"


@pytest.mark.parametrize("raw", [
    b"", b"garbage", b"path=a/b", b"file=x",
    body(path="not/uuids"),
    body(path=f"{sd.TENANT_A}"),
    body(path=f"{sd.TENANT_A}/{sd.CAM_A1}/extra"),
    body(file="./recordings/other/path/2026-10-24_08-07-45-067638.mp4"),   # file outside this path's folder
    body(file=f"./recordings/{P_A1}/no-time.mp4"),
    body(file=f"./recordings/{P_A1}/2026-13-45_99-99-99-000000.mp4"),
    body(duration="abc"),
    body(duration="-3"),
    b"\xff\xfe",
])
def test_bad_hook_bodies(raw):
    with pytest.raises(HookError):
        parse_hook_body(raw)


def test_file_name_time_is_utc_with_microseconds():
    assert started_at_from_file("x/2026-01-02_03-04-05-000006.mp4") == datetime(2026, 1, 2, 3, 4, 5, 6,
                                                                                 tzinfo=timezone.utc)


# ---- the receiver

def test_receiver_requires_the_shared_secret(client, world):
    assert client.post("/internal/v1/segments", content=body()).status_code == 403
    r = client.post("/internal/v1/segments", content=body(), headers={"X-Hook-Secret": "wrong"})
    assert r.status_code == 403
    r = client.post("/internal/v1/segments", content=body(), headers={"X-Hook-Secret": HOOK_SECRET})
    assert r.status_code == 204


def test_receiver_is_idempotent_and_keyed_by_path_and_start(client, world):
    h = {"X-Hook-Secret": HOOK_SECRET}
    for _ in range(3):
        assert client.post("/internal/v1/segments", content=body(), headers=h).status_code == 204
    other = body(file=f"./recordings/{P_A1}/2026-10-24_08-08-45-067638.mp4")
    assert client.post("/internal/v1/segments", content=other, headers=h).status_code == 204
    segs = asyncio.run(world.store.list_segments(sd.TENANT_A, sd.CAM_A1))
    assert len(segs) == 2


def test_receiver_refuses_unknown_camera_and_wrong_tenant(client, world):
    h = {"X-Hook-Secret": HOOK_SECRET}
    unknown = path_of(sd.TENANT_A, uuid.uuid4())
    assert client.post("/internal/v1/segments", content=body(path=unknown), headers=h).status_code == 404
    # the camera exists, but under tenant B: a path that names it under tenant A must not write a row
    wrong = path_of(sd.TENANT_A, sd.CAM_B1)
    assert client.post("/internal/v1/segments", content=body(path=wrong), headers=h).status_code == 404
    assert asyncio.run(world.store.list_segments(sd.TENANT_B, sd.CAM_B1)) == []


def test_receiver_bad_body_is_400(client):
    r = client.post("/internal/v1/segments", content=b"nonsense", headers={"X-Hook-Secret": HOOK_SECRET})
    assert r.status_code == 400


# ---- reconciliation against the saved real answers

def test_real_control_api_fixtures_parse():
    rec = json.loads((FIX / "mediamtx_recordings_get.json").read_text())
    starts = recording_starts(rec)
    assert len(starts) == 7 and starts == sorted(starts)
    assert starts[0] == datetime(2026, 10, 24, 8, 7, 45, 67638, tzinfo=timezone.utc)
    paths = json.loads((FIX / "mediamtx_paths_list.json").read_text())
    assert ready_paths(paths) == {"11111111-1111-4111-8111-111111111111/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}


@pytest.mark.parametrize("s,micro", [("2026-10-24T08:09:24.56448Z", 564480), ("2026-10-24T08:09:24Z", 0),
                                      ("2026-10-24T08:09:24.123456789Z", 123456), ("2026-10-24T08:09:24.5+00:00", 500000)])
def test_rfc3339_fractions(s, micro):
    assert parse_rfc3339(s).microsecond == micro and parse_rfc3339(s).utcoffset() == timedelta(0)


def T(sec):
    return datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=sec)


def test_plan_fills_gaps_and_marks_deleted():
    listed = [T(60), T(120), T(180), T(240)]
    known = {T(0), T(60), T(180)}            # T(0) was auto-deleted; T(120) and T(240) are gaps
    plan = plan_reconcile(listed, known, now=T(600))
    assert plan.to_insert == [T(120), T(240)]
    assert plan.to_mark_deleted == [T(0)]


def test_plan_leaves_the_open_segment_alone():
    listed = [T(0), T(60)]                   # T(60) is still open at now = T(100)
    plan = plan_reconcile(listed, {T(0)}, now=T(100))
    assert plan.to_insert == [] and plan.to_mark_deleted == []
    plan = plan_reconcile(listed, {T(0)}, now=T(151))
    assert plan.to_insert == [T(60)]


def test_plan_is_empty_when_everything_matches():
    s = [T(0), T(60), T(120)]
    plan = plan_reconcile(s, set(s), now=T(1000))
    assert plan.to_insert == [] and plan.to_mark_deleted == []


class FakeMtx:
    def __init__(self, starts):
        self.starts = starts

    async def recording_starts(self, path):
        assert path == P_A1
        return self.starts


def test_reconcile_inserts_gaps_once_and_marks_deleted(world):
    async def go():
        store = world.store
        await store.insert_segment(Segment(sd.TENANT_A, sd.CAM_A1, T(0), 60000, "a.mp4", "hook"))
        await store.insert_segment(Segment(sd.TENANT_A, sd.CAM_A1, T(60), 60000, "b.mp4", "hook"))
        mtx = FakeMtx([T(60), T(120), T(180)])
        p1 = await reconcile_camera(store, mtx, sd.TENANT_A, sd.CAM_A1, T(1000))
        p2 = await reconcile_camera(store, mtx, sd.TENANT_A, sd.CAM_A1, T(1000))
        segs = await store.list_segments(sd.TENANT_A, sd.CAM_A1)
        return p1, p2, segs

    p1, p2, segs = asyncio.run(go())
    assert p1.to_insert == [T(120), T(180)] and p1.to_mark_deleted == [T(0)]
    assert p2.to_insert == [] and p2.to_mark_deleted == []
    assert [s.started_at for s in segs] == [T(60), T(120), T(180)]
    assert [s.source for s in segs] == ["hook", "reconcile", "reconcile"]
    assert segs[1].duration_ms is None and segs[1].file_name.endswith("-000000.mp4")


def test_hook_after_reconcile_does_not_duplicate(world):
    async def go():
        await reconcile_camera(world.store, FakeMtx([T(0)]), sd.TENANT_A, sd.CAM_A1, T(1000))
        ev = parse_hook_body(body(file=f"./recordings/{P_A1}/2026-10-24_08-00-00-000000.mp4"))
        return await record_hook(world.store, ev), await world.store.list_segments(sd.TENANT_A, sd.CAM_A1)

    result, segs = asyncio.run(go())
    assert result == "duplicate" and len(segs) == 1


def test_segments_endpoint_lists_only_own_camera(client, world):
    asyncio.run(world.store.insert_segment(Segment(sd.TENANT_B, sd.CAM_B1, world.now - timedelta(minutes=1), 60000,
                                                   "b.mp4", "hook")))
    asyncio.run(world.store.insert_segment(Segment(sd.TENANT_A, sd.CAM_A1, world.now - timedelta(minutes=1), 60000,
                                                   "a.mp4", "hook")))
    r = client.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}/segments", headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 200 and len(r.json()) == 1
    r = client.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_B1}/segments", headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 404
