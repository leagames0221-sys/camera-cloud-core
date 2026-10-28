# Load test: the conditions (written before the first measurement; not changed afterwards)

Date written: 2026-10-24. The same conditions are used for every version that is measured. The first measurement is the
stack of the first version; the second is the later version. If a number below has to change, the measurements already taken
are thrown away and taken again, and the change is written at the bottom of this file with its reason.

Every value marked **(chosen)** is not taken from a source; it was decided here and the reason is in ADR 0010.

## What is measured
The parts written in this repository: the API (and from the later version on, the writer and its queue), and the way the
three kinds of traffic reach the database. Not measured: the video itself (MediaMTX streaming), Keycloak, the browser.

Three paths, all started from inside the Compose network by one load-test container (so the host's port forwarding is not
part of the path):

| Path | Who | Rate | What |
|---|---|---|---|
| (a) Viewer API | 50 Locust users (`FastHttpUser`), 10 for each of the 5 load tenants L1 to L5 | 1 request per second per user, in turn: camera list of the tenant (60 cameras), one camera, the camera's segment list of the last 10 minutes | Through the gateway, with the access token of the realm user `l-viewer` (a viewer of L1 to L5). Tokens are fetched again after 200 s (they live 300 s) |
| (b) Closed-segment call | 20 Locust users | 5 calls per second per user (100 per second) | The request MediaMTX's hook sends: a form with `path`, `file`, `duration`, and the shared secret in a header. Target: the first version, the API's internal door; the later version, the writer of stamp 1 (same request). Start times never repeat |
| (c) Camera state | 300 users of Locust's own `locust.contrib.mqtt.MqttUser`, one per load camera | 1 message per second per camera (the simulated cameras send one every 30 s, so this is 30 times faster) | MQTT, QoS 1, retained, the topic and body of a real camera (`cameras/<id>/state`, `{"state":"online","fps":15,"bitrate":1800}`), with the camera's own client id, user name and secret |

Per-tenant request rate in (a) is 10 per second, below the later version's limit of 20 per second per tenant, so the
limit does not fire in this test.

## Load data
- Tenants `load-L1` to `load-L5`, 60 cameras each (300), plus `load-probe` with 1 camera that only the lag probe uses
  **(chosen)**: the probe's questions would otherwise use up the 20-per-second tenant limit of the later version in one
  of the load tenants and change that tenant's failure rate.
- Created through the public API before the first session by `loadtest/setup_load.py`: `partner-admin` creates the
  tenants and makes `l-admin` their tenant admin; `l-admin` registers the cameras and makes `l-viewer` a viewer of L1 to
  L5. The realm users `l-admin` and `l-viewer` have fixed ids (`infra/keycloak/ccc-realm.json`). The demo tenants A and B
  and the demo cameras are not touched.
- Removed after the last session: the cameras through the API; the rows of the load tenants through SQL (the API only
  marks cameras deleted and has no tenant removal).
- Before each session the load tenants' segment rows and state rows are deleted through SQL, so every session starts from
  the same table sizes **(chosen)**.

## Resource limits (the sum for the parts written here is equalised)
Set by `compose.load.yaml` with Compose's `cpus`; memory stays as in `compose.yaml`.

| Container | First version | Later version |
|---|---|---|
| API | 2.5 CPUs | 1.0 CPU |
| Writer of stamp 1 | not there | 1.0 CPU |
| Valkey of stamp 1 | not there | 0.5 CPU |
| **Sum of the parts written here** | **2.5 CPUs** | **2.5 CPUs** |
| PostgreSQL | 1.0 | 1.0 |
| Mosquitto | 0.5 | 0.5 |
| gateway | 1.0 | 1.0 |
| MediaMTX | 1.0 | 1.0 |
| Load-test container (Locust, probe) | 2.0 CPUs, 1 GiB | 2.0 CPUs, 1 GiB |

The values are **(chosen)**. Keycloak (1 GiB memory, no CPU limit), the three simulated cameras and the `init` container
are the same in both. During the measurement everything of stamp 2 (its MediaMTX, Mosquitto, Valkey, writer, database,
camera C1, the device-grant camera) is stopped, so that both versions run the same set of containers. The API's
reconciliation interval is set to 86400 s in both (`RECONCILE_INTERVAL`), so that the periodic check of recordings
against MediaMTX, which would walk all 300 cameras every 60 s, is not part of the comparison.

The load-test container's own CPU is recorded, to see whether the load generator was the limit.

## Sessions
- One session = 340 seconds: 370 users are started at 10 per second (37 s), then 300 s with all of them running **(chosen:
  "5 minutes" is read as the 5 minutes after the ramp)**. Locust's statistics are reset when the ramp is over
  (`--reset-stats`), so the request counts, percentiles and failure rates cover the 300 s only.
- Three sessions one after the other, with 20 s between them. The reported value is the median of the three, with the
  lowest and the highest in brackets.
- `locust --headless --processes 2 -u 370 -r 10 -t 340s --reset-stats --csv ... --csv-full-history`; the user classes
  have fixed counts (50, 20, 300). The flags are those of Locust 2.46.7, checked with `locust --help` in the built image.
- Each version is measured in one measurement round of three sessions. A round is repeated once only if the measuring
  tools themselves failed (the reason is then written in the result file). A bad number is never a reason to repeat.
- MQTT users of Locust's own client use MQTT 3.1.1 **(chosen)**: that client's callbacks for MQTT 5 do not fit the
  callback version it creates in paho-mqtt 2.1.0 (the disconnect callback takes one argument more). The broker and the
  intake are the same for either protocol version.

## What is recorded
- Per path: requests (or publishes) per second, the time to the answer at p50 and p95, and the failure rate. For MQTT the
  time is the time to the broker's acknowledgement (PUBACK); that is not the time until the database has the value.
- **The time until the API shows the change**, by a probe (`loadtest/probe.py`) that runs next to Locust, from the same
  container: every 10 s one segment call (then asking the API's segment list for the probe camera) and, 5 s later, one
  state message with a number never used before in `fps` (then asking the API for the camera until it shows that number).
  The time is measured from just before the call to the answer that shows it, and the questions are asked after 20 ms and
  then at growing gaps up to 250 ms, so the value is exact to within one gap. A change that is not visible after 30 s is
  a timeout and is counted. Probes that start before all users are running are left out.
- CPU and memory of every container every 10 s (`docker stats`), and the host's total CPU, for the 300 s with all users
  running.
- PostgreSQL's `tup_inserted + tup_updated` and `xact_commit` (`pg_stat_database`), read before and after each session and
  given per second over the session.

## The machine
Recorded at the start of each round (`environment.json`): the Docker CPU count and memory, the content of `.wslconfig`,
each container's image id and limits.

- Host: 32 logical processors, 31.4 GB memory, Windows 11 Pro (read from Windows by hand; the script does not record it).
- WSL2 (`.wslconfig`): `memory=16GB`, `processors=16`, `swap=8GB`. Docker reports 16 CPUs and 16.8 GB.
- Docker Desktop with the WSL2 backend. No other containers are running (checked with `docker ps` at the start). Other
  programs on the host are not stopped; the host's total CPU is recorded so that a busy host can be seen.

## Limits of this test
- One machine, one load generator, a CPU shared with other programs. The numbers compare the two versions
  under the same conditions; they do not say how many cameras one server could carry.
- The load is small compared with what a million cameras would do; `docs/capacity.md` does the sums.
- The path through the browser, Keycloak's login and MediaMTX are not measured.

## Later notes (2026-10-26; the conditions above are kept as they were written before the measurement)
- The simulated cameras now send their state every 60 seconds (Frigate's `stats_interval`, ADR 0005), not every 30. The load of path
  (c), one message a second per camera, was not changed by this, so it is now 60 times faster than life.
- The numbers of users, the rates, the CPU limits and the three sessions above are the author's design decisions; no model gives
  them (ADR 0010 says where it was looked). The length of a session (a
  ramp of 37 s, then 300 s of full load) is within k6's guide for an average-load test (5 to 60 minutes, a ramp of 5 to 15 % of the
  run, full load at least five times as long as the ramp; ADR 0010); the length itself is as written above.
- The API's reconciliation runs every 900 s since 2026-10-26 (ZoneMinder's audit interval, ADR 0005), not every 60 s; the setting of
  86400 s for the measurement above is unchanged.
- The limit per tenant of the later version is 100 calls a second since 2026-10-26 (ADR 0007), not 20. The load of path (a) is 10 a second
  for each load tenant, below either number, and the probe tenant is still kept apart. The writer's group is 125 entries or 50 ms and its
  look for unconfirmed entries every 30 seconds. The conditions were not changed; the later version was measured once more under them
  (`load_v2_rerun.md`).
