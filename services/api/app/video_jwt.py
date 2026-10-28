"""Video tokens: short ES256 JWTs that MediaMTX checks against our JWKS (ADR 0003).

Every token carries exp, iat, iss and aud (MediaMTX accepts a token without exp, so this code never omits it),
and exactly one permission: one action and, except for `api`, one path.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .config import TTL_API, TTL_PLAYBACK, TTL_PUBLISH, TTL_READ

ACTIONS = ("publish", "read", "playback", "api")
TTL_BY_ACTION = {"publish": TTL_PUBLISH, "read": TTL_READ, "playback": TTL_PLAYBACK, "api": TTL_API}
CLAIM_KEY = "mediamtx_permissions"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def generate_private_key_pem() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


class KeyStore:
    def __init__(self, private_pem: bytes) -> None:
        key = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
            raise ValueError("the signing key must be an EC P-256 key")
        self._key = key
        nums = key.public_key().public_numbers()
        x = _b64u(nums.x.to_bytes(32, "big"))
        y = _b64u(nums.y.to_bytes(32, "big"))
        # RFC 7638 thumbprint as the key id: it changes only if the key changes.
        canonical = json.dumps({"crv": "P-256", "kty": "EC", "x": x, "y": y}, separators=(",", ":"), sort_keys=True)
        self.kid = _b64u(hashlib.sha256(canonical.encode()).digest())
        self._jwk = {"kty": "EC", "crv": "P-256", "x": x, "y": y, "kid": self.kid, "alg": "ES256", "use": "sig"}

    @classmethod
    def from_file(cls, path: pathlib.Path) -> "KeyStore":
        return cls(path.read_bytes())

    def jwks(self) -> dict:
        return {"keys": [dict(self._jwk)]}

    def issue(self, *, action: str, path: str | None, subject: str, issuer: str, audience: str,
              ttl: int | None = None, now: float | None = None) -> tuple[str, int]:
        """Return (token, exp). `path` is required for every action except `api`."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        if action == "api":
            if path is not None:
                raise ValueError("an api token has no path")
        elif not path:
            raise ValueError(f"a {action} token needs exactly one path")
        iat = int(now if now is not None else time.time())
        exp = iat + (ttl if ttl is not None else TTL_BY_ACTION[action])
        perm: dict = {"action": action}
        if path is not None:
            perm["path"] = path
        claims = {"iss": issuer, "aud": audience, "sub": subject, "iat": iat, "exp": exp,
                  "jti": uuid.uuid4().hex, CLAIM_KEY: [perm]}
        token = jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self.kid})
        return token, exp
