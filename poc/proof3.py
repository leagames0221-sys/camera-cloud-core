"""Proof 3: Mosquitto dynamic security with %c. Broker from poc/run_mqtt.sh on 127.0.0.1:11883.

Provisioning goes through the dynsec JSON API with aiomqtt (MQTT 5.0); the checks use paho-mqtt 2.1
callbacks because they expose the PUBACK / SUBACK / CONNACK reason codes.
"""
import asyncio
import json
import threading
import time

import aiomqtt
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

HOST, PORT = "127.0.0.1", 11883
ADMIN = ("admin", "poc-admin-pw")
CAM_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CAM_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SECRETS = {CAM_A: "secret-a", CAM_B: "secret-b", "ingest": "secret-ingest"}
OUT = {}


async def dynsec(commands):
    async with aiomqtt.Client(HOST, PORT, username=ADMIN[0], password=ADMIN[1], identifier="poc-admin",
                              protocol=aiomqtt.ProtocolVersion.V5, keepalive=10) as c:
        await c.subscribe("$CONTROL/dynamic-security/v1/response", qos=1)
        await c.publish("$CONTROL/dynamic-security/v1", json.dumps({"commands": commands}), qos=1)
        async with asyncio.timeout(5):
            async for m in c.messages:
                return json.loads(m.payload)


def provision():
    cmds = [
        {"command": "createRole", "rolename": "camera"},
        {"command": "addRoleACL", "rolename": "camera", "acltype": "publishClientSend", "topic": "cameras/%c/state",
         "priority": 0, "allow": True},
        {"command": "createRole", "rolename": "ingest"},
        {"command": "addRoleACL", "rolename": "ingest", "acltype": "subscribePattern", "topic": "cameras/+/state",
         "priority": 0, "allow": True},
        {"command": "createClient", "username": CAM_A, "password": SECRETS[CAM_A], "clientid": CAM_A,
         "roles": [{"rolename": "camera"}]},
        {"command": "createClient", "username": CAM_B, "password": SECRETS[CAM_B], "clientid": CAM_B,
         "roles": [{"rolename": "camera"}]},
        {"command": "createClient", "username": "ingest", "password": SECRETS["ingest"],
         "roles": [{"rolename": "ingest"}]},
    ]
    first = asyncio.run(dynsec(cmds))
    second = asyncio.run(dynsec(cmds[:1] + cmds[4:5]))  # same again: shows the 'already exists' error text
    OUT["provision_first"] = first
    OUT["provision_repeat_errors"] = second
    OUT["get_client_A"] = asyncio.run(dynsec([{"command": "getClient", "username": CAM_A}]))
    OUT["get_role_camera"] = asyncio.run(dynsec([{"command": "getRole", "rolename": "camera"}]))


class Peer:
    def __init__(self, cid, user, pwd, **kw):
        self.c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=cid, protocol=mqtt.MQTTv5)
        if user is not None:
            self.c.username_pw_set(user, pwd)
        self.connack = None
        self.acks = {}
        self.suback = {}
        self.received = []
        self.ev = threading.Event()
        self.c.on_connect = self._on_connect
        self.c.on_publish = lambda c, u, mid, rc, props: self.acks.__setitem__(mid, rc)
        self.c.on_subscribe = lambda c, u, mid, rcs, props: self.suback.__setitem__(mid, rcs)
        self.c.on_message = lambda c, u, m: self.received.append((m.topic, m.payload))
        self.kw = kw

    def _on_connect(self, c, u, flags, rc, props):
        self.connack = rc
        self.ev.set()

    def connect(self, keepalive=10):
        self.c.connect(HOST, PORT, keepalive=keepalive)
        self.c.loop_start()
        self.ev.wait(5)
        return self.connack

    def close(self):
        self.c.loop_stop()
        try:
            self.c.disconnect()
        except Exception:
            pass


def wait(cond, t=5):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def checks():
    # ingest listens to cameras/+/state
    ing = Peer("ingest-1", "ingest", SECRETS["ingest"])
    OUT["ingest_connack"] = str(ing.connect())
    r, mid = ing.c.subscribe("cameras/+/state", qos=1)
    wait(lambda: mid in ing.suback)
    OUT["ingest_subscribe_cameras_plus_state"] = [str(x) for x in ing.suback.get(mid, [])]
    r, mid = ing.c.subscribe("cameras/#", qos=1)
    wait(lambda: mid in ing.suback)
    OUT["ingest_subscribe_cameras_hash_(should_be_denied)"] = [str(x) for x in ing.suback.get(mid, [])]
    r, mid = ing.c.subscribe("#", qos=1)
    wait(lambda: mid in ing.suback)
    OUT["ingest_subscribe_hash_(should_be_denied)"] = [str(x) for x in ing.suback.get(mid, [])]
    ok = Peer("ingest-pub", "ingest", SECRETS["ingest"])
    ok.connect()
    mi = ok.c.publish(f"cameras/{CAM_A}/state", b'{"state":"online"}', qos=1)
    wait(lambda: mi.mid in ok.acks)
    OUT["ingest_publish_(should_be_denied)"] = str(ok.acks.get(mi.mid))
    ok.close()

    a = Peer(CAM_A, CAM_A, SECRETS[CAM_A])
    OUT["camA_connack"] = str(a.connect())
    own = a.c.publish(f"cameras/{CAM_A}/state", b'{"state":"online","from":"A"}', qos=1)
    wait(lambda: own.mid in a.acks)
    OUT["camA_publish_own_topic_puback"] = str(a.acks.get(own.mid))
    other = a.c.publish(f"cameras/{CAM_B}/state", b'{"state":"online","from":"A-as-B"}', qos=1)
    wait(lambda: other.mid in a.acks)
    OUT["camA_publish_B_topic_puback"] = str(a.acks.get(other.mid))
    r, mid = a.c.subscribe(f"cameras/{CAM_B}/state", qos=1)
    wait(lambda: mid in a.suback)
    OUT["camA_subscribe_B_topic_suback"] = [str(x) for x in a.suback.get(mid, [])]
    r, mid = a.c.subscribe(f"cameras/{CAM_A}/state", qos=1)
    wait(lambda: mid in a.suback)
    OUT["camA_subscribe_own_topic_suback"] = [str(x) for x in a.suback.get(mid, [])]
    r, mid = a.c.subscribe("cameras/+/state", qos=1)
    wait(lambda: mid in a.suback)
    OUT["camA_subscribe_wildcard_suback"] = [str(x) for x in a.suback.get(mid, [])]
    time.sleep(1.0)
    OUT["ingest_received_topics"] = sorted({t for t, _ in ing.received})
    OUT["ingest_received_payloads"] = sorted({p.decode() for _, p in ing.received})
    a.close()
    # credentials with another client id / wrong password / anonymous
    x = Peer(CAM_B, CAM_A, SECRETS[CAM_A])
    OUT["camA_credentials_with_client_id_B_connack"] = str(x.connect())
    x.close()
    x = Peer("some-other-id", CAM_A, SECRETS[CAM_A])
    OUT["camA_credentials_with_random_client_id_connack"] = str(x.connect())
    x.close()
    x = Peer(CAM_A, CAM_A, "wrong")
    OUT["camA_wrong_password_connack"] = str(x.connect())
    x.close()
    x = Peer("anon", None, None)
    OUT["anonymous_connack"] = str(x.connect())
    x.close()
    ing.close()


if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    # aiomqtt needs add_reader(), which the default Windows loop does not have
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
provision()
checks()
print(json.dumps(OUT, indent=2, ensure_ascii=False))
