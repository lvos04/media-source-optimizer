"""``/api/v1/recovery`` — Ontbrekend & herstel."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mso import jobs
from mso.audit import audit
from mso.auth import Principal, require
from mso.db import get_db, jsonb
from mso.domain import categories
from mso.features.recovery import actions, baseline, blacklist, candidates, fallback
from mso.features.recovery import tasks as _tasks  # noqa: F401  (registers job + schedule)

router = APIRouter(prefix="/api/v1/recovery", tags=["recovery"])
Category = Literal["tv", "tv-anime", "movies", "movies-anime"]


class Requirements(BaseModel):
    """Only tightening fields: MediaGuard requirements can never be relaxed through this API."""

    model_config = {"extra": "forbid"}
    min_resolution: Literal[480, 576, 720, 1080, 2160] | None = None
    max_resolution: Literal[480, 576, 720, 1080, 2160] | None = None
    require_hdr: list[Literal["hdr10", "hdr10plus", "dv", "hlg"]] = Field(default_factory=list)
    forbid_hdr: list[Literal["hdr10", "hdr10plus", "dv", "hlg"]] = Field(default_factory=list)
    required_audio_languages: list[str] = Field(default_factory=list, max_length=10)
    require_dual_audio: bool = False
    required_subtitles: list[str] = Field(default_factory=list, max_length=10)
    forbid_hardsub: bool = True


class ManualWanted(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    media_category: Category
    season_number: int | None = Field(default=None, ge=0, le=200)
    episode_number: int | None = Field(default=None, ge=0, le=5000)
    absolute_episode_number: int | None = Field(default=None, ge=0, le=20000)
    year: int | None = Field(default=None, ge=1900, le=2100)
    imdb_id: str | None = Field(default=None, pattern=r"^tt\d{5,10}$")
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    requirements: Requirements = Field(default_factory=Requirements)


class CandidateBody(BaseModel):
    extra: Requirements | None = None


class SendBody(BaseModel):
    release_key: str = Field(min_length=3, max_length=200)


class BlacklistBody(BaseModel):
    release_key: str = Field(min_length=3, max_length=200, pattern=r"^(nzb|mid|btih|btmh|guid):.+")
    reason: str = Field(min_length=2, max_length=300)
    media_ref: str | None = Field(default=None, max_length=100)
    protocol: Literal["usenet", "torrent"] | None = None
    ttl_h: float | None = Field(default=None, gt=0, le=8760)


def _extra(req: Requirements | None) -> dict[str, Any] | None:
    if req is None:
        return None
    d = req.model_dump()
    return {k: v for k, v in d.items() if v not in (None, [], False)} or None


# ------------------------------------------------------------------ baseline
@router.get("/baseline")
def get_baseline(
    days: int | None = Query(None, ge=1, le=365), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return baseline.compute(days)


# ------------------------------------------------------------------ wanted (MediaGuard cache + manual)
@router.get("/wanted")
def get_wanted(
    source: Literal["all", "mediaguard", "manual"] = "all",
    media_category: Category | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    """Paged list (default 50, max 200 per page). With ``source=all`` the open manual items come first
    (newest first), followed by the MediaGuard cache — one consistent ordering, so pages never overlap."""
    from mso.features.mediaguard import service as mg

    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"mode": mg.mode()}
    offset = (page - 1) * page_size
    manual_total = 0
    manual_rows: list[dict[str, Any]] = []
    if source in ("all", "manual"):
        where, params = ["status='open'"], []
        if media_category:
            where.append("media_category=%s")
            params.append(media_category)
        if q:
            where.append("title ILIKE %s")
            params.append(f"%{q}%")
        db = get_db()
        w = " WHERE " + " AND ".join(where)
        manual_total = int(db.scalar("SELECT count(*) FROM recovery_manual_wanted" + w, params) or 0)
        if offset < manual_total:
            manual_rows = db.fetchall(
                "SELECT * FROM recovery_manual_wanted" + w + " ORDER BY id DESC LIMIT %s OFFSET %s",
                [*params, page_size, offset],
            )
    total = manual_total
    for r in manual_rows:
        items.append(
            {
                "media_ref": f"manual:{r['id']}",
                "origin": "manual",
                "title": r["title"],
                "media_category": r["media_category"],
                "season_number": r["season_number"],
                "episode_number": r["episode_number"],
                "absolute_episode_number": r["absolute_episode_number"],
                "year": r["year"],
                "status": r["status"],
                "reason_code": None,
                "attempts": None,
                "stale": False,
                "requirements": r["requirements"],
            }
        )
    if source in ("all", "mediaguard"):
        res = mg.cached_wanted(
            media_category=media_category,
            q=q,
            offset=max(0, offset - manual_total),
            limit=page_size - len(manual_rows),
        )
        total += res["total"]
        meta.update(
            mediaguard_synced_at=res["synced_at"],
            mediaguard_fetched_at=res["fetched_at"],
            mediaguard_sync=res["sync"],
            mediaguard_stale=res["stale"],
        )
        for r in res["items"]:
            items.append(
                {
                    "media_ref": r["media_ref"],
                    "origin": "mediaguard",
                    "title": r["title"],
                    "media_category": r["media_category"],
                    "season_number": r["season_number"],
                    "episode_number": r["episode_number"],
                    "absolute_episode_number": r["absolute_episode_number"],
                    "status": r["status"],
                    "reason_code": r["last_reason_code"],
                    "attempts": r["attempts"],
                    "instance_id": r["instance_id"],
                    "stale": r["stale"],
                }
            )
    refs = [i["media_ref"] for i in items]
    if refs:
        counts = {
            r["media_ref"]: r
            for r in get_db().fetchall(
                "SELECT media_ref, count(*) FILTER (WHERE passed) AS accepted, count(*) AS total, "
                "max(computed_at) AS at "
                "FROM recovery_candidates WHERE media_ref = ANY(%s) AND expires_at > now() GROUP BY "
                "media_ref",
                (refs,),
            )
        }
        for i in items:
            c = counts.get(i["media_ref"])
            i["candidates"] = (
                {"accepted": c["accepted"], "total": c["total"], "computed_at": c["at"]} if c else None
            )
    return {"items": items, "total": total, "page": page, "page_size": page_size, **meta}


@router.post("/wanted")
def post_manual(body: ManualWanted, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if (
        body.media_category.startswith("tv")
        and body.episode_number is None
        and body.absolute_episode_number is None
    ):
        raise HTTPException(422, detail="Geef een afleveringsnummer of absoluut nummer op voor series")
    if body.episode_number is not None and body.season_number is None:
        raise HTTPException(422, detail="Een afleveringsnummer vereist een seizoensnummer")
    if body.media_category not in categories.MEDIA_CATEGORIES:
        raise HTTPException(422, detail="Onbekende categorie")
    reqs = body.requirements.model_dump()
    row = get_db().fetchone(
        "INSERT INTO recovery_manual_wanted(title,media_category,season_number,episode_number,"
        "absolute_episode_number,"
        "year,imdb_id,tvdb_id,tmdb_id,requirements,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "RETURNING *",
        (
            body.title.strip(),
            body.media_category,
            body.season_number,
            body.episode_number,
            body.absolute_episode_number,
            body.year,
            body.imdb_id,
            body.tvdb_id,
            body.tmdb_id,
            jsonb(reqs),
            p.actor,
        ),
    )
    audit(
        p.actor,
        "recovery.manual_added",
        f"Handmatig ontbrekend item: {body.title}",
        target=f"manual:{row['id']}",
    )
    return {**row, "media_ref": f"manual:{row['id']}"}


@router.delete("/wanted/{item_id}")
def delete_manual(item_id: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    n = get_db().execute(
        "UPDATE recovery_manual_wanted SET status='archived', updated_at=now() WHERE id=%s AND status='open'",
        (item_id,),
    )
    if not n:
        raise HTTPException(404, detail="Handmatig item niet gevonden")
    audit(p.actor, "recovery.manual_archived", "Handmatig item gearchiveerd", target=f"manual:{item_id}")
    return {"archived": True}


# ------------------------------------------------------------------ per item
@router.get("/items/{media_ref}")
def get_item(media_ref: str, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    from mso.connectors import mediaguard as mgc
    from mso.features.mediaguard import service as mg

    t = candidates.resolve_target(media_ref)
    b = candidates.build_requirements(t)
    diagnosis, diagnosis_error = None, None
    if t.origin == "mediaguard":
        try:
            diagnosis = mg.live_diagnosis(int(media_ref.split(":", 1)[1]))
        except mgc.MediaGuardError as exc:
            diagnosis_error = str(exc)
    can_search, search_why = (
        mg.can_request_search() if t.origin == "mediaguard" else (False, "Alleen voor MediaGuard-items")
    )
    ok, until = blacklist.media_allowed(media_ref)
    from mso import settings_registry as sr

    manual_send_allowed = (
        t.origin == "manual"
        or mg.mode() != "connected"
        or bool(sr.get("herstel.allow_manual_send_connected"))
    )
    return {
        "target": {
            **{k: v for k, v in t.__dict__.items() if k != "mg"},
            "instance_id": (t.mg or {}).get("instance_id"),
        },
        "requirements": candidates.requirements_view(b),
        "diagnosis": diagnosis,
        "diagnosis_error": diagnosis_error,
        "candidates": candidates.stored_candidates(media_ref),
        "sends": actions.sends(media_ref, 20),
        "search_requests": mg.search_requests(20, int(media_ref.split(":", 1)[1]))
        if t.origin == "mediaguard"
        else [],
        "blacklist": blacklist.list_entries(media_ref=media_ref, limit=50),
        "backoff": {"allowed": ok, "next_allowed_at": until, **(blacklist.media_backoff(media_ref) or {})},
        "actions": {
            "request_search": {"available": can_search, "reason": search_why or None},
            "manual_send": {
                "available": manual_send_allowed,
                "reason": None
                if manual_send_allowed
                else "MediaGuard is gekoppeld en eigenaar; vraag MediaGuard opnieuw te zoeken",
                "category": actions.client_category(t.media_category),
            },
        },
    }


@router.post("/items/{media_ref}/candidates")
def post_candidates(
    media_ref: str, body: CandidateBody | None = None, p: Principal = Depends(require("operator"))
) -> dict[str, Any]:
    res = candidates.find_candidates(media_ref, extra=_extra(body.extra if body else None), actor=p.actor)
    audit(
        p.actor,
        "recovery.candidates",
        f"Fallback-kandidaten gezocht: {res['counts']['accepted']} geschikt",
        target=media_ref,
        detail=res["counts"],
    )
    return res


@router.post("/items/{media_ref}/send")
def post_send(media_ref: str, body: SendBody, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    return actions.send(media_ref, body.release_key, actor=p.actor)


@router.post("/items/{media_ref}/request-search")
def post_request_search(media_ref: str, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    from mso.features.mediaguard import service as mg

    origin, _, ident = media_ref.partition(":")
    if origin != "mg" or not ident.isdigit():
        raise HTTPException(409, detail="Alleen MediaGuard-items kunnen bij MediaGuard worden aangevraagd")
    return mg.request_search(int(ident), actor=p.actor, trigger="manual")


@router.get("/sends")
def get_sends(
    limit: int = Query(100, ge=1, le=1000), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    return {"items": actions.sends(None, limit)}


# ------------------------------------------------------------------ failed downloads → fallback proposals
@router.get("/fallbacks")
def get_fallbacks(
    limit: int = Query(100, ge=1, le=500), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    """Failed downloads the analysis job looked at: mapping, skip reason, linked change set."""
    from mso import settings_registry as sr
    from mso.features.mediaguard import service as mg

    return {
        "items": fallback.list_fallbacks(limit),
        "settings": {
            "fallback_on_failure": bool(sr.get("recovery.fallback_on_failure")),
            "auto_send_fallback": bool(sr.get("recovery.auto_send_fallback")),
            "allow_manual_send_connected": bool(sr.get("herstel.allow_manual_send_connected")),
            "searches_per_run": int(sr.get("recovery.fallback_searches_per_run")),
        },
        "mediaguard_mode": mg.mode(),
    }


# ------------------------------------------------------------------ blacklist
@router.get("/blacklist")
def get_blacklist(
    active_only: bool = False,
    limit: int = Query(500, ge=1, le=5000),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    return {"items": blacklist.list_entries(active_only=active_only, limit=limit)}


@router.post("/blacklist")
def post_blacklist(body: BlacklistBody, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    return blacklist.add(
        body.release_key, body.reason, body.ttl_h, body.media_ref, body.protocol, actor=p.actor
    )


@router.delete("/blacklist/{entry_id}")
def delete_blacklist(entry_id: int, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    if not blacklist.remove(entry_id, actor=p.actor):
        raise HTTPException(404, detail="Blacklist-regel niet gevonden")
    return {"removed": True}


# ------------------------------------------------------------------ analysis job
@router.post("/analyze")
def post_analyze(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    job = jobs.enqueue("recovery.analyze", {}, created_by=p.actor, max_attempts=1)
    return {"job_id": job["id"], "status": job["status"]}
