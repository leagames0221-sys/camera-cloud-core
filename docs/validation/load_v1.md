# Load test of the first version (the first version), 2026-10-24

Conditions: [load_conditions.md](load_conditions.md), unchanged since it was committed. Raw files: `load_v1/` (per session:
Locust's statistics and its history every second, the probe's rows, CPU and memory every 10 seconds, the PostgreSQL
counters; `environment.json` for the machine).

## What was measured
- Stack: the files of the release of the first version plus the load-test files (the release files are not in this repository; the load-test files are), started with
  `docker compose -f compose.yaml -f compose.load.yaml --profile load up -d`. Images: API `ccc-api:1.0`
  (`sha256:e266beb5bf6a...`), gateway `ccc-gateway:1.0` (`sha256:c150ab4e05d7...`), load-test container `ccc-loadtest:1.0`
  (`sha256:f19e0554930f...`). CPU limits as in the conditions (API 2.5 CPUs).
- One measuring round of three sessions of 340 seconds (37 seconds of ramp, then 300 seconds with 370 users), started
  with `python loadtest/measure.py run --label v1`. Locust exited with code 0 in all three (the script passes `--exit-code-on-error 0`; the failure counts in
  `locust_stats.csv` are 0); the probe had no timeout. The round was not repeated.
- Before the round, one short trial (one session of 100 seconds) checked that the programs run; its numbers are not used.
- Machine: Docker Desktop (WSL2) with 16 CPUs and 16.8 GB (`.wslconfig`: `memory=16GB`, `processors=16`, `swap=8GB`), on a
  host with 32 logical processors and 31.4 GB (the host's values were read from Windows by hand; the script records only Docker's
  and `.wslconfig`; the comment lines of `.wslconfig` were removed from `environment.json` after the round, no value
  was changed). No other container was running. Other programs on the host were not
  stopped: the host's total CPU while all users were running is in the last block of the first table.

## Results (median of the three sessions; lowest and highest in brackets)

### Locust (300 seconds with all users running)

| Path | Requests per second | p50 (ms) | p95 (ms) | Failures (%) |
|---|---|---|---|---|
| (a) camera list of a tenant | 16.7 (16.7 to 16.7) | 70 (70 to 72) | 110 (110 to 110) | 0.00 |
| (a) one camera | 16.7 (16.7 to 16.7) | 70 (70 to 70) | 110 (110 to 110) | 0.00 |
| (a) segment list of a camera | 16.7 (16.7 to 16.7) | 70 (70 to 71) | 110 (110 to 110) | 0.00 |
| (b) closed-segment call | 100.1 (100.1 to 100.1) | 2 (2 to 2) | 44 (44 to 44) | 0.00 |
| (c) MQTT state, QoS 1, time to the broker's acknowledgement | 299.3 (299.3 to 299.3) | 1 (1 to 1) | 2 (2 to 2) | 0.00 |

Locust rounds response times to about two significant digits (Locust's documentation, configuration), so 110 stands for a value
between 105 and 115.

### Time until the API shows the change (probe, milliseconds)

| Probe | Probes per session | Timeouts | p50 | p95 | Slowest |
|---|---|---|---|---|---|
| segment call, until the segment list shows it | 30 | 0 | 29 (29 to 29) | 30 (30 to 31) | 30 (30 to 39) |
| state message, until the camera shows its `fps` | 30 | 0 | 209 (209 to 211) | 346 (345 to 348) | 346 (346 to 349) |

The probe asks after 20, 52, 103, 185 and 316 ms (the gap grows by 1.6 times up to 250 ms), and each question takes about
10 to 30 ms, so the values are the time of the first question that found the change: 29 ms is the first question, 209 ms
the fourth, 346 ms the fifth. The state change was therefore visible by the fourth question (about 209 ms) in at least half of
the probes and by the fifth (about 346 ms) in all of them. The segment change was visible at the first question every time.

### Containers (mean CPU in CPUs while all users were running; highest memory of a session in MiB, median of the three)

| Container | CPU (limit) | Memory (limit) |
|---|---|---|
| API | 0.40 (2.5) | 72 (256) |
| PostgreSQL | 0.22 (1.0) | 57 (256) |
| gateway | 0.13 (1.0) | 57 (128) |
| load-test container | 0.11 (2.0) | 103 (1024) |
| Mosquitto | 0.02 (0.5) | 10 (128) |
| MediaMTX | 0.01 (1.0) | 27 (512) |
| Keycloak | 0.00 (none) | 742 (1024) |

The whole host while all users were running: mean 14 % (11 to 16) of all its logical processors, highest single sample 23 %
(19 to 26).

### PostgreSQL over a whole session (per second)

| Counter | Value |
|---|---|
| tuples inserted + updated | 371 (371 to 371) |
| committed transactions | 1857 (1856 to 1858) |

The counters cover the whole session of about 347 seconds, including the 37 seconds of ramp, so they are a little under
the 400 rows per second (100 segment rows and 300 state rows) of the full load. The commit count also holds the reads of
the viewer calls and the demo cameras.

## What this shows
- At this load the first version is not near its limit: the API used 0.4 of its 2.5 CPUs and nothing failed. The
  numbers of this file are the reference for the later version, not a measure of how much the first version can take.
- The first version writes every state message and every segment call to PostgreSQL as it arrives, one transaction each.
  The change shows in the API in about 30 ms for a segment call and about 0.2 s at the median (0.35 s at the 95th percentile) for a state message.
- The three viewer calls take about 70 ms each at the median. Their cause was not examined.

## Limits
The same as in the conditions: one machine shared with other programs, one load generator, three sessions (the three agree
closely, which says the conditions were steady, not that they would be on another machine). The video path and Keycloak are
not measured.
