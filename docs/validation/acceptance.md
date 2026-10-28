# Acceptance checks run by the author (items 1 to 5), 2026-10-24

Run against the real stack: `docker compose -f compose.yaml -f compose.test.yaml up -d`, started once and kept up for all of
the checks below (the only container actions after that were the `kill` / `stop` / `pause` / `start` of single cameras that the
checks themselves require, and `restart api` by the mutation runner). Times are UTC as printed by the containers. The raw output
of `scripts/acceptance_8_1.py` was kept in `poc/out/acceptance.json` (not committed). The output of pytest and of the other commands
below (pass counts, times in seconds, the GPU line, the counted ids) was read from the screen and is not kept in the repository; the
counts of tests can be checked with `pytest --collect-only`.

The integration tests were run once before this, on the same stack: **51 passed in 242.7 s** (`pytest -m integration`), with no
fix needed. The mutation check is in `mutations.md`.

## 1. One command brings everything up
`docker compose -f compose.yaml -f compose.test.yaml up -d` took about 19 seconds to return (images were already built). The
ten containers: `init` exited 0; `postgres`, `keycloak`, `mosquitto`, `api`, `mediamtx`, `gateway` healthy; `cam-a1`, `cam-a2`,
`cam-b1` running. MediaMTX logged the first publisher about one second after the cameras started.

Memory (`docker stats --no-stream`, about 10 minutes after start, three cameras publishing; this output was not kept):

| Container | Used | Limit |
|---|---|---|
| keycloak | 829.6 MiB | 1 GiB |
| postgres | 57.2 MiB | 256 MiB |
| mediamtx | 43.3 MiB | 512 MiB |
| api | 45.1 MiB | 256 MiB |
| gateway | 39.0 MiB | 128 MiB |
| mosquitto | 8.1 MiB | 128 MiB |
| cam-a1 | 29.1 MiB | 256 MiB |
| cam-a2 | 28.7 MiB | 256 MiB |
| cam-b1 | 70.3 MiB | 256 MiB |
| **total** | **about 1,150 MiB (1.1 GiB)** | the nine limits add up to 3,072 MiB (3.0 GiB) |

Keycloak sits at 81 % of its limit; a second sign-in storm could reach it. The total is far below 8 GB.

## 2. Company A viewer: list, live, past five minutes
Signed in with the authorization code + PKCE flow as `a-viewer`. The playback check was made about 5 minutes 40 seconds after the
cameras started (cameras started 08:57:35; the request was made at about 09:03).

| Step | Result |
|---|---|
| `GET /api/v1/tenants/<A>/cameras` | `200`; "A 社 カメラ 1" `recording` (15.02 fps), "A 社 カメラ 2" `recording` (15.14 fps); no company B camera |
| `POST .../cameras/<A1>/live` | `200`, an HLS URL and a 5-minute `read` token |
| first playlist request with `Authorization: Bearer` | `302` to `/<tenant>/<camera>/index.m3u8?cookieCheck=1` |
| request after the redirect with Bearer | `200`, multivariant playlist |
| variant playlist with Bearer / with only the `session` query | `200` / `200`; it carries `EXT-X-TARGETDURATION` |
| `POST .../cameras/<A1>/playback` | `200`; start `08:58:15.84Z` (the first recorded segment), duration 300.0 s, token valid 5 minutes from issue |
| `GET <playback url>` with Bearer | `200`, `video/mp4`, 3,073,420 bytes, starts with `ftyp`; `ffprobe`: h264, 640x360, **duration 300.04 s** |

The video was checked with ffprobe; it was not watched in a browser (that check is separate).

## 3. Company B's camera is refused by the API and by MediaMTX
As `a-viewer`, every request below answered `404` with the body `{"detail":"not_found"}`, the same as for an id that does not exist:
`GET /tenants/<B>/cameras/<B1>`, `GET /tenants/<B>/cameras`, `POST /tenants/<B>/cameras/<B1>/live`, `POST .../playback`, and
`GET /tenants/<A>/cameras/<B1>` (B's camera under A's tenant).

MediaMTX: the `read` token that the API issued for A's camera, used on B's path
(`GET :8888/<B>/<B1>/index.m3u8?cookieCheck=1`): **`401`**, body `{"status":"error","error":"authentication error"}`. The same token
on A's own path: `200`. In `tests/integration/test_03_isolation.py` the other ways of being wrong (expired, wrong action, wrong key,
wrong audience, wrong issuer) and the playback server and an RTSP request were also checked: all `401`.

## 4. Company A's camera credentials against company B's topic
A camera registered in company A (MQTT 5.0, user name = client ID = its UUID) connected (`Success`).

| Publish (QoS 1) | PUBACK |
|---|---|
| `cameras/<its own id>/state` | `0` Success |
| `cameras/<B1 id>/state` with `{"state":"offline","reason":"forged"}` | **`135` (0x87) Not authorized** |

After 3 seconds the API still showed camera B1 as `recording` with no reason: the forged message did not reach the intake. The
temporary camera was deleted afterwards.

## 5. Disconnection times
Poll interval 0.5 s through `GET /api/v1/tenants/<T>/cameras/<id>`; Keep Alive is 10 s, so the limit for silence is 15 s.

| Action | Issued at | Shown `disconnected` at | Seconds | Reason |
|---|---|---|---|---|
| `docker kill` cam-a2 | 09:03:35.733 | 09:03:36.213 | 0.5 | `will` |
| `docker stop` cam-a2 | 09:03:38.817 | 09:03:39.636 | 0.8 | `normal` |
| `docker pause` cam-b1 | 09:03:42.236 | 09:03:53.218 | 11.0 | `will` |

The pause was noticed 11.0 s after the command (the last packet from the camera had arrived a few seconds before the pause), within the
15 s limit. The integration test `test_05_liveness.py` measured the same three cases (limit 15 s plus 5 s of slack) and passed. All cameras were restarted / unpaused
afterwards and were `recording` again.

## Not covered
Nothing was watched in a browser (live playback with hls.js, blob playback of the past five minutes, the screens, the logout). That
check is separate.

---

# The later version: acceptance checks run by the author (2026-10-25)

Run on the stack `docker compose -f compose.yaml -f compose.test.yaml up -d` (all eighteen containers; `cam-device` on request). The
numbers in the text are those of the commands and tests named; screenshots were not taken by the author.

## 6. A tenant administrator approves a code and the new camera appears and plays; a viewer cannot
`tests/integration_v2/test_05_device_camera.py` runs the real thing: `docker compose --profile device up -d cam-device`; the container's
log shows the line "To add this camera, open http://localhost:8080/realms/ccc/device and enter the code XXXX-XXXX" (and not the
device code); the test logs in as `c-admin` on Keycloak's pages, enters the code and approves; **the new camera is in company C's list
within the minute that the test allows** (the test fails if it takes longer) and then `recording` (the test waits up to another 60 s), and the HLS playlist of its live address on stamp 2 (port
8889) is served. The camera's volume holds its camera id and secret and no token; a restart does not ask for a code again. The second
test has `c-viewer` enter the code: Keycloak lets a viewer approve the device (`test_04_device_grant.py` gets a token after a viewer approves), the camera is told
`403` by the API ("registration stopped"), and nothing is listed. `test_04_device_grant.py` (12 tests) covers the rest: pending, slow_down,
access denied, an expired code (the code lifetime is set to 15 s through Keycloak's admin API during the test and put back), the same token twice
and four calls at the same moment (exactly one camera), the audience (`ccc-enroll` only, no `account`), no refresh token, no ID token, the token
refused on every other call and the page's token refused here.

## 7. The earlier tests pass unchanged; the API description breaks nothing
`tests/integration` was not changed since the first version (at that time checked with a diff against the release of the first version, which is not in this repository). The 52 integration tests of the first version: **52 passed** (214 s) on the stack with
two stamps, the writers and the queue, after the last change of the code. The 40 tests of `tests/integration_v2`: **40 passed** (420 s), also after the last change. The unit tests: 297 passed, 2 skipped (Windows). `docs/validation/openapi_diff.md`: oasdiff exits with 0 for both `--fail-on ERR` and
`--fail-on WARN`; the only change is the new endpoint.

After a review (2026-10-25: the API's own segment door limited to the first stamp, the writer's counters behind the stamp's
secret, segments of another stamp counted, more tests), on the same stack after the last change of the code: the 52 tests of the
first version **52 passed** (220 s), `tests/integration_v2` **42 passed** (426 s), the unit tests **313 passed, 2 skipped**. The API
description is unchanged (the running service's `/api/v1/openapi.json` equals `docs/openapi/v2.json`).

## 8. The two versions side by side
`docs/validation/load_compare.md` (tables), with the reasons in ADR 0010.

## 9. Formulas, measured values and assumptions are separate
`docs/capacity.md`.

## Liveness with the writer in the path
The three cases of `test_05_liveness.py` were measured again (`-s`): `docker kill` was seen as disconnected after **0.5 s**, `docker stop` after
**0.5 s** (reason `normal`), `docker pause` after **16.2 s** against the limit of 20 s (a Keep Alive of 10 s times 1.5 = 15 s, plus 5 s of slack; the
writer adds up to one second of waiting for its group). The first version's record above shows 11.0 s for the pause; the pause is noticed
when the broker's keep-alive timer runs out, and the moment of the last packet before the pause differs from run to run.

## Mutation check
Twelve places were broken one at a time and all were caught by at least one test: `docs/validation/mutations.md` (the
twelfth, the limit of handings out of the queue, was added after a review).

## Not covered
Nothing was watched in a browser by the author: the screen with the new link, the live picture on stamp 2 and the consent page are checked
through their HTTP answers and through the text of the pages. The acceptance 7-3 in `docs/requirements.md` is for a person to do.

---

# The workers that look at the recordings: acceptance checks run by the author (2026-10-25)

Run on the stack `docker compose -f compose.yaml -f compose.test.yaml --profile vision up -d`, with the four images of the version
built by hand (`ccc-api:3.0`, `ccc-gateway:3.0`, `ccc-camera:3.0`, `ccc-vision:3.0`; Ids in `docs/adr/0001-components.md`) and the
checkout's code mounted over their copy of the code (the same source). The GPU: `docker run --rm --gpus=all ccc-vision:3.0 python
-c "import torch;print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"` printed `True NVIDIA GeForce RTX 5080`.

## 10. Events appear for the check camera, and only where the video moves; the numbers are written down
`tests/integration_v3` (7 tests, 40 s): the camera `cam-b2` streams the check video again and again and prints when each round began; the
time of each event minus that gives a second of the video. In the finished rounds, **there is movement inside seconds 64 to 116 and none
inside seconds 4 to 56 and 124 to 176** (the held pictures, with the 4 seconds next to each join left out), named things are found
only in the moving part (a name, a score above 0.9, the model and its commit in each), the list is newest first and says each event has
its video, an event is played from 5 seconds before it (the video server answered 200 `video/mp4` for the token), another company
gets 404 and the partner administrator 403, the cameras of the test pattern have no event at all, and the writer keeps one row for an
event sent twice and refuses events without a recording, for a camera that does not exist and for another company's camera.

`docs/validation/vision_eval.md` has the rest: movement second by second against the way the video was made, for the cuts shifted by 0,
20 and 40 seconds (recall 0.885, 0.865 and 0.865; precision 1.000 in all three; no target value was set), and the model card's output
reproduced (remote 2, couch 1, cat 2, every score as on the card and every box with an overlap of 1.000), 28 ms a picture on the GPU.

## 11. The documents open; the earlier tests pass unchanged; the API description breaks nothing
- The ADRs 0001 to 0013, `docs/code-reading.md`, `docs/design-guide.md` and the README open: every relative link in them was checked to point at an existing file.
- `tests/integration` and `tests/integration_v2` were not changed since the second version (at that time checked with a diff against the release of the second version, which is not in this repository).
- On the final state of the code: unit tests **400 passed, 2 skipped**; `tests/integration` **52 passed** (221 s); `tests/integration_v2`
  **42 passed** (423 s); `tests/integration_v3` **7 passed**.
- oasdiff 1.33.0: `docs/openapi/v1.json` to `v2.json` and `docs/openapi/v2.0.1.json` to `v2.json` both 0 breaking changes
  (`docs/validation/openapi_diff.md`).
- The mutation check now has 17 places, all caught (`docs/validation/mutations.md`).

## Not covered (this part)
- Nothing was watched in a browser by the author: the new list of events and its button are checked through the API's answers and
  the video server's answer to the token.
- The pictures of the README for people without a GPU are made by the one who reviews the screen, not by this record.
- A machine without a GPU, and the case where the classify worker stops for lack of weights or CUDA, were not tried (only the checks
  that stop it are unit-tested).
- The first run of `tests/integration_v2` after the images were built had two failures of `test_05_device_camera.py`: the new camera did
  not reach "recording" within the 60 seconds the test allows (the cause was not isolated; the machine was busy with the other
  tests and the vision workers) and the second test found the cameras the first had left. Run alone the two tests passed (58 s), and the final run of all 42 passed.
  `test_03_isolation.py::test_control_api_is_not_published` failed once because a container's configuration hash, which is part of
  the output it searches for "9997", happened to contain those digits; changing the configuration of that container (a stop grace
  period) changed the hash. That does not take the cause away: with the profile `vision` the output holds 83 ids and hashes, 3,580
  hexadecimal characters (counted on 2026-10-25), so when they are all new (new images, new containers) it holds "9997" by chance
  in about 5 % of the cases, and a rebuilt image or a changed service draws its part again. The earlier test is not changed (it may
  not be); `tests/integration_v3/test_02_guards.py::test_the_control_api_port_is_published_by_no_container` looks at the published
  ports and says whether every "9997" of the output is inside an id or a hash, which tells a chance match from a published port.


## After the review of the third version (2026-10-25)
- Fixed: the writer takes events only of the cameras in `var/vision/cameras.txt` (it took any camera of its stamp with a recorded
  time); an event must end inside its segment and is at most 120 s long (it could be 24 hours); each worker has its own dead
  stream (`ccc:dead:motion`, `ccc:dead:classify`) instead of adding to the writer's `ccc:dead`; the classify worker also stops on
  the loader's errors. The v3 test of the writer's checks now sends the start time with every entry (two entries had been refused
  as unreadable before the table's checks were reached) and compares the writer's counters.
- Applied to the running stack without a rebuild (compose.test.yaml mounts the source): the job of the secrets was run again
  (`users.acl` made again), then `valkey`, `writer`, `writer-s2`, `motion` and `classify` were restarted.
- Tests: unit 413 passed, 2 skipped; integration v1 52 passed, v2 42 passed, v3 10 passed (one run each, on 2026-10-25).
  `tests/integration` and `tests/integration_v2` were not changed since the second version (checked at that time with a diff against its release).
- Mutation check: 19 places; m8, m15, m16, m18, m19 run again, all caught (`mutations.md`).
- The images `ccc-api:3.0` and `ccc-vision:3.0` still hold the code of the third version before its review and have to be built again.

## The images built again, and the screens watched in a browser
- `ccc-api:3.0` (Id 766a12e2374e) and `ccc-vision:3.0` (Id d8d872cccc44) were built again from the code after the review of the third version
  (`docker compose --profile vision build api motion`), the stack brought up on them, and Valkey, the writer and the two workers
  restarted for the new ACL file.
- In Microsoft Edge, `b-viewer` signed in on the Keycloak page, the list showed the two cameras of company B, the page of the check
  video showed its events (things and movement), and pressing 「この時刻から再生」 on an event played the recording of that time
  (「出来事の録画（10 秒）」). Done once by the author with a driven headless Edge, and once by hand. The two pictures of the README
  were taken then (`docs/images/`).


## Values changed to the values of models (2026-10-26)

Eight values were changed to the values of the models named in `docs/sources.md` (the sentences are in the decision records
named): the tenant limit 20 to 100 a second (Stripe's global limit, 0007), the pictures given to the model 8 to 1 (transformers'
pipelines guide, 0011), the writer's group 500 entries or 1 s to 125 entries or 50 ms (Logstash, 0007), the look for entries nobody
confirmed 5 s to 30 s (BullMQ, 0007), the MQTT Keep Alive 10 s to 60 s (paho-mqtt, 0005), the life of a device code 300 s to 600 s
(Keycloak's default, 0009), the list of events 24 hours and 500 to 100 in an answer and pages (Frigate, Amazon Kinesis Video Streams,
0004) and the 15 % margin of the memory limits of the containers (Kubernetes' Vertical Pod Autoscaler, 0012; the VPA gives only the 0.15 margin; the readings and the limits are the author's, as are the 512 MiB for PostgreSQL and the use of Keycloak's cluster example for one container).

Until now `tests/integration` and `tests/integration_v2` were unchanged since the second version (the statements above). They are not any more. Only the
lines whose value changes with the values above were changed, and nothing they check was dropped. The lines, and the model each one
follows (these are exactly the lines of the two folders that differ from the second version):

| File | Line before | Line after | Because |
|---|---|---|---|
| `tests/integration/test_05_liveness.py` | docstring "Keep Alive 10 s x 1.5 = 15 s" | "Keep Alive 60 s x 1.5 = 90 s" | Keep Alive 60 (paho-mqtt, 0005) |
| the same | `KEEPALIVE_SECONDS = 10`; comment "15 s of silence" | `KEEPALIVE_SECONDS = 60`; "90 s of silence" (`DETECTION_LIMIT` is 95 s, as 15 + 5 was 20 s) | the same |
| `tests/integration_v2/test_02_queue.py` | `wait_for(... == 555.0, 60,` | `wait_for(... == 555.0, 90,` | the entry is taken at the first look after it has been idle 30 s, and the looks are 30 s apart (BullMQ, 0007): up to 60 s after it was handed out |
| `tests/integration_v2/test_03_limit.py` | the limit of 20; the 21st call; bursts of 25, 21, 40, 20, 30, 20, 30, 20 calls; `codes[:20] == [200] * 20` | the limit of 100; the 101st call; bursts of 105, 101, 200, 100, 150, 100, 150, 100 calls; `codes[:100] == [200] * 100` | 100 a second (Stripe, 0007); each burst keeps its ratio to the limit (limit + 5, limit + 1, twice the limit, the limit, one and a half times the limit) |
| `tests/integration_v2/test_04_device_grant.py` | `st["expires_in"] == 300` (twice) | `== 600` | the life of a code 600 (Keycloak's default, 0009). The check of the access token's life (`exp - iat <= 300`) and the forged token's `ttl=300` are not about the code and are unchanged |

`tests/integration_v3/test_01_events.py` was changed for the pages of the list of events (the tests of the vision version
are not among the earlier ones): the fixture reads the list page by page, and a test checks the pages (100 in an answer, no event
twice, `limit` out of 1 to 1000 refused). The unit tests of the list, of the realm file and of the batch size of the classify worker
were changed in the same way.

### What was run on these values (2026-10-26)
- The images `ccc-api:3.0`, `ccc-gateway:3.0`, `ccc-camera:3.0` and `ccc-vision:3.0` were built again from the code of this repository's first commit (Ids in ADR 0001)
  and the stack brought up on them. Unit tests: **419 passed, 2 skipped**. The mutation check was run again on the places the change
  touched (m8, m9, m11, m12, m17, m18, m19): all caught; the patch of m17 was made again because its lines moved.
- Keycloak: the attribute `oauth2.device.code.lifespan` of the running realm was set to 600 through the admin API (it was 300; an existing
  realm is not imported again), and when Keycloak was made new later the realm file gave 600 by itself (`test_04_device_grant.py` checks `expires_in == 600`).
- First run, on the memory limits that `compose.yaml` had before this change: `tests/integration` **52 passed** (270 s), `tests/integration_v2` **42 passed**
  (423 s), `tests/integration_v3` **11 passed** (52 s; the list of events had more than 100 events, so the page tests ran and
  none was skipped). Second run on memory limits worked out from `docker stats` (replaced afterwards, `memory_limits.md`): 52 passed (276 s), 42 passed (430 s), 11 passed (51 s).
  Third run, with limits wide enough to read the kernel's `memory.peak` of containers made new: **52 passed** (254 s), **42 passed** (425 s), **11 passed** (52 s).
  Fourth run, on the final memory limits (`memory_limits.md`: the rules of PostgreSQL and Keycloak, the kernel's highest reading plus 15 % for the others):
  **52 passed** (249 s), **42 passed** (424 s), **11 passed** (52 s); no container was killed (`memory/cgroup_memory_peak_last_run.csv`).
- `test_05_liveness.py`: `docker kill` and `docker stop` were seen as disconnected at once; `docker pause` after **62.4 s**, **60.9 s**, **50.8 s** and **53.3 s** in the
  four runs against the limit of 95 s (Keep Alive 60 s x 1.5 = 90 s plus 5 s). The 100 calls of `test_03_limit.py` in a row took less than a second.
- The pages of the screen: the function that reads the pages (`readEvents` in `web/app.js`) was run in Node against a list of 230 fake
  events (one page: 100 and "more"; three pages: 230 and no more; five pages asked: 230 and no more, three calls). It was not watched in a
  browser by the author.
- Load test: one round of three sessions, `docs/validation/load_v2_rerun.md` (a change shows in the API after about 70 ms at the 95th percentile, it
  was 1.07 s; nothing failed). Memory limits: `memory_limits.md`.
- oasdiff: `docs/validation/oasdiff_output.txt` (0 breaking changes in all three comparisons).
