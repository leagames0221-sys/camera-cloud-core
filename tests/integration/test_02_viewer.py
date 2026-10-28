"""What an A company viewer can do: the camera list with states, live HLS with a Bearer token, and the last 5 minutes."""
from urllib.parse import urljoin

import httpx
import pytest

from conftest import HLS, PAGE_ORIGIN, PLAYBACK, camera_state, wait_for
from ids import CAM_A1, CAM_A2, TENANT_A

pytestmark = pytest.mark.integration
USER = "a-viewer"


def test_me_lists_only_company_a(http, tokens):
    me = http.get("/api/v1/me", headers=tokens.headers(USER)).json()
    assert [e["tenant_id"] for e in me["entries"]] == [TENANT_A]
    assert me["entries"][0]["role"] == "viewer"


def test_camera_list_shows_the_two_cameras_of_company_a_and_reaches_recording(http, tokens):
    def both_recording():
        cams = http.get(f"/api/v1/tenants/{TENANT_A}/cameras", headers=tokens.headers(USER)).json()
        assert {c["id"] for c in cams} == {CAM_A1, CAM_A2}
        return {c["id"]: c["state"] for c in cams} == {CAM_A1: "recording", CAM_A2: "recording"}

    wait_for(both_recording, timeout=180, what="both A cameras to be recording")
    one = camera_state(http, tokens, USER, TENANT_A, CAM_A1)
    assert one["fps"] is not None or one["state"] == "recording"


def live(http, tokens, camera=CAM_A1):
    r = http.post(f"/api/v1/tenants/{TENANT_A}/cameras/{camera}/live", headers=tokens.headers(USER))
    assert r.status_code == 200, r.text
    return r.json()


def test_live_playlist_with_bearer_follows_the_302(http, tokens):
    lv = live(http, tokens)
    h = {"Authorization": f"Bearer {lv['token']}", "Origin": PAGE_ORIGIN}
    with httpx.Client(follow_redirects=False, timeout=15) as c:
        first = c.get(lv["hls_url"], headers=h)
        assert first.status_code == 302 and "cookieCheck=1" in first.headers["location"]
        assert first.headers["access-control-allow-origin"] == PAGE_ORIGIN
        second = c.get(urljoin(lv["hls_url"], first.headers["location"]), headers=h)
        assert second.status_code == 200
        assert second.text.startswith("#EXTM3U") and "EXT-X-STREAM-INF" in second.text
        # without the header the second request is refused: the token has to follow the redirect
        bare = c.get(urljoin(lv["hls_url"], first.headers["location"]))
        assert bare.status_code == 401


def test_hls_preflight_allows_only_the_page_origin():
    url = f"{HLS}/{TENANT_A}/{CAM_A1}/index.m3u8"
    ok = httpx.options(url, headers={"Origin": PAGE_ORIGIN, "Access-Control-Request-Method": "GET",
                                      "Access-Control-Request-Headers": "authorization"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == PAGE_ORIGIN
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()
    evil = httpx.options(url, headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET",
                                       "Access-Control-Request-Headers": "authorization"})
    assert "access-control-allow-origin" not in evil.headers


def test_past_five_minutes_play_back_as_mp4(http, tokens):
    def playback_ready():
        r = http.post(f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_A1}/playback", headers=tokens.headers(USER))
        return r.json() if r.status_code == 200 else None

    # the first 60-second segment is closed about a minute after the camera started
    p = wait_for(playback_ready, timeout=420, interval=10, what="a recorded segment for playback")
    assert p["duration_seconds"] > 0
    r = httpx.get(p["url"], headers={"Authorization": f"Bearer {p['token']}", "Origin": PAGE_ORIGIN}, timeout=60)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.headers["access-control-allow-origin"] == PAGE_ORIGIN
    assert r.content[4:8] == b"ftyp" and len(r.content) > 10_000


def test_playback_cors_preflight_allows_only_the_page_origin():
    ok = httpx.options(f"{PLAYBACK}/get", headers={"Origin": PAGE_ORIGIN, "Access-Control-Request-Method": "GET",
                                                    "Access-Control-Request-Headers": "authorization"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == PAGE_ORIGIN
    evil = httpx.options(f"{PLAYBACK}/get", headers={"Origin": "http://evil.example",
                                                     "Access-Control-Request-Method": "GET",
                                                     "Access-Control-Request-Headers": "authorization"})
    assert "access-control-allow-origin" not in evil.headers


def test_recording_index_fills_from_the_hook_and_covers_the_gap_free_minutes(http, tokens):
    def two_segments():
        r = http.get(f"/api/v1/tenants/{TENANT_A}/cameras/{CAM_A1}/segments?minutes=30", headers=tokens.headers(USER))
        return r.json() if len(r.json()) >= 2 else None

    segs = wait_for(two_segments, timeout=420, interval=10, what="two indexed segments")
    starts = [s["started_at"] for s in segs]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)    # no duplicates
    assert any(s["source"] == "hook" for s in segs)
