# 0010. How the two versions were measured, and what came out

## Status
Accepted (2026-10-25).

## Context
0007 says the writer and the queue change how state and segments reach the database. That claim needs numbers from the first version and
the later version under the same conditions, including the numbers that get worse. The video itself (MediaMTX streaming) is not what this
repository wrote, so it is not measured.

## Decision (the conditions are in `docs/validation/load_conditions.md`, written and committed before the first measurement)
- **Three paths,** all driven from inside the Compose network by one load-test container (Locust 2.46.7, 2 CPUs):
  (a) 50 viewers reading the camera list, one camera and the segment list through the gateway, 1 request a second each, 10 for each of five load
  tenants; (b) 20 users making the closed-segment call that MediaMTX's hook makes, 5 a second each (100 a second); (c) 300 cameras publishing
  their state over MQTT, one message a second each (the simulated cameras sent one every 30 seconds when this was measured, so this was
  30 times faster than life; since 2026-10-26 they send one every 60 seconds, 0005).
- **The sum of the CPU limits of the parts written here is equal:** the first version's API 2.5 CPUs; the later version's API 1.0 + writer 1.0 +
  Valkey 0.5. PostgreSQL, Mosquitto, the gateway and MediaMTX have the same limits in both. For the split 1.0 / 1.0 / 0.5, the sum 2.5,
the size of the load (50, 20 and 300 users) and the three sessions are the author's design decisions; no model gives them (looked at: the Locust and k6 documentation, which explain how to set users and rates,
not which numbers to use; k6's guide says the number of users comes from production, "Know the specific number of users and the typical
throughput per process in the system", which this repository does not have). The only reason given is that the sums are equal; nothing
was derived from a measurement. The length of a session is within k6's guide for an average-load test, the test it names to "Assure
that the system still meets the performance standards after system changes (code and infrastructure)": its table gives "Mid (5-60
minutes)", the ramp-up "usually lasts between 5% and 15% of the total test duration" and the full load should last "at least five
times longer than the ramp-up"; here 300 s of full load after a ramp of 37 s (11 % of 340 s; 300 s is more than five times 37 s). The
length was fixed in the conditions before the measurement and was not changed for this comparison.
- **Same background work:** the API's reconciliation (which would walk all 300 cameras every minute, its interval then; every 15 minutes
  since 2026-10-26, 0005) is set to once a day in both; the second
  stamp is switched off in the later round, so both rounds run the same containers except the writer and the queue.
- **One round of three sessions each,** 300 seconds with all 370 users running after a ramp of 37 seconds. A round is repeated only if the tools
  failed. Median with the lowest and highest.
- **Time until the change is visible,** not only the time to the answer: a probe makes one segment call and, 5 seconds later, one state change
  every 10 seconds on a camera of its own and asks the API until it shows it. Without this, the later version's quick answer to the call
  (it only has to reach the queue) would be compared with the first version's slow answer (it waits for the database), which is not the same
  thing.
- **Also recorded:** every container's CPU and memory every 10 seconds, PostgreSQL's inserted and updated tuples and its commits, the host's CPU,
  the machine's settings.
- The numbers are copied to `docs/validation/load_v1/` and `load_v2/`; the tables are in `load_v1.md`, `load_v2.md` and `load_compare.md`.

## What came out (details in `docs/validation/load_compare.md`)
| | First version | Later version |
|---|---|---|
| PostgreSQL commits a second (about the same rows written: 371 and 360 a second) | 1857 | 384 |
| PostgreSQL CPU | 0.22 | 0.05 |
| Closed-segment call, p95 | 44 ms | 2 ms |
| Viewer calls, p50 | 70 ms | about 40 ms |
| Until a segment shows in the API, p50 / p95 | 29 / 30 ms | 563 / 1077 ms |
| Until a state change shows in the API, p50 / p95 | 209 / 346 ms | 560 / 1072 ms |
| CPU of the parts written here (limit 2.5 in both) | 0.40 | 0.34 |
| Failures | 0 | 0 |

**Why** (marked "likely" where it was not tested): the commits fall because a group of up to 500 entries is one statement (the queue's design,
0007). The longer delay is the other side of the same choice: an entry waits for its group, up to one second. The quicker answer to the call
is that it only reaches the queue. The viewer calls are probably faster because the API no longer spends its time on 400 writes a second.

**What the test does not show:** which version has the higher ceiling (neither was near its limit: the CPU used was 0.4 and 0.34 of 2.5), the
cost of a running second stamp (it was off), anything about video, Keycloak or a browser, anything about a different machine. It must not be
read as "N cameras" (`docs/capacity.md` does the sums, with its assumptions marked).

## Things found along the way
- A trial session of the later version showed that the camera list waited 3 seconds for the video server of a stopped stamp, because the
  API asked every stamp. It now asks the tenant's own stamp only, with a unit test.
- Locust's MQTT user for MQTT 5 has callbacks that do not fit the callback version of the paho-mqtt it pairs with (the disconnect callback
  takes one argument more), so the 300 cameras of the test use MQTT 3.1.1. The broker and the intake are the same for either.
- A load generator and a system under test on the same machine share CPU; the host's mean CPU (14 % and 8 %) and the generator's own CPU
  (0.11 of 2.0) are recorded to show that it did not run out first.

## Consequences
- The measured differences are small in absolute terms because the load was small; they show the direction of each effect, not its size at
  scale. A heavier round would be the next step, and `loadtest/measure.py` takes the conditions as they are.
- The three sessions of a round took about 18 minutes (the times in `run.json`), plus the set-up; the machine should be left alone meanwhile.

## Alternatives not chosen
- **Measuring the video path (MediaMTX):** it is not this project's code.
- **A harder load to find the ceiling:** the plan fixes one set of conditions for both versions, and finding the ceiling would have meant
  changing them between rounds.
- **Reading only the answer time of the closed-segment call:** see "Time until the change is visible" above.

## Update (2026-10-26): the later version measured again
After the writer's group became 125 entries or 50 ms, the look for unconfirmed entries every 30 seconds and the limit per tenant 100 calls a
second (0007), the round of the later version was run once more under the same conditions: `docs/validation/load_v2_rerun.md`. A segment or
a state change shows in the API after about 70 ms at the 95th percentile (it was about 1.07 seconds with groups of 500 entries or one
second); the writer used 0.21 CPU (0.18) and PostgreSQL committed 405 transactions a second (384); nothing failed and no probe timed out.
The sentence above, that the commits fall because a group of up to 500 entries is one statement, describes the first round.
