"""``GET /api/v1/overview`` — every block says whether its data is available (and why not) and how
stale it is. Nothing is estimated or filled in: missing data stays missing."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from mso import state
from mso.auth import Principal, require
from mso.db import Database, get_db
from mso.domain.outcomes import FAILURE_REASONS

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["overzicht"])

CLIENT_REASONS = {"client_failure", "disk_full", "import_failure"}
PROVIDER_REASONS = {"no_retention", "incomplete", "unpack_failed", "auth_failure", "account_expired", "quota_exceeded"}


def _age(db: Database, sql: str, params: Any = None) -> int | None:
    v = db.scalar(f"SELECT extract(epoch FROM now() - ({sql}))::bigint", params)
    return int(v) if v is not None else None


def connectors_block(db: Database) -> dict[str, Any]:
    rows = db.fetchall(
        "SELECT id, kind, label, url, enabled, status, version, last_checked_at, last_error, "
        "extract(epoch FROM now() - last_checked_at)::bigint AS stale_s FROM connectors ORDER BY id"
    )
    items = []
    for r in rows:
        configured = bool(r["enabled"] and r["url"])
        items.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "label": r["label"],
                "configured": configured,
                "status": r["status"] if configured else "not_configured",
                "version": r["version"],
                "last_checked_at": r["last_checked_at"],
                "stale_s": r["stale_s"],
                "message": r["last_error"],
            }
        )
    configured = [i for i in items if i["configured"]]
    return {
        "available": bool(configured),
        "reason": None if configured else "Nog geen koppelingen ingesteld",
        "items": items,
        "counts": {
            s: sum(1 for i in configured if i["status"] == s)
            for s in ("healthy", "degraded", "offline", "auth_failed", "unknown")
        },
    }


def downloads_block(db: Database) -> dict[str, Any]:
    total = db.scalar("SELECT count(*) FROM download_outcomes") or 0
    if not total:
        return {
            "available": False,
            "reason": "Nog geen downloads waargenomen; koppel SABnzbd of qBittorrent",
            "active": {"usenet": 0, "torrent": 0},
            "stale_s": None,
        }
    rows = db.fetchall(
        "SELECT protocol, count(*) AS n FROM download_outcomes WHERE status='active' GROUP BY protocol"
    )
    active = {"usenet": 0, "torrent": 0} | {r["protocol"]: int(r["n"]) for r in rows}
    return {
        "available": True,
        "reason": None,
        "active": active,
        "total_active": sum(active.values()),
        "stale_s": _age(db, "SELECT max(observed_at) FROM download_outcomes"),
    }


def _locations(db: Database, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = {r["id"]: r["label"] for r in db.fetchall("SELECT id, label FROM sources")}
    loc: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, lid: str, label: str, reason: str) -> None:
        e = loc.setdefault((kind, lid), {"kind": kind, "id": lid, "label": label, "count": 0, "reasons": {}})
        e["count"] += 1
        e["reasons"][reason] = e["reasons"].get(reason, 0) + 1

    for r in rows:
        reason = r["failure_reason"] or "unknown"
        if reason in CLIENT_REASONS:
            add("client", r["client"], r["client"], reason)
        elif reason in PROVIDER_REASONS and r["protocol"] == "usenet":
            providers = [k for k, v in (r["provider_bytes"] or {}).items()]
            if providers:
                for k in providers:
                    pid = k if ":" in k else f"provider:{k}"
                    add("provider", pid, labels.get(pid, labels.get(k, k)), reason)
            else:
                add("provider", "onbekend", "Onbekende provider", reason)
        elif r["source_id"]:
            add("source", r["source_id"], labels.get(r["source_id"], r["source_id"]), reason)
        else:
            add("client", r["client"], r["client"], reason)
    return sorted(loc.values(), key=lambda e: -e["count"])


def failures_block(db: Database) -> dict[str, Any]:
    if not db.scalar("SELECT count(*) FROM download_outcomes"):
        return {
            "available": False,
            "reason": "Nog geen downloads waargenomen",
            "last_24h": 0,
            "last_7d": 0,
            "by_reason": [],
            "by_location": [],
        }
    rows = db.fetchall(
        "SELECT protocol, client, source_id, failure_reason, provider_bytes, "
        "COALESCE(finished_at, observed_at) > now() - interval '24 hours' AS recent FROM download_outcomes "
        "WHERE status='failed' AND COALESCE(finished_at, observed_at) > now() - interval '7 days'"
    )
    reasons: dict[str, dict[str, int]] = {}
    for r in rows:
        k = r["failure_reason"] or "unknown"
        e = reasons.setdefault(k, {"last_24h": 0, "last_7d": 0})
        e["last_7d"] += 1
        e["last_24h"] += 1 if r["recent"] else 0
    done_7d = (
        db.scalar(
            "SELECT count(*) FROM download_outcomes WHERE status IN ('success','failed') AND "
            "COALESCE(finished_at, observed_at) > now() - interval '7 days'"
        )
        or 0
    )
    return {
        "available": True,
        "reason": None,
        "last_24h": sum(1 for r in rows if r["recent"]),
        "last_7d": len(rows),
        "finished_7d": int(done_7d),
        "failure_rate_7d": round(len(rows) / done_7d, 4) if done_7d else None,
        "by_reason": sorted(
            ({"reason": k, "label": FAILURE_REASONS.get(k, k)} | v for k, v in reasons.items()),
            key=lambda x: -x["last_7d"],
        ),
        "by_location": _locations(db, rows)[:12],
    }


def recommendations_block(db: Database) -> dict[str, Any]:
    rows = db.fetchall("SELECT risk, count(*) AS n FROM recommendations WHERE status='open' GROUP BY risk")
    by_risk = {"advies": 0, "automatisch_veilig": 0, "toestemming_nodig": 0} | {
        r["risk"]: int(r["n"]) for r in rows
    }
    top = db.fetchall(
        "SELECT id, area, title, risk, confidence, updated_at FROM recommendations WHERE status='open' "
        "ORDER BY CASE risk WHEN 'toestemming_nodig' THEN 0 WHEN 'automatisch_veilig' THEN 1 ELSE 2 END,"
        " updated_at DESC LIMIT 5"
    )
    return {"available": True, "open": sum(by_risk.values()), "by_risk": by_risk, "top": top}


def changes_block(db: Database) -> dict[str, Any]:
    rows = db.fetchall(
        "SELECT id, target, title, risk, status, created_at FROM change_sets "
        "WHERE status IN ('preview','approved') ORDER BY id DESC LIMIT 5"
    )
    n = db.scalar("SELECT count(*) FROM change_sets WHERE status IN ('preview','approved')") or 0
    failed = db.scalar(
        "SELECT count(*) FROM change_sets WHERE status='failed' AND created_at > now() - interval '7 days'"
    )
    return {"available": True, "open": int(n), "failed_7d": int(failed or 0), "items": rows}


def costs_block(db: Database) -> dict[str, Any]:
    try:
        from mso.features.costs.analysis import monthly_total
    except ImportError:
        return {"available": False, "reason": "Kostenmodule niet geladen"}
    t = monthly_total(db)
    if not t["services"]:
        return {"available": False, "reason": "Nog geen diensten met kosten ingevuld", "monthly_cents": None}
    return {"available": True, "reason": None} | t


def jobs_block(db: Database) -> dict[str, Any]:
    failing = db.fetchall(
        "SELECT kind, count(*) AS n, max(updated_at) AS last_failed, (array_agg(error ORDER BY updated_at "
        "DESC))[1] "
        "AS last_error FROM jobs WHERE status='failed' AND updated_at > now() - interval '24 hours' "
        "GROUP BY kind ORDER BY n DESC"
    )
    counts = {
        r["status"]: int(r["n"])
        for r in db.fetchall(
            "SELECT status, count(*) AS n FROM jobs WHERE status IN ('queued','running') GROUP BY status"
        )
    }
    hb = _age(db, "SELECT updated_at FROM system_state WHERE key='worker_heartbeat'")
    return {
        "available": True,
        "failing_24h": sum(int(r["n"]) for r in failing),
        "failing": failing,
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "worker": {"seen": hb is not None, "stale_s": hb, "healthy": hb is not None and hb < 90},
    }


MG_MODE_LABELS = {"standalone": "Zelfstandig", "degraded": "Beperkt", "connected": "Gekoppeld"}


def mediaguard_block(db: Database) -> dict[str, Any]:
    """MediaGuard mode from Slice D's cached probe (never a network call from the dashboard); falls back to
    the connectors table when that module is absent. Vocabulary: standalone | degraded | connected."""
    out: dict[str, Any] | None = None
    try:
        from mso.features.mediaguard import service as mg  # type: ignore[import-not-found]
    except ImportError:
        mg = None
    if mg is not None:
        try:
            configured = bool(mg.configured())
            mode = mg.mode()
            snap = mg.snap_get("probe") if configured else None
            probe = (snap or {}).get("data") or None
            reason = mg._mode(probe)[1] if hasattr(mg, "_mode") else None
            stale = None
            if snap and snap.get("fetched_at"):
                stale = int(
                    db.scalar(
                        "SELECT extract(epoch FROM now() - %s::timestamptz)::bigint", (snap["fetched_at"],)
                    )
                )
            out = {
                "available": configured,
                "mode": mode,
                "reason": reason,
                "stale_s": stale,
                "version": (probe or {}).get("version"),
                "emergency_stop": (probe or {}).get("emergency_stop"),
                "source": "mediaguard",
            }
        except Exception as exc:
            LOGGER.info("MediaGuard-status niet leesbaar: %s", exc)
            out = None
    if out is None:
        row = db.fetchone(
            "SELECT url, enabled, status, extract(epoch FROM now() - last_checked_at)::bigint AS stale_s "
            "FROM connectors WHERE id='mediaguard'"
        )
        if row is None or not row["enabled"] or not row["url"]:
            out = {
                "available": False,
                "mode": "standalone",
                "source": "connectors",
                "stale_s": None,
                "reason": "MediaGuard is niet gekoppeld; de app werkt zelfstandig",
            }
        else:
            out = {
                "available": True,
                "mode": "connected" if row["status"] == "healthy" else "degraded",
                "reason": None if row["status"] == "healthy" else f"Laatste test: {row['status']}",
                "stale_s": row["stale_s"],
                "source": "connectors",
            }
    out["mode_label"] = MG_MODE_LABELS.get(out["mode"], out["mode"])
    return out


def backup_block(db: Database) -> dict[str, Any]:
    row = db.fetchone(
        "SELECT id, kind, created_at, size_bytes, extract(epoch FROM now() - created_at)::bigint AS age_s "
        "FROM backups ORDER BY created_at DESC LIMIT 1"
    )
    if row is None:
        return {"available": False, "reason": "Nog geen back-up gemaakt", "age_s": None}
    return {"available": True, "age_s": row["age_s"], "last": row, "stale": row["age_s"] > 2 * 86400}


def health_summary(conn: dict[str, Any], jobs: dict[str, Any], failures: dict[str, Any]) -> dict[str, Any]:
    issues = []
    for c in conn["items"]:
        if c["configured"] and c["status"] in ("offline", "auth_failed", "degraded"):
            issues.append(f"{c['label']}: {c['status']}")
    if not jobs["worker"]["healthy"]:
        issues.append("Worker niet actief" if not jobs["worker"]["seen"] else "Worker-hartslag verouderd")
    if jobs["failing_24h"]:
        issues.append(f"{jobs['failing_24h']} mislukte taak/taken (24 u)")
    if not conn["available"]:
        status = "unknown"
    elif any(c["configured"] and c["status"] in ("offline", "auth_failed") for c in conn["items"]):
        status = "offline"
    elif issues:
        status = "degraded"
    else:
        status = "healthy"
    return {"status": status, "issues": issues}


@router.get("/overview")
def overview(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    db = get_db()
    conn, jobs_b, fail = connectors_block(db), jobs_block(db), failures_block(db)
    return {
        "mode": state.mode(),
        "paused": state.paused(),
        "automation_allowed": state.automation_allowed(),
        "health": health_summary(conn, jobs_b, fail),
        "connectors": conn,
        "downloads": downloads_block(db),
        "failures": fail,
        "recommendations": recommendations_block(db),
        "changes": changes_block(db),
        "costs": costs_block(db),
        "jobs": jobs_b,
        "mediaguard": mediaguard_block(db),
        "backup": backup_block(db),
        "recent_audit": db.fetchall(
            "SELECT id, ts, actor, action, target, summary FROM audit_log ORDER BY id DESC LIMIT 8"
        ),
        "generated_at": db.scalar("SELECT now()"),
    }
