-- 1.6.0 base: shared interfaces between the 1.6.0 work packages (see docs/dev/PLAN-1.6.0.md).
-- usenet_server_autoheal: when a switched-off server was confirmed working again (recovered_at, used by the retry
--   trigger "coverage improved") and the last SABnzbd server test (last_test_at / last_test_result).
-- change_overrides: values MSO set that the owner changed back outside MSO; while `until` is in the future MSO
--   never re-applies that path automatically (it may still propose it once).
-- migrate:up
ALTER TABLE usenet_server_autoheal
    ADD COLUMN IF NOT EXISTS recovered_at     timestamptz,
    ADD COLUMN IF NOT EXISTS last_test_at     timestamptz,
    ADD COLUMN IF NOT EXISTS last_test_result text;

CREATE TABLE change_overrides (
    target        text NOT NULL,                 -- change_sets.target, e.g. sabnzbd / qbittorrent
    path          text NOT NULL,                 -- op path within that target
    mso_value     jsonb,                         -- the value MSO had set
    current_value jsonb,                         -- the value found at the source
    change_id     bigint,                        -- the MSO change that set mso_value
    detected_at   timestamptz NOT NULL DEFAULT now(),
    until         timestamptz NOT NULL,
    PRIMARY KEY (target, path)
);

-- migrate:down
DROP TABLE IF EXISTS change_overrides;
ALTER TABLE usenet_server_autoheal
    DROP COLUMN IF EXISTS last_test_result,
    DROP COLUMN IF EXISTS last_test_at,
    DROP COLUMN IF EXISTS recovered_at;
