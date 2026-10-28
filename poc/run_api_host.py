"""Smoke helper: runs the API on the host against the poc-* containers. aiomqtt needs the selector loop on Windows."""
import asyncio
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

# the throw-away connection string is written by poc/smoke_up.sh into a git-ignored file
os.environ.update({
    "DATABASE_URL": (ROOT / "poc" / "out" / "database_url").read_text().strip(),
    "SECRETS_DIR": str(ROOT / "poc" / "out" / "secrets" / "api"),
    "OIDC_JWKS_URL": "http://localhost:8080/realms/ccc/protocol/openid-connect/certs",
    "MEDIAMTX_CONTROL_URL": "http://127.0.0.1:19997",
    "PUBLIC_HLS_URL": "http://localhost:18888",
    "PUBLIC_PLAYBACK_URL": "http://localhost:19996",
    "MQTT_HOST": "127.0.0.1",
    "MQTT_PORT": "11883",
    "RECONCILE_INTERVAL": "20",
})

if __name__ == "__main__":
    import uvicorn
    from app.main import build_app

    server = uvicorn.Server(uvicorn.Config(build_app, factory=True, host="127.0.0.1", port=8000, log_level="info"))
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
