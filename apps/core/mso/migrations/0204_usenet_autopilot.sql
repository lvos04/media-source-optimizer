-- Slice B (1.5.0): Autopilot for SABnzbd servers and failed downloads.
-- usenet_server_autoheal: per SABnzbd server the automatic off/on state (switched off by MSO, probe re-enable
--   with backoff, recovered). Rows only exist for servers MSO switched off or is watching.
-- usenet_retries: failed SABnzbd jobs MSO retried (mode=retry) or proposed to retry; once per job.
-- migrate:up
CREATE TABLE usenet_server_autoheal (
    server            text PRIMARY KEY,                     -- SABnzbd server name (get_config 'name')
    status            text NOT NULL CHECK (status IN ('disabled', 'probing', 'recovered', 'given_up')),
    reason            text,                                  -- serverstatus status, e.g. account_expired
    message           text,                                  -- SABnzbd's message (redacted)
    disabled_at       timestamptz,
    disable_change_id bigint,
    probes            integer NOT NULL DEFAULT 0,            -- failed probes in a row (backoff exponent)
    next_probe_at     timestamptz,
    probe_started_at  timestamptz,
    probe_change_id   bigint,
    probe_trigger     text,                                  -- autopilot | user | sabnzbd | own_test
    last_result       text,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usenet_retries (
    id           bigserial PRIMARY KEY,
    nzo_id       text NOT NULL UNIQUE,                       -- the failed SABnzbd job (once per job)
    outcome_id   bigint,
    title        text NOT NULL,
    reason       text,                                       -- failure_reason of the outcome
    status       text NOT NULL CHECK (status IN ('proposed', 'retried', 'verified', 'failed')),
    trigger      text NOT NULL DEFAULT 'proposal',           -- autopilot | proposal
    change_id    bigint,
    new_nzo_id   text,                                       -- SABnzbd may give the retried job a new id
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    retried_at   timestamptz,
    verified_at  timestamptz
);
CREATE INDEX usenet_retries_retried_idx ON usenet_retries (retried_at);

-- migrate:down
DROP TABLE IF EXISTS usenet_retries;
DROP TABLE IF EXISTS usenet_server_autoheal;
