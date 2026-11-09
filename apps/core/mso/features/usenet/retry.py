"""Retry failed SABnzbd downloads — only when retrying can help (1.5.0, tightened in 1.6.0).

Retrying asks SABnzbd to fetch the same job again from the same servers. It can only help when

1. *the cause is one a retry can fix* (:func:`basis`): SABnzbd's message itself names a login/account cause
   (``auth_failure``, ``account_expired``, ``quota_exceeded``), or articles were missing / par2 repair failed
   (stage-log facts, :mod:`mso.features.usenet.stagelog`; without facts: ``incomplete``). "Uitpakken mislukt" with
   complete data is a release or SABnzbd problem and is never retried; and
2. *the coverage improved after the job failed* (:mod:`mso.features.usenet.coverage`): a switched-off server is
   confirmed working again, or a new server appeared in SABnzbd. Switching a broken server off adds no articles and
   is no longer a trigger.

The 1.5.0 conditions stay: no enabled SABnzbd server still has a login/account problem and at least one enabled
server works (:func:`readiness`); once per job (``usenet_retries``); ``usenet.retry_per_hour``/``retry_per_day``;
``usenet.retry_max_age_days``; the job must still be failed in SABnzbd's history. One change set on the
``sabnzbd_retry`` applier retries the jobs (``mode=retry&value=<nzo_id>``), op ``one_way``, each verified by finding
the job back in SABnzbd's queue, audited. In Autopilot it is applied automatically; otherwise it is one bundle
proposal "N mislukte downloads opnieuw proberen".

Effectiveness brake (:func:`check_brake`): when at least :data:`BRAKE_MIN_ATTEMPTS` finished retries in
:data:`BRAKE_WINDOW_DAYS` days succeeded less than :data:`BRAKE_MIN_RATIO` of the time, retrying pauses itself
(audit ``usenet.retry_paused``, an in-app explanation) until the coverage improves again or an operator presses
"Hervatten" (audit ``usenet.retry_resumed``). Only retries chosen under these rules count: rows created before
migration 0601 (1.5.0 retried every failure near a server problem, mostly complete-data unpack failures) never pause
1.6.0 before it made a single attempt.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.connectors.sabnzbd import RETRY_AFTER, RETRY_BEFORE, RETRY_TARGET, SabClient, SabError
from mso.db import get_db
from mso.domain import outcomes
from mso.domain.outcomes import FAILURE_REASONS
from mso.features.usenet import autoapply, coverage, serverstatus, stagelog
from mso.security import redact

LOGGER = logging.getLogger(__name__)
ACTOR = "autopilot:usenet"
PREFIX = "retry/"
SERVER_REASONS = {"auth_failure", "account_expired", "quota_exceeded"}
HEALTHY = {"ok", "rate_limited"}
BUNDLE_KEY = "usenet.retry_bundle"
MAX_BUNDLE = 200
# effectiveness brake
BRAKE_KEY = "usenet.retry_brake"
BRAKE_REC_KEY = "usenet.retry_paused"
BRAKE_MIN_ATTEMPTS = 10
BRAKE_WINDOW_DAYS = 14
BRAKE_MIN_RATIO = 0.30
RULES_MIGRATION = 601  # retries chosen before this migration (1.5.0 rules) are not judged by the brake
COVERAGE_WAIT = (
    "Wacht op betere dekking: opnieuw proberen vraagt dezelfde artikelen aan dezelfde servers en helpt pas als er "
    "sinds het mislukken een server hersteld is of een nieuwe server bij is gekomen."
)


def op_path(nzo_id: str) -> str:
    return f"{PREFIX}{nzo_id}"


def parse_path(path: str) -> str:
    if not str(path).startswith(PREFIX) or len(path) <= len(PREFIX) or len(path) > 220:
        raise ValueError(f"ongeldig pad '{path}' (verwacht retry/<nzo_id>)")
    return path[len(PREFIX) :]


# ------------------------------------------------------------------ eligibility
def basis(o: dict[str, Any]) -> str | None:
    """Why retrying this failed job could help: ``server`` (SABnzbd's message names a login/account cause) or
    ``missing`` (articles were missing / repair failed). ``None``: retrying the same job cannot help — e.g. unpacking
    failed with complete data (a release or SABnzbd problem)."""
    reason = o.get("failure_reason") or ""
    if reason in SERVER_REASONS:
        return "server"
    missing = stagelog.data_missing(stagelog.facts_of(o.get("meta")))
    if missing is True or (missing is None and reason == "incomplete"):
        return "missing"
    return None


def failed_at(o: dict[str, Any]) -> datetime | None:
    return o.get("finished_at") or o.get("observed_at")


def after_gain(o: dict[str, Any], gain_at: datetime | None) -> bool:
    """The coverage improved after this job failed (otherwise a retry asks the same servers for the same articles)."""
    at = failed_at(o)
    return bool(gain_at and at and at < gain_at)


def _row(nzo_id: str) -> dict[str, Any] | None:
    return get_db().fetchone(
        "SELECT * FROM usenet_retries WHERE nzo_id=%s OR new_nzo_id=%s ORDER BY id DESC LIMIT 1", (nzo_id, nzo_id)
    )


def status_for(o: dict[str, Any], events: list[dict[str, Any]] | None = None) -> str | None:
    """For recovery: ``candidate`` (a retry can help and will happen: retryable cause, coverage improved after the
    failure, retrying not paused), ``retried`` (retry proposed/running/done), ``failed_again`` (MSO retried it and it
    failed again → the release itself is the problem) or ``None`` (recovery treats it as usual). ``events`` is kept
    for compatibility; the server timing alone no longer makes a job retryable."""
    if o.get("client") != "sabnzbd" or o.get("status") != "failed":
        return None
    row = _row(str(o.get("client_ref") or ""))
    if row is not None and (row["retried_at"] or row["status"] != "proposed"):
        finished = o.get("finished_at")
        if row["retried_at"] and finished and finished > row["retried_at"]:
            return "failed_again"
        return "retried"
    if row is not None and not brake_state().get("paused"):
        bundle = _open_bundle()
        if bundle and any(op.get("path") == op_path(row["nzo_id"]) for op in bundle["ops"] or []):
            return "retried"  # in the open retry proposal
    if basis(o) is None or not sr.get("usenet.retry_failed_downloads") or brake_state().get("paused"):
        return None
    return "candidate" if after_gain(o, coverage.last_gain_at()) else None


def recovery_text(o: dict[str, Any], status: str) -> str:
    """What recovery says about a failed download that SABnzbd retries (``status`` from :func:`status_for`:
    ``candidate`` or ``retried``), by why the retry can help (:func:`basis`)."""
    done = status == "retried"
    if basis(o) == "missing":
        return (
            "Artikelen ontbraken; sinds het mislukken is de dekking verbeterd (server hersteld of nieuwe server), dus "
            + ("MSO liet SABnzbd deze download opnieuw proberen." if done
               else "MSO laat SABnzbd deze download opnieuw proberen.")
            + " Pas als dat ook mislukt, zoekt herstel een andere release."
        )
    return (
        "Mislukt door een serverprobleem (inloggen/account) in SABnzbd; een andere release lost dit niet op. "
        + ("MSO laat SABnzbd deze download opnieuw proberen." if done
           else "MSO laat SABnzbd deze download opnieuw proberen nu de dekking verbeterd is (server hersteld of "
           "nieuwe server).")
    )


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


def candidates(client: SabClient, servers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Failed SABnzbd jobs that may be retried now (retryable cause, failed before the last coverage gain, still
    failed in SABnzbd's history, once per job). ``waiting_coverage`` counts the jobs with a retryable cause that wait
    for a coverage gain. Pass ``servers`` when the caller already read and observed them."""
    if servers is None:
        servers = client.servers()
        coverage.observe(servers)
    sab = serverstatus.current()
    ready = readiness(servers, sab)
    gain = coverage.last_gain()
    gain_at = gain["at"] if gain else None
    days = int(sr.get("usenet.retry_max_age_days"))
    rows = get_db().fetchall(
        "SELECT o.* FROM download_outcomes o WHERE o.client='sabnzbd' AND o.status='failed' "
        "AND COALESCE(o.finished_at, o.observed_at) > now() - make_interval(days => %s) "
        "AND NOT EXISTS (SELECT 1 FROM usenet_retries r WHERE (r.nzo_id=o.client_ref OR r.new_nzo_id=o.client_ref) "
        "AND (r.status IN ('retried','verified','failed') OR r.retried_at IS NOT NULL)) "
        "ORDER BY COALESCE(o.finished_at, o.observed_at) DESC LIMIT 1000",
        (days,),
    )
    retryable = [dict(o) for o in rows if basis(dict(o))]
    related = [o for o in retryable if after_gain(o, gain_at)]
    live: set[str] = set()
    if related:
        hist = client.history(limit=1000, failed_only=True) or {}
        live = {str(s.get("nzo_id")) for s in hist.get("slots") or [] if str(s.get("status")) == "Failed"}
    items = [o for o in related if o["client_ref"] in live]
    return {**ready, "items": items, "gone": len(related) - len(items), "quota": quota(), "gain": gain,
            "waiting_coverage": len(retryable) - len(related)}


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


def _discard_bundle(bundle: dict[str, Any]) -> None:
    """Discard an open bundle proposal and MSO's own 'proposed' bookkeeping rows for it (never a retried row)."""
    try:
        changes.discard(bundle["id"], ACTOR)
    except HTTPException:
        pass
    get_db().execute(
        "DELETE FROM usenet_retries WHERE change_id=%s AND status='proposed' AND retried_at IS NULL", (bundle["id"],)
    )


def propose(client: SabClient | None = None, *, actor: str = ACTOR) -> dict[str, Any]:
    """Create/refresh the bundle change "N mislukte downloads opnieuw proberen" and apply it when Autopilot (or the
    Usenet 'automatisch veilig' toggle) allows. Returns ``{status, change?, count, reason?}``."""
    if not sr.get("usenet.retry_failed_downloads"):
        return {"status": "off", "count": 0}
    own = client is None
    c = client or SabClient.from_config()
    try:
        servers = c.servers()
        coverage.observe(servers)  # a new server is a coverage gain, also while retrying is paused
        brake = check_brake()
        if brake.get("paused"):
            return {"status": "paused", "reason": brake.get("reason"), "count": 0}
        cand = candidates(c, servers)
    finally:
        if own:
            c.close()
    if not cand["ready"]:
        return {"status": "waiting", "reason": cand["reason"], "count": len(cand["items"])}
    if not cand["items"] and cand["waiting_coverage"]:
        return {"status": "waiting", "kind": "coverage", "reason": COVERAGE_WAIT, "count": cand["waiting_coverage"]}
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
        _discard_bundle(existing)
    n = len(ops)
    more = len(cand["items"]) - n
    title = f"{n} mislukte download{'s' if n != 1 else ''} opnieuw proberen"
    gain_text = coverage.describe(cand["gain"]) or "de dekking is verbeterd"
    explanation = (
        "Deze downloads mislukten doordat artikelen ontbraken of herstel mislukte, of door een serverprobleem dat "
        f"SABnzbd zelf noemde. Sinds ze mislukten is de dekking verbeterd: {gain_text}. Werkende server(s): "
        f"{', '.join(cand['healthy'])}. MSO laat SABnzbd elke download opnieuw proberen (mode=retry) en controleert "
        "daarna dat de download weer in de wachtrij staat. Opnieuw proberen kan niet worden teruggedraaid, maar "
        "verwijdert niets. Elke download wordt maar één keer opnieuw geprobeerd; downloads waarbij de data compleet "
        "was maar het uitpakken mislukte, probeert MSO niet opnieuw."
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
            "Dekking verbeterd": gain_text,
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
    bundle = _open_bundle()
    counts = {  # 'proposed' = in the open bundle proposal (a discarded proposal leaves nothing proposed)
        r["status"]: int(r["n"])
        for r in db.fetchall(
            "SELECT status, count(*) AS n FROM usenet_retries WHERE status <> 'proposed' OR change_id = %s "
            "GROUP BY status",
            (bundle["id"] if bundle else None,),
        )
    }
    gain = coverage.last_gain(db)
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
        "efficacy": efficacy(),
        "brake": public_brake(),
        "coverage": {"last_gain": gain, "description": coverage.describe(gain)},
    }


# ------------------------------------------------------------------ effectiveness brake
def _now() -> datetime:
    return datetime.now(UTC)


def _ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def rules_since() -> datetime | None:
    """When the 1.6.0 retry rules took effect (migration 0601 applied); ``None`` when unknown."""
    return get_db().scalar("SELECT applied_at FROM schema_migrations WHERE version=%s", (RULES_MIGRATION,))


def efficacy(since: datetime | None = None) -> dict[str, Any]:
    """Finished retries (verified / failed again) in the last :data:`BRAKE_WINDOW_DAYS` days (or since ``since`` when
    that is later) and their success share. Only retries chosen under the 1.6.0 rules count (:func:`rules_since`);
    ``since_kind`` says what limits the period: ``window``, ``rules`` (the upgrade) or ``resumed``."""
    start, kind = _now() - timedelta(days=BRAKE_WINDOW_DAYS), "window"
    rules = rules_since()
    if since and since > start:
        start, kind = since, "resumed"
    if rules and rules > start:
        start, kind = rules, "rules"
    row = get_db().fetchone(
        "SELECT count(*) FILTER (WHERE status IN ('verified','failed')) AS done, "
        "count(*) FILTER (WHERE status='verified') AS ok, count(*) FILTER (WHERE status='retried') AS running "
        "FROM usenet_retries WHERE retried_at IS NOT NULL AND retried_at > %s AND created_at >= %s",
        (start, rules or datetime(1970, 1, 1, tzinfo=UTC)),
    ) or {}
    done, ok = int(row.get("done") or 0), int(row.get("ok") or 0)
    return {
        "attempts": done,
        "verified": ok,
        "failed": done - ok,
        "running": int(row.get("running") or 0),
        "ratio": round(ok / done, 4) if done else None,
        "window_days": BRAKE_WINDOW_DAYS,
        "since": start.isoformat(timespec="seconds"),
        "since_kind": kind,
        "min_attempts": BRAKE_MIN_ATTEMPTS,
        "min_ratio": BRAKE_MIN_RATIO,
    }


def brake_state() -> dict[str, Any]:
    st = state.get_state(BRAKE_KEY, None)
    return dict(st) if isinstance(st, dict) else {"paused": False}


def public_brake() -> dict[str, Any]:
    st = brake_state()
    keys = ("paused", "paused_at", "reason", "attempts", "verified", "ratio", "resumed_at", "resume_trigger")
    return {k: st.get(k) for k in keys} | {"paused": bool(st.get("paused"))}


def _pct(r: float) -> str:
    return f"{r * 100:.0f} %"


def check_brake() -> dict[str, Any]:
    """Pause automatic retrying when it does not work; resume by itself after a new coverage gain."""
    st = brake_state()
    if st.get("paused"):
        gain = coverage.last_gain()
        paused_at = _ts(st.get("paused_at"))
        if gain and paused_at and gain["at"] > paused_at:
            return resume(ACTOR, trigger="coverage", gain=gain)
        return st
    eff = efficacy(since=_ts(st.get("resumed_at")))
    if eff["attempts"] >= BRAKE_MIN_ATTEMPTS and eff["ratio"] is not None and eff["ratio"] < BRAKE_MIN_RATIO:
        return _pause(eff, st)
    return st


def _pause(eff: dict[str, Any], prev: dict[str, Any]) -> dict[str, Any]:
    reason = (
        f"Automatisch opnieuw proberen staat op pauze: van de laatste {eff['attempts']} afgeronde pogingen "
        f"({eff['window_days']} dagen) lukte maar {_pct(eff['ratio'] or 0)} ({eff['verified']}). Opnieuw proberen "
        "vraagt dezelfde artikelen aan dezelfde servers; de oorzaak ligt dus waarschijnlijk bij de releases of bij "
        "SABnzbd zelf. Elke nutteloze poging telt mee in de faalratio. MSO hervat vanzelf zodra de dekking verbetert "
        "(een server werkt weer of er komt een nieuwe bij), of wanneer je op 'Hervatten' drukt."
    )
    st = {
        "paused": True,
        "paused_at": _now().isoformat(),
        "reason": reason,
        "attempts": eff["attempts"],
        "verified": eff["verified"],
        "ratio": eff["ratio"],
        "resumed_at": prev.get("resumed_at"),
        "resume_trigger": prev.get("resume_trigger"),
    }
    state.set_state(BRAKE_KEY, st)
    bundle = _open_bundle()
    if bundle:
        _discard_bundle(bundle)
    audit(ACTOR, "usenet.retry_paused",
          f"Automatisch opnieuw proberen gepauzeerd: {eff['verified']} van {eff['attempts']} pogingen gelukt",
          target="sabnzbd", detail=eff)
    outcomes.recommend(
        "usenet",
        "Automatisch opnieuw proberen gepauzeerd",
        reason,
        risk="advies",
        subject="sabnzbd",
        impact="Geen nutteloze pogingen meer die de faalratio verhogen",
        dedupe_key=BRAKE_REC_KEY,
        evidence={"attempts": eff["attempts"], "verified": eff["verified"], "ratio": eff["ratio"],
                  "window_days": eff["window_days"]},
    )
    get_db().execute(
        "UPDATE recommendations SET status='open', updated_at=now() WHERE dedupe_key=%s AND status='resolved'",
        (BRAKE_REC_KEY,),
    )
    return st


def resume(actor: str, *, trigger: str = "user", gain: dict[str, Any] | None = None) -> dict[str, Any]:
    """End the pause (operator button, or a new coverage gain). Later attempts are judged from now on."""
    st = brake_state()
    if not st.get("paused"):
        return st
    new = {
        "paused": False,
        "resumed_at": _now().isoformat(),
        "resumed_by": actor,
        "resume_trigger": trigger,
        "last_pause": {k: st.get(k) for k in ("paused_at", "attempts", "verified", "ratio")},
    }
    state.set_state(BRAKE_KEY, new)
    why = (
        f"de dekking is verbeterd ({coverage.describe(gain)})" if trigger == "coverage" and gain
        else "hervat door een gebruiker"
    )
    audit(actor, "usenet.retry_resumed", f"Automatisch opnieuw proberen hervat: {why}", target="sabnzbd",
          detail={"trigger": trigger, "gain_server": (gain or {}).get("server")})
    get_db().execute(  # also when accepted: a later pause shows the advisory again
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
        "AND status IN ('open','accepted')",
        (BRAKE_REC_KEY,),
    )
    return new


def _last_run() -> dict[str, Any] | None:
    from mso import state

    return state.get_state("usenet.autopilot_last_run", None)
