| Measure | First version | Later version |
|---|---|---|
| GET /tenants/{t}/cameras: per second | 16.7 (16.7 to 16.7) | 16.7 (16.7 to 16.7) |
| GET /tenants/{t}/cameras: p50 (ms) | 70 (70 to 72) | 43 (40 to 52) |
| GET /tenants/{t}/cameras: p95 (ms) | 110 (110 to 110) | 85 (82 to 99) |
| GET /tenants/{t}/cameras: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| GET /tenants/{t}/cameras/{c}: per second | 16.7 (16.7 to 16.7) | 16.7 (16.7 to 16.7) |
| GET /tenants/{t}/cameras/{c}: p50 (ms) | 70 (70 to 70) | 43 (39 to 52) |
| GET /tenants/{t}/cameras/{c}: p95 (ms) | 110 (110 to 110) | 84 (83 to 100) |
| GET /tenants/{t}/cameras/{c}: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| GET /tenants/{t}/cameras/{c}/segments: per second | 16.7 (16.7 to 16.7) | 16.7 (16.7 to 16.7) |
| GET /tenants/{t}/cameras/{c}/segments: p50 (ms) | 70 (70 to 71) | 43 (40 to 51) |
| GET /tenants/{t}/cameras/{c}/segments: p95 (ms) | 110 (110 to 110) | 84 (82 to 99) |
| GET /tenants/{t}/cameras/{c}/segments: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| POST segment call: per second | 100.1 (100.1 to 100.1) | 100.0 (100.0 to 100.0) |
| POST segment call: p50 (ms) | 2 (2 to 2) | 1 (1 to 1) |
| POST segment call: p95 (ms) | 44 (44 to 44) | 2 (2 to 3) |
| POST segment call: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| MQTT publish:1:cameras/+/state: per second | 299.3 (299.3 to 299.3) | 299.6 (299.3 to 299.6) |
| MQTT publish:1:cameras/+/state: p50 (ms) | 1 (1 to 1) | 1 (1 to 1) |
| MQTT publish:1:cameras/+/state: p95 (ms) | 2 (2 to 2) | 2 (2 to 4) |
| MQTT publish:1:cameras/+/state: failures (%) | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) |
| Until the API shows a segment change: p50 (ms) | 29 (29 to 29) | 68 (66 to 68) |
| Until the API shows a segment change: p95 (ms) | 30 (30 to 31) | 70 (68 to 71) |
| Until the API shows a segment change: slowest (ms) | 30 (30 to 39) | 70 (69 to 71) |
| segment probes that timed out (of 30 (30 to 30) / 30 (30 to 30)) | 0 (0 to 0) | 0 (0 to 0) |
| Until the API shows a state change: p50 (ms) | 209 (209 to 211) | 65 (64 to 66) |
| Until the API shows a state change: p95 (ms) | 346 (345 to 348) | 68 (66 to 123) |
| Until the API shows a state change: slowest (ms) | 346 (346 to 349) | 69 (66 to 133) |
| state probes that timed out (of 30 (30 to 30) / 30 (30 to 30)) | 0 (0 to 0) | 0 (0 to 0) |
| PostgreSQL: tuples inserted + updated per second | 371 (371 to 371) | 371 (371 to 372) |
| PostgreSQL: committed transactions per second | 1857 (1856 to 1858) | 405 (400 to 414) |
| Whole host: mean CPU (% of all logical processors) | 14 (11 to 16) | 14 (11 to 18) |

| Container | CPUs used, first | CPUs used, later | Memory MiB (highest), first | Memory MiB, later |
|---|---|---|---|---|
| api | 0.40 (0.40 to 0.41) | 0.13 (0.12 to 0.14) | 72 (72 to 75) | 68 (58 to 71) |
| cam-a1 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 30 (30 to 30) | 30 (30 to 30) |
| cam-a2 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 30 (30 to 30) | 30 (30 to 30) |
| cam-b1 | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 37 (37 to 37) | 30 (30 to 30) |
| gateway | 0.13 (0.13 to 0.13) | 0.12 (0.12 to 0.14) | 57 (56 to 58) | 55 (45 to 58) |
| keycloak | 0.00 (0.00 to 0.00) | 0.00 (0.00 to 0.00) | 742 (738 to 744) | 826 (826 to 827) |
| locust | 0.11 (0.11 to 0.12) | 0.12 (0.11 to 0.12) | 103 (103 to 104) | 112 (103 to 115) |
| mediamtx | 0.01 (0.01 to 0.01) | 0.01 (0.01 to 0.02) | 27 (27 to 27) | 24 (24 to 29) |
| mosquitto | 0.02 (0.02 to 0.03) | 0.02 (0.02 to 0.03) | 10 (10 to 14) | 8 (8 to 9) |
| postgres | 0.22 (0.20 to 0.22) | 0.06 (0.05 to 0.07) | 57 (55 to 57) | 57 (56 to 58) |
| valkey | - | 0.04 (0.04 to 0.05) | - | 48 (45 to 54) |
| writer | - | 0.21 (0.21 to 0.21) | - | 58 (49 to 60) |

