#!/bin/sh
# Throwaway smoke stack: the REAL config files of the repository, started with plain `docker run` (no build).
# The API itself runs on the host (poc/run_api_host.py) and the containers reach it as `api` via host-gateway.
set -e
export MSYS_NO_PATHCONV=1
R="$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })"
PY="$R/.venv/Scripts/python"
S="$R/poc/out/secrets"
mkdir -p "$S"
"$PY" -I -c "
import sys, pathlib
sys.path.insert(0, r'$R/services/api')
from app.init_secrets import init_secrets
print(init_secrets(pathlib.Path(r'$S')))"

docker network create poc-net >/dev/null 2>&1 || true
for c in poc-pg poc-mqtt poc-mtx; do sh "$R/poc/ctl.sh" "$c"; done
docker volume rm poc-mqtt-dynsec >/dev/null 2>&1 || true
docker volume create poc-mqtt-dynsec >/dev/null

PGPW="$("$PY" -c "import secrets; print(secrets.token_hex(12))")"
# (assembled from parts: the value is generated here, nothing secret is written in this file)
printf '%s://%s:%s@%s/%s' postgresql ccc "$PGPW" 127.0.0.1:15432 ccc > "$R/poc/out/database_url"
docker run -d --name poc-pg --network poc-net -e POSTGRES_USER=ccc -e POSTGRES_PASSWORD="$PGPW" -e POSTGRES_DB=ccc \
  -p 127.0.0.1:15432:5432 postgres:17.11-alpine >/dev/null

docker run -d --name poc-mqtt --network poc-net -p 127.0.0.1:11883:1883 \
  -v poc-mqtt-dynsec:/mosquitto/dynsec \
  -v "$R/infra/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  -v "$R/infra/mosquitto/entrypoint.sh:/entrypoint.sh:ro" \
  -v "$S/mosquitto:/run/secret:ro" \
  --entrypoint sh eclipse-mosquitto:2.1.2-alpine /entrypoint.sh >/dev/null

docker run -d --name poc-mtx --network poc-net --add-host api:host-gateway \
  -v "$R/infra/mediamtx/mediamtx.yml:/mediamtx.yml:ro" -v "$S/mediamtx:/run/secret:ro" \
  -v "$R/infra/mediamtx/segment-hook.sh:/segment-hook.sh:ro" \
  -p 127.0.0.1:18888:8888 -p 127.0.0.1:19996:9996 -p 127.0.0.1:19997:9997 -p 127.0.0.1:18554:8554 \
  bluenviron/mediamtx:1.21.2-ffmpeg >/dev/null
echo started
