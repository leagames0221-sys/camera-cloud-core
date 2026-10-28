# Does the later API break anything the first release published? (2026-10-25)

Method: the description that the running service publishes (`GET /api/v1/openapi.json` through the gateway) was saved as
`docs/openapi/v2.json`; `docs/openapi/v1.json` is the description of the first release, kept as it was. oasdiff
1.33.0 (image `tufin/oasdiff:v1.33.0`, digest `sha256:6263a96dd2ef0726c54e21fea9b8e1607eac4841add0079324b424c1f52b819c`) compared them
from PowerShell, in the folder of the repository (oasdiff's output in the tables below was read from the screen and was not kept;
the integration test repeats the first comparison and is skipped when the image is missing):

```
docker run --rm -v ${PWD}\docs\openapi:/specs:ro tufin/oasdiff:v1.33.0 breaking /specs/v1.json /specs/v2.json --fail-on ERR
```

| Run | Exit code | Output |
|---|---|---|
| `breaking ... --fail-on ERR` | 0 | No breaking changes to report, but the specs are different. |
| `breaking ... --fail-on WARN` | 0 | The same: not even a warning. |
| `changelog` | 0 | 1 change, 0 errors, 0 warnings, 1 info: `endpoint-added` for `POST /api/v1/devices/enroll`. |

The only difference is the new endpoint for registering a camera with the device authorization grant. A 429 answer (the
limit per tenant) and the other new answers are not in the description, so they do not appear as changes; clients of the first
release already have to handle an error status on every call.

The integration test `tests/integration_v2/test_06_openapi.py` repeats both runs and checks that the running service
publishes the saved file.

---

# Again, after the routes for events were added (2026-10-25)

`docs/openapi/v2.json` is the current contract (it is written from the code by `scripts/export_openapi.py`, and a unit test keeps it equal
to the code). The description as it was in the second version is kept next to it as `docs/openapi/v2.0.1.json` (byte for
byte as the second version had it; a unit test compares its sha256 with the one written in the test). Two comparisons, from PowerShell, one command at a time,
with the same oasdiff image as above:

```
docker run --rm -v ${PWD}\docs\openapi:/specs:ro tufin/oasdiff:v1.33.0 breaking /specs/v1.json /specs/v2.json --fail-on ERR
docker run --rm -v ${PWD}\docs\openapi:/specs:ro tufin/oasdiff:v1.33.0 breaking /specs/v2.0.1.json /specs/v2.json --fail-on ERR
```

| Comparison | Run | Exit code | Output |
|---|---|---|---|
| first release to current | `breaking ... --fail-on ERR` | 0 | No breaking changes to report, but the specs are different. |
| first release to current | `breaking ... --fail-on WARN` | 0 | The same: not even a warning. |
| first release to current | `changelog` | 0 | 4 changes, 0 errors, 0 warnings, 4 info: `endpoint-added` for `POST /api/v1/devices/enroll`, `GET .../cameras/{camera_id}/events` and `POST .../cameras/{camera_id}/events/{event_id}/playback`, and `new-optional-request-property` (`stamp_id` of `POST /api/v1/tenants`). |
| second version to current | `breaking ... --fail-on ERR` | 0 | No breaking changes to report, but the specs are different. |
| second version to current | `breaking ... --fail-on WARN` | 0 | The same. |
| second version to current | `changelog` | 0 | 2 changes, 0 errors, 0 warnings, 2 info: `endpoint-added` for the list of events and for the playback of an event. |

Both comparisons find 0 breaking changes. The only difference from the second version is the two new routes (0004); every operation that
was there is word for word the same (a unit test checks that too).


---

# Again, after the list of events got `limit` and `offset` (2026-10-26)

`docs/openapi/v2.json` was written again from the code by `scripts/export_openapi.py`. The only change to the description is two
optional query parameters on `GET .../cameras/{camera_id}/events` (`limit`, default 100, from 1 to 1000; `offset`, default 0, not
negative; ADR 0004). The answer is still a list, so the description of the answer is the same. The output of every run is kept as it
came from the tool in `docs/validation/oasdiff_output.txt` (oasdiff 1.33.0, the same image as above, run from PowerShell against the
files of `docs/openapi/`; the fourth pair is the description before this change (not kept in this repository), put next
to the new one in a folder of its own).

| Comparison | Run | Exit code | Output |
|---|---|---|---|
| first release to current | `breaking ... --fail-on ERR` and `--fail-on WARN` | 0 and 0 | No breaking changes to report, but the specs are different. |
| first release to current | `changelog` | 0 | 4 changes, 0 errors, 0 warnings, 4 info: the same four as before (the enrol route, the optional `stamp_id`, the two routes of events) |
| second version to current | `breaking ... --fail-on ERR` and `--fail-on WARN` | 0 and 0 | No breaking changes to report, but the specs are different. |
| second version to current | `changelog` | 0 | 2 changes, 0 errors, 0 warnings, 2 info: `endpoint-added` for the list of events and for the playback of an event |
| before this change to current | `breaking ... --fail-on ERR` and `--fail-on WARN` | 0 and 0 | No breaking changes to report, but the specs are different. |
| before this change to current | `changelog` | 0 | 2 changes, 0 errors, 0 warnings, 2 info: `new-optional-request-parameter` for `limit` and for `offset` of the list of events |

A caller that sends nothing new gets what it got, with one difference in the content: the list now holds 100 events (it held up to 500
within 24 hours), and a caller that wants more asks for the next page.
