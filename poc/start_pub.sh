#!/bin/sh
# Throwaway proof publisher: loops a short mp4 into MediaMTX over RTSP/TCP with a publish JWT.
# usage: start_pub.sh NAME PATH
set -e
export MSYS_NO_PATHCONV=1
NAME="$1"
P="$2"
D="$(cd "$(dirname "$0")" && { pwd -W 2>/dev/null || pwd; })"
sh "$D/ctl.sh" "$NAME"
TOK=$("$D/../.venv/Scripts/python" -c "
import sys
sys.path.insert(0, '$D')
import make_keys as m
print(m.token(m.load(), 'publish', '$P', ttl=3600))")
docker run -d --name "$NAME" --network poc-net -v "$D/out:/data:ro" --entrypoint ffmpeg bluenviron/mediamtx:1.21.2-ffmpeg \
  -loglevel info -re -stream_loop -1 -i /data/test.mp4 -c copy -f rtsp -rtsp_transport tcp "rtsp://poc-mtx:8554/$P?token=$TOK" >/dev/null
echo started "$NAME"
