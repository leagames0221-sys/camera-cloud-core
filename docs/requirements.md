# Requirements

## Purpose
A small multi-tenant backend for recording cameras. Customer companies (tenants) see their own cameras, live video and the
last five minutes of recording; a partner company looks after several tenants. It is a small working model, built to check
the core design (who may see what, how video is authorised, how camera state and the recording index are kept, how the
parts that grow with the number of cameras are copied and fed through a queue), not a product. It runs on one laptop with
Docker.

## Users and roles
| Role | Who | Scope |
|---|---|---|
| Partner administrator | staff of a partner company | the tenants of their partner |
| Tenant administrator | staff of a customer company | their tenant |
| Viewer | staff of a customer company | their tenant |

## What each role may do (checked on the server at every request)
| Operation | Partner administrator | Tenant administrator | Viewer |
|---|---|---|---|
| Create and list tenants of the partner | yes | | |
| Register and delete cameras (own tenant) | | yes | |
| Approve the registration of a new camera that shows a code (own tenant, exactly one) | | yes | |
| List cameras and their state (own tenant; the partner administrator for every tenant of the partner) | yes | yes | yes |
| Watch live video and the last five minutes (own tenant); list the events of a camera and play from one | | yes | yes |
| Add users to a tenant (partner administrator: up to tenant administrator; tenant administrator: viewers) | yes | yes | |

The partner administrator never watches video. A thing that belongs to another company is answered `404`, the same as a thing
that does not exist. A role that is too low inside one's own tenant is answered `403`. A tenant is served by one **stamp** (a
copy of the video server, broker, queue, writer and database); the partner administrator may name the stamp when creating a
tenant, otherwise the stamp with the fewest tenants is used.

## Functional requirements
| # | Requirement |
|---|---|
| F1 | A user signs in and out through Keycloak (authorization code with PKCE). |
| F2 | A viewer sees the cameras of their tenant with a state: recording, standby or disconnected. |
| F3 | A viewer watches a camera live (HLS). |
| F4 | A viewer plays back the last five minutes of a camera. |
| F5 | A tenant administrator registers a camera through the API and receives its secret once; a camera uses it to obtain a short token and to report its state. |
| F6 | Video is recorded in 60-second segments, kept one day, and each segment is entered in an index that can be searched by tenant, camera and time. A lost notification is made up by a periodic check. |
| F7 | A camera that stops without saying so (killed, frozen) is shown as disconnected within the MQTT keep-alive limit; one that stops normally is shown as disconnected with the reason `normal`. |
| F8 | A viewer of company B who types the address of a company A camera sees "not found". |
| F9 | A camera with no secret shows a code; a tenant administrator enters it on Keycloak's page and approves; within a minute the camera is registered once, listed and playing, and holds only its own secret (device authorization grant). |
| F10 | Two stamps run side by side. Company C is served by the second one, A and B by the first. What belongs to one stamp (a video token, a camera's secret, a segment, a state) does not work in the other. |
| F11 | State messages and closed-segment calls go through a queue and are written to the database in groups; none is lost when the writer is stopped, a repeated one gives the same result, one that cannot be written ends in a dead stream after five tries. |
| F12 | No tenant can use more than 100 API calls in a second; another tenant is not slowed by it and a caller who does not belong to a tenant cannot use up its calls. |
| F13 | The seconds of a recording in which something moves, and the names of the things in them (DETR, one picture a second, score above 0.9), are kept as events with a time. An event is written only for a camera of its stamp and a time that a segment of the index contains. A camera is looked at only if it is on the list (`var/vision/cameras.txt`). |
| F14 | A viewer lists the events of a camera (newest first, 100 in an answer and the next page with `offset`) and plays the recording from 5 seconds before one; an event whose recording is gone says so. A viewer of another company, and the partner administrator, get nothing. |

## Non-functional requirements
| # | Requirement |
|---|---|
| N1 | Company isolation holds in the API (`404`), the media server (a token opens one path on one stamp only) and the broker (a camera publishes to its own topic only, on its own stamp's broker). |
| N2 | Starts with one command; each container waits for what it needs; the containers (18, and four more that start only on request: `cam-device`, and with the profile `vision` `motion`, `classify` and `cam-b2`) have a memory limit each (5,504 MiB in all for the 18; 9,216 MiB with the three of the profile `vision`). |
| N3 | Only the gateway, Keycloak and the video ports (HLS and playback of each stamp) are published, and only on the loopback address. Databases, brokers, queues, the Control APIs and the internal doors of the API and the writers are not published. A camera cannot reach the queue. |
| N4 | No paid service, no credit card, no component from China, no copyleft code copied. |
| N5 | Every database change is a numbered SQL file and is recorded as applied; a change to a database that an older API still uses only adds. |
| N6 | The public shape of the API of the first release (`docs/openapi/v1.json`) is kept; the current shape (`docs/openapi/v2.json`) breaks nothing in it (oasdiff, `docs/validation/openapi_diff.md`). |
| N7 | The load of the three growing paths (viewer calls, closed segments, camera state) is measured under fixed conditions for both versions and the result is compared (`docs/validation/load_compare.md`). |
| N8 | The movement worker is measured second by second against a video whose true answer was fixed by how it was made, and the classify worker against the output the model card publishes (`docs/validation/vision_eval.md`); the numbers fixed beforehand are not changed to fit the result. Nothing of the workers runs without the profile `vision`, and a machine without a GPU starts as before. |

## Acceptance (what a person does)
1. Start with the one command in the README, wait five minutes, sign in as the company A viewer and see the camera list, live video and the last five minutes.
2. Sign out, sign in as the company B viewer: no company A camera is listed; opening the address of a company A camera shows "not found".
3. Start `cam-device`, sign in as the company C administrator, open "カメラを追加", enter the code that the camera shows and approve: within a minute the new camera is in company C's list and its live picture plays.
4. Read the table in `docs/validation/load_compare.md`: the two versions side by side under the same conditions.
5. With the profile `vision` running (README), sign in as the company B viewer and open the camera "確かめの映像": the events (movement, and names of things with scores) are listed; press one and the recording plays from its time. `docs/validation/vision_eval.md` shows the rates of the movement worker and that the classify worker gave the model card's answer (remote 2, couch 1, cat 2).
6. Open `docs/design-guide.md`: from the links of the section on keeping tenants apart, the lines of the code open.

## Out of scope for this version
A screen for registering users, more than one instance of the API (the limit per tenant would move to Valkey), moving a tenant between
stamps, adding stamps automatically, a tenant administrator of two tenants registering a camera by code, TLS, notifications and a search screen for events, tracking and recognising people, the workers on a second stamp, naming without a GPU,
automatic key rotation, forcing a running stream to stop when its token expires, Row Level Security (compared in a later version), load
tests of the video itself.
