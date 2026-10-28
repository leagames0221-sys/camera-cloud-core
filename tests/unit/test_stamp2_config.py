"""The files that set up the second stamp: its list entry, its MediaMTX settings, its tables, and what its containers get."""
import json
import pathlib
import re

from app import config, seed_data as sd
from app.pgstore import STAMP_MIGRATIONS_DIR, list_migrations
from app.stamps import StampInfo, read_stamps_file

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")


def block(name):
    body = COMPOSE.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    return re.split(rf"^  {re.escape(name)}:\n", body, flags=re.M)[1].split("\n\n", 1)[0]


def test_the_stamp_list_has_the_first_stamp_as_it_always_was_and_a_second_that_shares_nothing():
    s1, s2 = read_stamps_file(ROOT / "infra/stamps.json")
    assert s1 == StampInfo.from_settings(config.Settings())      # same as the defaults and the migration's first row
    sql = (ROOT / "services/api/migrations/0003_stamps.sql").read_text(encoding="utf-8")
    assert "'postgresql://ccc@postgres:5432/ccc'" in sql and "'mediamtx')" in sql
    assert (s2.id, s2.jwt_audience) == (2, "mediamtx-s2")
    assert s1.jwt_audience != s2.jwt_audience               # a token for one stamp's video server is not for the other
    for field in ("database_dsn", "mqtt_host", "mediamtx_control_url", "public_hls_url", "public_playback_url",
                  "public_rtsp_url", "public_mqtt_url"):
        assert getattr(s1, field) != getattr(s2, field), field
    assert s2.database_password_file and s2.mqtt_admin_password_file


def test_the_second_video_server_differs_from_the_first_in_one_line_only():
    a = (ROOT / "infra/mediamtx/mediamtx.yml").read_text(encoding="utf-8").splitlines()
    b = (ROOT / "infra/mediamtx/mediamtx-s2.yml").read_text(encoding="utf-8").splitlines()
    extra = [line for line in b if line not in a and not line.startswith("#")]    # comment lines may differ
    assert extra == ["authJWTAudience: mediamtx-s2"]
    assert [l for l in a if l not in b] == ["authJWTAudience: mediamtx"]


def test_the_second_hook_posts_to_the_second_writer_with_the_same_check():
    a = (ROOT / "infra/mediamtx/segment-hook.sh").read_text(encoding="utf-8")
    b = (ROOT / "infra/mediamtx/segment-hook-s2.sh").read_text(encoding="utf-8")
    assert b == a.replace("http://writer:8000/", "http://writer-s2:8000/") and "writer-s2" in b


def test_the_tables_of_a_later_stamp_are_numbered_and_match_the_control_database():
    ms = list_migrations(STAMP_MIGRATIONS_DIR)
    assert [v.split("_")[0] for v, _ in ms] == [f"{i:04d}" for i in range(1, len(ms) + 1)]
    sql = "\n".join(s for _, s in ms)
    control = "\n".join(s for _, s in list_migrations())
    for table in ("camera_status", "recording_segments"):
        mine = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", sql, re.S).group(1)
        theirs = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", control, re.S).group(1)
        assert re.sub(r"\s+", " ", mine) == re.sub(r"\s+", " ", theirs), table     # the same columns, keys and checks
    # what the writer's statements join on is there
    assert re.search(r"CREATE TABLE cameras \(.*?stamp_id\s+integer NOT NULL.*?deleted_at\s+timestamptz", sql, re.S)


def test_the_enroll_token_table_has_the_token_id_as_its_key():
    sql = "\n".join(s for _, s in list_migrations())
    assert re.search(r"CREATE TABLE enroll_tokens \(\s*jti\s+text PRIMARY KEY", sql)


def test_stamp_2_has_its_own_database_broker_queue_writer_and_video_server():
    assert "subpath: postgres2" in block("postgres-s2") and "subpath: mosquitto2" in block("mosquitto-s2")
    assert "subpath: valkey2" in block("valkey-s2") and "subpath: stamp2" in block("writer-s2")
    assert "subpath: mediamtx2" in block("mediamtx-s2")
    assert "networks: [stamp2]" in block("valkey-s2") and "networks: [default, stamp2]" in block("writer-s2")
    for name in ("postgres-s2", "mosquitto-s2", "mediamtx-s2", "api", "writer", "valkey"):
        assert "stamp2" not in block(name).replace("stamps.json", ""), name
    # the second writer reaches the second database with the password from its own folder, not from the address
    w = block("writer-s2")
    assert "postgres-s2" in w and "DATABASE_PASSWORD_FILE: db_password" in w and "POSTGRES_PASSWORD" not in w
    assert "POSTGRES_PASSWORD_FILE: /run/secret/db_password" in block("postgres-s2")


def test_company_c_has_its_own_camera_whose_broker_and_video_server_are_the_second_stamps():
    c = block("cam-c1")
    assert str(sd.CAM_C1) in c and "MQTT_HOST: mosquitto-s2" in c and "RTSP_BASE: rtsp://mediamtx-s2:8554" in c
    assert f"subpath: cameras/{sd.CAM_C1}" in c
    assert sd.EXTRA_TENANTS == [(sd.TENANT_C, "C 社", 2)] and [x[1] for x in sd.EXTRA_CAMERAS] == [sd.TENANT_C]


def test_the_camera_that_registers_itself_starts_only_when_asked_and_keeps_no_secret_from_us():
    d = block("cam-device")
    assert "profiles: [device]" in d and "ENROLL: device" in d and "devicecam:/data" in d
    assert "subpath" not in d and "secrets" not in d          # no secret is mounted: it gets its own by registering


def test_the_sample_data_of_company_c_is_apart_from_the_lists_the_earlier_tests_read():
    assert sd.TENANT_C not in {t for t, _ in sd.TENANTS} and sd.CAM_C1 not in {c for c, _, _ in sd.CAMERAS}
    assert {m[0] for m in sd.EXTRA_MEMBERSHIPS}.isdisjoint({m[0] for m in sd.MEMBERSHIPS})
    assert json.loads((ROOT / "infra/stamps.json").read_text(encoding="utf-8"))[1]["id"] == sd.STAMP_C
