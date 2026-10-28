# 0007. A queue and batch writes for state and segments, and a limit per tenant

## Status
Accepted (2026-10-25).

## Context
In the first version every MQTT state message and every closed-segment call from MediaMTX was written to PostgreSQL by the
API at the moment it arrived: one transaction each, on the same process that answers the viewers. The load grows with the number
of cameras (a state message every 60 seconds, every 30 seconds until 2026-10-26, and a segment every 60 seconds from each), while the number of viewers does not.
A burst (all cameras reconnecting after a broker restart) goes straight into the database.

The Azure Architecture Center describes the answer in three patterns used here: Queue-Based Load Leveling (put a queue between
the source and the part that writes, so the writer takes work at its own pace), Competing Consumers (several readers of one queue)
and Throttling (limit what one caller may use). The Valkey documentation describes the tool: streams with consumer groups,
`XREADGROUP`, `XACK`, `XPENDING`, `XAUTOCLAIM`, `XADD ... MAXLEN ~`.

## Decision
**The path.** The writer of a stamp receives MQTT state messages (it is the subscriber that the API was) and MediaMTX's
closed-segment calls on its internal door. It puts each on that stamp's Valkey stream with `XADD` and answers at once. A reader in
the same process takes entries with `XREADGROUP` in groups of at most **125 entries or 50 milliseconds** after the first (whichever
is first), writes the group to PostgreSQL in **one statement**, and sends `XACK` **after the statement has committed**.
The two numbers are the defaults of Logstash for the same job, a worker that collects events from its input and hands them on in a
batch (Logstash 8.15, `logstash.yml` settings): `pipeline.batch.size`, "The maximum number of events an individual worker thread will
collect from inputs before attempting to execute its filters and outputs.", default 125; `pipeline.batch.delay`, "how long in
milliseconds to wait for each event before dispatching an undersized batch to pipeline workers", default 50. Logstash says the
delay is waited for each event; here the group is closed 50 ms after its first entry (`read_loop` in `intake.py`), so the 50 ms is Logstash's number used for a different wait. Until 2026-10-26
the numbers were 500 and one second, with no model; 0010 measured those and measures again with the new ones. Looked at and not
taken: the Valkey documentation of `XREADGROUP`, whose consumer example reads with `BLOCK 2000 COUNT 10` (an example of the
command, not a recommended size), and Azure Functions' queue trigger, whose `batchSize` of 16 is how many messages are handled at
the same time, not how many rows one statement writes. The wait of 50 ms is the price of batching and shows in the measured time
until a change appears in the API (0010).

**Duplicates and order.**
- One `INSERT ... ON CONFLICT DO UPDATE` cannot touch the same row twice (PostgreSQL 17, INSERT, the `ON CONFLICT` clause), so a group is reduced to the newest entry per camera
  before it is written. A segment is written with `ON CONFLICT DO NOTHING` on (camera, start), so writing it twice gives one row.
- The MQTT message has no time in it. The order of arrival is the millisecond part of the entry id that Valkey gives. The upsert
  only replaces a row with data that is not older (`WHERE camera_status.seen_at <= EXCLUDED.seen_at`).
- Whether the camera exists, is not deleted and belongs to this stamp is checked by a join in the same statement; what does not
  match is not written.

**Entries that are not confirmed.** Every 30 seconds the reader takes over entries that were handed out and not confirmed for 30
seconds (`XAUTOCLAIM`) and writes them again; a failed write leaves its entries unconfirmed, so they come back this way. An entry that
has been handed out more than 5 times (the sixth time) is copied to the stream `ccc:dead` and confirmed, so one entry that can
never be written does not stay in the way; it is counted. Writing again is safe because of the rules above. (30 seconds is the
default visibility timeout of Azure Queue Storage, Get Messages: "The default value is 30 seconds." 5 times is the default
`maxDequeueCount` of Azure Functions' queue trigger: "The number of times to try processing a message before moving it to the poison
queue.") The interval of 30 seconds between two looks is BullMQ's `stalledInterval`, "Number of milliseconds between stallness
checks.", `@defaultValue 30000` (BullMQ `WorkerOptions`, `src/interfaces/worker-options.ts`); BullMQ's guide on stalled jobs (`https://docs.bullmq.io/guide/workers/stalled-jobs`) says what its check does: "A stalled job is moved back to the waiting status and will be processed again by another worker, or if it has reached its maximum number of stalls, it will be moved to the failed set." The takeover here does the same with an
entry nobody confirmed; the copy to `ccc:dead` after 5 times is the counterpart of "the failed set". So an entry that is not confirmed is taken between 30 and 60 seconds after it was handed out (30 seconds
idle, then the next look). Until 2026-10-26 the interval was 5 seconds, with no model.)

**What is checked at the door.** A closed segment is accepted only if its length is above 0 and not above 120 seconds (twice the
segment length; the cap is the author's decision. Moonfire NVR's `design/time.md` has the words "an upper bound of two minutes duration for the
initial recording" only inside a calculation of clock error ("At the design limit of 500 *ppm* camera frequency error ... this causes
a maximum of 60 milliseconds of error"); it is not a rule for accepting a recording, and it is named here only as a number of the same size), its file name gives a start time, and its path is `<tenant>/<camera>`. The rule follows Frigate's: its recording
maintainer only keeps a segment whose duration is above 0 and below the maximum, and discards the rest (Frigate also discards a
segment equal to its maximum; here 120 s itself is accepted). Rejections are counted
(`/internal/v1/counters`, not published). The camera is checked in the statement that writes the group: a segment of a camera
that is unknown, deleted or another stamp's is not indexed and is counted as `segment_not_this_stamp`. The writer's doors are
on the compose network that the cameras are on too (MediaMTX calls the writer there), so both, the closed-segment door and the
counters, ask for the stamp's closed-segment secret. The API's own door for closed segments (the first version's) checks the
first stamp's secret and therefore refuses a camera of any other stamp (404).

**The queue's settings.** Valkey 9.1.2 with `appendonly yes`, `maxmemory 256mb`, `maxmemory-policy noeviction` (Valkey's
default: `valkey.conf` of 9.1.2, "The default is: maxmemory-policy noeviction"), streams cut at about 100 000 entries (`MAXLEN ~`).
The 256 MB and the 100 000 are the author's design decisions; no model was found for either (looked at: the shipped `valkey.conf`
leaves `maxmemory` unset, and the Valkey documentation of `XADD` shows `MAXLEN ~ 1000` only as an example). The queue is on a network of its own with its writer, so no camera reaches it; the
default account is off and the one account (`writer`) may only use stream commands on keys `ccc:*`, with its password in the ACL
file only as a SHA-256 hash. A segment entry that the length limit of the stream cuts off is not lost for good: the
reconciliation of the control plane (0005) finds it from MediaMTX's list of recordings. (Update: with the profile `vision` the
network also holds the workers `motion` and `classify`, each with an account of its own (0013); the writer's account may also use the
few commands the client sends when it connects.)

**A limit per tenant.** In the API, after the caller has been shown to belong to the tenant and to be allowed the operation: at most
**100 calls** in any second for one tenant, otherwise 429 with `Retry-After`. It is counted there, not in the gateway, because the
gateway only sees the tenant id in the address: anyone who knows the id of another company could use up that company's calls. The count is in
the memory of the one API process; with several, it would have to move to Valkey. The 100 a second is
Stripe's global limit in production, which is counted per account: the page "Rate limits" of Stripe's API documentation lists
"本番環境 : 1 秒あたり 100 リクエスト" (read in its Japanese version) under "グローバルな API レート制限", and says that rate limits are
usually measured as requests per second for each Stripe account. A tenant here is the company that is served, as an account is
there. Until 2026-10-26 the limit was 20 a second, with no model (looked at: the Azure Architecture Center's Rate Limiting pattern,
whose figures illustrate the kinds of measure a limit can use, operations, amount of data and cost, and are not a recommended
limit). The integration test `tests/integration_v2/test_03_limit.py` checks 100.

## Consequences
- The API no longer holds the write load of state and segments; the writer can be stopped and started without losing what is on
  the queue. A state message received by the writer but not yet on the queue (a crash in between) is lost, which is acceptable for a
  state that is sent again after at most 60 seconds; a segment is made up by the reconciliation.
- A state shows in the API a little later than in the first version, and a segment too: the group is closed 50 ms after its first
  entry and then written: about 70 ms at the 95th percentile in the load test of 2026-10-26 (it was about 1.07 seconds with groups of 500
  entries or one second). The first version showed a segment in about 30 ms. The comparison is in 0010.
- Another moving part (Valkey) and a second copy of the data path to monitor; the dead stream is the place to look.
- Only one reader per stamp is built. The pattern allows several (competing consumers); the group and the claiming already
  work for that.

## Alternatives not chosen
- **Writing at once, as in the first version.** It is the baseline that is measured against.
- **Kafka or RabbitMQ.** The author's assumption is that they are heavier parts for the laptop this must run on; their memory was not measured and no source was read for it.
- **A Valkey list (LPUSH / BRPOP).** The Valkey page on streams (`https://valkey.io/topics/streams-intro/`) says: "when using blocking lists, when a message is received by the client it is popped (effectively removed) from the list", and "Streams Consumer Groups provide a level of control that Pub/Sub or blocking lists cannot achieve, with different groups for the same stream, explicit acknowledgment of processed items, ability to inspect the pending items, claiming of unprocessed messages". From these the author reads that with a plain list a crash between reading and writing loses the entries and nothing can take them over; a stream with a consumer group has both. Other list commands that move an entry instead of removing it were not examined.
- **Limiting in the gateway by the tenant id in the address.** See above.

## Update (2026-10-26): four values taken from models
The group of 500 entries or one second became 125 entries or 50 ms (Logstash's defaults), the look for unconfirmed entries every 5
seconds became every 30 (BullMQ's `stalledInterval`), the limit per tenant of 20 calls a second became 100 (Stripe's global limit).
The same interval of 30 seconds is in the two vision workers (`services/vision/vision/common.py`). The sources are in
`docs/sources.md`; the load test was run again with them (0010).
