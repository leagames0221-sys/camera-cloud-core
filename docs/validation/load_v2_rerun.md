# Load test of the later version again, with the values taken from models (2026-10-26)

The same conditions ([load_conditions.md](load_conditions.md), not changed), one round of three sessions of 340 seconds, run once
(`python loadtest/measure.py run --label v2b --files compose.yaml compose.load.yaml compose.load.later.yaml`). Raw files:
`load_v2_rerun/` (the same set as `load_v2/`, with Locust's log, which holds the deprecation warnings of its MQTT client, and its two lists of failures and
exceptions, which hold only their header: Locust exited with code 0 in all three, the failure counts are 0 and no probe timed out). The round was not repeated and no trial
session was run. The first round of the later version is [load_v2.md](load_v2.md) (2026-10-25); the first version is
[load_v1.md](load_v1.md).

## What is different from the first round of the later version
- **Three values** that the load passes through: the writer's group of **125 entries or 50 ms** (it was 500 entries or one second), the
  look for unconfirmed entries every **30 seconds** (it was 5), the limit per tenant **100 calls a second** (it was 20; the load
  of path (a) is 10 a second for each load tenant and does not reach either limit). ADR 0007 gives the models.
- **The code is the same as in this repository's first commit** (the services, the web page and `loadtest/`; the memory limits in `compose.yaml` were set afterwards, below), not that of the second version: among other things the writer of stamp 1 now also puts each indexed
  segment on a second stream for the motion worker (`ANNOUNCE_RECORDED`, ADR 0011). The workers (`motion`, `classify`, `cam-b2`) and everything of
  stamp 2 were **stopped** for the round, so nobody read that stream. The images are the ones built that day:
  `ccc-api:3.0` 34e27e3c1fbe, `ccc-gateway:3.0` 13150b31697a, `ccc-camera:3.0` 29925b5c79d6 (ADR 0001); load-test `ccc-loadtest:1.0` f19e0554930f
  and the programs of `loadtest/` from the checkout as before. The only change to them is a comment in `setup_load.py`.
- Machine: the same Docker Desktop with WSL2 (16 CPUs, 16.8 GB), recorded in `load_v2_rerun/environment.json`. The host's mean CPU
  while all users were running was 14 % (11 to 18), the same as the first version's round and higher than the 8 % of the first round of the later version.
- The memory limits of the containers were still those `compose.yaml` had before (they were changed afterwards, 0012, `memory_limits.md`), and the
  round was not run again on the new ones. The memory columns below are `docker stats`; the limits were worked out from the kernel's own
  `memory.peak` (`memory_limits.md` says why).

## Results next to the first round of the later version (median of the three sessions; lowest and highest in brackets)

### Locust (300 seconds with all users running)

| Path | Requests per second, before | now | p50 (ms), before | now | p95 (ms), before | now | Failures (%) |
|---|---|---|---|---|---|---|---|
| (a) camera list of a tenant | 16.7 | 16.7 | 41 (36 to 45) | 43 (40 to 52) | 90 (88 to 91) | 85 (82 to 99) | 0.00 and 0.00 |
| (a) one camera | 16.7 | 16.7 | 39 (35 to 45) | 43 (39 to 52) | 90 (86 to 92) | 84 (83 to 100) | 0.00 and 0.00 |
| (a) segment list of a camera | 16.7 | 16.7 | 41 (35 to 44) | 43 (40 to 51) | 91 (87 to 92) | 84 (82 to 99) | 0.00 and 0.00 |
| (b) closed-segment call | 100.0 | 100.0 | 1 (1 to 1) | 1 (1 to 1) | 2 (2 to 2) | 2 (2 to 3) | 0.00 and 0.00 |
| (c) MQTT state, time to the broker's acknowledgement | 299.6 | 299.6 (299.3 to 299.6) | 1 (1 to 1) | 1 (1 to 1) | 2 (2 to 2) | 2 (2 to 4) | 0.00 and 0.00 |

### Time until the API shows the change (probe, milliseconds)

| Probe | Timeouts, before and now | p50, before | now | p95, before | now | Slowest, before | now |
|---|---|---|---|---|---|---|---|
| segment call, until the segment list shows it | 0 and 0 | 563 (562 to 706) | 68 (66 to 68) | 1077 (1075 to 1078) | 70 (68 to 71) | 1079 (1076 to 1080) | 70 (69 to 71) |
| state message, until the camera shows its `fps` | 0 and 0 | 560 (558 to 562) | 65 (64 to 66) | 1072 (1069 to 1075) | 68 (66 to 123) | 1073 (1071 to 1076) | 69 (66 to 133) |

The probe asks after 20, 52, 103, 185, 316 and 526 ms and then every 250 ms or so (`loadtest/probe.py`), so a value is the time of the
first asking that found the change, exact to the gap before it.

### Containers (mean CPU in CPUs while all users were running; highest memory of a session in MiB, median of the three)

| Container | CPU before | now | Memory before | now |
|---|---|---|---|---|
| writer | 0.18 | 0.21 (0.21 to 0.21) | 58 | 58 (49 to 60) |
| API | 0.12 | 0.13 (0.12 to 0.14) | 70 | 68 (58 to 71) |
| gateway | 0.14 | 0.12 (0.12 to 0.14) | 57 | 55 (45 to 58) |
| PostgreSQL | 0.05 | 0.06 (0.05 to 0.07) | 56 | 57 (56 to 58) |
| Valkey | 0.04 | 0.04 (0.04 to 0.05) | 26 | 48 (45 to 54) |
| load-test container | 0.11 | 0.12 (0.11 to 0.12) | 101 | 112 (103 to 115) |
| Mosquitto | 0.02 | 0.02 (0.02 to 0.03) | 10 | 8 (8 to 9) |
| MediaMTX | 0.01 | 0.01 (0.01 to 0.02) | 25 | 24 (24 to 29) |
| Keycloak | 0.00 | 0.00 | 747 | 826 (826 to 827) |

### PostgreSQL over a whole session (per second)

| Counter | before | now |
|---|---|---|
| tuples inserted + updated | 360 (359 to 361) | 371 (371 to 372) |
| committed transactions | 384 (383 to 384) | 405 (400 to 414) |

## What this shows
- **A change reaches the API much sooner:** a segment after 68 ms at the median and 70 ms at the 95th percentile (it was 563 and 1077), a
  state after 65 and 68 ms (it was 560 and 1072; the first version had 29 and 30 ms for a segment and 209 and 346 ms for a state, `load_compare.md`).
  The groups close after 50 ms and not after one second, which is what ADR 0007 says the wait was the price of.
- **The price moved to the database a little:** the writer used 0.21 CPU (0.18), PostgreSQL committed 405 transactions a second (384) for 371 rows
  (360) and used 0.06 CPU (0.05); the first version committed 1857. A group is smaller, so there are more of them, but most of the saving against the first
  version is still there.
- **The viewer calls and the other paths are the same** within the spread of the sessions (p50 43 ms against 41, p95 about 85 against 90; the closed-segment call 1 and 2 ms; the MQTT acknowledgement 1 and 2 ms; no failure).
- Valkey's memory is 48 MiB against 26. This round differs from the first by the stream of recorded segments that nobody read (above),
  which is the likely cause; it was not isolated.
- The limit of 100 calls a second per tenant was not reached by this load, as the 20 was not.

The comparison with the first version (all values) for this round is `load_v2_rerun/compare.md`, made by `measure.py compare`.
