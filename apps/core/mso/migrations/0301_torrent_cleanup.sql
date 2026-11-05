-- Slice C (1.4.0): malware cleanup previews. One row per previewed set of suspicious torrents; the token
-- (sha256 of scope + sorted hashes, 16 hex) is part of every op path of the cleanup change set, so the applier
-- can verify at apply time that it only deletes torrents that were in this preview.
-- migrate:up
CREATE TABLE torrent_cleanup_previews (
    token       text PRIMARY KEY,
    scope       text NOT NULL CHECK (scope IN ('all', 'selection')),
    hashes      jsonb NOT NULL,                     -- sorted list of infohashes shown in the preview
    created_by  text NOT NULL DEFAULT 'system',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS torrent_cleanup_previews;
