-- Foundation: users/sessions (RBAC), settings, encrypted secrets, connectors, audit, jobs, change sets.
-- migrate:up
CREATE TABLE users (
    id            bigserial PRIMARY KEY,
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role          text NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
    disabled      boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

CREATE TABLE sessions (
    token_hash  text PRIMARY KEY,
    user_id     bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

CREATE TABLE settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text
);

-- AES-GCM encrypted with the key in APPDATA/secrets/vault.key. Never returned by the API.
CREATE TABLE secrets (
    name       text PRIMARY KEY,
    nonce      bytea NOT NULL,
    ciphertext bytea NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE connectors (
    id              text PRIMARY KEY,
    kind            text NOT NULL,
    label           text NOT NULL,
    url             text,
    enabled         boolean NOT NULL DEFAULT false,
    options         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'not_configured',
    version         text,
    capabilities    jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_checked_at timestamptz,
    last_error      text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    actor      text NOT NULL,
    action     text NOT NULL,
    target     text,
    summary    text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    change_id  bigint
);
CREATE INDEX audit_ts_idx ON audit_log(ts DESC);
CREATE INDEX audit_action_idx ON audit_log(action);

-- Persistent, restart-safe jobs. Claimed with FOR UPDATE SKIP LOCKED; a running job whose
-- heartbeat is stale is re-queued at worker start (lease expiry).
CREATE TABLE jobs (
    id              bigserial PRIMARY KEY,
    kind            text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    idempotency_key text UNIQUE,
    attempts        integer NOT NULL DEFAULT 0,
    max_attempts    integer NOT NULL DEFAULT 3,
    run_after       timestamptz NOT NULL DEFAULT now(),
    locked_by       text,
    heartbeat_at    timestamptz,
    progress        real,
    summary         text,
    result          jsonb,
    error           text,
    created_by      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_claim_idx ON jobs(status, run_after);

-- Change sets: every write to an external system (SABnzbd, qBittorrent, Prowlarr) is a change set
-- with before/after per operation. Apply stores the observed 'before'; rollback re-applies it.
CREATE TABLE change_sets (
    id          bigserial PRIMARY KEY,
    target      text NOT NULL,
    title       text NOT NULL,
    explanation text NOT NULL DEFAULT '',
    risk        text NOT NULL CHECK (risk IN ('advies', 'automatisch_veilig', 'toestemming_nodig')),
    status      text NOT NULL DEFAULT 'preview'
                CHECK (status IN ('preview', 'approved', 'applied', 'rolled_back', 'failed', 'discarded')),
    ops         jsonb NOT NULL DEFAULT '[]'::jsonb,
    impact      jsonb NOT NULL DEFAULT '{}'::jsonb,
    source      text NOT NULL DEFAULT 'user',
    dedupe_key  text,
    created_by  text,
    approved_by text,
    applied_by  text,
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    applied_at  timestamptz,
    rolled_back_at timestamptz,
    evaluate_after timestamptz
);
CREATE INDEX change_sets_status_idx ON change_sets(status);
CREATE UNIQUE INDEX change_sets_dedupe_open_idx ON change_sets(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('preview', 'approved');

CREATE TABLE login_attempts (
    id       bigserial PRIMARY KEY,
    username text NOT NULL,
    remote   text NOT NULL,
    ok       boolean NOT NULL,
    ts       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX login_attempts_ts_idx ON login_attempts(ts);

CREATE TABLE backups (
    id               bigserial PRIMARY KEY,
    file             text NOT NULL,
    kind             text NOT NULL DEFAULT 'manual',
    size_bytes       bigint NOT NULL DEFAULT 0,
    sha256           text,
    includes_secrets boolean NOT NULL DEFAULT false,
    schema_version   integer NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Small key/value state (operating mode, pause flag, setup completion).
CREATE TABLE system_state (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO system_state(key, value) VALUES
    ('mode', '"read_only"'), ('paused', 'false'), ('setup_completed', 'false');

-- migrate:down
DROP TABLE IF EXISTS system_state;
DROP TABLE IF EXISTS backups;
DROP TABLE IF EXISTS login_attempts;
DROP TABLE IF EXISTS change_sets;
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS connectors;
DROP TABLE IF EXISTS secrets;
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS users;
