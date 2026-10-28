-- Latest state of each camera (from MQTT) and the recording index (one row per segment).
CREATE TABLE camera_status (
    camera_id    uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL,
    state        text NOT NULL CHECK (state IN ('online', 'offline')),
    reason       text,
    fps          real,
    bitrate_kbps real,
    changed_at   timestamptz NOT NULL,
    seen_at      timestamptz NOT NULL,
    FOREIGN KEY (camera_id, tenant_id) REFERENCES cameras (id, tenant_id)
);

-- Key = camera (which fixes the tenant and the path) + start time. Written with ON CONFLICT DO NOTHING by both the
-- hook receiver and the reconciliation, so the two can run in any order and twice.
CREATE TABLE recording_segments (
    camera_id   uuid NOT NULL,
    tenant_id   uuid NOT NULL,
    started_at  timestamptz NOT NULL,
    duration_ms integer,
    file_name   text NOT NULL,
    source      text NOT NULL CHECK (source IN ('hook', 'reconcile')),
    deleted     boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (camera_id, started_at),
    FOREIGN KEY (camera_id, tenant_id) REFERENCES cameras (id, tenant_id)
);
CREATE INDEX recording_segments_tenant_time ON recording_segments (tenant_id, started_at);
