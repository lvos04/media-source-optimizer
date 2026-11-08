-- 1.6.0 WP-B: test SABnzbd servers with SABnzbd's own server test instead of switching them on blindly, and the
-- account data (expiry date, quota) SABnzbd keeps per server.
-- usenet_server_autoheal: next SABnzbd server test of a switched-off server (next_test_at), failed tests in a row
--   (tests_failed), a keyed fingerprint of the SABnzbd server config when MSO last looked (config_fp; a change means
--   the owner edited the server in SABnzbd → test right away; no username is stored) and SABnzbd's usage counters
--   when a re-enable started (probe_usage: {"bytes", "tried"}; "werkt weer" only once SABnzbd really used it).
-- usenet_server_tests: every SABnzbd server test MSO ran (bounded: 30 days, at most 100 per server).
-- usenet_server_accounts: per SABnzbd server the account data its get_config shows (never credentials): the last
--   expiry date seen (SABnzbd clears expire_date on the day itself), quota and usage_at_start.
-- usenet_providers.block_baseline_bytes/_at: SABnzbd's byte total for the linked server when the block size was set
--   (block usage = total - baseline instead of SABnzbd's all-time counter).
-- migrate:up
ALTER TABLE usenet_server_autoheal
    ADD COLUMN IF NOT EXISTS next_test_at timestamptz,
    ADD COLUMN IF NOT EXISTS tests_failed integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS config_fp    text,
    ADD COLUMN IF NOT EXISTS probe_usage  jsonb;

CREATE TABLE usenet_server_tests (
    id      bigserial PRIMARY KEY,
    server  text NOT NULL,                 -- SABnzbd server name (get_config 'name')
    result  text NOT NULL,                 -- ok|auth_failed|account_expired|access_denied|block_exhausted|rate_limited|
                                           -- unreachable|tls_error|unknown|unsupported
    message text,                          -- SABnzbd's answer (redacted, username removed)
    trigger text NOT NULL,                 -- schedule|user|confirm|config_changed|own_test
    at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX usenet_server_tests_server_at_idx ON usenet_server_tests (server, at DESC);

CREATE TABLE usenet_server_accounts (
    server         text PRIMARY KEY,
    expire_date    date,
    quota_bytes    bigint,
    usage_at_start bigint,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE usenet_providers
    ADD COLUMN IF NOT EXISTS block_baseline_bytes bigint,
    ADD COLUMN IF NOT EXISTS block_baseline_at    timestamptz;

-- migrate:down
ALTER TABLE usenet_providers
    DROP COLUMN IF EXISTS block_baseline_at,
    DROP COLUMN IF EXISTS block_baseline_bytes;
DROP TABLE IF EXISTS usenet_server_accounts;
DROP TABLE IF EXISTS usenet_server_tests;
ALTER TABLE usenet_server_autoheal
    DROP COLUMN IF EXISTS probe_usage,
    DROP COLUMN IF EXISTS config_fp,
    DROP COLUMN IF EXISTS tests_failed,
    DROP COLUMN IF EXISTS next_test_at;
