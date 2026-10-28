"""Settings read from the environment. Secrets are read from files in a shared volume, not from the environment."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

# Lifetimes of the video tokens, in seconds (see ADR 0003): all four are Keycloak's default access token lifespan
# (300 s, `Constants.DEFAULT_ACCESS_TOKEN_LIFESPAN` of Keycloak 26.8.1), the life of the publish token in MediaMTX's
# authentication guide (`"expires_in": 300`).
TTL_PUBLISH = 300
TTL_READ = 300
TTL_PLAYBACK = 300
TTL_API = 300

PLAYBACK_WINDOW_SECONDS = 300
SEGMENT_SECONDS = 60
# A listed segment younger than this may still be open, so reconciliation leaves it to the hook. 90 is the author's design decision; no model was found
# (docs/adr/0005).
RECONCILE_GRACE_SECONDS = 90


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    secrets_dir: pathlib.Path = pathlib.Path("/run/ccc")
    oidc_issuer: str = "http://localhost:8080/realms/ccc"
    oidc_jwks_url: str = "http://keycloak:8080/realms/ccc/protocol/openid-connect/certs"
    oidc_client_id: str = "ccc-web"
    video_issuer: str = "ccc-api"
    video_audience: str = "mediamtx"
    mediamtx_control_url: str = "http://mediamtx:9997"
    mediamtx_rtsp_host: str = "mediamtx:8554"
    public_hls_url: str = "http://localhost:8888"
    public_playback_url: str = "http://localhost:9996"
    public_mqtt_url: str = "mqtt://mosquitto:1883"
    public_rtsp_url: str = "rtsp://mediamtx:8554"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    seed: bool = True
    background: bool = True
    # ZoneMinder's ZM_AUDIT_CHECK_INTERVAL: its audit daemon matches the database with the files on disk every 900 s by
    # default ("The default check interval of 900 seconds (15 minutes) is fine for most systems"; docs/adr/0005).
    reconcile_interval: float = 900.0
    # The MQTT intake runs inside the API only in the first version; with this off, the writer of each stamp does it.
    ingest: bool = True
    # Calls per second one tenant may make to the API; 0 = no limit.
    tenant_rate: int = 0
    # A JSON file listing the stamps (see stamps.py); empty = one stamp made from the settings above.
    stamps_file: str = ""
    # The address of the first stamp's writer, where the reconciliation tells about the segments it added (so that the motion
    # worker looks at them too). Empty = nothing is told.
    announce_url: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        d = cls()
        return cls(
            database_url=e["DATABASE_URL"],
            secrets_dir=pathlib.Path(e.get("SECRETS_DIR", str(d.secrets_dir))),
            oidc_issuer=e.get("OIDC_ISSUER", d.oidc_issuer),
            oidc_jwks_url=e.get("OIDC_JWKS_URL", d.oidc_jwks_url),
            oidc_client_id=e.get("OIDC_CLIENT_ID", d.oidc_client_id),
            mediamtx_control_url=e.get("MEDIAMTX_CONTROL_URL", d.mediamtx_control_url),
            public_hls_url=e.get("PUBLIC_HLS_URL", d.public_hls_url),
            public_playback_url=e.get("PUBLIC_PLAYBACK_URL", d.public_playback_url),
            public_mqtt_url=e.get("PUBLIC_MQTT_URL", d.public_mqtt_url),
            public_rtsp_url=e.get("PUBLIC_RTSP_URL", d.public_rtsp_url),
            mqtt_host=e.get("MQTT_HOST", d.mqtt_host),
            mqtt_port=int(e.get("MQTT_PORT", d.mqtt_port)),
            reconcile_interval=float(e.get("RECONCILE_INTERVAL", d.reconcile_interval)),
            background=e.get("BACKGROUND", "true").lower() not in ("0", "false", "no"),
            ingest=e.get("INGEST", "true").lower() not in ("0", "false", "no"),
            tenant_rate=int(e.get("TENANT_RATE", d.tenant_rate)),
            stamps_file=e.get("STAMPS_FILE", d.stamps_file),
            announce_url=e.get("ANNOUNCE_URL", d.announce_url),
        )

    def secret(self, name: str) -> str:
        """Read one secret file written by the init container. Raises if it does not exist."""
        return (self.secrets_dir / name).read_text(encoding="utf-8").strip()
