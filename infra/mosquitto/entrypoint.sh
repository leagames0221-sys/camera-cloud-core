#!/bin/sh
# Starts Mosquitto. On the very first start only, creates the dynamic-security file with `mosquitto_ctrl dynsec init`
# (Mosquitto 2.1 would otherwise write a default file that includes a demo user with access to every topic).
# The admin password comes from the file the init container wrote.
set -eu

DYNSEC=/mosquitto/dynsec/dynamic-security.json

if [ ! -f "$DYNSEC" ]; then
  PW="$(cat /run/secret/dynsec_admin_password)"
  mosquitto_ctrl dynsec init "$DYNSEC" admin "$PW"
  chown -R 1883:1883 /mosquitto/dynsec
  echo "dynamic security file created"
fi

exec mosquitto -c /mosquitto/config/mosquitto.conf
