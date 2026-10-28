# Proofs before building (2026-10-24)

Three things had to work before the rest was written. They were checked with the pinned images (`docker run`, no pull, no build) and Python from `.venv`. Scripts and configs are in `poc/` (`run_stack.sh`, `start_pub.sh`, `proof12.py`, `run_mqtt.sh`, `proof3.py`); `make_keys.py` and `ctl.sh` are helpers, `extra.py` asks MediaMTX's Control API about paths and recordings after the publisher stopped, and `run_kc.sh` starts a throwaway Keycloak with the real realm file. Raw output of the HTTP proofs was written to `poc/out/proof12.json` (not committed; it holds a throwaway key).

**Not covered here:** nothing in this file ran in a real browser. Whether hls.js follows the 302 with its `Authorization` header kept, whether the browser accepts the CORS answers below, and whether `<video>` plays the fetched blob must be checked later in Chromium. What was checked is what MediaMTX answers to the exact HTTP requests a browser would send.

Setup: MediaMTX 1.21.2 (`-ffmpeg` image) with `authMethod: jwt`, `authJWTJWKS` pointing at a static JWKS file served by a second container, `authJWTIssuer`, `authJWTAudience: mediamtx`, `hlsAllowOrigins` and `playbackAllowOrigins` both set to the single origin `http://localhost:8080`, `record: yes`, `recordSegmentDuration: 10s` (the real config uses 60 s). A publisher container ran `ffmpeg -re -stream_loop -1 -c copy` of a 2-second-keyframe H.264 file into RTSP over TCP, with `?token=<publish JWT>`. Tokens are ES256, `kid`, `iss`, `aud`, `iat`, `exp`, `mediamtx_permissions` with one action and one path.

## Proof 1: live HLS with a Bearer token — passed at the HTTP level

| Request | Result |
|---|---|
| `GET /<path>/index.m3u8` with `Authorization: Bearer <read JWT>` and `Origin: http://localhost:8080` | **302**, `Location: /<path>/index.m3u8?cookieCheck=1`, `Set-Cookie: cookieCheck=1; HttpOnly; Secure; SameSite=None; Partitioned` (ignored on plain HTTP), `Access-Control-Allow-Origin: http://localhost:8080`. The 302 is returned **before** any token check. |
| same Location, **with** Bearer | **200** `application/vnd.apple.mpegurl`, multivariant playlist whose variant line is `video1_stream.m3u8?session=<uuid>`. |
| same Location, **without** Bearer | **401**, `WWW-Authenticate: Basic realm="mediamtx"`. So the Bearer header must be kept on the request that follows the 302. |
| variant playlist `video1_stream.m3u8?session=...` with and without Bearer | **200** both: after the session exists, the `session` query is what authorises. |
| init segment (`..._video1_init.mp4?session=...`) with and without Bearer | **200** both. |
| `gap.mp4` entries at the start of a fresh stream's playlist (marked `#EXT-X-GAP`, no `session` query) | **401** with and without Bearer. Open for the browser check: hls.js is expected to skip `#EXT-X-GAP` entries; not confirmed here. |
| preflight `OPTIONS` with `Origin: http://localhost:8080`, `Access-Control-Request-Method: GET`, `Access-Control-Request-Headers: authorization` | **204**, `Access-Control-Allow-Origin: http://localhost:8080`, `Access-Control-Allow-Headers: Authorization, Range`, `Access-Control-Allow-Methods: OPTIONS, GET`. |
| same preflight with `Origin: http://evil.example` | **204** but **no** `Access-Control-Allow-Origin`, so a browser refuses it. |
| first request to the playlist, token variants (sent with `cookieCheck=1` to skip the 302) | no token 401 (+`WWW-Authenticate: Basic`); expired 401; action `publish` instead of `read` 401; another camera's path 401; wrong `aud` 401; wrong `iss` 401; **JWT without `exp`: 200** (matches the source: MediaMTX does not require `exp`, so the API must always set it). |

Reading: a Bearer header is needed on the first two playlist requests (the 302 and the request after it); later requests carry the `session` query. hls.js's `xhrSetup` adds the header to every request, which covers this. A request with no token at all gets a `WWW-Authenticate: Basic` header; the page never sends one without a token, but a browser prompt on a missing token is not ruled out.

## Proof 2: playback of past recording with a playback JWT — passed at the HTTP level

| Request | Result |
|---|---|
| `GET :9996/list?path=<path>` with Bearer (playback JWT) | **200**, one span `{"start": ..., "duration": 59.498186, "url": ...}`: consecutive segments are joined into one span. |
| `GET :9996/get?path=<path>&start=<RFC3339>&duration=8` with Bearer | **200**, `Content-Type: video/mp4`, 87,969 bytes, begins with `ftyp` (brand `mp42`). |
| same with `&format=mp4` | **200**, `video/mp4`, 86,317 bytes, begins with `ftyp` (brand `isom`). Both forms work; the page uses the default form only (it has no `format=mp4` fallback). |
| `/get` without Bearer | **401** (`WWW-Authenticate: Basic`). |
| `/get` with a **read** JWT | **401**. |
| preflight to `/get` (`Access-Control-Request-Headers: authorization`) from `http://localhost:8080` | **204**, `Access-Control-Allow-Origin: http://localhost:8080`, `Access-Control-Allow-Headers: Authorization`. From `http://evil.example`: no `Access-Control-Allow-Origin`. |

## Proof 3: Mosquitto dynamic security with `%c` — passed

Mosquitto 2.1.2 with `allow_anonymous false`, one listener, the plugin at `/usr/lib/mosquitto_dynamic_security.so`, config file created with `mosquitto_ctrl dynsec init`. Roles: `camera` (`publishClientSend` on `cameras/%c/state` only) and `ingest` (`subscribePattern` on `cameras/+/state` only). Clients: two cameras with username = client ID = UUID, one ingest client. MQTT 5.0 throughout; reason codes read with paho-mqtt 2.1 callbacks.

| Check | Result |
|---|---|
| camera A publishes QoS 1 to `cameras/A/state` | PUBACK **Success**; the ingest client received it |
| camera A publishes QoS 1 to `cameras/B/state` | PUBACK **Not authorized (0x87)**; the ingest client did not receive it |
| camera A subscribes to `cameras/B/state`, to its own topic, to `cameras/+/state` | SUBACK **Not authorized** for all three (cameras cannot subscribe) |
| ingest client subscribes to `cameras/+/state` | Granted QoS 1 |
| ingest client subscribes to `cameras/#` or `#` | Not authorized |
| ingest client publishes to a camera topic | PUBACK Not authorized |
| connect with camera A's username/password but client ID = camera B's, or any other client ID | CONNACK **Not authorized** |
| wrong password, anonymous | CONNACK Not authorized |
| repeating `createRole` / `createClient` | answers with `"Role already exists"` / `"Client already exists"`, so provisioning can be run again safely |

Mosquitto 2.1 writes a default config with extra roles and a demo client when no dynsec file exists; `mosquitto_ctrl dynsec init` was run first so that file is not generated. On Windows, aiomqtt needs the selector event loop (`WindowsSelectorEventLoopPolicy`).

## Facts confirmed for the hook and the clients

- The `-ffmpeg` image is Alpine 3.24; `/usr/bin/wget` is BusyBox 1.37 and supports `--post-data` and `--header` (checked by running them: the receiver got the header and the form body, user agent `Wget`).
- `runOnRecordSegmentComplete` got its `$MTX_...` variables put in (MediaMTX does this itself; it does not use a shell, see `smoke.md`). The body received for one segment: `path=<tenant>/<camera>&file=./recordings/<tenant>/<camera>/2026-10-24_08-07-45-067638.mp4&duration=9.999999999`. The start time is only in the file name, in UTC. `MTX_SEGMENT_PATH` is relative to the working directory `/`. The YAML value must be quoted because it contains `: `.
- The Mosquitto dynsec plugin is `/usr/lib/mosquitto_dynamic_security.so`; `mosquitto_ctrl` is `/usr/bin/mosquitto_ctrl`; the image has no `openssl` command.
- Control API `GET /v3/recordings/get/<tenant>/<camera>` returns `{"name": ..., "segments": [{"start": "<RFC3339>"}, ...]}`: start times only, no durations. An unknown path returns **200** with `"segments": []`. Real responses are saved as `tests/fixtures/mediamtx_recordings_get.json` and `tests/fixtures/mediamtx_paths_list.json`.
- `GET /v3/paths/list` returns `{"itemCount", "pageCount", "items": [{"name", "ready", "online", "source", "readers", ...}]}`; a path is gone from the list (`items: []`) once its publisher stops. Both calls need an `api` JWT: no token 401, a read JWT 401.
- MediaMTX enables a MoQ server by default (`moq: true`); the real config turns it off.

## Open points of proof 1, looked up (2026-10-24)

**`gap.mp4` entries answer 401.** The playlist of a fresh stream starts with `#EXT-X-GAP` entries named `gap.mp4`, written without a
`session` query. MediaMTX answers any media request that has no session with `401 session not found`
(`internal/servers/hls/http_server.go`, the `default:` branch of the request switch, v1.21.2). hls.js 1.7.3 never makes that request:
`src/loader/fragment-loader.ts` (`load()`) rejects a fragment that carries a `GAP` tag before any network call, with the non-fatal
error `FRAG_GAP` ("GAP tag found"); `m3u8-parser.ts` sets `frag.gap = true` for the `GAP` tag. The page's error handler only reports
fatal errors, so the `FRAG_GAP` event is silent. Nothing to change.

**`WWW-Authenticate: Basic realm="mediamtx"`.** MediaMTX sets it only when a request carries no credentials at all (no user, no
password, no token): `Authenticate()` in `internal/auth/manager.go` sets `AskCredentials` as `EnableAskCredentials && user == "" &&
pass == "" && token == ""`, and the HLS server turns that into the header (`http_server.go`). The viewing page always sends
`Authorization: Bearer`, so it never gets this answer; a wrong or expired token gets a plain `401` without the header (seen in the
HTTP checks above). No sign-in dialog is expected for a script request that always carries its token; the real-browser check
will confirm there is none.
