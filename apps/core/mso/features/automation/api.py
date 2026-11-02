"""Automatisering API: recommendations inbox (accept/dismiss), area toggles, status/log, run now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mso import changes, jobs, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.auth import Principal, require
from mso.db import get_db
from mso.features.automation import AREAS, EVALUATORS, engine

router = APIRouter(prefix="/api/v1/automation", tags=["automatisering"])
RISK_EXPLANATION = {
    "advies": "Alleen uitleg of suggestie; er verandert niets tot jij zelf iets doet.",
    "automatisch_veilig": "Kleine, omkeerbare wijziging die automatisch mag worden toegepast als je dat per "
    "onderdeel aanzet (alleen in beheermodus, met limiet en evaluatie).",
    "toestemming_nodig": "Wijziging met mogelijk merkbaar effect; een beheerder moet eerst goedkeuren.",
}


def _change_summary(change_id: int | None) -> dict[str, Any] | None:
    if not change_id:
        return None
    ch = get_db().fetchone(
        "SELECT id, target, title, risk, status, created_at, applied_at, evaluate_after, impact "
        "FROM change_sets WHERE id=%s",
        (change_id,),
    )
    return dict(ch) if ch else None


@router.get("/recommendations")
def list_recommendations(
    area: str | None = None,
    risk: str | None = None,
    status: str | None = "open",
    limit: int = Query(100, ge=1, le=500),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    where, params = [], []
    for col, val in (("area", area), ("risk", risk), ("status", status)):
        if val and val != "all":
            where.append(f"{col} = ANY(%s)")
            params.append(val.split(","))
    sql = "SELECT * FROM recommendations" + (" WHERE " + " AND ".join(where) if where else "")
    sql += (
        " ORDER BY CASE risk WHEN 'toestemming_nodig' THEN 0 WHEN 'automatisch_veilig' THEN 1 ELSE 2 END, "
        "updated_at DESC LIMIT %s"
    )
    rows = get_db().fetchall(sql, params + [limit])
    items = []
    for r in rows:
        item = dict(r)
        item["change"] = _change_summary(r["change_id"])
        ev = r["evidence"] or {}
        item["can_create_change"] = bool(
            isinstance(ev, dict) and ev.get("proposed_change") and not r["change_id"]
        )
        items.append(item)
    counts = get_db().fetchall(
        "SELECT area, risk, count(*) AS n FROM recommendations WHERE status='open' GROUP BY area, risk"
    )
    return {"items": items, "open_counts": counts}


def _rec(rec_id: int) -> dict[str, Any]:
    r = get_db().fetchone("SELECT * FROM recommendations WHERE id=%s", (rec_id,))
    if r is None:
        raise HTTPException(404, detail="Aanbeveling niet gevonden")
    return dict(r)


@router.post("/recommendations/{rec_id}/accept")
def accept(rec_id: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """Accept = acknowledge and (if the recommendation proposes one) create/link its change set in preview.
    Applying still happens explicitly via the change set (approval rules unchanged)."""
    r = _rec(rec_id)
    if r["status"] != "open":
        raise HTTPException(409, detail=f"Aanbeveling heeft status '{r['status']}'")
    change_id = r["change_id"]
    ev = r["evidence"] or {}
    proposed = ev.get("proposed_change") if isinstance(ev, dict) else None
    if not change_id and isinstance(proposed, dict):
        try:
            ch = changes.create(
                proposed["target"],
                proposed.get("title") or r["title"],
                list(proposed["ops"]),
                risk=proposed.get("risk", r["risk"]),
                explanation=proposed.get("explanation") or r["explanation"],
                impact=proposed.get("impact") or {"area": r["area"]},
                source=f"recommendation:{rec_id}",
                actor=p.actor,
                dedupe_key=proposed.get("dedupe_key") or f"rec:{rec_id}",
                evaluate_after_h=proposed.get("evaluate_after_h"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, detail=f"Voorgestelde wijziging is onvolledig: {exc}") from exc
        change_id = ch["id"]
    get_db().execute(
        "UPDATE recommendations SET status='accepted', change_id=%s, updated_at=now() WHERE id=%s",
        (change_id, rec_id),
    )
    audit(
        p.actor,
        "recommendation.accepted",
        f"Aanbeveling geaccepteerd: {r['title']}",
        target=r["area"],
        change_id=change_id,
    )
    return _rec(rec_id) | {"change": _change_summary(change_id)}


class DismissBody(BaseModel):
    reason: str = Field("", max_length=500)


@router.post("/recommendations/{rec_id}/dismiss")
def dismiss(
    rec_id: int, body: DismissBody | None = None, p: Principal = Depends(require("operator"))
) -> dict[str, Any]:
    r = _rec(rec_id)
    if r["status"] not in ("open", "accepted"):
        raise HTTPException(409, detail=f"Aanbeveling heeft status '{r['status']}'")
    get_db().execute("UPDATE recommendations SET status='dismissed', updated_at=now() WHERE id=%s", (rec_id,))
    reason = body.reason if body else ""
    audit(
        p.actor,
        "recommendation.dismissed",
        f"Aanbeveling afgewezen: {r['title']}" + (f" ({reason})" if reason else ""),
        target=r["area"],
    )
    return _rec(rec_id)


@router.get("/status")
def status(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    db = get_db()
    pending = engine.pending_safe(db)
    per_area: dict[str, int] = {}
    for ch in pending:
        a = engine.change_area(ch, db) or "onbekend"
        per_area[a] = per_area.get(a, 0) + 1
    areas = [
        {
            "area": a,
            "label": label,
            "auto_apply_safe": bool(sr.get(f"automation.{a}.auto_apply_safe")),
            "setting_key": f"automation.{a}.auto_apply_safe",
            "pending_safe_changes": per_area.get(a, 0),
        }
        for a, label in AREAS.items()
    ]
    jobs_summary = db.fetchall(
        "SELECT kind, count(*) FILTER (WHERE status='failed' AND updated_at > now() - interval '24 hours') "
        "AS failed_24h, "
        "count(*) FILTER (WHERE status IN ('queued','running')) AS pending, max(updated_at) AS last_update "
        "FROM jobs GROUP BY kind ORDER BY kind"
    )
    return {
        "mode": state.mode(),
        "paused": state.paused(),
        "automation_allowed": state.automation_allowed(),
        "areas": areas,
        "max_per_hour": int(sr.get("automation.max_per_hour")),
        "max_age_h": int(sr.get("automation.max_age_h")),
        "auto_rollback": bool(sr.get("automation.auto_rollback")),
        "applied_last_hour": engine.applied_last_hour(db),
        "evaluators": sorted(EVALUATORS),
        "risk_explanation": RISK_EXPLANATION,
        "log": db.fetchall("SELECT * FROM automation_log ORDER BY id DESC LIMIT 30"),
        "jobs": jobs_summary,
    }


class TogglesBody(BaseModel):
    areas: dict[str, bool]


@router.put("/toggles")
def put_toggles(body: TogglesBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    unknown = [a for a in body.areas if a not in AREAS]
    if unknown:
        raise HTTPException(422, detail=f"Onbekend onderdeel: {', '.join(unknown)}")
    changed = sr.put({f"automation.{a}.auto_apply_safe": v for a, v in body.areas.items()}, p.actor)
    for key, (old, new) in changed.items():
        audit(
            p.actor,
            "automation.toggle",
            f"{sr.REGISTRY[key].label}: {'aan' if new else 'uit'}",
            target=key,
            detail={"before": old, "after": new},
        )
    return status(p)


@router.post("/run")
def run_now(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not state.automation_allowed():
        raise HTTPException(409, detail="Automatisering kan alleen draaien in beheermodus zonder pauze")
    import time

    job = jobs.enqueue(
        "automation.apply_safe",
        {},
        idempotency_key=f"automation.run:{int(time.time() // 60)}",
        max_attempts=1,
        created_by=p.actor,
    )
    audit(p.actor, "automation.run_requested", "Automatische ronde handmatig gestart", target="automation")
    return {"job_id": job["id"], "status": job["status"]}
