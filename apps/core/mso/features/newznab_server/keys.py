"""API keys of consumers of MSO's Newznab/Torznab endpoints.

- The default consumer ("Prowlarr", origin node ``prowlarr``) is generated on first use; its key lives in the
  vault as ``newznab.apikey``. Extra consumers get ``newznab.apikey.<id>``.
- Lookup is by SHA-256 hash (constant-time compare on the hash); the plaintext is only revealed to an admin
  through an audited endpoint.
- The consumer's ``node`` tells the origin graph *who* is calling (e.g. Prowlarr), which is how MSO avoids
  querying that same Prowlarr back.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any

from mso.config import get_settings
from mso.db import get_db
from mso.features.search import settings as S
from mso.vault import get_vault

DEFAULT_VAULT_NAME = "newznab.apikey"
NODE_RE = r"^[a-z0-9][a-z0-9:_-]{0,63}$"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _new_key() -> str:
    return secrets.token_hex(16)  # 32 hex chars, like Newznab/Prowlarr keys


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "label": row["label"],
        "node": row["node"],
        "key_hint": f"…{row['key_hint']}",
        "enabled": row["enabled"],
        "rate_per_min": row["rate_per_min"],
        "daily_quota": row["daily_quota"],
        "created_at": row["created_at"],
        "rotated_at": row["rotated_at"],
        "last_used_at": row["last_used_at"],
        "default": row["vault_name"] == DEFAULT_VAULT_NAME,
    }


def ensure_default_consumer() -> dict[str, Any]:
    """Generate the default (Prowlarr) consumer key on first use. Idempotent."""
    db = get_db()
    row = db.fetchone("SELECT * FROM newznab_consumers WHERE vault_name=%s", (DEFAULT_VAULT_NAME,))
    if row is not None:
        return row
    key = _new_key()
    with db.tx() as conn:
        row = conn.execute(
            "INSERT INTO newznab_consumers(label,node,key_hash,key_hint,vault_name) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (vault_name) DO NOTHING RETURNING *",
            ("Prowlarr", "prowlarr", _hash(key), key[-4:], DEFAULT_VAULT_NAME),
        ).fetchone()
    if row is None:  # lost a race
        return db.fetchone("SELECT * FROM newznab_consumers WHERE vault_name=%s", (DEFAULT_VAULT_NAME,))
    get_vault().set(DEFAULT_VAULT_NAME, key)
    return row


def create_consumer(label: str, node: str) -> dict[str, Any]:
    db = get_db()
    key = _new_key()
    with db.tx() as conn:
        row = conn.execute(
            "INSERT INTO newznab_consumers(label,node,key_hash,key_hint,vault_name) "
            "VALUES (%s,%s,%s,%s,'pending-' || md5(random()::text)) RETURNING *",
            (label, node, _hash(key), key[-4:]),
        ).fetchone()
        vname = f"{DEFAULT_VAULT_NAME}.{row['id']}"
        row = conn.execute(
            "UPDATE newznab_consumers SET vault_name=%s WHERE id=%s RETURNING *", (vname, row["id"])
        ).fetchone()
    get_vault().set(vname, key)
    return row


def rotate(consumer_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.fetchone("SELECT * FROM newznab_consumers WHERE id=%s", (consumer_id,))
    if row is None:
        return None
    key = _new_key()
    get_vault().set(row["vault_name"], key)
    return db.fetchone(
        "UPDATE newznab_consumers SET key_hash=%s, key_hint=%s, rotated_at=now() WHERE id=%s RETURNING *",
        (_hash(key), key[-4:], consumer_id),
    )


def reveal(consumer_id: int) -> str | None:
    row = get_db().fetchone("SELECT vault_name FROM newznab_consumers WHERE id=%s", (consumer_id,))
    return get_vault().get(row["vault_name"]) if row else None


def delete(consumer_id: int) -> bool:
    db = get_db()
    row = db.fetchone("SELECT vault_name FROM newznab_consumers WHERE id=%s", (consumer_id,))
    if row is None:
        return False
    get_vault().set(row["vault_name"], None)
    db.execute("DELETE FROM newznab_consumers WHERE id=%s", (consumer_id,))
    return True


def lookup(key: str | None) -> dict[str, Any] | None:
    if not key or len(key) > 128:
        return None
    h = _hash(key)
    row = get_db().fetchone("SELECT * FROM newznab_consumers WHERE key_hash=%s AND enabled", (h,))
    if row is None or not hmac.compare_digest(row["key_hash"], h):
        return None
    return row


def touch(consumer_id: int) -> None:
    get_db().execute(
        "UPDATE newznab_consumers SET last_used_at=%s WHERE id=%s", (datetime.now(UTC), consumer_id)
    )


def default_key() -> str:
    ensure_default_consumer()
    key = get_vault().get(DEFAULT_VAULT_NAME)
    if not key:  # vault lost/restored without secrets: re-generate
        row = get_db().fetchone("SELECT id FROM newznab_consumers WHERE vault_name=%s", (DEFAULT_VAULT_NAME,))
        rotate(int(row["id"]))
        key = get_vault().get(DEFAULT_VAULT_NAME)
    return str(key)


def public_base_url() -> str:
    """Base URL at which Prowlarr reaches MSO (setting, else MSO_PUBLIC_URL). Empty if unknown."""
    url = str(S.get("newznab.public_url", "") or "").strip().rstrip("/")
    return url or get_settings().public_url
