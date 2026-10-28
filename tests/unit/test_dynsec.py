"""The MQTT roles: a camera may publish only to its own topic (%c) and cannot subscribe; the intake user may only
subscribe; repeating the commands is harmless."""
import uuid

from app import dynsec

CID = uuid.UUID("30000000-0000-4000-8000-0000000000a1")


def acls(commands, role):
    return [(c["acltype"], c["topic"], c["allow"]) for c in commands
            if c["command"] == "addRoleACL" and c["rolename"] == role]


def test_camera_role_is_one_publish_rule_on_its_own_topic():
    cmds = dynsec.role_commands()
    assert acls(cmds, "camera") == [("publishClientSend", "cameras/%c/state", True)]


def test_camera_topic_rule_is_a_whole_level_pattern():
    # '%c' must be the only thing in its level, or the plugin does not treat it as a pattern
    topic = dynsec.CAMERA_STATE_TOPIC_ACL
    assert topic.split("/")[1] == "%c" and "+" not in topic and "#" not in topic


def test_ingest_role_can_only_subscribe_to_state_topics():
    cmds = dynsec.role_commands()
    assert acls(cmds, "ingest") == [("subscribePattern", "cameras/+/state", True)]
    assert not any(a[0].startswith("publish") for a in acls(cmds, "ingest"))


def test_no_role_grants_subscribe_to_cameras_or_anything_to_everybody():
    for c in dynsec.role_commands():
        if c["command"] == "addRoleACL" and c["rolename"] == "camera":
            assert not c["acltype"].startswith("subscribe")
        if c["command"] == "addRoleACL":
            assert c["topic"] not in ("#", "+/#", "cameras/#")


def test_camera_user_name_and_client_id_are_the_camera_uuid():
    (cmd,) = dynsec.camera_create_commands(CID, "s3cret")
    assert cmd["username"] == cmd["clientid"] == str(CID)
    assert cmd["roles"] == [{"rolename": "camera"}] and cmd["password"] == "s3cret"


def test_ingest_user_has_only_the_ingest_role():
    create, setpw = dynsec.ingest_commands("pw")
    assert create["roles"] == [{"rolename": "ingest"}] and create["username"] == "ingest"
    assert setpw["command"] == "setClientPassword"


def test_already_exists_is_not_an_error_but_anything_else_is():
    cmds = dynsec.role_commands()[:2]
    ok = {"responses": [{"command": "createRole", "error": "Role already exists"}, {"command": "addRoleACL"}]}
    assert dynsec.real_errors(cmds, ok) == []
    bad = {"responses": [{"command": "createRole"}, {"command": "addRoleACL", "error": "Role not found"}]}
    assert dynsec.real_errors(cmds, bad) == ["addRoleACL: Role not found"]
    assert dynsec.real_errors(cmds, {"responses": []}) == ["response count does not match command count"]
