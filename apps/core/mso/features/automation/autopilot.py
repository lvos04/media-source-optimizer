"""Autopilot helpers for other slices: "propose and (if allowed) apply in one go".

Usage from any feature module::

    from mso.features.automation import autopilot

    if autopilot.enabled("usenet"):
        ...  # e.g. retry a failed download yourself (direct action, audited)

    ch = autopilot.propose_and_apply(
        "sabnzbd", "Server 'xsnews' uitschakelen (abonnement verlopen)",
        [{"path": "servers/xsnews/enable", "label": "xsnews actief", "before": True, "after": False,
          "destructive": True}],
        explanation="…", impact={"Reden": "482 expired"}, dedupe_key="usenet.server_off:xsnews",
        area="usenet")
    ch["auto_applied"]  # True when MSO applied it right away

Rules (enforced again by :func:`mso.changes.apply`):
- Only in beheermodus/Autopilot without pause; in alleen-lezen nothing is ever written.
- An area is "on" when Autopilot is active, or when its own toggle ``automation.<area>.auto_apply_safe`` is on.
- ``destructive`` ops (switching off, reversible) are only applied automatically in Autopilot.
- ``irreversible`` ops (deleting) are NEVER applied automatically: such a change is created as
  ``toestemming_nodig`` and waits for one approval.
- The global cap ``automation.max_per_hour`` applies; over the cap the change stays a proposal and the periodic
  ``automation.apply_safe`` job picks it up later (within ``automation.max_age_h``).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from mso import changes, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import Database, get_db
from mso.features.automation import AREAS

ACTOR = "automatisering"


def area_toggle(area: str | None) -> bool:
    if area not in AREAS:
        return False
    return bool(sr.get(f"automation.{area}.auto_apply_safe"))


def area_on(area: str | None) -> bool:
    """Area switched on for automatic application (Autopilot, or the area's own toggle). Ignores mode/pause."""
    return state.autopilot() or area_toggle(area)


def enabled(area: str) -> bool:
    """May MSO act on its own in ``area`` right now? True when not read-only, not paused and either Autopilot
    is active or the area's 'automatisch veilig toepassen' toggle is on."""
    return state.automation_allowed() and area_on(area)


def applied_last_hour(db: Database | None = None) -> int:
    return int(
        (db or get_db()).scalar(
            "SELECT count(*) FROM automation_log WHERE kind='apply' AND result='applied' "
            "AND ts > now() - interval '1 hour'"
        )
        or 0
    )


def budget_left(db: Database | None = None) -> int:
    return int(sr.get("automation.max_per_hour")) - applied_last_hour(db)


def why_not(ch: dict[str, Any], area: str | None) -> str | None:
    """Reason this change may not be applied automatically now, or None when it may."""
    ops = ch.get("ops") or []
    if ch.get("status") not in ("preview",):
        return f"status '{ch.get('status')}'"
    if any(op.get("irreversible") for op in ops):
        return "verwijderen gebeurt nooit automatisch; één goedkeuring van jou nodig"
    if ch.get("risk") != "automatisch_veilig":
        return "vereist goedkeuring"
    if not state.automation_allowed():
        return "alleen-lezen modus" if state.mode() == "read_only" else "automatisering gepauzeerd"
    if any(op.get("destructive") for op in ops) and not state.autopilot():
        return "uitschakelen gebeurt alleen automatisch in Autopilot"
    if not area_on(area):
        return "automatisch toepassen staat uit voor dit onderdeel"
    return None


def log(kind: str, result: str, message: str, change_id: int | None = None, area: str | None = None,
        db: Database | None = None) -> None:
    from mso.security import redact

    (db or get_db()).execute(
        "INSERT INTO automation_log(kind,change_id,area,result,message) VALUES (%s,%s,%s,%s,%s)",
        (kind, change_id, area, result, redact(message)[:1000]),
    )


def apply_one(ch: dict[str, Any], area: str | None, db: Database | None = None) -> tuple[bool, str | None]:
    """Apply one change automatically (no checks beyond :func:`changes.apply`'s). Logs + audits.
    Returns (applied, error)."""
    db = db or get_db()
    try:
        changes.apply(ch["id"], ACTOR, automatic=True, db=db)
    except HTTPException as exc:
        log("apply", "failed", f"'{ch['title']}': {exc.detail}", ch["id"], area, db)
        audit(ACTOR, "automation.apply_failed", f"Automatisch toepassen mislukt: {ch['title']}",
              target=ch["target"], change_id=ch["id"], detail={"error": str(exc.detail), "area": area})
        return False, str(exc.detail)
    db.execute("UPDATE recommendations SET status='accepted', updated_at=now() WHERE change_id=%s "
               "AND status='open'", (ch["id"],))
    log("apply", "applied", f"Automatisch toegepast: {ch['title']}", ch["id"], area, db)
    audit(ACTOR, "automation.applied", f"Automatisch toegepast: {ch['title']}", target=ch["target"],
          change_id=ch["id"], detail={"area": area, "autopilot": state.autopilot()})
    return True, None


def propose_and_apply(target: str, title: str, ops: list[dict[str, Any]], *, explanation: str,
                      impact: dict[str, Any] | None, dedupe_key: str | None, evaluate_after_h: float | None = None,
                      area: str) -> dict[str, Any]:
    """Create the change as ``automatisch_veilig`` (``toestemming_nodig`` when an op is ``irreversible``) and apply
    it right away when allowed (see module docstring). Otherwise it stays a preview proposal.

    Returns the change row plus ``auto_applied: bool`` and ``auto_reason: str | None`` (why it was not applied,
    or the apply error). Idempotent on ``dedupe_key`` like :func:`changes.create`. Never raises on a failed
    automatic apply (the change is then ``failed`` and audited); raises ValueError/HTTPException only for an
    invalid change (empty ops, policy validation)."""
    db = get_db()
    irreversible = any(op.get("irreversible") for op in ops)
    imp = {"area": area, **(impact or {})}
    if state.autopilot():
        imp.setdefault("autopilot", True)
    ch = changes.create(target, title, [dict(op) for op in ops],
                        risk="toestemming_nodig" if irreversible else "automatisch_veilig",
                        explanation=explanation, impact=imp, source=f"automation:{area}", actor=ACTOR,
                        dedupe_key=dedupe_key, evaluate_after_h=evaluate_after_h, db=db)
    reason = why_not(ch, area)
    if reason is None and budget_left(db) <= 0:
        cap = int(sr.get("automation.max_per_hour"))
        reason = f"limiet van {cap} automatische wijzigingen per uur bereikt; volgt later"
        log("apply", "rate_limited", f"Limiet van {cap} per uur bereikt; '{title}' wacht", ch["id"], area, db)
    if reason is not None:
        return dict(ch) | {"auto_applied": False, "auto_reason": reason}
    ok, err = apply_one(ch, area, db)
    return changes.get(ch["id"], db) | {"auto_applied": ok, "auto_reason": err}


__all__ = ["apply_one", "area_on", "budget_left", "enabled", "propose_and_apply", "why_not"]
