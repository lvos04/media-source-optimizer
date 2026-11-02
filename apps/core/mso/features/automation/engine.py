"""Unattended application of 'automatisch veilig' change sets and evaluation of applied ones."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from mso import changes, jobs, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import Database, get_db, jsonb
from mso.domain.outcomes import recommend
from mso.features.automation import AREAS, TARGET_AREA
from mso.features.automation.evaluators import EVALUATORS, RESULTS
from mso.security import redact

LOGGER = logging.getLogger(__name__)
ACTOR = "automatisering"


def change_area(ch: dict[str, Any], db: Database | None = None) -> str | None:
    db = db or get_db()
    area = db.scalar("SELECT area FROM recommendations WHERE change_id=%s ORDER BY id LIMIT 1", (ch["id"],))
    if area:
        return str(area)
    if ch["target"] in TARGET_AREA:
        return TARGET_AREA[ch["target"]]
    imp = ch.get("impact") or {}
    return imp.get("area") if isinstance(imp, dict) else None


def area_enabled(area: str | None) -> bool:
    if area not in AREAS:
        return False
    return bool(sr.get(f"automation.{area}.auto_apply_safe"))


def _log(
    kind: str,
    result: str,
    message: str,
    change_id: int | None = None,
    area: str | None = None,
    db: Database | None = None,
) -> None:
    (db or get_db()).execute(
        "INSERT INTO automation_log(kind,change_id,area,result,message) VALUES (%s,%s,%s,%s,%s)",
        (kind, change_id, area, result, redact(message)[:1000]),
    )


def applied_last_hour(db: Database | None = None) -> int:
    return int(
        (db or get_db()).scalar(
            "SELECT count(*) FROM automation_log WHERE kind='apply' AND result='applied' "
            "AND ts > now() - interval '1 hour'"
        )
        or 0
    )


def pending_safe(db: Database | None = None) -> list[dict[str, Any]]:
    max_age = int(sr.get("automation.max_age_h"))
    return (db or get_db()).fetchall(
        "SELECT * FROM change_sets WHERE status='preview' AND risk='automatisch_veilig' "
        "AND created_at > now() - make_interval(hours => %s) ORDER BY id",
        (max_age,),
    )


def apply_safe(db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    if not state.automation_allowed():
        reason = "alleen-lezen modus" if state.mode() != "manage" else "automatisering gepauzeerd"
        _log("run", "skipped", f"Niet uitgevoerd: {reason}", db=db)
        return {"applied": 0, "skipped": "not_allowed", "reason": reason}
    cap = int(sr.get("automation.max_per_hour"))
    budget = cap - applied_last_hour(db)
    applied, skipped, failed = [], [], []
    for ch in pending_safe(db):
        area = change_area(ch, db)
        if not area_enabled(area):
            skipped.append(ch["id"])
            continue
        if budget <= 0:
            _log(
                "apply",
                "rate_limited",
                f"Limiet van {cap} per uur bereikt; '{ch['title']}' wacht",
                ch["id"],
                area,
                db,
            )
            skipped.append(ch["id"])
            continue
        try:
            changes.apply(ch["id"], ACTOR, automatic=True, db=db)
        except HTTPException as exc:
            failed.append(ch["id"])
            _log("apply", "failed", f"'{ch['title']}': {exc.detail}", ch["id"], area, db)
            audit(
                ACTOR,
                "automation.apply_failed",
                f"Automatisch toepassen mislukt: {ch['title']}",
                target=ch["target"],
                change_id=ch["id"],
                detail={"error": str(exc.detail), "area": area},
            )
            continue
        budget -= 1
        applied.append(ch["id"])
        db.execute(
            "UPDATE recommendations SET status='accepted', updated_at=now() WHERE change_id=%s "
            "AND status='open'",
            (ch["id"],),
        )
        _log("apply", "applied", f"Automatisch toegepast: {ch['title']}", ch["id"], area, db)
        audit(
            ACTOR,
            "automation.applied",
            f"Automatisch veilig toegepast: {ch['title']}",
            target=ch["target"],
            change_id=ch["id"],
            detail={"area": area},
        )
    _log(
        "run", "done", f"{len(applied)} toegepast, {len(skipped)} overgeslagen, {len(failed)} mislukt", db=db
    )
    return {"applied": applied, "skipped": skipped, "failed": failed}


def _set_evaluation(change_id: int, evaluation: dict[str, Any], db: Database) -> None:
    db.execute(
        "UPDATE change_sets SET impact = impact || %s WHERE id=%s",
        (jsonb({"evaluation": evaluation}), change_id),
    )


def evaluate_due(db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    due = db.fetchall(
        "SELECT * FROM change_sets WHERE status='applied' AND evaluate_after IS NOT NULL "
        "AND evaluate_after <= now() AND NOT (impact ? 'evaluation') ORDER BY id LIMIT 50"
    )
    counts = {"keep": 0, "rollback": 0, "unknown": 0, "niet_beschikbaar": 0}
    for ch in due:
        now = datetime.now(UTC).isoformat()
        area = change_area(ch, db)
        fn = EVALUATORS.get(ch["target"])
        if fn is None:
            _set_evaluation(
                ch["id"],
                {
                    "result": "niet_beschikbaar",
                    "at": now,
                    "message": "Evaluatie niet beschikbaar voor dit systeem; niets "
                    "teruggedraaid. Beoordeel het effect zelf.",
                },
                db,
            )
            _log("evaluate", "niet_beschikbaar", f"Geen evaluator voor '{ch['target']}'", ch["id"], area, db)
            counts["niet_beschikbaar"] += 1
            continue
        try:
            result = fn(dict(ch))
        except Exception as exc:  # noqa: BLE001
            result, msg = "unknown", f"Evaluator gaf een fout: {redact(str(exc))[:300]}"
        else:
            msg = ""
        if result not in RESULTS:
            result, msg = "unknown", f"Onbekende evaluatie-uitkomst '{result}'"
        counts[result] += 1
        if result == "keep":
            _set_evaluation(
                ch["id"],
                {"result": "keep", "at": now, "message": "Effect positief of neutraal; wijziging blijft"},
                db,
            )
            _log("evaluate", "keep", f"Behouden: {ch['title']}", ch["id"], area, db)
        elif result == "unknown":
            _set_evaluation(
                ch["id"],
                {
                    "result": "unknown",
                    "at": now,
                    "message": msg or "Onvoldoende gegevens om het effect te beoordelen",
                },
                db,
            )
            _log("evaluate", "unknown", f"Onbeslist: {ch['title']} {msg}".strip(), ch["id"], area, db)
        else:
            auto = (
                sr.get("automation.auto_rollback")
                and ch.get("applied_by") == ACTOR
                and state.automation_allowed()
            )
            if auto:
                try:
                    changes.rollback(ch["id"], ACTOR, reason="negatieve evaluatie", db=db)
                    _set_evaluation(
                        ch["id"],
                        {
                            "result": "rollback",
                            "at": now,
                            "action": "teruggedraaid",
                            "message": "Negatief effect gemeten; automatisch teruggedraaid",
                        },
                        db,
                    )
                    _log("evaluate", "rolled_back", f"Teruggedraaid: {ch['title']}", ch["id"], area, db)
                    continue
                except Exception as exc:  # noqa: BLE001
                    msg = (
                        f"Automatisch terugdraaien mislukt: {redact(str(getattr(exc, 'detail', exc)))[:300]}"
                    )
            _set_evaluation(
                ch["id"],
                {
                    "result": "rollback",
                    "at": now,
                    "action": "voorgesteld",
                    "message": msg or "Negatief effect gemeten; terugdraaien voorgesteld",
                },
                db,
            )
            recommend(
                area or "automation",
                f"Terugdraaien aanbevolen: {ch['title']}",
                "De evaluatie na het toepassen wijst op een negatief effect. Controleer en draai de "
                "wijziging terug via Automatisering.",
                risk="toestemming_nodig",
                subject=ch["target"],
                change_id=ch["id"],
                dedupe_key=f"automation.rollback:{ch['id']}",
                evidence={"change_id": ch["id"], "evaluation": "rollback"},
                db=db,
            )
            _log(
                "evaluate",
                "rollback_proposed",
                f"Terugdraaien voorgesteld: {ch['title']}",
                ch["id"],
                area,
                db,
            )
            audit(
                ACTOR,
                "automation.rollback_proposed",
                f"Terugdraaien voorgesteld: {ch['title']}",
                target=ch["target"],
                change_id=ch["id"],
            )
    return counts


@jobs.handler("automation.apply_safe")
def _job_apply(ctx: jobs.JobContext) -> dict[str, Any]:
    return apply_safe(ctx.db)


@jobs.handler("automation.evaluate")
def _job_evaluate(ctx: jobs.JobContext) -> dict[str, Any]:
    return evaluate_due(ctx.db)


jobs.every("automation.apply_safe", 300, enabled=state.automation_allowed)
jobs.every("automation.evaluate", 900)
