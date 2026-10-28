"""Fixtures for the integration tests of the workers that look at the recordings. Like the earlier ones they talk to the running
stack through its outside doors (the gateway, Keycloak, the video server) and, where a door is not published (the queue, the
database, the camera's log), through `docker compose exec` / `logs`. They need the profile `vision` to be running, with a GPU:

    docker compose -f compose.yaml -f compose.test.yaml --profile vision up -d
    .venv\\Scripts\\python tools\\setup_check_camera.py        (once; registers the camera of the check video)
    .venv\\Scripts\\python -m pytest -q -m integration tests/integration_v3

The tests of the earlier versions are not touched and are run separately (a `conftest.py` of the same name is in each folder).
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))   # the login walk-through of the earlier tests: oidc_flow.py
import oidc_flow  # noqa: E402


def _dotenv() -> dict:
    out = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


ENV = {**_dotenv(), **os.environ}
BASE = ENV.get("CCC_BASE", "http://localhost:8000")
KC = ENV.get("CCC_KC", "http://localhost:8080")
PROJECT = ENV.get("CCC_COMPOSE_PROJECT", "camera-cloud-core")
TENANT_A = "20000000-0000-4000-8000-00000000000a"
TENANT_B = "20000000-0000-4000-8000-00000000000b"
CAMERA_NAME = "確かめの映像"

# The check video (docs/validation/vision_truth.md): seconds 60 to 120 move, the rest is a held picture. The seconds next to
# the joins (60, 120 and the turn from 180 to 0) are not counted: a GOP of 2 s + 1 s + the 15 frames the worker only learns from.
LAP_SECONDS = 180
END_WIDTH = 4
MOVING = (60, 120)
pytestmark = pytest.mark.integration


def compose(*args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml"), "-f", str(ROOT / "compose.test.yaml"),
           "--profile", "vision", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout, encoding="utf-8")


def psql(sql: str) -> list[list[str]]:
    out = compose("exec", "-T", "postgres", "psql", "-U", "ccc", "-d", "ccc", "-At", "-F", "|", "-c", sql).stdout
    return [line.split("|") for line in out.strip().splitlines() if line]


def in_container(service: str, code: str, timeout: int = 120) -> str:
    return compose("exec", "-T", service, "python", "-c", code, timeout=timeout).stdout.strip()


def writer_counters() -> dict:
    """The counters of stamp 1's writer (its internal door, asked from inside its own container with its own secret)."""
    import json
    code = ("import json, urllib.request; s = open('/run/secret/hook_secret').read().strip(); "
            "r = urllib.request.Request('http://127.0.0.1:8000/internal/v1/counters', headers={'X-Hook-Secret': s}); "
            "print(json.dumps(json.load(urllib.request.urlopen(r, timeout=5))['counters']))")
    return json.loads(in_container("writer", code))


def wait_for(fn, timeout: float, interval: float = 2.0, what: str = "condition"):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}; last value: {last!r}")


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


class Tokens:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, str]] = {}

    def headers(self, user: str) -> dict:
        t, tok = self._cache.get(user, (0.0, ""))
        if time.monotonic() - t > 200:
            tok = oidc_flow.login(user, ENV["DEMO_USER_PASSWORD"], kc=KC)["access_token"]
            self._cache[user] = (time.monotonic(), tok)
        return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="session", autouse=True)
def workers_are_running():
    """The tests need the stack with the profile `vision`; without a GPU the classify worker does not run, and they say why."""
    try:
        assert httpx.get(f"{BASE}/healthz", timeout=3).status_code == 200
    except Exception:  # noqa: BLE001
        pytest.skip("the stack is not running (docker compose -f compose.yaml -f compose.test.yaml --profile vision up -d)")
    running = compose("ps", "--status", "running", "--services", check=False).stdout.split()
    missing = [s for s in ("motion", "classify", "cam-b2") if s not in running]
    if missing:
        why = ("classify needs a GPU and does not start without one" if "classify" in missing else
               "the profile vision is not up")
        pytest.skip(f"not running: {', '.join(missing)} ({why})")


@pytest.fixture(scope="session")
def tokens() -> Tokens:
    return Tokens()


@pytest.fixture(scope="session")
def http() -> httpx.Client:
    with httpx.Client(base_url=BASE, timeout=60, limits=httpx.Limits(keepalive_expiry=2)) as c:
        yield c


@pytest.fixture(scope="session")
def check_camera(http, tokens) -> str:
    cams = http.get(f"/api/v1/tenants/{TENANT_B}/cameras", headers=tokens.headers("b-viewer")).json()
    found = [c["id"] for c in cams if c["name"] == CAMERA_NAME]
    if not found:
        pytest.skip("the camera of the check video is not registered (python tools/setup_check_camera.py)")
    return found[0]


@pytest.fixture(scope="session")
def laps() -> list[datetime]:
    """When each round of the check video began, as the camera printed it in its log (from the output position of ffmpeg)."""
    def read():
        text = compose("logs", "--no-log-prefix", "cam-b2", check=False).stdout
        found = sorted({parse_time(m.group(1)) for m in re.finditer(r"^lap \d+ started_at (\S+)$", text, re.M)})
        return found if len(found) >= 2 else None
    # two rounds in the log: the first is finished, so it can be looked at (a round is 3 minutes; the workers need about a minute more)
    return wait_for(read, 420, 10, "two rounds of the check video in the log of cam-b2")


def clip_second(t: datetime, lap_starts: list[datetime]) -> float | None:
    """The second of the check video that the time t shows, by the round that began last before t (None: before the first)."""
    before = [s for s in lap_starts if s <= t]
    return (t - before[-1]).total_seconds() if before else None
