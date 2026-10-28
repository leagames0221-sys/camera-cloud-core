# Simulated camera. Base = the official MediaMTX image that contains ffmpeg (ffmpeg is only called as a command;
# it is not part of this repository). Build context = repository root.
FROM bluenviron/mediamtx:1.21.2-ffmpeg@sha256:c0feb4acc548d5f410d57d9046f62dfa355e2abc4098f5243de9e86430c77a95

RUN apk add --no-cache python3

# paho-mqtt only, from the pinned list (its one line, with the hash).
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN grep -E '^paho-mqtt==' /tmp/requirements.lock.txt > /tmp/paho.txt \
 && python3 -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/paho.txt \
 && rm /tmp/requirements.lock.txt /tmp/paho.txt

COPY services/camera/camera.py /app/camera.py

# The base image's entrypoint is the MediaMTX server; replace it. PID 1 is Python so SIGTERM reaches the handler.
ENTRYPOINT ["/venv/bin/python", "-u", "/app/camera.py"]
