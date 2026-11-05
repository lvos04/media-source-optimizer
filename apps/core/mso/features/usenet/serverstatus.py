"""SABnzbd's own view of its Usenet servers: login/account errors per server.

Sources (both read-only):
- ``mode=status`` (``fullstatus`` on older SABnzbd): per server ``servererror`` (SABnzbd's last warning for that
  server) and ``serveractive``;
- ``mode=warnings``: SABnzbd's warning list, e.g. "Server reader.xsnews.nl reported: 482 Account expired."
  Each warning is matched to a server by host, server name or display name.

Every message is classified with the same NNTP response classifier MSO uses for its own provider checks
(:func:`mso.features.usenet.nntp.classify_response`) plus SABnzbd-specific wording (access denied,
connection failures). The current state is kept per server in ``usenet_sab_servers``; failing periods are kept
in ``usenet_sab_server_events`` so a failed download can later be related to a server problem that was active
at that time. Every failing, enabled server gets an open recommendation (risk ``advies``); it is resolved
automatically when SABnzbd no longer reports the problem.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from mso import jobs
from mso import settings_registry as sr
from mso.connectors import base as cb
from mso.connectors.sabnzbd import SabClient, SabError
from mso.db import get_db
from mso.domain import outcomes
from mso.features.usenet.nntp import classify_response
from mso.security import redact

LOGGER = logging.getLogger(__name__)

# How long a SABnzbd warning (without a current ``servererror``) still counts as the server's state.
WARNING_WINDOW = timedelta(hours=6)
# Two observations of the same problem further apart than this start a new event.
EVENT_GAP = timedelta(hours=3)
# Account/login problems don't flap: a failed download up to this long before the first message is still
# related to it ("waarschijnlijk"), and up to EVENT_TAIL after the last one.
EVENT_LEAD = timedelta(hours=24)
EVENT_TAIL = timedelta(hours=2)

# Statuses that make a server useless for downloads (shown as danger, recommendation per server).
FAILING = {
    "account_expired",
    "auth_failed",
    "access_denied",
    "block_exhausted",
    "unreachable",
    "tls_error",
    "sab_blocked",
}
# Statuses that mean SABnzbd could not log in → likely cause of missing articles.
LOGIN_PROBLEMS = {"account_expired", "auth_failed", "access_denied", "block_exhausted"}
# Statuses for which MSO proposes disabling the server in SABnzbd (change set, toestemming_nodig).
DISABLE_SUGGESTED = {"account_expired", "auth_failed", "access_denied", "block_exhausted"}

LABELS = {
    "ok": "Werkt",
    "account_expired": "Account verlopen",
    "auth_failed": "Inloggen geweigerd",
    "access_denied": "Toegang geweigerd",
    "block_exhausted": "Blok op / limiet",
    "rate_limited": "Te veel verbindingen",
    "unreachable": "Onbereikbaar",
    "tls_error": "TLS-fout",
    "sab_blocked": "Tijdelijk geblokkeerd",
    "disabled": "Uitgeschakeld",
    "error": "Fout",
    "unknown": "Onbekend",
}

_EXPLAIN = {
    "ok": "SABnzbd meldt geen fouten voor deze server.",
    "account_expired": "De provider meldt dat het account verlopen is. SABnzbd krijgt hier geen artikelen "
    "meer; vernieuw het abonnement of schakel de server uit in SABnzbd.",
    "auth_failed": "De provider weigert de gebruikersnaam/het wachtwoord dat in SABnzbd staat. Controleer "
    "de inloggegevens in SABnzbd of schakel de server uit.",
    "access_denied": "De provider weigert de toegang (bijv. verkeerde inloggegevens, geblokkeerd of "
    "beëindigd account). Controleer het account en de inloggegevens in SABnzbd of schakel de server uit.",
    "block_exhausted": "Het blok of de datalimiet bij deze provider is op.",
    "rate_limited": "De provider meldt te veel verbindingen; verlaag het aantal verbindingen in SABnzbd.",
    "unreachable": "SABnzbd kan geen verbinding maken met deze server (host, poort of netwerk).",
    "tls_error": "De SSL/TLS-verbinding met deze server mislukt (poort/SSL-instelling of certificaat).",
    "sab_blocked": "SABnzbd heeft deze server tijdelijk buiten gebruik gezet na herhaalde fouten.",
    "disabled": "Deze server staat uit in SABnzbd.",
    "error": "SABnzbd meldt een fout voor deze server.",
    "unknown": "SABnzbd geeft (nog) geen status voor deze server.",
}

_CODE = re.compile(r"\b([2-5]\d\d)\b")
_ACCESS_DENIED = re.compile(r"access denied|toegang geweigerd|\bbanned\b|\bblocked\b|not allowed|forbidden", re.I)
_UNREACHABLE = re.compile(
    r"cannot connect|can't connect|failed to connect|unable to connect|connection (refused|reset|timed out)|"
    r"timed out|getaddrinfo|name or service not known|could not resolve|cannot resolve|no route|unreachable|"
    r"host not found",
    re.I,
)
_TLS = re.compile(r"certificate|\bssl\b|\btls\b|handshake", re.I)
_SERVER_WORDS = re.compile(r"server|login|account|authenticat|access|connect", re.I)


def classify_message(text: str) -> str | None:
    """SABnzbd server message → status, or ``None`` when it says nothing about the server's health."""
    t = (text or "").strip()
    if not t:
        return None
    m = _CODE.search(t)
    code = int(m.group(1)) if m else 0
    first = classify_response(code, t, "auth")  # expired / too many connections / block before codes
    if first in ("account_expired", "rate_limited", "block_exhausted"):
        return first
    if _ACCESS_DENIED.search(t):
        return "access_denied"
    if re.search(r"authenticat|login|log in|password|user/?pass|credentials", t, re.I) or code in (480, 481, 482):
        return "auth_failed"
    if _UNREACHABLE.search(t):
        return "unreachable"
    if _TLS.search(t):
        return "tls_error"
    if code == 502:
        return "auth_failed"
    if code and code >= 400:
        return "error"
    return None


def short_name(name: str) -> str:
    """"reader.xsnews.nl" → "xsnews"; names that aren't host names are kept."""
    n = (name or "").strip()
    parts = n.lower().split(".")
    if len(parts) >= 2 and all(re.fullmatch(r"[a-z0-9-]+", p) for p in parts):
        return parts[-2] if len(parts[-2]) > 3 or len(parts) == 2 else parts[-3]
    return n


def explain(status: str) -> str:
    return _EXPLAIN.get(status, _EXPLAIN["error"])


def _match_server(text: str, servers: list[dict[str, Any]]) -> dict[str, Any] | None:
    low = text.lower()
    best: tuple[int, dict[str, Any]] | None = None
    for s in servers:
        for cand in {s.get("host") or "", s.get("name") or "", s.get("displayname") or ""}:
            c = cand.strip().lower()
            if len(c) < 3:
                continue
            if re.search(rf"(?<![\w.-]){re.escape(c)}(?![\w-])", low) and (best is None or len(c) > best[0]):
                best = (len(c), s)
    return best[1] if best else None


# ------------------------------------------------------------------ evaluation (pure)
def evaluate(
    servers: list[dict[str, Any]],
    status: list[dict[str, Any]] | None,
    warnings: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Combine ``get_config`` servers, ``status`` and ``warnings`` into one state per server:
    ``{server, displayname, status, label, explanation, message, source, observed_at, active, enabled,
    events: [(status, message, ts)]}``."""
    now = now or datetime.now(UTC)
    by_display = {}
    for s in status or []:
        by_display[s["servername"].lower()] = s
    events: dict[str, list[tuple[str, str, datetime]]] = {}
    for w in warnings:
        text = w.get("text") or ""
        if not _SERVER_WORDS.search(text):
            continue
        st = classify_message(text)
        if st is None:
            continue
        srv = _match_server(text, servers)
        if srv is None:
            continue
        ts = datetime.fromtimestamp(w["time"], UTC) if w.get("time") else now
        events.setdefault(srv["name"], []).append((st, text, ts))
    out = []
    for s in servers:
        live = by_display.get((s.get("displayname") or "").lower()) or by_display.get(s["name"].lower())
        own = sorted(events.get(s["name"], []), key=lambda e: e[2])
        state, message, source, at = "unknown", "", "", None
        live_status = classify_message(live["error"]) if live and live.get("error") else None
        if not s.get("enable"):
            state, source = "disabled", "config"
        elif live_status:
            state, message, source, at = live_status, live["error"], "status", now
        elif own and own[-1][2] >= now - WARNING_WINDOW:
            state, message, source, at = own[-1][0], own[-1][1], "warnings", own[-1][2]
        elif live is not None and not live.get("active", True):
            state, source = "sab_blocked", "status"
        elif live is not None:
            state, source = "ok", "status"
        evs = list(own)
        if live_status:
            evs.append((live_status, live["error"], now))
        out.append(
            {
                "server": s["name"],
                "displayname": s.get("displayname") or s["name"],
                "short": short_name(s.get("displayname") or s["name"]),
                "status": state,
                "label": LABELS.get(state, LABELS["error"]),
                "explanation": explain(state),
                "message": redact(message)[:500] or None,
                "source": source or None,
                "message_at": at,
                "active": None if live is None else bool(live.get("active", True)),
                "enabled": bool(s.get("enable")),
                "events": evs,
            }
        )
    return out


# ------------------------------------------------------------------ persistence
def _record_event(server: str, status: str, message: str, ts: datetime) -> None:
    db = get_db()
    last = db.fetchone(
        "SELECT id, status, first_at, last_at FROM usenet_sab_server_events WHERE server=%s "
        "ORDER BY last_at DESC LIMIT 1",
        (server,),
    )
    if last and last["status"] == status and last["first_at"] - EVENT_GAP <= ts <= last["last_at"] + EVENT_GAP:
        db.execute(
            "UPDATE usenet_sab_server_events SET first_at=LEAST(first_at,%s), last_at=GREATEST(last_at,%s), "
            "message=%s WHERE id=%s",
            (ts, ts, message[:500], last["id"]),
        )
        return
    # an older warning that falls inside an existing event of the same status
    hit = db.scalar(
        "SELECT id FROM usenet_sab_server_events WHERE server=%s AND status=%s AND first_at - %s <= %s "
        "AND last_at + %s >= %s LIMIT 1",
        (server, status, EVENT_GAP, ts, EVENT_GAP, ts),
    )
    if hit:
        db.execute(
            "UPDATE usenet_sab_server_events SET first_at=LEAST(first_at,%s), last_at=GREATEST(last_at,%s) "
            "WHERE id=%s",
            (ts, ts, hit),
        )
        return
    db.execute(
        "INSERT INTO usenet_sab_server_events(server,status,message,first_at,last_at) VALUES (%s,%s,%s,%s,%s)",
        (server, status, message[:500], ts, ts),
    )


def persist(states: list[dict[str, Any]]) -> None:
    db = get_db()
    names = [s["server"] for s in states]
    for s in states:
        prev = db.fetchone("SELECT status, since FROM usenet_sab_servers WHERE server=%s", (s["server"],))
        since = prev["since"] if prev and prev["status"] == s["status"] and prev["since"] else datetime.now(UTC)
        db.execute(
            "INSERT INTO usenet_sab_servers(server,displayname,status,message,source,message_at,active,enabled,"
            "since,observed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (server) DO UPDATE SET "
            "displayname=EXCLUDED.displayname, status=EXCLUDED.status, message=EXCLUDED.message, "
            "source=EXCLUDED.source, message_at=EXCLUDED.message_at, active=EXCLUDED.active, "
            "enabled=EXCLUDED.enabled, since=EXCLUDED.since, observed_at=now()",
            (
                s["server"],
                s["displayname"],
                s["status"],
                s["message"],
                s["source"],
                s["message_at"],
                s["active"],
                s["enabled"],
                since,
            ),
        )
        for st, msg, ts in s["events"]:
            if st in FAILING or st == "rate_limited":
                _record_event(s["server"], st, redact(msg), ts)
        if prev is None or prev["status"] != s["status"]:
            outcomes.observe(
                "sabnzbd", "sab.server_status", 0.0 if s["status"] in FAILING else 1.0,
                {"server": s["server"], "status": s["status"]},
            )
    if names:
        db.execute("DELETE FROM usenet_sab_servers WHERE NOT (server = ANY(%s))", (names,))


def _provider_labels() -> dict[str, str]:
    return {
        r["sab_server"]: r["label"]
        for r in get_db().fetchall("SELECT sab_server, label FROM usenet_providers WHERE sab_server IS NOT NULL")
    }


def display_name(state: dict[str, Any], labels: dict[str, str] | None = None) -> str:
    labels = _provider_labels() if labels is None else labels
    label = labels.get(state["server"])
    if label and label.lower() != state["displayname"].lower():
        return label
    return state.get("short") or short_name(state["displayname"])


_REC_TITLE = {
    "account_expired": "Account verlopen bij {name}",
    "auth_failed": "Inloggen geweigerd bij {name}",
    "access_denied": "Toegang geweigerd bij {name}",
    "block_exhausted": "Blok op bij {name}",
    "unreachable": "{name} onbereikbaar voor SABnzbd",
    "tls_error": "TLS-fout bij {name}",
    "sab_blocked": "{name} tijdelijk geblokkeerd door SABnzbd",
}
_REC_ACTION = {
    "account_expired": "vernieuw het abonnement of schakel de server uit in SABnzbd",
    "auth_failed": "controleer gebruikersnaam/wachtwoord in SABnzbd of schakel de server uit",
    "access_denied": "controleer het account en de inloggegevens in SABnzbd of schakel de server uit",
    "block_exhausted": "koop een nieuw blok of schakel de server uit in SABnzbd",
    "unreachable": "controleer host, poort en SSL in SABnzbd",
    "tls_error": "controleer poort en SSL-instelling in SABnzbd",
    "sab_blocked": "bekijk de waarschuwingen in SABnzbd",
}


def recommendation_text(state: dict[str, Any], labels: dict[str, str] | None = None) -> tuple[str, str]:
    name = display_name(state, labels)
    title = _REC_TITLE.get(state["status"], "Serverfout bij {name}").format(name=name)
    action = _REC_ACTION.get(state["status"], "bekijk de waarschuwingen in SABnzbd")
    title = f"{title} — {action}"
    msg = f" SABnzbd meldt: \"{state['message']}\"." if state.get("message") else ""
    explanation = (
        f"{explain(state['status'])}{msg} Zolang de server aanstaat probeert SABnzbd hem bij elke download; "
        "ontbrekende artikelen worden dan niet aangevuld. Bron: SABnzbd."
    )
    return title, explanation


def _reminder_days() -> int:
    try:
        return int(sr.get("usenet.server_error_reminder_days"))
    except Exception:
        return 3


def reminder_text(state: dict[str, Any], days_failing: float, labels: dict[str, str] | None = None) -> tuple[str, str]:
    name = display_name(state, labels)
    if state["status"] == "account_expired":
        title = f"Herinnering: account bij {name} is verlopen — verleng het abonnement of schakel de server uit"
    else:
        title = (
            f"Herinnering: {name} geeft al {int(days_failing)} dag(en) fouten "
            f"({LABELS.get(state['status'], state['status']).lower()})"
        )
    explanation = (
        f"{explain(state['status'])} SABnzbd meldt dit sinds {int(days_failing)} dag(en)"
        + (f": \"{state['message']}\"" if state.get("message") else "")
        + ". Betaal je nog voor dit account, controleer dan of je het wilt verlengen of opzeggen (MSO zegt "
        "nooit zelf iets op). Uitschakelen in SABnzbd kan met één goedkeuring via 'Voorstel: kapotte servers "
        "uitschakelen' op de pagina Usenet-providers."
    )
    return title, explanation


def _sync_recommendations(states: list[dict[str, Any]]) -> None:
    db = get_db()
    # servers that no longer exist in SABnzbd: their advisories/reminders are resolved
    db.execute(
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE status='open' AND "
        "(dedupe_key LIKE 'usenet.sab\\_server:%%' OR dedupe_key LIKE 'usenet.sab\\_server\\_reminder:%%') "
        "AND NOT (split_part(dedupe_key, ':', 2) = ANY(%s))",
        ([s["server"] for s in states],),
    )
    labels = _provider_labels()
    persisted = current()
    now = datetime.now(UTC)
    remind_after = timedelta(days=_reminder_days())
    for s in states:
        key = f"usenet.sab_server:{s['server']}"
        rkey = f"usenet.sab_server_reminder:{s['server']}"
        since = (persisted.get(s["server"]) or {}).get("since") or now
        failing_for = now - since
        if s["status"] in FAILING and s["enabled"] and (
            s["status"] == "account_expired" or failing_for >= remind_after
        ):
            rtitle, rexpl = reminder_text(s, failing_for.total_seconds() / 86400, labels)
            outcomes.recommend(
                "usenet",
                rtitle,
                rexpl,
                risk="advies",
                subject=f"sabnzbd:{s['server']}",
                impact="Herinnering: account/abonnement nakijken",
                dedupe_key=rkey,
                evidence={
                    "server": s["server"],
                    "status": s["status"],
                    "since": since.isoformat(),
                    "reminder_after_days": remind_after.days,
                    "source": "sabnzbd",
                },
            )
            db.execute(
                "UPDATE recommendations SET status='open', updated_at=now() WHERE dedupe_key=%s "
                "AND status='resolved'",
                (rkey,),
            )
        else:
            db.execute(
                "UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
                "AND status='open'",
                (rkey,),
            )
        if s["status"] in FAILING and s["enabled"]:
            title, explanation = recommendation_text(s, labels)
            outcomes.recommend(
                "usenet",
                title,
                explanation,
                risk="advies",
                subject=f"sabnzbd:{s['server']}",
                impact="Mislukte of onvolledige downloads zolang de server faalt",
                dedupe_key=key,
                evidence={
                    "server": s["server"],
                    "status": s["status"],
                    "message": s["message"],
                    "source": "sabnzbd",
                },
            )
            db.execute(
                "UPDATE recommendations SET status='open', updated_at=now() WHERE dedupe_key=%s "
                "AND status='resolved'",
                (key,),
            )
        else:
            db.execute(
                "UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
                "AND status='open'",
                (key,),
            )


def refresh(client: SabClient | None = None) -> dict[str, Any]:
    """Read SABnzbd's server state + warnings, persist, sync recommendations."""
    own = client is None
    c = client or SabClient.from_config()
    try:
        servers = c.servers()
        try:
            live = c.server_status()
        except SabError as exc:
            if exc.code == "auth":
                raise
            live = None
        try:
            warns = c.warnings()
        except SabError as exc:
            if exc.code == "auth":
                raise
            warns = []
    finally:
        if own:
            c.close()
    states = evaluate(servers, live, warns)
    persist(states)
    _sync_recommendations(states)
    return {
        "servers": len(states),
        "failing": sorted(s["server"] for s in states if s["status"] in FAILING),
        "status_available": live is not None,
    }


# ------------------------------------------------------------------ reads
def current() -> dict[str, dict[str, Any]]:
    """Persisted state per SABnzbd server name."""
    out = {}
    for r in get_db().fetchall("SELECT * FROM usenet_sab_servers"):
        d = dict(r)
        d["label"] = LABELS.get(d["status"], LABELS["error"])
        d["explanation"] = explain(d["status"])
        d["short"] = short_name(d["displayname"] or d["server"])
        d["source_label"] = "SABnzbd"
        out[d["server"]] = d
    return out


def public(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    return {
        "status": state["status"],
        "label": state["label"],
        "explanation": state["explanation"],
        "message": state.get("message"),
        "message_at": state.get("message_at"),
        "since": state.get("since"),
        "observed_at": state.get("observed_at"),
        "active": state.get("active"),
        "source": "sabnzbd",
    }


def problems_at(
    ts: datetime | None, events: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Login/account problems SABnzbd reported around ``ts`` (one entry per server, worst first)."""
    if ts is None:
        return []
    if events is None:
        events = load_events()
    hits: dict[str, dict[str, Any]] = {}
    for e in events:
        if e["status"] not in LOGIN_PROBLEMS:
            continue
        if e["first_at"] - EVENT_LEAD <= ts <= e["last_at"] + EVENT_TAIL:
            hits.setdefault(e["server"], e)
    return list(hits.values())


def load_events(days: int = 120) -> list[dict[str, Any]]:
    return get_db().fetchall(
        "SELECT e.server, e.status, e.message, e.first_at, e.last_at, s.displayname FROM usenet_sab_server_events e "
        "LEFT JOIN usenet_sab_servers s ON s.server=e.server WHERE e.last_at > now() - %s::interval "
        "ORDER BY e.last_at DESC",
        (f"{days} days",),
    )


# ------------------------------------------------------------------ job
@jobs.handler("usenet.sab_server_status")
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    res = refresh()
    from mso.features.usenet import autoheal  # lazy: autoheal imports this module

    res["autopilot"] = autoheal.run()
    return res


def _sab_configured() -> bool:
    return cb.is_configured("sabnzbd")


jobs.every("usenet.sab_server_status", 300, enabled=_sab_configured)
