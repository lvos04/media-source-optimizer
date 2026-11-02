"""Kosten API: services CRUD (admin), analysis/simulation/scenarios (viewer), renewal reminders job."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mso import jobs
from mso import settings_registry as sr
from mso.audit import audit
from mso.auth import Principal, require
from mso.db import get_db
from mso.domain.outcomes import recommend
from mso.features.costs import analysis

router = APIRouter(prefix="/api/v1/costs", tags=["kosten"])
KINDS = ("usenet_provider", "block_account", "nzb_indexer", "torrent_indexer", "tracker", "vpn", "other")
KIND_LABELS = {
    "usenet_provider": "Usenet-provider",
    "block_account": "Blockaccount",
    "nzb_indexer": "NZB-indexer",
    "torrent_indexer": "Torrent-indexer",
    "tracker": "Tracker",
    "vpn": "VPN",
    "other": "Overig",
}


class ServiceBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    kind: Literal[
        "usenet_provider", "block_account", "nzb_indexer", "torrent_indexer", "tracker", "vpn", "other"
    ]
    billing: Literal["monthly", "yearly", "block", "free"] = "monthly"
    amount_cents: int = Field(0, ge=0, le=100_000_00)
    currency: str = Field("EUR", min_length=3, max_length=3)
    block_size_gb: float | None = Field(None, gt=0, le=1_000_000)
    block_used_gb: float | None = Field(None, ge=0, le=1_000_000)
    renewal_date: date | None = None
    notes: str = Field("", max_length=2000)
    active: bool = True
    source_id: str | None = Field(None, max_length=200)
    backbone: str | None = Field(None, max_length=120)


def _period(period_days: int | None) -> int:
    if period_days is None:
        return int(sr.get("costs.default_period_days"))
    return period_days


def _validate(body: ServiceBody) -> dict[str, Any]:
    data = body.model_dump()
    data["currency"] = data["currency"].upper()
    data["backbone"] = (data["backbone"] or "").strip() or None
    data["source_id"] = (data["source_id"] or "").strip() or None
    if data["source_id"] and not get_db().scalar(
        "SELECT count(*) FROM sources WHERE id=%s", (data["source_id"],)
    ):
        raise HTTPException(422, detail=f"Onbekende bron '{data['source_id']}'")
    if data["billing"] == "block" and not data["block_size_gb"]:
        raise HTTPException(422, detail="Een blockaccount heeft een blokgrootte (GB) nodig")
    if (
        data["block_used_gb"] is not None
        and data["block_size_gb"] is not None
        and data["block_used_gb"] > data["block_size_gb"]
    ):
        raise HTTPException(422, detail="Verbruikt kan niet groter zijn dan de blokgrootte")
    return data


def _get(service_id: int) -> dict[str, Any]:
    s = analysis.get_service(service_id)
    if s is None:
        raise HTTPException(404, detail="Dienst niet gevonden")
    return s


COLS = (
    "label",
    "kind",
    "billing",
    "amount_cents",
    "currency",
    "block_size_gb",
    "block_used_gb",
    "renewal_date",
    "notes",
    "active",
    "source_id",
    "backbone",
)


@router.get("/services")
def list_services(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    rows = get_db().fetchall(
        "SELECT s.*, src.label AS source_label FROM services s "
        "LEFT JOIN sources src ON src.id=s.source_id ORDER BY s.active DESC, s.label"
    )
    items = []
    for r in rows:
        item = analysis.serialise_service(r)
        item["monthly"] = analysis.monthly_cost(r)
        item["kind_label"] = KIND_LABELS.get(r["kind"], r["kind"])
        items.append(item)
    return {"items": items}


@router.get("/sources")
def linkable_sources(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    rows = get_db().fetchall("SELECT id, kind, label, origin, enabled FROM sources ORDER BY kind, label")
    return {"items": rows}


@router.post("/services")
def create_service(body: ServiceBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    data = _validate(body)
    row = get_db().fetchone(
        f"INSERT INTO services({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))}) RETURNING id",
        [data[c] for c in COLS],
    )
    audit(
        p.actor,
        "costs.service_created",
        f"Dienst toegevoegd: {data['label']}",
        target=f"service:{row['id']}",
        detail={k: data[k] for k in ("kind", "billing", "amount_cents", "currency")},
    )
    return analysis.serialise_service(_get(row["id"]))


@router.get("/services/{service_id}")
def get_service(service_id: int, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    s = _get(service_id)
    return analysis.serialise_service(s) | {"monthly": analysis.monthly_cost(s)}


@router.put("/services/{service_id}")
def update_service(
    service_id: int, body: ServiceBody, p: Principal = Depends(require("admin"))
) -> dict[str, Any]:
    before = _get(service_id)
    data = _validate(body)
    get_db().execute(
        f"UPDATE services SET {', '.join(c + '=%s' for c in COLS)}, updated_at=now() WHERE id=%s",
        [data[c] for c in COLS] + [service_id],
    )
    changed = {
        c: {"voor": str(before.get(c)), "na": str(data[c])}
        for c in COLS
        if str(before.get(c)) != str(data[c])
    }
    audit(
        p.actor,
        "costs.service_updated",
        f"Dienst gewijzigd: {data['label']}",
        target=f"service:{service_id}",
        detail={"changes": changed},
    )
    return analysis.serialise_service(_get(service_id))


@router.delete("/services/{service_id}")
def delete_service(service_id: int, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    s = _get(service_id)
    get_db().execute("DELETE FROM services WHERE id=%s", (service_id,))
    audit(
        p.actor,
        "costs.service_deleted",
        f"Dienst verwijderd uit kostenoverzicht: {s['label']} (het abonnement zelf is niet opgezegd)",
        target=f"service:{service_id}",
    )
    return {"ok": True}


@router.get("/services/{service_id}/analysis")
def service_analysis(
    service_id: int,
    period_days: int | None = Query(None, ge=7, le=365),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    pd = _period(period_days)
    return analysis.analyse(_get(service_id), pd) | {"period": analysis.period_bounds(pd)}


@router.get("/services/{service_id}/simulate")
def service_simulate(
    service_id: int,
    period_days: int | None = Query(None, ge=7, le=365),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    pd = _period(period_days)
    s = _get(service_id)
    sim = analysis.simulate_disable(s, pd)
    cost = analysis.monthly_cost(s)
    sim["entered_cost"] = {
        "monthly_cents": cost["monthly_cents"],
        "currency": s["currency"],
        "note": "Ingevoerd bedrag; wat je werkelijk betaalt hangt af van je contract.",
    }
    sim["period"] = analysis.period_bounds(pd)
    return sim


class ScenarioIn(BaseModel):
    label: str = Field("Scenario", max_length=120)
    service_ids: list[int] = Field(default_factory=list, max_length=20)


class ScenariosBody(BaseModel):
    scenarios: list[ScenarioIn] = Field(min_length=1, max_length=10)
    period_days: int | None = Field(None, ge=7, le=365)


@router.get("/scenarios")
def scenarios_default(
    period_days: int | None = Query(None, ge=7, le=365), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return analysis.scenarios(_period(period_days))


@router.post("/scenarios")
def scenarios_custom(body: ScenariosBody, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return analysis.scenarios(_period(body.period_days), [s.model_dump() for s in body.scenarios])


@router.get("/summary")
def summary(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    days = int(sr.get("costs.renewal_reminder_days"))
    return {
        "total": analysis.monthly_total(),
        "renewals": analysis.upcoming_renewals(max(days, 60)),
        "reminder_days": days,
        "disclaimers": analysis.DISCLAIMERS,
    }


# ------------------------------------------------------------------ renewal reminders
def create_renewal_reminders() -> int:
    days = int(sr.get("costs.renewal_reminder_days"))
    n = 0
    for r in analysis.upcoming_renewals(days):
        if r["days_left"] < 0:
            continue
        svc = analysis.get_service(r["id"])
        sim = analysis.simulate_disable(svc, int(sr.get("costs.default_period_days"))) if svc else {}
        rng = (sim or {}).get("affected_range") or {}
        evidence_line = ""
        if sim.get("available"):
            evidence_line = (
                f" In de afgelopen periode had deze dienst bij {rng.get('low')}–{rng.get('high')} van "
                f"{sim.get('observed_successes')} geslaagde downloads geen waargenomen alternatief "
                f"(betrouwbaarheid {sim.get('confidence')})."
            )
        amount = f"{r['amount_cents'] / 100:.2f} {r['currency']}"
        recommend(
            "costs",
            f"Verlenging {r['label']} over {r['days_left']} dag(en)",
            f"{r['label']} verlengt op {r['date']} ({amount}, {r['billing']}).{evidence_line} Bekijk de "
            "uitschakel-simulatie op Kosten & besparing voordat je beslist. De app zegt nooit zelf iets op.",
            risk="advies",
            subject=f"service:{r['id']}",
            impact="Alleen informatief",
            dedupe_key=f"costs.renewal:{r['id']}:{r['date']}",
            evidence={
                "service_id": r["id"],
                "renewal_date": r["date"],
                "simulation": {
                    "affected_range": rng,
                    "confidence": sim.get("confidence"),
                    "coverage": (sim.get("coverage") or {}).get("ratio"),
                },
            },
        )
        n += 1
    return n


@jobs.handler("costs.renewal_reminders")
def _job_renewals(ctx: jobs.JobContext) -> dict[str, Any]:
    return {"reminders": create_renewal_reminders()}


jobs.every("costs.renewal_reminders", 6 * 3600)
