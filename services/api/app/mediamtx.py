"""Calls to MediaMTX's Control API, authorised with a five-minute `api` token that this service signs itself."""
from __future__ import annotations

import time
from datetime import datetime

import httpx

from .config import Settings
from .video_jwt import KeyStore


def parse_rfc3339(s: str) -> datetime:
    # The start of a segment is a Go `time.Time` with json tag `start` (MediaMTX v1.21.2, internal/defs/api_recording.go).
    # Go writes it as RFC 3339 "with sub-second precision" (time.Time.MarshalJSON) and, with the format `.999999999`, drops
    # trailing zeros (format_rfc3339.go: stdFracSecond9), so the fraction has 0 to 9 digits and the offset can be Z or
    # +hh:mm. Example from a real run: 2026-10-24T08:07:45.067638Z (tests/fixtures/mediamtx_recordings_get.json).
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    head, _, rest = s.partition(".")
    if rest:
        frac, sign, tz = rest, "", ""
        for i, ch in enumerate(rest):
            if ch in "+-":
                frac, sign, tz = rest[:i], ch, rest[i + 1:]
                break
        frac = (frac + "000000")[:6]
        s = f"{head}.{frac}{sign}{tz}"
    return datetime.fromisoformat(s)


def ready_paths(paths_list: dict) -> set[str]:
    """Names of paths that have a ready stream, from the body of GET /v3/paths/list."""
    return {item["name"] for item in paths_list.get("items", []) if item.get("ready")}


def recording_starts(recordings_get: dict) -> list[datetime]:
    """Start times from the body of GET /v3/recordings/get/<path>."""
    return sorted(parse_rfc3339(s["start"]) for s in recordings_get.get("segments", []))


class MediaMtxClient:
    def __init__(self, settings: Settings, keys: KeyStore, http: httpx.AsyncClient) -> None:
        self._s = settings
        self._keys = keys
        self._http = http
        self._cache: tuple[float, set[str]] | None = None

    def _headers(self) -> dict:
        token, _ = self._keys.issue(action="api", path=None, subject="ccc-api", issuer=self._s.video_issuer,
                                    audience=self._s.video_audience)
        return {"Authorization": f"Bearer {token}"}

    async def ready_paths(self, max_age: float = 2.0) -> set[str]:
        """Ready paths, cached for a couple of seconds so a page of cameras costs one call. Empty set on error."""
        now = time.monotonic()
        if self._cache is not None and now - self._cache[0] < max_age:
            return self._cache[1]
        try:
            r = await self._http.get(f"{self._s.mediamtx_control_url}/v3/paths/list", headers=self._headers(), timeout=3.0)
            r.raise_for_status()
            paths = ready_paths(r.json())
        except (httpx.HTTPError, ValueError):
            paths = set()
        self._cache = (now, paths)
        return paths

    async def recording_starts(self, path: str) -> list[datetime]:
        r = await self._http.get(f"{self._s.mediamtx_control_url}/v3/recordings/get/{path}",
                                 headers=self._headers(), timeout=10.0)
        r.raise_for_status()
        return recording_starts(r.json())


class MediaMtxPool:
    """One client for each stamp's MediaMTX. A tenant's streaming paths and a camera's recordings are asked of the tenant's own
    stamp only: a stamp that is switched off or slow must not slow the tenants of the others."""

    def __init__(self, clients: dict[int, MediaMtxClient]) -> None:
        self._clients = clients

    def for_stamp(self, stamp_id: int) -> MediaMtxClient:
        return self._clients[stamp_id]
