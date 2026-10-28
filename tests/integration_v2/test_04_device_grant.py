"""Registering a camera with the device authorization grant (RFC 8628): the camera asks for a code, a tenant
administrator approves it in Keycloak's pages, the camera registers itself once with the token it gets."""
import base64
import json
import re
import threading
import time

import httpx
import pytest

import device_flow as dev
from conftest import ENV, KC, TENANT_A, TENANT_C, camera_view, compose, wait_for

pytestmark = pytest.mark.integration

PASSWORD = ENV["DEMO_USER_PASSWORD"]
C_ADMIN = "40000000-0000-4000-8000-0000000000c1"


def claims_of(token: str) -> dict:
    p = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def token_for(user: str, accept: bool = True) -> dict:
    """The whole grant for a camera and an approving user; returns the token response."""
    st = dev.start(KC)
    assert st.status_code == 200, st.text
    st = st.json()
    first = dev.poll(KC, st["device_code"])
    assert first.status_code == 400 and first.json()["error"] == "authorization_pending"
    page = dev.decide(KC, st["user_code"], user, PASSWORD, accept=accept)
    if accept:
        assert "Successful" in page, page[-400:]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(5.2)
        r = dev.poll(KC, st["device_code"])
        if r.status_code == 200 or r.json()["error"] != "authorization_pending":
            return {"status": r.status_code, **r.json()}
    pytest.fail("no answer from Keycloak within 30 seconds")


def enroll(http, access_token: str, name: str = "device test"):
    return http.post("/api/v1/devices/enroll", json={"name": name}, headers={"Authorization": f"Bearer {access_token}"})


def drop(http, tokens, camera_id: str) -> None:
    http.delete(f"/api/v1/tenants/{TENANT_C}/cameras/{camera_id}", headers=tokens.headers("c-admin"))


def test_pending_then_approved_then_the_camera_is_registered_and_appears_in_the_list(http, tokens):
    t = token_for("c-admin")
    assert t["status"] == 200 and t["token_type"].lower() == "bearer"
    r = enroll(http, t["access_token"])
    assert r.status_code == 201, r.text
    cam = r.json()
    try:
        assert cam["tenant_id"] == TENANT_C and cam["secret"] and cam["credential_type"] == "secret"
        assert cam["mqtt_url"] == "mqtt://mosquitto-s2:1883"
        listed = http.get(f"/api/v1/tenants/{TENANT_C}/cameras", headers=tokens.headers("c-admin")).json()
        assert cam["id"] in [c["id"] for c in listed]
        # the camera's own secret works for the publish token, the access token it used does not do that
        assert http.post("/api/v1/camera/token", json={"camera_id": cam["id"], "secret": cam["secret"]}).status_code == 200
    finally:
        drop(http, tokens, cam["id"])


def test_the_token_has_no_refresh_token_the_device_client_as_authorized_party_and_only_the_registration_audience():
    t = token_for("c-admin")
    assert t["status"] == 200
    assert "refresh_token" not in t and "id_token" not in t
    c = claims_of(t["access_token"])
    assert c["azp"] == "ccc-device" and c["typ"] == "Bearer" and c["sub"] == C_ADMIN
    aud = c["aud"] if isinstance(c["aud"], list) else [c["aud"]]
    assert aud == ["ccc-enroll"], aud                      # no `account`, nothing else
    assert "resource_access" not in c and "realm_access" not in c
    assert c["jti"] and c["exp"] - c["iat"] <= 300


def test_the_same_token_cannot_register_twice(http, tokens):
    t = token_for("c-admin")
    first = enroll(http, t["access_token"])
    assert first.status_code == 201
    try:
        assert enroll(http, t["access_token"]).status_code == 401
    finally:
        drop(http, tokens, first.json()["id"])


def test_two_calls_at_the_same_moment_with_one_token_register_one_camera(http, tokens):
    t = token_for("c-admin")
    results = []

    def go():
        with httpx.Client(base_url=http.base_url, timeout=30) as c:
            results.append(enroll(c, t["access_token"], "race"))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    codes = sorted(r.status_code for r in results)
    try:
        assert codes == [201, 401, 401, 401], codes
    finally:
        for r in results:
            if r.status_code == 201:
                drop(http, tokens, r.json()["id"])


def test_a_viewer_can_approve_but_the_api_refuses_to_register(http, tokens):
    t = token_for("c-viewer")
    assert t["status"] == 200
    assert enroll(http, t["access_token"]).status_code == 403


def test_a_partner_administrator_who_approves_does_not_register_either(http):
    t = token_for("partner-admin")
    assert t["status"] == 200
    assert enroll(http, t["access_token"]).status_code == 403


def test_refusing_in_the_consent_page_ends_the_grant_with_access_denied():
    t = token_for("c-admin", accept=False)
    assert t["status"] == 400 and t["error"] == "access_denied", t


def test_polling_faster_than_the_interval_gets_slow_down():
    st = dev.start(KC).json()
    answers = [dev.poll(KC, st["device_code"]).json()["error"] for _ in range(3)]
    assert answers[0] == "authorization_pending" and answers[1:] == ["slow_down", "slow_down"], answers
    assert st["interval"] == 5 and st["expires_in"] == 600


def admin_token() -> str:
    r = httpx.post(f"{KC}/realms/master/protocol/openid-connect/token", timeout=20,
                   data={"grant_type": "password", "client_id": "admin-cli", "username": ENV["KC_ADMIN_USER"],
                         "password": ENV["KC_ADMIN_PASSWORD"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def set_lifespan(value: str) -> str:
    """Set the code lifespan of the device client through Keycloak's admin API; returns the old value."""
    h = {"Authorization": f"Bearer {admin_token()}"}
    (client,) = httpx.get(f"{KC}/admin/realms/ccc/clients", params={"clientId": "ccc-device"}, headers=h).json()
    old = client["attributes"]["oauth2.device.code.lifespan"]
    client["attributes"]["oauth2.device.code.lifespan"] = value
    r = httpx.put(f"{KC}/admin/realms/ccc/clients/{client['id']}", json=client, headers=h)
    assert r.status_code == 204, r.text
    return old


def test_a_code_that_runs_out_gives_expired_token():
    old = set_lifespan("15")
    try:
        st = dev.start(KC).json()
        assert st["expires_in"] == 15
        time.sleep(17)
        r = dev.poll(KC, st["device_code"])
        assert r.status_code == 400 and r.json()["error"] == "expired_token", r.text
    finally:
        set_lifespan(old)
    assert dev.start(KC).json()["expires_in"] == 600


def test_the_device_token_opens_no_other_door_and_the_pages_token_does_not_register(http, tokens):
    t = token_for("c-admin")
    h = {"Authorization": f"Bearer {t['access_token']}"}
    assert http.get("/api/v1/me", headers=h).status_code == 401
    assert http.get(f"/api/v1/tenants/{TENANT_C}/cameras", headers=h).status_code == 401
    assert http.post(f"/api/v1/tenants/{TENANT_C}/cameras", json={"name": "x"}, headers=h).status_code == 401
    assert http.get("/api/v1/tenants", headers=h).status_code == 401
    # and the viewing page's token (a different client) does not register a camera
    assert enroll(http, tokens.get("c-admin")).status_code == 401
    assert enroll(http, "garbage").status_code == 401


def test_the_viewing_pages_client_cannot_start_the_grant():
    r = dev.start(KC, client_id="ccc-web")
    assert r.status_code == 400 and r.json()["error"] == "unauthorized_client"


def test_the_consent_page_says_what_is_being_asked():
    st = dev.start(KC).json()
    from urllib.parse import urljoin
    from oidc_flow import Jar
    jar = Jar()
    with httpx.Client(follow_redirects=False, timeout=20) as c:
        method, url, data = "GET", f"{KC}/realms/ccc/device?user_code={st['user_code']}", None
        text = ""
        for _ in range(8):
            r = c.request(method, url, data=data, headers=jar.header())
            jar.take(r)
            if r.status_code in (301, 302, 303):
                method, url, data = "GET", urljoin(str(r.url), r.headers["location"]), None
                continue
            if 'id="kc-form-login"' in r.text:
                action, fields, _ = dev._forms(r.text)[0]
                method, url, data = "POST", urljoin(str(r.url), action), {**fields, "username": "c-admin",
                                                                           "password": PASSWORD}
                continue
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
            break
    assert "Register a camera in your tenant" in text and "カメラをテナントに登録する" in text
