-- 1.6.0 WP-E2 maintenance: retention (hourly rollup of old observations, indexes for the daily clean-up) and
-- background connector monitoring.
-- observations_hourly: one row per (hour, subject, metric, labels) for raw observations older than the raw retention
--   window (min / max / avg / median / sum / count); kept 400 days by default.
-- connectors.status_since: when the current status started (e.g. "offline since 2 d 4 h");
--   last_ok_at: last successful background call or test; failure_kind: auth | offline | timeout (NULL when healthy).
-- jobs.connector_id: the connector a failed job's error is attributed to (an outage, not a bug in the job), so the
--   overview shows "qBittorrent onbereikbaar sinds …" instead of hundreds of failed jobs.
-- migrate:up
CREATE TABLE observations_hourly (
    hour      timestamptz NOT NULL,
    subject   text NOT NULL,
    metric    text NOT NULL,
    labels    jsonb NOT NULL DEFAULT '{}'::jsonb,
    n         integer NOT NULL DEFAULT 0,              -- raw rows (incl. NULL values)
    n_values  integer NOT NULL DEFAULT 0,              -- raw rows with a value
    min       double precision,
    max       double precision,
    avg       double precision,
    median    double precision,
    sum       double precision,
    PRIMARY KEY (subject, metric, hour, labels)
);
CREATE INDEX observations_hourly_hour_idx ON observations_hourly(hour);

CREATE INDEX IF NOT EXISTS observations_ts_idx ON observations(ts);
CREATE INDEX IF NOT EXISTS jobs_status_updated_idx ON jobs(status, updated_at);

ALTER TABLE connectors
    ADD COLUMN IF NOT EXISTS status_since timestamptz,
    ADD COLUMN IF NOT EXISTS last_ok_at   timestamptz,
    ADD COLUMN IF NOT EXISTS failure_kind text;
-- existing rows: the current status is at least as old as the last check
UPDATE connectors SET status_since = last_checked_at WHERE status_since IS NULL AND last_checked_at IS NOT NULL;
UPDATE connectors SET last_ok_at = last_checked_at WHERE last_ok_at IS NULL AND status = 'healthy';

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS connector_id text;

-- migrate:down
ALTER TABLE jobs DROP COLUMN IF EXISTS connector_id;
ALTER TABLE connectors
    DROP COLUMN IF EXISTS failure_kind,
    DROP COLUMN IF EXISTS last_ok_at,
    DROP COLUMN IF EXISTS status_since;
DROP INDEX IF EXISTS jobs_status_updated_idx;
DROP INDEX IF EXISTS observations_ts_idx;
DROP TABLE IF EXISTS observations_hourly;
