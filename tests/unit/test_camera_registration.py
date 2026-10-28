"""Camera registration: the secret is shown once and only its hash is kept; the response carries the fields a later credential type can reuse."""
import asyncio
import hashlib
import re

from app import seed_data as sd
from app.camera_secret import hash_secret, new_camera_secret, verify_secret
from app.domain import video_path
from helpers import cam_url


def test_secret_is_long_random_and_hashed_with_sha256():
    a, b = new_camera_secret(), new_camera_secret()
    assert a != b and len(a) >= 43   # 32 random bytes in url-safe base64
    assert hash_secret(a) == hashlib.sha256(a.encode()).hexdigest()
    assert verify_secret(a, hash_secret(a)) and not verify_secret(b, hash_secret(a))


def test_register_returns_secret_once_and_stores_only_the_hash(client, world):
    r = client.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": "ロビー"},
                    headers=world.auth(sd.USER_A_ADMIN))
    assert r.status_code == 201
    b = r.json()
    cam_id = b["id"]
    assert b["credential_type"] == "secret"
    assert b["mediamtx_url"] == f"rtsp://mediamtx:8554/{video_path(sd.TENANT_A, cam_id)}"
    assert b["mqtt_url"] == "mqtt://mosquitto:1883"
    assert b["mqtt_topic"] == f"cameras/{cam_id}/state" and b["mqtt_client_id"] == cam_id
    stored = asyncio.run(world.store.get_camera(sd.TENANT_A, __import__("uuid").UUID(cam_id)))
    assert stored.secret_sha256 == hash_secret(b["secret"]) and b["secret"] not in stored.__dict__.values()
    # the broker user was created with the same secret
    assert world.dynsec.added[-1][1] == b["secret"] and str(world.dynsec.added[-1][0]) == cam_id
    # it never comes back
    again = client.get(cam_url(sd.TENANT_A, cam_id), headers=world.auth(sd.USER_A_ADMIN))
    assert "secret" not in again.text and b["secret"] not in again.text
    lst = client.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras", headers=world.auth(sd.USER_A_ADMIN))
    assert b["secret"] not in lst.text and "sha256" not in lst.text
    # and the new camera can fetch its publish token with it
    t = client.post("/api/v1/camera/token", json={"camera_id": cam_id, "secret": b["secret"]})
    assert t.status_code == 200


def test_register_validates_the_name(client, world):
    h = world.auth(sd.USER_A_ADMIN)
    assert client.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": ""}, headers=h).status_code == 422
    assert client.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": "x" * 81}, headers=h).status_code == 422


def test_register_is_undone_when_the_broker_user_cannot_be_made(client, world):
    world.dynsec.fail = True
    r = client.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": "x"}, headers=world.auth(sd.USER_A_ADMIN))
    assert r.status_code == 502
    names = [c["name"] for c in client.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras",
                                           headers=world.auth(sd.USER_A_ADMIN)).json()]
    assert "x" not in names


def test_delete_removes_the_camera_and_its_broker_user(client, world):
    h = world.auth(sd.USER_A_ADMIN)
    assert client.delete(cam_url(sd.TENANT_A, sd.CAM_A2), headers=h).status_code == 204
    assert world.dynsec.removed == [sd.CAM_A2]
    assert client.get(cam_url(sd.TENANT_A, sd.CAM_A2), headers=h).status_code == 404
    assert client.post("/api/v1/camera/token", json={"camera_id": str(sd.CAM_A2), "secret": "secret-a2"}
                       ).status_code == 401


def test_other_tenants_admin_cannot_delete_a_camera(client, world):
    r = client.delete(cam_url(sd.TENANT_A, sd.CAM_A1), headers=world.auth(sd.USER_B_ADMIN))
    assert r.status_code == 404
    r = client.delete(cam_url(sd.TENANT_B, sd.CAM_A1), headers=world.auth(sd.USER_B_ADMIN))
    assert r.status_code == 404
    assert asyncio.run(world.store.get_camera(sd.TENANT_A, sd.CAM_A1)) is not None


def test_paths_are_lower_case_uuids(client, world):
    r = client.post(cam_url(sd.TENANT_A, sd.CAM_A1, "/live"), headers=world.auth(sd.USER_A_ADMIN))
    assert re.fullmatch(r"http://localhost:8888/[0-9a-f-]{36}/[0-9a-f-]{36}/index\.m3u8", r.json()["hls_url"])
