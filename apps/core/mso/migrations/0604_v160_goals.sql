-- 1.6.0 WP-D: honest failure measurement and the goals card.
-- download_outcomes.failed_at: the moment an outcome became 'failed' (set on the transition into 'failed', kept while
--   it stays failed, cleared when it leaves 'failed'). Outcome windows use COALESCE(finished_at, failed_at) instead of
--   COALESCE(finished_at, observed_at): observed_at is refreshed on every ingest, so a torrent stuck in the client
--   counted as "failed in the last 24 h" forever. Existing failed rows are backfilled from what is known: completion
--   time, else (torrents) the stall moment / first sighting / last progress, else start time, else last observation.
-- goal_snapshots: one row per local day with the goal values measured that day (suspicious torrents, failing and
--   MSO-disabled SABnzbd servers, failure ratios), upserted by the hourly job overview.goals_snapshot: the last run of
--   the day wins, so a row holds the end-of-day value (the trend). Start values are not taken from here: they are the
--   first hour of the trial, frozen in system_state['goals.start'].
-- migrate:up
ALTER TABLE download_outcomes ADD COLUMN IF NOT EXISTS failed_at timestamptz;

UPDATE download_outcomes o SET failed_at = COALESCE(
    o.finished_at,
    (SELECT LEAST(
                CASE
                    WHEN o.failure_reason IN ('stalled', 'tracker_error') AND t.last_progress_at IS NOT NULL THEN
                        t.last_progress_at + make_interval(hours => COALESCE(
                            (SELECT (s.value #>> '{}')::int FROM settings s WHERE s.key = 'torrent.stalled_fail_hours'),
                            72))
                    WHEN o.failure_reason = 'false_positive' THEN t.first_seen
                    ELSE COALESCE(t.last_progress_at, t.first_seen)
                END,
                o.observed_at)
       FROM torrent_state t
      WHERE o.protocol = 'torrent' AND (t.hash = o.client_ref OR t.hash = left(o.client_ref, 40))
      LIMIT 1),
    o.started_at,
    o.observed_at)
WHERE o.status = 'failed' AND o.failed_at IS NULL;

CREATE INDEX IF NOT EXISTS download_outcomes_outcome_at_idx
    ON download_outcomes ((COALESCE(finished_at, failed_at)));

CREATE TABLE IF NOT EXISTS goal_snapshots (
    day      date PRIMARY KEY,                       -- local day (Europe/Amsterdam or the configured TZ)
    metrics  jsonb NOT NULL DEFAULT '{}'::jsonb,     -- {failure_net, failure_net_n, failure_gross, failure_gross_n,
                                                     --  suspicious, failing_servers, disabled_servers}
    taken_at timestamptz NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS goal_snapshots;
DROP INDEX IF EXISTS download_outcomes_outcome_at_idx;
ALTER TABLE download_outcomes DROP COLUMN IF EXISTS failed_at;
