"""Acceptance checks that a script runs against the running stack (items 1-5), printing the numbers to put in
docs/validation/acceptance.md. Needs the stack started with compose.yaml + compose.test.yaml (MQTT on loopback).

  .venv\\Scripts\\python scripts\\acceptance_8_1.py > poc\\out\\acceptance.json
"""
import json
import pathlib
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "integration"))
import oidc_flow  # noqa: E402
from conftest import BASE, ENV, HLS, KC, MQTT_HOST, MQTT_PORT, PAGE_ORIGIN, Tokens, compose  # noqa: E402
from ids import CAM_A1, CAM_A2, CAM_B1, TENANT_A, TENANT_B  # noqa: E402

OUT: dict = {}
tokens = Tokens()
http = httpx.Client(base_url=BASE, timeout=30)


def H(user):
    return tokens.headers(user)


def now():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"


def state(user, tenant, cam):
    return http.get(f"/api/v1/tenants/{tenant}/cameras/{cam}", headers=H(user)).json()


# ---- 1. everything is up; memory
ps = json.loads("[" + ",".join(compose("ps", "-a", "--format", "json").stdout.strip().splitlines()) + "]")
OUT["1_containers"] = [{"name": c["Service"], "state": c["State"], "health": c.get("Health", ""),
                        "exit_code": c.get("ExitCode")} for c in ps]
stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.MemUsage}}"], capture_output=True,
                       text=True).stdout.strip().splitlines()
OUT["1_memory"] = [s for s in stats if "camera-cloud-core" in s]
started = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", "camera-cloud-core-cam-a1-1"],
                         capture_output=True, text=True).stdout.strip()
OUT["1_cam_a1_started_at"] = started

# ---- 2. A viewer: list, live, past 5 minutes
t0 = time.monotonic()
cams = http.get(f"/api/v1/tenants/{TENANT_A}/cameras", headers=H("a-viewer"))
OUT["2_list"] = {"status": cams.status_code, "cameras": [(c["name"], c["state"], c["fps"], c["bitrate_kbps"])
                                                         for c in cams.json()]}
live = http.post(f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_A1}/live", headers=H("a-viewer"))
lv = live.json()
h = {"Authorization": f"Bearer {lv['token']}", "Origin": PAGE_ORIGIN}
with httpx.Client(follow_redirects=False, timeout=15) as c:
    r1 = c.get(lv["hls_url"], headers=h)
    r2 = c.get(urljoin(lv["hls_url"], r1.headers["location"]), headers=h)
    variant = [ln for ln in r2.text.splitlines() if ln and not ln.startswith("#")][0]
    r3 = c.get(urljoin(lv["hls_url"], variant), headers=h)
    r4 = c.get(urljoin(lv["hls_url"], variant), headers={"Origin": PAGE_ORIGIN})
OUT["2_live"] = {"api_status": live.status_code, "first": r1.status_code, "first_location": r1.headers.get("location"),
                 "after_302": r2.status_code, "variant_with_bearer": r3.status_code,
                 "variant_without_bearer_but_with_session_query": r4.status_code,
                 "playlist_has_targetduration": "EXT-X-TARGETDURATION" in r3.text}
pb = http.post(f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_A1}/playback", headers=H("a-viewer"))
OUT["2_playback_request"] = {"status": pb.status_code, "body": {k: v for k, v in pb.json().items() if k != "token"}}
if pb.status_code == 200:
    p = pb.json()
    g = httpx.get(p["url"], headers={"Authorization": f"Bearer {p['token']}", "Origin": PAGE_ORIGIN}, timeout=120)
    target = ROOT / "poc" / "out" / "playback.mp4"
    target.write_bytes(g.content)
    probe = subprocess.run(
        ["docker", "run", "--rm", "--name", "poc-probe", "-v", f"{(ROOT / 'poc' / 'out').as_posix()}:/d:ro",
         "--entrypoint", "ffprobe", "bluenviron/mediamtx:1.21.2-ffmpeg", "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,width,height", "-of", "default=nw=1", "/d/playback.mp4"],
        capture_output=True, text=True, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})
    OUT["2_playback_get"] = {"status": g.status_code, "content_type": g.headers.get("content-type"),
                             "bytes": len(g.content), "ftyp": g.content[4:8].decode("latin1"),
                             "ffprobe": probe.stdout.split(), "ffprobe_err": probe.stderr[:200]}

# ---- 3. company B's camera refused by the API and by MediaMTX
b = {}
for name, req in {
    "api_get_camera": lambda: http.get(f"/api/v1/tenants/{TENANT_B}/cameras/{CAM_B1}", headers=H("a-viewer")),
    "api_list": lambda: http.get(f"/api/v1/tenants/{TENANT_B}/cameras", headers=H("a-viewer")),
    "api_live": lambda: http.post(f"/api/v1/tenants/{TENANT_B}/cameras/{CAM_B1}/live", headers=H("a-viewer")),
    "api_playback": lambda: http.post(f"/api/v1/tenants/{TENANT_B}/cameras/{CAM_B1}/playback", headers=H("a-viewer")),
    "api_b_camera_under_a_tenant": lambda: http.get(f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_B1}", headers=H("a-viewer")),
}.items():
    r = req()
    b[name] = {"status": r.status_code, "body": r.text}
mt = httpx.get(f"{HLS}/{TENANT_B}/{CAM_B1}/index.m3u8?cookieCheck=1", headers={"Authorization": f"Bearer {lv['token']}"})
b["mediamtx_hls_with_a_read_token_on_b_path"] = {"status": mt.status_code, "body": mt.text}
b["mediamtx_hls_with_a_read_token_on_a_path (control)"] = {
    "status": httpx.get(f"{HLS}/{TENANT_A}/{CAM_A1}/index.m3u8?cookieCheck=1",
                        headers={"Authorization": f"Bearer {lv['token']}"}).status_code}
OUT["3_company_b"] = b

# ---- 4. A's camera credentials against B's topic
reg = http.post(f"/api/v1/tenants/{TENANT_A}/cameras", json={"name": "acceptance-temp"}, headers=H("a-admin")).json()
got = {}
ev = threading.Event()
c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=reg["id"], protocol=mqtt.MQTTv5)
c.username_pw_set(reg["id"], reg["secret"])
c.on_connect = lambda cl, u, f, rc, p: (got.__setitem__("connack", str(rc)), ev.set())
c.on_publish = lambda cl, u, mid, rc, p: got.__setitem__(f"puback_{mid}", (rc.value, str(rc)))
c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
c.loop_start()
ev.wait(10)
before = state("b-viewer", TENANT_B, CAM_B1)
i_own = c.publish(f"cameras/{reg['id']}/state", '{"state":"online"}', qos=1)
i_b = c.publish(f"cameras/{CAM_B1}/state", '{"state":"offline","reason":"forged"}', qos=1)
i_b.wait_for_publish(5)
time.sleep(3)
after = state("b-viewer", TENANT_B, CAM_B1)
c.loop_stop()
c.disconnect()
http.delete(f"/api/v1/tenants/{TENANT_A}/cameras/{reg['id']}", headers=H("a-admin"))
OUT["4_mqtt"] = {"connack": got.get("connack"), "own_topic_puback": got.get(f"puback_{i_own.mid}"),
                 "company_b_topic_puback": got.get(f"puback_{i_b.mid}"),
                 "b1_before": [before["state"], before["reason"]], "b1_after": [after["state"], after["reason"]]}


# ---- 5. disconnection times
def measure(label, action, user, tenant, cam, limit):
    t_wall = now()
    t = time.monotonic()
    action()
    t_action = time.monotonic() - t
    while time.monotonic() - t < limit:
        s = state(user, tenant, cam)
        if s["state"] == "disconnected":
            return {"action": label, "action_issued_at": t_wall, "disconnected_seen_at": now(),
                    "seconds_from_action_start": round(time.monotonic() - t, 1),
                    "seconds_after_action_returned": round(time.monotonic() - t - t_action, 1),
                    "reason": s["reason"]}
        time.sleep(0.5)
    return {"action": label, "failed_within": limit}


def wait_recording(user, tenant, cam):
    end = time.monotonic() + 180
    while time.monotonic() < end:
        if state(user, tenant, cam)["state"] == "recording":
            return True
        time.sleep(1)
    return False


res = []
wait_recording("a-viewer", TENANT_A, CAM_A2)
res.append(measure("docker kill cam-a2", lambda: compose("kill", "cam-a2"), "a-viewer", TENANT_A, CAM_A2, 30))
compose("start", "cam-a2")
wait_recording("a-viewer", TENANT_A, CAM_A2)
res.append(measure("docker stop cam-a2", lambda: compose("stop", "-t", "10", "cam-a2", timeout=60), "a-viewer",
                   TENANT_A, CAM_A2, 40))
compose("start", "cam-a2")
wait_recording("b-viewer", TENANT_B, CAM_B1)
try:
    res.append(measure("docker pause cam-b1", lambda: compose("pause", "cam-b1"), "b-viewer", TENANT_B, CAM_B1, 40))
finally:
    compose("unpause", "cam-b1")
OUT["5_disconnect"] = res
OUT["5_keepalive_seconds"] = 10
OUT["5_limit_seconds"] = 15
print(json.dumps(OUT, indent=2, ensure_ascii=False))
