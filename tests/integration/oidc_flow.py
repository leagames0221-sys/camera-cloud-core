"""Authorization code flow with PKCE, walked the way a browser does it (no Direct Access Grants):
open the authorisation URL, post the login form, follow the redirect, exchange the code with the verifier."""
from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

KC = "http://localhost:8080"
REALM = "ccc"
CLIENT_ID = "ccc-web"
REDIRECT = "http://localhost:8000/"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class Jar:
    """Keeps cookies by name and sends them back whatever their flags say. A browser accepts Keycloak's Secure cookies
    on http://localhost (it is treated as a secure context); Python's own cookie jar would not send them."""

    def __init__(self) -> None:
        self.c: dict[str, str] = {}

    def take(self, r: httpx.Response) -> None:
        for line in r.headers.get_list("set-cookie"):
            name, _, rest = line.partition("=")
            value = rest.split(";", 1)[0]
            if "max-age=0" in line.lower() or value == "":
                self.c.pop(name.strip(), None)
            else:
                self.c[name.strip()] = value

    def header(self) -> dict:
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.c.items())} if self.c else {}


def login(username: str, password: str, *, kc: str = KC, realm: str = REALM, client_id: str = CLIENT_ID,
          redirect: str = REDIRECT, with_pkce: bool = True) -> dict:
    """Return the token response (access_token, id_token, ...). Raises AssertionError if a step does not go as expected."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": "openid",
              "state": state, "response_mode": "query"}
    if with_pkce:
        params |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    jar = Jar()
    with httpx.Client(follow_redirects=False, timeout=20, cookies=None) as c:
        r = c.get(f"{kc}/realms/{realm}/protocol/openid-connect/auth", params=params)
        jar.take(r)
        assert r.status_code == 200, f"authorisation page returned {r.status_code}: {r.text[:200]}"
        m = re.search(r'<form[^>]+id="kc-form-login"[^>]+action="([^"]+)"', r.text) or \
            re.search(r'<form[^>]+action="([^"]+)"[^>]+id="kc-form-login"', r.text)
        assert m, "login form not found"
        action = html.unescape(m.group(1))
        r = c.post(action, data={"username": username, "password": password, "credentialId": ""},
                   headers=jar.header())
        jar.take(r)
        assert r.status_code == 302, f"login returned {r.status_code}"
        loc = r.headers["location"]
        assert loc.startswith(redirect), f"redirected to {loc}"
        q = parse_qs(urlparse(loc).query)
        assert q.get("state") == [state], "state mismatch"
        assert "code" in q, f"no code in {loc}"
        form = {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": redirect,
                "client_id": client_id}
        if with_pkce:
            form["code_verifier"] = verifier
        t = c.post(f"{kc}/realms/{realm}/protocol/openid-connect/token", data=form)
        assert t.status_code == 200, f"token endpoint returned {t.status_code}: {t.text[:200]}"
        return t.json()


def authorization_url_status(params: dict, *, kc: str = KC, realm: str = REALM) -> httpx.Response:
    with httpx.Client(follow_redirects=False, timeout=20) as c:
        return c.get(f"{kc}/realms/{realm}/protocol/openid-connect/auth?{urlencode(params)}")
