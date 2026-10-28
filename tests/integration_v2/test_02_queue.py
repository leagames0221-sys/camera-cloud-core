"""The queue and the writer, against the real Valkey, PostgreSQL and brokers of both stamps."""
import json
import time

import pytest

from conftest import (CAM_A1, CAM_C1, TENANT_A, TENANT_C, camera_view, compose, hook_form, in_container, post_segment,
                      psql, unique_start, valkey, wait_for, writer_counters)

pytestmark = pytest.mark.integration


def count_segments(db: str, camera: str, started: str | None = None) -> int:
    where = f"camera_id = '{camera}'" + (f" AND started_at = '{started}'" if started else "")
    return int(psql(db, f"SELECT count(*) FROM recording_segments WHERE {where}")[0][0])


def iso(started: str) -> str:
    d, t = started.split("_")
    h, m, s, us = t.split("-")
    return f"{d} {h}:{m}:{s}.{us}+00"


def test_the_same_segment_call_twice_gives_one_row(new_camera):
    cam = new_camera("twice")
    started = unique_start()
    body = hook_form(TENANT_C, cam["id"], started)
    assert post_segment("writer-s2", body) == 204
    assert post_segment("writer-s2", body) == 204
    wait_for(lambda: count_segments("postgres-s2", cam["id"]) >= 1, 15, what="the segment row")
    time.sleep(3)                       # a duplicate would have been written by now as well
    assert count_segments("postgres-s2", cam["id"], iso(started)) == 1
    assert count_segments("postgres-s2", cam["id"]) == 1


def test_a_batch_with_the_same_camera_twice_does_not_fail_and_the_newer_state_stays(http, tokens, new_camera):
    cam = new_camera("two states")
    before = writer_counters("writer-s2")["counters"]
    # both entries are put on the queue in one go, so they are taken in the same group
    valkey("writer-s2", (
        "for fps in ('111', '222'):\n"
        f"    r.xadd('ccc:state', {{'camera_id': '{cam['id']}', 'state': 'online', 'reason': '', 'fps': fps, "
        "'bitrate': '1000'})\n"))
    wait_for(lambda: camera_view(http, tokens, TENANT_C, cam["id"])["fps"] == 222.0, 15, what="the newer state")
    after = writer_counters("writer-s2")["counters"]
    assert after.get("errors", 0) == before.get("errors", 0)         # the group was written without an error
    assert camera_view(http, tokens, TENANT_C, cam["id"])["fps"] == 222.0


def test_an_older_state_does_not_overwrite_a_newer_one(http, tokens, new_camera):
    """The database row can be newer than an entry that arrives (for example written by another writer); the upsert only
    accepts newer data. The row is made newer by hand here."""
    cam = new_camera("order")
    valkey("writer-s2", f"r.xadd('ccc:state', {{'camera_id': '{cam['id']}', 'state': 'online', 'fps': '300', "
                        "'bitrate': '', 'reason': ''})")
    wait_for(lambda: camera_view(http, tokens, TENANT_C, cam["id"])["fps"] == 300.0, 15, what="first state")
    psql("postgres-s2", f"UPDATE camera_status SET seen_at = now() + interval '1 hour', fps = 999 "
                        f"WHERE camera_id = '{cam['id']}'")
    valkey("writer-s2", f"r.xadd('ccc:state', {{'camera_id': '{cam['id']}', 'state': 'online', 'fps': '301', "
                        "'bitrate': '', 'reason': ''})")
    time.sleep(4)
    assert camera_view(http, tokens, TENANT_C, cam["id"])["fps"] == 999.0


def test_entries_left_in_the_queue_by_a_stopped_writer_are_written_after_it_starts_again(http, tokens, new_camera):
    cam = new_camera("left in the queue")
    compose("stop", "-t", "10", "writer-s2")
    # the queue keeps running in its own container; a short-lived container of the writer's image, on the same network and
    # with the same account, puts the entry on it while the writer is stopped
    code = (
        "import os, pathlib, valkey\n"
        "r = valkey.Valkey(host='valkey-s2', username='writer', "
        "password=pathlib.Path('/run/secret/valkey_password').read_text().strip(), decode_responses=True)\n"
        f"r.xadd('ccc:state', {{'camera_id': '{cam['id']}', 'state': 'online', 'fps': '444', 'bitrate': '', 'reason': ''}})\n"
        "print(r.xlen('ccc:state'))\n")
    try:
        out = compose("run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "writer-s2", "-c", code).stdout.strip()
        assert out.isdigit() and int(out) >= 1
    finally:
        compose("start", "writer-s2")
    wait_for(lambda: camera_view(http, tokens, TENANT_C, cam["id"])["fps"] == 444.0, 30, what="the state left on the queue")


def test_an_entry_handed_out_and_never_confirmed_is_taken_again_after_30_seconds(http, tokens, new_camera):
    cam = new_camera("unconfirmed")
    compose("stop", "-t", "10", "writer-s2")
    code = (
        "import pathlib, valkey\n"
        "r = valkey.Valkey(host='valkey-s2', username='writer', "
        "password=pathlib.Path('/run/secret/valkey_password').read_text().strip(), decode_responses=True)\n"
        f"r.xadd('ccc:state', {{'camera_id': '{cam['id']}', 'state': 'online', 'fps': '555', 'bitrate': '', 'reason': ''}})\n"
        "got = r.xreadgroup('writers', 'ghost', {'ccc:state': '>'}, count=100)\n"        # handed out, never confirmed
        "print(sum(len(e) for _, e in got))\n")
    try:
        out = compose("run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "writer-s2", "-c", code).stdout.strip()
        assert out.isdigit() and int(out) >= 1
    finally:
        compose("start", "writer-s2")
    time.sleep(10)
    assert camera_view(http, tokens, TENANT_C, cam["id"])["fps"] != 555.0      # not yet: it is owned by `ghost`
    wait_for(lambda: camera_view(http, tokens, TENANT_C, cam["id"])["fps"] == 555.0, 90,
             what="the unconfirmed entry to be taken again")
    assert writer_counters("writer-s2")["counters"].get("reclaimed", 0) >= 1


def test_an_entry_that_cannot_be_written_goes_to_the_dead_stream_at_the_sixth_handing_out():
    """Against the real Valkey, with the real Queue code, on a stream of its own (the live writer does not read it) and
    with the 30 seconds shortened to a fraction of a second."""
    out = valkey("writer", (
        "import asyncio, json, time, uuid\n"
        "import valkey.asyncio as av\n"
        "from app import intake\n"
        "intake.CLAIM_IDLE_MS = 50\n"
        "async def main():\n"
        "    c = av.Valkey(host=os.environ['VALKEY_HOST'], username='writer', password=pathlib.Path('/run/secret/valkey_password')"
        ".read_text().strip(), decode_responses=True)\n"
        "    q = intake.Queue(c, 'dead-test')\n"
        "    S = 'ccc:test-poison-' + uuid.uuid4().hex[:8]\n"
        "    try: await c.xgroup_create(S, intake.GROUP, id='0', mkstream=True)\n"
        "    except Exception: pass\n"
        "    await c.xadd(S, {'x': 'poison'})\n"
        "    await c.xreadgroup(intake.GROUP, 'dead-test', {S: '>'}, count=10)\n"       # handing out no. 1
        "    seen = []\n"
        "    for n in range(2, 8):\n"
        "        await asyncio.sleep(0.2)\n"
        "        again, dead = await q.reclaim(S)\n"
        "        seen.append([n, len(again), len(dead)])\n"
        "        if dead:\n"
        "            await q.bury(S, dead)\n"
        "            break\n"
        "    pend = await c.xpending(S, intake.GROUP)\n"
        "    dead_entries = [e for e in await c.xrange(intake.DEAD_STREAM) if e[1].get('stream') == S]\n"
        "    for eid, _ in dead_entries: await c.xdel(intake.DEAD_STREAM, eid)\n"
        "    await c.xgroup_destroy(S, intake.GROUP)\n"
        "    await c.xtrim(S, maxlen=0)\n"
        "    print(json.dumps({'seen': seen, 'pending': pend['pending'], 'dead': len(dead_entries)}))\n"
        "asyncio.run(main())\n"))
    res = json.loads(out)
    # handings out 2 to 5 give the entry back to be tried again; the 6th ends it in the dead stream
    assert res["seen"] == [[2, 1, 0], [3, 1, 0], [4, 1, 0], [5, 1, 0], [6, 0, 1]], res
    assert res["pending"] == 0 and res["dead"] == 1


def test_segments_of_a_camera_of_another_stamp_or_of_an_unknown_camera_are_not_indexed(new_camera):
    cam = new_camera("elsewhere")
    outside_1 = writer_counters("writer")["counters"].get("segment_not_this_stamp", 0)
    outside_2 = writer_counters("writer-s2")["counters"].get("segment_not_this_stamp", 0)
    s1, s2, s3 = unique_start(), unique_start(), unique_start()
    # a camera of company A (first stamp) sent to the second stamp's writer, and a camera of C sent to the first stamp's
    assert post_segment("writer-s2", hook_form(TENANT_A, CAM_A1, s1)) == 204
    assert post_segment("writer", hook_form(TENANT_C, cam["id"], s2)) == 204
    # a camera that does not exist at all
    ghost = "30000000-0000-4000-8000-0000000000ee"
    assert post_segment("writer", hook_form(TENANT_A, ghost, s3)) == 204
    # a good one, to know the group was written
    s4 = unique_start()
    assert post_segment("writer-s2", hook_form(TENANT_C, cam["id"], s4)) == 204
    wait_for(lambda: count_segments("postgres-s2", cam["id"], iso(s4)) == 1, 15, what="the good segment")
    time.sleep(2)
    assert count_segments("postgres-s2", CAM_A1, iso(s1)) == 0
    assert count_segments("postgres", cam["id"], iso(s2)) == 0
    assert count_segments("postgres", ghost) == 0
    # and what was left out is counted: C's camera and the unknown one at the first writer, A's camera at the second
    wait_for(lambda: writer_counters("writer")["counters"].get("segment_not_this_stamp", 0) >= outside_1 + 2, 10,
             what="the first writer to count the two segments it did not index")
    wait_for(lambda: writer_counters("writer-s2")["counters"].get("segment_not_this_stamp", 0) >= outside_2 + 1, 10,
             what="the second writer to count the segment it did not index")


def test_the_apis_own_segment_door_does_not_index_a_camera_of_the_second_stamp(new_camera):
    """The API keeps the first version's door for closed segments. It checks the first stamp's secret, so it must not
    write into the second stamp's index. Called from the first writer's container, which holds that secret."""
    cam = new_camera("api door")
    body = hook_form(TENANT_C, cam["id"], unique_start())
    code = (
        "import urllib.request, urllib.error\n"
        "req = urllib.request.Request('http://api:8000/internal/v1/segments', data=%r.encode(), method='POST', headers={"
        "'X-Hook-Secret': open('/run/secret/hook_secret').read().strip(), "
        "'Content-Type': 'application/x-www-form-urlencoded'})\n"
        "try:\n"
        "    print(urllib.request.urlopen(req, timeout=10).status)\n"
        "except urllib.error.HTTPError as e:\n"
        "    print(e.code)\n" % body)
    assert in_container("writer", code) == "404"
    time.sleep(1)
    assert count_segments("postgres-s2", cam["id"]) == 0


def test_the_door_refuses_wrong_secret_and_bad_lengths_and_counts_them(new_camera):
    cam = new_camera("door")
    before = writer_counters("writer-s2")["counters"].get("segment_rejected", 0)
    assert post_segment("writer-s2", hook_form(TENANT_C, cam["id"], unique_start()), secret_ok=False) == 403
    assert post_segment("writer-s2", hook_form(TENANT_C, cam["id"], unique_start(), "0")) == 400
    assert post_segment("writer-s2", hook_form(TENANT_C, cam["id"], unique_start(), "121")) == 400
    assert post_segment("writer-s2", hook_form(TENANT_C, cam["id"], unique_start(), "120")) == 204
    assert writer_counters("writer-s2")["counters"].get("segment_rejected", 0) == before + 2


def test_state_over_mqtt_reaches_the_api_through_the_queue_on_both_stamps(http, tokens):
    for tenant, camera, user in ((TENANT_C, CAM_C1, "c-viewer"), (TENANT_A, CAM_A1, "a-viewer")):
        v = camera_view(http, tokens, tenant, camera, user)
        assert v["state"] in ("recording", "standby") and v["last_seen"]
    writer = "writer-s2"
    assert writer_counters(writer)["counters"].get("states_received", 0) > 0
    assert writer_counters("writer")["counters"].get("states_received", 0) > 0
