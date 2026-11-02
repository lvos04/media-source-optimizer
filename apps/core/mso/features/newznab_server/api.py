"""MSO's own Newznab (``/newznab/api``) and Torznab (``/torznab/api``) endpoints + consumer-key management.

Prowlarr (or any Newznab client) can register these as indexers. Backends used to answer: those listed in
``newznab.backends`` (direct indexers, Spotweb, header index; Prowlarr only when explicitly enabled AND the
caller is not that Prowlarr — see ``mso.features.search.origin``). Results are referenced by opaque tokens;
``t=get`` proxies the NZB/.torrent through MSO (SSRF guard, size cap) so upstream API keys never leave MSO.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from mso.audit import audit
from mso.auth import Principal, require
from mso.db import get_db
from mso.domain import categories as C
from mso.features.indexers import registry
from mso.features.newznab_server import keys, xmlout
from mso.features.search import origin, service
from mso.features.search import settings as S
from mso.features.search.models import SearchQuery
from mso.features.search.urls import public_link, redact_magnet
from mso.security import UnsafeTarget, limiter, redact

LOGGER = logging.getLogger(__name__)
XML = "application/xml; charset=utf-8"
INT_RE = re.compile(r"^\d{1,9}$")
SEARCH_FUNCS = {
    "search": "search",
    "tvsearch": "tvsearch",
    "tv-search": "tvsearch",
    "movie": "movie",
    "movie-search": "movie",
}


def _xml(body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(content=body, media_type=XML, status_code=status, headers=headers)


def _err(
    code: int, detail: str | None = None, status: int = 200, headers: dict[str, str] | None = None
) -> Response:
    return _xml(xmlout.error(code, detail), status, headers)


def media_category_for(func: str, cats: list[int]) -> str | None:
    """Map requested Newznab categories to one of the four MediaGuard categories (None = mixed/unknown)."""
    if not cats:
        return None
    s = set(cats)
    anime = C.TV_ANIME in s
    has_movie = any(2000 <= c < 3000 for c in s)
    has_tv = any(5000 <= c < 6000 and c != C.TV_ANIME for c in s)
    if func == "movie":
        if has_movie or anime:
            return "movies-anime" if anime else "movies"
        return None
    if func == "tvsearch":
        if anime and not has_tv:
            return "tv-anime"
        if has_tv and not anime:
            return "tv"
        return None
    if anime and not has_tv and not has_movie:
        return "tv-anime"
    if has_movie and not has_tv and not anime:
        return "movies"
    if has_tv and not has_movie and not anime:
        return "tv"
    if has_movie and anime and not has_tv:
        return "movies-anime"
    return None


def _cat_match(r_cats: list[int], wanted: list[int]) -> bool:
    if not wanted:
        return True
    have = set(r_cats) | {(c // 1000) * 1000 for c in r_cats}
    want = set(wanted) | {(c // 1000) * 1000 for c in wanted if c % 1000 == 0}
    return bool(have & want)


def _request_origin(request: Request) -> str:
    """Origin as the caller saw it. The web container proxies /newznab and /torznab to core and sets
    X-Forwarded-Host/-Proto (core is only reachable through web), so links point at the published port,
    not at the internal ``api:8200``."""
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
    if host and proto in ("http", "https") and "/" not in host and "@" not in host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


def _base_url(request: Request) -> str:
    return keys.public_base_url() or _request_origin(request)


def _limits(consumer: dict[str, Any], *, grab: bool) -> Response | None:
    rate = int(consumer["rate_per_min"] or S.get("newznab.rate_per_min", 60))
    ok, wait = limiter.hit("newznab-grab" if grab else "newznab", str(consumer["id"]), rate, 60)
    if not ok:
        return _err(
            501 if grab else 500, f"retry in {int(wait) + 1}s", 429, {"Retry-After": str(int(wait) + 1)}
        )
    quota = int(
        consumer["daily_quota"] if consumer["daily_quota"] is not None else S.get("newznab.daily_quota", 0)
    )
    sid = f"consumer:{consumer['id']}"
    if quota and registry.usage_today(sid)["grabs" if grab else "queries"] >= quota:
        return _err(501 if grab else 500, "daily quota", 429)
    registry.count_usage(sid, queries=0 if grab else 1, grabs=1 if grab else 0)
    return None


def _handle(request: Request, *, torznab: bool) -> Response:
    p = request.query_params
    t = (p.get("t") or "").strip().lower()
    if not t:
        return _err(200, "t")
    max_limit = int(S.get("newznab.max_limit", 100))
    if t == "caps":
        return _xml(xmlout.caps(torznab=torznab, max_limit=max_limit))
    consumer = keys.lookup(p.get("apikey") or request.headers.get("X-Api-Key"))
    if consumer is None:
        return _err(100)
    keys.touch(int(consumer["id"]))
    if t == "get" or t == "download":
        token = p.get("id") or ""
        if not token or len(token) > 64:
            return _err(200, "id")
        if (resp := _limits(consumer, grab=True)) is not None:
            return resp
        return _download(token, torznab=torznab, consumer=consumer)
    func = SEARCH_FUNCS.get(t)
    if func is None:
        return _err(202, t)
    # validate numeric params
    nums: dict[str, int | None] = {}
    for name in ("season", "ep", "tvdbid", "tmdbid", "rid", "limit", "offset"):
        raw = (p.get(name) or "").strip()
        if raw == "":
            nums[name] = None
            continue
        if name == "season" and raw[:1].upper() == "S":
            raw = raw[1:]
        if not INT_RE.match(raw):
            return _err(201, name)
        nums[name] = int(raw)
    imdb = (p.get("imdbid") or "").strip().lower().removeprefix("tt")
    if imdb and not INT_RE.match(imdb):
        return _err(201, "imdbid")
    try:
        cats = [int(c) for c in (p.get("cat") or "").split(",") if c.strip()]
    except ValueError:
        return _err(201, "cat")
    if (resp := _limits(consumer, grab=False)) is not None:
        return resp
    limit = min(nums["limit"] or 50, max_limit)
    offset = nums["offset"] or 0
    mc = media_category_for(func, cats)
    ep = nums["ep"]
    absolute = None
    if func == "tvsearch" and ep is not None and nums["season"] is None and (mc == "tv-anime" or not cats):
        absolute, ep = ep, None
    query = SearchQuery(
        q=(p.get("q") or "").strip() or None,
        media_category=mc,
        kind=func,  # type: ignore[arg-type]
        season=nums["season"],
        episode=ep,
        absolute_episode=absolute,
        imdb_id=f"tt{int(imdb):07d}" if imdb else None,
        tvdb_id=nums["tvdbid"],
        tmdb_id=nums["tmdbid"],
        protocols={"torrent" if torznab else "usenet"},
        limit=limit,
        offset=offset,
        newznab_cats=cats or None,
    )
    try:
        trace = origin.inbound(
            header=request.headers.get(origin.TRACE_HEADER),
            consumer_node=consumer["node"],
            user_agent=request.headers.get("user-agent"),
            fingerprint=query.fingerprint(),
        )
    except origin.LoopRefused as exc:
        LOGGER.info("newznab-verzoek geweigerd: %s", exc.code)
        return _err(900, f"{exc.code}: {exc.message}", 508, {"X-MSO-Refusal": exc.code})
    allowed = set(S.get("newznab.backends", ["direct", "spotweb", "headerindex"]) or [])
    try:
        res = service.search(
            query, origin_trace=trace, allowed_kinds=allowed, actor=f"newznab:{consumer['label']}"
        )
    except origin.LoopRefused as exc:
        return _err(900, f"{exc.code}: {exc.message}", 508, {"X-MSO-Refusal": exc.code})
    items = [r for r in res.items if mc or _cat_match(r.categories, cats)]
    base = _base_url(request)
    endpoint = "torznab" if torznab else "newznab"
    key = p.get("apikey") or ""
    sources = service._source_rows()
    out = []
    for r in items:
        token = r.extra.get("token")
        if not token:
            continue
        r.extra["public_info_url"] = public_link(r.info_url)
        private = (
            r.private if r.private is not None else bool((sources.get(r.source_id) or {}).get("private"))
        )
        r.extra["public_magnet"] = redact_magnet(r.extra.get("magnet"), private)
        link = f"{base}/{endpoint}/api?" + urlencode({"t": "get", "id": token, "apikey": key})
        out.append((r, link))
    self_url = f"{base}/{endpoint}/api?" + urlencode({"t": t, **({"q": query.q} if query.q else {})})
    headers = {"X-MSO-Trace-Id": res.trace_id}
    return _xml(
        xmlout.rss(out, torznab=torznab, offset=offset, total=res.total, self_url=self_url), headers=headers
    )


def _download(token: str, *, torznab: bool, consumer: dict[str, Any]) -> Response:
    row = service.get_cached(token)
    if row is None or row["protocol"] != ("torrent" if torznab else "usenet"):
        return _err(300)
    try:
        f = service.fetch_download(token)
    except LookupError:
        return _err(300)
    except UnsafeTarget as exc:
        LOGGER.warning("download geweigerd (%s): %s", exc.code, redact(str(exc)))
        return _err(900, "download refused", 502)
    except Exception as exc:
        LOGGER.warning(
            "download via %s mislukt: %s", row["source_id"], redact(f"{type(exc).__name__}: {exc}")
        )
        return _err(900, "upstream download failed", 502)
    audit(
        f"newznab:{consumer['label']}",
        "newznab.grab",
        f"Download via MSO-endpoint: {row['title']}",
        target=row["source_id"],
        detail={"release_key": row["release_key"]},
    )
    if f.final_url:
        return RedirectResponse(f.final_url, status_code=302)
    safe_name = re.sub(r"[^A-Za-z0-9._ \-\[\]()]", "_", row["title"])[:180] or "download"
    ext = ".torrent" if torznab else ".nzb"
    ctype = "application/x-bittorrent" if torznab else "application/x-nzb"
    return Response(
        content=f.content,
        media_type=ctype,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}{ext}"'},
    )


newznab_router = APIRouter(tags=["newznab"])


@newznab_router.get("/newznab/api", include_in_schema=True)
def newznab_api(request: Request) -> Response:
    return _guarded(request, torznab=False)


@newznab_router.get("/torznab/api", include_in_schema=True)
def torznab_api(request: Request) -> Response:
    return _guarded(request, torznab=True)


def _guarded(request: Request, *, torznab: bool) -> Response:
    try:
        return _handle(request, torznab=torznab)
    except Exception as exc:
        LOGGER.error("newznab-endpoint fout: %s", redact(f"{type(exc).__name__}: {exc}"))
        return _err(900, None, 500)


# ------------------------------------------------------------------ management API
api_router = APIRouter(prefix="/api/v1/newznab", tags=["newznab"])


@api_router.get("/endpoint")
def endpoint_info(request: Request, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    consumer = keys.ensure_default_consumer()
    pub = keys.public_base_url()
    base = pub or _request_origin(request)
    regs = get_db().fetchall(
        "SELECT id, label, enabled, meta FROM sources WHERE origin='prowlarr' "
        "AND (meta->>'is_mso')::boolean IS TRUE ORDER BY id"
    )
    return {
        "public_url": pub or None,
        "public_url_configured": bool(pub),
        "newznab_url": f"{base}/newznab",
        "torznab_url": f"{base}/torznab",
        "api_path": "/api",
        "default_consumer": keys.public_view(consumer),
        "backends": S.get("newznab.backends", []),
        "max_limit": S.get("newznab.max_limit", 100),
        "prowlarr_registrations": [
            {
                "source_id": r["id"],
                "label": r["label"],
                "base_url_kind": (r["meta"] or {}).get("implementation"),
            }
            for r in regs
        ],
        "categories": {k: list(v.newznab) for k, v in C.CATEGORIES.items()},
    }


@api_router.get("/consumers")
def consumers(_: Principal = Depends(require("admin"))) -> dict[str, Any]:
    keys.ensure_default_consumer()
    rows = get_db().fetchall("SELECT * FROM newznab_consumers ORDER BY id")
    out = []
    for r in rows:
        v = keys.public_view(r)
        v["usage_today"] = registry.usage_today(f"consumer:{r['id']}")
        out.append(v)
    return {"items": out}


class ConsumerBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    node: str = Field("external", pattern=keys.NODE_RE)
    rate_per_min: int | None = Field(None, ge=1, le=1200)
    daily_quota: int | None = Field(None, ge=0, le=1_000_000)


@api_router.post("/consumers")
def consumer_create(body: ConsumerBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    row = keys.create_consumer(body.label, body.node)
    if body.rate_per_min is not None or body.daily_quota is not None:
        row = get_db().fetchone(
            "UPDATE newznab_consumers SET rate_per_min=%s, daily_quota=%s WHERE id=%s RETURNING *",
            (body.rate_per_min, body.daily_quota, row["id"]),
        )
    audit(
        p.actor,
        "newznab.consumer_created",
        f"Newznab-sleutel aangemaakt voor '{body.label}'",
        target=f"consumer:{row['id']}",
        detail={"node": body.node},
    )
    return {**keys.public_view(row), "apikey": keys.reveal(int(row["id"]))}


class ConsumerPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=80)
    node: str | None = Field(None, pattern=keys.NODE_RE)
    enabled: bool | None = None
    rate_per_min: int | None = Field(None, ge=1, le=1200)
    daily_quota: int | None = Field(None, ge=0, le=1_000_000)


@api_router.patch("/consumers/{cid}")
def consumer_patch(cid: int, body: ConsumerPatch, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, detail="Niets om te wijzigen")
    sets = ", ".join(f"{k}=%s" for k in data)
    row = get_db().fetchone(
        f"UPDATE newznab_consumers SET {sets} WHERE id=%s RETURNING *", [*data.values(), cid]
    )
    if row is None:
        raise HTTPException(404, detail="Sleutel niet gevonden")
    audit(
        p.actor,
        "newznab.consumer_updated",
        f"Newznab-sleutel '{row['label']}' gewijzigd",
        target=f"consumer:{cid}",
        detail=data,
    )
    return keys.public_view(row)


@api_router.post("/consumers/{cid}/reveal")
def consumer_reveal(cid: int, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    key = keys.reveal(cid)
    if key is None:
        raise HTTPException(404, detail="Sleutel niet gevonden")
    audit(p.actor, "newznab.key_revealed", "Newznab-sleutel getoond", target=f"consumer:{cid}")
    return {"apikey": key}


@api_router.post("/consumers/{cid}/rotate")
def consumer_rotate(cid: int, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    row = keys.rotate(cid)
    if row is None:
        raise HTTPException(404, detail="Sleutel niet gevonden")
    audit(
        p.actor,
        "newznab.key_rotated",
        f"Newznab-sleutel '{row['label']}' vernieuwd; oude sleutel werkt niet meer",
        target=f"consumer:{cid}",
    )
    return {
        **keys.public_view(row),
        "apikey": keys.reveal(cid),
        "note": "Werk de sleutel bij in Prowlarr (of laat MSO de registratie bijwerken via een voorstel).",
    }


@api_router.delete("/consumers/{cid}")
def consumer_delete(cid: int, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    row = get_db().fetchone("SELECT * FROM newznab_consumers WHERE id=%s", (cid,))
    if row is None:
        raise HTTPException(404, detail="Sleutel niet gevonden")
    if row["vault_name"] == keys.DEFAULT_VAULT_NAME:
        raise HTTPException(
            409, detail="De standaardsleutel kan niet worden verwijderd; vernieuw hem of schakel hem uit"
        )
    keys.delete(cid)
    audit(
        p.actor,
        "newznab.consumer_deleted",
        f"Newznab-sleutel '{row['label']}' verwijderd",
        target=f"consumer:{cid}",
    )
    return {"ok": True}


routers = [newznab_router, api_router]
