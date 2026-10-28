"""Checks on the files that configure the stack: realm import, compose, MediaMTX / Mosquitto settings,
migrations, and that the repository carries no secret or internal wording."""
import json
import pathlib
import re
import subprocess

import pytest

from app import seed_data as sd
from app.pgstore import list_migrations

ROOT = pathlib.Path(__file__).resolve().parents[2]
REALM = json.loads((ROOT / "infra/keycloak/ccc-realm.json").read_text(encoding="utf-8"))
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")
MEDIAMTX = (ROOT / "infra/mediamtx/mediamtx.yml").read_text(encoding="utf-8")
MOSQUITTO = (ROOT / "infra/mosquitto/mosquitto.conf").read_text(encoding="utf-8")


# ---- Keycloak realm

# The two users of the load test (loadtest/): they are in the realm but are not seeded; the load test gives them their
# memberships through the API. Their ids are repeated in loadtest/common.py and must equal these.
LOAD_USERS = {"l-admin": "40000000-0000-4000-8000-0000000000e1", "l-viewer": "40000000-0000-4000-8000-0000000000e2"}


def test_realm_has_users_only_with_the_fixed_ids():
    users = {u["username"]: u["id"] for u in REALM["users"]}
    assert users == {
        "partner-admin": str(sd.USER_PARTNER_ADMIN), "a-admin": str(sd.USER_A_ADMIN),
        "a-viewer": str(sd.USER_A_VIEWER), "b-admin": str(sd.USER_B_ADMIN), "b-viewer": str(sd.USER_B_VIEWER),
        "c-admin": str(sd.USER_C_ADMIN), "c-viewer": str(sd.USER_C_VIEWER), **LOAD_USERS}
    for key in ("groups", "roles", "organizations", "components"):
        assert key not in REALM or not REALM[key]   # no tenants / roles / groups in Keycloak: it only says who you are


def test_realm_ids_match_the_seeded_memberships():
    seeded = {m[0] for m in sd.MEMBERSHIPS + sd.EXTRA_MEMBERSHIPS}
    realm_ids = {__import__("uuid").UUID(u["id"]) for u in REALM["users"]}
    assert seeded <= realm_ids
    assert realm_ids - seeded == {__import__("uuid").UUID(v) for v in LOAD_USERS.values()}


def test_load_test_user_ids_equal_the_realm():
    import importlib.util
    spec = importlib.util.spec_from_file_location("loadtest_common", ROOT / "loadtest/common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert {k: v for k, v in mod.USER_SUB.items() if k in LOAD_USERS} == LOAD_USERS
    assert mod.USER_SUB["partner-admin"] == str(sd.USER_PARTNER_ADMIN)


def test_realm_has_no_identity_provider():
    assert REALM["identityProviders"] == [] and REALM["identityProviderMappers"] == []


def test_realm_client_is_public_pkce_s256_with_one_exact_redirect():
    (c,) = [x for x in REALM["clients"] if x["clientId"] == "ccc-web"]
    assert c["publicClient"] is True
    assert c["standardFlowEnabled"] is True
    assert c["implicitFlowEnabled"] is False and c["directAccessGrantsEnabled"] is False
    assert c["serviceAccountsEnabled"] is False
    assert c["attributes"]["pkce.code.challenge.method"] == "S256"
    assert c["redirectUris"] == ["http://localhost:8000/"] and c["webOrigins"] == ["http://localhost:8000"]
    assert c["attributes"]["post.logout.redirect.uris"] == "http://localhost:8000/"
    assert all("*" not in u for u in c["redirectUris"] + c["webOrigins"])


def test_the_device_client_can_only_register_a_camera():
    (c,) = [x for x in REALM["clients"] if x["clientId"] == "ccc-device"]
    a = c["attributes"]
    assert c["publicClient"] is True and c["consentRequired"] is True and c["fullScopeAllowed"] is False
    assert not (c["standardFlowEnabled"] or c["implicitFlowEnabled"] or c["directAccessGrantsEnabled"]
                or c["serviceAccountsEnabled"])
    assert a["oauth2.device.authorization.grant.enabled"] == "true"        # a string: Keycloak compares with "true"
    assert (a["oauth2.device.code.lifespan"], a["oauth2.device.polling.interval"]) == ("600", "5")
    assert a["use.refresh.tokens"] == "false"
    assert c["defaultClientScopes"] == ["basic"] and c["optionalClientScopes"] == []   # no `account` audience, no roles
    (m,) = c["protocolMappers"]
    assert m["protocolMapper"] == "oidc-audience-mapper" and m["config"]["included.custom.audience"] == "ccc-enroll"
    web, = [x for x in REALM["clients"] if x["clientId"] == "ccc-web"]
    assert web["attributes"]["oauth2.device.authorization.grant.enabled"] == "false"   # the page's client cannot do it


def test_realm_password_comes_from_the_environment_not_the_file():
    for u in REALM["users"]:
        (cred,) = u["credentials"]
        assert cred["value"] == "${DEMO_USER_PASSWORD}" and cred["temporary"] is False


# ---- compose

def service_names():
    return re.findall(r"^  ([a-z0-9-]+):\n", COMPOSE.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0], re.M)


def test_the_services():
    assert sorted(service_names()) == sorted(
        ["init", "postgres", "keycloak", "mosquitto", "api", "valkey", "writer", "mediamtx", "gateway",
         "cam-a1", "cam-a2", "cam-b1",
         "postgres-s2", "mosquitto-s2", "valkey-s2", "writer-s2", "mediamtx-s2", "cam-c1", "cam-device",
         "motion", "classify", "cam-b2"])


def service_block(name):
    body = COMPOSE.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    return re.split(rf"^  {re.escape(name)}:\n", body, flags=re.M)[1].split("\n\n", 1)[0]


def test_the_first_api_has_been_taken_out():
    assert "api-v1" not in COMPOSE and "1.0.1-frozen" not in COMPOSE


# The services that may be on the queue's network: the queue, its writer, and the two workers of the profile `vision`.
ON_THE_QUEUE_NETWORK = ("valkey", "writer", "motion", "classify")


def test_the_queue_is_alone_on_an_internal_network_with_its_writer_and_its_two_workers_and_gets_no_port():
    valkey, writer = service_block("valkey"), service_block("writer")
    assert re.search(r"image: valkey/valkey:9\.1\.2-alpine@sha256:[0-9a-f]{64}", valkey)
    assert "networks: [stamp1]" in valkey and "ports:" not in valkey
    assert "networks: [default, stamp1]" in writer
    assert re.search(r"^  stamp1:\n    internal: true", COMPOSE, re.M)
    for name in service_names():       # nothing else is on that network
        if name not in ON_THE_QUEUE_NETWORK:
            assert "stamp1" not in service_block(name), name


def test_the_vision_workers_have_the_queue_network_only_no_port_and_the_usual_hardening():
    for name in ("motion", "classify"):
        b = service_block(name)
        assert "profiles: [vision]" in b, name              # a plain `docker compose up` does not start them
        assert "networks: [stamp1]" in b and "default" not in b, name     # not the network of the API, the database, the cameras
        assert "ports:" not in b, name
        for needle in ("read_only: true", "tmpfs: [/tmp]", "no-new-privileges:true", "cap_drop: [ALL]", "mem_limit:"):
            assert needle in b, (name, needle)
        assert "user:" not in b and "privileged" not in b      # the image's own user (not root)
        # what they read is mounted read only
        for mount in re.findall(r"^\s+- (?:\./|recordings:)\S+", b, re.M):
            assert mount.rstrip().endswith(":ro"), (name, mount)
    assert "subpath: vision1/motion" in service_block("motion") and "subpath: vision1/classify" in service_block("classify")
    assert "capabilities: [gpu]" in service_block("classify") and "gpu" not in service_block("motion")
    assert 'restart: "on-failure:3"' in service_block("classify")
    # the image holds no video, no weights, no picture
    docker = (ROOT / "services/vision/Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^COPY .*(var/|\.mp4|\.bin|\.jpg)", docker, re.M)
    assert "var" in (ROOT / ".dockerignore").read_text(encoding="utf-8").split()


def test_the_writer_gets_only_its_own_stamps_folder_of_the_secrets():
    block = service_block("writer")
    assert "subpath: stamp1" in block and "subpath: api" not in block
    assert "subpath: valkey1" in service_block("valkey")


def test_every_published_port_is_on_loopback_and_only_the_expected_ones():
    ports = re.findall(r'^\s+- "([^"]+:\d+:\d+)"', COMPOSE, re.M)
    assert sorted(ports) == ["127.0.0.1:8000:8000", "127.0.0.1:8080:8080", "127.0.0.1:8888:8888",
                             "127.0.0.1:8889:8888", "127.0.0.1:9996:9996", "127.0.0.1:9998:9996"]


def test_every_long_running_service_has_a_memory_limit():
    body = COMPOSE.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    blocks = re.split(r"^  (?=[a-z0-9-]+:\n)", body, flags=re.M)
    for b in blocks:
        name = b.split(":", 1)[0]
        if name in service_names() and not b.lstrip().startswith("<<"):
            assert "mem_limit:" in b or "<<: *camera" in b, name
    assert "mem_limit: 98m" in COMPOSE.split("x-camera", 1)[1].split("services:", 1)[0]


def test_data_lives_in_named_volumes_not_bind_mounts():
    binds = re.findall(r"^\s+- (\./[^:]+):", COMPOSE, re.M)
    # only read-only config files are bind mounted
    for line in re.findall(r"^\s+- \./.*$", COMPOSE, re.M):
        assert line.rstrip().endswith(":ro"), line
    assert binds
    for v in ("secrets:", "pgdata:", "recordings:", "dynsec:", "valkeydata:", "pgdata2:", "recordings2:", "dynsec2:",
              "valkeydata2:", "devicecam:", "checkcam:"):
        assert re.search(rf"^  {v}$", COMPOSE, re.M)


def test_keycloak_flags_and_hostname():
    assert "--features-disabled=impersonation,token-exchange-standard" in COMPOSE
    assert "KC_HOSTNAME: http://localhost:8080" in COMPOSE and 'KC_HOSTNAME_BACKCHANNEL_DYNAMIC: "true"' in COMPOSE
    assert "start-dev" in COMPOSE


def test_each_camera_mounts_only_its_own_secret():
    for cid in (sd.CAM_A1, sd.CAM_A2, sd.CAM_B1):
        block = COMPOSE.split(f"CAMERA_ID: {cid}", 1)[1].split("\n\n", 1)[0]
        assert f"subpath: cameras/{cid}" in block


def test_pinned_image_tags():
    for image in ("quay.io/keycloak/keycloak:26.8.1", "bluenviron/mediamtx:1.21.2-ffmpeg",
                  "eclipse-mosquitto:2.1.2-alpine", "postgres:17.11-alpine", "valkey/valkey:9.1.2-alpine"):
        assert f"image: {image}" in COMPOSE


# ---- MediaMTX and Mosquitto

def test_mediamtx_settings():
    for line in ("authMethod: jwt", "authJWTJWKS: http://api:8000/jwks", "authJWTIssuer: ccc-api",
                 "authJWTAudience: mediamtx", "recordSegmentDuration: 60s", "recordFormat: fmp4",
                 "recordDeleteAfter: 1d", 'hlsAllowOrigins: ["http://localhost:8000"]',
                 'playbackAllowOrigins: ["http://localhost:8000"]',
                 "recordPath: ./recordings/%path/%Y-%m-%d_%H-%M-%S-%f"):
        assert line in MEDIAMTX, line
    assert "authJWTExclude" not in MEDIAMTX   # no unauthenticated door
    assert "*" not in re.findall(r"AllowOrigins: (.*)", MEDIAMTX)[0] + re.findall(r"AllowOrigins: (.*)", MEDIAMTX)[1]


def test_segment_hook_posts_the_three_variables_with_the_secret_header():
    sh = (ROOT / "infra/mediamtx/segment-hook.sh").read_text()
    for needle in ("X-Hook-Secret", "${MTX_PATH}", "${MTX_SEGMENT_PATH}", "${MTX_SEGMENT_DURATION}",
                   "http://writer:8000/internal/v1/segments", "--post-data"):
        assert needle in sh


def test_mosquitto_settings():
    assert re.search(r"^allow_anonymous false$", MOSQUITTO, re.M)
    assert len(re.findall(r"^listener ", MOSQUITTO, re.M)) == 1
    assert "http_api" not in MOSQUITTO
    assert "plugin /usr/lib/mosquitto_dynamic_security.so" in MOSQUITTO


# ---- migrations

def test_migrations_are_numbered_without_gaps_and_never_edit_history():
    ms = list_migrations()
    assert [v.split("_")[0] for v, _ in ms] == [f"{i:04d}" for i in range(1, len(ms) + 1)]
    sql = "\n".join(s for _, s in ms)
    for table in ("cameras", "camera_status", "recording_segments"):
        assert re.search(rf"CREATE TABLE {table} \(", sql)
    # every tenant-owned table carries the tenant id
    assert "tenant_id" in sql.split("CREATE TABLE camera_status")[1].split(");")[0]
    assert "PRIMARY KEY (camera_id, started_at)" in sql


# ---- repository hygiene

def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    return [f for f in out if f and not f.endswith((".png", ".ico", "package-lock.json"))]


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs the git repository")
def test_no_internal_wording_or_secret_files_are_tracked():
    # the words that must not be published are kept in an untracked local file (git ignores it), so the list itself is
    # not published either; without that file only the secret-file checks below run
    local = ROOT / "tests" / "banned_words.local.txt"
    banned = ([w.strip() for w in local.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
              if local.exists() else [])
    bad = []
    for f in tracked_files():
        if f.startswith("tests/unit/test_static_config.py"):
            continue
        text = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
        for b in banned:
            if b in text:
                bad.append((f, b))
    assert bad == []
    names = tracked_files()
    assert not any(n == ".env" or n.endswith((".pem", ".key")) for n in names)
    assert not any(n.startswith(("web/node_modules/", ".venv/")) for n in names)


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs the git repository")
def test_env_example_is_tracked_and_has_every_variable_compose_requires():
    # the README starts with `copy .env.example .env`, so a fresh clone must have the file
    assert ".env.example" in tracked_files()
    required = set(re.findall(r"\$\{([A-Z_]+):\?", COMPOSE))
    given = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#") and "=" in line}
    assert required and required <= given, required - given
