"""``GET /api/v1/overview/autopilot`` — "Wat MSO deed" (automatic actions in the last N days, from the audit log,
the automation log and the job table) and "Wacht op jou" (open changes/recommendations that need a person).

In-app only: nothing is sent to external services. Everything is read from MSO's own tables; nothing is invented.
An action counts as automatic when its actor is not a user account (``systeem``, ``automatisering``, ``scheduler``
…) or when the audit detail says ``automatic: true``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from mso import state
from mso.auth import Principal, require
from mso.db import Database, get_db

router = APIRouter(prefix="/api/v1/overview", tags=["overzicht"])

# key -> (Dutch label, link, tone)
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "applied": ("Instellingen automatisch afgestemd", "/automatisering?tab=controle", "success"),
    "switched_off": ("Uitgeschakeld (terug te draaien)", "/automatisering?tab=wijzigingen", "warning"),
    "rolled_back": ("Automatisch teruggedraaid na controle", "/automatisering?tab=controle", "warning"),
    "servers": ("Usenet-servers gecontroleerd of omgeschakeld", "/usenet-providers", "info"),
    "retries": ("Downloads opnieuw geprobeerd", "/downloads", "info"),
    "remedies": ("Vastgelopen torrents hersteld (opnieuw aankondigen of controleren)", "/torrentclient", "info"),
    "searches": ("Zoekopdrachten en vervangers verstuurd", "/ontbrekend", "info"),
    "prepared": ("Voor jou klaargezet (wacht op goedkeuring)", "/automatisering?tab=wijzigingen", "warning"),
    "evaluated": ("Wijzigingen gecontroleerd", "/automatisering?tab=controle", "neutral"),
    "syncs": ("Synchronisaties, imports en back-ups", "/systeem#automatische-taken", "neutral"),
    "failed": ("Mislukt (niets veranderd)", "/logs", "danger"),
    "other": ("Overige automatische acties", "/logs", "neutral"),
}
ORDER = list(CATEGORIES)

# periodic jobs whose runs are counted under "syncs" (from the job table, not the audit log)
SYNC_JOB_PREFIXES = ("costs.import_services", "prowlarr.sync", "usenet.sab_ingest", "usenet.sab_stats",
                     "usenet.sab_server_status", "usenet.provider_checks", "mediaguard.sync", "torrent.ingest",
                     "torrent.storage", "indexers.", "backup.create", "recovery.analyze", "usenet.headerindex",
                     "torrent.autopilot")
# audit actions that are noise here (duplicates of another row, or human-only)
SKIP_ACTIONS = {"automation.applied", "automation.run_requested", "automation.toggle", "settings.updated",
                "system.mode", "system.paused", "system.resumed", "change.approved", "change.discarded",
                "change.rolled_back"}
_SYNC_RE = re.compile(r"(^|[._])(sync|synced|ingest|import|imported|scan|stats|health|reliability|analyze)"
                      r"($|[._])|^backup\.created$|services_imported")
_SEARCH_RE = re.compile(r"search_requested|fallback_sent|fallback_send|recovery\.sent|search\.sent|add_url|add_nzb|"
                        r"add_torrent|requested_search|replacement")
_SERVER_RE = re.compile(r"server|provider_status|disable_failing|enable_recovered")


def classify(row: dict[str, Any], change: dict[str, Any] | None = None) -> str | None:
    """Category for one automatic audit row, or None to leave it out."""
    action = str(row["action"])
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    if action in SKIP_ACTIONS:
        return None
    if action.startswith("usenet.server_"):  # probe failed / recovered / kept last / retried server
        return "servers"
    if action == "sabnzbd.retry":
        return "retries"
    if action == "torrent.autopilot_remedy":
        return "remedies"
    if action in ("torrent.cleanup_proposed", "torrent.cleanup_refreshed"):
        return "prepared"
    if action in ("change.auto_rolled_back", "change.auto_restored"):
        return "rolled_back"
    if action == "change.applied":
        ops = (change or {}).get("ops") or []
        if any(op.get("destructive") for op in ops):
            return "switched_off"
        target = str(row.get("target") or "")
        if target == "sabnzbd_retry":
            return None  # every retried download has its own 'sabnzbd.retry' row
        if target in ("download_send",):
            return "searches"
        return "applied"
    if action == "change.created":
        return "prepared" if (detail.get("risk") == "toestemming_nodig") else None
    if action == "automation.rollback_proposed":
        return "prepared"
    if action == "change.failed" and row.get("actor") == "automatisering":
        return None  # automation.apply_failed already records it
    if action in ("automation.apply_failed", "change.failed", "change.auto_restore_failed") or action.endswith(
            "_failed"):
        return "failed"
    if "retry" in action or "retried" in action:
        return "retries"
    if _SERVER_RE.search(action):
        return "servers"
    if _SEARCH_RE.search(action):
        return "searches"
    if _SYNC_RE.search(action):
        return None  # counted from the job table (one row per run would flood the list)
    if action.endswith("_evaluated"):
        return "evaluated"
    if action.endswith("_proposed") or action.startswith("change."):
        return None  # proposals are shown via change.created / waiting list
    return "other"


def _automatic_rows(db: Database, since: datetime) -> list[dict[str, Any]]:
    return db.fetchall(
        "SELECT a.id, a.ts, a.actor, a.action, a.target, a.summary, a.detail, a.change_id, "
        "c.ops AS change_ops, c.status AS change_status FROM audit_log a "
        "LEFT JOIN change_sets c ON c.id = a.change_id "
        "WHERE a.ts >= %s AND (a.actor NOT IN (SELECT username FROM users) OR a.detail->>'automatic' = 'true' "
        "OR a.detail->>'trigger' = 'auto') "
        "ORDER BY a.ts DESC LIMIT 5000",
        (since,),
    )


def _link(cat: str, row: dict[str, Any]) -> str:
    if row.get("change_id") and cat in ("applied", "switched_off", "prepared", "rolled_back", "remedies"):
        tab = "controle" if cat in ("applied", "rolled_back") else "wijzigingen"
        return f"/automatisering?tab={tab}&change={row['change_id']}"
    return CATEGORIES[cat][1]


def summary(days: int, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    counts: dict[str, int] = {k: 0 for k in ORDER}
    examples: dict[str, list[dict[str, Any]]] = {k: [] for k in ORDER}
    items: list[dict[str, Any]] = []
    per_day: dict[str, dict[str, Any]] = {}

    def day_bucket(ts: datetime) -> dict[str, Any]:
        d = ts.astimezone(UTC).date().isoformat()
        return per_day.setdefault(d, {"date": d, "total": 0, "categories": {}, "items": []})

    # one change can have a specific row (remedy, cleanup proposal) and a generic one (change.applied/created):
    # rows are newest first and the specific row is written after the generic one, so it wins
    specific_changes: set[int] = set()
    for r in _automatic_rows(db, since):
        cat = classify(dict(r), {"ops": r["change_ops"]} if r["change_ops"] is not None else None)
        if cat is None:
            continue
        cid = r["change_id"]
        if cid is not None:
            if r["action"] in ("change.applied", "change.created") and cid in specific_changes:
                continue
            if not str(r["action"]).startswith("change."):
                if cat == "prepared" and cid in specific_changes:
                    continue  # refreshed cleanup proposal: count once
                specific_changes.add(cid)
            elif cat == "prepared":
                specific_changes.add(cid)
        item = {"id": r["id"], "ts": r["ts"], "category": cat, "action": r["action"], "summary": r["summary"],
                "target": r["target"], "change_id": r["change_id"], "actor": r["actor"], "link": _link(cat, r)}
        counts[cat] += 1
        if len(examples[cat]) < 5:
            examples[cat].append(item)
        if len(items) < 200:
            items.append(item)
        b = day_bucket(r["ts"])
        b["total"] += 1
        b["categories"][cat] = b["categories"].get(cat, 0) + 1
        if len(b["items"]) < 50:
            b["items"].append(item)

    # evaluations (automation_log) — the engine measures every applied change after its period
    for r in db.fetchall("SELECT ts, result FROM automation_log WHERE kind='evaluate' AND ts >= %s "
                         "AND result <> 'rolled_back'", (since,)):
        counts["evaluated"] += 1
        b = day_bucket(r["ts"])
        b["total"] += 1
        b["categories"]["evaluated"] = b["categories"].get("evaluated", 0) + 1

    # periodic syncs/imports/back-ups (job table; only scheduler/system-started runs)
    sync_rows = db.fetchall(
        "SELECT kind, status, date_trunc('day', updated_at AT TIME ZONE 'UTC') AS d, count(*) AS n FROM jobs "
        "WHERE updated_at >= %s AND status IN ('done','failed') AND created_by NOT IN (SELECT username FROM users) "
        "GROUP BY kind, status, d", (since,))
    sync_detail: dict[str, dict[str, int]] = {}
    for r in sync_rows:
        if not str(r["kind"]).startswith(SYNC_JOB_PREFIXES):
            continue
        n = int(r["n"])
        sd = sync_detail.setdefault(r["kind"], {"done": 0, "failed": 0})
        sd[r["status"]] += n
        counts["syncs"] += n
        d = r["d"].date().isoformat() if isinstance(r["d"], datetime) else str(r["d"])[:10]
        b = per_day.setdefault(d, {"date": d, "total": 0, "categories": {}, "items": []})
        b["total"] += n
        b["categories"]["syncs"] = b["categories"].get("syncs", 0) + n

    categories = [
        {"key": k, "label": CATEGORIES[k][0], "link": CATEGORIES[k][1], "tone": CATEGORIES[k][2],
         "count": counts[k], "examples": examples[k]}
        for k in ORDER if counts[k]
    ]
    sync_failed = sum(v["failed"] for v in sync_detail.values())
    by_day = []
    for i in range(days):
        d = (now.date() - timedelta(days=i)).isoformat()
        by_day.append(per_day.get(d, {"date": d, "total": 0, "categories": {}, "items": []}))
    return {
        "days": days,
        "since": since,
        "generated_at": now,
        "mode": state.mode(),
        "paused": state.paused(),
        "autopilot": state.autopilot(),
        "automation_allowed": state.automation_allowed(),
        "total": sum(counts.values()),
        "categories": categories,
        "items": items,
        "by_day": by_day,
        "syncs": {"jobs": [{"kind": k, **v} for k, v in sorted(sync_detail.items())], "failed": sync_failed},
        "waiting": waiting(db),
    }


def waiting(db: Database | None = None) -> dict[str, Any]:
    """Open changes that need a person: approval (deleting, cleanup, …), approved-but-not-applied, and safe
    proposals that cannot be applied automatically in the current mode."""
    from mso.features.automation import autopilot
    from mso.features.automation.engine import change_area

    db = db or get_db()
    rows = db.fetchall(
        "SELECT * FROM change_sets WHERE status IN ('preview','approved') ORDER BY "
        "CASE risk WHEN 'toestemming_nodig' THEN 0 ELSE 1 END, created_at DESC LIMIT 200")
    items: list[dict[str, Any]] = []
    for r in rows:
        ops = r["ops"] or []
        deletes = any(op.get("irreversible") for op in ops)
        if r["risk"] == "toestemming_nodig":
            kind = "toepassen" if r["status"] == "approved" else "goedkeuren"
            reason = ("Goedgekeurd; wacht nog op toepassen" if r["status"] == "approved"
                      else "Verwijderen gebeurt nooit automatisch; één klik van jou" if deletes
                      else "Vereist goedkeuring")
        elif r["risk"] == "automatisch_veilig":
            why = autopilot.why_not(dict(r), change_area(dict(r), db))
            if why is None or why.startswith("status"):
                continue  # MSO applies it itself in the next round
            kind, reason = "toepassen", why[:1].upper() + why[1:]
        else:
            continue
        items.append({"id": r["id"], "title": r["title"], "target": r["target"], "risk": r["risk"],
                      "status": r["status"], "created_at": r["created_at"], "kind": kind, "reason": reason,
                      "deletes": deletes, "ops": len(ops),
                      "link": f"/automatisering?tab=wijzigingen&change={r['id']}"})
    recs = db.fetchall(
        "SELECT id, area, title, created_at FROM recommendations WHERE status='open' AND risk='toestemming_nodig' "
        "AND change_id IS NULL ORDER BY updated_at DESC LIMIT 50")
    for r in recs:
        items.append({"id": f"rec:{r['id']}", "title": r["title"], "target": r["area"], "risk": "toestemming_nodig",
                      "status": "open", "created_at": r["created_at"], "kind": "bekijken",
                      "reason": "Aanbeveling die jouw beslissing vraagt", "deletes": False, "ops": 0,
                      "link": "/automatisering?tab=aanbevelingen"})
    return {"count": len(items), "items": items[:50]}


@router.get("/autopilot")
def autopilot_summary(days: int = Query(1, ge=1, le=31),
                      _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return summary(days)


__all__ = ["CATEGORIES", "classify", "router", "summary", "waiting"]
