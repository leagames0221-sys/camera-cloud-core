# The memory limits of the containers, read again (2026-10-26)

**Rule** (ADR 0012): where the product publishes a rule for its own memory (Keycloak), that rule gives the limit; for PostgreSQL the limit is the author's decision, see below. For the others the limit is the
highest memory the kernel counted for the container in a whole run of the integration tests, plus 15 %. The 15 % is the margin of the Vertical Pod Autoscaler
of Kubernetes: its flag `recommendation-margin-fraction` (default 0.15) is described as "Fraction of usage added as the safety margin to
the recommended request" (VPA `docs/flags.md`). The VPA adds the margin to a percentile of the usage (its `target-memory-percentile` is
0.9); here the highest reading is used (the author's decision).

## The two rules of the products
- **PostgreSQL 17**, "Resource Consumption" (https://www.postgresql.org/docs/17/runtime-config-resource.html): of `shared_buffers`, "The
  default is typically 128 megabytes (128MB), but might be less if your kernel settings will not support it", and "If you have a
  dedicated database server with 1GB or more of RAM, a reasonable starting value for shared_buffers is 25% of the memory in your system."
  `compose.yaml` does not set `shared_buffers`, and `SHOW shared_buffers` in both containers says `128MB`. The page's 25 % is a starting value for `shared_buffers`, for a dedicated database server with 1GB or more of RAM, taken from the memory of
  the system; it says nothing about how large a container must be, and a container of 512 MiB is below the 1GB of its condition. Reading the
  default 128 MB as 25 % and so setting the limit to 512 MiB for both databases is the author's decision, not a rule of the page. PostgreSQL also needs memory beyond `shared_buffers`. The buffers fill with use, so a limit set from a reading of a small database would be too low once they had.
- **Keycloak**, "Concepts for sizing CPU and memory resources"
  (https://www.keycloak.org/high-availability/multi-cluster/concepts-memory-and-cpu-sizing): "The base memory usage for a Pod including
  caches of Realm data and 10,000 cached sessions is 1250 MB of RAM. In containers, Keycloak allocates 70% of the memory limit for
  heap-based memory. It will also use approximately 300 MB of non-heap-based memory." and, for the limit, "subtract the non-heap memory
  from the value above and divide the result by 0.7"; its example gives "Memory limit per Pod: 1360 MB". (1250 - 300) / 0.7 = 1357, so the limit is **1360 MiB**.
  The page's example is for a cluster of Keycloak pods; its words are used for the single container here.

## What was read for the others
The kernel's own `memory.peak` of each container (`cat /sys/fs/cgroup/memory.peak` inside it; it is the highest value since the container
was made, and counts the file cache as the kernel does), read after the three sets of integration tests (`tests/integration` 52,
`tests/integration_v2` 42, `tests/integration_v3` 11) had run once on containers made new for that run, with limits wide enough that
none was near them (`memory/cgroup_memory_peak_fresh_containers_second_lap.csv`). A first reading of the same kind, of the containers made
new for the earlier run (`memory/cgroup_memory_peak_after_the_runs.csv`, where the first number of each row is `memory.peak` in bytes), was
higher for the writer, the second writer, the gateway, the classify worker and the motion worker, and the higher of the two is the reading.
`memory/cgroup_readings_and_limits.json` has both numbers and the result for every container.

(`memory/highest_and_margin.py` is the small program that took the highest `docker stats` value of each container in that attempt.)
The first attempt of the day (kept in `memory/docker_stats_integration_runs.csv`, `docker_stats_final_limits.csv`,
`camera_start_docker_stats.csv` and the load test's `containers.csv`) used `docker stats`, which leaves the file cache out. With limits worked
out from it the cameras were killed when made new, and the second writer used 65.0 of 66 MiB. Those limits are replaced; the readings stay as
the record of why.

## The limits

| Container | Limit before (MiB) | Reading (kernel's `memory.peak`, MiB) | Rule | Limit now (MiB) | Peak in the last run on these limits (MiB) |
|---|---|---|---|---|---|
| API | 256 | 65.32 | reading x 1.15 = 75.1 | 76 | 65.7 |
| writer, stamp 1 | 256 | 79.36 (first reading; 67.78 in the second) | x 1.15 = 91.3 | 92 | 67.6 |
| writer, stamp 2 | 256 | 73.39 (first reading; 58.79 in the second) | x 1.15 = 84.4 | 85 | 73.2 |
| gateway | 128 | 55.32 | x 1.15 = 63.6 | 64 | 55.3 |
| PostgreSQL, stamp 1 | 256 | 51.24 | the author's decision: `shared_buffers` 128MB read as 25 % of 512 (see above) | **512** | 56.6 |
| PostgreSQL, stamp 2 | 256 | 36.03 | the same | **512** | 37.2 |
| Mosquitto, stamp 1 | 128 | 10.38 | x 1.15 = 11.9 | 12 | 10.2 |
| Mosquitto, stamp 2 | 128 | 10.00 | x 1.15 = 11.5 | 12 | 9.7 |
| MediaMTX, stamp 1 | 512 | 94.38 | x 1.15 = 108.5 | 109 | 90.8 |
| MediaMTX, stamp 2 | 512 | 38.89 | x 1.15 = 44.7 | 45 | 38.6 |
| Keycloak | 1024 | 963.16 | Keycloak: (1250 - 300) / 0.7 | **1360** | 963.2 |
| motion worker | 384 | 91.95 | x 1.15 = 105.7 | 106 | 92.2 |
| classify worker | 3072 | 1411.51 | x 1.15 = 1623.2 | 1624 | 1408.3 |
| the simulated cameras (one limit for all five) | 256 | 84.62 (the highest of the five; 34.9 to 35.1 for the two that were only restarted) | x 1.15 = 97.3 | 98 | 83.9 to 84.5 (35.1 and 34.9) |
| Valkey, both stamps | 320 | 99.84 and 16.31 | **not changed**, see below | 320 | 99.8 and 16.3 |
| `init` (a one-shot job) | 128 | not read | not changed | 128 | |

The load-test container (`compose.load.yaml`, 1 GiB) is not part of the product and its limit is a condition of the test
(`load_conditions.md`); it is not changed. No container was killed in the last run (`oom_kill` in each container's `memory.events` is 0 for all
of them, `docker inspect` says `OOMKilled` false) and 105 tests passed on these limits (`memory/cgroup_memory_peak_last_run.csv`).

## Two containers that follow neither rule
- **Valkey.** The queue is told to hold at most 256 MB (`--maxmemory 256mb`, `noeviction`, 0007), and that has to fit under the limit of the
  container: with 115 MiB (99.84 x 1.15) a queue that grew would be killed instead of refusing new entries. The limit stays 320 MiB.
- **`init`** runs once and ends; it was not read, and its limit stays.
## What can still be said against the numbers
The readings are from one run of the tests (two for some); a longer or heavier load may use more, and 15 % is thin for a worker whose memory
depends on the data (the second writer read 58.79 MiB in one run and 73.39 in another). The load test of 2026-10-26 was run on the limits of
the commit before and was not run again on these.
