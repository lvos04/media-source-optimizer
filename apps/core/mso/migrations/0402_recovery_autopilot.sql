-- Slice D (1.5.0): Autopilot for failed downloads. New fallback statuses:
--   'retry'        server-related failure: SABnzbd retries the same job once the failing servers are off
--   'mg_requested' MediaGuard-owned item: MSO asked MediaGuard to search again via its official route
-- migrate:up
ALTER TABLE recovery_fallbacks DROP CONSTRAINT IF EXISTS recovery_fallbacks_status_check;
ALTER TABLE recovery_fallbacks ADD CONSTRAINT recovery_fallbacks_status_check
    CHECK (status IN ('waiting', 'skipped', 'no_candidate', 'proposed', 'sent', 'failed', 'discarded',
                      'retry', 'mg_requested'));

-- migrate:down
DELETE FROM recovery_fallbacks WHERE status IN ('retry', 'mg_requested');
ALTER TABLE recovery_fallbacks DROP CONSTRAINT IF EXISTS recovery_fallbacks_status_check;
ALTER TABLE recovery_fallbacks ADD CONSTRAINT recovery_fallbacks_status_check
    CHECK (status IN ('waiting', 'skipped', 'no_candidate', 'proposed', 'sent', 'failed', 'discarded'));
