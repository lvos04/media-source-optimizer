"""Usenet engine API (``/api/v1/usenet``). GET = viewer, operational actions = operator,
configuration/credentials/change proposals = admin."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mso import jobs
from mso import settings_registry as sr
from mso.audit import audit
from mso.auth import Principal, require
from mso.connectors import base as cb
from mso.connectors.sabnzbd import SabClient, SabError
from mso.db import get_db
from mso.domain import outcomes
from mso.domain.categories import MEDIA_CATEGORIES
from mso.features.usenet import (
    articles,
    autoheal,
    history,
    metrics,
    nzbs,
    providers,
    recommend,
    retry,
    serverstatus,
)
from mso.features.usenet.headerindex import engine as hi
from mso.security import limiter

router = APIRouter(prefix="/api/v1/usenet", tags=["usenet"])


def _sab[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except SabError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


def _limit(bucket: str, actor: str, n: int) -> None:
    ok, wait = limiter.hit(bucket, actor, n, 60)
    if not ok:
        raise HTTPException(429, detail=f"Te veel verzoeken; wacht {int(wait) + 1} s")


# ------------------------------------------------------------------ overview + SABnzbd
@router.get("/overview")
def overview(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    row = get_db().fetchone(
        "SELECT status, version, last_checked_at, last_error, url IS NOT NULL AS has_url, "
        "enabled FROM connectors WHERE id='sabnzbd'"
    )
    rows = providers.list_rows()
    sab_states = serverstatus.current()
    labels = {r["sab_server"]: r["label"] for r in rows if r["sab_server"]}
    health = {
        r["slug"]: providers.health(
            r, serverstatus.public(sab_states.get(r["sab_server"] or "")), providers.has_credentials(r["slug"])
        )
        for r in rows
    }
    return {
        "sab": {"configured": cb.is_configured("sabnzbd"), **(dict(row) if row else {})},
        "ingest": history.ingest_status(),
        "providers": {
            "total": len(rows),
            "ok": sum(1 for r in rows if health[r["slug"]]["status"] == "ok"),
            "missing_credentials": sum(1 for r in rows if not providers.has_credentials(r["slug"])),
            "problems": [
                {
                    "slug": r["slug"],
                    "label": r["label"],
                    "status": health[r["slug"]]["status"],
                    "source": health[r["slug"]]["source"],
                }
                for r in rows
                if r["enabled"] and health[r["slug"]]["status"] not in ("ok", "unknown", "missing_credentials")
            ],
        },
        "sab_servers": {
            "total": len(sab_states),
            "failing": [
                {
                    "server": st["server"],
                    "name": serverstatus.display_name(st, labels),
                    "status": st["status"],
                    "label": st["label"],
                    "explanation": st["explanation"],
                    "message": st["message"],
                    "since": st["since"],
                    "source": "sabnzbd",
                }
                for st in sab_states.values()
                if st["status"] in serverstatus.FAILING and st["enabled"]
            ],
        },
        "headerindex": {"enabled": bool(sr.get("usenet.headerindex.enabled"))},
    }


@router.get("/sab/queue")
def sab_queue(
    limit: int = Query(100, ge=1, le=500), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return _sab(lambda: history.queue_view(limit))


@router.get("/sab/history")
def sab_history(
    limit: int = Query(50, ge=1, le=500), failed_only: bool = False, _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return _sab(lambda: history.history_view(limit, failed_only))


@router.get("/sab/servers")
def sab_servers(
    period_days: int = Query(30, ge=1, le=365), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    def load() -> list[dict[str, Any]]:
        with SabClient.from_config() as c:
            return c.servers()

    servers = _sab(load)
    try:  # SABnzbd's own per-server errors (login refused, account expired, …)
        serverstatus.refresh()
    except SabError:
        pass  # keep the last persisted state; observed_at shows its age
    sab_states = serverstatus.current()
    linked = {r["sab_server"]: r for r in providers.list_rows() if r["sab_server"]}
    day0 = date.today() - timedelta(days=period_days)
    stats = {
        r["server"]: r
        for r in get_db().fetchall(
            "SELECT server, sum(bytes) AS bytes, sum(articles_tried) AS tried, sum(articles_success) AS "
            "success "
            "FROM usenet_server_stats WHERE day > %s GROUP BY server",
            (day0,),
        )
    }
    daily = {}
    for r in get_db().fetchall(
        "SELECT server, day, bytes FROM usenet_server_stats WHERE day > %s ORDER BY day", (day0,)
    ):
        daily.setdefault(r["server"], []).append({"day": r["day"], "bytes": r["bytes"]})
    totals = history.server_totals()
    heal = autoheal.rows()
    items = []
    for s in sorted(servers, key=lambda s: (s["priority"], s["name"])):
        st = stats.get(s["name"])
        tried = int(st["tried"]) if st and st["tried"] is not None else None
        success = int(st["success"]) if st and st["success"] is not None else None
        p = linked.get(s["name"])
        ratio = round(success / tried, 4) if tried and success is not None else None
        sab_pub = serverstatus.public(sab_states.get(s["name"]))
        p_health = providers.health(p, sab_pub, providers.has_credentials(p["slug"])) if p else None
        items.append(
            {
                **{k: v for k, v in s.items() if k != "host"},
                # the provider's chip shows its effective health (a stale/old MSO test never overrides SABnzbd)
                "provider": {
                    "slug": p["slug"],
                    "label": p["label"],
                    "kind": p["kind"],
                    "status": p_health["status"],
                    "source": p_health["source"],
                }
                if p and p_health
                else None,
                "sab_status": sab_pub,
                "low_article_success": bool(
                    s["enable"]
                    and tried
                    and tried >= recommend.LOW_SUCCESS_MIN_TRIED
                    and ratio is not None
                    and ratio < recommend.LOW_SUCCESS_RATIO
                ),
                "period": {
                    "bytes": int(st["bytes"]) if st else 0,
                    "articles_tried": tried,
                    "articles_success": success,
                    "success_ratio": ratio,
                },
                "daily": daily.get(s["name"], []),
                "total_bytes": totals.get(s["name"]),
                "autopilot": autoheal.public_row(heal.get(s["name"])),
            }
        )
    return {
        "items": items,
        "period_days": period_days,
        "last_stats_at": history.ingest_status()["last_stats_at"],
    }


@router.get("/sab/categories")
def sab_categories(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    def load() -> list[str]:
        with SabClient.from_config() as c:
            return c.categories()

    cats = _sab(load)
    mapping = history.category_map()
    items = []
    for c in ["*", *cats]:
        media, source = history.resolve_category(c, mapping)
        items.append(
            {
                "name": c,
                "media_category": media,
                "source": source,
                "auto": history.auto_category(c),
            }
        )
    return {"items": items, "mapping": mapping, "media_categories": list(MEDIA_CATEGORIES)}


class CategoryChoice(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    media_category: str | None = None  # None = explicitly no library category


class CategoryMapIn(BaseModel):
    items: list[CategoryChoice] = Field(max_length=200)


@router.put("/sab/category-map")
def sab_category_map(body: CategoryMapIn, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    """Store explicit choices only where they differ from the automatic name heuristic (so automatic
    mappings keep following SABnzbd)."""
    lines: list[str] = []
    for item in body.items:
        name = item.name.strip()
        if "=" in name or not name:
            raise HTTPException(422, detail=f"Ongeldige categorienaam '{item.name}'")
        if item.media_category is not None and item.media_category not in MEDIA_CATEGORIES:
            raise HTTPException(422, detail=f"Onbekende bibliotheekcategorie '{item.media_category}'")
        if item.media_category != history.auto_category(name):
            lines.append(f"{name}={item.media_category or 'none'}")
    changed = sr.put({"usenet.category_map": lines}, p.actor)
    if changed:
        audit(
            p.actor,
            "usenet.category_map",
            "Koppeling SABnzbd-categorieën → bibliotheekcategorieën gewijzigd",
            target="usenet.category_map",
            detail={"before": changed["usenet.category_map"][0], "after": lines},
        )
    return sab_categories(p)


class RefreshBody(BaseModel):
    full: bool = False


@router.post("/sab/refresh")
def sab_refresh(
    body: RefreshBody | None = None, p: Principal = Depends(require("operator"))
) -> dict[str, Any]:
    _limit("usenet-refresh", p.actor, 10)
    servers = _sab(serverstatus.refresh)
    res = _sab(lambda: history.ingest(force=bool(body and body.full)))
    stats = _sab(history.snapshot_server_stats)
    return {"history": res, "stats": stats, "servers": servers}


@router.get("/sab/server-status")
def sab_server_status(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """Last persisted SABnzbd view per server (no SABnzbd call)."""
    labels = {r["sab_server"]: r["label"] for r in providers.list_rows() if r["sab_server"]}
    return {
        "items": [
            {
                "server": st["server"],
                "name": serverstatus.display_name(st, labels),
                **(serverstatus.public(st) or {}),
            }
            for st in sorted(serverstatus.current().values(), key=lambda x: x["server"])
        ]
    }


@router.get("/failure-reasons")
def failure_reasons(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {"items": [{"key": k, "label": v} for k, v in outcomes.FAILURE_REASONS.items()]}


# ------------------------------------------------------------------ providers
class ProviderIn(BaseModel):
    slug: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["unlimited", "block"] = "unlimited"
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(563, ge=1, le=65535)
    ssl: bool = True
    connections: int = Field(10, ge=1, le=200)
    retention_days: int | None = Field(None, ge=0, le=20000)
    block_size_gb: float | None = Field(None, gt=0, le=1_000_000)
    sab_server: str | None = Field(None, max_length=200)
    enabled: bool = True
    notes: str = Field("", max_length=1000)
    username: str | None = Field(None, max_length=200)
    password: str | None = Field(None, max_length=500)


class ProviderPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=80)
    kind: Literal["unlimited", "block"] | None = None
    host: str | None = Field(None, min_length=1, max_length=253)
    port: int | None = Field(None, ge=1, le=65535)
    ssl: bool | None = None
    connections: int | None = Field(None, ge=1, le=200)
    retention_days: int | None = Field(None, ge=0, le=20000)
    block_size_gb: float | None = Field(None, gt=0, le=1_000_000)
    sab_server: str | None = Field(None, max_length=200)
    enabled: bool | None = None
    notes: str | None = Field(None, max_length=1000)
    username: str | None = Field(None, max_length=200)  # "" clears, omitted = unchanged
    password: str | None = Field(None, max_length=500)


def _usage() -> dict[str, int]:
    return history.server_totals()


@router.get("/providers")
def providers_list(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    usage = _usage()
    sab_states = serverstatus.current()
    return {
        "items": [
            providers.public(r, block_usage=usage, sab_states=sab_states) for r in providers.list_rows()
        ]
    }


@router.get("/providers/presets")
def providers_presets(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {"items": providers.PRESETS}


@router.post("/providers")
def providers_create(body: ProviderIn, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    data = body.model_dump(exclude={"username", "password"})
    row = providers.create(data, username=body.username, password=body.password, actor=p.actor)
    return providers.public(row, block_usage=_usage())


@router.put("/providers/{slug}")
def providers_update(
    slug: str, body: ProviderPatch, p: Principal = Depends(require("admin"))
) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True, exclude={"username", "password"})
    sent = body.model_dump(exclude_unset=True)
    row = providers.update(
        slug, data, username=sent.get("username"), password=sent.get("password"), actor=p.actor
    )
    return providers.public(row, block_usage=_usage())


@router.delete("/providers/{slug}")
def providers_delete(slug: str, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    providers.delete(slug, actor=p.actor)
    return {"deleted": slug}


@router.post("/providers/{slug}/test")
def providers_test(slug: str, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    _limit("usenet-provider-test", p.actor, 20)
    res = providers.check(slug, actor=p.actor)
    res["provider"]["block_used_bytes"] = providers.public(providers.get(slug), block_usage=_usage())[
        "block_used_bytes"
    ]
    return res


@router.post("/providers/import-sab")
def providers_import(p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    created = _sab(lambda: providers.import_from_sab(p.actor))
    sab_states = serverstatus.current()
    return {
        "items": [providers.public(r, sab_states=sab_states) for r in created],
        "note": "SABnzbd geeft geen wachtwoorden door: vul per provider gebruikersnaam en wachtwoord in om "
        "MSO zelf te laten testen. Tot die tijd toont MSO de status die SABnzbd zelf meldt."
        if created
        else None,
    }


# ------------------------------------------------------------------ availability / contribution
@router.get("/contribution")
def contribution(
    period_days: int = Query(30, ge=1, le=365), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return metrics.overview(period_days)


@router.get("/nzbs")
def nzbs_list(
    limit: int = Query(100, ge=1, le=500), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return {"items": nzbs.list_nzbs(limit)}


class NzbUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    content_b64: str = Field(min_length=4, max_length=60 * 1024 * 1024)
    nzo_id: str | None = Field(None, max_length=200)


@router.post("/nzbs")
def nzbs_upload(body: NzbUpload, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    _limit("usenet-nzb-upload", p.actor, 20)
    row = nzbs.upload(body.filename, body.content_b64, actor=p.actor, nzo_id=body.nzo_id)
    audit(
        p.actor,
        "usenet.nzb_uploaded",
        f"NZB geüpload voor artikeltest ({row['segment_total']} artikelen)",
        target=str(row["id"]),
    )
    return row


class ArticleTestIn(BaseModel):
    nzb_id: int
    providers: list[str] | None = None
    sample_size: int | None = Field(None, ge=1, le=1000)
    confirm: bool = False


@router.post("/article-tests")
def article_test_create(body: ArticleTestIn, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(
            422, detail="Bevestig de test: MSO maakt hiervoor NNTP-verbindingen met je providers"
        )
    _limit("usenet-article-test", p.actor, 10)
    return articles.create_test(
        body.nzb_id,
        provider_slugs=body.providers,
        sample_size=body.sample_size,
        trigger="manual",
        actor=p.actor,
    )


@router.get("/article-tests")
def article_tests(
    limit: int = Query(50, ge=1, le=200), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return {"items": articles.list_tests(limit)}


@router.get("/article-tests/{test_id}")
def article_test_get(test_id: int, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return articles.get_test(test_id)


# ------------------------------------------------------------------ recommendations → change set
@router.get("/recommendations")
def recommendations(
    period_days: int = Query(30, ge=1, le=365), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return _sab(lambda: recommend.suggestions(period_days))


class ServerOp(BaseModel):
    path: str = Field(max_length=300)
    after: int


class PriorityProposal(BaseModel):
    ops: list[ServerOp] | None = None  # omitted = use MSO's suggestion
    period_days: int = Field(30, ge=1, le=365)


@router.post("/recommendations/priority")
def propose_priority(
    body: PriorityProposal | None = None, p: Principal = Depends(require("admin"))
) -> dict[str, Any]:
    body = body or PriorityProposal()
    ops = [o.model_dump() for o in body.ops] if body.ops is not None else None
    return _sab(lambda: recommend.propose(p.actor, ops, body.period_days))


@router.post("/recommendations/disable-failing")
def propose_disable_failing(p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    """One change set (toestemming_nodig) that switches off every failing, enabled SABnzbd server."""
    return _sab(lambda: recommend.propose_disable(p.actor))


# ------------------------------------------------------------------ Autopilot: servers off/on, retries
@router.get("/autopilot")
def autopilot_overview(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """Automatic server off/on state per SABnzbd server + retried failed downloads (no SABnzbd call)."""
    return {**autoheal.overview(), "retry": retry.overview()}


@router.post("/autopilot/run")
def autopilot_run(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """Run the Autopilot pass now (refresh SABnzbd's server status first). Writes only in Autopilot; otherwise it
    watches and (re)creates the retry proposal."""
    _limit("usenet-autopilot", p.actor, 6)
    _sab(serverstatus.refresh)
    return _sab(autoheal.run)


@router.post("/sab/servers/{server}/retry")
def sab_server_retry(server: str, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """'Opnieuw proberen': switch a (failing/switched-off) SABnzbd server on again and watch it. Requires
    beheermodus; reversible (rollback switches it off again); in Autopilot it goes off again when the error
    returns."""
    _limit("usenet-server-retry", p.actor, 10)
    return _sab(lambda: autoheal.retry_server(server, p.actor))


@router.post("/retries/propose")
def retries_propose(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """(Re)create the bundle "N mislukte downloads opnieuw proberen" now (applied right away in Autopilot)."""
    _limit("usenet-retry-propose", p.actor, 10)
    _sab(serverstatus.refresh)
    res = _sab(lambda: retry.propose(actor=p.actor))
    return {k: v for k, v in res.items() if k != "change"} | (
        {"change_id": res["change"]["id"], "change_status": res["change"]["status"]} if res.get("change") else {}
    )


@router.post("/retries/resume")
def retries_resume(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """'Hervatten': end the pause of automatic retrying (the effectiveness brake). Writes nothing to SABnzbd; the next
    pass judges new attempts from now on."""
    if not retry.brake_state().get("paused"):
        raise HTTPException(409, detail="Automatisch opnieuw proberen staat niet op pauze")
    retry.resume(p.actor, trigger="user")
    return retry.overview()


# ------------------------------------------------------------------ 1.6.0: diagnosis from SABnzbd's processing log
@router.get("/sab/reclassify")
def sab_reclassify_status(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """Result of the last reclassification of the SABnzbd history (once after the upgrade, or on request)."""
    return {"last": history.reclassify_status()}


@router.post("/sab/reclassify")
def sab_reclassify(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """Classify every failed SABnzbd download again from SABnzbd's history and archive (stage-log facts). Changes only
    MSO's own labels; audited as ``usenet.reclassified``."""
    _limit("usenet-reclassify", p.actor, 3)
    return {"last": _sab(lambda: history.reclassify(p.actor, trigger="user"))}


@router.get("/stat-budget")
def stat_budget(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """Automatic STAT sample after sending: on/off, per-provider usage today/this month and the budgets."""
    return articles.budget_view()


# ------------------------------------------------------------------ header index
@router.get("/headerindex")
def headerindex_status(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return hi.status()


class HeaderIndexConfig(BaseModel):
    enabled: bool | None = None
    acknowledge_limits: bool = False
    groups: list[str] | None = Field(None, max_length=10)
    provider: str | None = Field(None, max_length=40)
    retention_days: int | None = None
    max_storage_mb: int | None = None
    max_headers_per_run: int | None = None
    max_runs_per_day: int | None = None
    max_download_mb_per_run: int | None = None
    max_run_seconds: int | None = None


@router.put("/headerindex/config")
def headerindex_config(body: HeaderIndexConfig, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True, exclude={"acknowledge_limits"})
    if "groups" in data and data["groups"] is not None:
        data["groups"] = hi.validate_groups(data["groups"])
    if data.get("provider"):
        providers.get(data["provider"])
    if data.get("enabled"):
        if not body.acknowledge_limits:
            raise HTTPException(
                422,
                detail="Bevestig dat de header-index geen indexer of provider vervangt en "
                "begrensd is tot de ingestelde groepen en budgetten",
            )
        groups = data.get("groups", sr.get("usenet.headerindex.groups"))
        prov = data.get("provider", sr.get("usenet.headerindex.provider"))
        if not groups or not prov:
            raise HTTPException(422, detail="Kies eerst een provider en minstens één toegestane groep")
    values = {f"usenet.headerindex.{k}": v for k, v in data.items() if v is not None}
    changed = sr.put(values, p.actor)
    if changed:
        audit(
            p.actor,
            "usenet.headerindex_config",
            "Header-index-instellingen gewijzigd",
            target="headerindex",
            detail={k: {"before": o, "after": n} for k, (o, n) in changed.items()},
        )
    return hi.status()


@router.post("/headerindex/run")
def headerindex_run(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not sr.get("usenet.headerindex.enabled"):
        raise HTTPException(409, detail="Header-index staat uit")
    _limit("usenet-hi-run", p.actor, 5)
    job = jobs.enqueue(
        "usenet.headerindex_run",
        {"actor": p.actor},
        max_attempts=1,
        created_by=p.actor,
        idempotency_key=f"usenet-hi-run:{get_db().scalar('SELECT count(*) FROM usenet_hi_runs')}",
    )
    return {"job_id": job["id"], "status": job["status"]}


class PurgeBody(BaseModel):
    scope: Literal["expired", "all"] = "expired"


@router.post("/headerindex/purge")
def headerindex_purge(body: PurgeBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    return hi.purge(body.scope, actor=p.actor)


@router.get("/headerindex/search")
def headerindex_search(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    return {
        "items": [r.as_dict() for r in hi.search(q, limit=limit)],
        "note": "Lage betrouwbaarheid: alleen onderwerpregels uit de eigen header-index. De NZB wordt "
        "opgebouwd uit de geïndexeerde berichtkoppen en kan onvolledig zijn.",
    }
