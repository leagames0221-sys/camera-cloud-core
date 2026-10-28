"""The gateway forwards /api/ and nothing else."""
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import build_config_js, create_app, safe_rest


class Recorder:
    def __init__(self):
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(201, json={"ok": True}, headers={"X-From": "api", "Connection": "close"})


@pytest.fixture
def gw(tmp_path: pathlib.Path):
    rec = Recorder()
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>viewer</h1>")
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "hls.min.js").write_text("// hls")
    app = create_app("http://api:8000", web, vendor, {"keycloakUrl": "http://localhost:8080", "realm": "ccc",
                                                      "clientId": "ccc-web", "apiBase": "/api/v1"},
                     transport=httpx.MockTransport(rec))
    with TestClient(app) as c:
        yield c, rec


def test_api_is_forwarded_with_method_query_body_and_auth(gw):
    c, rec = gw
    r = c.post("/api/v1/tenants/x/cameras?a=1&b=%20", json={"name": "n"}, headers={"Authorization": "Bearer T"})
    assert r.status_code == 201 and r.json() == {"ok": True}
    assert r.headers["x-from"] == "api" and r.headers["cache-control"] == "no-store"
    req = rec.seen[0]
    assert req.method == "POST"
    assert req.url.path == "/api/v1/tenants/x/cameras" and req.url.query == b"a=1&b=%20"
    assert req.headers["authorization"] == "Bearer T"
    assert req.content == b'{"name":"n"}'


@pytest.mark.parametrize("path", ["/internal/v1/segments", "/internal/", "/jwks"])
def test_internal_paths_are_not_reachable_from_outside(gw, path):
    c, rec = gw
    for method in ("get", "post"):
        r = getattr(c, method)(path)
        assert r.status_code in (404, 405)
    assert rec.seen == []


@pytest.mark.parametrize("path", [
    "/api/../internal/v1/segments", "/api/v1/../../internal/v1/segments", "/api/%2e%2e/internal/v1/segments",
    "/api/v1/%2e%2e/%2e%2e/jwks", "/api//", "/api/",
])
def test_nothing_climbs_out_of_api(gw, path):
    c, rec = gw
    c.get(path)
    for req in rec.seen:
        assert req.url.path.startswith("/api/") and ".." not in req.url.path
    assert not any(r.url.path.startswith("/internal") or r.url.path == "/jwks" for r in rec.seen)


def test_safe_rest():
    assert safe_rest("v1/me") and safe_rest("v1/tenants/1/cameras")
    for bad in ("", "../x", "v1/../x", "v1/./x", "v1/%2e%2e/x", "a\\b", "a\x00b"):
        assert not safe_rest(bad)


def test_static_page_vendor_and_config(gw):
    c, _ = gw
    assert "viewer" in c.get("/").text
    assert c.get("/vendor/hls.min.js").text == "// hls"
    cfg = c.get("/config.js")
    assert cfg.headers["content-type"].startswith("application/javascript")
    assert cfg.text.startswith("window.CCC_CONFIG = {") and '"clientId": "ccc-web"' in cfg.text
    assert c.get("/healthz").json() == {"status": "ok"}


def test_vendor_cannot_be_walked(gw):
    c, _ = gw
    assert c.get("/vendor/../index.html").status_code in (200, 404)  # normalised by the client to /index.html
    assert c.get("/vendor/%2e%2e/%2e%2e/etc/passwd").status_code == 404


def test_backend_down_is_502(tmp_path):
    def boom(request):
        raise httpx.ConnectError("down")

    app = create_app("http://api:8000", tmp_path / "none", tmp_path / "none", {}, transport=httpx.MockTransport(boom))
    with TestClient(app) as c:
        assert c.get("/api/v1/me").status_code == 502


def test_config_js_is_valid_json_assignment():
    s = build_config_js({"a": "日本語"})
    assert s == 'window.CCC_CONFIG = {"a": "日本語"};\n'
