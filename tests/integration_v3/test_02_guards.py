"""What a worker that was taken over still cannot do, checked against the running writer, and the port of MediaMTX's Control API
looked at more closely than the earlier test does.

The events are put on the queue from the writer's own container with the writer's account (the workers' accounts may add to
`ccc:events` as well; what matters is what the writer takes from it)."""
import json
import re
import socket
import time
import uuid
from datetime import timedelta

import pytest

from conftest import TENANT_A, TENANT_B, compose, in_container, parse_time, psql, wait_for, writer_counters

pytestmark = pytest.mark.integration

CAM_A1 = "30000000-0000-4000-8000-0000000000a1"    # a seeded camera of company A with the test pattern: not looked at


def latest_segment(camera: str):
    rows = psql(f"SELECT to_char(started_at AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00', duration_ms "
                f"FROM recording_segments WHERE camera_id = '{camera}' AND duration_ms > 20000 AND NOT deleted "
                f"ORDER BY started_at DESC LIMIT 1")
    assert rows, f"camera {camera} has no segment in the index"
    return parse_time(rows[0][0]), int(rows[0][1])


def send_events(events: list[dict]) -> None:
    code = ("import json, valkey\n"
            "r = valkey.Valkey(host='valkey', username='writer', password=open('/run/secret/valkey_password').read().strip(), "
            "decode_responses=True)\n"
            f"for e in json.loads({json.dumps(json.dumps(events))}):\n"
            "    r.xadd('ccc:events', e)\n"
            "print('sent')\n")
    assert in_container("writer", code) == "sent"


def counted(before: dict, keys: tuple[str, ...], wanted: dict, timeout: float = 30) -> dict:
    def delta():
        after = writer_counters()
        d = {k: after.get(k, 0) - before.get(k, 0) for k in keys}
        return d if all(d[k] >= v for k, v in wanted.items()) else None
    return wait_for(delta, timeout, 1, f"the writer to count {wanted}")


def test_an_event_of_a_camera_that_is_not_looked_at_is_not_written_even_at_a_recorded_time():
    """Camera a1 of company A is recording, so its time is in the index; it is not in the list of cameras that are looked at."""
    start, _ = latest_segment(CAM_A1)
    label = f"guard-{uuid.uuid4().hex[:8]}"
    base = {"tenant_id": TENANT_A, "camera_id": CAM_A1, "kind": "object", "label": label, "score": "0.95",
            "started_at": (start + timedelta(seconds=10)).isoformat(), "duration_ms": "0", "model": "test"}
    before = writer_counters()
    try:
        send_events([base, dict(base, kind="motion", label="", score="", duration_ms="5000")])
        d = counted(before, ("event_camera_not_looked_at", "event_rows_written"), {"event_camera_not_looked_at": 2})
        assert d["event_camera_not_looked_at"] == 2, d
        assert psql(f"SELECT count(*) FROM events WHERE camera_id = '{CAM_A1}'") == [["0"]]
    finally:
        psql(f"DELETE FROM events WHERE camera_id = '{CAM_A1}' AND model = 'test' RETURNING 1")


def test_an_event_that_runs_past_its_segment_is_not_written(check_camera):
    start, length_ms = latest_segment(check_camera)
    label = f"guard-{uuid.uuid4().hex[:8]}"
    at = start + timedelta(seconds=10)
    room = length_ms - 10_000                                  # from the event's start to the end of its segment, in ms
    base = {"tenant_id": TENANT_B, "camera_id": check_camera, "kind": "object", "score": "0.95", "model": "test",
            "started_at": at.isoformat()}
    send_events([dict(base, label=f"{label}-fits", duration_ms=str(room)),                 # ends where the segment ends
                 dict(base, label=f"{label}-over", duration_ms=str(room + 5_000))])        # 5 seconds past it
    rows = wait_for(lambda: psql(f"SELECT label FROM events WHERE label LIKE '{label}%' ORDER BY 1"), 30, 1,
                    "the event that fits in the table")
    time.sleep(3)
    rows = psql(f"SELECT label FROM events WHERE label LIKE '{label}%' ORDER BY 1")
    try:
        assert rows == [[f"{label}-fits"]], rows
    finally:
        psql(f"DELETE FROM events WHERE label LIKE '{label}%' RETURNING 1")


def test_the_control_api_port_is_published_by_no_container():
    """The earlier test (tests/integration/test_03_isolation.py) looks for the text "9997" anywhere in `docker compose ps`; that
    output also holds container ids and the hashes of images and configurations, which are hexadecimal and can hold those digits by
    chance. This one looks at the published ports themselves, and says where else the digits are, so that a red earlier test can
    be told apart from a published port."""
    with pytest.raises(OSError):
        socket.create_connection(("localhost", 9997), timeout=2).close()
    out = compose("ps", "--format", "json").stdout
    services = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert services
    for s in services:
        for p in s.get("Publishers") or []:
            assert p.get("PublishedPort") != 9997, (s["Service"], p)
            assert not (p.get("TargetPort") == 9997 and p.get("PublishedPort")), (s["Service"], p)
        assert not re.search(r"(?<![0-9a-f])9997(?![0-9a-f])", s.get("Ports", "")), (s["Service"], s.get("Ports"))
    # every "9997" left in the output is inside a hexadecimal id or hash of 12 or more characters
    outside = [m.start() for m in re.finditer("9997", out)
               if not any(h.start() <= m.start() < h.end() for h in re.finditer(r"[0-9a-f]{12,}", out))]
    assert outside == [], [out[max(0, i - 60):i + 20] for i in outside]
