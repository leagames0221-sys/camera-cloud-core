"""Company isolation at the two video-facing doors: the API (404) and MediaMTX (401 for every wrong token)."""
import socket
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from conftest import HLS, PLAYBACK, RTSP_PORT, compose, signing_key_pem, thumbprint_kid
from ids import CAM_A1, CAM_B1, TENANT_A, TENANT_B

pytestmark = pytest.mark.integration


# ---- the API says 404 for another company's things, and says the same for things that do not exist

@pytest.mark.parametrize("user", ["a-viewer", "a-admin"])
def test_company_a_cannot_reach_company_b_through_the_api(http, tokens, user):
    h = tokens.headers(user)
    paths = [
        ("get", f"/api/v1/tenants/{TENANT_B}/cameras"),
        ("get", f"/api/v1/tenants/{TENANT_B}/cameras/{CAM_B1}"),
        ("post", f"/api/v1/tenants/{TENANT_B}/cameras/{CAM_B1}/live"),
        ("post", f"/api/v1/tenants/{TENANT_B}/cameras/{CAM_B1}/playback"),
        ("get", f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_B1}"),                 # B's camera under A's tenant
        ("post", f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_B1}/live"),
        ("get", f"/api/v1/tenants/{TENANT_A}/cameras/00000000-0000-4000-8000-000000000000"),   # does not exist
    ]
    seen = set()
    for method, path in paths:
        r = getattr(http, method)(path, headers=h)
        assert r.status_code == 404, (path, r.status_code, r.text)
        seen.add(r.text)
    assert seen == {'{"detail":"not_found"}'}      # the same answer, so nothing tells 'other company' from 'missing'


def test_b_viewer_gets_the_same_404_for_a_camera_url_typed_into_the_page(http, tokens):
    r = http.get(f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_A1}", headers=tokens.headers("b-viewer"))
    assert r.status_code == 404
    lst = http.get(f"/api/v1/tenants/{TENANT_B}/cameras", headers=tokens.headers("b-viewer")).json()
    assert CAM_A1 not in str(lst)


def test_roles_are_checked_on_the_server(http, tokens):
    t = f"/api/v1/tenants/{TENANT_A}"
    assert http.post(f"{t}/cameras", json={"name": "x"}, headers=tokens.headers("a-viewer")).status_code == 403
    assert http.get("/api/v1/tenants", headers=tokens.headers("a-admin")).status_code == 403
    for tail in ("live", "playback"):
        r = http.post(f"{t}/cameras/{CAM_A1}/{tail}", headers=tokens.headers("partner-admin"))
        assert r.status_code == 403            # the partner administrator does not watch video
    assert http.get(f"{t}/cameras", headers=tokens.headers("partner-admin")).status_code == 200
    assert http.get("/api/v1/me").status_code == 401
    assert http.get("/api/v1/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_internal_doors_are_not_reachable_through_the_gateway(http):
    assert http.post("/internal/v1/segments", content=b"x").status_code == 404
    assert http.get("/jwks").status_code == 404
    assert http.get("/api/../internal/v1/segments").status_code in (404, 405)
    assert http.get("/api/v1/%2e%2e/%2e%2e/jwks").status_code == 404


# ---- MediaMTX: a token for one path never opens another, and each way of being wrong is refused

@pytest.fixture(scope="module")
def key_pem() -> bytes:
    return signing_key_pem()


def forge(pem: bytes, *, action="read", path=f"{TENANT_A}/{CAM_A1}", iss="ccc-api", aud="mediamtx", ttl=300,
          key=None, kid=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": "test", "iat": now, "exp": now + ttl,
              "mediamtx_permissions": [{"action": action, "path": path}]}
    k = key or serialization.load_pem_private_key(pem, None)
    return jwt.encode(claims, k, algorithm="ES256", headers={"kid": kid or thumbprint_kid(pem)})


def hls_status(token: str, path=f"{TENANT_A}/{CAM_A1}") -> int:
    # cookieCheck=1 goes straight to the token check (the first request is only redirected)
    r = httpx.get(f"{HLS}/{path}/index.m3u8?cookieCheck=1", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    return r.status_code


def test_mediamtx_accepts_a_correct_token(key_pem):
    assert hls_status(forge(key_pem)) == 200


@pytest.mark.parametrize("what", ["expired", "wrong_action", "wrong_key", "wrong_audience", "wrong_issuer",
                                  "other_companys_path"])
def test_mediamtx_refuses_a_token_that_is_wrong_in_one_way(key_pem, what):
    if what == "expired":
        tok = forge(key_pem, ttl=-30)
    elif what == "wrong_action":
        tok = forge(key_pem, action="publish")
    elif what == "wrong_key":
        other = ec.generate_private_key(ec.SECP256R1())     # same kid, different key
        tok = forge(key_pem, key=other)
    elif what == "wrong_audience":
        tok = forge(key_pem, aud="somebody-else")
    elif what == "wrong_issuer":
        tok = forge(key_pem, iss="somebody-else")
    else:
        tok = forge(key_pem, path=f"{TENANT_B}/{CAM_B1}")        # right key, B's path in the token...
        assert hls_status(tok, path=f"{TENANT_B}/{CAM_B1}") == 200   # ...opens B's path: the token itself is valid
        assert hls_status(tok) == 401                                 # ...but not A's
        return
    assert hls_status(tok) == 401


def test_a_company_a_read_token_does_not_open_company_b(http, tokens):
    r = http.post(f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_A1}/live", headers=tokens.headers("a-viewer"))
    token = r.json()["token"]
    assert hls_status(token) == 200
    assert hls_status(token, path=f"{TENANT_B}/{CAM_B1}") == 401


def test_playback_server_refuses_wrong_tokens(key_pem):
    start = "2026-01-01T00:00:00Z"
    url = f"{PLAYBACK}/get"
    params = {"path": f"{TENANT_A}/{CAM_A1}", "start": start, "duration": "5"}
    assert httpx.get(url, params=params).status_code == 401                     # no token
    read = forge(key_pem, action="read")
    assert httpx.get(url, params=params, headers={"Authorization": f"Bearer {read}"}).status_code == 401
    other_path = forge(key_pem, action="playback", path=f"{TENANT_B}/{CAM_B1}")
    assert httpx.get(url, params=params, headers={"Authorization": f"Bearer {other_path}"}).status_code == 401
    expired = forge(key_pem, action="playback", ttl=-30)
    assert httpx.get(url, params=params, headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def rtsp_describe(path: str, token: str) -> str:
    s = socket.create_connection(("localhost", RTSP_PORT), timeout=10)
    try:
        s.sendall(f"DESCRIBE rtsp://localhost:{RTSP_PORT}/{path}?token={token} RTSP/1.0\r\nCSeq: 1\r\n"
                  f"Accept: application/sdp\r\n\r\n".encode())
        return s.recv(4096).decode("latin1").split("\r\n", 1)[0]
    finally:
        s.close()


def test_rtsp_publish_token_is_bound_to_its_own_path(key_pem):
    # needs compose.test.yaml (RTSP published on loopback). A valid token on a path with no stream is 404; the same
    # token on another company's path is 401.
    try:
        socket.create_connection(("localhost", RTSP_PORT), timeout=2).close()
    except OSError:
        pytest.skip("RTSP is not published (start with compose.test.yaml)")
    own = forge(key_pem, action="read", path=f"{TENANT_A}/00000000-0000-4000-8000-0000000000aa")
    assert " 404 " in rtsp_describe(f"{TENANT_A}/00000000-0000-4000-8000-0000000000aa", own)
    assert " 401 " in rtsp_describe(f"{TENANT_B}/{CAM_B1}", own)


def test_control_api_is_not_published():
    with pytest.raises(OSError):
        socket.create_connection(("localhost", 9997), timeout=2).close()
    out = compose("ps", "--format", "json").stdout
    assert "9997" not in out
