"""``GET /api/v1/overview/autopilot`` — "Wat MSO deed" (automatic actions in the last N days, from the audit log,
the automation log and the job table) and "Wacht op jou" (open changes/recommendations that need a person).

In-app only: nothing is sent to external services. Everything is read from MSO's own tables; nothing is invented.
An action counts as automatic when its actor is not a user account (``systeem``, ``automatisering``, ``scheduler``
…) or when the audit detail says ``automatic: true``.

1.6.0 ("rust in de app"):
- ``actions_total`` counts only real actions (no synchronisation runs, no evaluations); ``total`` stays as it was.
- ``actions``: per change MSO made, *wat* (title), *waarom* (from the change's reason/explanation) and *effect*
  (from the stored evaluation, or "meting loopt" while the measurement period runs) plus whether it can be rolled
  back (the existing rollback endpoint).
- ``syncs.state``: one status line for all synchronisations (``ok`` / ``failing``), with the kinds failing now.
- Day buckets use local days (``TZ``, default Europe/Amsterdam; converted by PostgreSQL's own time-zone data).
- ``waiting()`` = one real inbox: per-torrent malware recommendations already in the open cleanup bundle are
  dropped, the bundle shows once ("41 nep-downloads opruimen (+3 nieuw sinds gisteren)"), the rest is grouped,
  and ``decisions`` counts the real decisions. Calm info items (e.g. "je hebt dit zelf teruggezet") are listed
  separately and never count as a decision. ``GET /overview/waiting`` serves it alone for the shell badge.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Query

from mso import state
from mso.auth import Principal, require
from mso.db import Database, get_db

LOGGER = logging.getLogger(__name__)
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
                     "torrent.autopilot", "automation.footprint")
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
    if action.startswith("footprint."):
        return None  # an observation (the owner changed something back) or a person's restore, not an action
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


# categories whose rows stand for one change MSO made (shown as Wat / Waarom / Effect)
ACTION_CATEGORIES = ("applied", "switched_off", "rolled_back", "remedies", "searches")
# not counted in ``actions_total`` (bookkeeping, not an action on your systems)
NOT_ACTIONS = ("syncs", "evaluated")
DEFAULT_TZ = "Europe/Amsterdam"
_TZ_OK: dict[str, str] = {}
TARGET_LABEL = {"sabnzbd": "SABnzbd", "sabnzbd_retry": "SABnzbd", "qbittorrent": "qBittorrent",
                "prowlarr": "Prowlarr", "download_send": "downloadclient", "mediaguard": "MediaGuard"}
WHY_KEYS = ("Reden", "reden", "Waarom", "waarom", "reason", "Aanleiding", "Oorzaak")
WHY_DETAIL_KEYS = ("SABnzbd meldt", "Melding", "Bron")
EVAL_ACTION_NL = {"behouden": "behouden", "teruggedraaid": "teruggedraaid", "voorgesteld": "terugdraaien voorgesteld"}
EVAL_TONE = {"beter": "success", "gelijk": "neutral", "slechter": "danger", "onvoldoende": "neutral",
             "niet_van_toepassing": "neutral"}


def local_tz(db: Database | None = None) -> str:
    """The local time zone for day buckets: ``TZ`` (the container's zone, Europe/Amsterdam by default). Checked
    once against PostgreSQL's own time-zone data (so a slim image without tzdata still gets local days); an
    unknown name falls back to UTC (cached). Any other error (the database briefly unavailable) gives UTC for this
    call only, so one hiccup after a start does not make every day bucket UTC until the next restart."""
    name = (os.environ.get("TZ") or DEFAULT_TZ).strip().lstrip(":") or DEFAULT_TZ
    if name not in _TZ_OK:
        try:
            (db or get_db()).scalar("SELECT now() AT TIME ZONE %s", (name,))
            _TZ_OK[name] = name
        except psycopg.errors.InvalidParameterValue:  # "time zone ... not recognized"
            _TZ_OK[name] = "UTC"
        except Exception:
            LOGGER.warning("tijdzone %s niet te controleren; deze keer UTC", name, exc_info=True)
            return "UTC"
    return _TZ_OK[name]


def _automatic_rows(db: Database, since: datetime, tz: str) -> list[dict[str, Any]]:
    return db.fetchall(
        "SELECT a.id, a.ts, (a.ts AT TIME ZONE %s)::date AS local_day, a.actor, a.action, a.target, a.summary, "
        "a.detail, a.change_id, c.ops AS change_ops, c.status AS change_status FROM audit_log a "
        "LEFT JOIN change_sets c ON c.id = a.change_id "
        "WHERE a.ts >= %s AND (a.actor NOT IN (SELECT username FROM users) OR a.detail->>'automatic' = 'true' "
        "OR a.detail->>'trigger' = 'auto') "
        "ORDER BY a.ts DESC LIMIT 5000",
        (tz, since),
    )


def _link(cat: str, row: dict[str, Any]) -> str:
    if row.get("change_id") and cat in ("applied", "switched_off", "prepared", "rolled_back", "remedies"):
        tab = "controle" if cat in ("applied", "rolled_back") else "wijzigingen"
        return f"/automatisering?tab={tab}&change={row['change_id']}"
    return CATEGORIES[cat][1]


def _day(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


# ------------------------------------------------------------------ wat / waarom / effect
def _short(text: str, limit: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    m = re.search(r"(?<=[.!?])\s+(?=[A-Z0-9'\"(])", text)
    return text[: m.start()] if m else text


def why_of(ch: dict[str, Any]) -> str | None:
    """Plain reason for a change: its impact's ``Reden`` (+ what the source reported), else the first sentence of
    its explanation."""
    imp = ch.get("impact") if isinstance(ch.get("impact"), dict) else {}
    for key in WHY_KEYS:
        value = imp.get(key)
        if isinstance(value, str) and value.strip():
            detail = next((str(imp[k]) for k in WHY_DETAIL_KEYS
                           if isinstance(imp.get(k), str) and imp[k].strip() not in ("", "—")), "")
            return _short(f"{value.strip()}: {detail}" if detail else value.strip())
    first = _first_sentence(ch.get("explanation") or "")
    return _short(first) if first else None


def _nl_num(value: float, digits: int) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _fmt_measure_value(value: Any, unit: str) -> str:
    if not isinstance(value, int | float):
        return "—"
    if unit == "ratio":
        pct = value * 100
        return f"{_nl_num(pct, 0 if pct >= 10 or pct == 0 else 1)} %"
    if unit == "s":
        if value >= 3600:
            return f"{_nl_num(value / 3600, 1)} u"
        return f"{_nl_num(value / 60, 0)} min"
    if unit == "B/s":
        return f"{_nl_num(value / 1_000_000, 1)} MB/s"
    return f"{_nl_num(value, 2 if abs(value) < 10 else 0)}" + (f" {unit}" if unit else "")


def measures_text(measures: Any, limit: int = 2) -> str:
    """'Faalratio downloads 31 % → 12 % (n=140)' for the first measures of a stored evaluation."""
    parts = []
    for m in (measures or [])[:limit]:
        if not isinstance(m, dict):
            continue
        base = (m.get("baseline") or {}) if isinstance(m.get("baseline"), dict) else {}
        cur = (m.get("current") or {}) if isinstance(m.get("current"), dict) else {}
        label = str(m.get("label") or m.get("key") or "meting")
        if base.get("value") is None or cur.get("value") is None:
            if m.get("why"):
                parts.append(f"{label}: {m['why']}")
            continue
        unit = str(m.get("unit") or "")
        n = cur.get("n")
        parts.append(f"{label} {_fmt_measure_value(base['value'], unit)} → {_fmt_measure_value(cur['value'], unit)}"
                     + (f" (n={n})" if n is not None else ""))
    return " · ".join(parts)


def _hours_left_text(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "binnen een uur"
    if hours < 48:
        return f"over {int(round(hours))} u"
    return f"over {int(round(hours / 24))} dagen"


def effect_of(ch: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Measured effect of an applied change, from its stored evaluation (nothing is invented)."""
    from mso import settings_registry as sr
    from mso.features.automation.engine import not_evaluable
    from mso.features.automation.evaluators import OUTCOME_LABEL

    now = now or datetime.now(UTC)
    imp = ch.get("impact") if isinstance(ch.get("impact"), dict) else {}
    ev = imp.get("evaluation") if isinstance(imp.get("evaluation"), dict) else None
    if ch.get("status") == "rolled_back":
        text = measures_text((ev or {}).get("measures"))
        msg = (ev or {}).get("reason") or (ev or {}).get("message") or "Deze wijziging is teruggedraaid."
        return {"state": "rolled_back", "result": (ev or {}).get("result"), "label": "Teruggedraaid",
                "text": _short(" · ".join(x for x in (text, str(msg)) if x), 300), "tone": "warning", "due_at": None}
    if ev is not None:
        result = str(ev.get("result") or "")
        text = measures_text(ev.get("measures")) or str(ev.get("message") or "")
        action = EVAL_ACTION_NL.get(str(ev.get("action") or ""))
        return {"state": "measured", "result": result, "label": ev.get("label") or OUTCOME_LABEL.get(result, result),
                "text": _short(" · ".join(x for x in (text, action) if x), 300),
                "tone": EVAL_TONE.get(result, "neutral"), "due_at": None}
    if ch.get("status") != "applied":
        return {"state": "none", "result": None, "label": None, "text": None, "tone": "neutral", "due_at": None}
    if not_evaluable(ch):
        return {"state": "not_measured", "result": "niet_van_toepassing", "label": OUTCOME_LABEL["niet_van_toepassing"],
                "text": "Uitschakelen en verwijderen worden niet gemeten en nooit automatisch teruggedraaid.",
                "tone": "neutral", "due_at": None}
    due = ch.get("evaluate_after")
    if due is None and ch.get("applied_at"):
        due = ch["applied_at"] + timedelta(hours=int(sr.get("automation.evaluation_hours")))
    if due is None:
        return {"state": "pending", "result": None, "label": "Wordt gemeten", "text": "Meting loopt.", "tone": "info",
                "due_at": None}
    left = due - now
    text = (f"Meting loopt, eindoordeel {_hours_left_text(left)}" if left.total_seconds() > 0
            else "Meetperiode voorbij; het eindoordeel volgt bij de volgende controle (elk kwartier)")
    return {"state": "pending", "result": None, "label": "Wordt gemeten", "text": text, "tone": "info", "due_at": due}


def rollback_of(ch: dict[str, Any]) -> dict[str, Any]:
    """Can this change be rolled back with the existing endpoint (``POST /changes/{id}/rollback``)?"""
    status = ch.get("status")
    if status == "rolled_back":
        return {"possible": False, "reason": "Al teruggedraaid"}
    if status != "applied":
        return {"possible": False, "reason": f"Status '{status}'"}
    ops = ch.get("ops") or []
    if not any(op.get("result") == "toegepast" and not (op.get("irreversible") or op.get("one_way")) for op in ops):
        return {"possible": False, "reason": "Niet terug te draaien (eenmalige actie of verwijdering)"}
    return {"possible": True, "reason": None}


def _actions(items: list[dict[str, Any]], db: Database, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
    """One row per change MSO made (newest first): wat / waarom / effect + rollback."""
    seen: dict[int, dict[str, Any]] = {}
    for it in items:
        cid = it.get("change_id")
        if cid is None or it["category"] not in ACTION_CATEGORIES or cid in seen:
            continue
        seen[cid] = it
        if len(seen) >= limit:
            break
    if not seen:
        return []
    rows = {r["id"]: dict(r) for r in db.fetchall("SELECT * FROM change_sets WHERE id = ANY(%s)", (list(seen),))}
    out = []
    for cid, it in seen.items():
        ch = rows.get(cid)
        if ch is None:
            continue
        out.append({
            "change_id": cid, "ts": it["ts"], "category": it["category"], "link": it["link"],
            "what": ch["title"], "target": ch["target"], "target_label": TARGET_LABEL.get(ch["target"], ch["target"]),
            "why": why_of(ch), "effect": effect_of(ch, now), "status": ch["status"], "rollback": rollback_of(ch),
        })
    return out


# ------------------------------------------------------------------ summary
def summary(days: int, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    tz = local_tz(db)
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    today = db.scalar("SELECT (now() AT TIME ZONE %s)::date", (tz,))
    today = today if isinstance(today, date) else now.date()
    counts: dict[str, int] = {k: 0 for k in ORDER}
    examples: dict[str, list[dict[str, Any]]] = {k: [] for k in ORDER}
    items: list[dict[str, Any]] = []
    per_day: dict[str, dict[str, Any]] = {}

    def day_bucket(d: str) -> dict[str, Any]:
        return per_day.setdefault(d, {"date": d, "total": 0, "actions": 0, "categories": {}, "items": []})

    # one change can have a specific row (remedy, cleanup proposal) and a generic one (change.applied/created):
    # rows are newest first and the specific row is written after the generic one, so it wins
    specific_changes: set[int] = set()
    for r in _automatic_rows(db, since, tz):
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
        b = day_bucket(_day(r["local_day"]))
        b["total"] += 1
        if cat not in NOT_ACTIONS:  # a sync or an evaluation (e.g. torrent.tuning_evaluated) is no action
            b["actions"] += 1
        b["categories"][cat] = b["categories"].get(cat, 0) + 1
        if len(b["items"]) < 50:
            b["items"].append(item)

    # evaluations (automation_log) — the engine measures every applied change after its period
    for r in db.fetchall("SELECT (ts AT TIME ZONE %s)::date AS local_day FROM automation_log "
                         "WHERE kind='evaluate' AND ts >= %s AND result <> 'rolled_back'", (tz, since)):
        counts["evaluated"] += 1
        b = day_bucket(_day(r["local_day"]))
        b["total"] += 1
        b["categories"]["evaluated"] = b["categories"].get("evaluated", 0) + 1

    # periodic syncs/imports/back-ups (job table; only scheduler/system-started runs)
    sync_rows = db.fetchall(
        "SELECT kind, status, (updated_at AT TIME ZONE %s)::date AS d, count(*) AS n FROM jobs "
        "WHERE updated_at >= %s AND status IN ('done','failed') AND created_by NOT IN (SELECT username FROM users) "
        "GROUP BY kind, status, d", (tz, since))
    sync_detail: dict[str, dict[str, int]] = {}
    for r in sync_rows:
        if not str(r["kind"]).startswith(SYNC_JOB_PREFIXES):
            continue
        n = int(r["n"])
        sd = sync_detail.setdefault(r["kind"], {"done": 0, "failed": 0})
        sd[r["status"]] += n
        counts["syncs"] += n
        b = day_bucket(_day(r["d"]))
        b["total"] += n
        b["categories"]["syncs"] = b["categories"].get("syncs", 0) + n
    # the latest run per synchronisation decides the status line ("op schema" vs "N mislukt → Systeem")
    failing_now = sorted(
        r["kind"] for r in db.fetchall(
            "SELECT DISTINCT ON (kind) kind, status FROM jobs WHERE updated_at >= %s AND status IN ('done','failed') "
            "AND created_by NOT IN (SELECT username FROM users) ORDER BY kind, updated_at DESC", (since,))
        if r["status"] == "failed" and str(r["kind"]).startswith(SYNC_JOB_PREFIXES))

    categories = [
        {"key": k, "label": CATEGORIES[k][0], "link": CATEGORIES[k][1], "tone": CATEGORIES[k][2],
         "count": counts[k], "examples": examples[k]}
        for k in ORDER if counts[k]
    ]
    sync_failed = sum(v["failed"] for v in sync_detail.values())
    sync_runs = sum(v["done"] + v["failed"] for v in sync_detail.values())
    by_day = []
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        by_day.append(per_day.get(d, {"date": d, "total": 0, "actions": 0, "categories": {}, "items": []}))
    return {
        "days": days,
        "since": since,
        "generated_at": now,
        "timezone": tz,
        "mode": state.mode(),
        "paused": state.paused(),
        "autopilot": state.autopilot(),
        "automation_allowed": state.automation_allowed(),
        "total": sum(counts.values()),
        "actions_total": sum(v for k, v in counts.items() if k not in NOT_ACTIONS),
        "categories": categories,
        "items": items,
        "actions": _actions(items, db, now),
        "by_day": by_day,
        "syncs": {"jobs": [{"kind": k, **v} for k, v in sorted(sync_detail.items())], "failed": sync_failed,
                  "runs": sync_runs, "failing_now": failing_now,
                  "state": "failing" if failing_now else ("ok" if sync_runs else "idle"),
                  "link": CATEGORIES["syncs"][1]},
        "waiting": waiting(db),
    }


# ------------------------------------------------------------------ wacht op jou
# recommendation prefixes that are calm information, never a decision (e.g. "je hebt dit zelf teruggezet")
INFO_PREFIXES = ("footprint.override:",)
GROUP_LABELS = {
    "automation.rollback": "Terugdraaien aanbevolen",
    "torrent-suspicious": "Verdachte torrents",
}


def _rec_prefix(dedupe_key: str | None, title: str) -> str:
    if dedupe_key and ":" in dedupe_key:
        return dedupe_key.split(":", 1)[0]
    return (dedupe_key or title)[:60]


def _cleanup_hashes(ops: list[dict[str, Any]]) -> set[str]:
    from mso.features.torrent.cleanup import DELETE_RE

    return {m[1] for op in ops if (m := DELETE_RE.match(str(op.get("path") or "")))}


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def waiting(db: Database | None = None) -> dict[str, Any]:
    """Open changes that need a person: approval (deleting, cleanup, …), approved-but-not-applied, and safe
    proposals that cannot be applied automatically in the current mode — plus open recommendations that ask for a
    decision. One decision = one row: per-torrent malware recommendations that the open cleanup bundle already
    covers are left out, the bundle shows once, the rest is grouped. ``count`` = rows (as before), ``decisions`` =
    real decisions (a group of 3 recommendations is 3, the bundle is 1), ``info`` = calm notes (no decision)."""
    from mso.features.automation import autopilot
    from mso.features.automation.engine import change_area
    from mso.features.torrent.cleanup import SOURCE as CLEANUP_SOURCE
    from mso.features.torrent.ingest import SUSPICIOUS_KEY

    db = db or get_db()
    now = datetime.now(UTC)
    rows = db.fetchall(
        "SELECT * FROM change_sets WHERE status IN ('preview','approved') ORDER BY "
        "CASE risk WHEN 'toestemming_nodig' THEN 0 ELSE 1 END, created_at DESC LIMIT 200")
    # malware cleanup: the biggest open cleanup change is "the bundle"; others it fully covers fold into it
    cleanups = [(dict(r), _cleanup_hashes(r["ops"] or [])) for r in rows if r["source"] == CLEANUP_SOURCE]
    cleanups = [c for c in cleanups if c[1]]
    bundle = max(cleanups, key=lambda c: (len(c[1]), c[0]["id"]), default=None)
    covered: set[str] = set().union(*(c[1] for c in cleanups)) if cleanups else set()
    folded = {c[0]["id"] for c in cleanups if bundle and c[0]["id"] != bundle[0]["id"] and c[1] <= bundle[1]}
    recs = db.fetchall(
        "SELECT id, area, subject, title, dedupe_key, evidence, created_at, risk FROM recommendations "
        "WHERE status='open' AND change_id IS NULL AND (risk='toestemming_nodig' OR dedupe_key LIKE ANY(%s)) "
        "ORDER BY updated_at DESC LIMIT 500",
        ([p + "%" for p in INFO_PREFIXES],))
    sus_recs = [r for r in recs
                if r["risk"] == "toestemming_nodig" and str(r["dedupe_key"] or "").startswith(SUSPICIOUS_KEY)]
    new_in_bundle = sum(
        1 for r in sus_recs
        if bundle and str((r["evidence"] or {}).get("hash") or "").lower() in bundle[1]
        and r["created_at"] >= now - timedelta(days=1))

    items: list[dict[str, Any]] = []
    for r in rows:
        if r["id"] in folded:
            continue
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
        item = {"id": r["id"], "title": r["title"], "target": r["target"], "risk": r["risk"],
                "status": r["status"], "created_at": r["created_at"], "kind": kind, "reason": reason,
                "deletes": deletes, "ops": len(ops), "decisions": 1, "group": False, "examples": [],
                "change_ids": [r["id"]], "new_since_yesterday": None,
                "link": f"/automatisering?tab=wijzigingen&change={r['id']}"}
        if bundle and r["id"] == bundle[0]["id"]:
            n = len(bundle[1])
            item["title"] = f"{n} {_plural(n, 'nep-download', 'nep-downloads')} opruimen" + (
                f" (+{new_in_bundle} nieuw sinds gisteren)" if new_in_bundle else "")
            item["change_title"] = r["title"]
            item["new_since_yesterday"] = new_in_bundle
            item["change_ids"] = [r["id"], *sorted(folded)]
            item["bundle"] = True
        items.append(item)

    # recommendations: drop the ones the bundle covers, group the rest (one row per kind)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    info: list[dict[str, Any]] = []
    for r in recs:
        key = str(r["dedupe_key"] or "")
        if key.startswith(INFO_PREFIXES):
            info.append({"id": f"rec:{r['id']}", "title": r["title"], "target": r["area"],
                         "created_at": r["created_at"],
                         "link": "/automatisering/voetafdruk" if key.startswith("footprint.") else
                         "/automatisering?tab=aanbevelingen"})
            continue
        if r["risk"] != "toestemming_nodig":
            continue
        if key.startswith(SUSPICIOUS_KEY) and str((r["evidence"] or {}).get("hash") or "").lower() in covered:
            continue  # already in the open cleanup bundle
        groups.setdefault((r["area"], _rec_prefix(r["dedupe_key"], r["title"])), []).append(dict(r))
    for (area, prefix), members in groups.items():
        first = members[0]
        if prefix == SUSPICIOUS_KEY.rstrip(":"):
            n = len(members)
            items.append({
                "id": f"group:{area}:{prefix}", "title": f"{n} verdachte {_plural(n, 'torrent', 'torrents')} opruimen",
                "target": area, "risk": "toestemming_nodig", "status": "open",
                "created_at": max(m["created_at"] for m in members), "kind": "bekijken",
                "reason": "Nog niet in een opruimvoorstel; MSO zet het voorstel klaar bij de volgende ronde, of maak "
                          "het zelf op Torrentclient",
                "deletes": True, "ops": 0, "decisions": 1, "group": n > 1,
                "examples": [m["title"] for m in members[:3]], "change_ids": [], "new_since_yesterday": None,
                "link": "/torrentclient"})
            continue
        if len(members) == 1:
            items.append({"id": f"rec:{first['id']}", "title": first["title"], "target": area,
                          "risk": "toestemming_nodig", "status": "open", "created_at": first["created_at"],
                          "kind": "bekijken", "reason": "Aanbeveling die jouw beslissing vraagt", "deletes": False,
                          "ops": 0, "decisions": 1, "group": False, "examples": [], "change_ids": [],
                          "new_since_yesterday": None, "link": "/automatisering?tab=aanbevelingen"})
            continue
        n = len(members)
        label = GROUP_LABELS.get(prefix) or "aanbevelingen"
        items.append({"id": f"group:{area}:{prefix}", "title": f"{n}× {label}" if prefix in GROUP_LABELS
                      else f"{n} {label}: {first['title']}…",
                      "target": area, "risk": "toestemming_nodig", "status": "open",
                      "created_at": max(m["created_at"] for m in members), "kind": "bekijken",
                      "reason": "Aanbevelingen die jouw beslissing vragen", "deletes": False, "ops": 0,
                      "decisions": n, "group": True, "examples": [m["title"] for m in members[:3]],
                      "change_ids": [], "new_since_yesterday": None, "link": "/automatisering?tab=aanbevelingen"})
    return {"count": len(items), "decisions": sum(i["decisions"] for i in items), "items": items[:50],
            "info": info[:20], "info_count": len(info)}


@router.get("/autopilot")
def autopilot_summary(days: int = Query(1, ge=1, le=31),
                      _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return summary(days)


@router.get("/waiting")
def waiting_summary(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """'Wacht op jou' alone (light; the shell polls it for the topbar counter, nav badge and tab title)."""
    return waiting()


__all__ = ["CATEGORIES", "classify", "effect_of", "local_tz", "rollback_of", "router", "summary", "waiting",
           "why_of"]
