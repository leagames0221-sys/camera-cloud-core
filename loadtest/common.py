"""Helpers shared by the load-test programs (setup, probe, locustfile). Standard library only, so that the same
code runs inside the load-test image and, for a quick check, on the host.

Everything talks to the stack from inside the compose network: the gateway for the API, Keycloak by its network
name (the login form's own links name http://localhost:8080, which is not reachable from here, so they are
rewritten), Mosquitto for MQTT.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone

GATEWAY = os.environ.get("GATEWAY_URL", "http://gateway:8000")
KC = os.environ.get("KC_URL", "http://keycloak:8080")      # how this container reaches Keycloak
KC_PUBLIC = "http://localhost:8080"                         # what Keycloak writes into its pages (KC_HOSTNAME)
REALM = "ccc"
CLIENT_ID = "ccc-web"
REDIRECT = "http://localhost:8000/"
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
SEGMENT_URL = os.environ.get("SEGMENT_URL", "http://api:8000/internal/v1/segments")
HOOK_SECRET_FILE = os.environ.get("HOOK_SECRET_FILE", "/run/secret/hook_secret")
DATA_FILE = os.environ.get("LOAD_DATA", "/work/load_data.json")
CLAIM_DIR = os.environ.get("CLAIM_DIR", "/work/claims")

# The load shape (docs/validation/load_conditions.md). Changing a number here changes the test.
TENANT_NAMES = [f"load-L{i}" for i in range(1, 6)]
PROBE_TENANT_NAME = "load-probe"
CAMERAS_PER_TENANT = 60
VIEWERS = 50
SEGMENT_USERS = 20
MQTT_CAMERAS = 300

# Fixed ids of the load users in the realm file (infra/keycloak/ccc-realm.json).
USER_SUB = {
    "partner-admin": "40000000-0000-4000-8000-0000000000f0",
    "l-admin": "40000000-0000-4000-8000-0000000000e1",
    "l-viewer": "40000000-0000-4000-8000-0000000000e2",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _send(req: urllib.request.Request, timeout: float = 30):
    """(status, headers, body bytes); a 3xx/4xx/5xx is returned, not raised."""
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def password() -> str:
    return os.environ["DEMO_USER_PASSWORD"]


def login(username: str, pw: str | None = None) -> str:
    """Authorisation code flow with PKCE, the way a browser does it (the realm has no password grant). Returns the
    access token."""
    pw = password() if pw is None else pw
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    q = urllib.parse.urlencode({"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code",
                                "scope": "openid", "state": state, "response_mode": "query",
                                "code_challenge": challenge, "code_challenge_method": "S256"})
    status, headers, body = _send(urllib.request.Request(f"{KC}/realms/{REALM}/protocol/openid-connect/auth?{q}"))
    if status != 200:
        raise RuntimeError(f"login page returned {status}")
    cookies: dict[str, str] = {}
    for line in headers.get_all("Set-Cookie") or []:
        name, _, rest = line.partition("=")
        cookies[name.strip()] = rest.split(";", 1)[0]
    text = body.decode("utf-8", "replace")
    m = (re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', text)
         or re.search(r'<form[^>]+action="([^"]+)"[^>]+id="kc-form-login"', text))
    if not m:
        raise RuntimeError("login form not found")
    action = html.unescape(m.group(1)).replace(KC_PUBLIC, KC, 1)
    data = urllib.parse.urlencode({"username": username, "password": pw, "credentialId": ""}).encode()
    req = urllib.request.Request(action, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())})
    status, headers, _ = _send(req)
    loc = headers.get("Location", "")
    if status != 302 or not loc.startswith(REDIRECT):
        raise RuntimeError(f"login returned {status}")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    if qs.get("state") != [state] or "code" not in qs:
        raise RuntimeError("login redirect has no code or a different state")
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": qs["code"][0], "redirect_uri": REDIRECT,
                                   "client_id": CLIENT_ID, "code_verifier": verifier}).encode()
    status, _, body = _send(urllib.request.Request(
        f"{KC}/realms/{REALM}/protocol/openid-connect/token", data=form, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"}))
    if status != 200:
        raise RuntimeError(f"token endpoint returned {status}")
    return json.loads(body)["access_token"]


class TokenCache:
    """A user's token, fetched again after 200 seconds (it lives 300)."""

    def __init__(self, username: str) -> None:
        self.username = username
        self._at = 0.0
        self._token = ""

    def get(self) -> str:
        if time.monotonic() - self._at > 200:
            self._token = login(self.username)
            self._at = time.monotonic()
        return self._token


def api(method: str, path: str, token: str, body: dict | None = None, *, base: str | None = None):
    """(status, parsed JSON or None) for one call to the public API."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    status, _, raw = _send(urllib.request.Request((base or GATEWAY) + path, data=data, method=method,
                                                  headers=headers))
    try:
        return status, (json.loads(raw) if raw else None)
    except ValueError:
        return status, None


def load_data() -> dict:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def claim(kind: str, limit: int) -> int:
    """Take the lowest free number 0..limit-1 for this kind, across all the Locust processes of one run (a file is
    created exclusively, so two processes never get the same number)."""
    d = os.path.join(CLAIM_DIR, os.environ["RUN_ID"])
    os.makedirs(d, exist_ok=True)
    for i in range(limit):
        try:
            os.close(os.open(os.path.join(d, f"{kind}-{i}"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return i
        except FileExistsError:
            continue
    raise RuntimeError(f"more than {limit} {kind} users were started")


def iso_z(dt) -> str:
    """The API's own time format: UTC, microseconds, trailing Z."""
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def segment_form(tenant_id: str, camera_id: str, started) -> bytes:
    """What MediaMTX's hook sends for a closed segment (infra/mediamtx/segment-hook.sh)."""
    path = f"{tenant_id}/{camera_id}"
    name = started.strftime("%Y-%m-%d_%H-%M-%S-") + f"{started.microsecond:06d}.mp4"
    return urllib.parse.urlencode({"path": path, "file": f"recordings/{path}/{name}", "duration": "60"}).encode()
