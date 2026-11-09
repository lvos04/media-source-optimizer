"""Doelenkaart (1.6.0): the owner's three trial goals, measured honestly.

``GET /api/v1/overview/goals`` (viewer) — per goal the start value, the value now, the target, "dag X van 28", a
daily trend and one sentence about what holds the goal back:

- **Faalratio < 10 %** — rolling 7 days, per episode/film (*netto*: an item that failed and then arrived after all
  counts as succeeded) next to per attempt (*bruto*), each with n; below ``MIN_RATIO_N`` it says "te weinig data".
  The goal status follows *netto* (what the owner experiences: did the episode/film arrive); *bruto* is always
  shown next to it. A failure counts at the moment it became failed (``download_outcomes.failed_at``), never at
  its last observation.
- **0 verdachte torrents** — torrents in qBittorrent whose name/files contain an executable (the same
  ``suspicious_payload`` finding the Torrentclient page uses).
- **0 falende servers** — SABnzbd servers that are *enabled and failing*. Servers MSO switched off because they do not
  work (automatically in Autopilot, or through an approved "kapotte servers uitschakelen" change) are counted
  separately ("4 uitgezet omdat ze niet werken; herstellen of verwijderen in SABnzbd is jouw keuze").

The trial starts on ``goals.trial_start`` (setting) or automatically on the day Autopilot was first switched on
(audit log), else the setup day; it lasts ``TRIAL_DAYS`` days. Days are local days in ``local_tz()`` (``TZ``,
default Europe/Amsterdam).

**Start value** = the state at the start of the trial, never a later measurement: the failure ratio over the 7 days
before the start; suspicious torrents present and servers SABnzbd reported failing during the first hour from the
start (``START_WINDOW``) — or, when MSO was not yet watching qBittorrent/SABnzbd at the start, during the first hour
from the moment it began to (first torrent seen / SABnzbd connector first saved). One hour so that what MSO does
right after the start (Autopilot switches an expired server off within minutes) never erases the starting point.
Until that hour has passed the start value is ``provisional``; afterwards the hourly job freezes it per trial start
in ``system_state['goals.start']``, so it never changes again (and survives pruning of history).

**Trend** = per local day the value at the end of that day (today: now). The hourly job ``overview.goals_snapshot``
stores one row per local day in ``goal_snapshots`` (later runs of the day overwrite it, so it holds the end-of-day
value); days without a snapshot are reconstructed at the end of the day from MSO's own history (torrent first-seen and
removal times, SABnzbd server error periods) and marked ``reconstructed``. The failure ratio is always computed from
``download_outcomes`` as it was known at the end of each day. Read-only; nothing is estimated beyond that, no
secrets.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends

from mso import jobs, state
from mso import settings_registry as sr
from mso.auth import Principal, require
from mso.db import Database, get_db, jsonb
from mso.domain.outcomes import FAILURE_REASONS, MIN_RATIO_N, failure_ratios, item_keys, outcome_at

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/overview", tags=["overzicht"])

TRIAL_DAYS = 28
DEFAULT_TZ = "Europe/Amsterdam"
WINDOW = timedelta(days=7)  # rolling window of the failure ratio
SUCCESS_LOOKBACK = timedelta(days=30)  # an item that arrived this long before its failed attempt counts as arrived
TARGET_FAILURE_RATIO = 0.10
START_WINDOW = timedelta(hours=1)  # the start value covers the first hour of the trial (see the module docstring)
EVENT_SLACK = timedelta(minutes=15)  # SABnzbd server status is polled every 5 min: an error lasts until the next poll
START_STATE = "goals.start"  # system_state key with the frozen start values per trial start
CLEANUP_SOURCE = "torrent-cleanup"  # mso.features.torrent.cleanup.SOURCE
SUS_SQL = (
    "EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(diagnosis->'findings', '[]'::jsonb)) f "
    "WHERE f->>'code' = 'suspicious_payload')"
)
# serverstatus.FAILING (copied so the card works when the usenet module is not loaded)
FAILING_FALLBACK = {"account_expired", "auth_failed", "access_denied", "block_exhausted", "unreachable", "tls_error",
                    "sab_blocked"}

sr.define(
    "goals.trial_start",
    "Start proefperiode (doelenkaart)",
    "string",
    "",
    category="automatisering",
    help="Datum als JJJJ-MM-DD. Leeg = automatisch: de dag dat Autopilot voor het eerst aanging, anders de dag van "
    "de installatie. De proefperiode duurt 28 dagen; de doelenkaart op Overzicht telt de dagen en vergelijkt met de "
    "waarde op de startdag.",
)


# ------------------------------------------------------------------ time
def local_tz() -> tzinfo:
    """The owner's time zone for day boundaries: ``TZ`` (as set in the container) when it is a valid IANA name,
    else Europe/Amsterdam."""
    for name in ((os.environ.get("TZ") or "").strip().lstrip(":"), DEFAULT_TZ):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return UTC


def day_start(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz).astimezone(UTC)


def day_end(d: date, tz: tzinfo) -> datetime:
    return day_start(d + timedelta(days=1), tz)


# ------------------------------------------------------------------ trial period
def _parse_date(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def trial(db: Database, now: datetime, tz: tzinfo) -> dict[str, Any]:
    """Start (setting / first Autopilot / setup), day X of ``TRIAL_DAYS`` and end, in local days."""
    raw = str(sr.get("goals.trial_start", db) or "").strip()
    warning = None
    start: datetime | None = None
    source = "setting"
    if raw:
        d = _parse_date(raw)
        if d is None:
            warning = f"Ongeldige startdatum '{raw[:20]}' genegeerd (gebruik JJJJ-MM-DD); automatisch bepaald."
        else:
            start = day_start(d, tz)
    if start is None:
        source = "autopilot"
        start = db.scalar(
            "SELECT min(ts) FROM audit_log WHERE (action='system.mode' AND detail->>'new'='autopilot') "
            "OR (action='setup.completed' AND detail->>'mode'='autopilot')"
        )
    if start is None:
        source = "setup"
        start = db.scalar(
            "SELECT LEAST((SELECT min(ts) FROM audit_log WHERE action IN ('setup.completed','setup.admin_created')), "
            "(SELECT min(created_at) FROM users))"
        )
    if start is None:
        source, start = "today", now
    start_day = start.astimezone(tz).date()
    today = now.astimezone(tz).date()
    day = (today - start_day).days + 1
    end_day = start_day + timedelta(days=TRIAL_DAYS - 1)
    return {
        "start": start,
        "start_date": start_day.isoformat(),
        "start_source": source,
        "start_warning": warning,
        "length_days": TRIAL_DAYS,
        "day": max(0, min(day, TRIAL_DAYS)),
        "days_since_start": day,
        "not_started": day < 1,
        "ended": day > TRIAL_DAYS,
        "end_date": end_day.isoformat(),
        "timezone": getattr(tz, "key", "UTC"),
        "today": today.isoformat(),
    }


# ------------------------------------------------------------------ failure ratio
def outcome_rows(db: Database, since: datetime, until: datetime | None = None) -> list[dict[str, Any]]:
    """Finished outcomes with their moment ``at`` in ``(since, until]`` (``until`` open = up to now)."""
    at = outcome_at()
    bound, args = (f" AND {at} <= %s", (since, until)) if until is not None else ("", (since,))
    return db.fetchall(
        f"SELECT client, client_ref, protocol, title, media_category, status, failure_reason, {at} AS at "
        f"FROM download_outcomes WHERE status IN ('success','failed') AND {at} > %s{bound} ORDER BY {at}",
        args,
    )


def retry_links(db: Database) -> list[tuple[str, str]]:
    """SABnzbd retries that got a new nzo_id: both jobs are the same item."""
    return [
        (r["nzo_id"], r["new_nzo_id"])
        for r in db.fetchall(
            "SELECT nzo_id, new_nzo_id FROM usenet_retries WHERE new_nzo_id IS NOT NULL AND new_nzo_id <> nzo_id"
        )
    ]


def ratio_now(db: Database, now: datetime | None = None) -> dict[str, Any]:
    """Netto + bruto failure ratio over the last 7 days (used by the failures block of ``GET /overview``)."""
    now = now or datetime.now(UTC)
    rows = outcome_rows(db, now - WINDOW - SUCCESS_LOOKBACK)
    return failure_ratios(rows, now - WINDOW, now, retry_links(db))


def _reason_label(code: str) -> str:
    label = FAILURE_REASONS.get(code, code)
    return label[:1].lower() + label[1:]


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def failure_start(db: Database, tr: dict[str, Any], now: datetime,
                  links: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Start value of the failure ratio: the 7 days before the trial start (as known now; only those rows are read)."""
    s = tr["start"]
    base: dict[str, Any] = {"date": tr["start_date"], "window_from": _iso(s - WINDOW), "window_until": _iso(s)}
    if s > now:  # the trial has not started yet
        return base | {"value": None, "n": 0, "failed": 0, "insufficient": True, "gross": None, "gross_n": 0,
                       "source": None, "provisional": True}
    rows = outcome_rows(db, s - WINDOW - SUCCESS_LOOKBACK, s)
    r = failure_ratios(rows, s - WINDOW, s, links if links is not None else retry_links(db))
    return base | {"value": r["net"]["value"], "n": r["net"]["n"], "failed": r["net"]["failed"],
                   "insufficient": r["net"]["insufficient"], "gross": r["gross"]["value"], "gross_n": r["gross"]["n"],
                   "source": "computed", "provisional": now < s + START_WINDOW}


def _failure_goal(db: Database, days: list[date], tr: dict[str, Any], now: datetime, tz: tzinfo,
                  stored: dict[str, Any]) -> dict[str, Any]:
    links = retry_links(db)
    rows = outcome_rows(db, day_start(days[0], tz) - WINDOW - SUCCESS_LOOKBACK, now)
    keys = item_keys(rows, links)

    def at(end: datetime) -> dict[str, Any]:
        return failure_ratios(rows, end - WINDOW, end, keys=keys)

    cur = at(now)
    net, gross = cur["net"], cur["gross"]
    trend = []
    for d in days:
        r = cur if d == days[-1] else at(min(day_end(d, tz), now))
        trend.append({"date": d.isoformat(), "value": r["net"]["value"], "n": r["net"]["n"],
                      "gross": r["gross"]["value"], "gross_n": r["gross"]["n"],
                      "insufficient": r["net"]["insufficient"], "source": "computed"})
    start = stored.get("failure_ratio") or failure_start(db, tr, now, links)
    if net["n"] == 0 or net["insufficient"]:
        status = "insufficient"
    else:
        status = "met" if net["value"] < TARGET_FAILURE_RATIO else "not_met"
    blocker = None
    if status == "insufficient":
        blocker = {
            "text": f"Te weinig data: {net['n']} aflevering(en)/film(s) in 7 dagen (minimaal {MIN_RATIO_N}).",
            "link": "/downloads",
        }
    elif status == "not_met" and net["reasons"]:
        code, count = max(net["reasons"].items(), key=lambda kv: (kv[1], kv[0]))
        blocker = {
            "text": f"Meeste mislukkingen: {_reason_label(code)} ({count} van {net['failed']}).",
            "link": "/downloads",
            "reason": code,
        }
    reasons = sorted(
        ({"reason": k, "label": FAILURE_REASONS.get(k, k), "count": v} for k, v in net["reasons"].items()),
        key=lambda x: (-x["count"], x["reason"]),
    )
    return {
        "key": "failure_ratio",
        "label": "Faalratio",
        "unit": "ratio",
        "target": TARGET_FAILURE_RATIO,
        "comparison": "lt",
        "status": status,
        "now": {"value": net["value"], "n": net["n"], "failed": net["failed"], "insufficient": net["insufficient"],
                "gross": gross["value"], "gross_n": gross["n"], "gross_failed": gross["failed"],
                "gross_insufficient": gross["insufficient"]},
        "start": start,
        "min_n": MIN_RATIO_N,
        "window_days": WINDOW.days,
        "by_protocol": net["by_protocol"],
        "reasons": reasons[:6],
        "trend": trend,
        "blocker": blocker,
        "available": True,
        "reason": None,
    }


# ------------------------------------------------------------------ suspicious torrents
def suspicious_now(db: Database) -> int:
    return int(db.scalar(f"SELECT count(*) FROM torrent_state WHERE NOT removed AND {SUS_SQL}") or 0)


def suspicious_at(db: Database, ts: datetime) -> int:
    """Reconstruction at moment ``ts`` (end of a day without snapshot): suspicious torrents first seen before ``ts``
    and not yet removed at ``ts`` (``updated_at`` of a removed torrent is its removal time)."""
    return int(
        db.scalar(
            f"SELECT count(*) FROM torrent_state WHERE {SUS_SQL} AND first_seen <= %s "
            "AND (NOT removed OR updated_at > %s)",
            (ts, ts),
        )
        or 0
    )


def _torrent_available(db: Database) -> bool:
    from mso.connectors import base as cb

    return cb.is_configured("qbittorrent") or bool(db.scalar("SELECT count(*) FROM torrent_state"))


def suspicious_start(db: Database, tr: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Start value: suspicious torrents present at some moment during the first hour from the trial start, or from the
    first torrent MSO saw when it was not yet watching qBittorrent at the start (``updated_at`` of a removed torrent
    is its removal time). A cleanup right after the start therefore never erases the starting point."""
    base: dict[str, Any] = {"date": tr["start_date"], "source": None, "window_from": None, "window_until": None}
    first = db.scalar("SELECT min(first_seen) FROM torrent_state")
    if first is None:
        return base | {"value": None, "provisional": False}
    m = max(tr["start"], first)
    base |= {"window_from": _iso(m), "window_until": _iso(m + START_WINDOW)}
    if m > now:
        return base | {"value": None, "provisional": True}
    value = int(db.scalar(
        f"SELECT count(*) FROM torrent_state WHERE {SUS_SQL} AND first_seen <= %s "
        "AND (NOT removed OR updated_at >= %s)",
        (min(m + START_WINDOW, now), m)) or 0)
    return base | {"value": value, "source": "history", "provisional": now < m + START_WINDOW}


def _suspicious_goal(db: Database, days: list[date], snaps: dict[date, dict[str, Any]], tr: dict[str, Any],
                     now: datetime, tz: tzinfo, stored: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {"key": "suspicious_torrents", "label": "Verdachte torrents", "unit": "count",
                            "target": 0, "comparison": "eq"}
    if not _torrent_available(db):
        return base | {"available": False, "reason": "qBittorrent is niet gekoppeld", "status": "unavailable",
                       "now": {"value": None}, "start": {"value": None, "date": tr["start_date"], "source": None},
                       "trend": [], "blocker": None}
    cur = suspicious_now(db)
    # every suspicious torrent that arrived this week, also the ones already cleaned up: does prevention work?
    new_7d = int(db.scalar(
        f"SELECT count(*) FROM torrent_state WHERE {SUS_SQL} AND first_seen > %s", (now - timedelta(days=7),)) or 0)
    change = db.fetchone(
        "SELECT id, ops, status FROM change_sets WHERE target='qbittorrent' AND source=%s "
        "AND status IN ('preview','approved') ORDER BY id DESC LIMIT 1",
        (CLEANUP_SOURCE,),
    )
    waiting = sum(1 for op in (change["ops"] or []) if str(op.get("path", "")).startswith("torrent:")) if change else 0
    trend = []
    for d in days:
        if d == days[-1]:
            trend.append({"date": d.isoformat(), "value": cur, "source": "live"})
        elif d in snaps and snaps[d].get("suspicious") is not None:
            trend.append({"date": d.isoformat(), "value": snaps[d]["suspicious"], "source": "snapshot"})
        else:
            trend.append({"date": d.isoformat(), "value": suspicious_at(db, min(day_end(d, tz), now)),
                          "source": "reconstructed"})
    start = stored.get("suspicious_torrents") or suspicious_start(db, tr, now)
    blocker = None
    if cur:
        news = f" · {new_7d} nieuw in 7 dagen" if new_7d else " · geen nieuwe in 7 dagen"
        if change and waiting:
            blocker = {"text": f"{waiting} wachten op jouw goedkeuring (opruimvoorstel){news}.",
                       "link": f"/automatisering?tab=wijzigingen&change={change['id']}"}
        else:
            blocker = {"text": f"{cur} verdachte torrent(s) zonder opruimvoorstel; maak er een op Torrentclient{news}.",
                       "link": "/torrentclient"}
    return base | {
        "available": True,
        "reason": None,
        "status": "met" if cur == 0 else "not_met",
        "now": {"value": cur, "new_7d": new_7d, "awaiting_approval": waiting,
                "change_id": change["id"] if change else None},
        "start": start,
        "trend": trend,
        "blocker": blocker,
    }


# ------------------------------------------------------------------ failing servers
def _failing_statuses() -> set[str]:
    try:
        from mso.features.usenet import serverstatus
    except ImportError:
        return set(FAILING_FALLBACK)
    return set(serverstatus.FAILING)


def _server_name(row: dict[str, Any], labels: dict[str, str]) -> str:
    try:
        from mso.features.usenet import serverstatus
    except ImportError:
        return str(row.get("displayname") or row["server"])
    return serverstatus.display_name(
        {"server": row["server"], "displayname": row.get("displayname") or row["server"],
         "short": serverstatus.short_name(row.get("displayname") or row["server"])},
        labels,
    )


def _status_label(status: str) -> str:
    try:
        from mso.features.usenet import serverstatus
    except ImportError:
        return status
    return serverstatus.LABELS.get(status, serverstatus.LABELS["error"])


OUTSIDE_MSO = "buiten MSO uitgeschakeld"  # autoheal.last_result when the user switched a server off during a probe


def _mso_switched_off(db: Database) -> dict[str, dict[str, Any]]:
    """Per SABnzbd server the latest *applied* change (not rolled back) that set its ``enable``: MSO switched it off
    when that op has ``after`` 0. Covers both Autopilot and an approved "kapotte servers uitschakelen" change in
    beheermodus (which creates no autoheal row)."""
    rows = db.fetchall(
        "SELECT DISTINCT ON (srv) srv, after, applied_at, id, impact FROM ("
        " SELECT cs.id, cs.applied_at, cs.impact, substring(op->>'path' from '^servers/(.+)/enable$') AS srv,"
        " op->>'after' AS after FROM change_sets cs CROSS JOIN LATERAL jsonb_array_elements(cs.ops) op"
        " WHERE cs.target = 'sabnzbd' AND cs.status = 'applied' AND op->>'path' LIKE 'servers/%%/enable') x "
        "WHERE srv IS NOT NULL ORDER BY srv, applied_at DESC NULLS LAST, id DESC"
    )
    out = {}
    for r in rows:
        if str(r["after"]).lower() in ("0", "false"):
            statuses = (r["impact"] or {}).get("statuses") if isinstance(r["impact"], dict) else None
            out[r["srv"]] = {"change_id": r["id"], "applied_at": r["applied_at"],
                             "reason": statuses.get(r["srv"]) if isinstance(statuses, dict) else None}
    return out


def servers_now(db: Database) -> dict[str, Any]:
    """Enabled-and-failing SABnzbd servers, servers MSO switched off (still off), servers in a probe."""
    failing_set = sorted(_failing_statuses())
    labels = {r["sab_server"]: r["label"] for r in db.fetchall(
        "SELECT sab_server, label FROM usenet_providers WHERE sab_server IS NOT NULL")}
    rows = db.fetchall("SELECT server, displayname, status, enabled, since FROM usenet_sab_servers ORDER BY server")
    heal = {r["server"]: r for r in db.fetchall(
        "SELECT server, status, reason, disabled_at, last_result FROM usenet_server_autoheal")}
    changed = _mso_switched_off(db) if any(not r["enabled"] for r in rows) else {}
    failing, disabled, probing = [], [], []
    for r in rows:
        h = heal.get(r["server"])
        entry = {"server": r["server"], "name": _server_name(r, labels), "status": r["status"],
                 "label": _status_label(r["status"]), "since": r["since"]}
        if r["enabled"] and r["status"] in failing_set:
            failing.append(entry)
        if h and h["status"] == "probing" and r["enabled"]:
            probing.append(entry)
        if r["enabled"]:
            continue
        # switched off by MSO and still off: an autoheal state other than a running probe or a recovery (not when
        # the user switched it off during a probe), or an applied MSO change whose latest enable-op set it to 0
        by_heal = bool(h and h["status"] not in ("probing", "recovered") and h["last_result"] != OUTSIDE_MSO)
        ch = changed.get(r["server"])
        if by_heal and h:
            reason = h["reason"] or (ch or {}).get("reason")
            disabled.append(entry | {"reason": reason, "reason_label": _status_label(reason or "error"),
                                     "disabled_at": h["disabled_at"] or (ch or {}).get("applied_at"),
                                     "given_up": h["status"] == "given_up", "via": "autopilot"})
        elif ch and not (h and h["last_result"] == OUTSIDE_MSO):
            disabled.append(entry | {"reason": ch["reason"], "reason_label": _status_label(ch["reason"] or "error"),
                                     "disabled_at": ch["applied_at"], "given_up": False, "via": "change",
                                     "change_id": ch["change_id"]})
    return {"known": bool(rows), "failing": failing, "disabled_by_mso": disabled, "probing": probing}


def failing_servers_at(db: Database, ts: datetime) -> int:
    """Reconstruction at moment ``ts`` (end of a day without snapshot): servers in a failing period that SABnzbd
    reported up to ``ts`` (the status lasts until the next poll; SABnzbd only reports servers it uses, i.e. enabled
    ones; a server switched off stops being reported)."""
    return int(
        db.scalar(
            "SELECT count(DISTINCT server) FROM usenet_sab_server_events WHERE status = ANY(%s) "
            "AND first_at <= %s AND last_at >= %s",
            (sorted(_failing_statuses()), ts, ts - EVENT_SLACK),
        )
        or 0
    )


def servers_start(db: Database, tr: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Start value: servers SABnzbd reported failing at some moment during the first hour from the trial start, or
    from the moment the SABnzbd connector was first saved when that was later. A server Autopilot switches off right
    after the start (account verlopen) therefore still counts at the start."""
    configured = db.scalar(
        "SELECT min(ts) FROM audit_log WHERE action='connector.updated' AND target='sabnzbd'")
    m = max(tr["start"], configured) if configured else tr["start"]
    base: dict[str, Any] = {"date": tr["start_date"], "source": None, "window_from": _iso(m),
                            "window_until": _iso(m + START_WINDOW)}
    if m > now:
        return base | {"value": None, "provisional": True}
    value = int(db.scalar(
        "SELECT count(DISTINCT server) FROM usenet_sab_server_events WHERE status = ANY(%s) "
        "AND first_at <= %s AND last_at >= %s",
        (sorted(_failing_statuses()), min(m + START_WINDOW, now), m - EVENT_SLACK)) or 0)
    return base | {"value": value, "source": "history", "provisional": now < m + START_WINDOW}


def _disabled_text(n: int) -> str:
    if n == 1:
        return "1 uitgezet omdat hij niet werkt; herstellen of verwijderen in SABnzbd is jouw keuze"
    return f"{n} uitgezet omdat ze niet werken; herstellen of verwijderen in SABnzbd is jouw keuze"


def _servers_goal(db: Database, days: list[date], snaps: dict[date, dict[str, Any]], tr: dict[str, Any],
                  now: datetime, tz: tzinfo, stored: dict[str, Any]) -> dict[str, Any]:
    from mso.connectors import base as cb

    base: dict[str, Any] = {"key": "failing_servers", "label": "Falende servers", "unit": "count", "target": 0,
                            "comparison": "eq"}
    sv = servers_now(db)
    if not sv["known"]:
        reason = ("SABnzbd heeft nog geen serverstatus gegeven" if cb.is_configured("sabnzbd")
                  else "SABnzbd is niet gekoppeld")
        return base | {"available": False, "reason": reason, "status": "unavailable", "now": {"value": None},
                       "start": {"value": None, "date": tr["start_date"], "source": None}, "trend": [],
                       "blocker": None, "failing": [], "disabled_by_mso": [], "probing": [], "disabled_text": None}
    cur = len(sv["failing"])
    trend = []
    for d in days:
        if d == days[-1]:
            trend.append({"date": d.isoformat(), "value": cur, "source": "live"})
        elif d in snaps and snaps[d].get("failing_servers") is not None:
            trend.append({"date": d.isoformat(), "value": snaps[d]["failing_servers"], "source": "snapshot"})
        else:
            trend.append({"date": d.isoformat(), "value": failing_servers_at(db, min(day_end(d, tz), now)),
                          "source": "reconstructed"})
    start = stored.get("failing_servers") or servers_start(db, tr, now)
    blocker = None
    if cur:
        names = " · ".join(f"{s['name']} ({s['label'].lower()})" for s in sv["failing"][:4])
        more = f" · +{cur - 4}" if cur > 4 else ""
        waiting = db.scalar(
            "SELECT count(*) FROM change_sets WHERE target='sabnzbd' AND status IN ('preview','approved') "
            "AND (dedupe_key LIKE 'usenet.disable\\_failing:%%' OR dedupe_key LIKE 'usenet.auto\\_off:%%')"
        )
        suffix = " — uitschakelen wacht op jouw goedkeuring" if waiting else ""
        blocker = {"text": f"Ingeschakeld maar falend: {names}{more}{suffix}.", "link": "/usenet-providers"}
    n_off = len(sv["disabled_by_mso"])
    return base | {
        "available": True,
        "reason": None,
        "status": "met" if cur == 0 else "not_met",
        "now": {"value": cur, "disabled_by_mso": n_off, "probing": len(sv["probing"])},
        "start": start,
        "trend": trend,
        "blocker": blocker,
        "failing": sv["failing"],
        "disabled_by_mso": sv["disabled_by_mso"],
        "probing": sv["probing"],
        "disabled_text": _disabled_text(n_off) if n_off else None,
    }


# ------------------------------------------------------------------ assembly
def _direction(goal: dict[str, Any]) -> str | None:
    """Now vs start (lower is better for all three goals); none when either side has too little data."""
    start = goal.get("start") or {}
    a, b = start.get("value"), (goal.get("now") or {}).get("value")
    if a is None or b is None or goal.get("status") in ("insufficient", "unavailable"):
        return None
    if goal.get("unit") == "ratio" and (start.get("insufficient") or int(start.get("n") or 0) < MIN_RATIO_N):
        return None  # a start ratio over fewer than MIN_RATIO_N items says nothing ("te weinig data")
    eps = 0.005 if goal.get("unit") == "ratio" else 0
    if b < a - eps:
        return "better"
    if b > a + eps:
        return "worse"
    return "same"


def stored_starts(db: Database, tr: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Start values frozen by the hourly job for this trial start (``{}`` when the start changed or none yet)."""
    raw = state.get_state(START_STATE, None, db)
    if not isinstance(raw, dict) or tr["start_source"] == "today" or raw.get("trial_start") != _iso(tr["start"]):
        return {}
    return {k: dict(v) | {"frozen": True} for k, v in (raw.get("goals") or {}).items() if isinstance(v, dict)}


def freeze_starts(db: Database, now: datetime) -> list[str]:
    """Freeze each goal's start value once its measurement window is complete, so it never changes afterwards (and
    survives pruning of the history it was derived from). Recomputed when the trial start changes."""
    tr = trial(db, now, local_tz())
    if tr["start_source"] == "today":
        return []
    stored = stored_starts(db, tr)
    new: dict[str, dict[str, Any]] = {}
    if "failure_ratio" not in stored:
        st = failure_start(db, tr, now)
        if not st["provisional"] and st["n"]:
            new["failure_ratio"] = st
    if "suspicious_torrents" not in stored and _torrent_available(db):
        st = suspicious_start(db, tr, now)
        if not st["provisional"] and st["value"] is not None:
            new["suspicious_torrents"] = st
    if "failing_servers" not in stored and db.scalar("SELECT count(*) FROM usenet_sab_servers"):
        st = servers_start(db, tr, now)
        if not st["provisional"] and st["value"] is not None:
            new["failing_servers"] = st
    if new:
        keep = {k: {f: x for f, x in v.items() if f != "frozen"} for k, v in stored.items()}
        state.set_state(START_STATE, {"trial_start": _iso(tr["start"]), "frozen_at": _iso(now),
                                      "goals": keep | new}, db)
    return sorted(new)


def snapshots(db: Database, since: date) -> dict[date, dict[str, Any]]:
    return {r["day"]: dict(r["metrics"] or {}) for r in db.fetchall(
        "SELECT day, metrics FROM goal_snapshots WHERE day >= %s ORDER BY day", (since,))}


def compute(db: Database | None = None, now: datetime | None = None, *, trend: bool = True) -> dict[str, Any]:
    """All three goals. ``trend=False`` only computes today (start and now values, no daily series)."""
    db = db or get_db()
    now = now or datetime.now(UTC)
    tz = local_tz()
    tr = trial(db, now, tz)
    today = now.astimezone(tz).date()
    start_day = date.fromisoformat(tr["start_date"])
    first = max(start_day, today - timedelta(days=TRIAL_DAYS - 1))
    if first > today:  # trial starts in the future: show the last week for orientation
        first = today - timedelta(days=6)
    if not trend:
        first = today
    days = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    snaps = snapshots(db, days[0])
    stored = stored_starts(db, tr)
    goals = [
        _failure_goal(db, days, tr, now, tz, stored),
        _suspicious_goal(db, days, snaps, tr, now, tz, stored),
        _servers_goal(db, days, snaps, tr, now, tz, stored),
    ]
    for g in goals:
        g["direction"] = _direction(g)
    met = sum(1 for g in goals if g["status"] == "met")
    return {
        "available": True,
        "trial": tr,
        "goals": goals,
        "met": met,
        "total": len(goals),
        "mode": state.mode(),
        "snapshot_days": len(snaps),
        "generated_at": now,
    }


def summary(db: Database | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Compact block for ``GET /overview``: per goal status, value now and target (no trend)."""
    full = compute(db, now, trend=False)
    return {
        "available": True,
        "trial": {k: full["trial"][k] for k in ("start_date", "day", "length_days", "ended", "not_started")},
        "met": full["met"],
        "total": full["total"],
        "items": [{"key": g["key"], "label": g["label"], "status": g["status"], "value": g["now"].get("value"),
                   "target": g["target"], "direction": g["direction"]} for g in full["goals"]],
    }


# ------------------------------------------------------------------ daily snapshot
def snapshot(db: Database | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Store today's goal values (local day; later runs of the same day overwrite it, so the row holds the value at
    the end of the day) and freeze the start values whose first hour has passed."""
    db = db or get_db()
    now = now or datetime.now(UTC)
    tz = local_tz()
    r = ratio_now(db, now)
    metrics: dict[str, Any] = {
        "failure_net": r["net"]["value"],
        "failure_net_n": r["net"]["n"],
        "failure_gross": r["gross"]["value"],
        "failure_gross_n": r["gross"]["n"],
        "suspicious": suspicious_now(db) if _torrent_available(db) else None,
        "failing_servers": None,
        "disabled_servers": None,
    }
    sv = servers_now(db)
    if sv["known"]:
        metrics["failing_servers"] = len(sv["failing"])
        metrics["disabled_servers"] = len(sv["disabled_by_mso"])
    day = now.astimezone(tz).date()
    db.execute(
        "INSERT INTO goal_snapshots(day, metrics, taken_at) VALUES (%s,%s,%s) ON CONFLICT (day) DO UPDATE SET "
        "metrics=EXCLUDED.metrics, taken_at=EXCLUDED.taken_at",
        (day, jsonb(metrics), now),
    )
    return {"day": day.isoformat(), **metrics, "frozen_starts": freeze_starts(db, now)}


@jobs.handler("overview.goals_snapshot")
def _snapshot_job(ctx: jobs.JobContext) -> dict[str, Any]:
    return snapshot()


jobs.every("overview.goals_snapshot", 3600)


@router.get("/goals")
def goals(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return compute()


__all__ = ["compute", "freeze_starts", "local_tz", "ratio_now", "router", "snapshot", "summary", "trial"]
