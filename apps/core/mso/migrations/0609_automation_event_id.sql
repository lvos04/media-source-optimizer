-- migrate:up
-- Oude waarnemingen behouden de gepubliceerde identiteit op basis van bron, nummer en tijd.
ALTER TABLE automation_log ADD COLUMN event_uuid uuid;
ALTER TABLE automation_log ALTER COLUMN event_uuid SET DEFAULT gen_random_uuid();

-- Ook bij een herstel vóór deze upgrade kunnen oude cursors en kale rijnummers dubbelzinnig zijn.
INSERT INTO system_state(key,value)
SELECT 'connector_event_history', to_jsonb(gen_random_uuid())
WHERE EXISTS (SELECT 1 FROM audit_log WHERE action IN ('backup.restored','backup.machine_keys_restore_recovered'))
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now();

-- migrate:down
ALTER TABLE automation_log DROP COLUMN event_uuid;
