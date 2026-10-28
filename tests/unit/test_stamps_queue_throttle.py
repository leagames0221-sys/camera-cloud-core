"""The pieces of the later version that need no running service: the per-tenant limit, the queue's pure logic, the
writer's door, the stamp lookup and the gateway's table of which API answers which kind of call."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import intake, seed_data as sd
from app.api import create_app as create_api
from app.config import Settings
from app.domain import Camera, CameraStatus, Segment, Tenant
from app.segments import HookEvent
from app.stamps import RoutedStore, StampInfo, StampRegistry, read_stamps_file
from app.store import MemoryStore
from app.throttle import TenantThrottle
from app.writer import WriterDeps, create_app as create_writer
from gateway import app as gw

from conftest import HOOK_SECRET

T1, T2 = uuid.uuid4(), uuid.uuid4()
C1 = uuid.uuid4()


# ---------------------------------------------------------------- the limit per tenant

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_throttle_lets_limit_calls_through_in_a_second_and_refuses_the_next():
    clock = Clock()
    th = TenantThrottle(20, clock)
    assert [th.take(T1) for _ in range(20)] == [0.0] * 20
    wait = th.take(T1)
    assert 0 < wait <= 1.0
    clock.t += wait + 0.001
    assert th.take(T1) == 0.0


def test_throttle_counts_each_tenant_separately():
    clock = Clock()
    th = TenantThrottle(2, clock)
    assert th.take(T1) == 0 and th.take(T1) == 0 and th.take(T1) > 0
    assert th.take(T2) == 0


def test_throttle_window_slides_instead_of_resetting_at_a_boundary():
    clock = Clock()
    th = TenantThrottle(2, clock)
    assert th.take(T1) == 0
    clock.t += 0.9
    assert th.take(T1) == 0
    clock.t += 0.2     # the first call is now older than a second, the second is not
    assert th.take(T1) == 0
    assert th.take(T1) > 0


def test_throttle_needs_a_positive_limit():
    with pytest.raises(ValueError):
        TenantThrottle(0)


def test_a_refused_call_does_not_use_up_the_tenants_calls(world):
    """Only a caller who belongs to the tenant and is allowed counts: someone from another tenant, and a viewer asking
    for something only an admin may do, are refused without touching the bucket."""
    async def factory():
        d = world.deps()
        d.throttle = TenantThrottle(3)
        return d

    with TestClient(create_api(world.settings, factory)) as c:
        url = f"/api/v1/tenants/{sd.TENANT_A}/cameras"
        for _ in range(5):   # a member of B asking for A's cameras: 404, never counted
            assert c.get(url, headers=world.auth(sd.USER_B_VIEWER)).status_code == 404
        for _ in range(5):   # a viewer of A registering a camera: 403, never counted
            assert c.post(url, json={"name": "x"}, headers=world.auth(sd.USER_A_VIEWER)).status_code == 403
        ok = [c.get(url, headers=world.auth(sd.USER_A_VIEWER)).status_code for _ in range(4)]
        assert ok == [200, 200, 200, 429]
        r = c.get(url, headers=world.auth(sd.USER_A_VIEWER))
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        # the other tenant is not affected
        assert c.get(f"/api/v1/tenants/{sd.TENANT_B}/cameras", headers=world.auth(sd.USER_B_VIEWER)).status_code == 200


# ---------------------------------------------------------------- the queue's pure logic

def at(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def test_entry_time_is_the_milliseconds_of_the_entry_id():
    assert intake.entry_time("1700000000123-5") == at(1700000000123)


def test_a_group_with_the_same_camera_twice_keeps_the_newest_only():
    cam = uuid.uuid4()
    other = uuid.uuid4()
    rows = [intake.StateRow(cam, "online", None, 15.0, 1800.0, at(1000)),
            intake.StateRow(other, "online", None, None, None, at(1500)),
            intake.StateRow(cam, "offline", "will", None, None, at(2000)),
            intake.StateRow(cam, "online", None, 14.0, 1700.0, at(1800))]
    kept = {r.camera_id: r for r in intake.newest_state_per_camera(rows)}
    assert len(kept) == 2 and kept[cam].state == "offline" and kept[cam].seen_at == at(2000)


def test_state_and_segment_survive_the_round_trip_through_the_queue_fields():
    f = intake.state_fields(C1, "online", None, 15.0, 1800.5)
    r = intake.state_row("1700000000000-0", f)
    assert (r.camera_id, r.state, r.reason, r.fps, r.bitrate_kbps) == (C1, "online", None, 15.0, 1800.5)
    f = intake.state_fields(C1, "offline", "will", None, None)
    r = intake.state_row("1700000000000-1", f)
    assert (r.reason, r.fps, r.bitrate_kbps) == ("will", None, None)

    ev = HookEvent(T1, C1, datetime(2026, 10, 24, 9, 0, 0, 123456, tzinfo=timezone.utc), 60000, "a.mp4")
    s = intake.segment_row(intake.segment_fields(ev))
    assert (s.tenant_id, s.camera_id, s.started_at, s.duration_ms, s.file_name) == (T1, C1, ev.started_at, 60000, "a.mp4")


@pytest.mark.parametrize("ms,reason", [(None, "no_length"), (0, "no_length"), (-5, "no_length"), (1, None),
                                       (60000, None), (120000, None), (120001, "too_long")])
def test_segment_length_must_be_above_zero_and_not_above_twice_a_segment(ms, reason):
    ev = HookEvent(T1, C1, datetime(2026, 10, 24, tzinfo=timezone.utc), ms, "a.mp4")
    assert intake.check_segment(ev) == reason


# ---------------------------------------------------------------- the writer's door

class FakeQueue:
    def __init__(self, fail=False):
        self.added, self.fail = [], fail
        self.r = self

    async def add(self, stream, fields):
        if self.fail:
            raise ConnectionError("down")
        self.added.append((stream, fields))

    async def ping(self):
        if self.fail:
            raise ConnectionError("down")

    async def xlen(self, name):
        return sum(1 for s, _ in self.added if s == name)


def writer_client(queue):
    async def factory():
        return WriterDeps(queue, HOOK_SECRET)
    return TestClient(create_writer(factory))


def hook_body(path, started="2026-10-24_09-00-00-000000", duration="60.0"):
    return f"path={path}&file=recordings/{path}/{started}.mp4&duration={duration}".encode()


def test_writer_door_puts_a_good_call_on_the_queue_and_answers_at_once():
    q = FakeQueue()
    path = f"{sd.TENANT_A}/{sd.CAM_A1}"
    with writer_client(q) as c:
        r = c.post("/internal/v1/segments", content=hook_body(path), headers={"X-Hook-Secret": HOOK_SECRET})
        assert r.status_code == 204
        stream, fields = q.added[0]
        assert stream == intake.SEGMENT_STREAM and fields["camera_id"] == str(sd.CAM_A1) and fields["duration_ms"] == "60000"
        counters = c.get("/internal/v1/counters", headers={"X-Hook-Secret": HOOK_SECRET}).json()
        assert counters["counters"]["segments_received"] == 1


def test_writer_door_refuses_a_wrong_secret_a_broken_call_and_a_bad_length():
    q = FakeQueue()
    path = f"{sd.TENANT_A}/{sd.CAM_A1}"
    with writer_client(q) as c:
        h = {"X-Hook-Secret": HOOK_SECRET}
        assert c.post("/internal/v1/segments", content=hook_body(path), headers={"X-Hook-Secret": "x"}).status_code == 403
        assert c.post("/internal/v1/segments", content=hook_body(path)).status_code == 403
        assert c.post("/internal/v1/segments", content=b"nonsense", headers=h).status_code == 400
        assert c.post("/internal/v1/segments", content=hook_body(path, duration="0"), headers=h).status_code == 400
        assert c.post("/internal/v1/segments", content=hook_body(path, duration="121"), headers=h).status_code == 400
        assert c.post("/internal/v1/segments", content=hook_body("a/b"), headers=h).status_code == 400
        assert q.added == []
        counters = c.get("/internal/v1/counters", headers=h).json()["counters"]
        assert counters["segment_rejected"] == 4 and counters["segment_rejected_no_length"] == 1


def test_writer_door_says_503_when_the_queue_is_down():
    q = FakeQueue(fail=True)
    path = f"{sd.TENANT_A}/{sd.CAM_A1}"
    with writer_client(q) as c:
        r = c.post("/internal/v1/segments", content=hook_body(path), headers={"X-Hook-Secret": HOOK_SECRET})
        assert r.status_code == 503
        assert c.get("/healthz").status_code == 503


# ---------------------------------------------------------------- stamps

def info(i, **kw):
    base = dict(id=i, name=f"stamp {i}", database_dsn="postgresql://x@h/db", database_password_file=None,
                mqtt_host="m", mqtt_port=1883, mqtt_admin_password_file=None, mediamtx_control_url="http://mtx:9997",
                public_hls_url=f"http://h:{8000 + i}", public_playback_url="p", public_rtsp_url="r", public_mqtt_url="q",
                jwt_audience=f"aud-{i}")
    base.update(kw)
    return StampInfo(**base)


def test_routed_store_sends_state_and_index_calls_to_the_tenants_stamp():
    async def run():
        control, other = MemoryStore(), MemoryStore()
        opened = []

        async def make(i):
            opened.append(i.id)
            return other

        reg = StampRegistry({1: info(1), 2: info(2)}, control, make)
        store = RoutedStore(control, reg)
        await store.seed_partner(sd.PARTNER_ID, "p")
        t_here = await store.create_tenant(sd.PARTNER_ID, "here", stamp_id=1)
        t_there = await store.create_tenant(sd.PARTNER_ID, "there", stamp_id=2)
        seg_a = Segment(t_here.id, C1, at(1000), 60000, "a.mp4", "hook")
        c2 = uuid.uuid4()
        seg_b = Segment(t_there.id, c2, at(1000), 60000, "b.mp4", "hook")
        assert await store.insert_segment(seg_a) and await store.insert_segment(seg_b)
        # each stamp's store holds only its own tenant's segment
        assert [s.file_name for s in await control.list_segments(t_here.id, C1)] == ["a.mp4"]
        assert await control.list_segments(t_there.id, c2) == []
        assert [s.file_name for s in await other.list_segments(t_there.id, c2)] == ["b.mp4"]
        assert [s.file_name for s in await store.list_segments(t_there.id, c2)] == ["b.mp4"]
        assert opened == [2]          # the second stamp was opened when first needed, once
        await store.list_segments(t_there.id, c2)
        assert opened == [2]
    asyncio.run(run())


def test_a_stamp_that_is_not_used_is_never_opened():
    async def run():
        control = MemoryStore()

        async def make(i):
            raise AssertionError("must not be opened")

        reg = StampRegistry({1: info(1), 2: info(2)}, control, make)
        store = RoutedStore(control, reg)
        await store.seed_partner(sd.PARTNER_ID, "p")
        t = await store.create_tenant(sd.PARTNER_ID, "here")
        assert await store.statuses(t.id) == {}
    asyncio.run(run())


def test_api_answers_use_the_urls_and_audience_of_the_tenants_stamp(world):
    reg = StampRegistry({1: info(1, public_hls_url="http://hls1", jwt_audience="a1"),
                         2: info(2, public_hls_url="http://hls2", jwt_audience="a2")}, world.store, None)
    asyncio.run(world.store.seed_tenant(sd.TENANT_B, sd.PARTNER_ID, "B"))
    world.store._tenants[sd.TENANT_B] = Tenant(sd.TENANT_B, sd.PARTNER_ID, "B", 2)

    async def factory():
        d = world.deps()
        d.stamps = reg
        return d

    with TestClient(create_api(world.settings, factory)) as c:
        a = c.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}/live", headers=world.auth(sd.USER_A_VIEWER)).json()
        b = c.post(f"/api/v1/tenants/{sd.TENANT_B}/cameras/{sd.CAM_B1}/live", headers=world.auth(sd.USER_B_VIEWER)).json()
        assert a["hls_url"].startswith("http://hls1/") and b["hls_url"].startswith("http://hls2/")
        import jwt
        assert jwt.decode(a["token"], options={"verify_signature": False})["aud"] == "a1"
        assert jwt.decode(b["token"], options={"verify_signature": False})["aud"] == "a2"


def test_stamps_file_is_checked(tmp_path):
    good = [{"id": 1, "name": "s1", "database_dsn": "d", "mqtt_host": "m", "mediamtx_control_url": "c",
             "public_hls_url": "h", "public_playback_url": "p", "public_rtsp_url": "r", "public_mqtt_url": "q",
             "jwt_audience": "a"}]
    f = tmp_path / "s.json"
    f.write_text(json.dumps(good))
    assert read_stamps_file(f)[0].mqtt_port == 1883
    f.write_text(json.dumps([{**good[0], "extra": 1}]))
    with pytest.raises(ValueError):
        read_stamps_file(f)
    f.write_text(json.dumps([{**good[0], "id": 2}]))
    with pytest.raises(ValueError):       # stamp 1 must be there
        read_stamps_file(f)


# ---------------------------------------------------------------- the gateway's table

@pytest.mark.parametrize("method,rest,kind", [
    ("GET", "v1/me", "me"), ("GET", "v1/tenants", "tenants"), ("POST", "v1/tenants", "tenants"),
    ("POST", "v1/tenants/abc/members", "members-add"), ("GET", "v1/tenants/abc/cameras", "cameras-list"),
    ("POST", "v1/tenants/abc/cameras", "camera-register"), ("GET", "v1/tenants/abc/cameras/d", "camera-get"),
    ("DELETE", "v1/tenants/abc/cameras/d", "camera-delete"), ("GET", "v1/tenants/abc/cameras/d/segments", "segments-list"),
    ("POST", "v1/tenants/abc/cameras/d/live", "live"), ("POST", "v1/tenants/abc/cameras/d/playback", "playback"),
    ("POST", "v1/camera/token", "camera-token"), ("GET", "v1/openapi.json", "openapi"),
    ("GET", "v1/openapiXjson", None), ("PUT", "v1/tenants/abc/cameras/d", None), ("GET", "v1/tenants/a/b/c/d/e", None)])
def test_gateway_recognises_each_kind_of_call(method, rest, kind):
    assert gw.shape_of(method, rest) == kind


def test_every_kind_of_call_has_an_entry_in_the_table_and_it_names_a_known_api():
    assert set(gw.ROUTES) == {n for n, _, _ in gw.SHAPES}
    assert set(gw.ROUTES.values()) == {"api-v2"}      # the first API is gone: the later one answers everything


def test_gateway_sends_each_kind_to_the_api_the_table_names():
    table = {n: "api-v2" for n, _, _ in gw.SHAPES}
    table["live"] = "other"
    assert gw.backend_for("POST", "v1/tenants/a/cameras/b/live", table) == "other"
    assert gw.backend_for("GET", "v1/me", table) == "api-v2"
    assert gw.backend_for("GET", "v1/unknown", table) == gw.DEFAULT_BACKEND    # no kind: the default


# ---------------------------------------------------------------- the order: write first, confirm after the commit

class OrderSink:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    async def apply_states(self, rows):
        self.log.append("write states")
        if self.fail:
            raise RuntimeError("database down")
        return len(rows)

    async def apply_segments(self, rows):
        self.log.append("write segments")
        return len(rows), 0


class OrderQueue:
    def __init__(self, log):
        self.log = log

    async def ack(self, stream, ids):
        self.log.append(f"confirm {stream} {len(ids)}")


def state_entry(n):
    return (f"1700000000{n:03d}-0", intake.state_fields(uuid.uuid4(), "online", None, 15.0, 100.0))


def test_a_group_is_confirmed_only_after_it_has_been_written():
    log = []
    segs = HookEvent(T1, C1, datetime(2026, 10, 24, tzinfo=timezone.utc), 60000, "a.mp4")
    group = {intake.STATE_STREAM: [state_entry(1), state_entry(2)],
             intake.SEGMENT_STREAM: [("1700000000005-0", intake.segment_fields(segs))]}
    asyncio.run(intake.write_group(OrderSink(log), OrderQueue(log), intake.Counters(), group))
    assert log == ["write states", f"confirm {intake.STATE_STREAM} 2",
                   "write segments", f"confirm {intake.SEGMENT_STREAM} 1"]


def test_a_group_that_cannot_be_written_is_not_confirmed():
    log = []
    with pytest.raises(RuntimeError):
        asyncio.run(intake.write_group(OrderSink(log, fail=True), OrderQueue(log), intake.Counters(),
                                       {intake.STATE_STREAM: [state_entry(1)]}))
    assert log == ["write states"]          # nothing was confirmed, so the entries come back after 30 seconds


# ---------------------------------------------------------------- which stamp a new tenant goes to

def tenant_client(world, ids):
    reg = StampRegistry({i: info(i) for i in ids}, world.store, None)

    async def factory():
        d = world.deps()
        d.stamps = reg
        return d

    return TestClient(create_api(world.settings, factory))


def test_a_new_tenant_goes_to_the_stamp_with_the_fewest_tenants(world):
    with tenant_client(world, [1, 2]) as c:
        h = world.auth(sd.USER_PARTNER_ADMIN)
        stamps = []
        for n in range(5):
            r = c.post("/api/v1/tenants", json={"name": f"t{n}"}, headers=h)
            assert r.status_code == 201, r.text
            stamps.append(world.store._tenants[uuid.UUID(r.json()["id"])].stamp_id)
        # A and B are on stamp 1 already (2 tenants): 2, 2, then 1 and 2 alternate by count, ties go to the lower number
        assert stamps == [2, 2, 1, 2, 1]


def test_the_partner_administrator_may_name_the_stamp_and_a_wrong_one_is_refused(world):
    with tenant_client(world, [1, 2]) as c:
        h = world.auth(sd.USER_PARTNER_ADMIN)
        r = c.post("/api/v1/tenants", json={"name": "named", "stamp_id": 1}, headers=h)
        assert r.status_code == 201 and world.store._tenants[uuid.UUID(r.json()["id"])].stamp_id == 1
        assert c.post("/api/v1/tenants", json={"name": "x", "stamp_id": 3}, headers=h).status_code == 422
        assert c.post("/api/v1/tenants", json={"name": "x", "stamp_id": 0}, headers=h).status_code == 422
        # without a list of stamps there is only stamp 1
    async def factory():
        return world.deps()
    with TestClient(create_api(world.settings, factory)) as c:
        r = c.post("/api/v1/tenants", json={"name": "only"}, headers=world.auth(sd.USER_PARTNER_ADMIN))
        assert world.store._tenants[uuid.UUID(r.json()["id"])].stamp_id == 1



# ---------------------------------------------------------------- taking again and giving up: the 5th and the 6th handing out

class PendingEntries:
    """What Valkey keeps for a consumer group: the entries handed out and not confirmed, with how often each was handed
    out. XAUTOCLAIM hands each of them out once more (the count goes up by one), XACK forgets them."""

    def __init__(self):
        self.pending: dict[str, list] = {}     # id -> [fields, times handed out]
        self.dead: list[dict] = []

    def hand_out(self, eid, fields):           # what XREADGROUP does the first time
        self.pending[eid] = [fields, 1]

    async def xautoclaim(self, stream, group, consumer, min_idle, start, count=None):
        out = []
        for eid in sorted(self.pending)[:count]:
            self.pending[eid][1] += 1
            out.append((eid, self.pending[eid][0]))
        return ["0-0", out, []]

    async def xpending_range(self, stream, group, mn, mx, count):
        ids = [e for e in sorted(self.pending) if mn <= e <= mx][:count]
        return [{"message_id": e, "consumer": "w", "time_since_delivered": 31000, "times_delivered": self.pending[e][1]}
                for e in ids]

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        assert stream == intake.DEAD_STREAM
        self.dead.append(fields)
        return "1-0"

    async def xack(self, stream, group, *ids):
        for e in ids:
            self.pending.pop(e, None)


def test_an_entry_is_taken_again_up_to_the_5th_handing_out_and_given_up_at_the_6th():
    """The first handing out is XREADGROUP; the 2nd to the 5th are XAUTOCLAIMs that give it back to be written again;
    the 6th moves it to the dead stream and confirms it."""
    async def run():
        r = PendingEntries()
        q = intake.Queue(r, "w")
        r.hand_out("1700000000000-0", {"x": "cannot be written"})
        seen = []
        for n in range(2, 8):
            again, dead = await q.reclaim(intake.STATE_STREAM)
            seen.append((n, len(again), len(dead)))
            if dead:
                await q.bury(intake.STATE_STREAM, dead)
                break
        return seen, r
    seen, r = asyncio.run(run())
    assert seen == [(2, 1, 0), (3, 1, 0), (4, 1, 0), (5, 1, 0), (6, 0, 1)]
    assert r.pending == {} and len(r.dead) == 1 and r.dead[0]["id"] == "1700000000000-0"


@pytest.mark.parametrize("times,gives_up", [(2, False), (5, False), (6, True), (9, True)])
def test_the_limit_of_handings_out_is_more_than_five(times, gives_up):
    async def run():
        r = PendingEntries()
        r.pending["1700000000000-0"] = [{"x": "y"}, times - 1]      # the claim below is handing out no. `times`
        return await intake.Queue(r, "w").reclaim(intake.SEGMENT_STREAM)
    again, dead = asyncio.run(run())
    assert (len(again), len(dead)) == ((0, 1) if gives_up else (1, 0))


# ---------------------------------------------------------------- the writer counts what its stamp does not index

class SegmentSink:
    def __init__(self, written, outside):
        self.answer = (written, outside)

    async def apply_states(self, rows):
        return len(rows)

    async def apply_segments(self, rows):
        return self.answer


def test_segments_of_a_camera_that_is_not_this_stamps_are_counted():
    counters = intake.Counters()
    ev = HookEvent(T1, C1, datetime(2026, 10, 24, tzinfo=timezone.utc), 60000, "a.mp4")
    group = {intake.SEGMENT_STREAM: [(f"170000000000{n}-0", intake.segment_fields(ev)) for n in range(3)]}
    asyncio.run(intake.write_group(SegmentSink(1, 2), OrderQueue([]), counters, group))
    assert counters.values["segment_rows_written"] == 1 and counters.values["segment_not_this_stamp"] == 2
    counters = intake.Counters()
    asyncio.run(intake.write_group(SegmentSink(3, 0), OrderQueue([]), counters, group))
    assert "segment_not_this_stamp" not in counters.values


def test_the_segment_insert_checks_the_camera_and_its_stamp_in_the_same_statement():
    sql = " ".join(intake.INSERT_SEGMENTS.split())
    assert "JOIN cameras c ON c.id = u.camera_id AND c.tenant_id = u.tenant_id AND c.deleted_at IS NULL" in sql
    assert "c.stamp_id = $6" in sql and "ON CONFLICT (camera_id, started_at) DO NOTHING" in sql


def test_the_writers_counters_need_the_stamps_secret():
    with writer_client(FakeQueue()) as c:
        assert c.get("/internal/v1/counters").status_code == 403
        assert c.get("/internal/v1/counters", headers={"X-Hook-Secret": "x"}).status_code == 403
        assert c.get("/internal/v1/counters", headers={"X-Hook-Secret": HOOK_SECRET}).status_code == 200


# ---------------------------------------------------------------- the API's own segment door is the first stamp's

def test_the_apis_segment_door_does_not_index_a_camera_of_another_stamp(world, client):
    world.store._tenants[sd.TENANT_B] = Tenant(sd.TENANT_B, sd.PARTNER_ID, "B", 2)
    h = {"X-Hook-Secret": HOOK_SECRET}
    b = hook_body(f"{sd.TENANT_B}/{sd.CAM_B1}")
    assert client.post("/internal/v1/segments", content=b, headers=h).status_code == 404
    assert asyncio.run(world.store.list_segments(sd.TENANT_B, sd.CAM_B1)) == []
    # a camera of the first stamp still goes in
    a = hook_body(f"{sd.TENANT_A}/{sd.CAM_A1}")
    assert client.post("/internal/v1/segments", content=a, headers=h).status_code == 204
    assert len(asyncio.run(world.store.list_segments(sd.TENANT_A, sd.CAM_A1))) == 1


# ---------------------------------------------------------------- every token and address of a later stamp's tenant

def test_playback_and_the_publish_token_use_the_audience_and_addresses_of_the_tenants_stamp(world):
    import jwt
    from conftest import CAMERA_SECRETS
    reg = StampRegistry({1: info(1, public_playback_url="http://pb1", public_rtsp_url="rtsp://one", jwt_audience="a1"),
                         2: info(2, public_playback_url="http://pb2", public_rtsp_url="rtsp://two",
                                 public_mqtt_url="mqtt://two", jwt_audience="a2")}, world.store, None)
    world.store._tenants[sd.TENANT_B] = Tenant(sd.TENANT_B, sd.PARTNER_ID, "B", 2)
    for tid, cid in ((sd.TENANT_B, sd.CAM_B1), (sd.TENANT_A, sd.CAM_A1)):
        asyncio.run(world.store.insert_segment(Segment(tid, cid, world.now - timedelta(seconds=90), 60000, "s.mp4",
                                                       "hook")))

    async def factory():
        d = world.deps()
        d.stamps = reg
        return d

    def aud(token):
        return jwt.decode(token, options={"verify_signature": False})["aud"]

    with TestClient(create_api(world.settings, factory)) as c:
        b = c.post(f"/api/v1/tenants/{sd.TENANT_B}/cameras/{sd.CAM_B1}/playback", headers=world.auth(sd.USER_B_VIEWER))
        a = c.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}/playback", headers=world.auth(sd.USER_A_VIEWER))
        assert b.status_code == 200 and a.status_code == 200
        assert b.json()["url"].startswith("http://pb2/get?") and aud(b.json()["token"]) == "a2"
        assert a.json()["url"].startswith("http://pb1/get?") and aud(a.json()["token"]) == "a1"
        pb = c.post("/api/v1/camera/token", json={"camera_id": str(sd.CAM_B1), "secret": CAMERA_SECRETS[sd.CAM_B1]}).json()
        assert aud(pb["token"]) == "a2" and pb["mediamtx_url"].startswith("rtsp://two/") and pb["mqtt_url"] == "mqtt://two"
        pa = c.post("/api/v1/camera/token", json={"camera_id": str(sd.CAM_A1), "secret": CAMERA_SECRETS[sd.CAM_A1]}).json()
        assert aud(pa["token"]) == "a1" and pa["mediamtx_url"].startswith("rtsp://one/")


@pytest.mark.parametrize("value", [True, "2", 2.5])
def test_the_stamp_of_a_new_tenant_must_be_a_whole_number(world, value):
    with tenant_client(world, [1, 2]) as c:
        r = c.post("/api/v1/tenants", json={"name": "typed", "stamp_id": value}, headers=world.auth(sd.USER_PARTNER_ADMIN))
        assert r.status_code == 422
        assert not any(t.name == "typed" for t in world.store._tenants.values())
