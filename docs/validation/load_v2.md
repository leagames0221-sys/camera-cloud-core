# Load test of the later version (the second version), 2026-10-25

Conditions: [load_conditions.md](load_conditions.md), the same file that was committed before the first measurement and not changed
since. Raw files: `load_v2/` (the same set of files as in `load_v1/`). The side-by-side table is
[load_compare.md](load_compare.md).

## What was measured
- Stack: `docker compose -f compose.yaml -f compose.load.yaml -f compose.load.later.yaml --profile load up -d` with the services of stamp 1
  and the load-test container only. **Stamp 2 (its database, broker, queue, writer, video server) and the camera of company C were
  stopped**, as the conditions say, so both rounds ran the same set of containers plus the later version's writer and queue.
  Images: API and writer `ccc-api:2.0` (`sha256:70b57299bf8e...`), gateway `ccc-gateway:2.0` (`2139f8321770...`), cameras
  `ccc-camera:2.0` (`ce3dbf28d56b...`), Valkey `48332870af35...`, load-test `ccc-loadtest:1.0` (`f19e0554930f...`). The code of the API and
  the writer was taken from the checkout of the second version (mounted over the image; see the note on images in ADR 0001), and the programs of
  `loadtest/` from the checkout, not from the image. The only change since the first round to the programs that make the load (`setup_load.py`): the load tenants are asked to be
  put on stamp 1 (the later version would otherwise put them on stamp 2, which is switched off), and the set-up waits and asks again
  when a tenant's limit of 20 calls a second is reached while 60 cameras are registered. `measure.py` gained `--files` and the
  comparison tables.
- CPU limits as in the conditions: API 1.0 + writer 1.0 + Valkey 0.5 = 2.5 CPUs (the first version's API had 2.5), PostgreSQL 1.0, Mosquitto 0.5,
  gateway 1.0, MediaMTX 1.0, load-test container 2.0. Reconciliation interval 86400 s.
- One measuring round of three sessions of 340 seconds (`python loadtest/measure.py run --label v2 --files compose.yaml compose.load.yaml
  compose.load.later.yaml`). Locust exited with code 0 in all three (see `load_v1.md`); the failure counts are 0; no probe timed out. The round was not repeated.
- Machine: the same as in `load_v1.md` (Docker Desktop with WSL2, 16 CPUs and 16.8 GB; host with 32 logical processors). The host's mean CPU
  while all users were running was 8 % (8 to 11), lower than in the first round (14 %); the cause was not recorded.
- **Before the round, two trial sessions of 100 seconds** were run to see that the programs work. The first showed a defect of the later
  version: the camera list and the single camera took 3 seconds, because the API asked the video servers of **all** stamps which streams
  were running and waited for the stopped one. It was fixed (the API now asks only the tenant's own stamp) and has a unit test
  (`test_the_camera_list_asks_only_the_tenants_own_stamp_for_running_streams`); the numbers of the trials are not used. It
  is a case where a switched-off stamp slowed the tenants of the other, which the design wants to avoid.

## Results (median of the three sessions; lowest and highest in brackets)

### Locust (300 seconds with all users running)

| Path | Requests per second | p50 (ms) | p95 (ms) | Failures (%) |
|---|---|---|---|---|
| (a) camera list of a tenant | 16.7 (16.7 to 16.7) | 41 (36 to 45) | 90 (88 to 91) | 0.00 |
| (a) one camera | 16.7 (16.7 to 16.7) | 39 (35 to 45) | 90 (86 to 92) | 0.00 |
| (a) segment list of a camera | 16.7 (16.7 to 16.7) | 41 (35 to 44) | 91 (87 to 92) | 0.00 |
| (b) closed-segment call (answered when it is on the queue) | 100.0 (100.0 to 100.0) | 1 (1 to 1) | 2 (2 to 2) | 0.00 |
| (c) MQTT state, QoS 1, time to the broker's acknowledgement | 299.6 (299.4 to 299.7) | 1 (1 to 1) | 2 (2 to 2) | 0.00 |

### Time until the API shows the change (probe, milliseconds)

| Probe | Probes per session | Timeouts | p50 | p95 | Slowest |
|---|---|---|---|---|---|
| segment call, until the segment list shows it | 30 | 0 | 563 (562 to 706) | 1077 (1075 to 1078) | 1079 (1076 to 1080) |
| state message, until the camera shows its `fps` | 30 | 0 | 560 (558 to 562) | 1072 (1069 to 1075) | 1073 (1071 to 1076) |

The probe asks after 20, 52, 103, 185, 316 and 526 ms and then every 250 ms or so (see `loadtest/probe.py`), so these values are exact
to a few hundred milliseconds. They are what the design says: a group is written when 500 entries are there or one second after the first,
so a change appears after up to about a second.

### Containers (mean CPU in CPUs while all users were running; highest memory of a session in MiB, median of the three)

| Container | CPU (limit) | Memory (limit) |
|---|---|---|
| writer | 0.18 (1.0) | 58 (256) |
| API | 0.12 (1.0) | 70 (256) |
| gateway | 0.14 (1.0) | 57 (128) |
| PostgreSQL | 0.05 (1.0) | 56 (256) |
| Valkey | 0.04 (0.5) | 26 (320) |
| load-test container | 0.11 (2.0) | 101 (1024) |
| Mosquitto | 0.02 (0.5) | 10 (128) |
| MediaMTX | 0.01 (1.0) | 25 (512) |
| Keycloak | 0.00 (none) | 747 (1024) |

### PostgreSQL over a whole session (per second)

| Counter | Value |
|---|---|
| tuples inserted + updated | 360 (359 to 361) |
| committed transactions | 384 (383 to 384) |

## What this shows
- Under the same conditions the later version wrote nearly the same number of rows (360 against 371 a second) in **about a fifth of the
  transactions** (384 against 1857 a second); PostgreSQL used 0.05 CPU instead of 0.22.
- The parts written here used 0.34 CPU in all (API 0.12 + writer 0.18 + Valkey 0.04) against 0.40 for the first version's API; the load is
  far below both limits, so neither version was near its ceiling and this test cannot say which one has the higher.
- The viewer calls were answered in about 40 ms at the median instead of 70 ms. The test does not show why; the API no longer does the
  writing of state and segments, which is the likely reason, but it was not isolated.
- The price is the delay: a segment or a state change shows up in the API after about half a second at the median and about one second at
  the 95th percentile, against 30 ms for a segment and about 0.2 s at the median (0.35 s at the 95th percentile) for a state in the first version.
- The closed-segment call is answered in 1 to 2 ms because it only has to reach the queue; in the first version it waited for the database
  (p95 44 ms).

## Limits
As in the conditions. In addition: the second stamp was off, so nothing was measured of what a running second stamp costs the first; the API
still tried to reach the stopped stamp every few seconds in the background (seen in its log during the round; the log was not kept), which is part of what was measured.
