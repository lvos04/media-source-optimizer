"""Append-only audit trail. Details are redacted before storage."""

from __future__ import annotations

import json
from typing import Any

from mso.db import get_db, jsonb
from mso.security import redact


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            lk = str(k).lower()
            if any(s in lk for s in ("password", "apikey", "api_key", "token", "secret", "passkey", "cookie")):
                out[k] = "***" if v not in (None, "") else v
            else:
                out[k] = _clean(v)
        return out
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value


def audit(actor: str, action: str, summary: str, *, target: str | None = None,
          detail: dict[str, Any] | None = None, change_id: int | None = None) -> int:
    detail = _clean(detail or {})
    json.dumps(detail, default=str)  # fail early on unserialisable detail
    return int(get_db().scalar(
        "INSERT INTO audit_log(actor,action,target,summary,detail,change_id) VALUES (%s,%s,%s,%s,%s,%s) "
        "RETURNING id", (actor, action, target, redact(summary), jsonb(detail), change_id)))
