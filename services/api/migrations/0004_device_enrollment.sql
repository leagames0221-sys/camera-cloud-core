-- Each access token of the camera registration (device authorization grant) may be used once. The token's `jti` is
-- inserted before anything else is done; a second use finds the row and is refused, also when two calls arrive at the
-- same moment (the primary key lets only one insert through). A row can be deleted after its token has expired.
CREATE TABLE enroll_tokens (
    jti        text PRIMARY KEY,
    expires_at timestamptz NOT NULL
);
