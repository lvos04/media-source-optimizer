-- Slice B: SABnzbd's own per-server state (login/account errors from mode=status + mode=warnings).
-- migrate:up

-- Current state per SABnzbd server (get_config servers[].name).
CREATE TABLE usenet_sab_servers (
    server       text PRIMARY KEY,
    displayname  text NOT NULL DEFAULT '',
    status       text NOT NULL DEFAULT 'unknown',  -- ok|account_expired|auth_failed|access_denied|block_exhausted|rate_limited|unreachable|tls_error|sab_blocked|disabled|error|unknown
    message      text,                             -- SABnzbd's own (redacted) message
    source       text,                             -- status|warnings|config
    message_at   timestamptz,
    active       boolean,
    enabled      boolean NOT NULL DEFAULT true,
    since        timestamptz,                      -- first observation of the current status
    observed_at  timestamptz NOT NULL DEFAULT now()
);

-- Periods in which SABnzbd reported a problem for a server (merged per status; used to relate failed
-- downloads to server problems that were active at that time).
CREATE TABLE usenet_sab_server_events (
    id        bigserial PRIMARY KEY,
    server    text NOT NULL,
    status    text NOT NULL,
    message   text,
    first_at  timestamptz NOT NULL,
    last_at   timestamptz NOT NULL
);
CREATE INDEX usenet_sab_server_events_idx ON usenet_sab_server_events(server, last_at DESC);
CREATE INDEX usenet_sab_server_events_last_idx ON usenet_sab_server_events(last_at DESC);

-- migrate:down
DROP TABLE IF EXISTS usenet_sab_server_events;
DROP TABLE IF EXISTS usenet_sab_servers;
