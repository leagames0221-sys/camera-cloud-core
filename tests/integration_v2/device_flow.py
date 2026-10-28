"""The parts of the device authorization grant that a browser and a camera do, for the tests: start it (the camera's
request), approve or refuse it as a user (the pages Keycloak shows, walked the way a browser does), poll for the token."""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin

import httpx

from oidc_flow import Jar

GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def start(kc: str, client_id: str = "ccc-device", realm: str = "ccc") -> httpx.Response:
    return httpx.post(f"{kc}/realms/{realm}/protocol/openid-connect/auth/device", data={"client_id": client_id}, timeout=20)


def poll(kc: str, device_code: str, client_id: str = "ccc-device", realm: str = "ccc") -> httpx.Response:
    return httpx.post(f"{kc}/realms/{realm}/protocol/openid-connect/token",
                      data={"grant_type": GRANT, "device_code": device_code, "client_id": client_id}, timeout=20)


def _forms(text: str) -> list[tuple[str, dict, list[tuple[str, str]]]]:
    """(action, hidden/other input values, submit buttons (name, value)) of every form on a page."""
    out = []
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        a = re.search(r'action="([^"]*)"', attrs)
        fields, buttons = {}, []
        for i in re.finditer(r"<input\b([^>]*)>", body, re.I):
            at = i.group(1)
            name = re.search(r'name="([^"]*)"', at)
            if not name:
                continue
            kind = (re.search(r'type="([^"]*)"', at) or [None, "text"])[1].lower()
            val = html.unescape((re.search(r'value="([^"]*)"', at) or [None, ""])[1])
            if kind in ("submit", "button"):
                buttons.append((name.group(1), val))
            elif kind not in ("checkbox", "radio") or "checked" in at:
                fields[name.group(1)] = val
        for b in re.finditer(r"<button\b([^>]*)>", body, re.I):
            at = b.group(1)
            name = re.search(r'name="([^"]*)"', at)
            if name:
                buttons.append((name.group(1), html.unescape((re.search(r'value="([^"]*)"', at) or [None, ""])[1])))
        out.append((html.unescape(a.group(1)) if a else "", fields, buttons))
    return out


def decide(kc: str, user_code: str, username: str, password: str, accept: bool = True) -> str:
    """Open Keycloak's page for the code, log in as the user, answer the consent page. Returns the text of the last page."""
    jar = Jar()
    method, url, data = "GET", f"{kc}/realms/ccc/device?user_code={user_code}", None
    with httpx.Client(follow_redirects=False, timeout=20) as c:
        for _ in range(15):
            r = c.request(method, url, data=data, headers=jar.header())
            jar.take(r)
            if r.status_code in (301, 302, 303):
                method, url, data = "GET", urljoin(str(r.url), r.headers["location"]), None
                continue
            forms = _forms(r.text)
            if 'id="kc-form-login"' in r.text:
                action, fields, _ = forms[0]
                method, url, data = "POST", urljoin(str(r.url), action), {**fields, "username": username,
                                                                           "password": password}
                continue
            consent = [f for f in forms if any(b[0] in ("accept", "cancel") for b in f[2])]
            if consent:
                action, fields, buttons = consent[0]
                name = "accept" if accept else "cancel"
                method, url, data = "POST", urljoin(str(r.url), action), {**fields, name: dict(buttons).get(name, "")}
                accept_done = True
                continue
            return r.text
    raise AssertionError("the device pages did not end after 15 steps")
