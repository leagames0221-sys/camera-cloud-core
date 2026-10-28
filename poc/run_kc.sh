#!/bin/sh
# Throwaway Keycloak with the real realm file (checks the import, the flags and the host name). Proof values only.
set -e
export MSYS_NO_PATHCONV=1
R="$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })"
sh "$R/poc/ctl.sh" poc-kc
docker run -d --name poc-kc --memory 1g -p 127.0.0.1:8080:8080 -p 127.0.0.1:9000:9000 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=poc-admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=poc-admin-pw-1234 \
  -e KC_HOSTNAME=http://localhost:8080 -e KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true -e KC_HEALTH_ENABLED=true \
  -e DEMO_USER_PASSWORD=poc-demo-pw-1234 \
  -v "$R/infra/keycloak/ccc-realm.json:/opt/keycloak/data/import/ccc-realm.json:ro" \
  quay.io/keycloak/keycloak:26.8.1 start-dev --import-realm --features-disabled=impersonation,token-exchange-standard >/dev/null
echo started
