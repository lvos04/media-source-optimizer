"""Thin bridge to Automatisering's Autopilot helper (``mso.features.automation.autopilot``).

Imported lazily so the Usenet/recovery modules keep working when the automation module is disabled
(``MSO_FEATURES``) or absent: then a change is created as ``automatisch_veilig`` and applied with
``changes.apply(automatic=True)`` only while Autopilot is active (same rules, enforced by :mod:`mso.changes`).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from mso import changes, state

LOGGER = logging.getLogger(__name__)
ACTOR = "autopilot"


def _autopilot() -> Any:
    try:
        from mso.features.automation import autopilot

        return autopilot
    except Exception:  # module disabled/absent
        return None


def enabled(area: str) -> bool:
    ap = _autopilot()
    if ap is not None:
        try:
            return bool(ap.enabled(area))
        except Exception:  # pragma: no cover - defensive
            LOGGER.debug("autopilot.enabled faalde", exc_info=True)
    return state.autopilot()


def propose_and_apply(
    target: str,
    title: str,
    ops: list[dict[str, Any]],
    *,
    explanation: str,
    impact: dict[str, Any] | None,
    dedupe_key: str | None,
    area: str,
    evaluate_after_h: float | None = None,
) -> dict[str, Any]:
    """Create the change (``automatisch_veilig``) and apply it right away when Autopilot/the area allows it.
    Returns the change row plus ``auto_applied`` and ``auto_reason``."""
    ap = _autopilot()
    if ap is not None and hasattr(ap, "propose_and_apply"):
        return dict(
            ap.propose_and_apply(
                target,
                title,
                ops,
                explanation=explanation,
                impact=impact,
                dedupe_key=dedupe_key,
                evaluate_after_h=evaluate_after_h,
                area=area,
            )
        )
    ch = changes.create(
        target,
        title,
        [dict(op) for op in ops],
        risk="automatisch_veilig",
        explanation=explanation,
        impact={"area": area, **(impact or {})},
        source=f"automation:{area}",
        actor=ACTOR,
        dedupe_key=dedupe_key,
        evaluate_after_h=evaluate_after_h,
    )
    if not state.autopilot():
        return dict(ch) | {"auto_applied": False, "auto_reason": "Autopilot staat uit"}
    try:
        changes.apply(ch["id"], ACTOR, automatic=True)
    except HTTPException as exc:
        return changes.get(ch["id"]) | {"auto_applied": False, "auto_reason": str(exc.detail)}
    return changes.get(ch["id"]) | {"auto_applied": True, "auto_reason": None}
