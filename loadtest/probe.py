"""The lag probe: how long after a segment call or a state message the public API shows it.

Every 10 seconds it does one probe of each kind (the state probe 5 seconds after the segment probe):
  segment  POST the closed-segment call for the probe camera (the same request MediaMTX's hook sends), then ask the
           API's segment list until the new start time is in it.
  state    publish an MQTT state message with a number in `fps` that was never used before, then ask the API for the
           camera until `fps` shows that number.
The asking starts after 20 ms, and the gap between questions grows to 250 ms (the probe tenant has no other traffic, so
this does not touch any per-tenant limit). The lag is the time from just before the call or publish until the answer that
shows it came back, so it is an upper bound by up to one gap. A probe that is not visible after 30 seconds is written as
a timeout.

  python probe.py --seconds 340 --out /work/out/probe.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import time
import urllib.request
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

import common as c

TIMEOUT = 30.0


def read_secret(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def wait_until(check, t0: float) -> float | None:
    """Poll with growing gaps until check() is true; the lag in ms, or None after TIMEOUT."""
    gap = 0.02
    while time.monotonic() - t0 < TIMEOUT:
        time.sleep(gap)
        if check():
            return (time.monotonic() - t0) * 1000
        gap = min(gap * 1.6, 0.25)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=340)
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=float, default=10)
    a = ap.parse_args()

    data = c.load_data()
    probe = next(t for t in data["tenants"] if t["name"] == c.PROBE_TENANT_NAME)
    tid, cam = probe["id"], probe["cameras"][0]
    admin = c.TokenCache("l-admin")
    hook = read_secret(c.HOOK_SECRET_FILE)

    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=cam["id"], protocol=mqtt.MQTTv5)
    client.username_pw_set(cam["id"], cam["secret"])
    client.connect(c.MQTT_HOST, c.MQTT_PORT, keepalive=30)
    client.loop_start()
    topic = f"cameras/{cam['id']}/state"
    seq = random.randint(1000, 5_000_000)

    def segment_probe() -> tuple[str, float | None]:
        started = datetime.now(timezone.utc)
        want = c.iso_z(started)
        t0 = time.monotonic()
        req = urllib.request.Request(c.SEGMENT_URL, data=c.segment_form(tid, cam["id"], started), method="POST",
                                     headers={"X-Hook-Secret": hook,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:  # noqa: BLE001 - the probe records the failure and goes on
            return f"call failed: {e}", None

        def seen() -> bool:
            st, body = c.api("GET", f"/api/v1/tenants/{tid}/cameras/{cam['id']}/segments?minutes=5", admin.get())
            return st == 200 and any(s["started_at"] == want for s in body or [])
        return "ok", wait_until(seen, t0)

    def state_probe() -> tuple[str, float | None]:
        nonlocal seq
        seq += 1
        want = float(seq)
        t0 = time.monotonic()
        client.publish(topic, json.dumps({"state": "online", "fps": seq, "bitrate": 1800}), qos=1, retain=True)

        def seen() -> bool:
            st, body = c.api("GET", f"/api/v1/tenants/{tid}/cameras/{cam['id']}", admin.get())
            return st == 200 and body.get("fps") == want
        return "ok", wait_until(seen, t0)

    start = time.monotonic()
    n = 0
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "elapsed_s", "kind", "result", "lag_ms"])
        while True:
            # a segment probe at 0 s, 10 s, 20 s ..., a state probe 5 s after each of them
            due = start + (n // 2) * a.every + (a.every / 2 if n % 2 else 0)
            if due - start >= a.seconds:
                break
            time.sleep(max(0.0, due - time.monotonic()))
            kind, fn = (("segment", segment_probe), ("state", state_probe))[n % 2]
            result, lag = fn()
            if lag is None and result == "ok":
                result = "timeout"
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), f"{due - start:.1f}", kind, result,
                        "" if lag is None else f"{lag:.1f}"])
            f.flush()
            n += 1
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
