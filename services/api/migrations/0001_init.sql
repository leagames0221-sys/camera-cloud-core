-- Partners, tenants, memberships, cameras. A user lives in Keycloak only; memberships.user_id is the token's `sub`.
CREATE TABLE partners (
    id   uuid PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE tenants (
    id         uuid PRIMARY KEY,
    partner_id uuid NOT NULL REFERENCES partners (id),
    name       text NOT NULL
);

CREATE TABLE memberships (
    user_id    uuid NOT NULL,
    role       text NOT NULL CHECK (role IN ('partner_admin', 'tenant_admin', 'viewer')),
    partner_id uuid REFERENCES partners (id),
    tenant_id  uuid REFERENCES tenants (id),
    CHECK (
        (role = 'partner_admin' AND partner_id IS NOT NULL AND tenant_id IS NULL)
        OR (role <> 'partner_admin' AND tenant_id IS NOT NULL AND partner_id IS NULL)
    )
);
CREATE UNIQUE INDEX memberships_partner_uq ON memberships (user_id, role, partner_id) WHERE partner_id IS NOT NULL;
CREATE UNIQUE INDEX memberships_tenant_uq ON memberships (user_id, role, tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX memberships_user ON memberships (user_id);

CREATE TABLE cameras (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    name          text NOT NULL,
    secret_sha256 text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    UNIQUE (id, tenant_id)
);
CREATE INDEX cameras_tenant ON cameras (tenant_id) WHERE deleted_at IS NULL;
