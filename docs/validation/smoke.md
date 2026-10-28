# Smoke run before the first build (2026-10-24)

`docker compose build` and `up` of the real stack had not been run when this was written. To catch mistakes early, the
repository's own files were exercised with plain `docker run` of the already-pulled images (no build) and the API and the
camera program's Python code running on the host. Script: `poc/smoke_up.sh`, `poc/run_api_host.py`, `poc/smoke.py`.

What ran for real: PostgreSQL 17.11 (migrations applied from empty, then re-run with "none" to apply), Mosquitto 2.1.2 with
`infra/mosquitto/mosquitto.conf` and `entrypoint.sh`, MediaMTX 1.21.2 with `infra/mediamtx/mediamtx.yml` and
`segment-hook.sh`, Keycloak 26.8.1 with `infra/keycloak/ccc-realm.json` and the feature flags of `compose.yaml`, the API
(`app.main:build_app`, real PostgreSQL / MQTT / Control API / Keycloak keys), the `Camera` class of `services/camera`
(MQTT part), and the integration tests' login helper.

## Result
| Check | Result |
|---|---|
| Realm import with the real file; `--features-disabled=impersonation,token-exchange-standard` accepted; impersonation call | imported; flags accepted; impersonation returns `501` |
| Keycloak login by authorization code + PKCE with `oidc_flow.py` | token with `iss=http://localhost:8080/realms/ccc`, `azp=ccc-web`, `sub` = the fixed id |
| Authorisation request without PKCE / implicit / password grant / wrong redirect URI | `invalid_request` / `unauthorized_client` / `unauthorized_client` / `400` |
| Identity providers in `master` and `ccc` | none |
| Keycloak's keys through the back-channel host name (`Host: keycloak:8080`) | `200` |
| `tests/integration/test_01_identity.py` against that Keycloak | 16 passed |
| Keycloak health check as written in `compose.yaml` (bash `/dev/tcp`, port 9000) | succeeds (`grep` exit 0) |
| API start: migrations, seed, MQTT roles and 3 camera users, intake subscribed | all in the log; the intake's first attempt failed with `Not authorized` before the user existed and succeeded one second later |
| Camera token, then RTSP publish with ffmpeg and the issued token | accepted; camera list shows `recording` for the publishing camera, `disconnected` for the other |
| Live: first request with Bearer | `302` to `?cookieCheck=1`, then `200` playlist |
| Read token of company A on company B's path (MediaMTX) / company B's camera through the API as an A viewer | `401` / `404` |
| Recording index | after the fix below: two rows from the hook (`source: hook`, `duration_ms: 60000`) |
| Playback token and `GET /get` | `200 video/mp4`, begins with `ftyp`, 1.25 MB for 122 s |
| Register as tenant administrator / as viewer / delete | `201` / `403` / `204` |
| Camera program's normal stop (`shutdown()`) | camera shown as `disconnected` |

## Mistakes found by this run and fixed
1. **The segment hook did not authenticate.** The first version of `mediamtx.yml` put `$(cat /run/secret/hook_secret)` inside the
   `runOnRecordSegmentComplete` command. MediaMTX does not run these commands through a shell (a test showed a file literally
   named `bare_$(echo`), so the literal text was sent and the API answered `403`. The reconciliation made up every segment
   (`source: reconcile`, no duration), which is why nothing looked broken at first. Fixed with `infra/mediamtx/segment-hook.sh`.
2. `uvicorn` on Windows uses a loop that `aiomqtt` cannot use; this affects only the host-run smoke helper, not the Linux containers.
3. Keycloak sets its cookies `Secure` even over `http://localhost`; Python's cookie jar will not send them back, so the test
   client sends cookies by hand (a browser treats `localhost` as a secure context and accepts them).

## Memory (docker stats, with traffic)
Keycloak 591 MiB (limit 1 GiB), PostgreSQL 56 MiB, MediaMTX 34 MiB (one stream), Mosquitto 8 MiB, one ffmpeg publisher 20 MiB.
The API and the cameras ran on the host, so their container numbers are not known; the limits in `compose.yaml` add up to
about 3.2 GB. The real total of the ten containers has not been measured.

## Not done here
The container builds (API, gateway, camera), the `apk add python3` + venv step of the camera image, the `volume.subpath`
mounts, the compose start-up order, the gateway serving the page, a real browser, and the integration tests 02 to 06.
