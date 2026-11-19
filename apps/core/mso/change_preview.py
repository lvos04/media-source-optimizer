"""Canonieke representatie van een voorvertoning en van gebonden aanvragen."""

import hashlib
import json
from typing import Any

JOURNAL_FIELDS = frozenset({
    "observed_before", "observed_after", "verified_at", "result", "restore_note", "restore_error",
    "rollback_note", "rollback_error", "skip_reason", "current", "checked_at",
})


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def preview_hash(target: str, risk: str, ops: list[dict[str, Any]]) -> str:
    return digest({"target": target, "risk": risk,
                   "ops": [{key: value for key, value in op.items() if key not in JOURNAL_FIELDS} for op in ops]})


def public_change(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"apply_request_id", "rollback_request_id"}}
