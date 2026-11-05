-- Slice E: automatic import of providers (SABnzbd servers / Usenet providers) and indexers (Prowlarr) as cost
-- services. One service per source (idempotent); imported rows start with price_missing=true until the user fills
-- in price/period/renewal. A user who removes an imported service from the overview is remembered in
-- cost_import_skips so the next import does not bring it back.
-- migrate:up
ALTER TABLE services ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'manual'
    CHECK (origin IN ('manual', 'import'));
ALTER TABLE services ADD COLUMN IF NOT EXISTS price_missing boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS services_source_idx ON services(source_id);

CREATE TABLE cost_import_skips (
    source_id  text PRIMARY KEY,
    label      text NOT NULL DEFAULT '',
    skipped_by text,
    skipped_at timestamptz NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS cost_import_skips;
DROP INDEX IF EXISTS services_source_idx;
ALTER TABLE services DROP COLUMN IF EXISTS price_missing;
ALTER TABLE services DROP COLUMN IF EXISTS origin;
