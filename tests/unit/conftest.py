"""Fixtures for the unit tests: the real FastAPI app on an in-memory store, with real token checking."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app import seed_data as sd
from app.api import Deps, create_app
from app.camera_secret import hash_secret
from app.config import Settings
from app.domain import Membership
from app.oidc import JwksCache, TokenVerifier
from app.store import MemoryStore
from app.video_jwt import KeyStore, generate_private_key_pem

ISSUER = "http://localhost:8080/realms/ccc"
CLIENT = "ccc-web"
HOOK_SECRET = "hook-secret-for-tests"
CAMERA_SECRETS = {sd.CAM_A1: "secret-a1", sd.CAM_A2: "secret-a2", sd.CAM_B1: "secret-b1"}


class FakeMediaMtx:
    def __init__(self) -> None:
        self.ready: set[str] = set()

    async def ready_paths(self) -> set[str]:
        return set(self.ready)


class FakeDynsec:
    def __init__(self) -> None:
        self.added: list[tuple[uuid.UUID, str]] = []
        self.removed: list[uuid.UUID] = []
        self.fail = False

    async def add_camera(self, camera_id, secret, force_password=False):
        if self.fail:
            raise RuntimeError("broker down")
        self.added.append((camera_id, secret))

    async def remove_camera(self, camera_id):
        self.removed.append(camera_id)


class World:
    def __init__(self) -> None:
        self.store = MemoryStore()
        self.mediamtx = FakeMediaMtx()
        self.dynsec = FakeDynsec()
        self.keys = KeyStore(generate_private_key_pem())
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = RSAAlgorithm.to_jwk(self.rsa.public_key(), as_dict=True)
        jwk.update({"kid": "kc1", "use": "sig", "alg": "RS256"})

        async def fetch() -> dict:
            return {"keys": [jwk]}

        self.verifier = TokenVerifier(JwksCache(fetch), ISSUER, CLIENT)
        self.settings = Settings(database_url="", background=False)
        self.now = datetime(2026, 10, 24, 9, 0, 0, tzinfo=timezone.utc)
        asyncio.run(self._seed())

    async def _seed(self) -> None:
        s = self.store
        await s.seed_partner(sd.PARTNER_ID, sd.PARTNER_NAME)
        for tid, name in sd.TENANTS:
            await s.seed_tenant(tid, sd.PARTNER_ID, name)
        for u, role, pid, tid in sd.MEMBERSHIPS:
            await s.add_membership(Membership(u, role, pid, tid))
        for cid, tid, name in sd.CAMERAS:
            await s.seed_camera(cid, tid, name, hash_secret(CAMERA_SECRETS[cid]))

    def token(self, user: uuid.UUID, *, iss: str = ISSUER, azp: str = CLIENT, exp_in: int = 300,
              kid: str = "kc1", key=None, alg: str = "RS256", typ: str | None = "Bearer") -> str:
        # the claims Keycloak puts into an access token (an ID token has typ "ID" and aud = the client)
        claims = {"iss": iss, "sub": str(user), "azp": azp, "exp": int(time.time()) + exp_in, "aud": "account"}
        if typ is not None:
            claims["typ"] = typ
        return jwt.encode(claims, key or self.rsa, algorithm=alg, headers={"kid": kid})

    def auth(self, user: uuid.UUID, **kw) -> dict:
        return {"Authorization": f"Bearer {self.token(user, **kw)}"}

    def deps(self) -> Deps:
        return Deps(self.settings, self.store, self.verifier, self.keys, self.mediamtx, self.dynsec, HOOK_SECRET,
                    now=lambda: self.now)


@pytest.fixture
def world():
    return World()


@pytest.fixture
def client(world):
    async def factory() -> Deps:
        return world.deps()

    app = create_app(world.settings, factory)
    with TestClient(app) as c:
        yield c
