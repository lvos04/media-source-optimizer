-- migrate:up
ALTER TABLE change_sets
    ADD COLUMN preview_hash text,
    ADD COLUMN approval_preview_hash text,
    ADD COLUMN approval_expires_at timestamptz,
    ADD COLUMN apply_request_id uuid,
    ADD COLUMN rollback_request_id uuid;

CREATE TABLE connector_idempotency (
    request_id uuid PRIMARY KEY,
    change_id bigint NOT NULL,
    route text NOT NULL,
    actor text NOT NULL,
    body_hash text NOT NULL,
    nonce uuid UNIQUE,
    approval_nonce uuid UNIQUE,
    response jsonb
);

-- migrate:down
DROP TABLE connector_idempotency;
ALTER TABLE change_sets
    DROP COLUMN rollback_request_id,
    DROP COLUMN apply_request_id,
    DROP COLUMN approval_expires_at,
    DROP COLUMN approval_preview_hash,
    DROP COLUMN preview_hash;
