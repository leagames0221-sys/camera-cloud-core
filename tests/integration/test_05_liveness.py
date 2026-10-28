"""State changes seen through the API: docker kill (the Will), docker stop (a normal goodbye), docker pause
(Keep Alive 60 s x 1.5 = 90 s). Times are measured on the host with a monotonic clock, polling the API."""
import time

import pytest

from conftest import compose, camera_state, wait_for
from ids import CAM_A2, CAM_B1, TENANT_A, TENANT_B

pytestmark = pytest.mark.integration

KEEPALIVE_SECONDS = 60
DETECTION_LIMIT = KEEPALIVE_SECONDS * 1.5 + 5       # 90 s of silence, and at most 5 s of slack
KILL_LIMIT = 5.0


def state(http, tokens, user, tenant, cam):
    return camera_state(http, tokens, user, tenant, cam)["state"]


def wait_recording(http, tokens, user, tenant, cam):
    wait_for(lambda: state(http, tokens, user, tenant, cam) == "recording", 180, what=f"camera {cam} recording")


def seconds_until(http, tokens, user, tenant, cam, wanted, limit):
    """Poll twice a second; return how long it took for the state to become `wanted` (fails after `limit`)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if state(http, tokens, user, tenant, cam) == wanted:
            return time.monotonic() - t0
        time.sleep(0.5)
    pytest.fail(f"camera {cam} did not become {wanted} within {limit}s")


def test_kill_is_seen_through_the_will_at_once(http, tokens):
    wait_recording(http, tokens, "a-viewer", TENANT_A, CAM_A2)
    compose("kill", "cam-a2")
    took = seconds_until(http, tokens, "a-viewer", TENANT_A, CAM_A2, "disconnected", KILL_LIMIT + 10)
    print(f"docker kill: disconnected after {took:.1f}s")
    assert took <= KILL_LIMIT + 2     # the broker sees the closed socket and publishes the Will; polling adds a little
    cam = camera_state(http, tokens, "a-viewer", TENANT_A, CAM_A2)
    assert cam["reason"] == "will"


def test_stop_says_goodbye_with_reason_normal(http, tokens):
    compose("start", "cam-a2")
    wait_recording(http, tokens, "a-viewer", TENANT_A, CAM_A2)
    compose("stop", "-t", "10", "cam-a2", timeout=60)
    took = seconds_until(http, tokens, "a-viewer", TENANT_A, CAM_A2, "disconnected", 15)
    print(f"docker stop: disconnected after {took:.1f}s")
    cam = camera_state(http, tokens, "a-viewer", TENANT_A, CAM_A2)
    assert cam["reason"] == "normal"        # not "will": a clean DISCONNECT discards the Will, so the camera said it itself


def test_pause_is_detected_within_keep_alive_times_one_and_a_half(http, tokens):
    wait_recording(http, tokens, "b-viewer", TENANT_B, CAM_B1)
    compose("pause", "cam-b1")
    try:
        took = seconds_until(http, tokens, "b-viewer", TENANT_B, CAM_B1, "disconnected", DETECTION_LIMIT + 5)
        print(f"docker pause: disconnected after {took:.1f}s (limit {DETECTION_LIMIT:.0f}s)")
        assert took <= DETECTION_LIMIT
    finally:
        compose("unpause", "cam-b1")
    # it comes back by itself after the pause
    wait_for(lambda: state(http, tokens, "b-viewer", TENANT_B, CAM_B1) in ("standby", "recording"), 60,
             what="cam-b1 to report again")


def test_leave_the_stack_as_it_was():
    compose("start", "cam-a2")
