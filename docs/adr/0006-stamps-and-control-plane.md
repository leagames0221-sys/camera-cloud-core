# 0006. Stamps and a control plane

## Status
Accepted (2026-10-25). Follows 0002 (tenant model), which left "Row Level Security compared with a physical split" for a later version.

## Context
Everything that grows with the number of cameras (the video server, the broker, the state intake, the recording index and the
database that holds them) was a single copy. One copy has a ceiling, and a tenant who uses a lot can crowd out the others.
The Azure Architecture Center's Deployment Stamps pattern describes the usual answer: deploy independent copies of the
parts that scale, each serving a set number of tenants, and put a small part in front that knows which tenant is served by
which copy. The pattern's own text advises at least two stamps, because with one the code quietly assumes there is only one,
and warns that moving a tenant between stamps is hard.

Two things had to stay true: the earlier integration tests (52 of them) must pass unchanged, and a token for one company's
video must not open another company's video, also across stamps.

## Decision
- **A stamp is the set of parts whose size follows the number of cameras:** MediaMTX, Mosquitto, Valkey, the writer
  (0007) and the PostgreSQL database that holds state and the recording index.
- **The control plane exists once:** Keycloak, the gateway, the API, and its own database with partners, tenants, memberships,
  the camera ledger and the `stamps` table. The video signing key, the JWKS, the check of a camera's secret, the
  reconciliation against MediaMTX and the "recording" decision stay in the control plane; no stamp holds the signing key.
- **Stamp 1 is the first version's set-up unchanged,** and its database is the control database. Companies A and B stay on it.
  Reason: the earlier tests know one MQTT address and one RTSP address (their `conftest.py`). If A and B were on different
  stamps, a test that expects a refusal could pass because "no camera is on that address", and what the test means would be
  lost. **Stamp 2** has its own database, broker, queue, writer and video server, and serves company C. Separation between
  the stamps is tested in `tests/integration_v2`.
- **The `stamps` table** (control database) holds, for each stamp: the database address and the file with its password, the
  broker address and the file with its administration password, the Control API address of its MediaMTX, the addresses given to
  browsers and cameras (HLS, playback, RTSP, MQTT) and the audience of its video tokens. Its rows are written at every start from
  `infra/stamps.json`. Secrets are made by the `init` container, separately for each stamp, in a folder per service.
- **One place turns a tenant into a stamp:** `RoutedStore` (`services/api/app/stamps.py`). Calls about people, tenants and the
  camera ledger go to the control database; calls about state and the recording index go to the database of the tenant's stamp.
  The tenant id stays a required argument everywhere, as in 0002. A tenant never moves between stamps, so the answer is kept
  for the life of the process.
- **A copy of the camera ledger in each other stamp's database** (`cameras`: id, tenant, stamp, deleted) lets the stamp's writer check
  in the same statement that writes a state or a segment that the camera exists and is its own. The control plane writes the copy when a
  camera is registered or removed and again at every start. If a stamp cannot be reached when a camera is registered, the
  registration is refused with 502 and the ledger row is taken back; if it cannot be reached when a camera is removed, the next
  start repairs the copy.
- **One audience per stamp:** the video tokens for stamp 1 carry `mediamtx`, those for stamp 2 `mediamtx-s2`. Both MediaMTX
  servers read the same `/jwks`, so without different audiences a token made for a path on one stamp would be accepted by the
  other. The audience of a token is taken from the tenant's stamp. The writer of a stamp also checks that a camera is its own.
- **A new tenant's stamp** is the one with the fewest tenants (ties go to the lower number); the partner administrator may name a
  stamp when creating a tenant. (Counting tenants as the measure of a stamp's capacity is the pattern's: "a proxy metric, such as the number of tenants that you can deploy to the stamp". Choosing the one with the fewest, and giving the tie to the lower number, is the author's design decision; no model was found for it. It balances the number of tenants, not their load.) Naming one is how the load test (0010) puts its tenants on
  stamp 1 while stamp 2 is switched off.
- **Addresses of the second stamp** on the host: HLS 8889 and playback 9998 (the browser needs them; 9997 is left alone because an earlier test checks that
  nothing listens on it, the Control API's number), MQTT 1884 and RTSP 8555 only
  with `compose.test.yaml`. These port numbers are the author's design decision; no model was found for them. Each is the first stamp's number plus one (8888, 9996, 1883, 8554), except 9997, which is left alone for the reason above.

## Consequences
- The state of the world is in more places. A camera exists in the ledger and, for a later stamp, in its copy; the repair at start
  handles a lost update, but a camera whose removal could not be copied still has its state accepted until the next start.
- A stamp that is switched off does not stop the control plane from starting; it makes the registration of cameras for its tenants
  fail with 502 and their live and recording views empty.
- Moving a tenant to another stamp is not built (the pattern says why it is hard). The number of stamps is not changed
  automatically.
- Row Level Security is still not used (0002). The tenant layer stayed in one place in the code, and the physical split by stamp was
  done first because it also isolates the load of a tenant. A comparison of the two is left for a later version.
- The second stamp lives on the same machine, so it separates load and failure of containers, not of hardware.

## Alternatives not chosen
- **A split of every part for every tenant:** a small tenant would carry a database, a broker and a video server of its own; the number
  of parts would grow with the number of tenants, not of cameras.
- **No stamps, scale one copy up:** it does not show the routing, the per-stamp audience or the copy of the ledger, which are what
  a larger set-up needs and what the plan asks to be built and tested.
- **Putting A and B on different stamps:** it would have changed what the earlier tests prove (see Decision).

## Not built
- Moving a tenant between stamps; adding stamps automatically; more than one API (the limit per tenant of 0007 would move to Valkey).
- One administrator of two tenants choosing which one a camera registered by code is for (it is refused with 409, 0009).
- mTLS or a certificate to identify a camera (0003).
- Comparison with Row Level Security, which 0002 left for a later version. The tenant layer stayed in one place and the physical split by
  stamp was done first.
- Stopping a running camera at once through MediaMTX's Control API (a "kick" on camera removal), which 0003 left for a later version. A
  removed camera's secret stops working for new tokens, but a stream that is running keeps running until its connection ends.
- Load tests of the video itself (MediaMTX streaming): the load test measures only the parts written here (0010).
