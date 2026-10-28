# 0009. Registering a camera with the device authorization grant

## Status
Accepted (2026-10-25). Changes the "Device Grant (a later version)" line of 0003.

## Context
In the first version a tenant administrator registers a camera through the API and copies its secret to the camera by hand. A
camera has no keyboard or screen to log in with. RFC 8628 (OAuth 2.0 Device Authorization Grant) is made for this: the device asks
for a code, shows it to a person, and the person approves it on another device, where they log in. Keycloak 26.8 supports
it (feature `device-flow`, enabled by default).

## Decision
- **The flow.** The camera POSTs to Keycloak's `/auth/device` endpoint with the client id `ccc-device` and receives `device_code`,
  `user_code`, `verification_uri` and `interval` (RFC 8628 section 3.2). It prints the user code and the address (to the log: its
  "screen") and polls the token endpoint every `interval` seconds (5): `authorization_pending` means wait, `slow_down` means 5 seconds
  more from now on, `access_denied` and `expired_token` stop it (section 3.5). The camera's polling code and these answers are tested
  (`tests/unit/test_camera_device.py`, `tests/integration_v2/test_04_device_grant.py`).
- **The approval.** The tenant administrator opens `/realms/ccc/device` (the viewing page has a link, "カメラを追加"), logs in, enters
  the code and sees a consent page, "Register a camera in your tenant (カメラをテナントに登録する)" (RFC 8628 section 5.4, remote phishing: the user
  should be told that they are authorizing a device). Consent is required; the client's `consent.screen.text` attribute carries the text.
- **The client `ccc-device`** (in the realm file): public, only the device grant is on (no authorization code, no password grant, no
  implicit flow), a code lives **600 seconds**: Keycloak's own default, `DEFAULT_OAUTH2_DEVICE_CODE_LIFESPAN = 600` in `OAuth2DeviceConfig` of
  26.8.1 (the line before it in the source says "10 minutes"), set in the realm file as `oauth2.device.code.lifespan` and checked by
  `tests/unit/test_static_config.py` and `tests/integration_v2/test_04_device_grant.py` (`expires_in == 600`); the camera uses 600
  too when an answer has no `expires_in`. Until 2026-10-26 the value was 300, with no model. A realm that already exists is not
  imported again by Keycloak, so a running one has the attribute changed through the admin API (the test does the same with a
  shorter value) or is made again from the file. The interval is 5
  seconds (the RFC's default, and Keycloak's `DEFAULT_OAUTH2_DEVICE_POLLING_INTERVAL = 5`), `fullScopeAllowed=false`, one default scope (`basic`), an
  audience mapper that puts `ccc-enroll` in `aud`, refresh tokens off. The result, checked in the tests: the access token has
  `azp=ccc-device`, `aud=["ccc-enroll"]` (no `account`), no roles, no refresh token and no ID token in the answer. The attribute names
  were read in Keycloak's source (`ClientScopeModel`: `display.on.consent.screen`, `consent.screen.text`) and the consent page was
  walked in a test. The viewing page's client cannot start this flow (`unauthorized_client`).
- **The registration** is a new call, `POST /api/v1/devices/enroll` with only a name. The API checks the signature, the fixed issuer,
  the expiry, `typ=Bearer`, `azp=ccc-device` and `aud` containing `ccc-enroll`; it then **inserts the token's `jti` into a table with
  a primary key before doing anything else** (a second use, also one at the same moment, is refused; the test starts four calls at once
  and exactly one is created); it then requires that the user is the tenant administrator of **exactly one** tenant (a viewer or a
  partner administrator gets 403; an administrator of two tenants gets 409, because which tenant the camera is for is not known). Then
  it does what registering does: creates the camera in the ledger and the broker user in the tenant's stamp, and returns the same
  answer as the registration call, with the stamp's own addresses.
- **The camera throws the token away** and keeps only its camera id and secret (a file in its own volume, owner only). From then on it
  works like any camera. A token for this flow opens no other door (401 on every other call), and the viewing page's token does not open
  this one.

## Consequences
- An administrator's rights are not left on the camera: the token is used once, expires in minutes and is not stored. A person who
  leaves the company can no longer stop the camera by being deleted from Keycloak.
- A tenant administrator of two tenants cannot register a camera this way until the choice of tenant is built.
- Keycloak does not add 5 seconds by itself after `slow_down` (its `DeviceGrantType.isPollingAllowed` keeps the polling mark for the interval only); the camera adds
  them, as the RFC tells a client to.
- The camera's address for Keycloak inside the compose network (`keycloak:8080`) differs from the address the person types
  (`localhost:8080`); Keycloak is set up to write its public name into the pages and to accept the inside name for calls from the
  back end (0003).

## Alternatives not chosen
- **The administrator copies the secret to the camera (the first version).** It remains possible.
- **Giving the camera Keycloak tokens for good.** The camera would depend on the administrator's account and carry rights it should
  not have.
- **mTLS or a certificate per camera.** Not built (0003).
