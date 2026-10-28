# Reading the code of two projects, and what came of it

Two open-source projects were read at the source, not only in their documentation, because the design depends on what they
really do. Lines are those of the upstream repository at the stated commit. Both are MIT-licensed; nothing was copied, the
facts below were used to decide (the licence texts are in `THIRD_PARTY_NOTICES.md`).

## MediaMTX v1.21.2 (MIT), commit `914e39535ece709919ac325aaeed98d394ed9093`
https://github.com/bluenviron/mediamtx

| What was looked up | Where | What it says | What was decided because of it |
|---|---|---|---|
| How a JWT is checked | `internal/auth/manager.go` 264-273 | The issuer and the audience are checked only if they are set in the configuration (`if m.JWTIssuer != ""`, `if m.JWTAudience != ""`); the token is parsed with `ParseWithClaims` and **no option makes `exp` required** | The audience is always set, so an empty value cannot turn the check off (0003). The API always puts `exp`, `iat`, `iss` and `aud` in every video token and a unit test checks it. Each stamp has its own audience (0006): without it a token for one stamp would open the other, since both read the same keys |
| How long the keys are kept | `internal/auth/manager.go` 26 (`jwksRefreshPeriod = 60 * 60 * time.Second`) and 291 | The key set is fetched again only after an hour | The signing key is made once and kept (0003); a replacement would have to be listed in the key set first and then `POST /v3/auth/jwks/refresh` called (`internal/api/api.go` 111) |
| Where a token may be sent | `internal/protocols/httpp/credentials.go` 15-24 | A JWT is read from `Authorization: Bearer` (and `user:pass` if it has a colon) | Playback uses a `fetch` with the header and plays the answer as a blob (0004) |
| Live over HTTP | `internal/servers/hls/http_server.go` 250-271, `internal/servers/hls/session.go` 28 | The first request is answered with a redirect that adds `cookieCheck=1` (on plain HTTP the mark is put in the address, since the cookie needs HTTPS); a session ends after 30 seconds without a request | The HLS player (hls.js) has to send the header on the redirect too; what MediaMTX answers to the requests a browser sends was checked before building (`docs/validation/poc.md`); live playback was not watched in a browser (0004) |
| Listing recordings | `internal/playback/on_list.go` 109-125 (`concatenateSegments`) | The list joins segments that follow each other into one entry | The recording index is **not** made from the list: it would show one long entry instead of the segments. The check against MediaMTX uses `GET /v3/recordings/get/<path>` (`internal/api/api.go` 241) instead (0005) |
| What the segment-complete command gets | `mediamtx.yml` 863-871 | `MTX_PATH`, `MTX_SEGMENT_PATH`, `MTX_SEGMENT_DURATION`; **the start time is not passed** | The start time is read from the file name; the recording path pattern is fixed so that it can be (0005). The closed-segment call goes to the writer of the stamp (0007) |

## Frigate v0.18.2 (MIT), commit `cfa08c56b23d23280c3d0a218e5a43182c97445b`
https://github.com/blakeblackshear/frigate

| What was looked up | Where | What it says | What was decided because of it |
|---|---|---|---|
| A broken segment is thrown away | `frigate/record/maintainer.py` 370 (`if 0 < duration < MAX_SEGMENT_DURATION:`), and 335 (`validate_and_move_segment`) | A segment is kept only if the length read from the file is above 0 and below the maximum; otherwise it is reported as invalid and dropped | The writer only puts a closed segment on the queue if its length is above 0 and not above 120 seconds, and counts the others (0007) |
| Rows are inserted in groups | `frigate/record/maintainer.py` 312 (`INSERT_MANY_RECORDINGS`) | The maintainer collects the recordings of one round and sends them to be inserted together | The writer writes a group of up to 125 entries in one statement (0007) |
| Old rows are deleted in groups | `frigate/record/cleanup.py` 212-216 and 319-323 (`max_deletes = 100000`, a loop in slices) | Deletions are done in slices of 100 000 ids so one statement does not become unbounded | Not needed yet (the index is not trimmed by this version; segments older than a day are marked deleted by the reconciliation). Noted for when expiry is built |

## Keycloak 26.8.1 (Apache-2.0)
The names of the client attributes for the consent page (`display.on.consent.screen`, `consent.screen.text`) were read in
`server-spi/src/main/java/org/keycloak/models/ClientScopeModel.java` at tag 26.8.1, where they are defined, and then confirmed by
walking the real consent page in `tests/integration_v2/test_04_device_grant.py` (0009).
