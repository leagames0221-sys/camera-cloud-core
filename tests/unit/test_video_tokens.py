"""Video tokens: one action, one path, always exp / iat / iss / aud; the JWKS has kid and alg; endpoints issue
the right action for the right path."""
import time
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from jwt.algorithms import ECAlgorithm

from app import seed_data as sd
from app.config import TTL_API, TTL_PLAYBACK, TTL_PUBLISH, TTL_READ
from app.domain import Segment, video_path
from app.video_jwt import KeyStore, generate_private_key_pem
from helpers import cam_url, path_of

ISS, AUD = "ccc-api", "mediamtx"


@pytest.fixture
def keys():
    return KeyStore(generate_private_key_pem())


def decode(keys, token):
    pub = ECAlgorithm.from_jwk(keys.jwks()["keys"][0])
    return jwt.decode(token, pub, algorithms=["ES256"], audience=AUD, issuer=ISS,
                      options={"require": ["exp", "iat", "iss", "aud"]})


def test_jwks_shape(keys):
    doc = keys.jwks()
    assert list(doc) == ["keys"] and len(doc["keys"]) == 1
    k = doc["keys"][0]
    assert k["kty"] == "EC" and k["crv"] == "P-256" and k["alg"] == "ES256" and k["use"] == "sig"
    assert k["kid"] == keys.kid and len(k["kid"]) >= 20
    assert "d" not in k  # never the private part


def test_kid_is_stable_for_the_same_key():
    pem = generate_private_key_pem()
    assert KeyStore(pem).kid == KeyStore(pem).kid
    assert KeyStore(pem).kid != KeyStore(generate_private_key_pem()).kid


@pytest.mark.parametrize("action,ttl", [("publish", TTL_PUBLISH), ("read", TTL_READ), ("playback", TTL_PLAYBACK)])
def test_claims_of_path_tokens(keys, action, ttl):
    p = "11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222"
    token, exp = keys.issue(action=action, path=p, subject="u", issuer=ISS, audience=AUD, now=1_000_000)
    claims = jwt.decode(token, ECAlgorithm.from_jwk(keys.jwks()["keys"][0]), algorithms=["ES256"], audience=AUD,
                        issuer=ISS, options={"verify_exp": False})
    assert claims["exp"] == exp == 1_000_000 + ttl
    assert claims["iat"] == 1_000_000
    assert claims["iss"] == ISS and claims["aud"] == AUD
    assert claims["mediamtx_permissions"] == [{"action": action, "path": p}]
    assert jwt.get_unverified_header(token)["kid"] == keys.kid
    assert jwt.get_unverified_header(token)["alg"] == "ES256"


def test_api_token_has_no_path_and_one_minute_life(keys):
    token, exp = keys.issue(action="api", path=None, subject="x", issuer=ISS, audience=AUD, now=5)
    claims = jwt.decode(token, ECAlgorithm.from_jwk(keys.jwks()["keys"][0]), algorithms=["ES256"], audience=AUD,
                        issuer=ISS, options={"verify_exp": False})
    assert claims["mediamtx_permissions"] == [{"action": "api"}] and exp == 5 + TTL_API


@pytest.mark.parametrize("action,path", [("read", None), ("read", ""), ("publish", None), ("playback", ""),
                                         ("api", "a/b"), ("delete", "a/b")])
def test_refuses_to_issue_without_exactly_one_path_or_with_unknown_action(keys, action, path):
    with pytest.raises(ValueError):
        keys.issue(action=action, path=path, subject="x", issuer=ISS, audience=AUD)


def test_every_issued_token_has_the_required_claims(keys):
    for action, path in (("publish", "a/b"), ("read", "a/b"), ("playback", "a/b"), ("api", None)):
        token, _ = keys.issue(action=action, path=path, subject="x", issuer=ISS, audience=AUD)
        decode(keys, token)  # raises if any of exp, iat, iss, aud is missing or wrong
        perms = jwt.decode(token, options={"verify_signature": False})["mediamtx_permissions"]
        assert len(perms) == 1


def test_a_token_is_rejected_with_another_key(keys):
    token, _ = keys.issue(action="read", path="a/b", subject="x", issuer=ISS, audience=AUD)
    other = KeyStore(generate_private_key_pem())
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, ECAlgorithm.from_jwk(other.jwks()["keys"][0]), algorithms=["ES256"], audience=AUD)


def test_the_signing_key_must_be_p256():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with pytest.raises(ValueError):
        KeyStore(pem)


# ---- through the HTTP API

def token_claims(world, token):
    return jwt.decode(token, ECAlgorithm.from_jwk(world.keys.jwks()["keys"][0]), algorithms=["ES256"],
                      audience=AUD, issuer=ISS)


def test_live_token_is_read_for_that_path_only(client, world):
    r = client.post(cam_url(sd.TENANT_A, sd.CAM_A1, "/live"), headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 200
    body = r.json()
    claims = token_claims(world, body["token"])
    expected = path_of(sd.TENANT_A, sd.CAM_A1)
    assert claims["mediamtx_permissions"] == [{"action": "read", "path": expected}]
    assert claims["sub"] == str(sd.USER_A_VIEWER)
    assert body["hls_url"] == f"http://localhost:8888/{expected}/index.m3u8"
    assert 0 < claims["exp"] - time.time() <= TTL_READ


def test_playback_token_covers_the_last_five_minutes(client, world):
    now = world.now
    for minutes_ago in (7, 6, 5, 4, 3, 2, 1):
        st = now - timedelta(minutes=minutes_ago)
        import asyncio
        asyncio.run(world.store.insert_segment(
            Segment(sd.TENANT_A, sd.CAM_A1, st, 60000, "x.mp4", "hook")))
    r = client.post(cam_url(sd.TENANT_A, sd.CAM_A1, "/playback"), headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 200
    body = r.json()
    claims = token_claims(world, body["token"])
    p = path_of(sd.TENANT_A, sd.CAM_A1)
    assert claims["mediamtx_permissions"] == [{"action": "playback", "path": p}]
    q = parse_qs(urlparse(body["url"]).query)
    assert q["path"] == [p]
    assert body["url"].startswith("http://localhost:9996/get?")
    assert body["duration_seconds"] == 300.0
    assert body["start"] == (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def test_playback_start_is_clipped_to_the_first_recorded_segment(client, world):
    import asyncio
    st = world.now - timedelta(seconds=100)
    asyncio.run(world.store.insert_segment(Segment(sd.TENANT_A, sd.CAM_A1, st, 60000, "x.mp4", "hook")))
    r = client.post(cam_url(sd.TENANT_A, sd.CAM_A1, "/playback"), headers=world.auth(sd.USER_A_VIEWER))
    assert r.json()["duration_seconds"] == 100.0


def test_playback_without_recording_is_404(client, world):
    r = client.post(cam_url(sd.TENANT_A, sd.CAM_A1, "/playback"), headers=world.auth(sd.USER_A_VIEWER))
    assert r.status_code == 404 and r.json() == {"detail": "no_recording"}


def test_camera_token_is_publish_for_its_own_path(client, world):
    from conftest import CAMERA_SECRETS
    r = client.post("/api/v1/camera/token", json={"camera_id": str(sd.CAM_B1), "secret": CAMERA_SECRETS[sd.CAM_B1]})
    assert r.status_code == 200
    claims = token_claims(world, r.json()["token"])
    assert claims["mediamtx_permissions"] == [{"action": "publish", "path": path_of(sd.TENANT_B, sd.CAM_B1)}]
    assert r.json()["path"] == path_of(sd.TENANT_B, sd.CAM_B1)


def test_camera_token_refuses_wrong_secret_and_unknown_camera_alike(client, world):
    from conftest import CAMERA_SECRETS
    a = client.post("/api/v1/camera/token", json={"camera_id": str(sd.CAM_B1), "secret": "nope"})
    b = client.post("/api/v1/camera/token", json={"camera_id": str(uuid.uuid4()), "secret": "nope"})
    c = client.post("/api/v1/camera/token", json={"camera_id": str(sd.CAM_B1),
                                                   "secret": CAMERA_SECRETS[sd.CAM_A1]})  # another camera's secret
    assert a.status_code == b.status_code == c.status_code == 401
    assert a.text == b.text == c.text


def test_jwks_endpoint(client, world):
    r = client.get("/jwks")
    assert r.status_code == 200
    assert r.json() == world.keys.jwks()
    k = r.json()["keys"][0]
    assert k["kid"] and k["alg"] == "ES256"
