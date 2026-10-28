"""The one-shot secrets job: makes each secret once, gives each service only its own folder, owner-only modes."""
import os
import stat

import pytest

from app import init_secrets as init
from app import seed_data as sd


@pytest.fixture
def owners(monkeypatch):
    seen: dict[str, int] = {}

    def fake_chown(path, uid, gid):
        seen[str(path)] = uid

    monkeypatch.setattr(os, "chown", fake_chown, raising=False)  # the job runs as root in its container
    return seen


def test_makes_every_secret_once_and_keeps_them(tmp_path, owners):
    made = init.init_secrets(tmp_path)
    assert "video_signing_key" in made and len([m for m in made if m.startswith("camera ")]) == 4
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert init.init_secrets(tmp_path) == []
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_each_service_folder_holds_only_its_own_secrets(tmp_path, owners):
    init.init_secrets(tmp_path)
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert {f for f in files if f.startswith("mediamtx/")} == {"mediamtx/hook_secret"}
    assert {f for f in files if f.startswith("mosquitto/")} == {"mosquitto/dynsec_admin_password"}
    assert {f for f in files if f.startswith("cameras/")} == {f"cameras/{c}/secret" for c, _, _ in sd.CAMERAS + sd.EXTRA_CAMERAS}
    # the camera folder and the API's copy carry the same secret, and different cameras different ones
    values = {(tmp_path / f"cameras/{c}/secret").read_text() for c, _, _ in sd.CAMERAS + sd.EXTRA_CAMERAS}
    assert len(values) == 4
    for c, _, _ in sd.CAMERAS + sd.EXTRA_CAMERAS:
        assert (tmp_path / f"cameras/{c}/secret").read_text() == (tmp_path / f"api/cameras/{c}.secret").read_text()
    assert (tmp_path / "api/hook_secret").read_text() == (tmp_path / "mediamtx/hook_secret").read_text()


def test_api_and_writer_folders_belong_to_the_api_user_the_queue_folder_to_its_user_and_the_rest_to_root(tmp_path, owners):
    init.init_secrets(tmp_path)
    expected_by_folder = {"api": init.API_UID, "stamp1": init.API_UID, "valkey1": init.VALKEY_UID,
                          "stamp2": init.API_UID, "valkey2": init.VALKEY_UID, "vision1": init.VISION_UID}
    for p in tmp_path.rglob("*"):
        rel = p.relative_to(tmp_path).as_posix()
        assert owners[str(p)] == expected_by_folder.get(rel.split("/")[0], init.ROOT_UID), rel
    assert owners[str(tmp_path / "api" / "video_signing_key.pem")] == 10001


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_files_are_owner_only(tmp_path, owners):
    init.init_secrets(tmp_path)
    for p in tmp_path.rglob("*"):
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == (0o500 if p.is_dir() else 0o400), (p, oct(mode))


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_files_from_an_earlier_version_are_tightened(tmp_path, owners):
    init.init_secrets(tmp_path)
    key = tmp_path / "api" / "video_signing_key.pem"
    for p in tmp_path.rglob("*"):
        p.chmod(0o755 if p.is_dir() else 0o644)  # what the earlier version wrote
    init.init_secrets(tmp_path)
    assert stat.S_IMODE(key.stat().st_mode) == 0o400


def test_writer_folder_has_no_signing_key_and_the_queue_gets_only_a_hash(tmp_path, owners):
    init.init_secrets(tmp_path)
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert {f for f in files if f.startswith("stamp1/")} == {"stamp1/hook_secret", "stamp1/mqtt_ingest_password",
                                                            "stamp1/valkey_password"}
    assert {f for f in files if f.startswith("valkey1/")} == {"valkey1/users.acl"}
    assert (tmp_path / "stamp1/hook_secret").read_text() == (tmp_path / "mediamtx/hook_secret").read_text()
    assert (tmp_path / "stamp1/mqtt_ingest_password").read_text() == (tmp_path / "api/mqtt_ingest_password").read_text()
    password = (tmp_path / "stamp1/valkey_password").read_text()
    acl = (tmp_path / "valkey1/users.acl").read_text()
    assert password not in acl and "user default off" in acl
    import hashlib
    assert f"user writer on #{hashlib.sha256(password.encode()).hexdigest()} " in acl


def test_a_later_stamp_has_secrets_of_its_own_and_the_control_plane_holds_copies(tmp_path, owners):
    init.init_secrets(tmp_path)
    r = lambda rel: (tmp_path / rel).read_text()   # noqa: E731
    # nothing of stamp 1 is reused by stamp 2
    for name in ("hook_secret", "mqtt_ingest_password", "valkey_password"):
        assert r(f"stamp2/{name}") != r(f"stamp1/{name}")
    assert r("mosquitto2/dynsec_admin_password") != r("mosquitto/dynsec_admin_password")
    # each service of stamp 2 gets its own folder with only what it needs
    assert r("mediamtx2/hook_secret") == r("stamp2/hook_secret")
    assert r("postgres2/db_password") == r("stamp2/db_password")
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert {f for f in files if f.startswith(("mediamtx2/", "mosquitto2/", "postgres2/"))} == {
        "mediamtx2/hook_secret", "mosquitto2/dynsec_admin_password", "postgres2/db_password"}
    # the control plane's copies, in the API folder (the writer's folder has no signing key)
    assert r("api/stamp2_db_password") == r("stamp2/db_password")
    assert r("api/stamp2_mqtt_admin_password") == r("mosquitto2/dynsec_admin_password")
    assert r("api/stamp2_mqtt_ingest_password") == r("stamp2/mqtt_ingest_password")
    assert not any(f.endswith(".pem") for f in files if f.startswith(("stamp", "valkey", "mediamtx", "mosquitto", "postgres")))
    # running it again changes nothing
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert init.init_secrets(tmp_path) == []
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
