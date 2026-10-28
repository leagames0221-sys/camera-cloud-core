"""A camera registers itself with the access token of the device authorization grant: who may, once only, and what the
other doors say to that token. Also the copy of the camera ledger in a stamp's own database."""
import asyncio
import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from app import seed_data as sd
from app.api import create_app
from app.domain import Membership
from app.oidc import DeviceTokenVerifier
from app.stamps import RoutedStore, StampRegistry, StampUnavailable
from app.store import MemoryStore
from test_stamps_queue_throttle import info

DEVICE = "ccc-device"
ENROLL = "ccc-enroll"


def device_token(world, user, *, azp=DEVICE, aud=ENROLL, jti="default", typ="Bearer", exp_in=300, extra=None):
    claims = {"iss": "http://localhost:8080/realms/ccc", "sub": str(user), "azp": azp, "typ": typ,
              "exp": int(time.time()) + exp_in}
    if aud is not None:
        claims["aud"] = aud
    claims["jti"] = uuid.uuid4().hex if jti == "default" else jti
    if claims["jti"] is None:
        del claims["jti"]
    claims.update(extra or {})
    return jwt.encode(claims, world.rsa, algorithm="RS256", headers={"kid": "kc1"})


@pytest.fixture
def enroll_client(world):
    async def factory():
        d = world.deps()
        d.device_verifier = DeviceTokenVerifier(world.verifier, DEVICE, ENROLL)
        return d

    with TestClient(create_app(world.settings, factory)) as c:
        yield c


def enroll(c, token, name="door camera"):
    return c.post("/api/v1/devices/enroll", json={"name": name}, headers={"Authorization": f"Bearer {token}"})


def test_a_tenant_admin_registers_a_camera_once_with_the_token(world, enroll_client):
    t = device_token(world, sd.USER_A_ADMIN)
    r = enroll(enroll_client, t)
    assert r.status_code == 201
    body = r.json()
    assert body["tenant_id"] == str(sd.TENANT_A) and body["credential_type"] == "secret" and body["secret"]
    assert body["mqtt_url"] and body["mediamtx_url"].endswith(f"{sd.TENANT_A}/{body['id']}")
    assert [a[0] for a in world.dynsec.added][-1] == uuid.UUID(body["id"])
    # the same token a second time: refused, and no second camera
    n = len(world.dynsec.added)
    assert enroll(enroll_client, t).status_code == 401
    assert len(world.dynsec.added) == n


def test_a_viewer_a_partner_admin_and_a_stranger_cannot(world, enroll_client):
    assert enroll(enroll_client, device_token(world, sd.USER_A_VIEWER)).status_code == 403
    assert enroll(enroll_client, device_token(world, sd.USER_PARTNER_ADMIN)).status_code == 403
    assert enroll(enroll_client, device_token(world, uuid.uuid4())).status_code == 403
    assert world.dynsec.added == []


def test_an_admin_of_two_tenants_gets_409_because_the_target_is_not_known(world, enroll_client):
    asyncio.run(world.store.add_membership(Membership(sd.USER_A_ADMIN, "tenant_admin", None, sd.TENANT_B)))
    assert enroll(enroll_client, device_token(world, sd.USER_A_ADMIN)).status_code == 409
    assert world.dynsec.added == []


@pytest.mark.parametrize("kw", [
    {"azp": "ccc-web"},            # the viewing page's own token
    {"azp": "account"},
    {"aud": None},                 # no audience
    {"aud": "account"},
    {"aud": ["account", "other"]},
    {"typ": "ID"},
    {"typ": "Refresh"},
    {"jti": None},
    {"exp_in": -5},
])
def test_a_token_that_is_wrong_in_one_way_is_refused(world, enroll_client, kw):
    assert enroll(enroll_client, device_token(world, sd.USER_A_ADMIN, **kw)).status_code == 401
    assert world.dynsec.added == []


def test_no_token_a_wrong_key_and_a_missing_verifier_are_refused(world, enroll_client):
    assert enroll_client.post("/api/v1/devices/enroll", json={"name": "x"}).status_code == 401
    assert enroll(enroll_client, "garbage").status_code == 401
    other = jwt.encode({"iss": "http://localhost:8080/realms/ccc", "sub": str(sd.USER_A_ADMIN), "azp": DEVICE,
                        "aud": ENROLL, "typ": "Bearer", "jti": "x", "exp": int(time.time()) + 60},
                       "x" * 40, algorithm="HS256", headers={"kid": "kc1"})
    assert enroll(enroll_client, other).status_code == 401


def test_the_audience_may_be_one_of_several(world, enroll_client):
    assert enroll(enroll_client, device_token(world, sd.USER_A_ADMIN, aud=["account", ENROLL])).status_code == 201


def test_the_device_token_opens_no_other_door(world, enroll_client):
    t = device_token(world, sd.USER_A_ADMIN)
    h = {"Authorization": f"Bearer {t}"}
    assert enroll_client.get("/api/v1/me", headers=h).status_code == 401
    assert enroll_client.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras", headers=h).status_code == 401
    assert enroll_client.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": "x"}, headers=h).status_code == 401
    assert enroll_client.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}/live", headers=h).status_code == 401
    assert enroll_client.delete(f"/api/v1/tenants/{sd.TENANT_A}/cameras/{sd.CAM_A1}", headers=h).status_code == 401


def test_the_viewing_pages_token_does_not_register_a_camera(world, enroll_client):
    assert enroll(enroll_client, world.token(sd.USER_A_ADMIN)).status_code == 401


def test_the_door_is_closed_when_no_verifier_is_set(world):
    async def factory():
        return world.deps()

    with TestClient(create_app(world.settings, factory)) as c:
        assert enroll(c, device_token(world, sd.USER_A_ADMIN)).status_code == 401


# ---------------------------------------------------------------- the ledger copy in a stamp's own database

def make_routed():
    control, other = MemoryStore(), MemoryStore()

    async def make(i):
        return other

    return control, other, RoutedStore(control, StampRegistry({1: info(1), 2: info(2)}, control, make))


def test_a_camera_of_a_later_stamp_is_copied_there_and_its_removal_too():
    async def run():
        control, other, store = make_routed()
        await store.seed_partner(sd.PARTNER_ID, "p")
        t1 = await store.create_tenant(sd.PARTNER_ID, "one", stamp_id=1)
        t2 = await store.create_tenant(sd.PARTNER_ID, "two", stamp_id=2)
        c1 = await store.create_camera(t1.id, "a", "h")
        c2 = await store.create_camera(t2.id, "b", "h")
        assert other.mirrored == {c2.id: (t2.id, 2, False)}       # only the second stamp's camera, in the second stamp
        assert await store.delete_camera(t2.id, c2.id)
        assert other.mirrored == {c2.id: (t2.id, 2, True)}
        assert await store.delete_camera(t1.id, c1.id) and len(other.mirrored) == 1
    asyncio.run(run())


def test_without_the_copy_the_camera_is_not_registered():
    async def run():
        control = MemoryStore()

        async def make(i):
            raise ConnectionError("switched off")

        store = RoutedStore(control, StampRegistry({1: info(1), 2: info(2)}, control, make))
        await store.seed_partner(sd.PARTNER_ID, "p")
        t2 = await store.create_tenant(sd.PARTNER_ID, "two", stamp_id=2)
        with pytest.raises(StampUnavailable):
            await store.create_camera(t2.id, "b", "h")
        assert await control.list_cameras(t2.id) == []             # the ledger row was taken back
        # seeding does not fail, it is made right when the stamp is up
        await store.seed_camera(sd.CAM_C1, t2.id, "c", "h")
        assert [c.id for c in await control.list_cameras(t2.id)] == [sd.CAM_C1]
    asyncio.run(run())


def test_sync_copies_every_camera_of_the_stamp():
    async def run():
        control, other, store = make_routed()
        await store.seed_partner(sd.PARTNER_ID, "p")
        t2 = await store.create_tenant(sd.PARTNER_ID, "two", stamp_id=2)
        t1 = await store.create_tenant(sd.PARTNER_ID, "one", stamp_id=1)
        await control.seed_camera(sd.CAM_C1, t2.id, "c", "h")        # in the ledger only
        await control.seed_camera(sd.CAM_A1, t1.id, "a", "h")
        assert await store.sync_stamp(2) == 1
        assert list(other.mirrored) == [sd.CAM_C1]
    asyncio.run(run())


def test_a_camera_registered_in_a_later_stamp_uses_that_stamps_broker(world):
    from conftest import FakeDynsec
    world.store._tenants[sd.TENANT_B] = world.store._tenants[sd.TENANT_B].__class__(sd.TENANT_B, sd.PARTNER_ID, "B", 2)
    second = FakeDynsec()
    reg = StampRegistry({1: info(1, public_mqtt_url="mqtt://one"), 2: info(2, public_mqtt_url="mqtt://two")},
                        world.store, None)

    async def factory():
        d = world.deps()
        d.stamps = reg
        d.dynsec_by_stamp = {1: world.dynsec, 2: second}
        return d

    with TestClient(create_app(world.settings, factory)) as c:
        r = c.post(f"/api/v1/tenants/{sd.TENANT_B}/cameras", json={"name": "n"}, headers=world.auth(sd.USER_B_ADMIN))
        assert r.status_code == 201 and r.json()["mqtt_url"] == "mqtt://two"
        assert len(second.added) == 1 and world.dynsec.added == []
        r = c.post(f"/api/v1/tenants/{sd.TENANT_A}/cameras", json={"name": "n"}, headers=world.auth(sd.USER_A_ADMIN))
        assert r.json()["mqtt_url"] == "mqtt://one" and len(world.dynsec.added) == 1


# ---------------------------------------------------------------- a stamp that is down must not slow the others

class PathsOf:
    def __init__(self, paths=None, fail=False):
        self.paths, self.fail, self.calls = paths or set(), fail, 0

    async def ready_paths(self):
        self.calls += 1
        if self.fail:
            raise AssertionError("this stamp must not be asked")
        return set(self.paths)


class FakePool:
    def __init__(self, clients):
        self.clients = clients

    def for_stamp(self, sid):
        return self.clients[sid]


def test_the_camera_list_asks_only_the_tenants_own_stamp_for_running_streams(world):
    from app.domain import video_path
    world.store._tenants[sd.TENANT_B] = world.store._tenants[sd.TENANT_B].__class__(sd.TENANT_B, sd.PARTNER_ID, "B", 2)
    one = PathsOf({video_path(sd.TENANT_A, sd.CAM_A1)})
    two = PathsOf({video_path(sd.TENANT_B, sd.CAM_B1)})
    reg = StampRegistry({1: info(1), 2: info(2)}, world.store, None)

    async def factory():
        d = world.deps()
        d.stamps, d.mediamtx = reg, FakePool({1: one, 2: two})
        return d

    with TestClient(create_app(world.settings, factory)) as c:
        a = c.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras", headers=world.auth(sd.USER_A_VIEWER)).json()
        assert one.calls == 1 and two.calls == 0
        assert {x["id"]: x["state"] for x in a}.get(str(sd.CAM_A1)) in ("standby", "disconnected", "recording")
        b = c.get(f"/api/v1/tenants/{sd.TENANT_B}/cameras/{sd.CAM_B1}", headers=world.auth(sd.USER_B_VIEWER)).json()
        assert one.calls == 1 and two.calls == 1 and b["id"] == str(sd.CAM_B1)
    # a stamp that is down answers nothing, and the tenant of the other stamp is not told so
    down = PathsOf(fail=True)

    async def factory2():
        d = world.deps()
        d.stamps, d.mediamtx = reg, FakePool({1: one, 2: down})
        return d

    with TestClient(create_app(world.settings, factory2)) as c:
        assert c.get(f"/api/v1/tenants/{sd.TENANT_A}/cameras", headers=world.auth(sd.USER_A_VIEWER)).status_code == 200
        assert down.calls == 0


# ---------------------------------------------------------------- the issuer, and the token used up before anything else

def test_a_token_of_another_issuer_is_refused(world, enroll_client):
    t = device_token(world, sd.USER_A_ADMIN, extra={"iss": "http://localhost:8080/realms/other"})
    assert enroll(enroll_client, t).status_code == 401
    assert world.dynsec.added == []


class CallOrder(MemoryStore):
    """The in-memory store, writing down the order of the calls the registration makes."""

    def __init__(self, inner: MemoryStore):
        self.__dict__.update(inner.__dict__)
        self.calls: list[str] = []

    async def use_enroll_token(self, jti, expires_at):
        self.calls.append("use token")
        return await super().use_enroll_token(jti, expires_at)

    async def memberships(self, user_id):
        self.calls.append("memberships")
        return await super().memberships(user_id)

    async def create_camera(self, *a, **kw):
        self.calls.append("create camera")
        return await super().create_camera(*a, **kw)


@pytest.mark.parametrize("user,code", [(sd.USER_A_ADMIN, 201), (sd.USER_A_VIEWER, 403)])
def test_the_token_is_used_up_before_the_caller_is_looked_at(world, user, code):
    """Two calls at the same moment are told apart by the one insert of the token id; that must come before anything
    that depends on who the caller is, and a refused call uses the token up as well."""
    store = CallOrder(world.store)

    async def factory():
        d = world.deps()
        d.store = store
        d.device_verifier = DeviceTokenVerifier(world.verifier, DEVICE, ENROLL)
        return d

    with TestClient(create_app(world.settings, factory)) as c:
        t = device_token(world, user)
        assert enroll(c, t).status_code == code
        assert store.calls[0] == "use token" and "memberships" in store.calls
        assert enroll(c, t).status_code == 401           # used up, whatever the first answer was
