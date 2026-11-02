-- Slice D: MediaGuard integration (read-only cache, search requests) and recovery (blacklist, backoff,
-- manual wanted items, candidates, manual sends). MediaGuard stays authoritative: the cache is never a
-- source of truth and has a TTL.
-- migrate:up

-- Read-only cache of MediaGuard wanted items (GET /api/v1/wanted). Never authoritative.
CREATE TABLE mg_wanted_cache (
    mg_id            bigint PRIMARY KEY,              -- MediaGuard wanted item id (API field "id")
    instance_id      text NOT NULL,
    media_type       text,                            -- MediaGuard: tv|anime|movie|anime_movie
    media_category   text CHECK (media_category IN ('tv', 'tv-anime', 'movies', 'movies-anime')),
    kind             text NOT NULL,                   -- episode|movie
    reason           text,                            -- missing|cutoff
    title            text NOT NULL,
    item_title       text,
    item_provider_id bigint,
    provider_id      bigint,
    season_number    integer,
    episode_number   integer,
    absolute_episode_number integer,
    air_date_utc     text,
    status           text,
    attempts         integer,
    last_searched_at text,
    last_reason_code text,
    raw              jsonb NOT NULL DEFAULT '{}'::jsonb,
    fetched_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL
);
CREATE INDEX mg_wanted_cache_cat_idx ON mg_wanted_cache(media_category);

-- Small snapshots of MediaGuard read endpoints: kpis, reasons, quota, capabilities, health, sync state.
CREATE TABLE mg_snapshots (
    key         text PRIMARY KEY,
    data        jsonb NOT NULL DEFAULT '{}'::jsonb,
    etag        text,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz
);

-- Every "request search" (POST /api/v1/wanted/{id}/search) MSO sent or refused. Idempotency per item+window.
CREATE TABLE mg_search_requests (
    id               bigserial PRIMARY KEY,
    mg_id            bigint NOT NULL,
    instance_id      text,
    idempotency_key  text NOT NULL UNIQUE,
    trigger          text NOT NULL CHECK (trigger IN ('manual', 'auto')),
    status           text NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'refused_quota',
                                                     'refused_mediaguard_quota')),
    actor            text NOT NULL,
    http_status      integer,
    response         jsonb NOT NULL DEFAULT '{}'::jsonb,
    error            text,
    requested_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mg_search_requests_ts_idx ON mg_search_requests(requested_at DESC);
CREATE INDEX mg_search_requests_item_idx ON mg_search_requests(mg_id, requested_at DESC);

-- Exponential backoff state per MediaGuard wanted item for repeated failed search requests.
CREATE TABLE mg_search_backoff (
    mg_id           bigint PRIMARY KEY,
    failures        integer NOT NULL DEFAULT 0,
    last_failure_at timestamptz,
    next_allowed_at timestamptz
);

-- Failed releases (NZB id / message-id / infohash). TTL doubles per repeat; after the hard cap the entry
-- becomes permanent (expires_at NULL) so the same release is never retried endlessly.
CREATE TABLE recovery_blacklist (
    id           bigserial PRIMARY KEY,
    release_key  text NOT NULL UNIQUE,
    media_ref    text,                                -- 'mg:<id>' | 'manual:<id>' | free text
    protocol     text CHECK (protocol IN ('usenet', 'torrent')),
    reason       text NOT NULL,
    failures     integer NOT NULL DEFAULT 1,
    permanent    boolean NOT NULL DEFAULT false,
    created_by   text NOT NULL DEFAULT 'system',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz                          -- NULL = permanent (hard cap reached)
);
CREATE INDEX recovery_blacklist_media_idx ON recovery_blacklist(media_ref);

-- Exponential backoff per media item (repeated failed downloads for the same episode/movie).
CREATE TABLE recovery_media_backoff (
    media_ref       text PRIMARY KEY,
    failures        integer NOT NULL DEFAULT 0,
    last_failure_at timestamptz,
    next_allowed_at timestamptz
);

-- User-entered wanted items for standalone mode (no MediaGuard).
CREATE TABLE recovery_manual_wanted (
    id               bigserial PRIMARY KEY,
    title            text NOT NULL,
    media_category   text NOT NULL CHECK (media_category IN ('tv', 'tv-anime', 'movies', 'movies-anime')),
    season_number    integer,
    episode_number   integer,
    absolute_episode_number integer,
    year             integer,
    imdb_id          text,
    tvdb_id          bigint,
    tmdb_id          bigint,
    requirements     jsonb NOT NULL DEFAULT '{}'::jsonb,
    status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'archived')),
    created_by       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Last evaluated fallback candidates per media item (server-side, so a send can only use a release that
-- MSO itself evaluated; the client can never inject release data that bypasses the quality gate).
CREATE TABLE recovery_candidates (
    id            bigserial PRIMARY KEY,
    media_ref     text NOT NULL,
    release_key   text NOT NULL,
    protocol      text NOT NULL,
    title         text NOT NULL,
    source_id     text,
    passed        boolean NOT NULL,
    score         real,
    reasons       jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence      jsonb NOT NULL DEFAULT '[]'::jsonb,
    release       jsonb NOT NULL,
    requirements  jsonb NOT NULL DEFAULT '{}'::jsonb,
    computed_at   timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    UNIQUE (media_ref, release_key)
);

-- Manual sends to SABnzbd/qBittorrent (idempotent per media item + release).
CREATE TABLE recovery_sends (
    id               bigserial PRIMARY KEY,
    idempotency_key  text NOT NULL UNIQUE,
    media_ref        text NOT NULL,
    release_key      text NOT NULL,
    protocol         text NOT NULL,
    client           text NOT NULL,
    client_category  text,
    client_ref       text,
    title            text NOT NULL,
    status           text NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'download_failed',
                                                     'download_ok')),
    error            text,
    actor            text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recovery_sends_ts_idx ON recovery_sends(created_at DESC);

-- Failed download_outcomes already turned into blacklist entries (so a re-observed failure is not counted
-- twice towards the hard cap).
CREATE TABLE recovery_outcome_seen (
    outcome_id   bigint PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS recovery_outcome_seen;
DROP TABLE IF EXISTS recovery_sends;
DROP TABLE IF EXISTS recovery_candidates;
DROP TABLE IF EXISTS recovery_manual_wanted;
DROP TABLE IF EXISTS recovery_media_backoff;
DROP TABLE IF EXISTS recovery_blacklist;
DROP TABLE IF EXISTS mg_search_backoff;
DROP TABLE IF EXISTS mg_search_requests;
DROP TABLE IF EXISTS mg_snapshots;
DROP TABLE IF EXISTS mg_wanted_cache;
