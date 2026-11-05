"""Unattended application of 'automatisch veilig' change sets and evaluation of applied ones."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes, jobs, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import Database, get_db, jsonb
from mso.domain.outcomes import recommend
from mso.features.automation import AREAS, TARGET_AREA
from mso.features.automation import evaluators as ev_mod
from mso.features.automation.evaluators import EVALUATORS, OUTCOME, OUTCOME_LABEL
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
        reason = "alleen-lezen modus" if state.mode() == "read_only" else "automatisering gepauzeerd"
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


# ------------------------------------------------------------------ controle na een wijziging
def not_evaluable(ch: dict[str, Any]) -> bool:
    """Removing/disabling or irreversible changes are never evaluated nor rolled back automatically."""
    return any(op.get("destructive") or op.get("irreversible") for op in (ch.get("ops") or []))


def _rollback_disables(ch: dict[str, Any]) -> bool:
    """A rollback that would itself switch something off (it enabled something: False → True) is a 'disable' and
    therefore needs explicit approval — never automatic."""
    for op in ch.get("ops") or []:
        if op.get("result") != "toegepast":
            continue
        before = op.get("observed_before", op.get("before"))
        if before is False and op.get("after") is True:
            return True
    return False


def _no_auto_rollback(ch: dict[str, Any], ev: dict[str, Any]) -> str | None:
    imp = ch.get("impact") if isinstance(ch.get("impact"), dict) else {}
    if ev.get("auto_rollback") is False:
        return str(ev.get("auto_rollback_reason") or "de evaluator sluit automatisch terugdraaien uit")
    if imp.get("guardrail") or imp.get("_guardrail") or imp.get("kind") == "guardrail":
        return "dit is een beleidsgrens; die blijft staan"
    if _rollback_disables(ch):
        return "terugdraaien zou iets uitschakelen; dat vereist altijd goedkeuring"
    return None


def auto_rollback_setting(ch: dict[str, Any]) -> tuple[bool, str]:
    """qBittorrent targets follow ``torrent.autotune_auto_rollback`` (the more specific setting, when the torrent
    module defines it); every other target follows ``automation.auto_rollback``."""
    if ch.get("target") == "qbittorrent" and "torrent.autotune_auto_rollback" in sr.REGISTRY:
        return bool(sr.get("torrent.autotune_auto_rollback")), "torrent.autotune_auto_rollback"
    return bool(sr.get("automation.auto_rollback")), "automation.auto_rollback"


def evaluator_for(target: str) -> tuple[str, Any]:
    fn = EVALUATORS.get(target)
    if fn is not None:
        return target, fn
    return "generiek", ev_mod.generic_evaluator


def run_evaluator(ch: dict[str, Any], *, interim: bool = False) -> dict[str, Any]:
    """Run (without storing) the evaluator for an applied change; always returns a normalised dict."""
    name, fn = evaluator_for(ch["target"])
    try:
        out = ev_mod.normalise(fn(dict(ch) | ({"_interim": True} if interim else {})))
    except Exception as exc:
        out = {
            "verdict": "insufficient",
            "measures": [],
            "message": f"Evaluator gaf een fout: {redact(str(exc))[:300]}",
            "error": True,
        }
    out["evaluator"] = name
    return out


def _measures_line(measures: list[dict[str, Any]]) -> str:
    parts = [f"{m.get('label', m.get('key'))}: {m.get('why')}" for m in measures if m.get("why")]
    return "; ".join(parts)


def evaluate_due(db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    default_h = int(sr.get("automation.evaluation_hours"))
    due = db.fetchall(
        "SELECT * FROM change_sets WHERE status='applied' AND applied_at IS NOT NULL "
        "AND NOT (impact ? 'evaluation') AND (ops @> '[{\"destructive\": true}]'::jsonb "
        "OR ops @> '[{\"irreversible\": true}]'::jsonb "
        "OR COALESCE(evaluate_after, applied_at + make_interval(hours => %s)) <= now()) ORDER BY id LIMIT 50",
        (default_h,),
    )
    counts = {k: 0 for k in ("beter", "gelijk", "slechter", "onvoldoende", "niet_van_toepassing")}
    counts["rolled_back"] = 0
    for ch in due:
        now = datetime.now(UTC).isoformat()
        area = change_area(ch, db)
        if not_evaluable(ch):
            _set_evaluation(
                ch["id"],
                {
                    "result": "niet_van_toepassing",
                    "label": OUTCOME_LABEL["niet_van_toepassing"],
                    "at": now,
                    "action": "geen",
                    "message": "Verwijderen of uitschakelen wordt niet geëvalueerd en nooit automatisch "
                    "teruggedraaid.",
                    "measures": [],
                },
                db,
            )
            _log("evaluate", "niet_van_toepassing", f"Niet geëvalueerd: {ch['title']}", ch["id"], area, db)
            counts["niet_van_toepassing"] += 1
            continue
        ev = run_evaluator(ch)
        outcome = OUTCOME[ev["verdict"]]
        counts[outcome] += 1
        detail_line = _measures_line(ev.get("measures") or [])
        record: dict[str, Any] = {
            "result": outcome,
            "label": OUTCOME_LABEL[outcome],
            "at": now,
            "evaluator": ev["evaluator"],
            "measures": ev.get("measures") or [],
            "windows": ev.get("windows"),
        }
        if outcome in ("beter", "gelijk"):
            record |= {
                "action": "behouden",
                "message": ev.get("message")
                or ("Verbetering gemeten; wijziging blijft." if outcome == "beter"
                    else "Geen meetbaar verschil binnen de hysterese; wijziging blijft."),
            }
            _set_evaluation(ch["id"], record, db)
            _log("evaluate", outcome, f"{OUTCOME_LABEL[outcome]}: {ch['title']} {detail_line}".strip(),
                 ch["id"], area, db)
            continue
        if outcome == "niet_van_toepassing":
            record |= {"action": "geen", "message": ev.get("message") or "Deze wijziging wordt niet gemeten."}
            _set_evaluation(ch["id"], record, db)
            _log("evaluate", outcome, f"Niet geëvalueerd: {ch['title']}", ch["id"], area, db)
            continue
        if outcome == "onvoldoende":
            record |= {
                "action": "behouden",
                "message": ev.get("message") or "Onvoldoende metingen om het effect te beoordelen; niets "
                "teruggedraaid.",
            }
            _set_evaluation(ch["id"], record, db)
            _log("evaluate", "onvoldoende", f"Onvoldoende data: {ch['title']} {detail_line}".strip(),
                 ch["id"], area, db)
            continue

        # slechter
        reason = "verslechtering gemeten" + (f": {detail_line}" if detail_line else "")
        enabled, setting_key = auto_rollback_setting(ch)
        blocker = _no_auto_rollback(ch, ev)
        msg = ""
        if enabled and blocker is None and state.automation_allowed():
            try:
                changes.rollback(ch["id"], ACTOR, reason=reason[:300], db=db)
            except Exception as exc:
                msg = f"Automatisch terugdraaien mislukt: {redact(str(getattr(exc, 'detail', exc)))[:300]}"
                _log("evaluate", "rollback_failed", f"{msg} ({ch['title']})", ch["id"], area, db)
            else:
                record |= {
                    "action": "teruggedraaid",
                    "reason": reason,
                    "setting": setting_key,
                    "message": f"Slechter gemeten; automatisch teruggedraaid ({setting_key} staat aan).",
                }
                _set_evaluation(ch["id"], record, db)
                audit(
                    ACTOR,
                    "change.auto_rolled_back",
                    f"Automatisch teruggedraaid na controle: {ch['title']} — {reason}"[:500],
                    target=ch["target"],
                    change_id=ch["id"],
                    detail={"reason": reason, "setting": setting_key, "measures": [
                        {k: m.get(k) for k in ("key", "label", "baseline", "current", "delta_pct", "verdict")}
                        for m in record["measures"]
                    ]},
                )
                _log("evaluate", "rolled_back", f"Teruggedraaid: {ch['title']} — {reason}", ch["id"], area, db)
                counts["rolled_back"] += 1
                continue
        if not msg:
            if not enabled:
                msg = f"Slechter gemeten; automatisch terugdraaien staat uit ({setting_key}). Terugdraaien voorgesteld."
            elif blocker:
                msg = f"Slechter gemeten; niet automatisch teruggedraaid: {blocker}. Terugdraaien voorgesteld."
            else:
                msg = "Slechter gemeten; automatisering is gepauzeerd of alleen-lezen. Terugdraaien voorgesteld."
        record |= {"action": "voorgesteld", "reason": reason, "message": msg}
        _set_evaluation(ch["id"], record, db)
        recommend(
            area or "automation",
            f"Terugdraaien aanbevolen: {ch['title']}",
            f"De controle na het toepassen meet een verslechtering ({detail_line or 'zie metingen'}). "
            "Controleer en draai de wijziging terug via Automatisering.",
            risk="toestemming_nodig",
            subject=ch["target"],
            change_id=ch["id"],
            dedupe_key=f"automation.rollback:{ch['id']}",
            evidence={"change_id": ch["id"], "evaluation": "slechter", "measures": record["measures"]},
            db=db,
        )
        _log("evaluate", "rollback_proposed", f"Terugdraaien voorgesteld: {ch['title']}", ch["id"], area, db)
        audit(
            ACTOR,
            "automation.rollback_proposed",
            f"Terugdraaien voorgesteld: {ch['title']} — {reason}"[:500],
            target=ch["target"],
            change_id=ch["id"],
        )
    return counts


def evaluation_view(ch: dict[str, Any], db: Database | None = None) -> dict[str, Any]:
    """Applied (or rolled back) change with its stored evaluation, or a live interim comparison."""
    db = db or get_db()
    imp = ch.get("impact") if isinstance(ch.get("impact"), dict) else {}
    default_h = int(sr.get("automation.evaluation_hours"))
    due = ch.get("evaluate_after")
    if due is None and ch.get("applied_at"):
        due = ch["applied_at"] + timedelta(hours=default_h)
    out: dict[str, Any] = {
        k: ch.get(k) for k in ("id", "target", "title", "risk", "status", "applied_at", "applied_by",
                               "rolled_back_at")
    }
    out["evaluate_at"] = due
    out["applicable"] = not not_evaluable(ch)
    out["evaluation"] = imp.get("evaluation")
    out["interim"] = None
    if out["evaluation"] is None and ch["status"] == "applied":
        if not out["applicable"]:
            out["evaluation"] = {
                "result": "niet_van_toepassing",
                "label": OUTCOME_LABEL["niet_van_toepassing"],
                "action": "geen",
                "message": "Verwijderen of uitschakelen wordt niet geëvalueerd en nooit automatisch teruggedraaid.",
                "measures": [],
            }
        else:
            ev = run_evaluator(ch, interim=True)
            out["interim"] = {
                "result": OUTCOME[ev["verdict"]],
                "label": OUTCOME_LABEL[OUTCOME[ev["verdict"]]],
                "evaluator": ev["evaluator"],
                "measures": ev.get("measures") or [],
                "message": ev.get("message") or "",
            }
    enabled, key = auto_rollback_setting(ch)
    out["auto_rollback"] = {"enabled": enabled, "setting": key}
    return out


@jobs.handler("automation.apply_safe")
def _job_apply(ctx: jobs.JobContext) -> dict[str, Any]:
    return apply_safe(ctx.db)


@jobs.handler("automation.evaluate")
def _job_evaluate(ctx: jobs.JobContext) -> dict[str, Any]:
    return evaluate_due(ctx.db)


jobs.every("automation.apply_safe", 300, enabled=state.automation_allowed)
jobs.every("automation.evaluate", 900)
