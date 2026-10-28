"""Load shape for the API and the two intake paths (docs/validation/load_conditions.md).

  ViewerUser   50 users, 10 for each of the five load tenants, one request per second each, in turn: camera list,
               one camera, segment list. They log in as the realm's l-viewer.
  SegmentUser  20 users, 5 calls per second each: the closed-segment call that MediaMTX's hook makes.
  CameraUser   300 users, one per load camera, one state message per second each (QoS 1, retained, like the simulated
               cameras). This is Locust's own MQTT user (locust.contrib.mqtt).

Each user takes its number from the files in the claims folder (common.claim), so the numbers are right whatever the
number of Locust processes. RUN_ID must be set and different for every run.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone

import gevent
import gevent.lock
import paho.mqtt.client as mqtt
from locust import FastHttpUser, constant_throughput, task
from locust.contrib.mqtt import MqttClient, MqttUser

import common as c

DATA = c.load_data()
LOAD_TENANTS = [t for t in DATA["tenants"] if t["name"] != c.PROBE_TENANT_NAME]
ALL_CAMERAS = [(t["id"], cam) for t in LOAD_TENANTS for cam in t["cameras"]]
assert len(ALL_CAMERAS) == c.MQTT_CAMERAS

_viewer_token = c.TokenCache("l-viewer")
_token_lock = gevent.lock.Semaphore()


def viewer_headers() -> dict:
    with _token_lock:  # one login at a time, not one per user
        return {"Authorization": f"Bearer {_viewer_token.get()}"}


class ViewerUser(FastHttpUser):
    host = c.GATEWAY
    fixed_count = c.VIEWERS
    wait_time = constant_throughput(1)

    def on_start(self) -> None:
        n = c.claim("viewer", c.VIEWERS)
        self.tenant = LOAD_TENANTS[n // (c.VIEWERS // len(LOAD_TENANTS))]
        self.turn = n  # the three calls start at different places so they are spread out

    @task
    def view(self) -> None:
        t = self.tenant
        base = f"/api/v1/tenants/{t['id']}/cameras"
        what = self.turn % 3
        self.turn += 1
        h = viewer_headers()
        if what == 0:
            self.client.get(base, headers=h, name="GET /tenants/{t}/cameras")
        elif what == 1:
            cam = random.choice(t["cameras"])["id"]
            self.client.get(f"{base}/{cam}", headers=h, name="GET /tenants/{t}/cameras/{c}")
        else:
            cam = random.choice(t["cameras"])["id"]
            self.client.get(f"{base}/{cam}/segments?minutes=10", headers=h,
                            name="GET /tenants/{t}/cameras/{c}/segments")


class SegmentUser(FastHttpUser):
    host = c.GATEWAY
    fixed_count = c.SEGMENT_USERS
    wait_time = constant_throughput(5)

    def on_start(self) -> None:
        self.n = c.claim("segment", c.SEGMENT_USERS)
        with open(c.HOOK_SECRET_FILE, encoding="utf-8") as f:
            self.secret = f.read().strip()

    @task
    def segment_done(self) -> None:
        tid, cam = random.choice(ALL_CAMERAS)
        # A start time that no other call uses: the microseconds of now, the last two digits = this user's number.
        now = datetime.now(timezone.utc)
        started = now.replace(microsecond=now.microsecond - now.microsecond % 100 + self.n)
        self.client.post(c.SEGMENT_URL, data=c.segment_form(tid, cam["id"], started), name="POST segment call",
                         headers={"X-Hook-Secret": self.secret,
                                  "Content-Type": "application/x-www-form-urlencoded"})


class StateClient(MqttClient):
    def _generate_event_name(self, event_type: str, qos: int, topic: str) -> str:
        return f"{event_type}:{qos}:cameras/+/state"  # one row in the results, not one per camera


class CameraUser(MqttUser):
    host = c.MQTT_HOST
    port = c.MQTT_PORT
    fixed_count = c.MQTT_CAMERAS
    wait_time = constant_throughput(1)
    # MQTT 3.1.1: Locust's MQTT client has callbacks for MQTT 5 that do not fit the paho callback version it creates
    # (the disconnect one takes one argument more). The path under test (broker -> intake) is the same.
    protocol = mqtt.MQTTv311
    client_cls = StateClient
    use_loop_selectors = True

    def __init__(self, environment) -> None:
        _, cam = ALL_CAMERAS[c.claim("camera", c.MQTT_CAMERAS)]
        self.client_id = self.username = cam["id"]
        self.password = cam["secret"]
        super().__init__(environment)
        self.topic = f"cameras/{cam['id']}/state"
        self.payload = json.dumps({"state": "online", "fps": 15, "bitrate": 1800})

    def on_start(self) -> None:
        end = time.monotonic() + 15
        while not self.client.is_connected() and time.monotonic() < end:
            gevent.sleep(0.1)

    @task
    def state(self) -> None:
        self.client.publish(self.topic, self.payload, qos=1, retain=True)
