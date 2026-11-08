-- 1.6.0 WP-G1 (C35): "Voetafdruk" — what MSO changed in your systems right now.
-- One row per (target, path) MSO set through a reversible applier and that is not rolled back: the original value
-- (before MSO's first change in the chain), MSO's value (its latest change), the value found at the source by the
-- hourly read-only job ``automation.footprint`` and the resulting status:
--   actief       MSO's value is still in effect
--   gewijzigd    changed outside MSO (the owner's choice; an override is recorded, MSO does not re-apply it)
--   onbereikbaar the source could not be read (last known value kept)
--   verdwenen    the object no longer exists (e.g. torrent or server removed)
-- migrate:up
CREATE TABLE change_footprint (
    target          text NOT NULL,
    path            text NOT NULL,
    label           text NOT NULL DEFAULT '',
    change_id       bigint,                      -- MSO's latest change that set mso_value
    change_ids      jsonb NOT NULL DEFAULT '[]', -- every change in the chain (oldest first)
    applied_at      timestamptz,
    applied_by      text,
    original_value  jsonb,
    mso_value       jsonb,
    current_value   jsonb,
    status          text NOT NULL CHECK (status IN ('actief', 'gewijzigd', 'onbereikbaar', 'verdwenen')),
    detail          text,
    checked_at      timestamptz NOT NULL DEFAULT now(),
    status_since    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (target, path)
);
CREATE INDEX change_footprint_status ON change_footprint (status);

-- migrate:down
DROP TABLE IF EXISTS change_footprint;
