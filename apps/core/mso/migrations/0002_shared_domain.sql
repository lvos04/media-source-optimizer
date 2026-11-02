-- Shared domain tables used across engines (usenet, torrent, search, recovery, costs).
-- migrate:up

-- Every finished/failed download observed in SABnzbd or qBittorrent (written by the engines,
-- read by baseline, recovery and cost analysis). One row per client item; upserted.
CREATE TABLE download_outcomes (
    id              bigserial PRIMARY KEY,
    protocol        text NOT NULL CHECK (protocol IN ('usenet', 'torrent')),
    client          text NOT NULL,                 -- sabnzbd|qbittorrent|transmission|deluge
    client_ref      text NOT NULL,                 -- nzo_id or infohash
    release_key     text,                          -- nzb guid / 'btih:<v1>' / 'btmh:<v2>'
    title           text NOT NULL,
    client_category text,
    media_category  text CHECK (media_category IN ('tv', 'tv-anime', 'movies', 'movies-anime')),
    source_id       text,                          -- sources.id (indexer/tracker) if known
    status          text NOT NULL CHECK (status IN ('active', 'success', 'failed', 'removed')),
    failure_reason  text,                          -- see mso.domain.outcomes.FAILURE_REASONS
    failure_detail  text,
    size_bytes      bigint,
    downloaded_bytes bigint,
    provider_bytes  jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {provider_id: bytes} (usenet)
    private         boolean,                       -- torrent private flag
    started_at      timestamptz,
    finished_at     timestamptz,
    observed_at     timestamptz NOT NULL DEFAULT now(),
    meta            jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (client, client_ref)
);
CREATE INDEX download_outcomes_finished_idx ON download_outcomes(finished_at DESC);
CREATE INDEX download_outcomes_release_idx ON download_outcomes(release_key);

-- Registry of every search/download source (indexers, trackers, usenet providers, spotweb, header index).
CREATE TABLE sources (
    id          text PRIMARY KEY,                  -- e.g. 'prowlarr:12', 'direct:nzbgeek', 'provider:eweka'
    kind        text NOT NULL CHECK (kind IN ('nzb_indexer', 'torrent_indexer', 'usenet_provider',
                                              'block_account', 'spotweb', 'header_index', 'tracker')),
    label       text NOT NULL,
    origin      text NOT NULL CHECK (origin IN ('prowlarr', 'direct', 'mso', 'sabnzbd', 'qbittorrent')),
    scopes      text[] NOT NULL DEFAULT ARRAY['normal','anime'],  -- normal and/or anime
    private     boolean NOT NULL DEFAULT false,
    enabled     boolean NOT NULL DEFAULT true,
    meta        jsonb NOT NULL DEFAULT '{}'::jsonb,
    reliability real,                               -- 0..1, computed (search success, false positives)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Paid services with manually entered cost (user input only; never auto-cancelled).
CREATE TABLE services (
    id            bigserial PRIMARY KEY,
    source_id     text REFERENCES sources(id) ON DELETE SET NULL,
    kind          text NOT NULL,                    -- usenet_provider|block_account|nzb_indexer|tracker|vpn|other
    label         text NOT NULL,
    billing       text NOT NULL DEFAULT 'monthly' CHECK (billing IN ('monthly', 'yearly', 'block', 'free')),
    amount_cents  integer NOT NULL DEFAULT 0 CHECK (amount_cents >= 0),
    currency      text NOT NULL DEFAULT 'EUR',
    block_size_gb real,
    block_used_gb real,
    renewal_date  date,
    notes         text NOT NULL DEFAULT '',
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Time-series observations (health checks, latency, hit rates) for any source/connector.
CREATE TABLE observations (
    id         bigserial PRIMARY KEY,
    subject    text NOT NULL,                       -- sources.id or connector id
    metric     text NOT NULL,                       -- e.g. 'search.latency_ms', 'nntp.article_ok'
    value      double precision,
    labels     jsonb NOT NULL DEFAULT '{}'::jsonb,
    ts         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX observations_subject_metric_ts_idx ON observations(subject, metric, ts DESC);

-- Recommendations shown in UI (each links to an optional change set for apply/rollback).
CREATE TABLE recommendations (
    id          bigserial PRIMARY KEY,
    area        text NOT NULL,                      -- usenet|torrent|indexers|recovery|costs|mediaguard
    subject     text,
    title       text NOT NULL,
    explanation text NOT NULL,
    impact      text NOT NULL DEFAULT '',
    risk        text NOT NULL CHECK (risk IN ('advies', 'automatisch_veilig', 'toestemming_nodig')),
    confidence  real,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'dismissed', 'resolved')),
    change_id   bigint REFERENCES change_sets(id) ON DELETE SET NULL,
    dedupe_key  text UNIQUE,
    evidence    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS recommendations;
DROP TABLE IF EXISTS observations;
DROP TABLE IF EXISTS services;
DROP TABLE IF EXISTS sources;
DROP TABLE IF EXISTS download_outcomes;
