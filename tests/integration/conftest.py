"""Fixtures for the integration tests. They need the stack to be running (see the README) and talk only to its outside
doors: the gateway, Keycloak, MediaMTX's HLS and playback ports, and (with compose.test.yaml) MQTT and RTSP.

Settings come from the environment or from .env in the repository root."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import subprocess
import time

import httpx
import pytest

import oidc_flow
from ids import USER_SUB

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _dotenv() -> dict:
    p = ROOT / ".env"
    out = {}
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
HLS = ENV.get("CCC_HLS", "http://localhost:8888")
PLAYBACK = ENV.get("CCC_PLAYBACK", "http://localhost:9996")
MQTT_HOST = ENV.get("CCC_MQTT_HOST", "localhost")
MQTT_PORT = int(ENV.get("CCC_MQTT_PORT", "1883"))
RTSP_PORT = int(ENV.get("CCC_RTSP_PORT", "8554"))
PAGE_ORIGIN = "http://localhost:8000"
PROJECT = ENV.get("CCC_COMPOSE_PROJECT", "camera-cloud-core")


def compose(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


class Tokens:
    """Logs in through the real authorisation code + PKCE flow and refreshes before the 5-minute token ends."""

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
    except Exception:  # noqa: BLE001
        pytest.skip("the stack is not running (docker compose up first)")


@pytest.fixture(scope="session")
def tokens() -> Tokens:
    return Tokens()


@pytest.fixture(scope="session")
def http() -> httpx.Client:
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


def jwt_payload(token: str) -> dict:
    p = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def wait_for(fn, timeout: float, interval: float = 1.0, what: str = "condition"):
    """Poll `fn` until it returns a truthy value; return that value. Fail with the last result if time runs out."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}; last value: {last!r}")


def camera_state(http: httpx.Client, tokens: Tokens, user: str, tenant: str, camera: str) -> dict:
    r = http.get(f"/api/v1/tenants/{tenant}/cameras/{camera}", headers=tokens.headers(user))
    assert r.status_code == 200, r.text
    return r.json()


def signing_key_pem() -> bytes:
    """The video signing key, read out of the running API container. Used only to forge tokens that are wrong in
    exactly one respect, so that each MediaMTX check can be seen to fire on its own."""
    return compose("exec", "-T", "api", "cat", "/run/secret/video_signing_key.pem").stdout.encode()


def thumbprint_kid(pem: bytes) -> str:
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_private_key(pem, None).public_key().public_numbers()
    b64 = lambda n: base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()  # noqa: E731
    canonical = json.dumps({"crv": "P-256", "kty": "EC", "x": b64(pub.x), "y": b64(pub.y)}, separators=(",", ":"),
                           sort_keys=True)
    return base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=").decode()
