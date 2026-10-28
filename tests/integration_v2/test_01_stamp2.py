"""The second stamp is a separate copy: what belongs to one stamp does not open the other, and company C is served by the
second stamp from end to end."""
import socket
import time

import httpx
import jwt
import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from conftest import (CAM_A1, CAM_C1, HLS1, HLS2, MQTT1, MQTT2, PLAYBACK2, RTSP1, RTSP2, TENANT_A, TENANT_C,
                      camera_view, compose, in_container, wait_for)

pytestmark = pytest.mark.integration


def live(http, tokens, tenant, camera, user):
    r = http.post(f"/api/v1/tenants/{tenant}/cameras/{camera}/live", headers=tokens.headers(user))
    assert r.status_code == 200, r.text
    return r.json()


def get_playlist(url, token):
    return httpx.get(url, headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:8000"},
                     follow_redirects=True, timeout=20)


def camera_secret(container: str) -> str:
    return compose("exec", "-T", container, "cat", "/run/secret/secret").stdout.strip()


def rtsp_request(addr, request: str) -> str:
    s = socket.create_connection(addr, timeout=10)
    try:
        s.sendall(request.encode())
        s.settimeout(10)
        return s.recv(4096).decode(errors="replace").splitlines()[0]
    finally:
        s.close()


SDP = ("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=x\r\nt=0 0\r\nm=video 0 RTP/AVP 96\r\n"
       "a=rtpmap:96 H264/90000\r\na=control:streamid=0\r\n")


def announce(addr, path, token) -> str:
    return rtsp_request(addr, f"ANNOUNCE rtsp://{addr[0]}:{addr[1]}/{path}?token={token} RTSP/1.0\r\nCSeq: 1\r\n"
                              f"Content-Type: application/sdp\r\nContent-Length: {len(SDP)}\r\n\r\n{SDP}")


def publish_token(http, camera, secret):
    r = http.post("/api/v1/camera/token", json={"camera_id": camera, "secret": secret})
    assert r.status_code == 200, r.text
    return r.json()


def test_company_c_is_recording_on_the_second_stamp_and_its_urls_are_that_stamps(http, tokens):
    wait_for(lambda: camera_view(http, tokens, TENANT_C, CAM_C1, "c-viewer")["state"] == "recording", 120,
             what="the camera of company C to be recording")
    lv = live(http, tokens, TENANT_C, CAM_C1, "c-viewer")
    assert lv["hls_url"].startswith(HLS2 + "/") and not lv["hls_url"].startswith(HLS1)
    r = get_playlist(lv["hls_url"], lv["token"])
    assert r.status_code == 200 and r.text.startswith("#EXTM3U")
    # the playback address is the second stamp's, too, and the recording plays from there (the first segment is closed
    # 60 seconds after the camera starts, so this waits for it)
    def playback():
        r = http.post(f"/api/v1/tenants/{TENANT_C}/cameras/{CAM_C1}/playback", headers=tokens.headers("c-viewer"))
        return r.json() if r.status_code == 200 else None
    pb = wait_for(playback, 150, interval=5, what="a recorded segment of company C")
    assert pb["url"].startswith(PLAYBACK2 + "/get?")
    assert jwt.decode(pb["token"], options={"verify_signature": False})["aud"] == "mediamtx-s2"
    r = httpx.get(pb["url"], headers={"Authorization": f"Bearer {pb['token']}", "Origin": "http://localhost:8000"},
                  timeout=60)
    assert r.status_code == 200 and len(r.content) > 0


def test_registration_answer_of_company_c_names_the_second_stamps_broker_and_video_server(new_camera):
    cam = new_camera("answer test")
    assert cam["mqtt_url"] == "mqtt://mosquitto-s2:1883"
    assert cam["mediamtx_url"].startswith("rtsp://mediamtx-s2:8554/") and cam["mediamtx_url"].endswith(
        f"{TENANT_C}/{cam['id']}")
    assert cam["credential_type"] == "secret"


def test_company_a_read_token_does_not_open_company_c_on_the_second_stamp(http, tokens):
    a = live(http, tokens, TENANT_A, CAM_A1, "a-viewer")
    # a token for A's path opens A's path on the first stamp ...
    assert get_playlist(a["hls_url"], a["token"]).status_code == 200
    # ... but not C's path on the second stamp (another path), and not even A's own path name there (another audience)
    c_url = f"{HLS2}/{TENANT_C}/{CAM_C1}/index.m3u8"
    assert get_playlist(c_url, a["token"]).status_code in (401, 403)
    assert get_playlist(f"{HLS2}/{TENANT_A}/{CAM_A1}/index.m3u8", a["token"]).status_code in (401, 403)


def test_a_token_made_for_the_second_stamp_does_not_open_the_first(http, tokens):
    c = live(http, tokens, TENANT_C, CAM_C1, "c-viewer")
    assert get_playlist(c["hls_url"], c["token"]).status_code == 200
    # the same path name and token on the first stamp's video server: the audience differs
    assert get_playlist(f"{HLS1}/{TENANT_C}/{CAM_C1}/index.m3u8", c["token"]).status_code in (401, 403)


def test_the_publish_token_of_company_c_does_not_let_a_camera_publish_on_the_first_stamp(http):
    secret = camera_secret("cam-c1")
    tok = publish_token(http, CAM_C1, secret)
    path = f"{TENANT_C}/{CAM_C1}"
    assert " 401 " in announce(RTSP1, path, tok["token"])
    # the same token on its own stamp is not refused for authorisation (the path already has the real camera on it, so the
    # server may answer something else, but never 401)
    assert " 401 " not in announce(RTSP2, path, tok["token"])


def mqtt_connect(addr, user, password, client_id=None, wait=8):
    result = {}
    c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id or user, protocol=mqtt.MQTTv5)
    c.username_pw_set(user, password)
    c.on_connect = lambda cl, ud, fl, rc, pr: result.setdefault("rc", rc)
    c.connect(addr[0], addr[1], keepalive=10)
    c.loop_start()
    end = time.monotonic() + wait
    while "rc" not in result and time.monotonic() < end:
        time.sleep(0.05)
    c.loop_stop()
    c.disconnect()
    return result.get("rc")


def test_the_camera_of_company_c_connects_to_the_second_broker_and_not_to_the_first():
    secret = camera_secret("cam-c1")
    rc2 = mqtt_connect(MQTT2, CAM_C1, secret)
    assert rc2 is not None and not rc2.is_failure
    rc1 = mqtt_connect(MQTT1, CAM_C1, secret)
    assert rc1 is not None and rc1.is_failure           # not authorised: that broker does not know the camera


def test_a_camera_cannot_reach_the_queue_of_its_stamp():
    for camera, queue in (("cam-c1", "valkey-s2"), ("cam-a1", "valkey")):
        code = ("import socket\n"
                "try:\n"
                f"    socket.create_connection(('{queue}', 6379), timeout=3)\n"
                "    print('reached')\n"
                "except OSError as e:\n"
                "    print('refused', e.__class__.__name__)\n")
        out = compose("exec", "-T", camera, "/venv/bin/python", "-c", code).stdout.strip()
        assert out.startswith("refused"), (camera, out)


def test_a_camera_reaches_the_writer_but_its_doors_ask_for_the_stamps_secret():
    """The writer is on the network the cameras are on (MediaMTX calls it there); without the secret its doors say 403."""
    for camera, writer in (("cam-c1", "writer-s2"), ("cam-a1", "writer")):
        code = ("import urllib.request, urllib.error\n"
                "for method, path in (('GET', 'counters'), ('POST', 'segments')):\n"
                f"    req = urllib.request.Request('http://{writer}:8000/internal/v1/' + path, data=b'' if method == 'POST'"
                " else None, method=method)\n"
                "    try:\n"
                "        print(urllib.request.urlopen(req, timeout=5).status)\n"
                "    except urllib.error.HTTPError as e:\n"
                "        print(e.code)\n")
        out = compose("exec", "-T", camera, "/venv/bin/python", "-c", code).stdout.split()
        assert out == ["403", "403"], (camera, out)


def test_the_queue_only_accepts_the_writers_account_and_the_default_user_is_off():
    code = ("import os, valkey\n"
            "r = valkey.Valkey(host=os.environ['VALKEY_HOST'])\n"
            "try:\n"
            "    r.ping(); print('open')\n"
            "except Exception as e:\n"
            "    print('refused', type(e).__name__)\n")
    assert in_container("writer", code).startswith("refused")
    code = ("import os, valkey\n"
            "r = valkey.Valkey(host=os.environ['VALKEY_HOST'], username='writer', password='wrong')\n"
            "try:\n"
            "    r.ping(); print('open')\n"
            "except Exception as e:\n"
            "    print('refused', type(e).__name__)\n")
    assert in_container("writer", code).startswith("refused")


def test_the_writer_of_one_stamp_cannot_open_the_other_stamps_queue():
    # the folder of the first writer holds only the first stamp's queue password; the second queue is not even a name it
    # can resolve (it is on the second stamp's own network)
    code = ("import socket\n"
            "try:\n"
            "    socket.getaddrinfo('valkey-s2', 6379); print('resolved')\n"
            "except OSError:\n"
            "    print('unknown')\n")
    assert in_container("writer", code) == "unknown"
