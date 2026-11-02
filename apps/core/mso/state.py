"""Operating mode (read_only | manage), automation pause flag, setup completion."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from mso.db import Database, get_db, jsonb


def get_state(key: str, default: Any = None, db: Database | None = None) -> Any:
    row = (db or get_db()).fetchone("SELECT value FROM system_state WHERE key=%s", (key,))
    return default if row is None else row["value"]


def set_state(key: str, value: Any, db: Database | None = None) -> None:
    (db or get_db()).execute(
        "INSERT INTO system_state(key,value,updated_at) VALUES (%s,%s,now()) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, jsonb(value)))


def mode() -> str:
    return str(get_state("mode", "read_only"))


def paused() -> bool:
    return bool(get_state("paused", False))


def require_manage_mode() -> None:
    """External writes are only allowed in beheermodus (manage). Read-only is the default."""
    if mode() != "manage":
        raise HTTPException(409, detail="De app staat in alleen-lezen modus; schakel beheermodus in om "
                                        "wijzigingen toe te passen")


def automation_allowed() -> bool:
    """Automatic (unattended) safe changes: only in manage mode and when not paused."""
    return mode() == "manage" and not paused()
