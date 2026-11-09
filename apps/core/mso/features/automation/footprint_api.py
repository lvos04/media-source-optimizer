"""Automatisering → Voetafdruk API (1.6.0, C35). See :mod:`mso.features.automation.footprint`.

- ``GET  /api/v1/automation/footprint``          (viewer)   stored footprint per system + owner overrides
- ``POST /api/v1/automation/footprint/refresh``  (operator) re-read every entry now (read-only, every mode)
- ``GET  /api/v1/automation/footprint/restore``  (viewer)   live list for "Alles terugzetten" (+ fingerprint)
- ``POST /api/v1/automation/footprint/restore``  (operator) execute that exact list (never in Alleen-lezen)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mso.auth import Principal, require
from mso.features.automation import footprint
from mso.features.automation.api import SCHEDULE_LABELS
from mso.security import limiter

router = APIRouter(prefix="/api/v1/automation/footprint", tags=["automatisering"])
# Systeem → Automatische taken: a readable label for the hourly check (the dict lives in automation/api.py)
SCHEDULE_LABELS.setdefault(footprint.JOB, ("Voetafdruk controleren (wat MSO veranderde: nog actief?)",
                                           "Automatisering"))


def _limit(bucket: str, actor: str, n: int) -> None:
    """These routes read every footprint entry live at SABnzbd/qBittorrent/Prowlarr: bounded per person."""
    ok, wait = limiter.hit(bucket, actor, n, 60)
    if not ok:
        raise HTTPException(429, detail=f"Te veel verzoeken; wacht {int(wait) + 1} s")


@router.get("")
def get_footprint(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return footprint.view()


@router.post("/refresh")
def refresh_footprint(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    _limit("footprint.refresh", p.actor, 6)
    footprint.refresh()
    return footprint.view()


@router.get("/restore")
def restore_preview(p: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    _limit("footprint.restore_plan", p.actor, 12)
    return footprint.restore_plan()


class RestoreBody(BaseModel):
    fingerprint: str = Field(..., min_length=1, max_length=64)


@router.post("/restore")
def restore(body: RestoreBody, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    _limit("footprint.restore", p.actor, 6)
    out = footprint.restore_all(p.actor, fingerprint=body.fingerprint, is_admin=p.can("admin"))
    return out | {"footprint": footprint.view()}
