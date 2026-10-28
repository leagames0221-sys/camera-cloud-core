"""Smoke run against the poc-* containers and the API running on the host (see poc/smoke_up.sh).
Not part of the test suite. Prints what happened; the numbers go into docs/validation/."""
import json
import pathlib
import subprocess
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "services" / "camera"))
import oidc_flow  # noqa: E402
import camera as cam_mod  # noqa: E402
from app import seed_data as sd  # noqa: E402

API = "http://127.0.0.1:8000"
HLS = "http://127.0.0.1:18888"
PB = "http://127.0.0.1:19996"
PW = "poc-demo-pw-1234"
S = ROOT / "poc" / "out" / "secrets" / "cameras"
out = {}


def tok(user):
    return oidc_flow.login(user, PW)["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


a_viewer, a_admin, b_viewer = tok("a-viewer"), tok("a-admin"), tok("b-viewer")
c = httpx.Client(timeout=20)
out["me_a_viewer"] = c.get(f"{API}/api/v1/me", headers=H(a_viewer)).json()
out["cameras_before"] = [(x["name"], x["state"]) for x in c.get(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras", headers=H(a_viewer)).json()]

# --- camera A1: MQTT from the real Camera class, video from ffmpeg in a container
secret = (S / str(sd.CAM_A1) / "secret").read_text().strip()
cam = cam_mod.Camera(str(sd.CAM_A1), secret, API, "127.0.0.1", 11883, "rtsp://poc-mtx:8554")
cam.start_mqtt()
t = cam_mod.fetch_publish_token(API, str(sd.CAM_A1), secret)
out["camera_token_path"] = t["path"]
subprocess.run(["sh", str(ROOT / "poc" / "ctl.sh"), "poc-pub"], check=False)
subprocess.run(["docker", "run", "-d", "--name", "poc-pub", "--network", "poc-net", "-v",
                f"{(ROOT / 'poc' / 'out').as_posix()}:/data:ro", "--entrypoint", "ffmpeg",
                "bluenviron/mediamtx:1.21.2-ffmpeg", "-loglevel", "warning", "-re", "-stream_loop", "-1", "-i",
                "/data/test.mp4", "-c", "copy", "-f", "rtsp", "-rtsp_transport", "tcp",
                f"rtsp://poc-mtx:8554/{t['path']}?token={t['token']}"], check=True, capture_output=True,
               env={**__import__('os').environ, "MSYS_NO_PATHCONV": "1"})
time.sleep(8)
cams = c.get(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras", headers=H(a_viewer)).json()
out["cameras_with_a1_publishing"] = [(x["name"], x["state"]) for x in cams]

# --- live: the HLS flow with the read token
live = c.post(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}/live", headers=H(a_viewer)).json()
r1 = c.get(live["hls_url"].replace("localhost", "127.0.0.1"), headers=H(live["token"]), follow_redirects=False)
r2 = c.get(f"{HLS}{r1.headers['location']}", headers=H(live["token"]))
out["live"] = {"first": r1.status_code, "after302": r2.status_code, "playlist_head": r2.text[:40]}
# B's camera with A's read token -> MediaMTX refuses
other = f"{HLS}/{sd.TENANT_B}/{sd.CAM_B1}/index.m3u8?cookieCheck=1"
out["live_other_path_with_a_token"] = c.get(other, headers=H(live["token"])).status_code
out["api_b_camera_as_a_viewer"] = c.post(f"{API}/api/v1/tenants/{sd.TENANT_B}/cameras/{sd.CAM_B1}/live",
                                         headers=H(a_viewer)).status_code

# --- wait for segments (60 s each) and the hook / reconcile
print("waiting for segments ...", flush=True)
deadline = time.time() + 200
segs = []
while time.time() < deadline:
    segs = c.get(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}/segments?minutes=10", headers=H(a_viewer)).json()
    if len(segs) >= 2:
        break
    time.sleep(10)
out["segments"] = segs
pb = c.post(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}/playback", headers=H(a_viewer))
out["playback_token_status"] = pb.status_code
if pb.status_code == 200:
    p = pb.json()
    g = c.get(p["url"].replace("localhost", "127.0.0.1"), headers=H(p["token"]))
    out["playback_get"] = {"status": g.status_code, "type": g.headers.get("content-type"), "bytes": len(g.content),
                           "ftyp": g.content[4:8].decode("latin1"), "duration_requested": p["duration_seconds"]}

# --- registration by the tenant admin and what the viewer cannot do
reg = c.post(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": "smoke"}, headers=H(a_admin))
out["register_admin"] = reg.status_code
out["register_viewer"] = c.post(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": "x"}, headers=H(a_viewer)).status_code
if reg.status_code == 201:
    new = reg.json()
    out["delete_admin"] = c.delete(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras/{new['id']}", headers=H(a_admin)).status_code

# --- state changes: normal stop, then unclean (Will)
cam.shutdown()
time.sleep(2)
cams = c.get(f"{API}/api/v1/tenants/{sd.TENANT_A}/cameras", headers=H(a_viewer)).json()
out["after_normal_stop"] = [(x["name"], x["state"]) for x in cams]
subprocess.run(["sh", str(ROOT / "poc" / "ctl.sh"), "poc-pub"], check=False)
print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
