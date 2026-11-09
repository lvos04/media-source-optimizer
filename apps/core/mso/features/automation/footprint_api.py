"""Automatisering → Voetafdruk API (1.6.0, C35). See :mod:`mso.features.automation.footprint`.

- ``GET  /api/v1/automation/footprint``          (viewer)   stored footprint per system + owner overrides
- ``POST /api/v1/automation/footprint/refresh``  (operator) re-read every entry now (read-only, every mode)
- ``GET  /api/v1/automation/footprint/restore``  (viewer)   live list for "Alles terugzetten" (+ fingerprint)
- ``POST /api/v1/automation/footprint/restore``  (operator) execute that exact list (never in Alleen-lezen)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from mso.auth import Principal, require
from mso.features.automation import footprint
from mso.features.automation.api import SCHEDULE_LABELS

router = APIRouter(prefix="/api/v1/automation/footprint", tags=["automatisering"])
# Systeem → Automatische taken: a readable label for the hourly check (the dict lives in automation/api.py)
SCHEDULE_LABELS.setdefault(footprint.JOB, ("Voetafdruk controleren (wat MSO veranderde: nog actief?)",
                                           "Automatisering"))


@router.get("")
def get_footprint(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return footprint.view()


@router.post("/refresh")
def refresh_footprint(_: Principal = Depends(require("operator"))) -> dict[str, Any]:
    footprint.refresh()
    return footprint.view()


@router.get("/restore")
def restore_preview(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return footprint.restore_plan()


class RestoreBody(BaseModel):
    fingerprint: str = Field(..., min_length=1, max_length=64)


@router.post("/restore")
def restore(body: RestoreBody, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    out = footprint.restore_all(p.actor, fingerprint=body.fingerprint, is_admin=p.can("admin"))
    return out | {"footprint": footprint.view()}
