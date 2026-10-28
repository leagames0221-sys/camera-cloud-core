# 0004. How live and past video reach the browser

## Status
Accepted (2026-10-24). The HTTP behaviour below was checked against MediaMTX 1.21.2 (`docs/validation/poc.md`); the playback in a real browser is still to be confirmed.

## Context
The browser has to show live video and the last five minutes of recording, for one tenant's cameras only, without the video passing through our API (which would not scale with viewers). MediaMTX serves live video as HLS and recordings from its playback server (`/get` returns fragmented MP4, [playback documentation](https://mediamtx.org/docs/features/playback)). Both read a bearer token from the `Authorization` header ([MediaMTX authentication documentation](https://mediamtx.org/docs/features/authentication)); MediaMTX does not accept a token in the query of an HTTP URL unless asked to (`authJWTInHTTPQuery`, MediaMTX 1.21.2 `internal/auth/manager.go`).

## Decision
- **Live:** the page asks the API (`POST .../cameras/{id}/live`); the API checks the user's role and tenant (`404` for another company's camera), then returns a `read` token for that one path and the HLS URL. The page plays it with hls.js and adds `Authorization: Bearer <token>` to every request through `xhrSetup`. It asks for a new token 30 seconds before the five-minute life of the token ends, that is every 4 minutes 30 seconds, and uses the newest one for later requests (30 seconds is the author's choice. Keycloak's JavaScript adapter guide uses `updateToken(30)` in an example call and says "If the token expires within minValidity seconds (minValidity is optional, if not specified 5 is used) the token is refreshed"; the example is not a recommendation of 30. Until 2026-10-26 the page asked every four minutes, a value without a source).
- **Past five minutes:** the page asks the API (`POST .../cameras/{id}/playback`). The API looks at its recording index, picks a start time (the last five minutes, but not before the first recorded segment), and returns a `playback` token for that one path with the ready-made `/get?path=&start=&duration=` URL. The page fetches the URL with the header, makes a blob from the answer and puts it into the `<video>` element. If the default fMP4 does not play, adding `format=mp4` is the documented fallback; both returned `200 video/mp4` in the check.
- **CORS:** MediaMTX's `hlsAllowOrigins` and `playbackAllowOrigins` (default `*`) are set to the one origin of the viewing page, `http://localhost:8000`. The API is served from the same origin through the gateway and sets no CORS headers. Keycloak's web origin is the same single value.
- **What MediaMTX does on the first request** (observed): `GET .../index.m3u8` is answered with `302` to `...index.m3u8?cookieCheck=1` *before* any token check; the request after the redirect needs the Bearer header (without it `401`) and returns the multivariant playlist whose variant links carry a `session` query; later requests use the session. A preflight `OPTIONS` with `Access-Control-Request-Headers: authorization` is answered `204` with `Access-Control-Allow-Origin` for the page's origin only. Requests from other origins get no `Access-Control-Allow-Origin`.

## Consequences
- Video bytes go from MediaMTX to the browser directly; the API only issues five-minute tokens.
- The browser must keep the `Authorization` header across the `302`. hls.js adds it to every request, and the redirect target has the same origin as the first request. The Fetch Standard (`https://fetch.spec.whatwg.org/`, HTTP-redirect fetch) says: "If request's current URL's origin is not same origin with locationURL's origin, then for each headerName of CORS non-wildcard request-header name, delete headerName from request's header list." and "A CORS non-wildcard request-header name is a header name that is a byte-case-insensitive match for `Authorization`." So the header is removed only when the origin changes, and here it does not. A real-browser check is open.
- At the start of a fresh stream the playlist can list `#EXT-X-GAP` entries named `gap.mp4`, which answer `401` when fetched. hls.js is expected to skip such entries; open until checked in a browser.
- A playback fetch holds the whole requested clip in memory as a blob (about 2 MB for three minutes of the demo video). That is fine for five minutes; longer clips would need a different approach.
- A token in the URL query is not used for HTTP. A browser address bar or log therefore never holds a video token.
- The page plays only through hls.js, which adds the header, and refuses to play when `Hls.isSupported()` is false. hls.js's README says: "Safari browsers (iOS, iPadOS, and macOS) have built-in HLS support through the plain video "tag" source URL." With the video tag the browser makes the requests itself and the page has no hook to add a header; that this is why the page does not use Safari's own HLS is the author's reading, no source was found that says so, and it was not tried in Safari.

## Alternatives not chosen
- **Relaying video through the API.** Simple to authorise, but every viewer's bandwidth would pass through one Python process.
- **Putting the token in the URL query for HTTP.** MediaMTX only accepts that when configured to; URLs end up in logs and history.

---

## Added: playing the video around an event (2026-10-25)

The route that plays "the last five minutes" cannot play an event, which can be anywhere in the last day. A second route does:
`POST /api/v1/tenants/{tenant}/cameras/{camera}/events/{event}/playback`. It answers like the first one (an address of the video
server's `/get`, a token for the action `playback` and the path of that one camera, valid five minutes, with the audience of the
tenant's stamp), for the part of the recording that **starts 5 seconds before the event and is as long as the event plus 10
seconds**, that is 5 seconds before and 5 seconds after: Frigate's defaults for the video kept around an event (`pre_capture` and
`post_capture`, Frigate 0.18.2 `frigate/config/camera/record.py`; its recording documentation: "Default: 5 seconds for both pre and
post capture."). An object event has no length, so it is 10 seconds. The start is moved forward to the first segment that is in the index when the recording does not reach back that far.
Who may call it is who may watch video (the partner administrator does not, as for the other routes of video); an event of another
tenant or of another camera is `404`, as everywhere. Events are kept longer than the recordings (MediaMTX deletes after a day); when
no segment of the index covers the part, the route answers `410` with `recording_deleted` and gives no token, and the list of events
marks such an event (`playable: false`) so that the screen does not offer it.

The list is `GET .../cameras/{camera}/events?from=&to=&limit=&offset=`, newest first. One answer holds at most `limit` events:
**100** unless the caller asks for another number, from 1 to 1000. `offset` skips that many events, so the next page is
`offset=100`, and a page with fewer than `limit` events is the last. There is no limit of time: `from` and `to` only cut the list
(`from` not before `to` is refused with 422). The part of the recording index that decides `playable` is read for the events of
the page only. The screen reads 100, shows a button "続きを読む" while the last page was full, and reads the pages shown so far
again every 10 seconds. Until 2026-10-26 the list was cut to the last 24 hours and to 500 events and had no paging.

*The models.* Frigate's list of events (`/api/events`, Frigate 0.18.2 `frigate/api/defs/query/events_query_parameters.py`) has
`limit: int | None = 100` and `offset: int | None = Field(0, ge=0)` and no limit of time. The list of fragments of Amazon Kinesis
Video Streams (`ListFragments`, API reference) says of `MaxResults`: "The default value is 100.", gives it a valid range of 1 to
1000, and returns a `NextToken` "to resume pagination". *The principle taken from them:* a list of recorded things is given in
pages of 100 and the caller asks for the next page. *What was decided from it (the author's design decision):* the default of 100
(both models) and the range of 1 to 1000 (the second model's) are taken over; the next page is asked for with `offset`, as in
Frigate's list, and not with a token as in the other. A token has to come back in the answer, and this route's answer is a list of
events (the third version); `offset` leaves the answer as it is and adds two optional query parameters (`docs/validation/openapi_diff.md`
keeps the output of oasdiff). What `offset` costs: an event that is written while someone reads, with a time earlier than the page's
first, moves the events below it one place down, so the screen shows each id once. The check of the vision version
(`tests/integration_v3/test_01_events.py`) reads two rounds of the check video, more than 100 events, page by page.

## Update (2026-10-25): what was seen in a browser
Playing an event's recording (the blob path) was watched in Microsoft Edge (`docs/validation/acceptance.md`). For `gap.mp4`,
hls.js 1.7.3 rejects a `GAP` entry before any request (read in its source, `docs/validation/poc.md`). No record is kept of live
playback watched in a browser, so the points above about the `302` and the header stay open.
