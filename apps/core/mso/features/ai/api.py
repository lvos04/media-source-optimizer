"""AI-adviseur API."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mso import settings_registry as sr
from mso.audit import audit
from mso.auth import Principal, require
from mso.db import get_db
from mso.features.ai import budget, service
from mso.security import limiter
from mso.vault import get_vault

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.get("/status")
def get_status(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return service.status()


class ConfigBody(BaseModel):
    enabled: bool | None = None
    monthly_budget_eur: float | None = Field(None, ge=0, le=500)
    model: str | None = Field(None, min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._:\-/@]+$")
    effort: Literal["low", "medium", "high", "none"] | None = None
    max_output_tokens: int | None = Field(None, ge=200, le=16000)


@router.put("/config")
def put_config(body: ConfigBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    values = {f"ai.{k}": v for k, v in body.model_dump().items() if v is not None}
    changed = sr.put(values, p.actor)
    if changed:
        audit(
            p.actor,
            "ai.config",
            "AI-adviseur instellingen gewijzigd",
            target="ai",
            detail={k: {"before": o, "after": n} for k, (o, n) in changed.items()},
        )
    return service.status()


class KeyBody(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)


@router.put("/key")
def put_key(body: KeyBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    get_vault().set(service.KEY_NAME, body.api_key.strip())
    audit(p.actor, "ai.key_set", "AI API-sleutel ingesteld", target="ai")
    return {"key": get_vault().status(service.KEY_NAME)}


@router.delete("/key")
def delete_key(p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    get_vault().set(service.KEY_NAME, None)
    audit(p.actor, "ai.key_deleted", "AI API-sleutel verwijderd", target="ai")
    return {"key": get_vault().status(service.KEY_NAME)}


@router.get("/prices")
def get_prices(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {
        "items": budget.prices(),
        "unit": "cent per miljoen tokens",
        "note": "Standaardprijzen zijn de lijstprijzen van de aanbieder (USD); pas ze aan naar wat je "
        "werkelijk betaalt in EUR.",
    }


class PriceBody(BaseModel):
    input_cents_per_mtok: int = Field(ge=0, le=1_000_000)
    output_cents_per_mtok: int = Field(ge=0, le=1_000_000)


@router.put("/prices/{model}")
def put_price(model: str, body: PriceBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    if not (3 <= len(model) <= 100):
        raise HTTPException(422, detail="Ongeldige modelnaam")
    budget.set_price(model, body.input_cents_per_mtok, body.output_cents_per_mtok, p.actor)
    audit(p.actor, "ai.price_set", f"AI-prijs ingesteld voor {model}", target="ai", detail=body.model_dump())
    return {"items": budget.prices()}


@router.get("/usage")
def usage(limit: int = Query(50, ge=1, le=500), _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    rows = get_db().fetchall(
        "SELECT id, ts, model, purpose, status, input_tokens, output_tokens, cost_microcents, "
        "error FROM ai_usage ORDER BY id DESC LIMIT %s",
        (limit,),
    )
    for r in rows:
        r["cost_cents"] = round(r.pop("cost_microcents") / budget.MICRO, 4)
    return {"items": rows, "month": budget.month_usage()}


@router.post("/test")
def test(p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    ok, wait = limiter.hit("ai.test", p.actor, 5, 60)
    if not ok:
        raise HTTPException(429, detail=f"Te veel tests; wacht {int(wait) + 1} s")
    res = service.test_call(p.actor)
    audit(
        p.actor,
        "ai.test",
        "AI-verbinding getest: " + ("gelukt" if res["ok"] else "mislukt"),
        target="ai",
        detail={"ok": res["ok"]},
    )
    return res


class AdviseBody(BaseModel):
    subject: Literal["overview", "download", "source"] = "overview"
    ref: str | None = Field(None, max_length=200)
    use_ai: bool = True


@router.post("/advise")
def advise(body: AdviseBody, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    ok, wait = limiter.hit("ai.advise", p.actor, 10, 60)
    if not ok:
        raise HTTPException(429, detail=f"Te veel adviesverzoeken; wacht {int(wait) + 1} s")
    return service.advise(body.subject, body.ref, p.actor, use_ai=body.use_ai)
