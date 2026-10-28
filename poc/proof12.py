"""Proofs 1 and 2 at the HTTP level (MediaMTX with authMethod jwt).

Run after poc/run_stack.sh and poc/start_pub.sh. Prints one JSON document; nothing is asserted
beyond what is printed, so the numbers are read from the output, not from this script.
"""
import json
import pathlib
import sys
import urllib.parse

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import make_keys as m  # noqa: E402

KEY = m.load()
T = "11111111-1111-4111-8111-111111111111"
C = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
P = f"{T}/{C}"
HLS = "http://127.0.0.1:18888"
PB = "http://127.0.0.1:19996"
API = "http://127.0.0.1:19997"
ORIGIN = "http://localhost:8080"
OUT = {}

KEEP = ("content-type", "location", "access-control-allow-origin", "access-control-allow-headers",
        "access-control-allow-methods", "access-control-allow-credentials", "access-control-max-age",
        "www-authenticate", "set-cookie", "vary", "content-length", "cache-control")


def summ(r, body=False):
    d = {"status": r.status_code, "headers": {k: v for k, v in r.headers.items() if k.lower() in KEEP}}
    if body:
        d["body_head"] = r.text[:600]
    return d


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


read_tok = m.token(KEY, "read", P, ttl=300)
cli = httpx.Client(follow_redirects=False, timeout=15)

# ---- Proof 1: HLS ----
p1 = {}
r = cli.get(f"{HLS}/{P}/index.m3u8", headers=bearer(read_tok) | {"Origin": ORIGIN})
p1["1_multivariant_first_request_with_bearer"] = summ(r)
loc = r.headers.get("location", "")
p1["1_location"] = loc
# follow the 302 with and without the Bearer header
r2 = cli.get(urllib.parse.urljoin(HLS + "/", loc), headers=bearer(read_tok) | {"Origin": ORIGIN})
p1["2_after_302_with_bearer"] = summ(r2, body=True)
variant = None
for line in r2.text.splitlines():
    if line and not line.startswith("#"):
        variant = line
        break
p1["2_variant_uri"] = variant
r3 = cli.get(f"{HLS}/{P}/index.m3u8?cookieCheck=1")
p1["3_after_302_without_bearer_new_session"] = summ(r3)
if variant:
    vurl = f"{HLS}/{P}/{variant}"
    rv = cli.get(vurl, headers=bearer(read_tok) | {"Origin": ORIGIN})
    p1["4_variant_playlist_with_bearer"] = summ(rv, body=True)
    rv2 = cli.get(vurl, headers={"Origin": ORIGIN})
    p1["5_variant_playlist_without_bearer"] = summ(rv2)
    # find an init segment / media segment uri in the variant playlist
    uris = []
    for line in rv.text.splitlines():
        if 'URI="' in line:
            uris.append(line.split('URI="')[1].split('"')[0])
        elif line and not line.startswith("#"):
            uris.append(line)
    p1["6_variant_uris_head"] = uris[:4]
    for i, u in enumerate(uris[:2]):
        ru = cli.get(f"{HLS}/{P}/{u}", headers={"Origin": ORIGIN})
        p1[f"7_segment_{i}_without_bearer"] = summ(ru)
        ru = cli.get(f"{HLS}/{P}/{u}", headers=bearer(read_tok) | {"Origin": ORIGIN})
        p1[f"8_segment_{i}_with_bearer"] = summ(ru)
# preflights
for origin in (ORIGIN, "http://evil.example"):
    pr = cli.request("OPTIONS", f"{HLS}/{P}/index.m3u8", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    p1[f"9_preflight_hls_origin_{origin}"] = summ(pr)
# negative cases on the first request
for name, tok in {
    "no_token": None,
    "expired": m.token(KEY, "read", P, ttl=-30),
    "wrong_action_publish": m.token(KEY, "publish", P),
    "other_path": m.token(KEY, "read", f"{T}/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    "wrong_aud": m.token(KEY, "read", P, aud="other"),
    "wrong_iss": m.token(KEY, "read", P, iss="http://other"),
    "no_exp": m.token(KEY, "read", P, exp=False),
}.items():
    h = bearer(tok) if tok else {}
    # the 302 (cookieCheck) is returned before auth, so ask with cookieCheck=1 directly
    rr = cli.get(f"{HLS}/{P}/index.m3u8?cookieCheck=1", headers=h | {"Origin": ORIGIN})
    p1[f"10_neg_{name}"] = summ(rr)
OUT["proof1_hls"] = p1

# ---- Proof 2: playback ----
p2 = {}
play_tok = m.token(KEY, "playback", P, ttl=300)
rl = cli.get(f"{PB}/list", params={"path": P}, headers=bearer(play_tok) | {"Origin": ORIGIN})
p2["1_list"] = summ(rl, body=True)
spans = rl.json() if rl.status_code == 200 else []
if spans:
    s = spans[0]
    p2["2_first_span"] = s
    for fmt in (None, "mp4"):
        params = {"path": P, "start": s["start"], "duration": min(float(s["duration"]), 8.0)}
        if fmt:
            params["format"] = fmt
        rg = cli.get(f"{PB}/get", params=params, headers=bearer(play_tok) | {"Origin": ORIGIN})
        d = summ(rg)
        d["bytes"] = len(rg.content)
        d["first_16_bytes_hex"] = rg.content[:16].hex()
        d["ftyp_at_4"] = rg.content[4:8].decode("latin1")
        p2[f"3_get_format_{fmt or 'default'}"] = d
    rg = cli.get(f"{PB}/get", params={"path": P, "start": s["start"], "duration": 5})
    p2["4_get_without_bearer"] = summ(rg)
    rg = cli.get(f"{PB}/get", params={"path": P, "start": s["start"], "duration": 5},
                 headers=bearer(m.token(KEY, "read", P)))
    p2["5_get_with_read_token"] = summ(rg)
    rg = cli.get(f"{PB}/get", params={"path": P, "start": s["start"], "duration": 5}, headers=bearer(play_tok))
    p2["6_get_no_origin_header"] = summ(rg)
for origin in (ORIGIN, "http://evil.example"):
    pr = cli.request("OPTIONS", f"{PB}/get", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    p2[f"7_preflight_playback_origin_{origin}"] = summ(pr)
OUT["proof2_playback"] = p2

# ---- Control API shapes (fixtures) ----
fx = pathlib.Path(__file__).parent.parent / "tests" / "fixtures"
fx.mkdir(parents=True, exist_ok=True)
api_tok = m.token(KEY, "api", None, ttl=60)
ra = cli.get(f"{API}/v3/paths/list", headers=bearer(api_tok))
OUT["control_paths_list"] = summ(ra, body=False)
if ra.status_code == 200:
    (fx / "mediamtx_paths_list.json").write_text(json.dumps(ra.json(), indent=2))
rr = cli.get(f"{API}/v3/recordings/get/{P}", headers=bearer(api_tok))
OUT["control_recordings_get"] = summ(rr, body=False)
if rr.status_code == 200:
    (fx / "mediamtx_recordings_get.json").write_text(json.dumps(rr.json(), indent=2))
ra2 = cli.get(f"{API}/v3/paths/list")
OUT["control_paths_list_no_token"] = summ(ra2)
ra3 = cli.get(f"{API}/v3/paths/list", headers=bearer(read_tok))
OUT["control_paths_list_read_token"] = summ(ra3)

print(json.dumps(OUT, indent=2, ensure_ascii=False))
(pathlib.Path(__file__).parent / "out" / "proof12.json").write_text(json.dumps(OUT, indent=2, ensure_ascii=False))
