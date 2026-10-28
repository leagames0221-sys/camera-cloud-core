"""From the recordings of the check camera to the list of events and the playback of one: the whole way through the running
stack (the camera, the video server, the writer's index, the motion worker, the classify worker, the writer's table, the API).

The check video is made so that its true answer is known (docs/validation/vision_truth.md): seconds 60 to 120 move, seconds 0 to 60 and
120 to 180 are a held picture. The camera prints when each round of it began; an event's time minus that gives the second of the
video it shows. The seconds next to the joins are left out of the count (END_WIDTH on each side)."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import (BASE, END_WIDTH, LAP_SECONDS, MOVING, TENANT_A, TENANT_B, clip_second, compose, in_container,
                      parse_time, psql, wait_for, writer_counters)

pytestmark = pytest.mark.integration

MOVING_INNER = (MOVING[0] + END_WIDTH, MOVING[1] - END_WIDTH)                 # 64 to 116
STILL_INNER = [(END_WIDTH, MOVING[0] - END_WIDTH), (MOVING[1] + END_WIDTH, LAP_SECONDS - END_WIDTH)]   # 4 to 56, 124 to 176


def events_url(camera: str, tenant: str = TENANT_B) -> str:
    return f"/api/v1/tenants/{tenant}/cameras/{camera}/events"


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


@pytest.fixture(scope="module")
def complete_laps(laps):
    """The last two rounds that ended more than 100 seconds ago (the workers need about a minute for the last segment)."""
    now = datetime.now(timezone.utc)
    done = [s for s in laps if s + timedelta(seconds=LAP_SECONDS + 100) < now]
    if len(done) < 1:
        pytest.skip("no round of the check video has been finished and worked on yet")
    return done[-2:]


PAGE = 100          # events in an answer unless `limit` says otherwise (docs/adr/0004)


def read_pages(http, tokens, camera, since, user="b-viewer"):
    """Every event from `since`, page by page: 100 in an answer, the next page with `offset`, until a page is not full.
    Each id is taken once (an event written while the pages are read can move one down a page)."""
    out, seen, offset = [], set(), 0
    while True:
        r = http.get(events_url(camera), params={"from": since.isoformat(), "offset": offset}, headers=tokens.headers(user))
        assert r.status_code == 200, r.text
        page = r.json()
        assert len(page) <= PAGE
        out += [e for e in page if e["id"] not in seen]
        seen.update(e["id"] for e in page)
        if len(page) < PAGE:
            return out
        offset += PAGE


@pytest.fixture(scope="module")
def events(http, tokens, check_camera, complete_laps):
    return read_pages(http, tokens, check_camera, complete_laps[0] - timedelta(seconds=2))


def placed(events, laps_in_log, wanted_laps):
    """(event, first second, last second) in the video, for the events that fall in one of the wanted rounds."""
    result = []
    for e in events:
        t = parse_time(e["started_at"])
        s = clip_second(t, laps_in_log)
        lap_start = [x for x in laps_in_log if x <= t][-1] if s is not None else None
        if s is not None and lap_start in wanted_laps:
            result.append((e, s, s + e["duration_ms"] / 1000))
    return result


def test_movement_is_in_the_moving_part_and_not_in_the_held_pictures(events, laps, complete_laps):
    found = placed([e for e in events if e["kind"] == "motion"], laps, complete_laps)
    assert found, "no movement event at all in the finished rounds"
    # (ii) inside the end widths: there is movement; (i) and (iii) inside the end widths: there is none
    for lap in complete_laps:
        mine = [(s, end) for e, s, end in placed([x for x in events if x["kind"] == "motion"], laps, [lap])]
        assert any(overlaps(MOVING_INNER, m) for m in mine), f"round {lap}: no movement in seconds {MOVING_INNER}: {mine}"
    wrong = [(round(s, 1), round(end, 1)) for _, s, end in found if any(overlaps(z, (s, end)) for z in STILL_INNER)]
    assert wrong == [], f"movement events in the held pictures {STILL_INNER}: {wrong}"


def test_named_things_come_only_from_the_moving_part_with_a_name_and_a_score(events, laps, complete_laps):
    found = placed([e for e in events if e["kind"] == "object"], laps, complete_laps)
    assert found, "no named thing at all in the finished rounds"
    for e, s, _ in found:
        assert e["label"] and 0.9 < e["score"] <= 1.0 and e["duration_ms"] == 0
        assert e["model"].startswith("facebook/detr-resnet-50@70120ba")
        assert not any(z[0] <= s < z[1] for z in STILL_INNER), f"{e['label']} at second {s:.1f} of the held pictures"
    assert any(MOVING_INNER[0] <= s < MOVING_INNER[1] for _, s, _ in found)


def test_the_list_is_newest_first_and_says_which_events_have_their_video(events):
    times = [e["started_at"] for e in events]
    assert times == sorted(times, reverse=True)
    assert all(set(e) == {"id", "kind", "label", "score", "model", "started_at", "duration_ms", "playable"} for e in events)
    assert all(e["playable"] for e in events)           # the recordings are from the last minutes


def test_an_event_is_played_from_its_time(http, tokens, check_camera, events):
    e = next(x for x in events if x["kind"] == "motion")
    h = tokens.headers("b-viewer")
    r = http.post(f"{events_url(check_camera)}/{e['id']}/playback", headers=h)
    assert r.status_code == 200, r.text
    p = r.json()
    start = parse_time(e["started_at"])
    assert parse_time(p["start"]) >= start - timedelta(seconds=5) - timedelta(seconds=1)    # not before the event minus 5 seconds
    assert parse_time(p["start"]) <= start
    assert p["duration_seconds"] <= e["duration_ms"] / 1000 + 10 + 0.1
    video = httpx.get(p["url"], headers={"Authorization": f"Bearer {p['token']}"}, timeout=60)
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4" and len(video.content) > 10_000
    # an object event (no length) is played too
    o = next(x for x in events if x["kind"] == "object")
    assert http.post(f"{events_url(check_camera)}/{o['id']}/playback", headers=h).status_code == 200
    # an event that does not exist, and the token alone does not open another camera's video
    assert http.post(f"{events_url(check_camera)}/{uuid.uuid4()}/playback", headers=h).status_code == 404
    assert http.post(f"{events_url(check_camera)}/{e['id']}/playback").status_code == 401


def test_other_companies_and_the_partner_administrator_get_nothing(http, tokens, check_camera, events):
    e = events[0]
    for user, status in (("a-viewer", 404), ("a-admin", 404), ("c-viewer", 404), ("partner-admin", 403)):
        h = tokens.headers(user)
        assert http.get(events_url(check_camera), headers=h).status_code == status, user
        assert http.post(f"{events_url(check_camera)}/{e['id']}/playback", headers=h).status_code == status, user
    # company A asking for B's camera under A's own address is not found either
    assert http.get(events_url(check_camera, TENANT_A), headers=tokens.headers("a-viewer")).status_code == 404
    # the company's own administrator sees them (the same right as the viewer)
    assert http.get(events_url(check_camera), headers=tokens.headers("b-admin")).status_code == 200
    # there is no limit of time and a wrong range is refused
    h = tokens.headers("b-viewer")
    assert http.get(events_url(check_camera), params={"from": "2020-01-01T00:00:00Z"}, headers=h).status_code == 200
    now = datetime.now(timezone.utc)
    assert http.get(events_url(check_camera), params={"from": now.isoformat(), "to": (now - timedelta(hours=1)).isoformat()},
                    headers=h).status_code == 422


def test_the_list_comes_in_pages_of_100_and_the_pages_do_not_overlap(http, tokens, check_camera, events, complete_laps):
    since = complete_laps[0] - timedelta(seconds=2)
    h = tokens.headers("b-viewer")
    first = http.get(events_url(check_camera), params={"from": since.isoformat()}, headers=h).json()
    assert min(PAGE, len(events)) <= len(first) <= PAGE            # (events may have been written since `events` was read)
    if len(events) <= PAGE:
        pytest.skip(f"{len(events)} events only: no second page to compare")
    second = http.get(events_url(check_camera), params={"from": since.isoformat(), "offset": PAGE}, headers=h).json()
    assert second and not ({e["id"] for e in first} & {e["id"] for e in second})
    assert [e["started_at"] for e in first + second] == sorted((e["started_at"] for e in first + second), reverse=True)
    small = http.get(events_url(check_camera), params={"from": since.isoformat(), "limit": 7}, headers=h).json()
    assert [e["id"] for e in small] == [e["id"] for e in first[:7]]
    for bad in ({"limit": 0}, {"limit": 1001}, {"offset": -1}):
        assert http.get(events_url(check_camera), params=bad, headers=h).status_code == 422, bad


def test_the_cameras_of_the_test_pattern_have_no_events(http, tokens, check_camera):
    rows = psql(f"SELECT camera_id, count(*) FROM events WHERE camera_id <> '{check_camera}' GROUP BY 1")
    assert rows == [], f"events of other cameras: {rows}"
    for tenant, user in ((TENANT_A, "a-viewer"), (TENANT_B, "b-viewer")):
        h = tokens.headers(user)
        for cam in http.get(f"/api/v1/tenants/{tenant}/cameras", headers=h).json():
            if cam["id"] != check_camera:
                assert http.get(events_url(cam["id"], tenant), headers=h).json() == [], cam["name"]


def test_the_writer_keeps_one_row_for_an_event_sent_twice_and_refuses_one_without_a_recording(check_camera):
    """Events are put on the queue by the workers only, so this test speaks to the queue as the writer's account does (from the
    writer's own container) and checks what lands in the table."""
    seg = psql(f"SELECT to_char(started_at AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00', "
               f"COALESCE(duration_ms, 0) FROM recording_segments WHERE camera_id = '{check_camera}' AND duration_ms > 20000 "
               f"AND NOT deleted ORDER BY started_at DESC LIMIT 1")
    assert seg, "the check camera has no segment in the index"
    started = parse_time(seg[0][0])
    inside = (started + timedelta(seconds=10)).isoformat()
    label = f"integration-test-{uuid.uuid4().hex[:8]}"
    nocam = uuid.uuid4()
    before = writer_counters()
    code = f"""
import json, valkey
r = valkey.Valkey(host="valkey", username="writer", password=open("/run/secret/valkey_password").read().strip(), decode_responses=True)
base = dict(tenant_id="{TENANT_B}", camera_id="{check_camera}", kind="object", label="{label}", score="0.95", duration_ms="0", model="test")
r.xadd("ccc:events", dict(base, started_at="{inside}"))
r.xadd("ccc:events", dict(base, started_at="{inside}"))                              # the same event again
r.xadd("ccc:events", dict(base, label="{label}-old", started_at="2020-01-01T00:00:00+00:00"))   # no recording of that time
r.xadd("ccc:events", dict(base, label="{label}-nocam", camera_id="{nocam}", started_at="{inside}"))   # a camera that does not exist
r.xadd("ccc:events", dict(base, label="{label}-other", tenant_id="{TENANT_A}", started_at="{inside}"))  # the camera is not company A's
print("sent")
"""
    assert in_container("writer", code) == "sent"
    rows = wait_for(lambda: psql(f"SELECT label, count(*) FROM events WHERE label LIKE '{label}%' GROUP BY 1"), 30, 1,
                    "the test events in the table")
    # give the writer time to have taken the entries that must not be written
    import time
    time.sleep(3)
    rows = psql(f"SELECT label, count(*) FROM events WHERE label LIKE '{label}%' GROUP BY 1")
    after = writer_counters()
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in ("event_unreadable", "event_rejected", "event_camera_not_looked_at")}
    try:
        assert rows == [[label, "1"]], rows
        # all were readable; the old time and company A's tenant were refused by the table's checks (the camera that does not
        # exist is not in the list of cameras that are looked at, so it is left out before the table)
        assert delta == {"event_unreadable": 0, "event_rejected": 2, "event_camera_not_looked_at": 1}, delta
    finally:
        psql(f"DELETE FROM events WHERE label LIKE '{label}%' RETURNING 1")
