# What a million cameras would need: formulas, a measured column and the assumptions

**This is a calculation, not a result.** Nothing here was run with a million cameras, and the repository does not claim that it
could. Each line gives a formula, what was measured in this repository, what the formula gives for N = 1 000 000, and what had to be assumed.
Each assumption names the source it was taken from (until 2026-10-26 the bit rate and a safety factor had no source); what cannot be computed without a measurement is said so. Names of cloud recording services and their prices are not used.

Symbols: N = number of cameras; b = video bit rate of one camera; R = days of video kept; S = length of a segment (60 s); H = interval of
the state message that carries a camera's figures (60 s; 30 s when the load test was made).

## Measured in this repository
| Quantity | Value | Where from |
|---|---|---|
| Bit rate of a simulated camera | **79.97 kbit/s** (15 fps test pattern, 640x360), the same for all four cameras | `bitrate_kbps` in the API's camera list, read once on 2026-10-25 after the load test (the reading was not saved; the same field gives it again); it is the average of the looped clip (`services/camera/camera.py`), not a real camera |
| State messages a stamp took in, with no failure | **299.6 a second** (= 8 988 cameras at one message per 30 s, the interval when it was measured; 17 976 at the 60 s of now) | `load_v2.md`, path (c) |
| Closed-segment calls a stamp took in, with no failure | **100 a second** (= 6 000 cameras at one segment per 60 s) | `load_v2.md`, path (b) |
| CPU used at that load by the stamp's own parts | writer 0.18 (of 1.0), Valkey 0.04 (of 0.5), PostgreSQL 0.05 (of 1.0), Mosquitto 0.02 (of 0.5) | `load_v2.md` |
| Viewer calls | 50 a second, p95 90 ms | `load_v2.md`, path (a) |
| Memory of the parts of a stamp at that load | writer 58 MiB, Valkey 26 MiB, PostgreSQL 56 MiB, Mosquitto 10 MiB (highest in a session, median of the three) | `load_v2.md` |

A second round with the values of 2026-10-26 (`load_v2_rerun.md`) took the same rates in with no failure (299.6 state messages and 100 closed
segments a second); the writer used 0.21 CPU, Valkey 0.04, PostgreSQL 0.06 and Mosquitto 0.02, and the highest memory was writer 58 MiB,
Valkey 48, PostgreSQL 57, Mosquitto 8. The table above is the first round, as it was written.

The load test did not find a limit: nothing failed and no part was near its CPU limit. The message rates that were taken in are what
**at least 6 000** cameras would send (the smaller of the two paths). Only 300 MQTT connections were held and no video was sent, so
6 000 is a number of cameras worked out from message rates, not a number of cameras that was run.

## Formulas and what they give for N = 1 000 000
| Resource | Formula | Measured (one stamp, this repository) | For 1 000 000 cameras | Assumption |
|---|---|---|---|---|
| Open MQTT connections | N | 300 connections held for the test | 1 000 000 | one connection a camera |
| Open RTSP connections (publishers) | N | 3 to 4 simulated cameras only | 1 000 000 | one stream a camera |
| Video coming in | N x b | not measured (the video path is not tested) | 80 Gbit/s at the measured 79.97 kbit/s; **3 000 Gbit/s at 3 Mbit/s** (100 Gbit/s at 100 kbit/s) | b = 3 Mbit/s, the main stream of the typical security camera set-up in Moonfire NVR's schema design ("the high-quality "main" stream as 1080p/30fps, 3000 kbps"; the low-bandwidth sub stream is 100 kbps), the stream an NVR records; Moonfire is GPL-3.0, the figure is cited, nothing copied |
| Video kept | all video: N x b / 8 x 3 x 86 400; then the share with motion (m) for days 4 to 7 and the share with alerts (a) for days 8 to 30: N x b / 8 x 86 400 x (4 m + 23 a) | the recording volume holds one day of four cameras (the setting of this version) | all video for 3 days: **2.59 PB** at 79.97 kbit/s, **97.2 PB** at 3 Mbit/s (3.24 PB at 100 kbit/s); the later days are **not computed**: m and a were not measured in this repository | the most conservative recording set-up of Frigate's recording documentation: it "will store all video for 3 days. After 3 days, only video containing motion will be saved for 7 days. After 7 days, only video containing motion and overlapping with alerts or detections will be retained until 30 days have passed."; decimal petabytes |
| Segment rows written | N / S | 100 a second held for the test | **16 667 a second** | S = 60 s |
| Segment rows alive | N x R x 86 400 / S for all video | one day of four cameras | 1.44 billion (1 day, the setting of this version), 4.32 billion (the 3 days of all video above); the rows of days 4 to 30 depend on m and a (not computed) | the index is kept as long as the video |
| State messages | N / H | 299.6 a second held for the test | **16 667 a second** | H = 60 s, as the simulated cameras (Frigate's `stats_interval`, 0005) |
| API calls from viewers | not a function of N | 50 a second | depends on the number of viewers, not cameras | not estimated |

## How many stamps
- Stamps for the load = N divided by what one stamp took in: 1 000 000 / 6 000 = 166.7, **167**. 6 000 is the smaller of the two measured
  rates and not a limit (the test stopped before any limit), so it is used as a stamp's capacity as it is, without a margin.
- Stamps to provision = 167 + 2 = **169**: Google's SRE book, "Provision to handle a simultaneous planned and unplanned outage, without making the user experience unacceptable; this results in an "N + 2” configuration, where peak traffic can be handled by N instances (possibly in degraded mode) while the largest 2 instances are unavailable" (Appendix B, Capacity Planning). Until 2026-10-26 this page divided by a safety factor of 2, which had no source (334 stamps).
- Per stamp: about 5 900 cameras with all 169 running, 6 000 with two of them down: 12 000 connections (6 000 MQTT + 6 000 RTSP), 100 state messages a second, 100 segment rows a second (the rates measured), and, at 3 Mbit/s a camera, 18 Gbit/s
  of video in (not measured; it would decide the machine the video server needs long before the writer or the database does).

## What this leaves out
- The video server (MediaMTX), its disk and network; Keycloak and the logins of many viewers; a real network between cameras and servers;
  the cost of 169 databases to look after; moving a tenant between stamps (not built, 0006); a failure of one stamp.
- The control plane (API, gateway, Keycloak, the camera ledger) would also grow: the ledger would have 1 000 000 rows, and the API would need
  more than one process (0007), with the limit per tenant moved to Valkey.
- A machine with a bigger CPU or a faster disk would give another measured number; the measurement was made on one machine shared
  with other programs (`load_conditions.md`).
