-- Slice B: provider "test" results recorded before 1.3.0 while MSO had no credentials were stored as
-- status 'auth_failed' with detail "Gebruikersnaam of wachtwoord ontbreekt" (nothing was actually tested).
-- Convert them to 'missing_credentials' so the UI and the per-server issues no longer report a refused login.
-- migrate:up
UPDATE usenet_providers
   SET status = 'missing_credentials',
       status_detail = 'Geen gebruikersnaam/wachtwoord in MSO ingesteld (SABnzbd geeft wachtwoorden niet door). '
                       'Vul ze in via Bewerken om MSO zelf te laten testen; tot die tijd toont MSO de status die '
                       'SABnzbd meldt.',
       updated_at = now()
 WHERE status = 'auth_failed'
   AND status_detail IN ('Gebruikersnaam of wachtwoord ontbreekt', 'Gebruikersnaam ontbreekt');

-- migrate:down
-- Restores the pre-1.3.0 representation for rows that look like the ones up converted: no credentials and a
-- last (failed) check after the last successful one. Pre-1.3.0 code knows no 'missing_credentials' status.
UPDATE usenet_providers
   SET status = 'auth_failed',
       status_detail = 'Gebruikersnaam of wachtwoord ontbreekt'
 WHERE status = 'missing_credentials'
   AND last_check_at IS NOT NULL
   AND (last_ok_at IS NULL OR last_ok_at < last_check_at);
