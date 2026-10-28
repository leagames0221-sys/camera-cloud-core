"""Checks the access token that Keycloak issued to the browser app.

What is checked: signature (against Keycloak's JWKS), `iss` equal to the fixed issuer string, `azp` equal to the
viewing client, `exp`, that `sub` is a UUID, and that the token is an access token. The algorithm list is fixed so a
token cannot choose its own.

Keycloak's ID token has `typ` "ID" where the access token has "Bearer", and its `aud` is the client id where the
access token has "account" (tests/unit/conftest.py). The API does not check `aud`. That the ID token also carries the
same `iss` and `azp` and the same signing key is the author's reading; the `typ` check is what refuses it. Without
that check the ID token, which the page also puts into the logout URL as `id_token_hint`, would open the API as well.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

ALLOWED_ALGS = ["RS256"]
ACCESS_TOKEN_TYP = "Bearer"  # the `typ` claim of a Keycloak access token (an ID token says "ID")


class AuthError(Exception):
    pass


class JwksCache:
    """Fetches the JWKS once, and again when an unknown `kid` shows up (at most every `min_interval` seconds)."""

    def __init__(self, fetch: Callable[[], Awaitable[dict]], min_interval: float = 30.0) -> None:
        self._fetch = fetch
        self._keys: dict[str, Any] = {}
        self._last = 0.0
        self._min_interval = min_interval
        self._lock = asyncio.Lock()

    async def key(self, kid: str):
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            if self._last == 0.0 or time.monotonic() - self._last >= self._min_interval:
                doc = await self._fetch()
                self._last = time.monotonic()
                self._keys = {k["kid"]: RSAAlgorithm.from_jwk(k) for k in doc.get("keys", [])
                              if k.get("kty") == "RSA" and k.get("use", "sig") == "sig" and "kid" in k}
            if kid not in self._keys:
                raise AuthError("unknown signing key")
            return self._keys[kid]


def http_jwks_fetcher(url: str, client: httpx.AsyncClient) -> Callable[[], Awaitable[dict]]:
    async def fetch() -> dict:
        r = await client.get(url, timeout=5.0)
        r.raise_for_status()
        return r.json()
    return fetch


class TokenVerifier:
    def __init__(self, jwks: JwksCache, issuer: str, client_id: str) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._client_id = client_id

    async def claims(self, token: str) -> dict:
        """The claims of a token that Keycloak signed for this realm: signature (fixed algorithm list), the fixed issuer,
        an expiry, a subject, and the type of an access token. Who the token was issued to is the caller's check."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError("malformed token") from e
        if header.get("alg") not in ALLOWED_ALGS or not header.get("kid"):
            raise AuthError("unexpected algorithm or no key id")
        try:
            key = await self._jwks.key(header["kid"])
        except AuthError:
            raise
        except Exception as e:  # network or parse problem: refuse, never accept
            raise AuthError("cannot load signing keys") from e
        try:
            claims = jwt.decode(token, key, algorithms=ALLOWED_ALGS, issuer=self._issuer,
                                options={"require": ["exp", "iss", "sub"], "verify_aud": False})
        except jwt.PyJWTError as e:
            raise AuthError("invalid token") from e
        if claims.get("typ") != ACCESS_TOKEN_TYP:
            raise AuthError("not an access token")
        return claims

    async def verify(self, token: str) -> uuid.UUID:
        """Return the user id (`sub`). Raise AuthError for anything else."""
        claims = await self.claims(token)
        if claims.get("azp") != self._client_id:
            raise AuthError("token was not issued to the viewing client")
        try:
            return uuid.UUID(str(claims["sub"]))
        except ValueError as e:
            raise AuthError("subject is not a UUID") from e


@dataclass(frozen=True)
class EnrollToken:
    user_id: uuid.UUID
    jti: str
    expires_at: datetime


class DeviceTokenVerifier:
    """The access token that a camera gets through the device authorization grant, accepted for one call only
    (registering the camera). It must have been issued to the device client and carry the registration audience; a token
    of the viewing client therefore does not pass here, and this one does not pass TokenVerifier.verify."""

    def __init__(self, base: TokenVerifier, client_id: str, audience: str) -> None:
        self._base, self._client_id, self._audience = base, client_id, audience

    async def verify(self, token: str) -> EnrollToken:
        claims = await self._base.claims(token)
        if claims.get("azp") != self._client_id:
            raise AuthError("token was not issued to the device client")
        aud = claims.get("aud")
        if self._audience not in ([aud] if isinstance(aud, str) else (aud or [])):
            raise AuthError("token is not for camera registration")
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti:
            raise AuthError("token has no id")
        try:
            return EnrollToken(uuid.UUID(str(claims["sub"])), jti, datetime.fromtimestamp(claims["exp"], tz=timezone.utc))
        except (ValueError, TypeError) as e:
            raise AuthError("bad subject or expiry") from e
