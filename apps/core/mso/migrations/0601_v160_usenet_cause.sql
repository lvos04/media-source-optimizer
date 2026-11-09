-- 1.6.0 WP-A: Usenet — the real cause, and retrying only when it can help.
-- usenet_server_stats.articles_success: SABnzbd's server_stats field `articles_success` holds the number of FAILED
--   articles (sabnzbd/api.py _api_server_stats → BPSMeter.amounts → article_stats_failed). Up to 1.5.0 MSO stored
--   that number as successes ("Eweka 0,4 % gevonden" was really 99,6 %). The ingest now stores tried − failed; this
--   corrects the rows already stored. The formula is its own inverse, so the down section restores 1.5.0's values.
-- usenet_server_coverage: per SABnzbd server when MSO first saw it and first saw it enabled. A server that appears
--   (enabled) after MSO's first observation is a coverage gain (gain_at) — one of the two moments a retry of a failed
--   download can help (the other: usenet_server_autoheal.recovered_at). Servers present at the first observation are
--   the baseline (gain_at NULL), as are servers MSO itself switched off and on again.
-- migrate:up
UPDATE usenet_server_stats
   SET articles_success = GREATEST(articles_tried - articles_success, 0)
 WHERE articles_tried IS NOT NULL AND articles_success IS NOT NULL;

CREATE TABLE usenet_server_coverage (
    server           text PRIMARY KEY,                     -- SABnzbd server name (get_config 'name')
    displayname      text,                                 -- SABnzbd display name (last observed)
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    first_enabled_at timestamptz,                          -- first time MSO saw it enabled (NULL = never)
    gain_at          timestamptz,                          -- counted as coverage gain (NULL = baseline / MSO's own)
    enabled          boolean NOT NULL DEFAULT false,       -- last observed
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS usenet_server_coverage;
UPDATE usenet_server_stats
   SET articles_success = GREATEST(articles_tried - articles_success, 0)
 WHERE articles_tried IS NOT NULL AND articles_success IS NOT NULL;
