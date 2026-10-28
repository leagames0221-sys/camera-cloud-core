"""Operation x role table (docs/requirements.md): every pair without a 'yes' is refused on the server,
and another company's tenant or camera is a 404 that looks the same as a thing that does not exist."""
import uuid

import pytest

from app import seed_data as sd
from helpers import cam_url

A, B = sd.TENANT_A, sd.TENANT_B
NEW_VIEWER = uuid.UUID("40000000-0000-4000-8000-0000000000c1")


def call(client, world, user, op):
    h = world.auth(user) if user else {}
    t = f"/api/v1/tenants/{A}"
    if op == "tenant_list":
        return client.get("/api/v1/tenants", headers=h)
    if op == "tenant_create":
        return client.post("/api/v1/tenants", json={"name": "C 社"}, headers=h)
    if op == "camera_list":
        return client.get(f"{t}/cameras", headers=h)
    if op == "camera_register":
        return client.post(f"{t}/cameras", json={"name": "new"}, headers=h)
    if op == "camera_delete":
        return client.delete(cam_url(A, sd.CAM_A2), headers=h)
    if op == "live":
        return client.post(cam_url(A, sd.CAM_A1, "/live"), headers=h)
    if op == "playback":
        return client.post(cam_url(A, sd.CAM_A1, "/playback"), headers=h)
    if op == "member_add":
        return client.post(f"{t}/members", json={"user_id": str(NEW_VIEWER), "role": "viewer"}, headers=h)
    raise AssertionError(op)


# status for (partner admin, tenant admin of A, viewer of A). 'playback' with nothing recorded is a 404 with
# detail no_recording when allowed, which is told apart from not_found below.
EXPECTED = {
    "tenant_list": (200, 403, 403),
    "tenant_create": (201, 403, 403),
    "camera_list": (200, 200, 200),
    "camera_register": (403, 201, 403),
    "camera_delete": (403, 204, 403),
    "live": (403, 200, 200),
    "playback": (403, 404, 404),
    "member_add": (201, 201, 403),
}
USERS = (sd.USER_PARTNER_ADMIN, sd.USER_A_ADMIN, sd.USER_A_VIEWER)


@pytest.mark.parametrize("op", sorted(EXPECTED))
@pytest.mark.parametrize("i", range(3))
def test_role_table(client, world, op, i):
    r = call(client, world, USERS[i], op)
    assert r.status_code == EXPECTED[op][i], (op, USERS[i], r.text)
    if op == "playback" and i > 0:
        assert r.json() == {"detail": "no_recording"}


@pytest.mark.parametrize("op", ["camera_list", "camera_register", "camera_delete", "live", "playback", "member_add"])
@pytest.mark.parametrize("user", [sd.USER_B_ADMIN, sd.USER_B_VIEWER])
def test_other_company_gets_404_not_403(client, world, op, user):
    r = call(client, world, user, op)
    assert r.status_code == 404 and r.json() == {"detail": "not_found"}


@pytest.mark.parametrize("op", sorted(EXPECTED))
def test_no_token_is_401(client, world, op):
    r = call(client, world, None, op)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("user", [sd.USER_B_ADMIN, sd.USER_B_VIEWER])
def test_tenant_partner_ops_are_403_for_tenant_users(client, world, user):
    assert call(client, world, user, "tenant_list").status_code == 403
    assert call(client, world, user, "tenant_create").status_code == 403


def test_other_companys_camera_and_missing_camera_look_the_same(client, world):
    h = world.auth(sd.USER_A_ADMIN)
    other_tenant = client.get(cam_url(B, sd.CAM_B1), headers=h)
    other_camera_in_my_tenant = client.get(cam_url(A, sd.CAM_B1), headers=h)
    missing = client.get(cam_url(A, uuid.uuid4()), headers=h)
    missing_tenant = client.get(cam_url(uuid.uuid4(), uuid.uuid4()), headers=h)
    malformed = client.get(f"/api/v1/tenants/{A}/cameras/not-a-uuid", headers=h)
    bodies = {(r.status_code, r.text) for r in (other_tenant, other_camera_in_my_tenant, missing, missing_tenant,
                                                 malformed)}
    assert bodies == {(404, '{"detail":"not_found"}')}


def test_other_company_never_reaches_video_tokens(client, world):
    for tail in ("/live", "/playback"):
        r = client.post(cam_url(B, sd.CAM_B1, tail), headers=world.auth(sd.USER_A_ADMIN))
        assert r.status_code == 404
        assert "token" not in r.text


def test_camera_list_shows_only_own_tenant(client, world):
    r = client.get(f"/api/v1/tenants/{A}/cameras", headers=world.auth(sd.USER_A_VIEWER))
    assert {c["id"] for c in r.json()} == {str(sd.CAM_A1), str(sd.CAM_A2)}
    r = client.get(f"/api/v1/tenants/{B}/cameras", headers=world.auth(sd.USER_B_VIEWER))
    assert {c["id"] for c in r.json()} == {str(sd.CAM_B1)}


def test_partner_admin_sees_cameras_of_every_tenant_but_no_video(client, world):
    h = world.auth(sd.USER_PARTNER_ADMIN)
    for tenant, cam in ((A, sd.CAM_A1), (B, sd.CAM_B1)):
        assert client.get(f"/api/v1/tenants/{tenant}/cameras", headers=h).status_code == 200
        assert client.post(cam_url(tenant, cam, "/live"), headers=h).status_code == 403
        assert client.post(cam_url(tenant, cam, "/playback"), headers=h).status_code == 403


def test_member_grant_limits(client, world):
    t = f"/api/v1/tenants/{A}/members"
    body = lambda role: {"user_id": str(NEW_VIEWER), "role": role}  # noqa: E731
    # partner admin: up to tenant admin, not another partner admin
    h = world.auth(sd.USER_PARTNER_ADMIN)
    assert client.post(t, json=body("tenant_admin"), headers=h).status_code == 201
    assert client.post(t, json=body("partner_admin"), headers=h).status_code == 403
    assert client.post(t, json=body("nonsense"), headers=h).status_code == 403
    # tenant admin: viewers only
    h = world.auth(sd.USER_A_ADMIN)
    assert client.post(t, json=body("viewer"), headers=h).status_code == 201
    assert client.post(t, json=body("tenant_admin"), headers=h).status_code == 403
    # the grant takes effect, and is repeatable
    assert client.post(t, json=body("viewer"), headers=h).status_code == 200
    me = client.get("/api/v1/me", headers=world.auth(NEW_VIEWER)).json()
    assert {e["role"] for e in me["entries"]} == {"tenant_admin", "viewer"}


def test_tenant_admin_cannot_grant_in_another_tenant(client, world):
    r = client.post(f"/api/v1/tenants/{B}/members", json={"user_id": str(NEW_VIEWER), "role": "viewer"},
                    headers=world.auth(sd.USER_A_ADMIN))
    assert r.status_code == 404


def test_me_lists_only_own_entries(client, world):
    me = client.get("/api/v1/me", headers=world.auth(sd.USER_B_VIEWER)).json()
    assert me["entries"] == [{"role": "viewer", "tenant_id": str(B), "tenant_name": "B 社"}]
    me = client.get("/api/v1/me", headers=world.auth(sd.USER_PARTNER_ADMIN)).json()
    assert {e["tenant_id"] for e in me["entries"]} == {str(A), str(B)}
    assert {e["role"] for e in me["entries"]} == {"partner_admin"}


def test_user_without_membership_sees_nothing(client, world):
    stranger = uuid.uuid4()
    h = world.auth(stranger)
    assert client.get("/api/v1/me", headers=h).json()["entries"] == []
    assert client.get(f"/api/v1/tenants/{A}/cameras", headers=h).status_code == 404
    assert client.get("/api/v1/tenants", headers=h).status_code == 403


def test_partner_admin_creates_and_lists_tenants(client, world):
    h = world.auth(sd.USER_PARTNER_ADMIN)
    r = client.post("/api/v1/tenants", json={"name": "C 社"}, headers=h)
    assert r.status_code == 201
    names = [t["name"] for t in client.get("/api/v1/tenants", headers=h).json()]
    assert names == ["A 社", "B 社", "C 社"]
    # a partner admin cannot name some other partner
    r = client.post("/api/v1/tenants", json={"name": "D", "partner_id": str(uuid.uuid4())}, headers=h)
    assert r.status_code == 404
