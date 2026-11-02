-- Slice A: direct indexers (Newznab/Torznab/Spotweb), search result cache, sends, MSO Newznab consumers,
-- explicit origin-graph edges (anti-loop) and per-day usage counters (quota).
-- migrate:up

-- User-configured external indexers. The API key lives only in the vault as 'indexer.<slug>.api_key'.
CREATE TABLE direct_indexers (
    slug                 text PRIMARY KEY CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
    kind                 text NOT NULL CHECK (kind IN ('newznab', 'torznab', 'spotweb')),
    label                text NOT NULL,
    url                  text NOT NULL,
    api_path             text NOT NULL DEFAULT '/api',
    enabled              boolean NOT NULL DEFAULT true,
    scopes               text[] NOT NULL DEFAULT ARRAY['normal','anime'],
    private              boolean NOT NULL DEFAULT false,
    rate_per_min         integer NOT NULL DEFAULT 30 CHECK (rate_per_min > 0),
    daily_quota          integer NOT NULL DEFAULT 0 CHECK (daily_quota >= 0),   -- 0 = no limit
    timeout_s            real NOT NULL DEFAULT 15 CHECK (timeout_s > 0),
    caps                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    caps_checked_at      timestamptz,
    status               text NOT NULL DEFAULT 'unknown',
    last_error           text,
    consecutive_failures integer NOT NULL DEFAULT 0,
    backoff_until        timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- Daily query/grab counters per source ('direct:<slug>', 'consumer:<id>') for quota enforcement.
CREATE TABLE indexer_usage (
    source_id text NOT NULL,
    day       date NOT NULL DEFAULT CURRENT_DATE,
    queries   integer NOT NULL DEFAULT 0,
    grabs     integer NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, day)
);

-- Consumers of MSO's own Newznab/Torznab endpoints (e.g. Prowlarr). Only a SHA-256 hash of the key is
-- stored here (lookup); the key itself is in the vault (reveal/rotate by admin, audited).
CREATE TABLE newznab_consumers (
    id           bigserial PRIMARY KEY,
    label        text NOT NULL,
    node         text NOT NULL DEFAULT 'external',     -- origin-graph node of the caller, e.g. 'prowlarr'
    key_hash     text NOT NULL UNIQUE,
    key_hint     text NOT NULL,                        -- last 4 characters, for recognition only
    vault_name   text NOT NULL UNIQUE,
    enabled      boolean NOT NULL DEFAULT true,
    rate_per_min integer,                              -- NULL = setting newznab.rate_per_min
    daily_quota  integer,                              -- NULL = setting newznab.daily_quota
    created_at   timestamptz NOT NULL DEFAULT now(),
    rotated_at   timestamptz,
    last_used_at timestamptz
);

-- Short-lived cache of search results so a chosen result can be sent / proxied (t=get) by opaque token
-- without ever exposing upstream URLs with credentials. Secret query parameters are stripped before
-- storage and re-injected from the vault at download time.
CREATE TABLE release_cache (
    token          text PRIMARY KEY,
    release_key    text NOT NULL,
    source_id      text NOT NULL,
    protocol       text NOT NULL CHECK (protocol IN ('usenet', 'torrent')),
    title          text NOT NULL,
    download_url   text,
    stripped_params text[] NOT NULL DEFAULT '{}',
    magnet         text,
    media_category text,
    private        boolean,
    data           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL
);
CREATE INDEX release_cache_key_idx ON release_cache(release_key);
CREATE INDEX release_cache_expiry_idx ON release_cache(expires_at);

-- User-initiated "send to download client" (idempotent per release_key).
CREATE TABLE search_sends (
    id          bigserial PRIMARY KEY,
    release_key text NOT NULL UNIQUE,
    client      text NOT NULL CHECK (client IN ('sabnzbd', 'qbittorrent')),
    category    text NOT NULL,
    title       text NOT NULL,
    source_id   text,
    status      text NOT NULL CHECK (status IN ('sending', 'sent', 'failed')),
    client_ref  text,
    error       text,
    actor       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Explicitly configured origin-graph edges (in addition to edges derived from configuration).
CREATE TABLE origin_edges (
    src        text NOT NULL,
    dst        text NOT NULL,
    note       text NOT NULL DEFAULT '',
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (src, dst),
    CHECK (src <> dst)
);

-- migrate:down
DROP TABLE IF EXISTS origin_edges;
DROP TABLE IF EXISTS search_sends;
DROP TABLE IF EXISTS release_cache;
DROP TABLE IF EXISTS newznab_consumers;
DROP TABLE IF EXISTS indexer_usage;
DROP TABLE IF EXISTS direct_indexers;
