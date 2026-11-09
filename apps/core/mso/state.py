"""Operating mode (read_only | manage | autopilot), automation pause flag, setup completion.

- read_only: nothing is written to external systems (default).
- manage: a person may apply approved changes; automation only for areas switched on.
- autopilot ("set it and forget it"): like manage, plus MSO applies every reversible, policy-bounded change by
  itself (tuning, switching failing servers off, retries, fallback searches) with checks and auto-rollback.
  Deleting (irreversible) is NEVER automatic in any mode: it always needs one approval.
- autopilot_hold (1.6.0, "noodrem"): MSO paused its OWN automatic writes after too many automatic rollbacks or
  failed automatic applies. Only unattended writes stop (as with a pause); syncs, checks and preparing proposals go
  on, and a person can still apply changes. Lifted only by an explicit "Hervatten" (operator+).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from mso.db import Database, get_db, jsonb

HOLD_KEY = "autopilot_hold"


def get_state(key: str, default: Any = None, db: Database | None = None) -> Any:
    row = (db or get_db()).fetchone("SELECT value FROM system_state WHERE key=%s", (key,))
    return default if row is None else row["value"]


def set_state(key: str, value: Any, db: Database | None = None) -> None:
    (db or get_db()).execute(
        "INSERT INTO system_state(key,value,updated_at) VALUES (%s,%s,now()) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, jsonb(value)))


def _flags() -> dict[str, Any]:
    """mode, paused and the hold in one query (these are checked before every automatic action)."""
    rows = get_db().fetchall("SELECT key, value FROM system_state WHERE key IN ('mode','paused',%s)", (HOLD_KEY,))
    return {r["key"]: r["value"] for r in rows}


def mode() -> str:
    return str(get_state("mode", "read_only"))


def paused() -> bool:
    return bool(get_state("paused", False))


def hold() -> dict[str, Any] | None:
    """The active self-imposed hold (noodrem) ``{active, since, reason, rollbacks, failures, …}``, or None."""
    value = get_state(HOLD_KEY, None)
    return value if isinstance(value, dict) and value.get("active") else None


def _held(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("active"))


def require_manage_mode() -> None:
    """External writes are only allowed in beheermodus (manage). Read-only is the default."""
    if mode() not in ("manage", "autopilot"):
        raise HTTPException(409, detail="De app staat in alleen-lezen modus; schakel beheermodus in om "
                                        "wijzigingen toe te passen")


def automation_allowed() -> bool:
    """Automatic (unattended) safe changes: only in manage mode, when not paused and not held by the noodrem."""
    f = _flags()
    return f.get("mode", "read_only") in ("manage", "autopilot") and not f.get("paused") and not _held(f.get(HOLD_KEY))


def autopilot() -> bool:
    """Set-it-and-forget-it: reversible changes (incl. switching things off) may be applied unattended."""
    f = _flags()
    return f.get("mode", "read_only") == "autopilot" and not f.get("paused") and not _held(f.get(HOLD_KEY))
