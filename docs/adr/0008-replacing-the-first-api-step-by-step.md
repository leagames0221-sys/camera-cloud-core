# 0008. Replacing the first API one kind of call at a time

## Status
Accepted (2026-10-25).

## Context
The later API (stamps, queue, limit per tenant, device registration) had to replace the first one without a day on which both the
old and the new behaviour were unknown. The earlier integration tests (52) are the definition of "still works" and are not changed.
The Azure Architecture Center's Strangler Fig pattern is the method: a facade in front sends each kind of request to the old or the
new implementation; kinds move one by one; the old one is removed when it has none left.

## Decision
- The **gateway** (already the only door from outside) got a table, `ROUTES` in `services/gateway/gateway/app.py`: for each kind of
  call (twelve at the time; the two calls for events were added later, 0011: me, tenants, members, camera list, register, get, delete, segment list, live, playback, camera token, API
  description) the name of the API that answers. The table is code, so every move is a commit and the history shows the order.
- Both APIs ran side by side from the same database. The first one ran from the **frozen image** (the release's image, given a
  second name; it was never built again). The later API's migration only **adds** (a table, and columns with a default), so the
  first API's inserts keep working.
- The kinds moved in this order, one commit each (the test runs between the moves were not recorded; after the last move the 52
  earlier tests passed, `docs/validation/acceptance.md`): me, tenants, members, camera list,
  camera get, segment list, live, playback, camera token, API description; then the writer started and, **in the same commit**,
  (a) the first API's background work was stopped (its broker address was set to a name that leads nowhere and its reconciliation
  interval to a day; its code was not changed), (b) MediaMTX's closed-segment call was sent to the writer, (c) registering and
  removing a camera moved, because the broker user of a camera is made by a background part of the first API. This is the point where
  writing twice could have happened, so it is one commit. The last commit removed the first API; the 52 tests passed after it.
- The table stays in the code after the first API is gone: it is how a third implementation or a second version of one kind would be
  introduced.

## Consequences
- During the move a restart of the gateway was needed after each change of the table (the source is mounted over the image in
  `compose.test.yaml`, so no image was built).
- A kind of call that has no entry in the table goes to the default (now the later API), so a call added later is not forgotten.
- Nothing here moves data: the database is shared. A later split of the database for a kind of call would be a different step.

## Alternatives not chosen
- **Replace everything at once.** The earlier tests could not then show which kind of call broke.
- **A feature flag inside the API.** The old code would have been changed, which is what the frozen image avoids.
