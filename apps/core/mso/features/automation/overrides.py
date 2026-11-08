"""Owner overrides (1.6.0): a value MSO set that the owner changed back outside MSO.

While an override is active (``until`` in the future) MSO never re-applies that ``(target, path)``
automatically; it may still create one proposal that waits for approval. The footprint job records
overrides; :func:`mso.features.automation.autopilot.why_not` honours them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mso.db import Database, get_db, jsonb


def active(target: str, path: str, db: Database | None = None) -> dict[str, Any] | None:
    """The active override for ``(target, path)``, or None."""
    return (db or get_db()).fetchone(
        "SELECT * FROM change_overrides WHERE target=%s AND path=%s AND until > now()", (target, path)
    )


def active_for_ops(target: str, ops: list[dict[str, Any]], db: Database | None = None) -> dict[str, Any] | None:
    """The first active override that covers one of ``ops`` (by path), or None."""
    db = db or get_db()
    for op in ops or []:
        path = op.get("path")
        if path:
            row = active(target, str(path), db)
            if row:
                return row
    return None


def record(target: str, path: str, *, mso_value: Any, current_value: Any, change_id: int | None, days: int,
           db: Database | None = None) -> bool:
    """Record (or refresh) an override for ``days`` days. Returns True when it is new (not yet active)."""
    db = db or get_db()
    was_active = active(target, path, db) is not None
    until = datetime.now(UTC) + timedelta(days=max(1, int(days)))
    db.execute(
        "INSERT INTO change_overrides(target,path,mso_value,current_value,change_id,until) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (target,path) DO UPDATE SET mso_value=EXCLUDED.mso_value, current_value=EXCLUDED.current_value, "
        "change_id=EXCLUDED.change_id, until=EXCLUDED.until, "
        "detected_at=CASE WHEN change_overrides.until > now() THEN change_overrides.detected_at ELSE now() END",
        (target, path, jsonb(mso_value), jsonb(current_value), change_id, until),
    )
    return not was_active


def clear(target: str, path: str, db: Database | None = None) -> None:
    (db or get_db()).execute("DELETE FROM change_overrides WHERE target=%s AND path=%s", (target, path))


def list_active(db: Database | None = None) -> list[dict[str, Any]]:
    return (db or get_db()).fetchall("SELECT * FROM change_overrides WHERE until > now() ORDER BY detected_at DESC")


__all__ = ["active", "active_for_ops", "clear", "list_active", "record"]
