"""Keycloak: the real login flow, fixed user ids, PKCE required, no other way in, no identity providers."""
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import oidc_flow
from conftest import BASE, ENV, KC, jwt_payload
from ids import USER_SUB

pytestmark = pytest.mark.integration

REDIRECT = oidc_flow.REDIRECT


@pytest.mark.parametrize("user", sorted(USER_SUB))
def test_login_with_pkce_gives_a_token_with_the_fixed_subject(user):
    tok = oidc_flow.login(user, ENV["DEMO_USER_PASSWORD"], kc=KC)
    claims = jwt_payload(tok["access_token"])
    assert claims["sub"] == USER_SUB[user]            # fixed in the realm file, so the API's rows can name it
    assert claims["iss"] == "http://localhost:8080/realms/ccc"
    assert claims["azp"] == "ccc-web"


def test_the_api_takes_the_access_token_but_not_the_id_token():
    # The two tokens differ in typ (and in aud, which the API does not check); the page also puts the ID token into the logout URL.
    tok = oidc_flow.login("a-viewer", ENV["DEMO_USER_PASSWORD"], kc=KC)
    assert jwt_payload(tok["access_token"])["typ"] == "Bearer" and jwt_payload(tok["id_token"])["typ"] == "ID"
    me = f"{BASE}/api/v1/me"
    assert httpx.get(me, headers={"Authorization": f"Bearer {tok['access_token']}"}).status_code == 200
    assert httpx.get(me, headers={"Authorization": f"Bearer {tok['id_token']}"}).status_code == 401


def test_wrong_password_does_not_log_in():
    with pytest.raises(AssertionError):
        oidc_flow.login("a-viewer", "definitely-wrong", kc=KC)


def test_authorisation_without_pkce_is_refused():
    r = oidc_flow.authorization_url_status({"client_id": "ccc-web", "redirect_uri": REDIRECT, "response_type": "code",
                                            "scope": "openid", "state": "s"}, kc=KC)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q.get("error") == ["invalid_request"]


def test_plain_pkce_method_is_refused():
    r = oidc_flow.authorization_url_status({"client_id": "ccc-web", "redirect_uri": REDIRECT, "response_type": "code",
                                            "scope": "openid", "state": "s", "code_challenge": "x" * 43,
                                            "code_challenge_method": "plain"}, kc=KC)
    assert "error" in r.headers.get("location", "") or r.status_code == 400


def test_implicit_flow_is_refused():
    r = oidc_flow.authorization_url_status({"client_id": "ccc-web", "redirect_uri": REDIRECT, "response_type": "token",
                                            "scope": "openid", "state": "s", "code_challenge": "x" * 43,
                                            "code_challenge_method": "S256"}, kc=KC)
    assert "unauthorized_client" in r.headers.get("location", "")


def test_direct_access_grant_is_closed():
    r = httpx.post(f"{KC}/realms/ccc/protocol/openid-connect/token",
                   data={"grant_type": "password", "client_id": "ccc-web", "username": "a-viewer",
                         "password": ENV["DEMO_USER_PASSWORD"]})
    assert r.status_code == 400 and r.json()["error"] == "unauthorized_client"


@pytest.mark.parametrize("redirect", ["http://localhost:8000/other", "http://localhost:8000", "http://evil.example/",
                                      "http://localhost:8000/*"])
def test_only_the_exact_redirect_uri_is_accepted(redirect):
    r = oidc_flow.authorization_url_status({"client_id": "ccc-web", "redirect_uri": redirect, "response_type": "code",
                                            "scope": "openid", "code_challenge": "x" * 43,
                                            "code_challenge_method": "S256"}, kc=KC)
    assert r.status_code == 400


def test_no_realm_has_an_identity_provider():
    admin = httpx.post(f"{KC}/realms/master/protocol/openid-connect/token",
                       data={"grant_type": "password", "client_id": "admin-cli", "username": ENV["KC_ADMIN_USER"],
                             "password": ENV["KC_ADMIN_PASSWORD"]}).json()["access_token"]
    h = {"Authorization": f"Bearer {admin}"}
    realms = [r["realm"] for r in httpx.get(f"{KC}/admin/realms", headers=h).json()]
    assert "ccc" in realms and "master" in realms
    for realm in realms:
        r = httpx.get(f"{KC}/admin/realms/{realm}/identity-provider/instances", headers=h)
        assert r.status_code == 200 and r.json() == [], realm


def test_impersonation_feature_is_off():
    admin = httpx.post(f"{KC}/realms/master/protocol/openid-connect/token",
                       data={"grant_type": "password", "client_id": "admin-cli", "username": ENV["KC_ADMIN_USER"],
                             "password": ENV["KC_ADMIN_PASSWORD"]}).json()["access_token"]
    r = httpx.post(f"{KC}/admin/realms/ccc/users/{USER_SUB['a-viewer']}/impersonation",
                   headers={"Authorization": f"Bearer {admin}"})
    assert r.status_code == 501
