-- The tables in the database of a stamp other than the first (the first stamp uses the control database, which has them
-- in its own migrations). `cameras` is a copy of the camera ledger of the control plane, only as far as the stamp needs
-- it: which cameras exist, whose they are and whether they are deleted. The control plane writes it when a camera is
-- registered or removed and again at every start, and the writer of the stamp checks a camera against it in the same
-- statement that writes its state or segment.
CREATE TABLE cameras (
    id         uuid PRIMARY KEY,
    tenant_id  uuid NOT NULL,
    stamp_id   integer NOT NULL,
    deleted_at timestamptz,
    UNIQUE (id, tenant_id)
);

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
