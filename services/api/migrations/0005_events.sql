-- Events: what the motion and classify workers saw in the recordings. This file is for the control database, which is also
-- the database of the first stamp; the databases of the other stamps get the same table from stamp_migrations/0002_events.sql.
-- The key that makes a second write of the same event do nothing is the camera, the kind, the label and the start time.
-- `label` is '' (not NULL) for a movement event: in a UNIQUE key two NULLs are different, two '' are equal.
CREATE TABLE events (
    id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    camera_id   uuid NOT NULL,
    tenant_id   uuid NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('motion', 'object')),
    label       text NOT NULL DEFAULT '',
    started_at  timestamptz NOT NULL,
    duration_ms integer NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    score       real CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
    model       text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (camera_id, kind, label, started_at),
    FOREIGN KEY (camera_id, tenant_id) REFERENCES cameras (id, tenant_id)
);
CREATE INDEX events_tenant_camera_time ON events (tenant_id, camera_id, started_at);
