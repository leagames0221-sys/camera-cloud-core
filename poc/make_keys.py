"""Proof helper: make an ES256 key, a JWKS file, and a helper to sign MediaMTX JWTs."""
import base64, json, sys, time, pathlib
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

OUT = pathlib.Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make(name="poc-key-1"):
    key = ec.generate_private_key(ec.SECP256R1())
    nums = key.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": name,
           "x": b64u(nums.x.to_bytes(32, "big")), "y": b64u(nums.y.to_bytes(32, "big"))}
    (OUT / "jwks.json").write_text(json.dumps({"keys": [jwk]}))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    (OUT / "poc_private.pem").write_bytes(pem)
    return key


def load():
    return serialization.load_pem_private_key((OUT / "poc_private.pem").read_bytes(), None)


def token(key, action, path=None, *, iss="http://api.poc", aud="mediamtx", ttl=300, kid="poc-key-1", exp=True):
    now = int(time.time())
    perm = {"action": action}
    if path is not None:
        perm["path"] = path
    claims = {"iss": iss, "aud": aud, "sub": "poc", "iat": now, "mediamtx_permissions": [perm]}
    if exp:
        claims["exp"] = now + ttl
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": kid})


if __name__ == "__main__":
    make()
    print("ok")
