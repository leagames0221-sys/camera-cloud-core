#!/bin/sh
# Throwaway proof stack. Only containers/networks named poc-* are touched.
set -e
export MSYS_NO_PATHCONV=1
D="$(cd "$(dirname "$0")" && { pwd -W 2>/dev/null || pwd; })"
docker network create poc-net >/dev/null 2>&1 || true
for c in poc-jwks poc-hook poc-mtx; do
  docker rm -f "$c" >/dev/null 2>&1 || true
done
docker run -d --name poc-jwks --network poc-net -v "$D/out:/data:ro" -w /data python:3.12-slim python -m http.server 8000 >/dev/null
docker run -d --name poc-hook --network poc-net -v "$D/out:/data" -v "$D/hook_recv.py:/hook_recv.py:ro" python:3.12-slim python /hook_recv.py >/dev/null
docker run -d --name poc-mtx --network poc-net -v "$D/mediamtx.poc.yml:/mediamtx.yml:ro" -v "$D/out:/data" \
  -p 127.0.0.1:18888:8888 -p 127.0.0.1:19996:9996 -p 127.0.0.1:19997:9997 -p 127.0.0.1:18554:8554 \
  bluenviron/mediamtx:1.21.2-ffmpeg >/dev/null
echo started
