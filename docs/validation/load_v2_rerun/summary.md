### Locust results (3 sessions; median, then the lowest to highest in brackets)

| Path | Requests per second | p50 (ms) | p95 (ms) | Failures (%) |
|---|---|---|---|---|
| GET /tenants/{t}/cameras | 16.7 (16.7 to 16.7) | 43 (40 to 52) | 85 (82 to 99) | 0.00 (0.00 to 0.00) |
| GET /tenants/{t}/cameras/{c} | 16.7 (16.7 to 16.7) | 43 (39 to 52) | 84 (83 to 100) | 0.00 (0.00 to 0.00) |
| GET /tenants/{t}/cameras/{c}/segments | 16.7 (16.7 to 16.7) | 43 (40 to 51) | 84 (82 to 99) | 0.00 (0.00 to 0.00) |
| POST segment call | 100.0 (100.0 to 100.0) | 1 (1 to 1) | 2 (2 to 3) | 0.00 (0.00 to 0.00) |
| MQTT connect | 0.0 (0.0 to 0.0) | 0 (0 to 0) | 0 (0 to 0) | 0.00 (0.00 to 0.00) |
| MQTT publish:1:cameras/+/state | 299.6 (299.3 to 299.6) | 1 (1 to 1) | 2 (2 to 4) | 0.00 (0.00 to 0.00) |
| TOTAL | 449.6 (449.4 to 449.7) | 1 (1 to 1) | 45 (42 to 57) | 0.00 (0.00 to 0.00) |

### Time until the API shows the change (probe, ms)

| Probe | Probes per session | Timeouts per session | p50 | p95 | Slowest |
|---|---|---|---|---|---|
| segment | 30 (30 to 30) | 0 (0 to 0) | 68 (66 to 68) | 70 (68 to 71) | 70 (69 to 71) |
| state | 30 (30 to 30) | 0 (0 to 0) | 65 (64 to 66) | 68 (66 to 123) | 69 (66 to 133) |

### Containers while all users were running (mean CPU in CPUs, highest memory in MiB)

| Container | CPU | Memory |
|---|---|---|
| camera-cloud-core-api-1 | 0.13 (0.12 to 0.14) | 68 (58 to 71) |
| camera-cloud-core-cam-a1-1 | 0.00 (0.00 to 0.00) | 30 (30 to 30) |
| camera-cloud-core-cam-a2-1 | 0.00 (0.00 to 0.00) | 30 (30 to 30) |
| camera-cloud-core-cam-b1-1 | 0.00 (0.00 to 0.00) | 30 (30 to 30) |
| camera-cloud-core-gateway-1 | 0.12 (0.12 to 0.14) | 55 (45 to 58) |
| camera-cloud-core-keycloak-1 | 0.00 (0.00 to 0.00) | 826 (826 to 827) |
| camera-cloud-core-locust-1 | 0.12 (0.11 to 0.12) | 112 (103 to 115) |
| camera-cloud-core-mediamtx-1 | 0.01 (0.01 to 0.02) | 24 (24 to 29) |
| camera-cloud-core-mosquitto-1 | 0.02 (0.02 to 0.03) | 8 (8 to 9) |
| camera-cloud-core-postgres-1 | 0.06 (0.05 to 0.07) | 57 (56 to 58) |
| camera-cloud-core-valkey-1 | 0.04 (0.04 to 0.05) | 48 (45 to 54) |
| camera-cloud-core-writer-1 | 0.21 (0.21 to 0.21) | 58 (49 to 60) |

The whole host (all its logical processors) while all users were running: mean 14 (11 to 18) %, highest sample 37 (28 to 43) %.

### PostgreSQL over a whole session (per second)

| Counter | Value |
|---|---|
| tuples inserted + updated | 371 (371 to 372) |
| committed transactions | 405 (400 to 414) |

