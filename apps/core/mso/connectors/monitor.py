"""Background connector monitoring: what SABnzbd, qBittorrent and Prowlarr answer to MSO's own background jobs keeps
``connectors.status`` honest (not only the manual "Testen" button).

How it works (no feature module needs to know about it):

- ``jobs.run_one`` opens a *scope* per job (``begin``/``end``). Every HTTP client made by ``base.http_client`` for a
  monitored connector reports each response and each transport error into that scope.
- At the end of the job one verdict per connector is stored (``record``):
  an uncaught connector error (``SabError`` code ``auth``/``timeout``/``unreachable``, ``QbitAuthError``,
  ``ProwlarrError`` code ``auth``/``timeout``/``unavailable``, raw ``httpx`` transport errors) → ``auth_failed`` or
  ``offline`` (``failure_kind`` auth | offline | timeout); otherwise at least one successful response (2xx, not a
  login handshake) → healthy; only failures (refused, time-out, HTTP 401/403, a redirect instead of an API answer)
  → down. A read time-out of a slow aggregate search (Prowlarr waits for every indexer) is not an outage.
- ``status_since`` changes only when the status changes; ``last_ok_at`` is the last success.
- ``last_error`` is always redacted: no API key, password or user name ever reaches it.
- A "Wacht op jou" item (open recommendation, area ``system``) appears only when the key has been refused for more
  than 15 minutes or the connector has been unreachable for more than 24 hours, and resolves itself on recovery.
- While a connector is down, ``jobs`` runs its schedules at most every 15 minutes; on recovery one full round runs
  immediately (``end`` returns the recovered connector ids).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from mso.db import Database, get_db, jsonb
from mso.security import redact

LOGGER = logging.getLogger(__name__)

MONITORED = ("sabnzbd", "qbittorrent", "prowlarr")
DOWN = ("offline", "auth_failed")
THROTTLE_S = 15 * 60  # schedules of a connector that is down run at most this often
AUTH_ALERT_S = 15 * 60  # "Wacht op jou" after the key has been refused this long
OFFLINE_ALERT_S = 24 * 3600  # … or after the connector has been unreachable this long
RECOVERED_AUDIT_S = 15 * 60  # write "weer bereikbaar" to the audit log only after an outage at least this long
ALERT_KEY = "connector.down:"
ACTOR = "systeem"

FAILURE_TEXT = {
    "auth": "{label} weigert de API-sleutel of de inloggegevens",
    "offline": "Verbinding met {label} geweigerd of host onbereikbaar",
    "timeout": "Time-out: {label} reageert niet binnen de limiet",
}

# login handshakes prove nothing about the key that is used afterwards (qBittorrent answers 200 "Fails.")
_LOGIN_PATHS = ("/auth/login", "/login")
# requests that may legitimately take long: Prowlarr's aggregate search waits for every indexer, so a *read* time-out
# there says something about slow indexers, not about Prowlarr (a connect time-out still counts as unreachable)
SLOW_PATHS = ("/api/v1/search",)


@dataclass
class _Evidence:
    ok: int = 0  # successful responses (not a login handshake)
    failure: tuple[str, str] | None = None  # (kind, message) of the latest failure


@dataclass
class _Scope:
    kind: str
    evidence: dict[str, _Evidence] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ev(self, cid: str) -> _Evidence:
        return self.evidence.setdefault(cid, _Evidence())


_SCOPE: ContextVar[_Scope | None] = ContextVar("mso_connector_scope", default=None)
_LOOKUPS: ContextVar[set[str] | None] = ContextVar("mso_connector_lookups", default=None)


# ------------------------------------------------------------------ helpers
def label(cid: str) -> str:
    from mso.connectors import base

    spec = base.SPECS.get(cid)
    return spec.label if spec else cid


def human_duration(seconds: float | int | None) -> str:
    """Dutch, compact: '45 s', '35 min', '4 u 10 min', '2 d 4 u'."""
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} u {m} min" if m else f"{h} u"
    d, h = divmod(h, 24)
    return f"{d} d {h} u" if h else f"{d} d"


def failure_message(cid: str, kind: str) -> str:
    return FAILURE_TEXT.get(kind, FAILURE_TEXT["offline"]).format(label=label(cid))


# ------------------------------------------------------------------ dependency capture (schedule enabled hooks)
def note_lookup(cid: str) -> None:
    """Called by ``base.load_config``: inside ``capture_lookups`` it records which connector a hook depends on."""
    seen = _LOOKUPS.get()
    if seen is not None:
        seen.add(cid)


@contextlib.contextmanager
def capture_lookups() -> Iterator[set[str]]:
    seen: set[str] = set()
    token = _LOOKUPS.set(seen)
    try:
        yield seen
    finally:
        _LOOKUPS.reset(token)


# ------------------------------------------------------------------ evidence from HTTP clients
def note_response(cid: str, request: httpx.Request, response: httpx.Response) -> None:
    scope = _SCOPE.get()
    if scope is None:
        return
    path = request.url.path or ""
    with scope.lock:
        ev = scope.ev(cid)
        if response.status_code in (401, 403):
            ev.failure = ("auth", failure_message(cid, "auth") + f" (HTTP {response.status_code})")
        elif 200 <= response.status_code < 300:
            if not path.endswith(_LOGIN_PATHS):
                ev.ok += 1
        elif 300 <= response.status_code < 400:
            # the clients never follow redirects: an API call answered with a redirect (SSO / reverse proxy login
            # page, http → https) means MSO is not talking to the API at all
            ev.failure = ("offline", redirect_message(cid, response.status_code))


def redirect_message(cid: str, status: int) -> str:
    return (f"{label(cid)} stuurt API-verzoeken door (HTTP {status}) in plaats van te antwoorden; staat er een "
            "inlogpagina of proxy voor, of klopt http/https in het adres niet?")


def slow_timeout(exc: BaseException) -> bool:
    """A read/write time-out of a request that may legitimately take long (see ``SLOW_PATHS``)."""
    if not isinstance(exc, httpx.TimeoutException) or isinstance(exc, httpx.ConnectTimeout | httpx.PoolTimeout):
        return False
    try:
        path = exc.request.url.path or ""
    except (AttributeError, RuntimeError):
        return False
    return path.rstrip("/").endswith(SLOW_PATHS)


def note_transport_error(cid: str, exc: BaseException) -> None:
    """Called by ``base.MonitoredClient`` for every transport error of a monitored connector."""
    kind = transport_kind(exc)
    if kind is not None and not slow_timeout(exc):
        note_error(cid, kind)


def note_error(cid: str, kind: str, message: str | None = None) -> None:
    scope = _SCOPE.get()
    if scope is None:
        return
    with scope.lock:
        scope.ev(cid).failure = (kind, message or failure_message(cid, kind))


def transport_kind(exc: BaseException) -> str | None:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "offline"
    return None


# ------------------------------------------------------------------ job scope
def begin(kind: str) -> Token[_Scope | None]:
    return _SCOPE.set(_Scope(kind))


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def _cid_for_request(exc: BaseException) -> str | None:
    from mso.connectors import base

    try:
        url = str(exc.request.url)  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return None
    return base.connector_for_url(url, prefix=True)


def classify_exception(exc: BaseException) -> tuple[str, str, str] | None:
    """(connector id, failure kind, redacted Dutch message) when ``exc`` means a monitored connector is down
    (key refused, unreachable, time-out); None for anything else (a bug, bad input, a 5xx answer …)."""
    from mso.connectors import base

    if any(slow_timeout(e) for e in _chain(exc)):
        return None  # a slow aggregate search timed out: the indexers are slow, the connector itself answers
    for e in _chain(exc):
        if isinstance(e, base.ConnectorNotConfigured):
            return None
        tk = transport_kind(e)
        if tk is not None:
            cid = _cid_for_request(e)
            if cid in MONITORED:
                return cid, tk, failure_message(cid, tk)
            continue
        module = type(e).__module__ or ""
        if not module.startswith("mso.connectors."):
            continue
        cid = module.rsplit(".", 1)[1]
        if cid not in MONITORED:
            return None
        code = str(getattr(e, "code", "") or "")
        status = getattr(e, "status", None)
        name = type(e).__name__
        if code == "auth" or "Auth" in name or status in (401, 403):
            kind = "auth"
        elif code == "timeout":
            kind = "timeout"
        elif code in ("unreachable", "unavailable"):
            kind = "offline"
        else:
            # the connector answered (HTTP error, invalid data, refused input): not an outage — unless the root cause
            # in the chain is a transport error
            inner = next((transport_kind(x) for x in _chain(e) if transport_kind(x)), None)
            if inner is None:
                return None
            kind = inner
        return cid, kind, redact(str(e))[:500] or failure_message(cid, kind)
    return None


def end(token: Token[_Scope | None], exc: BaseException | None = None, *, db: Database | None = None) -> list[str]:
    """Close the job scope and store one verdict per connector. Returns connector ids that just recovered."""
    scope = _SCOPE.get()
    _SCOPE.reset(token)
    if scope is None:
        return []
    verdicts: dict[str, tuple[bool, str | None, str | None]] = {}
    if exc is not None:
        hit = classify_exception(exc)
        if hit is not None:
            verdicts[hit[0]] = (False, hit[1], hit[2])
    for cid, ev in scope.evidence.items():
        if cid in verdicts or cid not in MONITORED:
            continue
        if ev.ok:
            verdicts[cid] = (True, None, None)  # it worked in this job (later partial errors are the job's business)
        elif ev.failure is not None:
            verdicts[cid] = (False, ev.failure[0], ev.failure[1])
    recovered = []
    for cid, (ok, kind, message) in verdicts.items():
        try:
            if record(cid, ok, kind, message, db=db, job_kind=scope.kind) == "recovered":
                recovered.append(cid)
        except Exception as e:  # monitoring must never break the job queue
            LOGGER.warning("koppelingsstatus %s niet bijgewerkt: %s", cid, redact(str(e)))
    return recovered


def job_failure_connector(exc: BaseException) -> str | None:
    hit = classify_exception(exc)
    return hit[0] if hit else None


# ------------------------------------------------------------------ persistence
def record(cid: str, ok: bool, kind: str | None = None, message: str | None = None, *, db: Database | None = None,
           job_kind: str | None = None) -> str | None:
    """Store one background verdict. Returns 'recovered' | 'down' (a transition) or None."""
    db = db or get_db()
    with db.tx() as conn:
        row = conn.execute(
            "SELECT status, enabled, url, status_since FROM connectors WHERE id=%s FOR UPDATE", (cid,)).fetchone()
        if row is None or not row["enabled"] or not row["url"]:
            return None
        prev = row["status"]
        if ok:
            new = "degraded" if prev == "degraded" else "healthy"  # 'degraded' is a finding of the manual test
            after = conn.execute(
                "UPDATE connectors SET status=%s, failure_kind=NULL, last_ok_at=now(), last_checked_at=now(), "
                "last_error=CASE WHEN %s='healthy' THEN NULL ELSE last_error END, "
                "status_since=CASE WHEN status IS DISTINCT FROM %s OR status_since IS NULL THEN now() "
                "ELSE status_since END WHERE id=%s RETURNING status_since",
                (new, new, new, cid)).fetchone()
        else:
            new = "auth_failed" if kind == "auth" else "offline"
            msg = redact(message or failure_message(cid, kind or "offline"))[:500]
            after = conn.execute(
                "UPDATE connectors SET status=%s, failure_kind=%s, last_error=%s, last_checked_at=now(), "
                "status_since=CASE WHEN status IS DISTINCT FROM %s OR status_since IS NULL THEN now() "
                "ELSE status_since END WHERE id=%s RETURNING status_since",
                (new, kind or "offline", msg, new, cid)).fetchone()
    since = after["status_since"] if after else None
    transition = None
    if ok and prev in DOWN:
        transition = "recovered"
        down_for = (datetime.now(UTC) - row["status_since"]).total_seconds() if row["status_since"] else None
        LOGGER.info("%s weer bereikbaar (na %s)", label(cid), human_duration(down_for))
        if down_for is not None and down_for >= RECOVERED_AUDIT_S:
            from mso.audit import audit

            audit(ACTOR, "connector.recovered", f"{label(cid)} werkt weer (na {human_duration(down_for)})",
                  target=cid, detail={"previous": prev, "down_s": int(down_for), "job": job_kind})
    elif not ok and prev not in DOWN:
        transition = "down"
        LOGGER.warning("%s: %s", label(cid), redact(message or failure_message(cid, kind or "offline")))
    if not ok or transition == "recovered":
        sync_alert(cid, db=db, status=new, since=since)
    return transition


def resolve_stale_alerts(down: set[str] | list[str], db: Database | None = None) -> None:
    """Resolve open connector items whose connector is no longer down (disabled, address cleared, or fixed
    outside a job). ``down`` must be the complete current result of ``down_connectors``."""
    db = db or get_db()
    db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key LIKE %s "
               "AND status='open' AND NOT (dedupe_key = ANY(%s))",
               (ALERT_KEY + "%", [ALERT_KEY + c for c in down if c in MONITORED]))


def down_connectors(db: Database | None = None) -> dict[str, dict[str, Any]]:
    """Enabled, configured connectors that are currently down (offline / key refused)."""
    db = db or get_db()
    rows = db.fetchall(
        "SELECT id, status, status_since, failure_kind FROM connectors WHERE enabled AND url IS NOT NULL "
        "AND status = ANY(%s)", (list(DOWN),))
    return {r["id"]: dict(r) for r in rows}


def sync_alert(cid: str, *, db: Database | None = None, status: str | None = None,
               since: datetime | None = None) -> None:
    """Open / refresh / resolve the "Wacht op jou" item for one connector (SABnzbd, qBittorrent, Prowlarr only:
    MediaGuard has its own status probe and is not retried every 15 minutes)."""
    db = db or get_db()
    key = ALERT_KEY + cid
    if cid not in MONITORED:
        status, since = "not_monitored", None
    elif status is None:
        row = db.fetchone("SELECT status, status_since, enabled, url FROM connectors WHERE id=%s", (cid,))
        if row is None:
            return
        status = row["status"] if row["enabled"] and row["url"] else "not_configured"
        since = row["status_since"]
    if status not in DOWN or since is None:
        db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
                   "AND status='open'", (key,))
        return
    age = (datetime.now(UTC) - since).total_seconds()
    if age < (AUTH_ALERT_S if status == "auth_failed" else OFFLINE_ALERT_S):
        return
    info = db.fetchone("SELECT last_error FROM connectors WHERE id=%s", (cid,)) or {}
    name, dur = label(cid), human_duration(age)
    error = redact(str(info.get("last_error") or ""))[:300]
    if status == "auth_failed":
        title = f"{name} weigert de API-sleutel of inloggegevens"
        explanation = (
            f"Al {dur} weigert {name} wat MSO meestuurt" + (f" (“{error}”)" if error else "") + ". "
            "Zolang dat zo is kan MSO niets lezen of bijsturen. Vervang de sleutel of de inloggegevens bij "
            "Instellingen → Verbindingen en klik op Testen. MSO probeert het elke 15 minuten opnieuw en "
            "hervat vanzelf zodra het weer werkt.")
    else:
        title = f"{name} is al {dur} onbereikbaar"
        explanation = (
            f"MSO kan {name} sinds {dur} niet bereiken" + (f" (“{error}”)" if error else "") + ". "
            f"Controleer of {name} draait en of het adres bij Instellingen → Verbindingen nog klopt. MSO "
            f"probeert het elke 15 minuten opnieuw en haalt alles meteen bij zodra {name} terug is.")
    evidence = {"connector": cid, "status": status, "since": since.isoformat(), "down_s": int(age),
                "last_error": error}
    with db.tx() as conn:
        prev = conn.execute("SELECT status, evidence FROM recommendations WHERE dedupe_key=%s FOR UPDATE",
                            (key,)).fetchone()
        same_episode = prev is not None and (prev["evidence"] or {}).get("since") == evidence["since"]
        # the owner's decision (weggeklikt / geaccepteerd) holds for the whole outage; a new outage asks again
        keep = prev is not None and same_episode and prev["status"] in ("dismissed", "accepted")
        new_status = prev["status"] if keep else "open"
        conn.execute(
            "INSERT INTO recommendations(area,subject,title,explanation,impact,risk,confidence,dedupe_key,evidence,"
            "status) VALUES ('system',%s,%s,%s,%s,'toestemming_nodig',1.0,%s,%s,%s) "
            "ON CONFLICT (dedupe_key) DO UPDATE SET title=EXCLUDED.title, explanation=EXCLUDED.explanation, "
            "impact=EXCLUDED.impact, evidence=EXCLUDED.evidence, status=EXCLUDED.status, updated_at=now()",
            (cid, title, explanation,
             "Zonder deze koppeling doet MSO niets voor dit deel; de doelen lopen dan stilletjes achter.",
             key, jsonb(evidence), new_status))
    newly_open = new_status == "open" and (prev is None or prev["status"] != "open")
    if newly_open:
        from mso.audit import audit

        audit(ACTOR, "connector.down", title, target=cid,
              detail={"status": status, "down_s": int(age), "last_error": error})


__all__ = [
    "DOWN", "MONITORED", "SLOW_PATHS", "THROTTLE_S", "begin", "capture_lookups", "classify_exception",
    "down_connectors", "end", "human_duration", "job_failure_connector", "note_error", "note_lookup", "note_response",
    "note_transport_error", "record", "resolve_stale_alerts", "slow_timeout", "sync_alert",
]
