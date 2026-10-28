# 0003. Authentication of people, cameras and video

## Status
Accepted (2026-10-24).

## Context
Three kinds of callers must prove who they are: people using the viewing page, cameras sending video and state, and the media server (MediaMTX) deciding whether to hand out or accept video. MediaMTX can check a JWT whose permissions are inside the token, using a public key set (JWKS) fetched from a URL ([MediaMTX authentication documentation](https://mediamtx.org/docs/features/authentication)). Each permission is one action (`publish`, `read`, `playback`, `api`, `metrics`, `pprof`) and, for `publish`, `read` and `playback`, a path (empty means every path; a path that starts with `~` is a regular expression; MediaMTX 1.21.2 `internal/auth/manager.go`). This project always writes one exact path.

## Decision

### People
- The viewing page signs in with Keycloak using the authorization code flow with PKCE ([RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.txt): public clients MUST use PKCE; clients SHOULD NOT use the implicit grant "unless access token injection in the authorization response is prevented and the aforementioned token leakage vectors are mitigated", and this project does not use it at all; [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.txt)) through the `keycloak-js` library. The client is public, `pkce.code.challenge.method` is `S256` (if it is empty, PKCE is not required), implicit and Direct Access Grants are off, and the redirect URI and web origin are one exact value each. The access token is kept in memory, never in browser storage. Logout sends `id_token_hint` and `post_logout_redirect_uri`.
- The API checks the signature (keys read from Keycloak's JWKS at its internal address), `iss` (a fixed string, `http://localhost:8080/realms/ccc`, which is the name the browser uses), `azp` (the viewing client), `exp`, that `sub` is a UUID, and that the `typ` claim is `Bearer`. Keycloak's ID token has `typ` "ID" where the access token has "Bearer" (Keycloak 26.8.1 `core/src/main/java/org/keycloak/util/TokenUtil.java`: `TOKEN_TYPE_BEARER = "Bearer"`, `TOKEN_TYPE_ID = "ID"`), and its `aud` is the client id where the access token has `account` (`tests/unit/conftest.py`). The API does not check `aud`. That the ID token also carries the same `iss` and `azp` and is signed with the same key is the author's reading, not something this repository tests separately; the `typ` check is what refuses it. Without that check the ID token, which logout puts into the URL as `id_token_hint`, would also open the API. `tests/integration/test_01_identity.py` shows against a running Keycloak that the API answers 200 for the access token and 401 for the ID token. The accepted algorithm is fixed to RS256.
- Keycloak runs with `KC_HOSTNAME=http://localhost:8080` and `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`, so the browser sees one name while the API reaches it as `keycloak:8080`. It runs as `start-dev` because this is a demonstration; features `impersonation` and `token-exchange-standard` are disabled; the realm has no identity providers (checked in every realm by an integration test); its ports are on `127.0.0.1` only.

### Cameras
- A tenant administrator registers a camera through the API. The API returns a secret (`secrets.token_urlsafe(32)`) **once**, stores only its SHA-256 (the secret has 256 bits of randomness, so a slow hash adds nothing), and creates the camera's MQTT user. The response also carries `credential_type: "secret"`, `mediamtx_url` and `mqtt_url`, so that another credential type can be added later without breaking the shape.
- A camera trades its id and secret for a short `publish` token (`POST /api/v1/camera/token`). Unknown camera and wrong secret give the same answer.

### Video tokens (API to MediaMTX)
- The API signs ES256 JWTs and publishes the public key at `/jwks` (with `kid` and `"alg":"ES256"`). MediaMTX is configured with `authMethod: jwt`, `authJWTJWKS`, `authJWTIssuer: ccc-api` and `authJWTAudience: mediamtx` (always filled: with an empty audience MediaMTX does not check it).
- A token has exactly one permission: one action and one path, `<tenant uuid>/<camera uuid>` in lower case. Lifetimes: 5 minutes for every action (`publish`, `read`, `playback` and the API's internal `api` token). Source: Keycloak's default access token lifespan is 300 seconds (Keycloak 26.8.1 `Constants.java`: `DEFAULT_ACCESS_TOKEN_LIFESPAN = 300`), and the publish token that MediaMTX's authentication guide gets from Keycloak answers `"expires_in": 300`. (Until 2026-10-26 `publish` lived 10 minutes and `api` 1 minute, values without a source.) Every token has `exp`, `iat`, `iss`, `aud`; unit tests refuse to issue one without them.
- The Control API (port 9997) is not published. The API calls it with its own `api` token. No `authJWTExclude` is used, because that would open an unauthenticated door to anyone on the compose network.
- The signing key is made once by the init container and stored in a named volume. MediaMTX keeps the key set for one hour, so the key is never regenerated at start-up.
- The camera sends `?token=` in the RTSP URL. MediaMTX 1.21.2 (`internal/auth/manager.go`) has this comment where it reads the token: "always allow passing tokens through query parameters with RTSP and RTMP since there's no alternative." That is a statement about MediaMTX, not about RTSP: RTSP has an `Authorization` request header too (RFC 2326 section 12.5, which says only "See [H14.8]", the HTTP header). This project did not try to send the token in that header; the query is what the camera program uses. [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750.txt) section 2.3 says of the URI query method that it "SHOULD NOT be used unless it is impossible to transport the access token in the "Authorization" request header field or the HTTP request entity-body"; that text is about HTTP, and applying it to RTSP is the author's reading. The camera program removes the token from anything it prints.

## Consequences
- **MediaMTX checks a token only when a connection or session starts.** Later HLS requests are let through by the session (`docs/validation/poc.md`). Whether a running publisher is cut off when its token ends was not tested. So lifetimes shorten the window in which a leaked token can start something new; they are not relied on to cut off a running stream. Stopping a camera on the spot would need a call to the Control API to kick the session; that is not built.
- **MediaMTX accepts a token without `exp`.** The API therefore always sets it; a missing `exp` is caught by a unit test and by the mutation check (`m3_jwt_exp`).
- Replacing the signing key later means putting the new key into the JWKS first and then calling `POST /v3/auth/jwks/refresh` on MediaMTX; this version does not rotate keys.
- The token in the RTSP URL can end up in a log. The short life, the single path and the `publish`-only action keep that small; the camera program does not log it.
- `start-dev` is Keycloak's development profile (Keycloak's hostname guide). It must be replaced by `start` with a real database and TLS before anything outside a laptop.
- Camera secrets are shared secrets: if one leaks, that camera must be deleted and registered again.

## Alternatives not chosen
- **Keycloak's standard token exchange to mint video tokens.** Keycloak would have to know the video paths, a second place for them.
- **mTLS for cameras.** Not needed at this size; left for a later version.
- **Device Grant for registering cameras** (RFC 8628). A new function for a later version; the registration response already has the fields it needs.

## Update (2026-10-25)
- Device Grant for registering cameras was built: see 0009. The registration answer's fields (`mediamtx_url`, `mqtt_url`) now come from the
  tenant's stamp (0006).
- Stopping a running camera at once (a Control API "kick" when a camera is deleted) is still not built; it is listed in 0006 under "Not built".
