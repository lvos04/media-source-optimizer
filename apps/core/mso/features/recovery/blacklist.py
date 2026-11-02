"""Failed-release blacklist (TTL, exponential, hard cap) and per-media-item backoff.

Contract (shared with other slices)::

    is_blacklisted(release_key: str) -> bool
    add(release_key, reason, ttl_h, media_ref=None, protocol=None)

A release key is the protocol identity from ``mso.domain.release.release_key`` (``nzb:…``, ``mid:…``,
``btih:…``, ``btmh:…``). Every repeat failure doubles the TTL; at ``herstel.blacklist_max_failures`` the entry
becomes permanent so the same NZB/infohash is never retried endlessly. Only an admin can remove an entry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mso import settings_registry as sr
from mso.audit import audit
from mso.db import get_db
from mso.features.recovery import settings as _settings  # noqa: F401  (registers settings)

# Outcome failure reasons that say something about the *release itself* (not about the client/account).
RELEASE_SPECIFIC_FAILURES = {
    "false_positive",
    "incomplete",
    "no_retention",
    "encrypted",
    "stalled",
    "rejected_quality",
}


def _now() -> datetime:
    return datetime.now(UTC)


def is_blacklisted(release_key: str) -> bool:
    if not release_key:
        return False
    return bool(
        get_db().scalar(
            "SELECT count(*) FROM recovery_blacklist WHERE release_key=%s AND (permanent OR expires_at > "
            "now())",
            (release_key,),
        )
    )


def active_keys(release_keys: list[str]) -> set[str]:
    if not release_keys:
        return set()
    rows = get_db().fetchall(
        "SELECT release_key FROM recovery_blacklist WHERE release_key = ANY(%s) AND "
        "(permanent OR expires_at > now())",
        (list(release_keys),),
    )
    return {r["release_key"] for r in rows}


def add(
    release_key: str,
    reason: str,
    ttl_h: float | None = None,
    media_ref: str | None = None,
    protocol: str | None = None,
    *,
    actor: str = "system",
) -> dict[str, Any]:
    """Add or escalate. ``ttl_h`` is the base TTL (default setting); repeats double it; at the hard cap the
    entry becomes permanent."""
    if not release_key:
        raise ValueError("release_key is verplicht")
    if protocol not in (None, "usenet", "torrent"):
        raise ValueError("protocol moet usenet of torrent zijn")
    base_h = float(ttl_h if ttl_h is not None else sr.get("herstel.blacklist_ttl_h"))
    cap = int(sr.get("herstel.blacklist_max_failures"))
    db = get_db()
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM recovery_blacklist WHERE release_key=%s FOR UPDATE", (release_key,)
        ).fetchone()
        failures = (row["failures"] + 1) if row else 1
        permanent = failures >= cap
        expires = None if permanent else _now() + timedelta(hours=base_h * 2 ** (failures - 1))
        if row is None:
            out = conn.execute(
                "INSERT INTO recovery_blacklist(release_key,media_ref,protocol,reason,failures,"
                "permanent,created_by,"
                "expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (release_key, media_ref, protocol, reason, failures, permanent, actor, expires),
            ).fetchone()
        else:
            out = conn.execute(
                "UPDATE recovery_blacklist SET failures=%s, permanent=%s, expires_at=%s, reason=%s, "
                "media_ref=COALESCE(%s, media_ref), protocol=COALESCE(%s, protocol), updated_at=now() "
                "WHERE id=%s RETURNING *",
                (failures, permanent, expires, reason, media_ref, protocol, row["id"]),
            ).fetchone()
    audit(
        actor,
        "recovery.blacklist_add",
        f"Release op blacklist ({'permanent' if permanent else f'{failures}e keer'}): {reason}",
        target=release_key,
        detail={"media_ref": media_ref, "protocol": protocol, "failures": failures},
    )
    return dict(out)


def get(entry_id: int) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM recovery_blacklist WHERE id=%s", (entry_id,))


def list_entries(
    *, active_only: bool = False, media_ref: str | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    where, params = [], []
    if active_only:
        where.append("(permanent OR expires_at > now())")
    if media_ref:
        where.append("media_ref=%s")
        params.append(media_ref)
    sql = "SELECT *, (permanent OR expires_at > now()) AS active FROM recovery_blacklist"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT %s"
    params.append(limit)
    return get_db().fetchall(sql, params)


def remove(entry_id: int, *, actor: str) -> bool:
    row = get(entry_id)
    if row is None:
        return False
    get_db().execute("DELETE FROM recovery_blacklist WHERE id=%s", (entry_id,))
    audit(
        actor,
        "recovery.blacklist_remove",
        "Release van blacklist verwijderd",
        target=row["release_key"],
        detail={"failures": row["failures"], "permanent": row["permanent"]},
    )
    return True


# ------------------------------------------------------------------ per media item backoff
def media_backoff(media_ref: str) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM recovery_media_backoff WHERE media_ref=%s", (media_ref,))


def media_allowed(media_ref: str) -> tuple[bool, datetime | None]:
    row = media_backoff(media_ref)
    if row is None or row["next_allowed_at"] is None or row["next_allowed_at"] <= _now():
        return True, None
    return False, row["next_allowed_at"]


def register_media_failure(media_ref: str) -> datetime:
    row = media_backoff(media_ref)
    failures = (row["failures"] if row else 0) + 1
    base = float(sr.get("herstel.media_backoff_base_h"))
    cap = float(sr.get("herstel.media_backoff_max_h"))
    nxt = _now() + timedelta(hours=min(cap, base * 2 ** (failures - 1)))
    get_db().execute(
        "INSERT INTO recovery_media_backoff(media_ref,failures,last_failure_at,next_allowed_at) VALUES "
        "(%s,%s,now(),%s) ON CONFLICT (media_ref) DO UPDATE SET failures=EXCLUDED.failures, "
        "last_failure_at=now(), "
        "next_allowed_at=EXCLUDED.next_allowed_at",
        (media_ref, failures, nxt),
    )
    return nxt


def clear_media_backoff(media_ref: str) -> None:
    get_db().execute("DELETE FROM recovery_media_backoff WHERE media_ref=%s", (media_ref,))
