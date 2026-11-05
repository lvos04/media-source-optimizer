-- Slice B: automatic STAT sample after every NZB MSO sends (trigger 'auto_send') and the per-provider daily
-- usage counters that enforce the daily/monthly STAT budgets (connections, commands, bytes).
-- migrate:up
ALTER TABLE usenet_article_tests DROP CONSTRAINT IF EXISTS usenet_article_tests_trigger_check;
ALTER TABLE usenet_article_tests ADD CONSTRAINT usenet_article_tests_trigger_check
    CHECK (trigger IN ('failure', 'manual', 'auto_send'));

-- One row per provider per day. Monthly usage = sum over the calendar month.
CREATE TABLE usenet_stat_usage (
    day          date NOT NULL,
    provider     text NOT NULL,                  -- usenet_providers.slug
    connections  integer NOT NULL DEFAULT 0,
    commands     bigint NOT NULL DEFAULT 0,       -- NNTP commands sent (STAT + login/quit)
    bytes        bigint NOT NULL DEFAULT 0,       -- bytes sent + received on those connections
    PRIMARY KEY (day, provider)
);

-- migrate:down
DROP TABLE IF EXISTS usenet_stat_usage;
DELETE FROM usenet_article_tests WHERE trigger = 'auto_send';
ALTER TABLE usenet_article_tests DROP CONSTRAINT IF EXISTS usenet_article_tests_trigger_check;
ALTER TABLE usenet_article_tests ADD CONSTRAINT usenet_article_tests_trigger_check
    CHECK (trigger IN ('failure', 'manual'));
