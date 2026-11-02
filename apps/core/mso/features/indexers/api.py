"""Indexers API: overview of all search sources (Prowlarr + direct + Spotweb + backends), CRUD and tests for
direct indexers, Prowlarr sync/view/registration preview."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from mso.audit import audit
from mso.auth import Principal, require
from mso.connectors import base as cb
from mso.connectors.prowlarr import ProwlarrError
from mso.db import get_db
from mso.features.indexers import prowlarr_sync, prowlarr_view, registry, reliability
from mso.features.search import settings as S  # noqa: F401  (defines settings)
from mso.security import UnsafeTarget, limiter

router = APIRouter(prefix="/api/v1/indexers", tags=["indexers"])
SOURCE_KINDS = ("nzb_indexer", "torrent_indexer", "spotweb", "header_index")


def _prowlarr_http(exc: ProwlarrError) -> HTTPException:
    status = {"auth": 502, "timeout": 504, "not_configured": 409}.get(exc.code, 502)
    return HTTPException(status, detail=exc.message)


def _stats(days: int = 7) -> dict[str, dict[str, Any]]:
    rows = get_db().fetchall(
        "SELECT subject, "
        " count(*) FILTER (WHERE metric='search.ok' AND value=1) AS ok, "
        " count(*) FILTER (WHERE metric='search.empty' AND value=1) AS empty, "
        " count(*) FILTER (WHERE metric='search.error' AND value=1) AS errors, "
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY value) FILTER (WHERE metric='search.latency_ms') "
        "AS p50, "
        " max(ts) FILTER (WHERE metric IN ('search.ok','search.error','search.empty')) AS last_search, "
        " (array_agg(labels->>'status' ORDER BY ts DESC) FILTER (WHERE metric='search.error'))[1] AS "
        "last_status "
        "FROM observations WHERE ts > now() - make_interval(days => %s) "
        "AND metric IN ('search.ok','search.empty','search.error','search.latency_ms') GROUP BY subject",
        (days,),
    )
    return {
        r["subject"]: {
            "ok": r["ok"],
            "empty": r["empty"],
            "errors": r["errors"],
            "median_latency_ms": round(r["p50"]) if r["p50"] is not None else None,
            "last_search_at": r["last_search"],
            "last_status": r["last_status"],
        }
        for r in rows
    }


@router.get("")
def list_sources(
    protocol: Literal["usenet", "torrent"] | None = None, _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    rows = get_db().fetchall(
        "SELECT * FROM sources WHERE kind = ANY(%s) ORDER BY origin, label", (list(SOURCE_KINDS),)
    )
    direct = {registry.source_id(r): r for r in registry.list_rows()}
    stats = _stats()
    items = []
    for s in rows:
        meta = s["meta"] or {}
        proto = "torrent" if s["kind"] == "torrent_indexer" else "usenet"
        if protocol and proto != protocol:
            continue
        if meta.get("removed"):
            continue
        d = {
            "id": s["id"],
            "kind": s["kind"],
            "label": s["label"],
            "origin": s["origin"],
            "protocol": proto,
            "scopes": list(s["scopes"] or []),
            "private": s["private"],
            "enabled": s["enabled"],
            "reliability": s["reliability"],
            "reliability_detail": meta.get("reliability"),
            "untagged": bool(meta.get("untagged")),
            "is_mso": bool(meta.get("is_mso")),
            "stats": stats.get(s["id"]),
            "updated_at": s["updated_at"],
        }
        if s["origin"] == "prowlarr":
            d["prowlarr"] = {
                k: meta.get(k)
                for k in (
                    "prowlarr_id",
                    "implementation",
                    "definition",
                    "privacy",
                    "tags",
                    "disabled_till",
                    "most_recent_failure",
                    "avg_response_ms",
                    "queries",
                    "grabs",
                    "failed_queries",
                    "anime_name",
                )
            }
        if s["id"] in direct:
            d["direct"] = registry.public_view(direct[s["id"]])
        items.append(d)
    prowlarr = get_db().fetchone(
        "SELECT id, url, enabled, status, version, capabilities, last_checked_at, last_error "
        "FROM connectors WHERE id='prowlarr'"
    )
    return {"items": items, "prowlarr": prowlarr, "prowlarr_configured": cb.is_configured("prowlarr")}


# ------------------------------------------------------------------ direct indexers
class DirectBody(BaseModel):
    kind: Literal["newznab", "torznab", "spotweb"]
    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=500)
    api_path: str = Field("/api", max_length=200, pattern=r"^/[A-Za-z0-9/_.\-]*(\?[A-Za-z0-9=&_\-]*)?$")
    slug: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    api_key: str | None = Field(None, max_length=200)
    enabled: bool = True
    scopes: list[Literal["normal", "anime"]] = Field(
        default_factory=lambda: ["normal", "anime"], min_length=1
    )
    private: bool = False
    rate_per_min: int | None = Field(None, ge=1, le=600)
    daily_quota: int = Field(0, ge=0, le=1_000_000)
    timeout_s: float = Field(15, ge=2, le=120)

    @field_validator("scopes")
    @classmethod
    def _uniq(cls, v: list[str]) -> list[str]:
        return sorted(set(v), key=["normal", "anime"].index)


class DirectPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=80)
    url: str | None = Field(None, min_length=8, max_length=500)
    api_path: str | None = Field(None, max_length=200, pattern=r"^/[A-Za-z0-9/_.\-]*(\?[A-Za-z0-9=&_\-]*)?$")
    api_key: str | None = Field(None, max_length=200)  # "" clears
    enabled: bool | None = None
    scopes: list[Literal["normal", "anime"]] | None = Field(None, min_length=1)
    private: bool | None = None
    rate_per_min: int | None = Field(None, ge=1, le=600)
    daily_quota: int | None = Field(None, ge=0, le=1_000_000)
    timeout_s: float | None = Field(None, ge=2, le=120)


def _check_url(url: str) -> str:
    try:
        return registry.validate_url(url)
    except UnsafeTarget as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/direct")
def direct_list(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {"items": [registry.public_view(r) for r in registry.list_rows()]}


@router.post("/direct")
def direct_create(body: DirectBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    url = _check_url(body.url)
    slug = body.slug or registry.slugify(body.label)
    if registry.get_row(slug) is not None:
        if body.slug:
            raise HTTPException(409, detail=f"Indexer '{slug}' bestaat al")
        base, n = slug[:36], 2
        while registry.get_row(f"{base}-{n}") is not None:
            n += 1
        slug = f"{base}-{n}"
    values = body.model_dump(exclude={"api_key", "slug"})
    values["url"] = url
    values["rate_per_min"] = body.rate_per_min or int(S.get("indexers.default_rate_per_min", 30))
    row = registry.save(values, slug=slug, api_key=body.api_key or None)
    audit(
        p.actor,
        "indexer.created",
        f"Indexer '{body.label}' toegevoegd ({body.kind})",
        target=registry.source_id(row),
        detail={"url": url, "kind": body.kind, "scopes": body.scopes, "api_key_set": bool(body.api_key)},
    )
    return registry.public_view(row)


@router.patch("/direct/{slug}")
def direct_patch(slug: str, body: DirectPatch, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    if registry.get_row(slug) is None:
        raise HTTPException(404, detail="Indexer niet gevonden")
    data = body.model_dump(exclude_unset=True)
    key = data.pop("api_key", None)
    if data.get("url"):
        data["url"] = _check_url(data["url"])
    if "scopes" in data and data["scopes"]:
        data["scopes"] = sorted(set(data["scopes"]), key=["normal", "anime"].index)
    row = registry.save(data, slug=slug, api_key=key or None, clear_key=key == "")
    audit(
        p.actor,
        "indexer.updated",
        f"Indexer '{row['label']}' gewijzigd",
        target=registry.source_id(row),
        detail={**{k: v for k, v in data.items()}, "api_key_changed": key is not None},
    )
    return registry.public_view(row)


@router.delete("/direct/{slug}")
def direct_delete(slug: str, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    row = registry.get_row(slug)
    if row is None or not registry.delete(slug):
        raise HTTPException(404, detail="Indexer niet gevonden")
    audit(
        p.actor,
        "indexer.deleted",
        f"Indexer '{row['label']}' verwijderd (meetgeschiedenis blijft bewaard)",
        target=registry.source_id(row),
    )
    return {"ok": True}


def _rate(p: Principal) -> None:
    ok, _ = limiter.hit("indexer-test", p.actor, 20, 60)
    if not ok:
        raise HTTPException(429, detail="Te veel testen; wacht een minuut")


@router.post("/direct/{slug}/test")
def direct_test(slug: str, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    _rate(p)
    row = registry.get_row(slug)
    if row is None:
        raise HTTPException(404, detail="Indexer niet gevonden")
    res = registry.test(row)
    caps = res.pop("caps", None)
    db = get_db()
    if caps:
        from mso.db import jsonb

        db.execute(
            "UPDATE direct_indexers SET caps=%s, caps_checked_at=now() WHERE slug=%s", (jsonb(caps), slug)
        )
    if res["ok"]:
        registry.record_success(row)
    else:
        db.execute(
            "UPDATE direct_indexers SET status=%s, last_error=%s WHERE slug=%s",
            (res["status"], res["message"][:500], slug),
        )
    audit(
        p.actor,
        "indexer.tested",
        f"Test indexer '{row['label']}': {res['status']}",
        target=registry.source_id(row),
        detail={"ok": res["ok"], "message": res["message"]},
    )
    return res


class AdhocTest(BaseModel):
    kind: Literal["newznab", "torznab", "spotweb"]
    url: str = Field(min_length=8, max_length=500)
    api_path: str = Field("/api", max_length=200, pattern=r"^/[A-Za-z0-9/_.\-]*(\?[A-Za-z0-9=&_\-]*)?$")
    api_key: str | None = Field(None, max_length=200)
    slug: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")  # use the stored key of this indexer


@router.post("/direct/test")
def direct_test_adhoc(body: AdhocTest, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    """Test unsaved settings (the 'Test' button in the add/edit dialog)."""
    _rate(p)
    url = _check_url(body.url)
    key = body.api_key
    if not key and body.slug:
        from mso.vault import get_vault

        key = get_vault().get(registry.vault_name(body.slug))
    row = {
        "slug": body.slug or "test",
        "kind": body.kind,
        "url": url,
        "api_path": body.api_path,
        "timeout_s": 15,
        "private": False,
        "label": url,
    }
    res = registry.test(row, api_key=key or "")
    res.pop("caps", None)
    return res


# ------------------------------------------------------------------ prowlarr
@router.post("/prowlarr/sync")
def prowlarr_sync_now(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    try:
        res = prowlarr_sync.sync_sources()
        res["conflicts"] = len(prowlarr_view.refresh_recommendations())
    except ProwlarrError as exc:
        raise _prowlarr_http(exc) from exc
    audit(
        p.actor,
        "prowlarr.synced",
        f"Prowlarr-indexers gesynchroniseerd ({res['indexers']})",
        target="prowlarr",
    )
    return res


@router.get("/prowlarr/view")
def prowlarr_view_api(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    try:
        return prowlarr_view.build_view()
    except ProwlarrError as exc:
        raise _prowlarr_http(exc) from exc


@router.get("/prowlarr/health")
def prowlarr_health(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    from mso.connectors.prowlarr import ProwlarrClient

    try:
        c = ProwlarrClient.from_config()
        return {
            "health": c.health(),
            "indexer_status": c.indexer_status(),
            "history": (c.history(page_size=20) or {}).get("records", []),
        }
    except ProwlarrError as exc:
        raise _prowlarr_http(exc) from exc


class RegisterBody(BaseModel):
    kinds: list[Literal["newznab", "torznab"]] = Field(
        default_factory=lambda: ["newznab", "torznab"], min_length=1
    )


@router.post("/prowlarr/register")
def prowlarr_register(
    body: RegisterBody | None = None, p: Principal = Depends(require("admin"))
) -> dict[str, Any]:
    """Create (or return the open) change preview to register MSO's endpoints in Prowlarr. Never applies."""
    try:
        res = prowlarr_sync.propose_registration(p.actor, (body or RegisterBody()).kinds)
    except ProwlarrError as exc:
        raise _prowlarr_http(exc) from exc
    return res


@router.get("/recommendations")
def recommendations(
    status: str = Query("open", pattern=r"^(open|accepted|dismissed|resolved|all)$"),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    sql = "SELECT * FROM recommendations WHERE area='indexers'"
    params: list[Any] = []
    if status != "all":
        sql += " AND status=%s"
        params.append(status)
    return {"items": get_db().fetchall(sql + " ORDER BY updated_at DESC LIMIT 200", params)}


@router.post("/reliability/recompute")
def reliability_now(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    res = reliability.compute_all()
    audit(p.actor, "indexers.reliability", f"Betrouwbaarheid herberekend ({res['updated']} bronnen)")
    return res
