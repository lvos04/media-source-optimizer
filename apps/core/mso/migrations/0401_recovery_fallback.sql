-- Slice D (1.4.0): failed download → fallback candidate → change set "Versturen naar SABnzbd/qBittorrent".
-- One row per failed download_outcome that the fallback job looked at: how it was mapped to a wanted item,
-- why it was skipped (if so), and which change set proposes the replacement release.
-- migrate:up
CREATE TABLE recovery_fallbacks (
    id                    bigserial PRIMARY KEY,
    outcome_id            bigint NOT NULL UNIQUE,
    failed_release_key    text,
    failed_title          text NOT NULL,
    protocol              text,
    failure_reason        text,
    media_ref             text,                        -- 'mg:<id>' | 'manual:<id>' | NULL (not mappable)
    mapping               text,                        -- send|mediaguard_ids|mediaguard_title|outcome_meta
    status                text NOT NULL CHECK (status IN ('waiting', 'skipped', 'no_candidate', 'proposed',
                                                          'sent', 'failed', 'discarded')),
    reason                text,                        -- Dutch explanation (skip reason, failure)
    change_id             bigint,
    candidate_release_key text,
    candidate_title       text,
    detail                jsonb NOT NULL DEFAULT '{}'::jsonb,
    next_attempt_at       timestamptz,                 -- status 'waiting': earliest retry (backoff/quota)
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recovery_fallbacks_ts_idx ON recovery_fallbacks(created_at DESC);
CREATE INDEX recovery_fallbacks_change_idx ON recovery_fallbacks(change_id);

-- migrate:down
DROP TABLE IF EXISTS recovery_fallbacks;
