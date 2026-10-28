"""The real thing from the acceptance text: the camera container with ENROLL=device shows a code, a tenant administrator
of company C enters it in Keycloak's page, and within a minute the camera is in C's list and its live picture plays."""
import re
import subprocess
import time

import httpx
import pytest

import device_flow as dev
from conftest import ENV, KC, TENANT_C, compose, wait_for

pytestmark = pytest.mark.integration

VOLUME = "camera-cloud-core_devicecam"


def profile(*args, check=True):
    return compose("--profile", "device", *args, check=check, timeout=300)


def clean_device_camera():
    profile("rm", "-sf", "cam-device", check=False)
    subprocess.run(["docker", "volume", "rm", VOLUME], capture_output=True)


def test_the_camera_shows_a_code_the_administrator_enters_it_and_the_camera_appears_and_plays(http, tokens):
    clean_device_camera()
    profile("up", "-d", "--no-build", "cam-device")
    camera_id = None
    try:
        logs = ""
        def code_shown():
            nonlocal logs
            logs = profile("logs", "--no-color", "cam-device").stdout
            return re.search(r"enter the code ([A-Z0-9]{4}-[A-Z0-9]{4})", logs)

        m = wait_for(code_shown, 60, interval=2, what="the camera to show its code")
        code = m.group(1)
        assert "/realms/ccc/device" in logs and "device_code" not in logs      # the address is shown, the secret code is not
        t0 = time.monotonic()
        page = dev.decide(KC, code, "c-admin", ENV["DEMO_USER_PASSWORD"])
        assert "Successful" in page

        def listed():
            r = http.get(f"/api/v1/tenants/{TENANT_C}/cameras", headers=tokens.headers("c-admin")).json()
            new = [c for c in r if c["name"] == "Device camera"]
            return new[0] if new else None

        cam = wait_for(listed, 60, interval=2, what="the new camera in the list of company C")
        camera_id = cam["id"]
        assert time.monotonic() - t0 < 60                   # "within a minute" of entering the code
        wait_for(lambda: listed()["state"] == "recording", 60, interval=2, what="the new camera to be recording")
        lv = http.post(f"/api/v1/tenants/{TENANT_C}/cameras/{camera_id}/live", headers=tokens.headers("c-viewer"))
        assert lv.status_code == 200 and lv.json()["hls_url"].startswith("http://localhost:8889/")
        r = httpx.get(lv.json()["hls_url"], headers={"Authorization": f"Bearer {lv.json()['token']}"},
                      follow_redirects=True, timeout=20)
        assert r.status_code == 200 and r.text.startswith("#EXTM3U")
        # it keeps its own secret and nothing else: no token in its volume, and a second start does not ask again
        files = profile("exec", "-T", "cam-device", "cat", "/data/credentials.json", check=False)
        assert files.returncode == 0 and "access_token" not in files.stdout and "device_code" not in files.stdout
        profile("restart", "cam-device")
        time.sleep(15)
        logs2 = profile("logs", "--no-color", "--since", "20s", "cam-device").stdout
        assert "enter the code" not in logs2
    finally:
        if camera_id:
            http.delete(f"/api/v1/tenants/{TENANT_C}/cameras/{camera_id}", headers=tokens.headers("c-admin"))
        clean_device_camera()


def test_a_viewer_who_enters_the_code_does_not_get_the_camera_registered(http, tokens):
    clean_device_camera()
    profile("up", "-d", "--no-build", "cam-device")
    try:
        def code_shown():
            return re.search(r"enter the code ([A-Z0-9]{4}-[A-Z0-9]{4})", profile("logs", "--no-color", "cam-device").stdout)

        code = wait_for(code_shown, 60, interval=2, what="the code").group(1)
        dev.decide(KC, code, "c-viewer", ENV["DEMO_USER_PASSWORD"])
        wait_for(lambda: "registration stopped" in profile("logs", "--no-color", "cam-device").stdout, 60, interval=2,
                 what="the camera to be told no")
        r = http.get(f"/api/v1/tenants/{TENANT_C}/cameras", headers=tokens.headers("c-admin")).json()
        assert [c for c in r if c["name"] == "Device camera"] == []
        assert "403" in profile("logs", "--no-color", "cam-device").stdout
    finally:
        clean_device_camera()
