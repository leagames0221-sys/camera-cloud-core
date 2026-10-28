"""The limit of 100 calls per second for each tenant, counted after the caller's membership and role are checked."""
import time

import pytest

from conftest import TENANT_A, TENANT_C

pytestmark = pytest.mark.integration


def burst(http, headers, url, n, method="get", **kw):
    """n calls one after the other; returns (status codes, seconds it took)."""
    t0 = time.monotonic()
    codes = [getattr(http, method)(url, headers=headers, **kw).status_code for _ in range(n)]
    return codes, time.monotonic() - t0


def settle():
    time.sleep(1.3)         # the window is one second: let the earlier calls leave it


def test_the_101st_call_within_a_second_gets_429_with_retry_after(http, tokens):
    h = tokens.headers("c-viewer")
    url = f"/api/v1/tenants/{TENANT_C}/cameras"
    for attempt in range(4):
        settle()
        codes, took = burst(http, h, url, 105)
        if took < 0.9:
            break
    else:
        pytest.fail("the 105 calls never fitted into one second on this machine")
    assert codes[:100] == [200] * 100 and set(codes[100:]) == {429}, codes
    settle()
    r = http.get(url, headers=h)
    assert r.status_code == 200          # the window has moved on
    codes, _ = burst(http, h, url, 101)
    r = http.get(url, headers=h)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json() == {"detail": "too_many_requests"}


def test_calls_of_someone_who_is_not_a_member_do_not_use_up_the_tenants_calls(http, tokens):
    mine, theirs = tokens.headers("c-viewer"), tokens.headers("b-viewer")
    url = f"/api/v1/tenants/{TENANT_C}/cameras"
    settle()
    codes, took = burst(http, theirs, url, 200)           # B's viewer knows C's tenant id and asks 200 times
    assert set(codes) == {404}
    codes, took = burst(http, mine, url, 100)             # right after, C's own viewer still has all 100
    assert took < 1.0 and codes == [200] * 100, codes


def test_refused_calls_of_a_member_do_not_use_them_up_either(http, tokens):
    h = tokens.headers("c-viewer")
    settle()
    codes, _ = burst(http, h, f"/api/v1/tenants/{TENANT_C}/cameras", 150, method="post", json={"name": "x"})
    assert set(codes) == {403}                            # a viewer may not register a camera
    codes, took = burst(http, h, f"/api/v1/tenants/{TENANT_C}/cameras", 100)
    assert took < 1.0 and codes == [200] * 100, codes


def test_one_tenant_running_out_does_not_slow_another(http, tokens):
    c, a = tokens.headers("c-viewer"), tokens.headers("a-viewer")
    settle()
    burst(http, c, f"/api/v1/tenants/{TENANT_C}/cameras", 150)
    codes, _ = burst(http, a, f"/api/v1/tenants/{TENANT_A}/cameras", 100)
    assert codes == [200] * 100
