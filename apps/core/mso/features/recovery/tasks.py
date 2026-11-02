"""Periodic recovery analysis (worker job ``recovery.analyze``).

1. Failed ``download_outcomes`` with a release-specific cause → blacklist their release key (each outcome is
   counted once, so the TTL escalation and hard cap reflect real repeat failures).
2. Manual sends are matched with their outcome: failure → per-item exponential backoff; success → reset.
3. Recommendations (area ``recovery``): sources with repeated false positives, items with an accepted fallback
   candidate (action: ask MediaGuard to search / send manually in standalone).
4. Optional (default off): bounded automatic candidate scan, and — only with
   ``mediaguard.auto_search_requests`` on AND ``state.automation_allowed()`` — automatic search requests to
   MediaGuard for items that have a candidate passing every MediaGuard requirement.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from mso import jobs, state
from mso import settings_registry as sr
from mso.db import get_db
from mso.domain.outcomes import recommend
from mso.features.recovery import baseline, blacklist, candidates

LOGGER = logging.getLogger(__name__)


def process_outcomes() -> dict[str, int]:
    db = get_db()
    rows = db.fetchall(
        "SELECT o.* FROM download_outcomes o LEFT JOIN recovery_outcome_seen s ON s.outcome_id=o.id "
        "WHERE o.status='failed' AND s.outcome_id IS NULL ORDER BY o.id LIMIT 1000"
    )
    added = 0
    backoffs = 0
    for o in rows:
        send = db.fetchone(
            "SELECT * FROM recovery_sends WHERE client=%s AND (client_ref=%s OR release_key=%s) "
            "AND status='sent' ORDER BY id DESC LIMIT 1",
            (o["client"], o["client_ref"], o["release_key"] or ""),
        )
        media_ref = send["media_ref"] if send else None
        if o["release_key"] and (o["failure_reason"] or "") in blacklist.RELEASE_SPECIFIC_FAILURES:
            blacklist.add(
                o["release_key"], f"download mislukt: {o['failure_reason']}", None, media_ref, o["protocol"]
            )
            added += 1
        if send:
            db.execute(
                "UPDATE recovery_sends SET status='download_failed', error=%s, updated_at=now() WHERE id=%s",
                (o["failure_reason"] or "onbekend", send["id"]),
            )
            blacklist.register_media_failure(send["media_ref"])
            backoffs += 1
        db.execute(
            "INSERT INTO recovery_outcome_seen(outcome_id) VALUES (%s) ON CONFLICT DO NOTHING", (o["id"],)
        )
    ok = db.fetchall(
        "SELECT s.id, s.media_ref FROM recovery_sends s JOIN download_outcomes o ON o.client=s.client "
        "AND (o.client_ref=s.client_ref OR o.release_key=s.release_key) WHERE s.status='sent' "
        "AND o.status='success'"
    )
    for s in ok:
        db.execute("UPDATE recovery_sends SET status='download_ok', updated_at=now() WHERE id=%s", (s["id"],))
        blacklist.clear_media_backoff(s["media_ref"])
    return {"blacklisted": added, "media_backoffs": backoffs, "successes": len(ok)}


def make_recommendations() -> int:
    n = 0
    base = baseline.compute()
    for src in base["by_source"]:
        fp = src["counts"]["false_positive"]
        if src["origin"] == "download" and fp >= 3 and fp / max(1, src["total"]) >= 0.3:
            recommend(
                "recovery",
                f"{src['label']}: veel vals-positieve resultaten",
                f"{fp} van {src['total']} mislukte downloads uit deze bron bleken vals-positief in de "
                f"laatste "
                f"{base['window_days']} dagen. Overweeg de bron lager te prioriteren.",
                subject=src["source_id"],
                confidence=min(0.9, 0.4 + fp / 20),
                dedupe_key=f"recovery:fp:{src['source_id']}",
                evidence={"counts": src["counts"], "window_days": base["window_days"]},
            )
            n += 1
    from mso.features.mediaguard import service as mg

    connected = mg.mode() == "connected"
    rows = get_db().fetchall(
        "SELECT media_ref, count(*) AS n, max(score) AS best, (array_agg(title ORDER BY score DESC))[1] AS "
        "title "
        "FROM recovery_candidates WHERE passed AND expires_at > now() GROUP BY media_ref"
    )
    for r in rows:
        action = (
            "Vraag MediaGuard opnieuw te zoeken"
            if connected and r["media_ref"].startswith("mg:")
            else "Verstuur handmatig naar de downloadclient"
        )
        recommend(
            "recovery",
            f"Fallback-kandidaat gevonden ({r['media_ref']})",
            f"{r['n']} release(s) voldoen aan alle kwaliteitseisen, bv. '{r['title']}'. Actie: {action}.",
            subject=r["media_ref"],
            confidence=0.7,
            dedupe_key=f"recovery:cand:{r['media_ref']}",
            evidence={"accepted": r["n"], "best_score": r["best"]},
        )
        n += 1
    return n


def auto_scan() -> dict[str, Any]:
    if not sr.get("herstel.auto_candidate_scan"):
        return {"skipped": "uit"}
    limit = int(sr.get("herstel.auto_candidates_per_run"))
    db = get_db()
    refs = [
        f"mg:{r['mg_id']}"
        for r in db.fetchall(
            "SELECT mg_id FROM mg_wanted_cache c WHERE last_reason_code IN "
            "('no-indexer-hit','searched-no-grab',"
            "'rejected-quality','rejected-language','download-client-failed') AND NOT EXISTS (SELECT 1 FROM "
            "recovery_candidates rc WHERE rc.media_ref='mg:' || c.mg_id AND rc.expires_at > now()) "
            "ORDER BY (raw->>'priority')::int DESC NULLS LAST LIMIT %s",
            (limit,),
        )
    ]
    refs += [
        f"manual:{r['id']}"
        for r in db.fetchall(
            "SELECT id FROM recovery_manual_wanted m WHERE status='open' AND NOT EXISTS (SELECT 1 FROM "
            "recovery_candidates rc WHERE rc.media_ref='manual:' || m.id AND rc.expires_at > now()) "
            "ORDER BY id LIMIT %s",
            (limit,),
        )
    ]
    done = 0
    for ref in refs[:limit]:
        try:
            candidates.find_candidates(ref, actor="system:herstel")
            done += 1
        except candidates.SearchUnavailable:
            return {"skipped": "zoekmachine niet beschikbaar", "scanned": done}
        except HTTPException as exc:
            LOGGER.info("kandidaten voor %s overgeslagen: %s", ref, exc.detail)
    return {"scanned": done}


def auto_search_requests() -> dict[str, Any]:
    if not (sr.get("mediaguard.auto_search_requests") and state.automation_allowed()):
        return {"skipped": "uit of niet toegestaan"}
    from mso.features.mediaguard import service as mg

    if not mg.can_request_search()[0]:
        return {"skipped": "MediaGuard niet beschikbaar voor zoekverzoeken"}
    rows = get_db().fetchall(
        "SELECT DISTINCT substring(media_ref from 4)::bigint AS mg_id FROM recovery_candidates "
        "WHERE passed AND expires_at > now() AND media_ref LIKE 'mg:%%'"
    )
    sent = 0
    for r in rows:
        try:
            res = mg.request_search(int(r["mg_id"]), actor="system:herstel", trigger="auto")
            sent += 0 if res.get("duplicate") else 1
        except HTTPException as exc:
            if exc.status_code == 429:
                break  # quota reached: stop for this run
    return {"requested": sent}


@jobs.handler("recovery.analyze")
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    out: dict[str, Any] = {"outcomes": process_outcomes()}
    ctx.progress(0.3)
    out["auto_scan"] = auto_scan()
    ctx.progress(0.6)
    out["recommendations"] = make_recommendations()
    out["auto_search"] = auto_search_requests()
    return out


jobs.every("recovery.analyze", 1800)
