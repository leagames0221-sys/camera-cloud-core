"""Mosquitto dynamic security: roles, the intake user and the camera users (ADR 0005).

Roles (the `%c` in the topic is replaced by the connecting client's ID, so one role covers every camera):
  camera  -> publishClientSend on  cameras/%c/state   (no subscribe rule: the default is deny)
  ingest  -> subscribePattern  on  cameras/+/state    (no publish rule)
A camera's user name and client ID are both the camera's UUID. All calls are repeatable: "already exists" is not an
error. The command shapes are those of the plugin's documentation; the response topic was checked on the pinned image.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

import aiomqtt

log = logging.getLogger("ccc.dynsec")

CONTROL_TOPIC = "$CONTROL/dynamic-security/v1"
RESPONSE_TOPIC = "$CONTROL/dynamic-security/v1/response"

CAMERA_ROLE = "camera"
INGEST_ROLE = "ingest"
INGEST_USER = "ingest"
CAMERA_STATE_TOPIC_ACL = "cameras/%c/state"
INGEST_TOPIC_FILTER = "cameras/+/state"


def role_commands() -> list[dict]:
    return [
        {"command": "createRole", "rolename": CAMERA_ROLE},
        {"command": "addRoleACL", "rolename": CAMERA_ROLE, "acltype": "publishClientSend",
         "topic": CAMERA_STATE_TOPIC_ACL, "priority": 0, "allow": True},
        {"command": "createRole", "rolename": INGEST_ROLE},
        {"command": "addRoleACL", "rolename": INGEST_ROLE, "acltype": "subscribePattern",
         "topic": INGEST_TOPIC_FILTER, "priority": 0, "allow": True},
    ]


def ingest_commands(password: str) -> list[dict]:
    return [
        {"command": "createClient", "username": INGEST_USER, "password": password,
         "roles": [{"rolename": INGEST_ROLE}]},
        {"command": "setClientPassword", "username": INGEST_USER, "password": password},
    ]


def camera_create_commands(camera_id: uuid.UUID, secret: str) -> list[dict]:
    cid = str(camera_id)
    return [{"command": "createClient", "username": cid, "password": secret, "clientid": cid,
             "roles": [{"rolename": CAMERA_ROLE}]}]


def camera_set_password_commands(camera_id: uuid.UUID, secret: str) -> list[dict]:
    return [{"command": "setClientPassword", "username": str(camera_id), "password": secret}]


def camera_delete_commands(camera_id: uuid.UUID) -> list[dict]:
    return [{"command": "deleteClient", "username": str(camera_id)}]


def real_errors(commands: list[dict], response: dict) -> list[str]:
    """Errors in a dynsec response other than 'already exists' (those make a repeat call harmless)."""
    out = []
    for cmd, res in zip(commands, response.get("responses", [])):
        err = res.get("error")
        if err and "already exists" not in err.lower():
            out.append(f"{cmd.get('command')}: {err}")
    if len(response.get("responses", [])) != len(commands):
        out.append("response count does not match command count")
    return out


class DynsecClient:
    def __init__(self, host: str, port: int, admin_user: str, admin_password: str) -> None:
        self._host, self._port, self._user, self._pw = host, port, admin_user, admin_password

    async def run(self, commands: list[dict]) -> dict:
        async with aiomqtt.Client(self._host, self._port, username=self._user, password=self._pw,
                                  identifier=f"ccc-dynsec-{uuid.uuid4().hex[:10]}",
                                  protocol=aiomqtt.ProtocolVersion.V5, keepalive=60) as c:
            await c.subscribe(RESPONSE_TOPIC, qos=1)
            await c.publish(CONTROL_TOPIC, json.dumps({"commands": commands}), qos=1)
            async with asyncio.timeout(10):
                async for m in c.messages:
                    return json.loads(m.payload)
        raise RuntimeError("no dynsec response")

    async def run_checked(self, commands: list[dict]) -> None:
        errs = real_errors(commands, await self.run(commands))
        if errs:
            raise RuntimeError("dynsec: " + "; ".join(errs))

    async def provision_base(self, ingest_password: str) -> None:
        await self.run_checked(role_commands())
        await self.run_checked(ingest_commands(ingest_password))

    async def add_camera(self, camera_id: uuid.UUID, secret: str, *, force_password: bool = False) -> None:
        await self.run_checked(camera_create_commands(camera_id, secret))
        if force_password:
            await self.run_checked(camera_set_password_commands(camera_id, secret))

    async def remove_camera(self, camera_id: uuid.UUID) -> None:
        await self.run(camera_delete_commands(camera_id))
