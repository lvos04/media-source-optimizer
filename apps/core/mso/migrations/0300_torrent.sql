-- Slice C: torrent engine (qBittorrent). Seed rules, per-torrent state + samples, client snapshot,
-- tuning runs, cross-seed candidates, one-shot client actions, storage scans.
-- migrate:up

-- User-configured seed obligations / permissions per tracker host and/or qBittorrent category.
CREATE TABLE torrent_tracker_rules (
    id                bigserial PRIMARY KEY,
    tracker_host      text,                         -- e.g. 'tracker.privehd.example' (NULL = any tracker)
    category          text,                         -- qBittorrent category (NULL = any category)
    label             text NOT NULL DEFAULT '',
    private           boolean NOT NULL DEFAULT false,
    min_ratio         real CHECK (min_ratio IS NULL OR min_ratio >= 0),
    min_seed_time_h   real CHECK (min_seed_time_h IS NULL OR min_seed_time_h >= 0),
    mode              text NOT NULL DEFAULT 'either' CHECK (mode IN ('either', 'both')),
    allow_cross_seed  boolean NOT NULL DEFAULT false,
    notes             text NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (tracker_host IS NOT NULL OR category IS NOT NULL)
);
CREATE UNIQUE INDEX torrent_tracker_rules_scope_idx
    ON torrent_tracker_rules (COALESCE(tracker_host, ''), COALESCE(category, ''));

-- Latest known state per torrent (written by the ingest job; the UI reads from here).
CREATE TABLE torrent_state (
    hash             text PRIMARY KEY,               -- qBittorrent id (v1 hash, or truncated v2)
    client           text NOT NULL DEFAULT 'qbittorrent',
    name             text NOT NULL,
    category         text,
    media_category   text,
    state            text,
    private          boolean,
    tracker_host     text,
    info             jsonb NOT NULL DEFAULT '{}'::jsonb,  -- sanitized torrents/info row
    trackers         jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{host,status,msg,seeds,peers,kind}] (no URLs/passkeys)
    trackers_at      timestamptz,
    diagnosis        jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen       timestamptz NOT NULL DEFAULT now(),
    last_progress_at timestamptz,
    last_progress    double precision,
    removed          boolean NOT NULL DEFAULT false,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX torrent_state_state_idx ON torrent_state(state);

-- Short-term per-torrent samples (false-ETA detection, stall duration). Pruned by retention setting.
CREATE TABLE torrent_samples (
    id           bigserial PRIMARY KEY,
    hash         text NOT NULL,
    ts           timestamptz NOT NULL DEFAULT now(),
    state        text,
    progress     double precision,
    dlspeed      bigint,
    upspeed      bigint,
    amount_left  bigint,
    num_seeds    integer,
    num_leechs   integer,
    availability double precision,
    eta          bigint
);
CREATE INDEX torrent_samples_hash_ts_idx ON torrent_samples(hash, ts DESC);

-- Last client snapshot (single row id=1): version, capabilities, server_state, sanitized preferences.
CREATE TABLE torrent_client_snapshot (
    id            integer PRIMARY KEY CHECK (id = 1),
    version       text,
    webapi        text,
    auth_method   text,
    capabilities  jsonb NOT NULL DEFAULT '{}'::jsonb,
    server_state  jsonb NOT NULL DEFAULT '{}'::jsonb,
    preferences   jsonb NOT NULL DEFAULT '{}'::jsonb,
    interfaces    jsonb NOT NULL DEFAULT '[]'::jsonb,
    findings      jsonb NOT NULL DEFAULT '[]'::jsonb,
    error         text,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- One reversible parameter change at a time: baseline → change → evaluation → keep/rollback.
CREATE TABLE torrent_tuning_runs (
    id              bigserial PRIMARY KEY,
    change_id       bigint REFERENCES change_sets(id) ON DELETE SET NULL,
    param           text NOT NULL,                  -- e.g. 'pref:max_connec'
    kind            text NOT NULL DEFAULT 'tuning' CHECK (kind IN ('tuning', 'guardrail', 'disk_io')),
    direction       text CHECK (direction IN ('up', 'down', 'set')),
    before_value    jsonb,
    after_value     jsonb,
    metric          text NOT NULL,
    higher_is_better boolean NOT NULL DEFAULT true,
    baseline        jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {median, n, from, to}
    result          jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {median, n, delta_pct, ...}
    reason          text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed', 'evaluating', 'kept', 'rolled_back', 'degraded_pending',
                                      'inconclusive', 'discarded')),
    evaluate_after_h real NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    applied_at      timestamptz,
    evaluated_at    timestamptz,
    note            text NOT NULL DEFAULT ''
);
CREATE INDEX torrent_tuning_runs_status_idx ON torrent_tuning_runs(status);

-- Cross-seed candidates: proven identical payload (file names + sizes) of a complete torrent.
-- The .torrent itself (contains the tracker passkey) is stored encrypted in the vault, never here.
CREATE TABLE torrent_crossseed_candidates (
    id               bigserial PRIMARY KEY,
    source_hash      text NOT NULL,
    candidate_hash   text NOT NULL UNIQUE,
    tracker_host     text,
    title            text NOT NULL,
    release_source   text,                           -- sources.id that returned it
    files            integer NOT NULL,
    total_size       bigint NOT NULL,
    save_path        text,
    category         text,
    match            jsonb NOT NULL DEFAULT '{}'::jsonb,
    status           text NOT NULL DEFAULT 'candidate'
                     CHECK (status IN ('candidate', 'previewed', 'added', 'rejected')),
    change_id        bigint REFERENCES change_sets(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- One-shot client actions (recheck/reannounce/cross-seed add) executed via change sets: audit + read-back.
CREATE TABLE torrent_actions (
    id         bigserial PRIMARY KEY,
    hash       text NOT NULL,
    action     text NOT NULL,
    change_id  bigint,
    ts         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX torrent_actions_hash_idx ON torrent_actions(hash, action, ts DESC);

-- Last storage / hardlink scan (single row id=1; read-only filesystem inspection).
CREATE TABLE torrent_storage_scan (
    id         integer PRIMARY KEY CHECK (id = 1),
    result     jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS torrent_storage_scan;
DROP TABLE IF EXISTS torrent_actions;
DROP TABLE IF EXISTS torrent_crossseed_candidates;
DROP TABLE IF EXISTS torrent_tuning_runs;
DROP TABLE IF EXISTS torrent_client_snapshot;
DROP TABLE IF EXISTS torrent_samples;
DROP TABLE IF EXISTS torrent_state;
DROP TABLE IF EXISTS torrent_tracker_rules;
