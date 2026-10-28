"""Simulated camera: asks the API for a publish token, streams a short looped clip to MediaMTX with ffmpeg
(`-c copy`, so nothing is re-encoded), and reports its state over MQTT 5.0.

Runs inside the MediaMTX `-ffmpeg` image (ffmpeg is called as a command; it is not part of this repository).
The token is part of the RTSP URL, so ffmpeg's output is filtered before it is printed.

With SOURCE_FILE=<a video file> the camera streams that file, again and again, instead of its own test pattern. It works out
when each round of the file started (from the position ffmpeg reports for its output) and says so in the log and in its state
message (`lap_started_at`), so that a time in the recording can be turned back into a second of the file.
With CRED_FILE=<a JSON file> the camera takes its id, secret and addresses from that file (waiting until it exists), the way
a camera that registered itself keeps them.

With ENROLL=device the camera has no secret to start with. It asks Keycloak for a code (device authorization grant,
RFC 8628), prints the code and the address where a tenant administrator enters it, waits, and when the administrator has
approved it registers itself once with the access token it gets, throws the token away and keeps only the camera id and
the secret that the registration returns. After that it works like any other camera.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KEEPALIVE = 60           # paho-mqtt's default for `connect(keepalive=...)`; the broker gives up on silence after 1.5 times this (docs/adr/0005)
HEARTBEAT_SECONDS = 60   # Frigate's `mqtt.stats_interval` default (frigate/config/mqtt.py, v0.18.2); docs/adr/0005
LOOP_FILE = "/tmp/loop.mp4"
LOOP_SECONDS = 20

_TOKEN_RE = re.compile(r"(token=)[^&\s'\"]+")


def redact(line: str) -> str:
    """Remove the video token from any text that may be printed."""
    return _TOKEN_RE.sub(r"\1***", line)


def state_topic(camera_id: str) -> str:
    return f"cameras/{camera_id}/state"


def online_payload(fps: float | None = None, bitrate: float | None = None, lap_started_at: str | None = None) -> str:
    body: dict = {"state": "online"}
    if fps is not None:
        body["fps"] = round(fps, 2)
    if bitrate is not None:
        body["bitrate"] = round(bitrate, 2)
    if lap_started_at is not None:
        body["lap_started_at"] = lap_started_at
    return json.dumps(body, separators=(",", ":"))


def offline_payload(reason: str) -> str:
    return json.dumps({"state": "offline", "reason": reason}, separators=(",", ":"))


def parse_progress(line: str, stats: dict) -> None:
    """ffmpeg `-progress` lines are key=value. Keep the latest fps and bitrate (kbit/s)."""
    key, _, value = line.strip().partition("=")
    value = value.strip()
    if key == "fps":
        try:
            stats["fps"] = float(value)
        except ValueError:
            pass
    elif key == "bitrate":
        m = re.match(r"([0-9.]+)kbits/s", value)
        if m:
            stats["bitrate"] = float(m.group(1))
    elif key == "out_time_us":
        try:
            stats["out_time"] = int(value) / 1_000_000       # where the output is, in seconds (N/A before the first packet)
        except ValueError:
            pass


class LapClock:
    """When each round of a looped file started, in wall-clock time. ffmpeg's output position (out_time) keeps counting
    across the rounds, so the round it is in is the position divided by the file's length, and the round began that many
    seconds before `now` as the remainder says."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.lap: int | None = None
        self.started_at: datetime | None = None

    def update(self, out_time: float, now: datetime) -> bool:
        """True when a new round has begun (its start is then in `started_at`)."""
        lap = int(out_time // self.seconds)
        if lap == self.lap:
            return False
        self.lap = lap
        self.started_at = now - timedelta(seconds=out_time - lap * self.seconds)
        return True


def media_seconds(path: str) -> float | None:
    """The length of a media file, by ffprobe."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fetch_publish_token(gateway: str, camera_id: str, secret: str) -> dict:
    req = urllib.request.Request(
        f"{gateway}/api/v1/camera/token", method="POST",
        data=json.dumps({"camera_id": camera_id, "secret": secret}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ---- registration by the device authorization grant (RFC 8628)

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SLOW_DOWN_STEP = 5   # seconds added to the polling interval after `slow_down` (RFC 8628 section 3.5)


class EnrollRefused(Exception):
    """The administrator said no, or the code ran out, or Keycloak or the API answered something unexpected."""


def post_form(url: str, form: dict, timeout: float = 15.0) -> tuple[int, dict]:
    """(status, JSON body) of a form POST; an error status is returned, not raised."""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def poll_for_token(post, token_url: str, client_id: str, device: dict, sleep=time.sleep, now=time.monotonic) -> str:
    """Ask for the token every `interval` seconds (5 if Keycloak did not say) until it is given. `authorization_pending`
    = keep waiting; `slow_down` = wait 5 seconds longer from now on; `access_denied` and `expired_token` end it."""
    interval = float(device.get("interval") or 5)
    deadline = now() + float(device.get("expires_in") or 600)
    while now() < deadline:
        sleep(interval)
        status, body = post(token_url, {"grant_type": DEVICE_GRANT, "device_code": device["device_code"],
                                        "client_id": client_id})
        if status == 200 and body.get("access_token"):
            return body["access_token"]
        err = body.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += SLOW_DOWN_STEP
            continue
        raise EnrollRefused(err or f"unexpected answer {status}")
    raise EnrollRefused("expired_token")


def enroll_with_device_grant(keycloak: str, realm: str, client_id: str, gateway: str, name: str, post=post_form,
                             sleep=time.sleep, say=print) -> dict:
    """Run the whole flow and return the registration answer (camera id, secret, addresses). The access token lives in
    this function only."""
    base = f"{keycloak}/realms/{realm}/protocol/openid-connect"
    status, device = post(f"{base}/auth/device", {"client_id": client_id})
    if status != 200 or "device_code" not in device:
        raise EnrollRefused(f"Keycloak did not start the registration ({status})")
    say(f"To add this camera, open {device.get('verification_uri')} and enter the code {device.get('user_code')}",
        flush=True)
    token = poll_for_token(post, f"{base}/token", client_id, device, sleep=sleep)
    req = urllib.request.Request(f"{gateway}/api/v1/devices/enroll", method="POST",
                                 data=json.dumps({"name": name}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    del token
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise EnrollRefused(f"the API refused the registration ({e.code})") from None


def split_urls(answer: dict) -> tuple[str, str, int, str]:
    """(camera id, MQTT host, MQTT port, RTSP base) from the registration answer."""
    mqtt = urllib.parse.urlparse(answer["mqtt_url"])
    rtsp = answer["mediamtx_url"].rsplit("/", 2)[0]     # .../<tenant>/<camera> -> the server's address
    return answer["id"], mqtt.hostname, mqtt.port or 1883, rtsp


def load_or_enroll(data_dir: str, keycloak: str, realm: str, client_id: str, gateway: str, name: str) -> dict:
    """The camera's own credentials from its volume; if there are none yet, register and keep them (owner only)."""
    path = os.path.join(data_dir, "credentials.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        pass
    answer = enroll_with_device_grant(keycloak, realm, client_id, gateway, name)
    cid, host, port, rtsp = split_urls(answer)
    creds = {"camera_id": cid, "secret": answer["secret"], "mqtt_host": host, "mqtt_port": port, "rtsp_base": rtsp}
    os.makedirs(data_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(creds, f)
    print(f"registered as camera {cid}", flush=True)
    return creds


def make_loop_clip() -> None:
    """Encode once: 15 fps test pattern, a key frame every 2 seconds (Apple's HLS authoring specification: "Key frames (IDRs)
    SHOULD be present every two seconds."), 20 seconds long."""
    if os.path.exists(LOOP_FILE):
        return
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=15",
                    "-t", str(LOOP_SECONDS), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-g", "30",
                    "-keyint_min", "30", "-sc_threshold", "0", "-profile:v", "baseline", LOOP_FILE], check=True)


def clip_bitrate_kbps(path: str = LOOP_FILE, seconds: int = LOOP_SECONDS) -> float | None:
    """Average bit rate of the looped clip. ffmpeg reports `bitrate=N/A` for an RTSP output: in ffmpeg n8.1.2 the RTSP
    muxer has the flag AVFMT_NOFILE (libavformat/rtspenc.c), so there is no output file; `filesize()` in
    fftools/ffmpeg_mux.c then returns -1, and print_report() in fftools/ffmpeg.c prints `bitrate=N/A` when the size is
    below 0. With `-c copy` the stream carries exactly the clip's bits, so the clip's average is the stream's."""
    try:
        return os.path.getsize(path) * 8 / seconds / 1000
    except OSError:
        return None


def load_credentials_file(path: str, sleep=time.sleep, say=print) -> dict:
    """The camera's own credentials from a JSON file (camera_id, secret, mqtt_host, mqtt_port, rtsp_base). The file is
    written after the camera was registered, which may be after this container started, so wait for it."""
    shown = False
    while True:
        try:
            with open(path, encoding="utf-8") as f:
                c = json.load(f)
            if all(k in c for k in ("camera_id", "secret", "mqtt_host", "mqtt_port", "rtsp_base")):
                return c
        except (OSError, ValueError):
            pass
        if not shown:
            say(f"waiting for {path}", flush=True)
            shown = True
        sleep(5)


class Camera:
    def __init__(self, camera_id: str, secret: str, gateway: str, mqtt_host: str, mqtt_port: int, rtsp_base: str,
                 source_file: str | None = None):
        self.id = camera_id
        self.secret = secret
        self.gateway = gateway
        self.mqtt_host, self.mqtt_port = mqtt_host, mqtt_port
        self.rtsp_base = rtsp_base
        self.source_file = source_file
        self.source_seconds = (media_seconds(source_file) if source_file else None) or LOOP_SECONDS
        self.laps = LapClock(self.source_seconds)
        self.stats: dict = {}
        self.stop = threading.Event()
        self.ffmpeg: subprocess.Popen | None = None
        self.mqtt = None

    # ---- MQTT
    def start_mqtt(self) -> None:
        import paho.mqtt.client as mqtt
        from paho.mqtt.enums import CallbackAPIVersion

        c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=self.id, protocol=mqtt.MQTTv5)
        c.username_pw_set(self.id, self.secret)
        # the Will is retained: whoever subscribes later still learns the camera is gone
        c.will_set(state_topic(self.id), offline_payload("will"), qos=1, retain=True)
        c.reconnect_delay_set(min_delay=1, max_delay=10)

        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                print(f"mqtt connect refused: {reason_code}", flush=True)
                return
            client.publish(state_topic(self.id), online_payload(), qos=1, retain=True)

        c.on_connect = on_connect
        c.connect_async(self.mqtt_host, self.mqtt_port, keepalive=KEEPALIVE)
        c.loop_start()
        self.mqtt = c

    def heartbeat(self) -> None:
        while not self.stop.wait(HEARTBEAT_SECONDS):
            if self.mqtt is not None and self.mqtt.is_connected():
                bitrate = self.stats.get("bitrate") or clip_bitrate_kbps(self.source_file or LOOP_FILE,
                                                                         self.source_seconds)
                lap = iso_utc(self.laps.started_at) if self.source_file and self.laps.started_at else None
                self.mqtt.publish(state_topic(self.id), online_payload(self.stats.get("fps"), bitrate, lap),
                                  qos=1, retain=True)

    # ---- video
    def run_ffmpeg_once(self) -> int:
        tok = fetch_publish_token(self.gateway, self.id, self.secret)
        url = f"{self.rtsp_base}/{tok['path']}?token={tok['token']}"
        cmd = ["ffmpeg", "-nostdin", "-nostats", "-loglevel", "warning", "-progress", "pipe:1", "-re",
               "-stream_loop", "-1", "-i", self.source_file or LOOP_FILE, "-c", "copy", "-f", "rtsp",
               "-rtsp_transport", "tcp", url]
        self.ffmpeg = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.laps = LapClock(self.source_seconds)     # a new ffmpeg counts its output position from 0 again

        def pump() -> None:
            assert self.ffmpeg and self.ffmpeg.stdout
            for line in self.ffmpeg.stdout:
                if re.match(r"^[a-z_0-9]+=", line.strip()) and " " not in line.strip().split("=", 1)[0]:
                    parse_progress(line, self.stats)
                    if self.source_file and line.startswith("progress=") and "out_time" in self.stats:
                        if self.laps.update(self.stats["out_time"], datetime.now(timezone.utc)):
                            print(f"lap {self.laps.lap} started_at {iso_utc(self.laps.started_at)}", flush=True)
                else:
                    print(redact(line.rstrip()), flush=True)

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        rc = self.ffmpeg.wait()
        t.join(timeout=2)
        return rc

    def video_loop(self) -> None:
        delay = 1.0
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                rc = self.run_ffmpeg_once()
                print(f"ffmpeg exited with {rc}", flush=True)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                print(f"cannot start the stream: {redact(str(e))}", flush=True)
            if self.stop.is_set():
                return
            delay = 1.0 if time.monotonic() - started > 30 else min(delay * 2, 15.0)
            self.stop.wait(delay)

    # ---- shutdown
    def shutdown(self, *_: object) -> None:
        if self.stop.is_set():
            return
        self.stop.set()
        # A clean stop: say so ourselves (the broker discards the Will after a normal DISCONNECT).
        if self.mqtt is not None and self.mqtt.is_connected():
            info = self.mqtt.publish(state_topic(self.id), offline_payload("normal"), qos=1, retain=True)
            info.wait_for_publish(timeout=3)
            self.mqtt.disconnect()
            self.mqtt.loop_stop()
        if self.ffmpeg is not None and self.ffmpeg.poll() is None:
            self.ffmpeg.terminate()


def main() -> int:
    gateway = os.environ.get("GATEWAY_URL", "http://gateway:8000")
    source_file = os.environ.get("SOURCE_FILE") or None
    if os.environ.get("CRED_FILE"):
        c = load_credentials_file(os.environ["CRED_FILE"])
        cam = Camera(c["camera_id"], c["secret"], gateway, c["mqtt_host"], c["mqtt_port"], c["rtsp_base"], source_file)
    elif os.environ.get("ENROLL") == "device":
        try:
            c = load_or_enroll(os.environ.get("DATA_DIR", "/data"),
                               os.environ.get("KEYCLOAK_URL", "http://keycloak:8080"),
                               os.environ.get("KEYCLOAK_REALM", "ccc"), os.environ.get("DEVICE_CLIENT_ID", "ccc-device"),
                               gateway, os.environ.get("DEVICE_NAME", "Device camera"))
        except EnrollRefused as e:
            print(f"registration stopped: {e}", flush=True)
            return 1
        cam = Camera(c["camera_id"], c["secret"], gateway, c["mqtt_host"], c["mqtt_port"], c["rtsp_base"], source_file)
    else:
        camera_id = os.environ["CAMERA_ID"]
        with open(os.environ.get("SECRET_FILE", "/run/secret/secret"), encoding="utf-8") as f:
            secret = f.read().strip()
        cam = Camera(camera_id, secret, gateway, os.environ.get("MQTT_HOST", "mosquitto"),
                     int(os.environ.get("MQTT_PORT", "1883")), os.environ.get("RTSP_BASE", "rtsp://mediamtx:8554"),
                     source_file)
    signal.signal(signal.SIGTERM, cam.shutdown)
    signal.signal(signal.SIGINT, cam.shutdown)
    if not source_file:
        make_loop_clip()
    cam.start_mqtt()
    threading.Thread(target=cam.heartbeat, daemon=True).start()
    cam.video_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
