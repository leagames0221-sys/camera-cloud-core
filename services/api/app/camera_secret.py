"""Camera secrets: high-entropy random strings, stored as a plain SHA-256 (a slow hash adds nothing to 256 random bits)."""
from __future__ import annotations

import hashlib
import hmac
import secrets


def new_camera_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret: str, stored_sha256: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), stored_sha256)
