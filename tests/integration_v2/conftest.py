"""Fixtures for the integration tests of the later version (second stamp, queue, limit, device registration). Like the
earlier ones they talk to the running stack only through its outside doors (the gateway, Keycloak, the video servers,
the brokers) and, where a door is not published (the writer's queue, a stamp's database), through `docker compose exec`
in the container that owns it. Start the stack with the test file:
    docker compose -f compose.yaml -f compose.test.yaml up -d
The tests of the first version (tests/integration) are not touched and are run separately.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

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
HLS1, HLS2 = "http://localhost:8888", "http://localhost:8889"
PLAYBACK1, PLAYBACK2 = "http://localhost:9996", "http://localhost:9998"
MQTT1, MQTT2 = ("localhost", 1883), ("localhost", 1884)
RTSP1, RTSP2 = ("localhost", 8554), ("localhost", 8555)
PROJECT = ENV.get("CCC_COMPOSE_PROJECT", "camera-cloud-core")
PAGE_ORIGIN = "http://localhost:8000"

TENANT_A = "20000000-0000-4000-8000-00000000000a"
TENANT_B = "20000000-0000-4000-8000-00000000000b"
TENANT_C = "20000000-0000-4000-8000-00000000000c"
CAM_A1 = "30000000-0000-4000-8000-0000000000a1"
CAM_C1 = "30000000-0000-4000-8000-0000000000c1"

pytestmark = pytest.mark.integration


def compose(*args: str, check: bool = True, timeout: int = 180, input: str | None = None):
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml"), "-f", str(ROOT / "compose.test.yaml"),
           *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout, input=input,
                          encoding="utf-8")


def in_container(service: str, code: str, *, check: bool = True, timeout: int = 120) -> str:
    """Run a few lines of Python inside a service's container and return what it printed."""
    return compose("exec", "-T", service, "python", "-c", code, check=check, timeout=timeout).stdout.strip()


def psql(service: str, sql: str) -> list[list[str]]:
    out = compose("exec", "-T", service, "psql", "-U", "ccc", "-d", "ccc", "-At", "-F", "|", "-c", sql).stdout
    return [line.split("|") for line in out.strip().splitlines() if line]


def wait_for(fn, timeout: float, interval: float = 0.5, what: str = "condition"):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}; last value: {last!r}")


class Tokens:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, str]] = {}

    def get(self, user: str) -> str:
        t, tok = self._cache.get(user, (0.0, ""))
        if time.monotonic() - t > 200:
            tok = oidc_flow.login(user, ENV["DEMO_USER_PASSWORD"], kc=KC)["access_token"]
            self._cache[user] = (time.monotonic(), tok)
        return tok

    def headers(self, user: str) -> dict:
        return {"Authorization": f"Bearer {self.get(user)}"}


@pytest.fixture(scope="session", autouse=True)
def stack_is_up():
    try:
        assert httpx.get(f"{BASE}/healthz", timeout=3).status_code == 200
        assert httpx.get(f"{KC}/realms/ccc/.well-known/openid-configuration", timeout=3).status_code == 200
        assert httpx.get(f"{HLS2}/", timeout=3).status_code in (200, 401, 404)
    except Exception:  # noqa: BLE001
        pytest.skip("the stack is not running (docker compose -f compose.yaml -f compose.test.yaml up -d first)")


@pytest.fixture(scope="session")
def tokens() -> Tokens:
    return Tokens()


@pytest.fixture(scope="session")
def http() -> httpx.Client:
    # idle connections are dropped after 2 s, before the server's own 5-second limit, so a test that waits does not
    # send its next call on a connection the server has just closed
    with httpx.Client(base_url=BASE, timeout=30, limits=httpx.Limits(keepalive_expiry=2)) as c:
        yield c


@pytest.fixture
def new_camera(http, tokens):
    """Registers cameras in company C (stamp 2) as c-admin and removes them at the end of the test."""
    made: list[dict] = []

    def make(name: str = "test camera", tenant: str = TENANT_C, user: str = "c-admin") -> dict:
        r = http.post(f"/api/v1/tenants/{tenant}/cameras", json={"name": name}, headers=tokens.headers(user))
        assert r.status_code == 201, r.text
        made.append({**r.json(), "_tenant": tenant, "_user": user})
        return made[-1]

    yield make
    for c in made:
        http.delete(f"/api/v1/tenants/{c['_tenant']}/cameras/{c['id']}", headers=tokens.headers(c["_user"]))


def camera_view(http, tokens, tenant: str, camera: str, user: str = "c-admin") -> dict:
    r = http.get(f"/api/v1/tenants/{tenant}/cameras/{camera}", headers=tokens.headers(user))
    assert r.status_code == 200, r.text
    return r.json()


def segment_starts(http, tokens, tenant: str, camera: str, minutes: int = 60 * 24 * 400, user: str = "c-admin"):
    r = http.get(f"/api/v1/tenants/{tenant}/cameras/{camera}/segments?minutes=1440", headers=tokens.headers(user))
    assert r.status_code == 200, r.text
    return [s["started_at"] for s in r.json()]


def hook_form(tenant: str, camera: str, started: str, duration: str = "60.0") -> str:
    """The body MediaMTX's hook sends; `started` like 2026-01-02_03-04-05-000000."""
    path = f"{tenant}/{camera}"
    return f"path={path}&file=recordings/{path}/{started}.mp4&duration={duration}"


def post_segment(writer: str, body: str, secret_ok: bool = True) -> int:
    """POST a closed-segment call to a writer's internal door (from inside the writer's container)."""
    code = (
        "import urllib.request,urllib.error,sys\n"
        "secret = open('/run/secret/hook_secret').read().strip() if %r else 'wrong'\n"
        "req = urllib.request.Request('http://127.0.0.1:8000/internal/v1/segments', data=%r.encode(), method='POST',"
        " headers={'X-Hook-Secret': secret, 'Content-Type': 'application/x-www-form-urlencoded'})\n"
        "try:\n"
        "    print(urllib.request.urlopen(req, timeout=10).status)\n"
        "except urllib.error.HTTPError as e:\n"
        "    print(e.code)\n" % (secret_ok, body))
    return int(in_container(writer, code))


def writer_counters(writer: str) -> dict:
    """The writer's counters, asked from inside its container with the stamp's secret (the door asks for it)."""
    code = ("import urllib.request\n"
            "req = urllib.request.Request('http://127.0.0.1:8000/internal/v1/counters', "
            "headers={'X-Hook-Secret': open('/run/secret/hook_secret').read().strip()})\n"
            "print(urllib.request.urlopen(req, timeout=10).read().decode())\n")
    return json.loads(in_container(writer, code))


def valkey(writer: str, body: str, *, check: bool = True) -> str:
    """Run Python inside a writer's container with `r` = a connected Valkey client (the writer's own account)."""
    pre = (
        "import json, os, pathlib, valkey\n"
        "r = valkey.Valkey(host=os.environ['VALKEY_HOST'], username='writer', "
        "password=pathlib.Path('/run/secret/valkey_password').read_text().strip(), decode_responses=True)\n")
    return in_container(writer, pre + body, check=check)


def unique_start() -> str:
    """A start time in 2020 that no other test uses: the file-name form of MediaMTX (UTC)."""
    n = uuid.uuid4().int
    return f"2020-{1 + n % 12:02d}-{1 + (n >> 8) % 28:02d}_{(n >> 16) % 24:02d}-{(n >> 24) % 60:02d}-{(n >> 32) % 60:02d}-{(n >> 40) % 1000000:06d}"


def iso_of(started: str) -> str:
    d, t = started.split("_")
    h, m, s, us = t.split("-")
    return f"{d}T{h}:{m}:{s}.{us}Z"
