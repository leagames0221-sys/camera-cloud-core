"""The Keycloak access token check: signature, fixed issuer, viewing client, expiry, fixed algorithms."""
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import seed_data as sd
from app.oidc import AuthError, JwksCache, TokenVerifier
from conftest import CLIENT, ISSUER


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_good_token_gives_the_subject(world):
    assert run(world.verifier.verify(world.token(sd.USER_A_VIEWER))) == sd.USER_A_VIEWER


@pytest.mark.parametrize("kw", [
    {"iss": "http://keycloak:8080/realms/ccc"},   # the back-channel name is not the fixed issuer string
    {"iss": "http://localhost:8080/realms/master"},
    {"azp": "account"},
    {"azp": "ccc-other"},
    {"exp_in": -5},
    {"kid": "unknown-key"},
    {"typ": "ID"},        # Keycloak's ID token (it also differs in aud, which the verifier does not check)
    {"typ": "Refresh"},
    {"typ": None},
])
def test_bad_tokens_are_refused(world, kw):
    with pytest.raises(AuthError):
        run(world.verifier.verify(world.token(sd.USER_A_VIEWER, **kw)))


def test_token_signed_with_another_key_is_refused(world):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        run(world.verifier.verify(world.token(sd.USER_A_VIEWER, key=other)))


def test_alg_none_and_hmac_are_refused(world):
    claims = {"iss": ISSUER, "sub": str(sd.USER_A_VIEWER), "azp": CLIENT, "typ": "Bearer",
              "exp": int(time.time()) + 100}
    none_token = jwt.encode(claims, key=None, algorithm="none", headers={"kid": "kc1"})
    hs_token = jwt.encode(claims, key="x" * 40, algorithm="HS256", headers={"kid": "kc1"})
    for t in (none_token, hs_token, "garbage", ""):
        with pytest.raises(AuthError):
            run(world.verifier.verify(t))


def test_missing_exp_and_non_uuid_subject_are_refused(world):
    base = {"iss": ISSUER, "azp": CLIENT, "typ": "Bearer"}  # so each token is refused only for the part named
    no_exp = jwt.encode({**base, "sub": str(sd.USER_A_VIEWER)}, world.rsa, algorithm="RS256", headers={"kid": "kc1"})
    bad_sub = jwt.encode({**base, "sub": "alice", "exp": int(time.time()) + 100}, world.rsa, algorithm="RS256",
                         headers={"kid": "kc1"})
    for t in (no_exp, bad_sub):
        with pytest.raises(AuthError):
            run(world.verifier.verify(t))


def test_failure_to_load_keys_refuses_instead_of_accepting(world):
    async def broken():
        raise RuntimeError("keycloak down")

    v = TokenVerifier(JwksCache(broken), ISSUER, CLIENT)
    with pytest.raises(AuthError):
        run(v.verify(world.token(sd.USER_A_VIEWER)))


def test_jwks_is_refetched_at_most_once_per_interval():
    calls = []

    async def fetch():
        calls.append(1)
        return {"keys": []}

    cache = JwksCache(fetch, min_interval=1000)

    async def go():
        for _ in range(3):
            with pytest.raises(AuthError):
                await cache.key("nope")

    run(go())
    assert len(calls) == 1


def test_a_bearer_header_is_required_in_the_api(client, world):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer "}).status_code == 401
    bad = world.auth(sd.USER_A_ADMIN, azp="account")
    assert client.get("/api/v1/me", headers=bad).status_code == 401
    assert client.get("/api/v1/me", headers=world.auth(sd.USER_A_ADMIN, typ="ID")).status_code == 401
    assert client.get("/api/v1/me", headers=world.auth(uuid.uuid4())).status_code == 200
