-- migrate:up
CREATE TABLE machine_keys (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    key_hash text NOT NULL,
    last_four char(4) NOT NULL,
    scopes jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at timestamptz
);
CREATE UNIQUE INDEX machine_keys_name_unique ON machine_keys (lower(btrim(name)));
CREATE TABLE machine_auth_limits (
    bucket text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    attempts integer NOT NULL
);
CREATE TABLE machine_key_requests (
    request_id uuid PRIMARY KEY,
    key_id uuid NOT NULL REFERENCES machine_keys(id),
    actor text NOT NULL,
    body_hash text NOT NULL,
    response jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- migrate:down
DROP TABLE machine_key_requests;
DROP TABLE machine_auth_limits;
DROP TABLE machine_keys;
