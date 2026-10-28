"""The camera's registration by the device authorization grant: what it asks, how it waits, and what it keeps."""
import json
import os
import stat

import pytest

import camera

DEVICE = {"device_code": "DC", "user_code": "ABCD-EFGH", "verification_uri": "http://localhost:8080/realms/ccc/device",
          "expires_in": 600, "interval": 5}


class Clock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def answers(*items):
    """A fake token endpoint: the given (status, body) answers in turn."""
    seen, it = [], iter(items)

    def post(url, form):
        seen.append((url, form))
        return next(it)
    post.seen = seen
    return post


def run_poll(post, device=DEVICE):
    c = Clock()
    token = camera.poll_for_token(post, "T", "ccc-device", device, sleep=c.sleep, now=c.now)
    return token, c


def test_it_waits_while_the_answer_is_pending_and_takes_the_token_when_it_comes():
    post = answers((400, {"error": "authorization_pending"}), (400, {"error": "authorization_pending"}),
                   (200, {"access_token": "AT"}))
    token, c = run_poll(post)
    assert token == "AT" and c.sleeps == [5, 5, 5]
    url, form = post.seen[0]
    assert form == {"grant_type": camera.DEVICE_GRANT, "device_code": "DC", "client_id": "ccc-device"}


def test_slow_down_makes_every_later_wait_five_seconds_longer():
    post = answers((400, {"error": "slow_down"}), (400, {"error": "authorization_pending"}),
                   (400, {"error": "slow_down"}), (200, {"access_token": "AT"}))
    token, c = run_poll(post)
    assert token == "AT" and c.sleeps == [5, 10, 10, 15]


@pytest.mark.parametrize("err", ["access_denied", "expired_token", "invalid_grant", "unknown"])
def test_denied_expired_and_unexpected_answers_end_it(err):
    with pytest.raises(camera.EnrollRefused, match=err):
        run_poll(answers((400, {"error": err})))


def test_it_gives_up_when_the_code_runs_out_even_if_keycloak_stays_pending():
    pending = (400, {"error": "authorization_pending"})
    with pytest.raises(camera.EnrollRefused, match="expired_token"):
        run_poll(answers(*[pending] * 100), {**DEVICE, "expires_in": 12})


def test_the_default_interval_is_five_seconds():
    token, c = run_poll(answers((200, {"access_token": "AT"})), {"device_code": "x", "expires_in": 60})
    assert c.sleeps == [5]


def test_the_urls_of_the_answer_become_the_cameras_broker_and_video_server():
    answer = {"id": "C", "mqtt_url": "mqtt://mosquitto-s2:1883", "mediamtx_url": "rtsp://mediamtx-s2:8554/T/C"}
    assert camera.split_urls(answer) == ("C", "mosquitto-s2", 1883, "rtsp://mediamtx-s2:8554")
    assert camera.split_urls({**answer, "mqtt_url": "mqtt://b"})[2] == 1883


def test_the_whole_flow_prints_the_code_not_the_device_code_and_sends_the_token_only_to_the_api(monkeypatch):
    said, posted = [], []

    def post(url, form):
        posted.append(url)
        if url.endswith("/auth/device"):
            return 200, DEVICE
        return 200, {"access_token": "SECRET-TOKEN"}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"id": "C", "secret": "s", "mqtt_url": "mqtt://m:1", "mediamtx_url": "rtsp://r:2/T/C"}).encode()

    sent = []

    def fake_open(req, timeout=0):
        sent.append((req.full_url, dict(req.header_items())))
        return Resp()

    monkeypatch.setattr(camera.urllib.request, "urlopen", fake_open)
    out = camera.enroll_with_device_grant("http://kc", "ccc", "ccc-device", "http://gw", "door",
                                          post=post, sleep=lambda s: None, say=lambda *a, **k: said.append(a[0]))
    assert out["secret"] == "s"
    assert "ABCD-EFGH" in said[0] and "/realms/ccc/device" in said[0] and "DC" not in said[0].replace("ABCD", "")
    assert posted == ["http://kc/realms/ccc/protocol/openid-connect/auth/device",
                      "http://kc/realms/ccc/protocol/openid-connect/token"]
    assert sent[0][0] == "http://gw/api/v1/devices/enroll" and sent[0][1]["Authorization"] == "Bearer SECRET-TOKEN"


def test_credentials_are_kept_owner_only_and_the_second_start_does_not_register_again(tmp_path, monkeypatch):
    calls = []

    def fake_enroll(*a, **k):
        calls.append(a)
        return {"id": "C", "secret": "s", "mqtt_url": "mqtt://m:1884", "mediamtx_url": "rtsp://r:2/T/C"}

    monkeypatch.setattr(camera, "enroll_with_device_grant", fake_enroll)
    first = camera.load_or_enroll(str(tmp_path), "kc", "ccc", "ccc-device", "gw", "door")
    assert first == {"camera_id": "C", "secret": "s", "mqtt_host": "m", "mqtt_port": 1884, "rtsp_base": "rtsp://r:2"}
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "credentials.json").stat().st_mode) == 0o600
    assert "token" not in (tmp_path / "credentials.json").read_text().lower()
    assert camera.load_or_enroll(str(tmp_path), "kc", "ccc", "ccc-device", "gw", "door") == first
    assert len(calls) == 1
