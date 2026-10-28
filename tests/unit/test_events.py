"""Events: the writer's side (the stream of events into the table, the announcement of segments), the API's two routes, the
queue's accounts for the workers, the check camera's lap clock, and the reconciliation's message."""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import init_secrets as init, intake, seed_data as sd
from app.api import create_app as create_api
from app.domain import Event, Segment, Tenant
from app.segments import announce_body
from app.stamps import StampInfo, StampRegistry
from app.writer import WriterDeps, create_app as create_writer
from camera import LapClock, online_payload, parse_progress
from conftest import HOOK_SECRET

T1, C1 = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 10, 24, 9, 0, 0, tzinfo=timezone.utc)


def ev_fields(**kw):
    f = {"tenant_id": str(T1), "camera_id": str(C1), "kind": "object", "label": "cat", "score": "0.9990",
         "started_at": "2026-10-24T08:00:30.500000+00:00", "duration_ms": "0", "model": "m@1"}
    f.update(kw)
    return f


# ---------------------------------------------------------------- an event from the stream

def test_an_event_entry_becomes_a_row_and_a_motion_event_has_the_empty_label():
    r = intake.event_row(ev_fields())
    assert (r.kind, r.label, r.score, r.duration_ms, r.model) == ("object", "cat", 0.999, 0, "m@1")
    assert r.started_at == datetime(2026, 10, 24, 8, 0, 30, 500000, tzinfo=timezone.utc)
    m = intake.event_row(ev_fields(kind="motion", label="", score="", duration_ms="5000", model=""))
    assert (m.label, m.score, m.duration_ms) == ("", None, 5000)


@pytest.mark.parametrize("bad", [
    {"kind": "other"}, {"kind": "motion", "label": "cat"}, {"label": "x" * 101}, {"started_at": "2026-10-24T08:00:00"},
    {"started_at": "soon"}, {"duration_ms": "-1"}, {"duration_ms": "86400001"}, {"score": "1.5"}, {"score": "-0.1"},
    {"tenant_id": "x"}, {"duration_ms": "abc"}])
def test_an_entry_the_table_would_refuse_is_refused_before_it_can_fail_a_whole_group(bad):
    with pytest.raises((ValueError, KeyError)):
        intake.event_row(ev_fields(**bad))
    with pytest.raises(KeyError):
        intake.event_row({"kind": "object"})


def test_the_event_insert_checks_camera_stamp_and_a_segment_that_contains_the_time_and_has_the_key_of_the_table():
    sql = " ".join(intake.INSERT_EVENTS.split())
    assert "JOIN cameras c ON c.id = u.camera_id AND c.tenant_id = u.tenant_id AND c.deleted_at IS NULL AND c.stamp_id = $9" in sql
    assert "FROM recording_segments s WHERE s.camera_id = u.camera_id AND s.tenant_id = u.tenant_id AND NOT s.deleted" in sql
    assert "s.started_at <= u.started_at AND u.started_at < s.started_at + COALESCE(s.duration_ms, 120000)" in sql
    assert "ON CONFLICT (camera_id, kind, label, started_at) DO NOTHING" in sql


def test_both_migrations_make_the_same_table_with_the_key_and_the_empty_default():
    root = __import__("pathlib").Path(__file__).resolve().parents[2] / "services/api"
    a = (root / "migrations/0005_events.sql").read_text(encoding="utf-8")
    b = (root / "stamp_migrations/0002_events.sql").read_text(encoding="utf-8")
    body = lambda s: "\n".join(x for x in s.splitlines() if not x.startswith("--"))   # noqa: E731
    assert body(a) == body(b)
    assert "label       text NOT NULL DEFAULT ''" in a and "UNIQUE (camera_id, kind, label, started_at)" in a
    assert "FOREIGN KEY (camera_id, tenant_id) REFERENCES cameras (id, tenant_id)" in a


# ---------------------------------------------------------------- writing a group, announcing segments

class Log:
    def __init__(self):
        self.items = []

    def __call__(self, what):
        self.items.append(what)


class FullSink:
    """A sink that records what it is asked, and looks segments up in what it has `indexed`."""

    def __init__(self, log, indexed=None, event_answer=(None, 0), fail_lookup=False):
        self.log, self.indexed, self.event_answer, self.fail_lookup = log, indexed or {}, event_answer, fail_lookup

    async def apply_states(self, rows):
        return len(rows)

    async def apply_segments(self, rows):
        self.log("write segments")
        return len(rows), 0

    async def apply_events(self, rows):
        self.log(f"write events {len(rows)}")
        written, outside = self.event_answer
        return (len(rows) if written is None else written), outside

    async def indexed_segments(self, keys):
        self.log("look up")
        if self.fail_lookup:
            raise RuntimeError("database down")
        return [self.indexed[k] for k in keys if k in self.indexed]


class LogQueue:
    def __init__(self, log, fail_add=False):
        self.log, self.fail_add, self.added = log, fail_add, []

    async def add(self, stream, fields):
        if self.fail_add:
            raise ConnectionError("down")
        self.log(f"add {stream}")
        self.added.append((stream, fields))

    async def ack(self, stream, ids):
        self.log(f"confirm {stream} {len(ids)}")


START = datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc)


def seg_entry(n, camera=C1, start=START):
    ev = type("E", (), {})()
    from app.segments import HookEvent
    return (f"17000000000{n:02d}-0", intake.segment_fields(HookEvent(T1, camera, start + timedelta(seconds=60 * n), 60000,
                                                                   f"f{n}.mp4")))


def row_for(n, camera=C1):
    start = START + timedelta(seconds=60 * n)
    return (camera, start), (intake.SegmentRow(T1, camera, start, 60000, f"f{n}.mp4"), 60000)


def test_after_the_commit_and_before_the_confirmation_every_segment_of_the_group_that_is_in_the_index_is_announced():
    log = Log()
    # three entries in the group, two are in the index (the third's camera is not this stamp's), one of the two was there before
    entries = [seg_entry(0), seg_entry(1), seg_entry(2, camera=uuid.uuid4())]
    sink = FullSink(log, indexed=dict([row_for(0), row_for(1)]))
    q = LogQueue(log)
    counters = intake.Counters()
    asyncio.run(intake.write_group(sink, q, counters, {intake.SEGMENT_STREAM: entries}, announce=True))
    assert log.items == ["write segments", "look up", f"add {intake.RECORDED_STREAM}", f"add {intake.RECORDED_STREAM}",
                         f"confirm {intake.SEGMENT_STREAM} 3"]
    (_, first), (_, second) = q.added
    assert first == {"tenant_id": str(T1), "camera_id": str(C1), "started_at": START.isoformat(), "duration_ms": "60000",
                     "file_name": "f0.mp4"}
    assert second["file_name"] == "f1.mp4" and "path" not in first
    assert counters.values["recorded_announced"] == 2


def test_nothing_is_announced_when_it_is_switched_off_as_in_the_second_stamp():
    log = Log()
    q = LogQueue(log)
    asyncio.run(intake.write_group(FullSink(log, indexed=dict([row_for(0)])), q, intake.Counters(),
                                   {intake.SEGMENT_STREAM: [seg_entry(0)]}))
    assert q.added == [] and "look up" not in log.items and log.items[-1] == f"confirm {intake.SEGMENT_STREAM} 1"


def test_a_failure_to_announce_is_counted_and_does_not_stop_the_writing_or_the_confirmation():
    for sink_fails, queue_fails in ((True, False), (False, True)):
        log = Log()
        sink = FullSink(log, indexed=dict([row_for(0)]), fail_lookup=sink_fails)
        counters = intake.Counters()
        asyncio.run(intake.write_group(sink, LogQueue(log, fail_add=queue_fails), counters,
                                       {intake.SEGMENT_STREAM: [seg_entry(0)]}, announce=True))
        assert counters.values["recorded_not_announced"] == 1 and "recorded_announced" not in counters.values
        assert log.items[0] == "write segments" and log.items[-1] == f"confirm {intake.SEGMENT_STREAM} 1"


def test_a_group_written_again_after_a_crash_is_announced_again_at_least_once():
    """The crash is between the commit and the confirmation: nothing is confirmed, the entries come back, and the same
    segments are written (the insert does nothing for rows that are there) and announced once more."""
    log = Log()
    sink = FullSink(log, indexed=dict([row_for(0), row_for(1)]))
    q = LogQueue(log)
    group = {intake.SEGMENT_STREAM: [seg_entry(0), seg_entry(1)]}
    asyncio.run(intake.write_group(sink, q, intake.Counters(), group, announce=True))
    asyncio.run(intake.write_group(sink, q, intake.Counters(), group, announce=True))
    assert [f["file_name"] for _, f in q.added] == ["f0.mp4", "f1.mp4", "f0.mp4", "f1.mp4"]


def test_events_are_written_then_confirmed_and_what_the_table_refused_is_counted():
    log = Log()
    good = ("1700000000001-0", ev_fields())
    bad = ("1700000000002-0", ev_fields(kind="other"))
    sink = FullSink(log, event_answer=(1, 1))
    counters = intake.Counters()
    asyncio.run(intake.write_group(sink, LogQueue(log), counters, {intake.EVENT_STREAM: [good, bad]}))
    assert log.items == ["write events 1", f"confirm {intake.EVENT_STREAM} 2"]
    assert counters.values == {"event_unreadable": 1, "event_rows_written": 1, "event_rejected": 1, "event_entries_done": 2}


def test_an_event_that_cannot_be_written_is_not_confirmed():
    class Failing(FullSink):
        async def apply_events(self, rows):
            raise RuntimeError("database down")
    log = Log()
    with pytest.raises(RuntimeError):
        asyncio.run(intake.write_group(Failing(log), LogQueue(log), intake.Counters(),
                                       {intake.EVENT_STREAM: [("1-0", ev_fields())]}))
    assert log.items == []


def test_the_queue_reads_the_event_stream_too():
    assert intake.EVENT_STREAM in intake.READ_STREAMS and intake.RECORDED_STREAM not in intake.READ_STREAMS


# ---------------------------------------------------------------- the writer's door for the reconciliation

class DoorQueue:
    def __init__(self, fail=False):
        self.added, self.fail, self.r = [], fail, self

    async def add(self, stream, fields):
        if self.fail:
            raise ConnectionError("down")
        self.added.append((stream, fields))

    async def ping(self):
        return True

    async def xlen(self, name):
        return 0


def door(queue, announce=True, indexed=None):
    async def factory():
        return WriterDeps(queue, HOOK_SECRET, sink=FullSink(Log(), indexed=indexed or {}), announce=announce)
    return TestClient(create_writer(factory))


def test_the_reconciliation_tells_the_writer_which_segments_it_added_and_only_those_in_the_index_are_announced():
    q = DoorQueue()
    body = announce_body(C1, [START, START + timedelta(seconds=60)])
    with door(q, indexed=dict([row_for(0)])) as c:
        h = {"X-Hook-Secret": HOOK_SECRET}
        assert c.post("/internal/v1/recorded", json=body).status_code == 403
        assert c.post("/internal/v1/recorded", json=body, headers={"X-Hook-Secret": "x"}).status_code == 403
        assert c.post("/internal/v1/recorded", json={"segments": []}, headers=h).status_code == 400
        assert c.post("/internal/v1/recorded", json={"segments": [{"camera_id": "x", "started_at": "y"}]}, headers=h).status_code == 400
        assert c.post("/internal/v1/recorded", json={"segments": [{"camera_id": str(C1), "started_at": "2026-10-24T08:00:00"}]},
                      headers=h).status_code == 400
        assert q.added == []
        assert c.post("/internal/v1/recorded", json=body, headers=h).status_code == 204
        assert [f["file_name"] for _, f in q.added] == ["f0.mp4"]       # the second start is not in the index: not announced


def test_the_door_is_closed_where_nothing_is_announced_and_says_503_when_the_queue_is_down():
    body = announce_body(C1, [START])
    h = {"X-Hook-Secret": HOOK_SECRET}
    with door(DoorQueue(), announce=False) as c:
        assert c.post("/internal/v1/recorded", json=body, headers=h).status_code == 404
    with door(DoorQueue(fail=True), indexed=dict([row_for(0)])) as c:
        assert c.post("/internal/v1/recorded", json=body, headers=h).status_code == 503


def test_the_message_of_the_reconciliation_has_the_camera_and_the_start_times_in_utc():
    body = announce_body(C1, [START.astimezone(timezone(timedelta(hours=9)))])
    assert body == {"segments": [{"camera_id": str(C1), "started_at": "2026-10-24T08:00:00+00:00"}]}


# ---------------------------------------------------------------- the API: the list of events and playing one

CAM, TEN = sd.CAM_A1, sd.TENANT_A


def event(world, n, kind="object", label="cat", score=0.99, start=None, duration_ms=0, cam=CAM, ten=TEN):
    e = Event(uuid.uuid4(), ten, cam, kind, label, start or world.now - timedelta(minutes=10, seconds=n), duration_ms,
              score, "m@1")
    world.store.add_event(e)
    return e


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def url(tail="", tenant=TEN, cam=CAM):
    return f"/api/v1/tenants/{tenant}/cameras/{cam}/events{tail}"


def test_the_viewer_gets_the_events_newest_first_with_whether_their_video_is_still_there(world, client):
    old = event(world, 0, start=world.now - timedelta(hours=3))
    new = event(world, 0, kind="motion", label="", score=None, start=world.now - timedelta(minutes=2), duration_ms=5000)
    asyncio.run(world.store.insert_segment(Segment(TEN, CAM, world.now - timedelta(minutes=3), 60000, "s.mp4", "hook")))
    r = client.get(url(), headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [str(new.id), str(old.id)]
    assert rows[0] == {"id": str(new.id), "kind": "motion", "label": "", "score": None, "model": "m@1",
                       "started_at": iso(new.started_at), "duration_ms": 5000, "playable": True}
    assert rows[1]["playable"] is False and rows[1]["score"] == 0.99          # no segment of that hour any more


def test_a_list_has_100_events_unless_asked_otherwise_and_the_next_page_comes_with_offset(world, client):
    for n in range(230):
        event(world, n, start=world.now - timedelta(minutes=5, seconds=n))
    h = world.auth(sd.USER_A_VIEWER)
    first = client.get(url(), headers=h).json()
    second = client.get(url("?offset=100"), headers=h).json()
    third = client.get(url("?limit=100&offset=200"), headers=h).json()
    assert [len(first), len(second), len(third)] == [100, 100, 30]
    everything = first + second + third
    assert len({x["id"] for x in everything}) == 230                                 # no event twice, none missed
    assert [x["started_at"] for x in everything] == sorted((x["started_at"] for x in everything), reverse=True)
    assert client.get(url("?offset=230"), headers=h).json() == []                    # past the end: an empty page
    assert len(client.get(url("?limit=7"), headers=h).json()) == 7


def test_limit_is_between_1_and_1000_and_offset_is_not_negative(world, client):
    for n in range(3):
        event(world, n)
    h = world.auth(sd.USER_A_VIEWER)
    for bad in ("?limit=0", "?limit=1001", "?limit=-1", "?limit=many", "?offset=-1", "?offset=x"):
        assert client.get(url(bad), headers=h).status_code == 422, bad
    assert len(client.get(url("?limit=1000"), headers=h).json()) == 3
    assert len(client.get(url("?limit=1"), headers=h).json()) == 1


def test_there_is_no_limit_of_time_and_from_and_to_cut_the_list(world, client):
    event(world, 0, start=world.now - timedelta(days=40))
    event(world, 1, start=world.now - timedelta(minutes=5))
    h = world.auth(sd.USER_A_VIEWER)
    assert len(client.get(url(), headers=h).json()) == 2                              # an event of 40 days ago is listed
    recent = client.get(url(f"?from={iso(world.now - timedelta(hours=1))}&to={iso(world.now)}"), headers=h).json()
    assert len(recent) == 1
    assert client.get(url(f"?from={iso(world.now)}&to={iso(world.now - timedelta(hours=1))}"), headers=h).status_code == 422
    assert client.get(url("?from=yesterday"), headers=h).status_code == 422


def test_a_page_says_which_of_its_events_have_their_video_from_the_part_of_the_index_it_needs(world, client):
    old = event(world, 0, start=world.now - timedelta(days=3))
    new = event(world, 0, start=world.now - timedelta(minutes=4))
    asyncio.run(world.store.insert_segment(Segment(TEN, CAM, world.now - timedelta(minutes=5), 60000, "s.mp4", "hook")))
    h = world.auth(sd.USER_A_VIEWER)
    both = {x["id"]: x["playable"] for x in client.get(url(), headers=h).json()}
    assert both == {str(new.id): True, str(old.id): False}
    assert [x["playable"] for x in client.get(url("?limit=1&offset=1"), headers=h).json()] == [False]


def test_events_follow_the_rules_of_the_video_and_of_the_tenant(world, client):
    event(world, 0)
    assert client.get(url()).status_code == 401
    assert client.get(url(), headers=world.auth(sd.USER_PARTNER_ADMIN)).status_code == 403     # the partner does not watch
    assert client.get(url(), headers=world.auth(sd.USER_A_ADMIN)).status_code == 200
    # a member of company B asking for company A's camera, and A's camera under B's address: not found
    assert client.get(url(), headers=world.auth(sd.USER_B_VIEWER)).status_code == 404
    assert client.get(url(tenant=sd.TENANT_B), headers=world.auth(sd.USER_B_VIEWER)).status_code == 404
    assert client.get(url(cam="nope"), headers=world.auth(sd.USER_A_VIEWER)).status_code == 404


def test_company_a_never_sees_the_events_of_company_b(world, client):
    event(world, 0, cam=sd.CAM_B1, ten=sd.TENANT_B, label="dog")
    mine = client.get(url(), headers=world.auth(sd.USER_A_VIEWER)).json()
    assert mine == []
    theirs = client.get(url(tenant=sd.TENANT_B, cam=sd.CAM_B1), headers=world.auth(sd.USER_B_VIEWER)).json()
    assert [x["label"] for x in theirs] == ["dog"]


def test_playing_an_event_starts_5_seconds_before_and_lasts_the_event_plus_10_seconds(world, client):
    start = world.now - timedelta(minutes=10)
    asyncio.run(world.store.insert_segment(Segment(TEN, CAM, start - timedelta(seconds=20), 60000, "s.mp4", "hook")))
    e = event(world, 0, kind="motion", label="", score=None, start=start, duration_ms=7000)
    r = client.post(url(f"/{e.id}/playback"), headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["start"] == iso(start - timedelta(seconds=5)) and b["duration_seconds"] == 17.0
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(b["url"]).query)
    assert q["path"] == [f"{TEN}/{CAM}"] and q["start"] == [b["start"]] and q["duration"] == ["17.0"]
    import jwt
    claims = jwt.decode(b["token"], options={"verify_signature": False})
    perms = claims["mediamtx_permissions"]
    assert perms == [{"action": "playback", "path": f"{TEN}/{CAM}"}] and claims["aud"] == "mediamtx"
    # an object event has no length: 10 seconds
    o = event(world, 1, start=start + timedelta(seconds=30))
    assert client.post(url(f"/{o.id}/playback"), headers=world.auth(sd.USER_A_VIEWER)).json()["duration_seconds"] == 10.0


def test_the_playback_does_not_start_before_the_first_segment_there_is(world, client):
    start = world.now - timedelta(minutes=10)
    seg_start = start - timedelta(seconds=2)
    asyncio.run(world.store.insert_segment(Segment(TEN, CAM, seg_start, 60000, "s.mp4", "hook")))
    e = event(world, 0, start=start)
    b = client.post(url(f"/{e.id}/playback"), headers=world.auth(sd.USER_A_VIEWER)).json()
    assert b["start"] == iso(seg_start) and b["duration_seconds"] == 7.0      # the window ends at 5 s after the event as before; only its start moved


def test_an_event_whose_video_is_gone_says_so_and_does_not_give_a_token(world, client):
    e = event(world, 0, start=world.now - timedelta(days=2))
    asyncio.run(world.store.insert_segment(Segment(TEN, CAM, e.started_at - timedelta(seconds=10), 60000, "s.mp4", "hook")))
    asyncio.run(world.store.mark_segments_deleted(TEN, CAM, [e.started_at - timedelta(seconds=10)]))
    r = client.post(url(f"/{e.id}/playback"), headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 410 and r.json() == {"detail": "recording_deleted"} and "token" not in r.text


def test_playing_an_event_follows_the_same_rules_as_listing_them(world, client):
    asyncio.run(world.store.insert_segment(Segment(TEN, CAM, world.now - timedelta(minutes=11), 60000, "s.mp4", "hook")))
    e = event(world, 0, start=world.now - timedelta(minutes=10, seconds=30))
    p = url(f"/{e.id}/playback")
    assert client.post(p).status_code == 401
    assert client.post(p, headers=world.auth(sd.USER_PARTNER_ADMIN)).status_code == 403
    assert client.post(p, headers=world.auth(sd.USER_B_VIEWER)).status_code == 404
    assert client.post(url(f"/{uuid.uuid4()}/playback"), headers=world.auth(sd.USER_A_VIEWER)).status_code == 404
    assert client.post(url("/not-a-uuid/playback"), headers=world.auth(sd.USER_A_VIEWER)).status_code == 404
    # the event of A's first camera is not found under A's second camera
    assert client.post(url(f"/{e.id}/playback", cam=sd.CAM_A2), headers=world.auth(sd.USER_A_VIEWER)).status_code == 404
    assert client.post(p, headers=world.auth(sd.USER_A_VIEWER)).status_code == 200


def test_an_event_of_a_camera_of_the_second_stamp_is_played_with_that_stamps_address_and_audience(world):
    world.store._tenants[sd.TENANT_B] = Tenant(sd.TENANT_B, sd.PARTNER_ID, "B", 2)
    asyncio.run(world.store.insert_segment(Segment(sd.TENANT_B, sd.CAM_B1, world.now - timedelta(minutes=11), 60000, "s.mp4", "hook")))
    e = event(world, 0, cam=sd.CAM_B1, ten=sd.TENANT_B, start=world.now - timedelta(minutes=10, seconds=30))
    info = lambda i: StampInfo(i, f"s{i}", "d", None, "m", 1883, None, "c", f"h{i}", f"http://pb{i}", "r", "q", f"aud-{i}")   # noqa: E731
    reg = StampRegistry({1: info(1), 2: info(2)}, world.store, None)

    async def factory():
        d = world.deps()
        d.stamps = reg
        return d

    with TestClient(create_api(world.settings, factory)) as c:
        b = c.post(url(f"/{e.id}/playback", tenant=sd.TENANT_B, cam=sd.CAM_B1), headers=world.auth(sd.USER_B_VIEWER)).json()
    import jwt
    assert b["url"].startswith("http://pb2/get?") and jwt.decode(b["token"], options={"verify_signature": False})["aud"] == "aud-2"


# ---------------------------------------------------------------- the queue's accounts for the two workers

def test_the_acl_gives_each_worker_only_the_stream_commands_it_uses_on_only_the_streams_it_uses(tmp_path, monkeypatch):
    monkeypatch.setattr("os.chown", lambda *a: None, raising=False)
    init.init_secrets(tmp_path)
    acl = (tmp_path / "valkey1/users.acl").read_text()
    lines = {l.split()[1]: l for l in acl.splitlines() if l.startswith("user ") and l.split()[1] != "default"}
    assert set(lines) == {"writer", "motion", "classify"} and acl.startswith("user default off\n")
    for name in ("motion", "classify"):
        pw = (tmp_path / f"vision1/{name}/valkey_password").read_text()
        assert f"#{hashlib.sha256(pw.encode()).hexdigest()}" in lines[name] and pw not in acl
        assert "~ccc:*" not in lines[name] and "+@stream" not in lines[name] and "-@all" in lines[name]
    # motion reads ccc:recorded and adds to the other three; classify reads ccc:motion and adds to two
    assert "(~ccc:recorded +xreadgroup +xack +xautoclaim +xpending +xgroup|create)" in lines["motion"]
    assert "(~ccc:motion ~ccc:events ~ccc:dead:motion +xadd)" in lines["motion"]
    assert "(~ccc:motion +xreadgroup +xack +xautoclaim +xpending +xgroup|create)" in lines["classify"]
    assert "(~ccc:events ~ccc:dead:classify +xadd)" in lines["classify"]
    assert "ccc:recorded" not in lines["classify"]                       # classify never touches the announcements
    assert "~ccc:* -@all +@stream" in lines["writer"]                      # the writer's account is as it was
    # each worker has a folder of its own with its own password and nothing else
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file() and "vision" in p.as_posix()}
    assert files == {"vision1/motion/valkey_password", "vision1/classify/valkey_password"}
    assert (tmp_path / "vision1/motion/valkey_password").read_text() != (tmp_path / "vision1/classify/valkey_password").read_text()


def test_an_account_file_of_an_earlier_run_is_made_again_when_the_rules_changed_and_left_when_they_did_not(tmp_path, monkeypatch):
    monkeypatch.setattr("os.chown", lambda *a: None, raising=False)
    acl = tmp_path / "valkey1/users.acl"
    init.init_secrets(tmp_path)
    pw = {w: (tmp_path / f"vision1/{w}/valkey_password").read_text() for w in ("motion", "classify")}
    writer_pw = (tmp_path / "stamp1/valkey_password").read_text()
    # the file of the earlier version: the writer only
    acl.chmod(0o600)
    digest = hashlib.sha256(writer_pw.encode()).hexdigest()
    acl.write_text(f"user default off\nuser writer on #{digest} {init.VALKEY_RULES}\n")
    made = init.init_secrets(tmp_path)
    assert made == ["valkey1/users.acl"]
    assert "user motion on " in acl.read_text() and "user classify on " in acl.read_text()
    assert {w: (tmp_path / f"vision1/{w}/valkey_password").read_text() for w in pw} == pw     # the passwords are the same
    assert (tmp_path / "stamp1/valkey_password").read_text() == writer_pw
    before = acl.read_bytes()
    assert init.init_secrets(tmp_path) == [] and acl.read_bytes() == before                    # nothing to do the next time


def test_the_second_stamp_has_no_worker_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr("os.chown", lambda *a: None, raising=False)
    init.init_secrets(tmp_path)
    acl2 = (tmp_path / "valkey2/users.acl").read_text()
    assert "motion" not in acl2 and "classify" not in acl2 and not (tmp_path / "vision2").exists()


# ---------------------------------------------------------------- the check camera

def test_the_lap_clock_finds_the_start_of_each_round_from_the_output_position():
    clock = LapClock(180.0)
    t0 = datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc)
    assert clock.update(0.5, t0 + timedelta(seconds=0.5)) and clock.lap == 0 and clock.started_at == t0
    assert not clock.update(100.0, t0 + timedelta(seconds=100))
    assert clock.started_at == t0
    assert clock.update(180.2, t0 + timedelta(seconds=180.3)) and clock.lap == 1
    assert clock.started_at == t0 + timedelta(seconds=180.1)           # the round began 0.2 s of output ago
    assert clock.update(361.0, t0 + timedelta(seconds=361)) and clock.lap == 2 and clock.started_at == t0 + timedelta(seconds=361 - 1.0)


def test_the_position_of_the_output_is_read_from_the_progress_lines_and_is_part_of_the_state_when_given():
    s: dict = {}
    for line in ("out_time_us=12500000\n", "out_time_us=N/A\n"):
        parse_progress(line, s)
    assert s == {"out_time": 12.5}
    assert json.loads(online_payload(15.0, 40.0, "2026-10-25T09:00:00.000Z")) == {
        "state": "online", "fps": 15.0, "bitrate": 40.0, "lap_started_at": "2026-10-25T09:00:00.000Z"}
    assert "lap_started_at" not in json.loads(online_payload(15.0, 40.0))


# ---------------------------------------------------------------- the gateway

@pytest.mark.parametrize("method,rest,kind", [
    ("GET", "v1/tenants/abc/cameras/d/events", "events-list"),
    ("POST", "v1/tenants/abc/cameras/d/events/e/playback", "event-playback"),
    ("POST", "v1/tenants/abc/cameras/d/events", None), ("GET", "v1/tenants/abc/cameras/d/events/e/playback", None),
    ("GET", "v1/tenants/abc/cameras/d/events/e", None)])
def test_the_gateway_knows_the_two_routes_of_the_events_and_sends_them_to_the_api(method, rest, kind):
    from gateway import app as gw
    assert gw.shape_of(method, rest) == kind
    if kind:
        assert gw.ROUTES[kind] == "api-v2"


# ---------------------------------------------------------------- only the cameras that are looked at; no event longer than a segment

def test_the_writer_takes_events_only_of_the_cameras_that_are_looked_at_and_counts_the_others():
    """A worker that was taken over could put events of any camera on the queue; the writer keeps only those of the list."""
    log, counters = Log(), intake.Counters()
    other = uuid.uuid4()
    entries = [("1-0", ev_fields()), ("2-0", ev_fields(camera_id=str(other))), ("3-0", ev_fields(camera_id=str(other), label="dog"))]
    seen = []

    class Sink(FullSink):
        async def apply_events(self, rows):
            seen.extend(rows)
            return await super().apply_events(rows)

    asyncio.run(intake.write_group(Sink(log), LogQueue(log), counters, {intake.EVENT_STREAM: entries},
                                   event_cameras=lambda: {C1}))
    assert [r.camera_id for r in seen] == [C1]
    assert log.items == ["write events 1", f"confirm {intake.EVENT_STREAM} 3"]       # all three are confirmed
    assert counters.values["event_camera_not_looked_at"] == 2 and counters.values["event_rows_written"] == 1
    # an empty list: nothing is written, everything is confirmed and counted
    log2, counters2 = Log(), intake.Counters()
    asyncio.run(intake.write_group(FullSink(log2), LogQueue(log2), counters2, {intake.EVENT_STREAM: entries},
                                   event_cameras=lambda: set()))
    assert log2.items == ["write events 0", f"confirm {intake.EVENT_STREAM} 3"]
    assert counters2.values["event_camera_not_looked_at"] == 3


def test_the_read_loop_hands_the_list_of_cameras_to_every_group():
    import inspect
    src = inspect.getsource(intake.read_loop)
    assert src.count("event_cameras)") == 2          # the new entries and the entries taken again


def test_the_list_of_cameras_is_read_like_the_motion_workers_and_a_missing_file_means_none(tmp_path):
    f = tmp_path / "cameras.txt"
    assert intake.read_camera_list(f) == set()
    f.write_text(f"# the check camera\n{C1}  # 確かめの映像\nnot-a-uuid\n\n{T1}\n", encoding="utf-8")
    assert intake.read_camera_list(f) == {C1, T1}
    f.write_bytes(b"\xff\xfe\x00")                    # not text: none
    assert intake.read_camera_list(f) == set()


def test_the_writer_takes_no_event_without_the_setting_and_reads_the_file_again_each_time(tmp_path):
    from app.writer import event_cameras_from
    assert event_cameras_from({})() == set()
    f = tmp_path / "cameras.txt"
    get = event_cameras_from({"EVENT_CAMERAS_FILE": str(f)})
    assert get() == set()
    f.write_text(f"{C1}\n", encoding="utf-8")
    assert get() == {C1}                                # added later: counted without a restart


def test_an_event_is_never_longer_than_the_longest_segment():
    assert intake.event_row(ev_fields(kind="motion", label="", score="", duration_ms="120000")).duration_ms == 120000
    with pytest.raises(ValueError):
        intake.event_row(ev_fields(kind="motion", label="", score="", duration_ms="120001"))


def test_the_event_insert_also_needs_the_end_of_the_event_inside_the_segment():
    sql = " ".join(intake.INSERT_EVENTS.split())
    assert ("AND u.started_at + u.duration_ms * interval '1 millisecond' <= s.started_at + "
            "(COALESCE(s.duration_ms, 120000) + 1000) * interval '1 millisecond')") in sql
    assert intake.EVENT_END_SLACK_MS == 1000 and "+ 1000)" in sql


def test_compose_gives_the_first_writer_the_list_of_cameras_read_only_and_the_second_none():
    import pathlib
    import re
    compose = (pathlib.Path(__file__).resolve().parents[2] / "compose.yaml").read_text(encoding="utf-8")
    body = compose.split("\nservices:\n", 1)[1]
    block = lambda n: re.split(rf"^  {re.escape(n)}:\n", body, flags=re.M)[1].split("\n\n", 1)[0]
    w1, w2, motion = block("writer"), block("writer-s2"), block("motion")
    assert "EVENT_CAMERAS_FILE: /run/vision/cameras.txt" in w1 and "- ./var/vision:/run/vision:ro" in w1
    assert "- ./var/vision:/run/vision:ro" in motion and "CAMERAS_FILE: /run/vision/cameras.txt" in motion   # the same file
    assert "EVENT_CAMERAS_FILE" not in w2 and "var/vision" not in w2


def test_each_worker_adds_to_a_dead_stream_of_its_own_and_not_to_the_writers():
    from vision import common as vc
    assert vc.DEAD_STREAMS == {"motion": "ccc:dead:motion", "classify": "ccc:dead:classify"}
    for w, dead in vc.DEAD_STREAMS.items():
        rules = init.WORKER_RULES[w]
        assert f"~{dead} " in rules
        assert "~ccc:dead " not in rules and "~ccc:dead)" not in rules and "~ccc:dead*" not in rules, w
    assert vc.DEAD_STREAMS["motion"] not in init.WORKER_RULES["classify"]
    assert vc.DEAD_STREAMS["classify"] not in init.WORKER_RULES["motion"]
