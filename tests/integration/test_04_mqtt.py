"""MQTT 5.0: a camera can publish only to its own topic, cannot subscribe, and cannot connect under another client ID.
Cameras are registered through the API (tenant administrators), so the test needs no broker password."""
import json
import socket
import threading
import time

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from conftest import MQTT_HOST, MQTT_PORT, camera_state, wait_for
from ids import TENANT_A, TENANT_B

pytestmark = pytest.mark.integration

NOT_AUTHORIZED = 0x87


@pytest.fixture(scope="module", autouse=True)
def broker_is_published():
    try:
        socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=2).close()
    except OSError:
        pytest.skip("MQTT is not published (start with compose.test.yaml)")


def register(http, tokens, user, tenant, name):
    r = http.post(f"/api/v1/tenants/{tenant}/cameras", json={"name": name}, headers=tokens.headers(user))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def cam_a(http, tokens):
    c = register(http, tokens, "a-admin", TENANT_A, "mqtt-test-a")
    yield c
    http.delete(f"/api/v1/tenants/{TENANT_A}/cameras/{c['id']}", headers=tokens.headers("a-admin"))


@pytest.fixture(scope="module")
def cam_b(http, tokens):
    c = register(http, tokens, "b-admin", TENANT_B, "mqtt-test-b")
    yield c
    http.delete(f"/api/v1/tenants/{TENANT_B}/cameras/{c['id']}", headers=tokens.headers("b-admin"))


class Peer:
    def __init__(self, client_id, user, password):
        self.c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv5)
        self.c.username_pw_set(user, password)
        self.connack = None
        self.puback: dict = {}
        self.suback: dict = {}
        self._ev = threading.Event()
        self.c.on_connect = lambda c, u, f, rc, p: (setattr(self, "connack", rc), self._ev.set())
        self.c.on_publish = lambda c, u, mid, rc, p: self.puback.__setitem__(mid, rc)
        self.c.on_subscribe = lambda c, u, mid, rcs, p: self.suback.__setitem__(mid, rcs)

    def connect(self):
        self.c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        self.c.loop_start()
        self._ev.wait(10)
        return self.connack

    def close(self):
        self.c.loop_stop()
        try:
            self.c.disconnect()
        except Exception:  # noqa: BLE001
            pass


def topic(camera_id: str) -> str:
    return f"cameras/{camera_id}/state"


def test_own_topic_is_accepted_and_shows_up_in_the_api(http, tokens, cam_a):
    p = Peer(cam_a["id"], cam_a["id"], cam_a["secret"])
    assert not p.connect().is_failure
    try:
        info = p.c.publish(topic(cam_a["id"]), json.dumps({"state": "online", "fps": 12.5}), qos=1)
        info.wait_for_publish(5)
        assert p.puback[info.mid].value == 0
        wait_for(lambda: camera_state(http, tokens, "a-admin", TENANT_A, cam_a["id"])["state"] == "standby", 15,
                 what="the API to show the camera as online (standby: no video path yet)")
    finally:
        p.close()


def test_publish_to_another_companys_topic_gets_0x87_and_changes_nothing(http, tokens, cam_a, cam_b):
    before = camera_state(http, tokens, "b-admin", TENANT_B, cam_b["id"])
    assert before["state"] == "disconnected"
    p = Peer(cam_a["id"], cam_a["id"], cam_a["secret"])
    assert not p.connect().is_failure
    try:
        info = p.c.publish(topic(cam_b["id"]), json.dumps({"state": "online"}), qos=1)
        info.wait_for_publish(5)
        assert p.puback[info.mid].value == NOT_AUTHORIZED
        time.sleep(3)    # the intake would have written it by now
        after = camera_state(http, tokens, "b-admin", TENANT_B, cam_b["id"])
        assert after["state"] == "disconnected" and after["last_seen"] is None
    finally:
        p.close()


def test_a_camera_cannot_subscribe_to_anything(cam_a, cam_b):
    p = Peer(cam_a["id"], cam_a["id"], cam_a["secret"])
    assert not p.connect().is_failure
    try:
        for flt in (topic(cam_b["id"]), topic(cam_a["id"]), "cameras/+/state", "cameras/#", "#"):
            _, mid = p.c.subscribe(flt, qos=1)
            wait_for(lambda: mid in p.suback, 5, 0.1, what=f"SUBACK for {flt}")
            assert [rc.value for rc in p.suback[mid]] == [NOT_AUTHORIZED], flt
    finally:
        p.close()


def test_another_client_id_is_refused(cam_a, cam_b):
    for client_id in (cam_b["id"], "some-other-id"):
        p = Peer(client_id, cam_a["id"], cam_a["secret"])
        rc = p.connect()
        p.close()
        assert rc is not None and rc.is_failure, client_id


def test_wrong_secret_and_anonymous_are_refused(cam_a):
    p = Peer(cam_a["id"], cam_a["id"], "wrong")
    rc = p.connect()
    p.close()
    assert rc.is_failure
    c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id="anon", protocol=mqtt.MQTTv5)
    got = {}
    ev = threading.Event()
    c.on_connect = lambda cl, u, f, r, p_: (got.setdefault("rc", r), ev.set())
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
    c.loop_start()
    ev.wait(10)
    c.loop_stop()
    assert got["rc"].is_failure
