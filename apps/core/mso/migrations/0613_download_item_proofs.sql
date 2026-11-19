-- migrate:up
-- De verzendende installatie gebruikt voor preview en directe verzending dezelfde openbare identiteit.
CREATE TABLE download_receipt_origin (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    marker uuid NOT NULL DEFAULT gen_random_uuid()
);
INSERT INTO download_receipt_origin DEFAULT VALUES;

-- Dezelfde kandidaat houdt bij preview en verzending hetzelfde openbare ontvangstmerk.
ALTER TABLE recovery_candidates ADD COLUMN receipt_id uuid NOT NULL DEFAULT gen_random_uuid();

-- Eén eigenaar per doel. Een tweede ontvangst op hetzelfde ID maakt het eerste bewijs ook onbruikbaar.
CREATE TABLE download_receipt_targets (
    client text NOT NULL,
    instance text NOT NULL,
    download_id text NOT NULL,
    owner text,
    proof jsonb,
    disputed boolean NOT NULL DEFAULT false,
    PRIMARY KEY (client, instance, download_id)
);

-- Behoud oude waarnemingen. De huidige dienst kan achteraf geen bewijs van verzending leveren.
INSERT INTO download_receipt_targets (client, instance, download_id, disputed)
SELECT 'sabnzbd', instance, nzo_id, count(DISTINCT (category, sha256, request_hash)) > 1
FROM sab_send_receipts GROUP BY instance, nzo_id;

INSERT INTO download_receipt_targets (client, instance, download_id, disputed)
SELECT client, instance, client_ref, count(*) > 1 FROM (
    SELECT client, command->>'instance' AS instance, client_ref FROM recovery_sends
    WHERE command->>'instance' IS NOT NULL AND client_ref IS NOT NULL
    UNION ALL
    SELECT client, client_instance, client_ref FROM search_sends
    WHERE client_instance IS NOT NULL AND client_ref IS NOT NULL
) AS claims GROUP BY client, instance, client_ref
ON CONFLICT (client,instance,download_id) DO UPDATE
SET disputed=download_receipt_targets.disputed OR EXCLUDED.disputed;

CREATE INDEX sab_send_receipts_target ON sab_send_receipts (instance, nzo_id);

-- migrate:down
ALTER TABLE recovery_candidates DROP COLUMN receipt_id;
DROP INDEX sab_send_receipts_target;
DROP TABLE download_receipt_targets;
DROP TABLE download_receipt_origin;
