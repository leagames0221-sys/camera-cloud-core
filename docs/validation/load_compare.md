# The first version and the later version under the same conditions

Both rounds: [load_conditions.md](load_conditions.md) (written before the first measurement, not changed), three sessions of 300
seconds with 370 users, the median of the three with the lowest and highest in brackets. First version = the first version
([load_v1.md](load_v1.md)); later version = the second version ([load_v2.md](load_v2.md)). The sum of the CPU limits of the parts written here is
2.5 CPUs in both. Nothing failed in either; no probe timed out. The reasons for the differences are in
[ADR 0010](../adr/0010-load-test-method-and-result.md).

## What a person can read from it
- **Better:** the closed-segment call is answered at its 95th percentile in 2 ms instead of 44 ms; PostgreSQL commits one fifth as many
  transactions (384 against 1857 a second) for nearly the same rows (360 against 371) and uses less than a quarter of the CPU (0.05
  against 0.22); the viewer calls take about 40
  ms at the median instead of 70 ms.
- **Not better:** a change reaches the API **later**. A segment call shows in the segment list after 563 ms at the median instead of 29 ms,
  and a state message after 560 ms instead of 209 ms (the 95th percentile is 1.07 seconds against 0.35 for the state). This is the price
  of taking entries in groups of up to 500 or one second. The later version also has two more containers to run (the writer 0.18 CPU and
  58 MiB, Valkey 0.04 CPU and 26 MiB).
- **The same:** the number of requests and publishes per second (the load was fixed), the time to the broker's acknowledgement for MQTT (1 ms,
  2 ms at p95), the failure rate (0), the memory of the other containers.
- **Not shown:** which version has the higher ceiling. The API used 0.40 of 2.5 CPUs in the first version and the three parts 0.34 of 2.5 in the
  later; both were far from their limits. A heavier load would be needed to tell, and it was not run (the conditions allow one round).

## All values

| Measure | First version | Later version |
|---|---|---|
| GET /tenants/{t}/cameras: per second | 16.7 (16.7 to 16.7) | 16.7 (16.7 to 16.7) |
| GET /tenants/{t}/cameras: p50 (ms) | 70 (70 to 72) | 41 (36 to 45) |
| GET /tenants/{t}/cameras: p95 (ms) | 110 (110 to 110) | 90 (88 to 91) |
| GET /tenants/{t}/cameras: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| GET /tenants/{t}/cameras/{c}: per second | 16.7 (16.7 to 16.7) | 16.7 (16.7 to 16.7) |
| GET /tenants/{t}/cameras/{c}: p50 (ms) | 70 (70 to 70) | 39 (35 to 45) |
| GET /tenants/{t}/cameras/{c}: p95 (ms) | 110 (110 to 110) | 90 (86 to 92) |
| GET /tenants/{t}/cameras/{c}: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| GET /tenants/{t}/cameras/{c}/segments: per second | 16.7 (16.7 to 16.7) | 16.7 (16.7 to 16.7) |
| GET /tenants/{t}/cameras/{c}/segments: p50 (ms) | 70 (70 to 71) | 41 (35 to 44) |
| GET /tenants/{t}/cameras/{c}/segments: p95 (ms) | 110 (110 to 110) | 91 (87 to 92) |
| GET /tenants/{t}/cameras/{c}/segments: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| POST segment call: per second | 100.1 (100.1 to 100.1) | 100.0 (100.0 to 100.0) |
| POST segment call: p50 (ms) | 2 (2 to 2) | 1 (1 to 1) |
| POST segment call: p95 (ms) | 44 (44 to 44) | 2 (2 to 2) |
| POST segment call: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| MQTT publish:1:cameras/+/state: per second | 299.3 (299.3 to 299.3) | 299.6 (299.4 to 299.7) |
| MQTT publish:1:cameras/+/state: p50 (ms) | 1 (1 to 1) | 1 (1 to 1) |
| MQTT publish:1:cameras/+/state: p95 (ms) | 2 (2 to 2) | 2 (2 to 2) |
| MQTT publish:1:cameras/+/state: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| Until the API shows a segment change: p50 (ms) | 29 (29 to 29) | 563 (562 to 706) |
| Until the API shows a segment change: p95 (ms) | 30 (30 to 31) | 1077 (1075 to 1078) |
| Until the API shows a segment change: slowest (ms) | 30 (30 to 39) | 1079 (1076 to 1080) |
| segment probes that timed out (of 30 (30 to 30) / 30 (30 to 30)) | 0 (0 to 0) | 0 (0 to 0) |
| Until the API shows a state change: p50 (ms) | 209 (209 to 211) | 560 (558 to 562) |
| Until the API shows a state change: p95 (ms) | 346 (345 to 348) | 1072 (1069 to 1075) |
| Until the API shows a state change: slowest (ms) | 346 (346 to 349) | 1073 (1071 to 1076) |
| state probes that timed out (of 30 (30 to 30) / 30 (30 to 30)) | 0 (0 to 0) | 0 (0 to 0) |
| PostgreSQL: tuples inserted + updated per second | 371 (371 to 371) | 360 (359 to 361) |
| PostgreSQL: committed transactions per second | 1857 (1856 to 1858) | 384 (383 to 384) |
| Whole host: mean CPU (% of all logical processors) | 14 (11 to 16) | 8 (8 to 11) |

The "Until the API shows ..." rows are the time from just before the call or publish until the answer of the API that shows it; the probe
asks after 20, 52, 103, 185, 316 and 526 ms and then about every 250 ms (`loadtest/probe.py`), so the values are exact to the gaps (the first
version's 29 ms is the first question; its 209 and 346 ms are the fourth and the fifth).

## Containers while all users were running

| Container | CPUs used, first | CPUs used, later | Memory MiB (highest), first | Memory MiB, later |
|---|---|---|---|---|
| api | 0.40 (0.40 to 0.41) | 0.12 (0.12 to 0.13) | 72 (72 to 75) | 70 (67 to 71) |
| cam-a1 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 30 (30 to 30) | 30 (30 to 30) |
| cam-a2 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 30 (30 to 30) | 30 (29 to 30) |
| cam-b1 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 37 (37 to 37) | 30 (30 to 30) |
| gateway | 0.13 (0.13 to 0.13) | 0.14 (0.11 to 0.14) | 57 (56 to 58) | 57 (45 to 58) |
| keycloak | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.01) | 742 (738 to 744) | 747 (746 to 748) |
| locust | 0.11 (0.11 to 0.12) | 0.11 (0.11 to 0.12) | 103 (103 to 104) | 101 (100 to 101) |
| mediamtx | 0.01 (0.01 to 0.01) | 0.01 (0.01 to 0.02) | 27 (27 to 27) | 25 (25 to 30) |
| mosquitto | 0.02 (0.02 to 0.03) | 0.02 (0.02 to 0.03) | 10 (10 to 14) | 10 (9 to 10) |
| postgres | 0.22 (0.20 to 0.22) | 0.05 (0.04 to 0.06) | 57 (55 to 57) | 56 (55 to 56) |
| valkey | - | 0.04 (0.03 to 0.04) | - | 26 (21 to 30) |
| writer | - | 0.18 (0.17 to 0.19) | - | 58 (47 to 60) |


The whole host's mean CPU is the last row of the table above. Keycloak (no CPU limit) and the three simulated cameras are the same set in both rounds; the later round also runs the writer and Valkey.

---

# Later version again, 2026-10-26
The later version was measured once more after three of its values were taken from models (a group of 125 entries or 50 ms, a look for
unconfirmed entries every 30 seconds, 100 calls a second per tenant; ADR 0007). The first version's values above are the same. A
segment now shows in the API after 68 ms at the median and 70 ms at the 95th percentile (563 and 1077), a state after 65 and 68 ms
(560 and 1072), the writer used 0.21 CPU and PostgreSQL committed 405 transactions a second. The tables, with the earlier round next to
it, are in [load_v2_rerun.md](load_v2_rerun.md), the comparison with the first version in `load_v2_rerun/compare.md`.
