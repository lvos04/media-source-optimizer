"""Retry failed SABnzbd downloads that failed because of server problems (1.5.0).

A failed job is *server related* when SABnzbd's message names a login/account cause (``auth_failure``,
``account_expired``, ``quota_exceeded``), or when an unspecific failure (``client_failure``, ``incomplete``,
``unpack_failed``, ``unknown``) happened while SABnzbd reported login/account problems for a server
(:func:`serverstatus.problems_at`, or the ``likely_cause`` recorded at ingest). Another release would fail the same
way; the same job will usually succeed once the failing servers are off.

Retrying is only offered when (a) no enabled SABnzbd server still has a login/account problem and (b) at least one
enabled server works. Then one change set on the ``sabnzbd_retry`` applier retries the jobs
(``mode=retry&value=<nzo_id>``), op ``one_way``, each verified by finding the job back in SABnzbd's queue, audited.
In Autopilot it is applied automatically; otherwise it is one bundle proposal "N mislukte downloads opnieuw
proberen". Bounded: once per job (``usenet_retries``), ``usenet.retry_per_hour``/``usenet.retry_per_day``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from mso import changes
from mso import settings_registry as sr
from mso.audit import audit
from mso.connectors.sabnzbd import RETRY_AFTER, RETRY_BEFORE, RETRY_TARGET, SabClient, SabError
from mso.db import get_db
from mso.domain.outcomes import FAILURE_REASONS
from mso.features.usenet import autoapply, serverstatus
from mso.security import redact

LOGGER = logging.getLogger(__name__)
ACTOR = "autopilot:usenet"
PREFIX = "retry/"
SERVER_REASONS = {"auth_failure", "account_expired", "quota_exceeded"}
CONTEXT_REASONS = {"client_failure", "incomplete", "unpack_failed", "unknown"}
HEALTHY = {"ok", "rate_limited"}
BUNDLE_KEY = "usenet.retry_bundle"
MAX_BUNDLE = 200


def op_path(nzo_id: str) -> str:
    return f"{PREFIX}{nzo_id}"


def parse_path(path: str) -> str:
    if not str(path).startswith(PREFIX) or len(path) <= len(PREFIX) or len(path) > 220:
        raise ValueError(f"ongeldig pad '{path}' (verwacht retry/<nzo_id>)")
    return path[len(PREFIX) :]


# ------------------------------------------------------------------ eligibility
def server_related(o: dict[str, Any], events: list[dict[str, Any]] | None = None) -> bool:
    reason = o.get("failure_reason") or ""
    if reason in SERVER_REASONS:
        return True
    if reason not in CONTEXT_REASONS:
        return False
    cls = ((o.get("meta") or {}).get("classification") or {}) if isinstance(o.get("meta"), dict) else {}
    if cls.get("likely_cause"):
        return True
    return bool(serverstatus.problems_at(o.get("finished_at") or o.get("observed_at"), events))


def _row(nzo_id: str) -> dict[str, Any] | None:
    return get_db().fetchone(
        "SELECT * FROM usenet_retries WHERE nzo_id=%s OR new_nzo_id=%s ORDER BY id DESC LIMIT 1", (nzo_id, nzo_id)
    )


def status_for(o: dict[str, Any], events: list[dict[str, Any]] | None = None) -> str | None:
    """For recovery: ``candidate`` (server related, not retried yet), ``retried`` (retry proposed/running/done),
    ``failed_again`` (MSO retried it and it failed again → the release itself is the problem) or ``None``."""
    if o.get("client") != "sabnzbd" or o.get("status") != "failed":
        return None
    row = _row(str(o.get("client_ref") or ""))
    if row is not None:
        finished = o.get("finished_at")
        if row["retried_at"] and finished and finished > row["retried_at"]:
            return "failed_again"
        return "retried"
    return "candidate" if server_related(o, events) else None


def readiness(servers: list[dict[str, Any]], sab: dict[str, dict[str, Any]]) -> dict[str, Any]:
    enabled = [s for s in servers if s["enable"]]
    failing = [s for s in enabled if (sab.get(s["name"]) or {}).get("status") in serverstatus.LOGIN_PROBLEMS]
    healthy = [s for s in enabled if (sab.get(s["name"]) or {}).get("status") in HEALTHY]
    name = lambda s: serverstatus.short_name(s.get("displayname") or s["name"])  # noqa: E731
    if failing:
        return {
            "ready": False,
            "reason": f"Wacht: {len(failing)} kapotte server(s) staan nog aan in SABnzbd "
            f"({', '.join(name(s) for s in failing)}). Eerst uitschakelen, dan opnieuw proberen.",
            "healthy": [name(s) for s in healthy],
        }
    if not healthy:
        return {"ready": False, "reason": "Geen werkende server over in SABnzbd; opnieuw proberen heeft nu geen zin.",
                "healthy": []}
    return {"ready": True, "reason": None, "healthy": [name(s) for s in healthy]}


def used(interval: str) -> int:
    return int(
        get_db().scalar(
            "SELECT count(*) FROM usenet_retries WHERE retried_at > now() - %s::interval", (interval,)
        )
        or 0
    )


def quota() -> dict[str, int]:
    ph, pd = int(sr.get("usenet.retry_per_hour")), int(sr.get("usenet.retry_per_day"))
    h, d = used("1 hour"), used("1 day")
    return {"hour_used": h, "hour_max": ph, "day_used": d, "day_max": pd, "left": max(0, min(ph - h, pd - d))}


def candidates(client: SabClient) -> dict[str, Any]:
    """Failed SABnzbd jobs that may be retried now (still failed in SABnzbd's history, once per job)."""
    servers = client.servers()
    sab = serverstatus.current()
    ready = readiness(servers, sab)
    days = int(sr.get("usenet.retry_max_age_days"))
    rows = get_db().fetchall(
        "SELECT o.* FROM download_outcomes o WHERE o.client='sabnzbd' AND o.status='failed' "
        "AND COALESCE(o.finished_at, o.observed_at) > now() - make_interval(days => %s) "
        "AND NOT EXISTS (SELECT 1 FROM usenet_retries r WHERE (r.nzo_id=o.client_ref OR r.new_nzo_id=o.client_ref) "
        "AND (r.status IN ('retried','verified','failed') OR r.retried_at IS NOT NULL)) "
        "ORDER BY COALESCE(o.finished_at, o.observed_at) DESC LIMIT 1000",
        (days,),
    )
    events = serverstatus.load_events()
    related = [dict(o) for o in rows if server_related(dict(o), events)]
    live: set[str] = set()
    if related:
        hist = client.history(limit=1000, failed_only=True) or {}
        live = {str(s.get("nzo_id")) for s in hist.get("slots") or [] if str(s.get("status")) == "Failed"}
    items = [o for o in related if o["client_ref"] in live]
    return {**ready, "items": items, "gone": len(related) - len(items), "quota": quota()}


# ------------------------------------------------------------------ applier helpers (called by the connector)
def read_state(client: SabClient, nzo_id: str) -> str:
    row = _row(nzo_id)
    ids = {nzo_id} | ({row["new_nzo_id"]} if row and row.get("new_nzo_id") else set())
    q = client.queue(limit=1000) or {}
    if any(str(s.get("nzo_id")) in ids for s in q.get("slots") or []):
        return RETRY_AFTER
    hist = client.history(limit=1000) or {}
    retried = bool(row and row.get("retried_at"))
    for s in hist.get("slots") or []:
        if str(s.get("nzo_id")) not in ids:
            continue
        if str(s.get("status")) == "Failed":
            return "opnieuw mislukt" if retried else RETRY_BEFORE
        return RETRY_AFTER if retried else "niet mislukt"
    return "niet gevonden in SABnzbd"


def do_retry(client: SabClient, nzo_id: str) -> None:
    q = quota()
    if q["left"] <= 0:
        raise SabError(
            f"limiet opnieuw proberen bereikt ({q['hour_max']} per uur / {q['day_max']} per dag)", "rate_limited"
        )
    try:
        new = client.retry(nzo_id)
    except SabError as exc:
        get_db().execute(
            "UPDATE usenet_retries SET status='failed', error=%s WHERE nzo_id=%s", (redact(str(exc))[:300], nzo_id)
        )
        raise
    get_db().execute(
        "INSERT INTO usenet_retries(nzo_id,title,status,new_nzo_id,retried_at) VALUES (%s,%s,'retried',%s,now()) "
        "ON CONFLICT (nzo_id) DO UPDATE SET status='retried', new_nzo_id=EXCLUDED.new_nzo_id, retried_at=now(), "
        "error=NULL",
        (nzo_id, nzo_id, None if new == nzo_id else new),
    )
    audit(ACTOR, "sabnzbd.retry", "Mislukte download opnieuw geprobeerd in SABnzbd", target="sabnzbd",
          detail={"nzo_id": nzo_id, "new_nzo_id": None if new == nzo_id else new})


# ------------------------------------------------------------------ proposal / autopilot
def _open_bundle() -> dict[str, Any] | None:
    return get_db().fetchone(
        "SELECT * FROM change_sets WHERE dedupe_key=%s AND status IN ('preview','approved')", (BUNDLE_KEY,)
    )


def propose(client: SabClient | None = None, *, actor: str = ACTOR) -> dict[str, Any]:
    """Create/refresh the bundle change "N mislukte downloads opnieuw proberen" and apply it when Autopilot (or the
    Usenet 'automatisch veilig' toggle) allows. Returns ``{status, change?, count, reason?}``."""
    if not sr.get("usenet.retry_failed_downloads"):
        return {"status": "off", "count": 0}
    own = client is None
    c = client or SabClient.from_config()
    try:
        cand = candidates(c)
    finally:
        if own:
            c.close()
    if not cand["ready"]:
        return {"status": "waiting", "reason": cand["reason"], "count": len(cand["items"])}
    left = cand["quota"]["left"]
    items = cand["items"][: min(left, MAX_BUNDLE)]
    if not items:
        return {"status": "nothing" if left else "quota", "count": len(cand["items"]), "quota": cand["quota"]}
    ops = []
    for o in items:
        reason = FAILURE_REASONS.get(o.get("failure_reason") or "unknown", "Onbekende oorzaak")
        ops.append({"path": op_path(o["client_ref"]), "label": f"{o['title']} ({reason.lower()})",
                    "before": RETRY_BEFORE, "after": RETRY_AFTER, "one_way": True})
    existing = _open_bundle()
    if existing and sorted(o["path"] for o in existing["ops"]) != sorted(o["path"] for o in ops):
        try:
            changes.discard(existing["id"], ACTOR)
        except HTTPException:
            pass
    n = len(ops)
    more = len(cand["items"]) - n
    title = f"{n} mislukte download{'s' if n != 1 else ''} opnieuw proberen"
    explanation = (
        f"Deze downloads mislukten terwijl SABnzbd inlog- of accountproblemen bij een server meldde. Die servers "
        f"staan nu uit; werkende server(s): {', '.join(cand['healthy'])}. MSO laat SABnzbd elke download opnieuw "
        "proberen (mode=retry) en controleert daarna dat de download weer in de wachtrij staat. Opnieuw proberen "
        "kan niet worden teruggedraaid, maar verwijdert niets. Elke download wordt maar één keer opnieuw geprobeerd."
        + (f" Nog {more} download(s) volgen later (limiet per uur/dag)." if more > 0 else "")
    )
    q = cand["quota"]
    db = get_db()
    for o in items:  # once per job: a row that was retried before is never reset
        db.execute(
            "INSERT INTO usenet_retries(nzo_id,outcome_id,title,reason,status) VALUES (%s,%s,%s,%s,'proposed') "
            "ON CONFLICT (nzo_id) DO UPDATE SET outcome_id=EXCLUDED.outcome_id, title=EXCLUDED.title, "
            "reason=EXCLUDED.reason, status='proposed', error=NULL WHERE usenet_retries.retried_at IS NULL",
            (o["client_ref"], o["id"], o["title"][:500], o.get("failure_reason")),
        )
    ch = autoapply.propose_and_apply(
        RETRY_TARGET,
        title,
        ops,
        explanation=explanation,
        impact={
            "Downloads": n,
            "Werkende servers": cand["healthy"],
            "Limieten": f"max. {q['hour_max']} per uur en {q['day_max']} per dag; één keer per download",
            "Terugdraaien": "Niet mogelijk (opnieuw proberen verwijdert niets)",
            "nzo_ids": [o["client_ref"] for o in items],
        },
        dedupe_key=BUNDLE_KEY,
        area="usenet",
    )
    db.execute(
        "UPDATE usenet_retries SET change_id=%s, trigger=%s WHERE nzo_id = ANY(%s)",
        (ch["id"], "autopilot" if ch.get("auto_applied") else "proposal", [o["client_ref"] for o in items]),
    )
    return {"status": "applied" if ch.get("status") == "applied" else ch.get("status"), "change": ch, "count": n,
            "more": max(0, more), "auto_reason": ch.get("auto_reason")}


def sync() -> dict[str, int]:
    """Retried jobs → verified (finished OK) or failed (failed again). Reads download_outcomes only."""
    db = get_db()
    ok = db.execute(
        "UPDATE usenet_retries r SET status='verified', verified_at=now() FROM download_outcomes o "
        "WHERE r.status='retried' AND o.client='sabnzbd' AND o.client_ref = COALESCE(r.new_nzo_id, r.nzo_id) "
        "AND o.status='success'"
    )
    bad = db.execute(
        "UPDATE usenet_retries r SET status='failed', error='opnieuw mislukt' FROM download_outcomes o "
        "WHERE r.status='retried' AND o.client='sabnzbd' AND o.client_ref = COALESCE(r.new_nzo_id, r.nzo_id) "
        "AND o.status='failed' AND o.finished_at > r.retried_at"
    )
    return {"verified": ok, "failed": bad}


def overview(limit: int = 50) -> dict[str, Any]:
    db = get_db()
    counts = {
        r["status"]: int(r["n"])
        for r in db.fetchall("SELECT status, count(*) AS n FROM usenet_retries GROUP BY status")
    }
    bundle = _open_bundle()
    return {
        "enabled": bool(sr.get("usenet.retry_failed_downloads")),
        "counts": counts,
        "quota": quota(),
        "open_change": {"id": bundle["id"], "title": bundle["title"], "risk": bundle["risk"]} if bundle else None,
        "items": [
            {**dict(r), "reason_label": FAILURE_REASONS.get(r["reason"] or "", None)}
            for r in db.fetchall("SELECT * FROM usenet_retries ORDER BY id DESC LIMIT %s", (limit,))
        ],
        "last_run": _last_run(),
    }


def _last_run() -> dict[str, Any] | None:
    from mso import state

    return state.get_state("usenet.autopilot_last_run", None)
