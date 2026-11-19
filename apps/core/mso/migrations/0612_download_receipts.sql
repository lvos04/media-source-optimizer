-- migrate:up
-- Een oude verzending verraadt niet welke instantie destijds op het adres luisterde.
-- Laat ontbrekend bewijs leeg. De huidige koppeling mag het nooit achteraf invullen.
ALTER TABLE recovery_sends ADD COLUMN command jsonb;
ALTER TABLE search_sends ADD COLUMN client_instance text;
ALTER TABLE usenet_retries ADD COLUMN client_instance text;
ALTER TABLE torrent_actions ADD COLUMN client_instance text;
CREATE TABLE sab_send_receipts (
    id bigserial PRIMARY KEY,
    instance text NOT NULL,
    nzo_id text NOT NULL,
    category text NOT NULL,
    sha256 text NOT NULL,
    request_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sab_send_receipts_content ON sab_send_receipts (sha256, created_at DESC);

-- migrate:down
DROP TABLE sab_send_receipts;
ALTER TABLE torrent_actions DROP COLUMN client_instance;
ALTER TABLE usenet_retries DROP COLUMN client_instance;
ALTER TABLE search_sends DROP COLUMN client_instance;
ALTER TABLE recovery_sends DROP COLUMN command;
