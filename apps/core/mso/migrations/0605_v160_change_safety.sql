-- 1.6.0 WP-E1: core safety of changes and Autopilot.
-- change_sets.status 'applying': written (with observed_before per op) BEFORE the first external write, so an apply
--   that is cut off by a restart can be found and finished or undone by the reconciler; part of the open dedupe set.
-- change_sets.apply_started_at: when the (last) apply started; the reconciler's grace period runs from here.
-- change_sets.applied_auto: applied unattended (Autopilot / automatisch veilig) — "Autopilot terugdraaien".
-- automation_failures: cooldown ledger for automatic applies that failed (1 h → 4 h → 24 h, parked after 3).
-- migrate:up
ALTER TABLE change_sets DROP CONSTRAINT IF EXISTS change_sets_status_check;
ALTER TABLE change_sets ADD CONSTRAINT change_sets_status_check
    CHECK (status IN ('preview', 'approved', 'applying', 'applied', 'rolled_back', 'failed', 'discarded'));
DROP INDEX IF EXISTS change_sets_dedupe_open_idx;
CREATE UNIQUE INDEX change_sets_dedupe_open_idx ON change_sets(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('preview', 'approved', 'applying');
ALTER TABLE change_sets
    ADD COLUMN IF NOT EXISTS apply_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS applied_auto     boolean NOT NULL DEFAULT false;
-- 1.5.0 applied unattended changes as these actors (automation engine, usenet fallback, qBittorrent autotune)
UPDATE change_sets SET applied_auto = true
 WHERE applied_by IN ('automatisering', 'autopilot', 'autotune') AND applied_at IS NOT NULL;

CREATE TABLE automation_failures (
    key             text PRIMARY KEY,               -- change dedupe_key, or 'fp:<fingerprint>' without one
    fingerprint     text NOT NULL,                  -- target + ops (path/before/after): a changed preview resets
    target          text NOT NULL,
    title           text NOT NULL,
    area            text,
    change_id       bigint,                         -- last change that failed
    failures        integer NOT NULL DEFAULT 0,
    last_error      text,
    last_failed_at  timestamptz NOT NULL DEFAULT now(),
    next_attempt_at timestamptz NOT NULL,
    parked          boolean NOT NULL DEFAULT false, -- after 3 failures: one "Wacht op jou" item, one attempt a day
    rec_id          bigint                          -- that recommendation
);
CREATE INDEX automation_failures_next_idx ON automation_failures(next_attempt_at);

-- migrate:down
DROP TABLE IF EXISTS automation_failures;
-- an apply still marked 'applying' cannot be represented in 1.5.0: record it as failed (ops keep their results)
UPDATE change_sets SET status = 'failed',
       error = COALESCE(error || ' · ', '') || 'onderbroken tijdens toepassen (teruggezet naar een oudere versie)'
 WHERE status = 'applying';
DROP INDEX IF EXISTS change_sets_dedupe_open_idx;
CREATE UNIQUE INDEX change_sets_dedupe_open_idx ON change_sets(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('preview', 'approved');
ALTER TABLE change_sets DROP CONSTRAINT IF EXISTS change_sets_status_check;
ALTER TABLE change_sets ADD CONSTRAINT change_sets_status_check
    CHECK (status IN ('preview', 'approved', 'applied', 'rolled_back', 'failed', 'discarded'));
ALTER TABLE change_sets
    DROP COLUMN IF EXISTS applied_auto,
    DROP COLUMN IF EXISTS apply_started_at;
