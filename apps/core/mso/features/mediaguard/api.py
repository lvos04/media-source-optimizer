"""``/api/v1/mediaguard`` — handshake/status, compatibility matrix, cached wanted data, request search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from mso import jobs
from mso.auth import Principal, require
from mso.connectors import mediaguard as mgc
from mso.features.mediaguard import compat, service

router = APIRouter(prefix="/api/v1/mediaguard", tags=["mediaguard"])


@jobs.handler("mediaguard.sync")
def _job_sync(ctx: jobs.JobContext) -> dict[str, Any]:
    from mso import settings_registry as sr

    if not service.configured():
        return {"skipped": "standalone"}
    if not ctx.payload.get("force"):
        last = (service.snap_data("sync_state", {}) or {}).get("finished_at")
        interval = int(sr.get("mediaguard.sync_interval_min")) * 60
        if last:
            from datetime import UTC, datetime

            if (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() < interval - 30:
                return {"skipped": "not_due"}
    return service.sync(force=bool(ctx.payload.get("force")), actor=ctx.payload.get("actor", "system"))


jobs.every("mediaguard.sync", 300, enabled=service.configured)


@router.get("/status")
def get_status(refresh: bool = False, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """Handshake: ``{mode, version, capabilities, compat, last_sync, quota, …}``."""
    return service.status(refresh=refresh)


@router.get("/compat")
def get_compat(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    p = service.snap_data("probe") if service.configured() else None
    return compat.matrix(
        (p or {}).get("capabilities") if p else None,
        source=(p or {}).get("capability_source"),
        checked_at=(p or {}).get("checked_at"),
    )


@router.post("/probe")
def post_probe(_: Principal = Depends(require("operator"))) -> dict[str, Any]:
    return service.status(refresh=True)


@router.post("/sync")
def post_sync(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not service.configured():
        raise HTTPException(409, detail="MediaGuard is niet gekoppeld; MSO werkt zelfstandig")
    job = jobs.enqueue(
        "mediaguard.sync", {"force": True, "actor": p.actor}, created_by=p.actor, max_attempts=1
    )
    return {"job_id": job["id"], "status": job["status"]}


@router.get("/wanted")
def get_wanted(
    media_category: str | None = None,
    q: str | None = None,
    reason_code: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    return service.cached_wanted(
        media_category=media_category, q=q, reason_code=reason_code, page=page, page_size=page_size
    )


@router.get("/wanted/{mg_id}")
def get_wanted_item(mg_id: int, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    item = service.cache_item(mg_id)
    if item is None:
        raise HTTPException(404, detail="Item niet in de MediaGuard-cache")
    diagnosis = None
    diagnosis_error = None
    try:
        diagnosis = service.live_diagnosis(mg_id)
    except mgc.MediaGuardError as exc:
        diagnosis_error = str(exc)
    item = dict(item)
    raw = item.pop("raw", {}) or {}
    item["priority"] = raw.get("priority")
    return {
        "item": item,
        "diagnosis": diagnosis,
        "diagnosis_error": diagnosis_error,
        "search_requests": service.search_requests(20, mg_id),
    }


@router.get("/kpis")
def get_kpis(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    row = service.snap_get("kpis")
    return {"data": row["data"] if row else None, "fetched_at": row["fetched_at"] if row else None}


@router.get("/reasons")
def get_reasons(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    row = service.snap_get("reasons")
    return {"data": row["data"] if row else None, "fetched_at": row["fetched_at"] if row else None}


@router.get("/quota")
def get_quota(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return service.quota_overview()


@router.post("/wanted/{mg_id}/search")
def post_search(mg_id: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """'Vraag MediaGuard opnieuw te zoeken' via MediaGuard's officiële route (quota + idempotent)."""
    return service.request_search(mg_id, actor=p.actor, trigger="manual")


@router.get("/search-requests")
def get_search_requests(
    limit: int = Query(100, ge=1, le=1000), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return {"items": service.search_requests(limit)}
