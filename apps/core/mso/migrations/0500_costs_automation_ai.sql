-- Slice E: costs (backbone field), automation run log, AI advisor usage + per-model prices.
-- migrate:up
ALTER TABLE services ADD COLUMN IF NOT EXISTS backbone text;  -- user-entered; NULL = onbekend

-- Every unattended automation decision (apply / skip / evaluate) for rate caps and the UI log.
CREATE TABLE automation_log (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    kind       text NOT NULL CHECK (kind IN ('apply', 'evaluate', 'run')),
    change_id  bigint REFERENCES change_sets(id) ON DELETE SET NULL,
    area       text,
    result     text NOT NULL,
    message    text NOT NULL DEFAULT ''
);
CREATE INDEX automation_log_ts_idx ON automation_log(ts DESC);

-- User-editable token prices (cents per million tokens) per model; used for budget enforcement.
CREATE TABLE ai_model_prices (
    model                    text PRIMARY KEY,
    input_cents_per_mtok     integer NOT NULL CHECK (input_cents_per_mtok >= 0),
    output_cents_per_mtok    integer NOT NULL CHECK (output_cents_per_mtok >= 0),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    updated_by               text
);
INSERT INTO ai_model_prices(model, input_cents_per_mtok, output_cents_per_mtok, updated_by) VALUES
    ('claude-sonnet-5-5', 200, 1000, 'standaard'),
    ('claude-opus-5-5', 400, 2000, 'standaard'),
    ('claude-haiku-5-5', 10, 50, 'standaard');

-- One row per AI advisor attempt (also refused/failed ones, cost 0) — the monthly budget is summed here.
CREATE TABLE ai_usage (
    id            bigserial PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    model         text NOT NULL,
    purpose       text NOT NULL,
    status        text NOT NULL CHECK (status IN ('ok', 'error', 'refused_budget', 'refused_model')),
    input_tokens  integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_microcents bigint NOT NULL DEFAULT 0,       -- 1 cent = 1e6 microcents (exact for small calls)
    error         text,
    actor         text
);
CREATE INDEX ai_usage_ts_idx ON ai_usage(ts DESC);

-- migrate:down
DROP TABLE IF EXISTS ai_usage;
DROP TABLE IF EXISTS ai_model_prices;
DROP TABLE IF EXISTS automation_log;
ALTER TABLE services DROP COLUMN IF EXISTS backbone;
