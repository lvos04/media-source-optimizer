-- Dagmetingen van MSO's eigen opslag. De kalenderdag wordt bepaald uit de databaseklok in UTC.
-- migrate:up
CREATE TABLE resource_samples (
    day date PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    data jsonb NOT NULL,
    CHECK (day = (observed_at AT TIME ZONE 'UTC')::date)
);

-- migrate:down
DROP TABLE resource_samples;
