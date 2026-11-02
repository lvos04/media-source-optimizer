-- Slice B: Usenet engine (SABnzbd ingestion, Usenet providers, article availability, optional header index).
-- migrate:up

-- Usenet providers / block accounts. Credentials live ONLY in the vault (provider.<slug>.username/password).
CREATE TABLE usenet_providers (
    slug            text PRIMARY KEY CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
    label           text NOT NULL,
    kind            text NOT NULL DEFAULT 'unlimited' CHECK (kind IN ('unlimited', 'block')),
    host            text NOT NULL,
    port            integer NOT NULL DEFAULT 563 CHECK (port BETWEEN 1 AND 65535),
    ssl             boolean NOT NULL DEFAULT true,
    connections     integer NOT NULL DEFAULT 10 CHECK (connections BETWEEN 1 AND 200),
    retention_days  integer CHECK (retention_days IS NULL OR retention_days >= 0),  -- provider's claim (user input)
    block_size_gb   real CHECK (block_size_gb IS NULL OR block_size_gb > 0),
    sab_server      text,                      -- linked SABnzbd server name (get_config servers[].name)
    enabled         boolean NOT NULL DEFAULT true,
    notes           text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'unknown',  -- ok|auth_failed|account_expired|rate_limited|block_exhausted|unreachable|tls_error|timeout|error|unknown
    status_detail   text,
    last_check_at   timestamptz,
    last_ok_at      timestamptz,
    capabilities    jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Incremental SABnzbd history polling state (single row, id = 1).
CREATE TABLE usenet_ingest_state (
    id                   integer PRIMARY KEY CHECK (id = 1),
    last_history_update  bigint,               -- SAB's own change counter (conditional polling)
    last_completed       bigint,               -- highest 'completed' unix time ingested
    last_run_at          timestamptz,
    last_stats_at        timestamptz,
    last_error           text,
    ingested_total       bigint NOT NULL DEFAULT 0
);

-- NZBs MSO knows the contents of: sent by MSO (add_nzb / record_nzb) or uploaded by the user for a test.
-- Only a bounded sample of segment message-ids is kept (never the whole NZB).
CREATE TABLE usenet_nzbs (
    id              bigserial PRIMARY KEY,
    nzo_id          text UNIQUE,              -- SABnzbd job id when sent through SAB
    origin          text NOT NULL CHECK (origin IN ('send_url', 'send_nzb', 'upload')),
    title           text NOT NULL,
    category        text,
    release_key     text,
    source_id       text,
    sha256          text,
    url_redacted    text,
    groups          text[] NOT NULL DEFAULT '{}',
    posted_at       timestamptz,
    files           integer NOT NULL DEFAULT 0,
    segment_total   integer NOT NULL DEFAULT 0,
    bytes_total     bigint NOT NULL DEFAULT 0,
    segments        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{m: message-id, b: bytes, f: file index, n: number}]
    created_by      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX usenet_nzbs_created_idx ON usenet_nzbs(created_at DESC);

-- Passive per-server statistics from SABnzbd server_stats (one row per server per day; upserted).
CREATE TABLE usenet_server_stats (
    server            text NOT NULL,
    day               date NOT NULL,
    bytes             bigint NOT NULL DEFAULT 0,
    articles_tried    bigint,
    articles_success  bigint,
    observed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (server, day)
);

-- Explicit article availability tests on the real articles of one NZB.
CREATE TABLE usenet_article_tests (
    id            bigserial PRIMARY KEY,
    nzb_id        bigint NOT NULL REFERENCES usenet_nzbs(id) ON DELETE CASCADE,
    trigger       text NOT NULL CHECK (trigger IN ('failure', 'manual')),
    status        text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    providers     text[] NOT NULL,
    sample_size   integer NOT NULL,
    summary       jsonb NOT NULL DEFAULT '{}'::jsonb,
    error         text,
    requested_by  text,
    job_id        bigint,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);
CREATE INDEX usenet_article_tests_created_idx ON usenet_article_tests(created_at DESC);

CREATE TABLE usenet_article_results (
    test_id     bigint NOT NULL REFERENCES usenet_article_tests(id) ON DELETE CASCADE,
    provider    text NOT NULL,                 -- usenet_providers.slug
    message_id  text NOT NULL,
    available   boolean,                       -- NULL = not determined (error/timeout)
    code        integer,
    checked_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (test_id, provider, message_id)
);
CREATE INDEX usenet_article_results_checked_idx ON usenet_article_results(checked_at DESC);

-- Optional, opt-in header index (bounded: whitelisted groups, TTL, storage budget).
CREATE TABLE usenet_hi_groups (
    name          text PRIMARY KEY,
    last_article  bigint,
    last_run_at   timestamptz,
    last_error    text
);

CREATE TABLE usenet_hi_headers (
    id            bigserial PRIMARY KEY,
    grp           text NOT NULL,
    article_num   bigint NOT NULL,
    message_id    text NOT NULL,
    subject       text NOT NULL,
    release_name  text NOT NULL,               -- filename/stem derived from the subject (no poster stored)
    posted_at     timestamptz,
    bytes         bigint NOT NULL DEFAULT 0,
    obfuscated    boolean NOT NULL DEFAULT false,
    usable        boolean NOT NULL DEFAULT false,
    inserted_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (grp, article_num)
);
CREATE INDEX usenet_hi_headers_posted_idx ON usenet_hi_headers(posted_at);
CREATE INDEX usenet_hi_headers_inserted_idx ON usenet_hi_headers(inserted_at);
CREATE INDEX usenet_hi_headers_release_idx ON usenet_hi_headers(lower(release_name));

CREATE TABLE usenet_hi_runs (
    id                bigserial PRIMARY KEY,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'done', 'budget_reached', 'failed', 'skipped')),
    headers_fetched   integer NOT NULL DEFAULT 0,
    headers_stored    integer NOT NULL DEFAULT 0,
    bytes_received    bigint NOT NULL DEFAULT 0,
    obfuscated        integer NOT NULL DEFAULT 0,
    usable            integer NOT NULL DEFAULT 0,
    message           text
);
CREATE INDEX usenet_hi_runs_started_idx ON usenet_hi_runs(started_at DESC);

-- migrate:down
DROP TABLE IF EXISTS usenet_hi_runs;
DROP TABLE IF EXISTS usenet_hi_headers;
DROP TABLE IF EXISTS usenet_hi_groups;
DROP TABLE IF EXISTS usenet_article_results;
DROP TABLE IF EXISTS usenet_article_tests;
DROP TABLE IF EXISTS usenet_server_stats;
DROP TABLE IF EXISTS usenet_nzbs;
DROP TABLE IF EXISTS usenet_ingest_state;
DROP TABLE IF EXISTS usenet_providers;
