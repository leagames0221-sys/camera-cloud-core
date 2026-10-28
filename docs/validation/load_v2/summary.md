### Locust results (3 sessions; median, then the lowest to highest in brackets)

| Path | Requests per second | p50 (ms) | p95 (ms) | Failures (%) |
|---|---|---|---|---|
| GET GET /tenants/{t}/cameras | 16.7 (16.7 to 16.7) | 41 (36 to 45) | 90 (88 to 91) | 0.00 (0.00 to 0.00) |
| GET GET /tenants/{t}/cameras/{c} | 16.7 (16.7 to 16.7) | 39 (35 to 45) | 90 (86 to 92) | 0.00 (0.00 to 0.00) |
| GET GET /tenants/{t}/cameras/{c}/segments | 16.7 (16.7 to 16.7) | 41 (35 to 44) | 91 (87 to 92) | 0.00 (0.00 to 0.00) |
| POST POST segment call | 100.0 (100.0 to 100.0) | 1 (1 to 1) | 2 (2 to 2) | 0.00 (0.00 to 0.00) |
| MQTT connect | 0.0 (0.0 to 0.0) | 0 (0 to 0) | 0 (0 to 0) | 0.00 (0.00 to 0.00) |
| MQTT publish:1:cameras/+/state | 299.6 (299.4 to 299.7) | 1 (1 to 1) | 2 (2 to 2) | 0.00 (0.00 to 0.00) |
| TOTAL | 449.7 (449.5 to 449.8) | 1 (1 to 1) | 45 (40 to 49) | 0.00 (0.00 to 0.00) |

### Time until the API shows the change (probe, ms)

| Probe | Probes per session | Timeouts per session | p50 | p95 | Slowest |
|---|---|---|---|---|---|
| segment | 30 (30 to 30) | 0 (0 to 0) | 563 (562 to 706) | 1077 (1075 to 1078) | 1079 (1076 to 1080) |
| state | 30 (30 to 30) | 0 (0 to 0) | 560 (558 to 562) | 1072 (1069 to 1075) | 1073 (1071 to 1076) |

### Containers while all users were running (mean CPU in CPUs, highest memory in MiB)

| Container | CPU | Memory |
|---|---|---|
| camera-cloud-core-api-1 | 0.12 (0.12 to 0.13) | 70 (67 to 71) |
| camera-cloud-core-cam-a1-1 | 0.00 (0.00 to 0.00) | 30 (30 to 30) |
| camera-cloud-core-cam-a2-1 | 0.00 (0.00 to 0.00) | 30 (29 to 30) |
| camera-cloud-core-cam-b1-1 | 0.00 (0.00 to 0.00) | 30 (30 to 30) |
| camera-cloud-core-gateway-1 | 0.14 (0.11 to 0.14) | 57 (45 to 58) |
| camera-cloud-core-keycloak-1 | 0.00 (0.00 to 0.01) | 747 (746 to 748) |
| camera-cloud-core-locust-1 | 0.11 (0.11 to 0.12) | 101 (100 to 101) |
| camera-cloud-core-mediamtx-1 | 0.01 (0.01 to 0.02) | 25 (25 to 30) |
| camera-cloud-core-mosquitto-1 | 0.02 (0.02 to 0.03) | 10 (9 to 10) |
| camera-cloud-core-postgres-1 | 0.05 (0.04 to 0.06) | 56 (55 to 56) |
| camera-cloud-core-valkey-1 | 0.04 (0.03 to 0.04) | 26 (21 to 30) |
| camera-cloud-core-writer-1 | 0.18 (0.17 to 0.19) | 58 (47 to 60) |

The whole host (all its logical processors) while all users were running: mean 8 (8 to 11) %, highest sample 18 (13 to 22) %.

### PostgreSQL over a whole session (per second)

| Counter | Value |
|---|---|
| tuples inserted + updated | 360 (359 to 361) |
| committed transactions | 384 (383 to 384) |
