#!/bin/sh
# Throwaway proof broker (Proof 3). Admin password for the proof only: poc-admin-pw
set -e
export MSYS_NO_PATHCONV=1
D="$(cd "$(dirname "$0")" && { pwd -W 2>/dev/null || pwd; })"
docker network create poc-net >/dev/null 2>&1 || true
sh "$D/ctl.sh" poc-mqtt
docker volume rm poc-mqtt-dynsec >/dev/null 2>&1 || true
docker volume create poc-mqtt-dynsec >/dev/null
docker run -d --name poc-mqtt --network poc-net -p 127.0.0.1:11883:1883 \
  -v poc-mqtt-dynsec:/mosquitto/dynsec -v "$D/mosquitto.poc.conf:/mosquitto/config/mosquitto.conf:ro" \
  --entrypoint sh eclipse-mosquitto:2.1.2-alpine -c '
    if [ ! -f /mosquitto/dynsec/dynamic-security.json ]; then
      mosquitto_ctrl dynsec init /mosquitto/dynsec/dynamic-security.json admin poc-admin-pw
      chown -R 1883:1883 /mosquitto/dynsec
    fi
    exec mosquitto -c /mosquitto/config/mosquitto.conf' >/dev/null
sleep 2
docker logs poc-mqtt 2>&1 | tail -10
