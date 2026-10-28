"""Camera registration and the tenant creation through the API (there is no screen for them)."""
import httpx
import pytest

from ids import TENANT_A

pytestmark = pytest.mark.integration


def test_register_get_token_and_delete(http, tokens):
    r = http.post(f"/api/v1/tenants/{TENANT_A}/cameras", json={"name": "registration-test"},
                  headers=tokens.headers("a-admin"))
    assert r.status_code == 201
    cam = r.json()
    try:
        assert cam["credential_type"] == "secret" and len(cam["secret"]) >= 43
        assert cam["mediamtx_url"].startswith("rtsp://") and cam["mqtt_url"].startswith("mqtt://")
        # the secret is never shown again
        again = http.get(f"/api/v1/tenants/{TENANT_A}/cameras/{cam['id']}", headers=tokens.headers("a-viewer"))
        assert cam["secret"] not in again.text
        # the camera trades id + secret for a publish token for its own path
        t = http.post("/api/v1/camera/token", json={"camera_id": cam["id"], "secret": cam["secret"]})
        assert t.status_code == 200 and t.json()["path"] == f"{TENANT_A}/{cam['id']}"
        bad = http.post("/api/v1/camera/token", json={"camera_id": cam["id"], "secret": "wrong"})
        assert bad.status_code == 401
    finally:
        d = http.delete(f"/api/v1/tenants/{TENANT_A}/cameras/{cam['id']}", headers=tokens.headers("a-admin"))
        assert d.status_code == 204
    gone = http.post("/api/v1/camera/token", json={"camera_id": cam["id"], "secret": cam["secret"]})
    assert gone.status_code == 401


def test_partner_admin_creates_a_tenant_and_a_tenant_admin_cannot(http, tokens):
    h = tokens.headers("partner-admin")
    r = http.post("/api/v1/tenants", json={"name": "C 社 (test)"}, headers=h)
    assert r.status_code == 201
    names = [t["name"] for t in http.get("/api/v1/tenants", headers=h).json()]
    assert {"A 社", "B 社", "C 社 (test)"} <= set(names)
    assert http.post("/api/v1/tenants", json={"name": "x"}, headers=tokens.headers("a-admin")).status_code == 403


def test_openapi_is_published_through_the_gateway(http):
    r = http.get("/api/v1/openapi.json")
    assert r.status_code == 200 and r.json()["info"]["title"] == "camera-cloud-core"
    assert httpx.get(f"{http.base_url}/api/v1/docs").status_code in (404, 405)
