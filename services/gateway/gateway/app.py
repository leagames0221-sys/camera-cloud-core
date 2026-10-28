"""The gateway: the only door from outside. It passes /api/ on to an API and serves the static viewing page. Nothing
else is forwarded: /internal/ and /jwks stay unreachable from outside.

Which API answers is decided per kind of call by the table ROUTES below (the Strangler Fig pattern: the new API takes
over one kind of call at a time, and the old one is removed when it has none left). The table is code, so every
change of it is a commit. A call that matches no kind goes to DEFAULT_BACKEND.
"""
from __future__ import annotations

import json
import os
import pathlib
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import re

# The kinds of call: (name, method or None for any, the path after /api/ as a pattern; a name in {} is one path segment).
SHAPES = [
    ("me", "GET", "v1/me"),
    ("tenants", None, "v1/tenants"),
    ("members-add", "POST", "v1/tenants/{t}/members"),
    ("cameras-list", "GET", "v1/tenants/{t}/cameras"),
    ("camera-register", "POST", "v1/tenants/{t}/cameras"),
    ("camera-get", "GET", "v1/tenants/{t}/cameras/{c}"),
    ("camera-delete", "DELETE", "v1/tenants/{t}/cameras/{c}"),
    ("segments-list", "GET", "v1/tenants/{t}/cameras/{c}/segments"),
    ("live", "POST", "v1/tenants/{t}/cameras/{c}/live"),
    ("playback", "POST", "v1/tenants/{t}/cameras/{c}/playback"),
    ("events-list", "GET", "v1/tenants/{t}/cameras/{c}/events"),
    ("event-playback", "POST", "v1/tenants/{t}/cameras/{c}/events/{e}/playback"),
    ("camera-token", "POST", "v1/camera/token"),
    ("openapi", "GET", "v1/openapi.json"),
]
# Which API answers each kind of call. The first version ("api-v1") has been taken out; all kinds are answered by "api-v2".
ROUTES = {
    "me": "api-v2",
    "tenants": "api-v2",
    "members-add": "api-v2",
    "cameras-list": "api-v2",
    "camera-register": "api-v2",
    "camera-get": "api-v2",
    "camera-delete": "api-v2",
    "segments-list": "api-v2",
    "live": "api-v2",
    "playback": "api-v2",
    "events-list": "api-v2",
    "event-playback": "api-v2",
    "camera-token": "api-v2",
    "openapi": "api-v2",
}
DEFAULT_BACKEND = "api-v2"

_COMPILED = [(name, method, re.compile("^" + re.sub(r"\\{\w+\\}", "[^/]+", re.escape(pattern)) + "$"))
             for name, method, pattern in SHAPES]


def shape_of(method: str, rest: str) -> str | None:
    for name, m, rx in _COMPILED:
        if (m is None or m == method.upper()) and rx.match(rest):
            return name
    return None


def backend_for(method: str, rest: str, routes: dict[str, str] | None = None, default: str | None = None) -> str:
    table = ROUTES if routes is None else routes
    name = shape_of(method, rest)
    return table.get(name, default or DEFAULT_BACKEND) if name else (default or DEFAULT_BACKEND)


HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length", "content-encoding"}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def safe_rest(rest: str) -> bool:
    """False for anything that could climb out of /api/ once normalised."""
    if not rest or "%" in rest or "\\" in rest or "\x00" in rest:
        return False
    return all(seg not in ("..", ".") for seg in rest.split("/"))


def build_config_js(cfg: dict) -> str:
    return "window.CCC_CONFIG = " + json.dumps(cfg, ensure_ascii=False) + ";\n"


def create_app(api_url: str, web_dir: pathlib.Path, vendor_dir: pathlib.Path, public_config: dict,
               transport: httpx.AsyncBaseTransport | None = None, backends: dict[str, str] | None = None,
               routes: dict[str, str] | None = None, default_backend: str | None = None) -> FastAPI:
    """`backends` maps a name ("api-v2") to the address of that API; without it, every call goes to `api_url`."""
    urls = backends or {DEFAULT_BACKEND: api_url}
    clients = {name: httpx.AsyncClient(base_url=url, timeout=30.0, transport=transport) for name, url in urls.items()}
    fallback = default_backend or (DEFAULT_BACKEND if DEFAULT_BACKEND in clients else next(iter(clients)))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for c in clients.values():
            await c.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/config.js")
    async def config_js():
        return Response(build_config_js(public_config), media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})

    @app.api_route("/api/{rest:path}", methods=METHODS)
    async def proxy(rest: str, request: Request):
        if not safe_rest(rest):
            return JSONResponse({"detail": "not_found"}, status_code=404)
        target = f"/api/{rest}"
        if request.url.query:
            target += "?" + request.url.query
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        if request.client:
            headers["x-forwarded-for"] = request.client.host
        name = backend_for(request.method, rest, routes if routes is not None else (None if backends else {}), fallback)
        try:
            r = await clients[name].request(request.method, target, headers=headers, content=await request.body())
        except httpx.HTTPError:
            return JSONResponse({"detail": "bad_gateway"}, status_code=502)
        out = {k: v for k, v in r.headers.items() if k.lower() not in HOP_BY_HOP}
        out["Cache-Control"] = "no-store"
        return Response(r.content, status_code=r.status_code, headers=out)

    @app.api_route("/internal/{rest:path}", methods=METHODS)
    async def no_internal(rest: str):
        return PlainTextResponse("not found", status_code=404)

    @app.get("/jwks")
    async def no_jwks():
        return PlainTextResponse("not found", status_code=404)

    if vendor_dir.is_dir():
        app.mount("/vendor", StaticFiles(directory=str(vendor_dir)), name="vendor")
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


def build_app() -> FastAPI:
    e = os.environ
    kc = e.get("KC_PUBLIC_URL", "http://localhost:8080")
    cfg = {
        "keycloakUrl": kc,
        "realm": e.get("KC_REALM", "ccc"),
        "clientId": e.get("OIDC_CLIENT_ID", "ccc-web"),
        "apiBase": "/api/v1",
    }
    backends = {name: url for name, url in (("api-v2", e.get("API_LATER_URL")),) if url}
    return create_app(e.get("API_URL", "http://api:8000"), pathlib.Path(e.get("WEB_DIR", "/srv/web")),
                      pathlib.Path(e.get("VENDOR_DIR", "/srv/vendor")), cfg, backends=backends or None)
