"""Which stamp a new tenant is put on."""
import pytest

from conftest import psql

pytestmark = pytest.mark.integration


def stamp_of(name):
    rows = psql("postgres", f"SELECT stamp_id FROM tenants WHERE name = '{name}'")
    return int(rows[0][0])


def counts():
    return {int(a): int(b) for a, b in psql("postgres", "SELECT stamp_id, count(*) FROM tenants GROUP BY stamp_id")}


def test_a_new_tenant_goes_to_the_stamp_with_fewer_tenants_unless_one_is_named(http, tokens):
    h = tokens.headers("partner-admin")
    names = ["stamp rule test 1", "stamp rule test 2", "stamp rule test 3"]
    try:
        before = counts()
        fewer = 2 if before.get(2, 0) < before.get(1, 0) else 1      # ties go to the lower number
        r = http.post("/api/v1/tenants", json={"name": names[0]}, headers=h)
        assert r.status_code == 201, r.text
        assert stamp_of(names[0]) == fewer
        # naming a stamp overrides, whichever has fewer
        other = 1 if fewer == 2 else 2
        r = http.post("/api/v1/tenants", json={"name": names[1], "stamp_id": other}, headers=h)
        assert r.status_code == 201 and stamp_of(names[1]) == other
        # a stamp that does not exist is refused
        assert http.post("/api/v1/tenants", json={"name": names[2], "stamp_id": 3}, headers=h).status_code == 422
        assert psql("postgres", f"SELECT count(*) FROM tenants WHERE name = '{names[2]}'")[0][0] == "0"
        # a tenant administrator cannot create tenants, with or without a stamp
        assert http.post("/api/v1/tenants", json={"name": "x", "stamp_id": 1},
                         headers=tokens.headers("c-admin")).status_code == 403
    finally:
        psql("postgres", "DELETE FROM memberships WHERE tenant_id IN (SELECT id FROM tenants WHERE name LIKE 'stamp rule test%'); "
                         "DELETE FROM tenants WHERE name LIKE 'stamp rule test%'")
