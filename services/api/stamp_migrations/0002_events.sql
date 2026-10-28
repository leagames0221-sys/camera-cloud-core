-- Events in the database of a stamp other than the first (the first stamp uses the control database: migrations/0005_events.sql).
-- Same table, same key: a second write of the same event does nothing. `label` is the empty text for a movement event.
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
