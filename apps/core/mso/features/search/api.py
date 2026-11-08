"""Search API: query, compare candidates, flag false positives, send to SABnzbd/qBittorrent, origin graph."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mso.audit import audit
from mso.auth import Principal, require
from mso.db import get_db
from mso.domain import categories as C
from mso.domain import outcomes, releasegate
from mso.domain.release import NormalizedRelease, ParsedTitle, QualityRequirements, check_quality
from mso.features.search import origin, service
from mso.features.search import settings as S
from mso.features.search.models import SearchQuery
from mso.security import UnsafeTarget, limiter, redact

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/search", tags=["zoeken"])
CLIENT_FOR_PROTOCOL = {"usenet": "sabnzbd", "torrent": "qbittorrent"}


def _result_view(res: service.SearchResult) -> dict[str, Any]:
    sources = service._source_rows()
    return {
        "items": [service.item_view(r, sources) for r in res.items],
        "sources": [s.as_dict() for s in res.sources],
        "trace_id": res.trace_id,
        "trace": res.trace,
        "total": res.total,
        "offset": res.offset,
        "limit": res.limit,
    }


@router.get("")
def search_api(
    q: str | None = Query(None, max_length=300),
    category: Literal["tv", "tv-anime", "movies", "movies-anime"] | None = None,
    kind: Literal["search", "tvsearch", "movie"] | None = None,
    season: int | None = Query(None, ge=0, le=999),
    episode: int | None = Query(None, ge=0, le=9999),
    absolute: int | None = Query(None, ge=0, le=99999),
    imdb: str | None = Query(None, pattern=r"^(tt)?\d{5,10}$"),
    tvdb: int | None = Query(None, ge=1),
    tmdb: int | None = Query(None, ge=1),
    protocol: Literal["usenet", "torrent"] | None = None,
    lang: list[str] | None = Query(None),
    sort: Literal["relevance", "age", "size", "seeders"] = "relevance",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=5000),
    slow: bool = Query(False, description="Opnieuw proberen met de ruimere Prowlarr-time-out"),
    p: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    ok, _ = limiter.hit("search", p.actor, 60, 60)
    if not ok:
        raise HTTPException(429, detail="Te veel zoekopdrachten; wacht een minuut")
    if not (q or imdb or tvdb or tmdb or category):
        raise HTTPException(422, detail="Geef een zoekterm, ID of categorie op")
    if kind is None:
        if category:
            kind = "movie" if C.CATEGORIES[category].kind == "movie" else "tvsearch"
        elif season is not None or episode is not None or tvdb or absolute is not None:
            kind = "tvsearch"
        elif tmdb or imdb:
            kind = "movie"
        else:
            kind = "search"
    imdb_id = (imdb if imdb.startswith("tt") else f"tt{imdb}") if imdb else None
    query = SearchQuery(
        q=q,
        media_category=category,
        kind=kind,
        season=season,
        episode=episode,
        absolute_episode=absolute,
        imdb_id=imdb_id,
        tvdb_id=tvdb,
        tmdb_id=tmdb,
        protocols={protocol} if protocol else None,
        limit=limit,
        offset=offset,
        languages=[x for x in (lang or []) if x in S.LANGS] or None,
    )
    try:
        res = service.search(
            query, actor=p.actor, sort=sort, slow=slow,
            per_actor_limit=int(S.get("search.max_concurrent_per_user", 2)),
        )
    except origin.LoopRefused as exc:
        raise HTTPException(409, detail=f"{exc.code}: {exc.message}") from exc
    except service.SearchBusy as exc:
        raise HTTPException(429, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, detail=str(exc)) from exc
    return _result_view(res)


# ------------------------------------------------------------------ compare / flag
def release_from_data(data: dict[str, Any]) -> NormalizedRelease:
    parsed = ParsedTitle(
        **{k: v for k, v in (data.get("parsed") or {}).items() if k in ParsedTitle.__dataclass_fields__}
    )
    fields = NormalizedRelease.__dataclass_fields__
    kw = {
        k: v
        for k, v in data.items()
        if k in fields and k not in ("parsed", "publish_date", "indexer_tested_at")
    }
    r = NormalizedRelease(**kw)
    r.parsed = parsed
    if data.get("publish_date"):
        try:
            r.publish_date = datetime.fromisoformat(data["publish_date"])
        except ValueError:
            r.publish_date = None
    return r


class Requirements(BaseModel):
    media_category: Literal["tv", "tv-anime", "movies", "movies-anime"]
    season: int | None = None
    episodes: list[int] = Field(default_factory=list)
    absolute_episodes: list[int] = Field(default_factory=list)
    min_resolution: int | None = None
    max_resolution: int | None = None
    require_hdr: list[str] = Field(default_factory=list)
    forbid_hdr: list[str] = Field(default_factory=list)
    required_audio_languages: list[str] = Field(default_factory=list)
    require_dual_audio: bool = False
    required_subtitles: list[str] = Field(default_factory=list)
    year: int | None = None


class CompareBody(BaseModel):
    tokens: list[str] = Field(min_length=2, max_length=6)
    requirements: Requirements | None = None


COMPARE_FIELDS = [
    ("protocol", "Protocol"),
    ("source_label", "Bron"),
    ("size_bytes", "Grootte"),
    ("age_days", "Leeftijd (dagen)"),
    ("seeders", "Seeders"),
    ("grabs", "Grabs"),
    ("resolution", "Resolutie"),
    ("source", "Bronmateriaal"),
    ("video_codec", "Videocodec"),
    ("hdr", "HDR"),
    ("audio_codecs", "Audio"),
    ("audio_languages", "Audiotalen"),
    ("dual_audio", "Dual audio"),
    ("subtitles", "Ondertitels"),
    ("release_group", "Releasegroep"),
    ("confidence", "Zekerheid"),
    ("false_positive_flags", "Waarschuwingen"),
    ("private", "Privé"),
]


@router.post("/compare")
def compare(body: CompareBody, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    rows = []
    for token in body.tokens:
        row = service.get_cached(token)
        if row is None:
            raise HTTPException(404, detail="Een of meer resultaten zijn verlopen; zoek opnieuw")
        rows.append(row)
    candidates = []
    for row in rows:
        data = dict(row["data"])
        parsed = data.get("parsed") or {}
        flat = {k: data.get(k) for k, _ in COMPARE_FIELDS}
        for k in (
            "resolution",
            "source",
            "video_codec",
            "hdr",
            "audio_codecs",
            "audio_languages",
            "dual_audio",
            "subtitles",
            "release_group",
        ):
            flat[k] = parsed.get(k)
        rejections: list[str] | None = None
        if body.requirements:
            req = QualityRequirements(**body.requirements.model_dump())
            rejections = check_quality(release_from_data(data), req)
        candidates.append(
            {
                "token": row["token"],
                "title": row["title"],
                "release_key": row["release_key"],
                "media_category": row["media_category"],
                "fields": flat,
                "rejections": rejections,
            }
        )
    differs = [k for k, _ in COMPARE_FIELDS if len({repr(c["fields"].get(k)) for c in candidates}) > 1]
    same_identity = len({c["release_key"] for c in candidates}) == 1
    return {
        "candidates": candidates,
        "fields": [{"key": k, "label": label} for k, label in COMPARE_FIELDS],
        "differs": differs,
        "same_identity": same_identity,
        "note": "Kwaliteitseisen van MediaGuard blijven leidend; MSO versoepelt ze nooit.",
    }


class FlagBody(BaseModel):
    token: str
    reason: Literal["verkeerde_inhoud", "verkeerde_aflevering", "verkeerde_taal", "nep", "anders"] = (
        "verkeerde_inhoud"
    )


@router.post("/flag")
def flag(body: FlagBody, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    row = service.get_cached(body.token)
    if row is None:
        raise HTTPException(404, detail="Resultaat niet (meer) beschikbaar")
    outcomes.observe(
        row["source_id"],
        "search.false_positive",
        1.0,
        {"reason": body.reason, "release_key": row["release_key"]},
    )
    audit(
        p.actor,
        "search.flagged",
        f"Vals-positief gemeld: {row['title']}",
        target=row["source_id"],
        detail={"reason": body.reason, "release_key": row["release_key"]},
    )
    return {"ok": True}


# ------------------------------------------------------------------ send to client
def _client_module(client: str) -> Any:
    try:
        if client == "sabnzbd":
            from mso.connectors.sabnzbd import SabClient  # type: ignore[import-not-found]

            return SabClient
        from mso.connectors.qbittorrent import QbitClient  # type: ignore[import-not-found]

        return QbitClient
    except ImportError as exc:
        raise HTTPException(409, detail="downloadclient niet beschikbaar") from exc


@router.get("/send-targets")
def send_targets(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    out = []
    for client, label, proto in (("sabnzbd", "SABnzbd", "usenet"), ("qbittorrent", "qBittorrent", "torrent")):
        entry: dict[str, Any] = {
            "client": client,
            "label": label,
            "protocol": proto,
            "available": False,
            "categories": [],
            "reason": None,
        }
        try:
            cls = _client_module(client)
            entry["categories"] = list(cls.from_config().categories())
            entry["available"] = True
        except HTTPException as exc:
            entry["reason"] = exc.detail
        except Exception as exc:
            entry["reason"] = redact(f"Niet bereikbaar of niet ingesteld: {exc}")
        out.append(entry)
    return {"items": out, "media_categories": list(C.MEDIA_CATEGORIES)}


class SendBody(BaseModel):
    token: str = Field(min_length=4, max_length=64)
    client: Literal["sabnzbd", "qbittorrent"] | None = None
    category: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9 _.\-]+$")


FAKE_BLACKLIST_TTL_H = 24 * 365 * 10  # a proven fake never becomes good (same as the malware cleanup)


def _blacklist_module() -> Any:
    """Slice D's release blacklist when installed (search also works without the recovery module)."""
    try:
        from mso.features.recovery import blacklist
    except ImportError:
        return None
    return blacklist


def _refuse(row: dict[str, Any], exc: releasegate.Refused, *, actor: str, client: str) -> HTTPException:
    """Audit a release-gate refusal (``search.send_refused``); a fake proven by the fetched file list goes on the
    release blacklist (shown as 'geblokkeerd' in later searches, never a recovery candidate)."""
    keys = list(dict.fromkeys([row["release_key"], *exc.keys])) if exc.content else []
    listed: list[str] = []
    bl = _blacklist_module() if keys else None
    for k in keys if bl is not None else []:
        try:
            bl.add(
                k, f"nep-release: {exc.reasons[0]}"[:300], FAKE_BLACKLIST_TTL_H, protocol=row["protocol"], actor=actor
            )
            listed.append(k)
        except ValueError:  # pragma: no cover - only for an empty key
            continue
    fake = exc.content or bool(exc.evidence.get("matches"))
    audit(
        actor,
        "search.send_refused",
        f"Versturen naar {client} geweigerd, "
        + ("nep-release" if fake else "bestanden niet te controleren")
        + f": {row['title']}",
        target=row["source_id"],
        detail={"release_key": row["release_key"], "reasons": exc.reasons, "checked": exc.evidence,
                "blacklisted": listed},
    )
    return HTTPException(422, detail=exc.message)


@router.post("/send")
def send(body: SendBody, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """Direct send (no change set). Release gate: a fake title is refused before anything is fetched; after
    fetching, the .torrent file list / magnet name / NZB file names are checked before the client gets it."""
    row = service.get_cached(body.token)
    if row is None:
        raise HTTPException(404, detail="Resultaat niet (meer) beschikbaar; zoek opnieuw")
    client = body.client or CLIENT_FOR_PROTOCOL[row["protocol"]]
    if CLIENT_FOR_PROTOCOL[row["protocol"]] != client:
        raise HTTPException(422, detail=f"Een {row['protocol']}-resultaat kan niet naar {client}")
    if service.is_blacklisted(row["release_key"]):
        raise HTTPException(
            409, detail="Deze release staat op de blokkeerlijst (eerder mislukt); niet opnieuw versturen"
        )
    media = releasegate.is_media_release(row["title"], row["media_category"])
    if hits := releasegate.executable_hint(row["title"], media=media):
        refused = releasegate.Refused(
            [releasegate.describe(m) for m in hits], evidence={"kind": "title", "matches": hits}
        )
        raise _refuse(row, refused, actor=p.actor, client=client)
    db = get_db()
    existing = db.fetchone("SELECT * FROM search_sends WHERE release_key=%s", (row["release_key"],))
    if existing and existing["status"] in ("sent", "sending"):
        return {"status": "already_sent", "send": existing}
    cls = _client_module(client)
    ok, wait = limiter.hit("search-send", p.actor, int(S.get("search.send_rate_per_min", 20)), 60)
    if not ok:
        raise HTTPException(429, detail=f"Te veel verstuur-acties; wacht {int(wait) + 1} s")
    claimed = db.fetchone(
        "INSERT INTO search_sends(release_key,client,category,title,source_id,status,actor) "
        "VALUES (%s,%s,%s,%s,%s,'sending',%s) ON CONFLICT (release_key) DO UPDATE SET status='sending', "
        "client=EXCLUDED.client, category=EXCLUDED.category, actor=EXCLUDED.actor, error=NULL, "
        "updated_at=now() "
        "WHERE search_sends.status='failed' RETURNING *",
        (row["release_key"], client, body.category, row["title"], row["source_id"], p.actor),
    )
    if claimed is None:
        return {
            "status": "already_sent",
            "send": db.fetchone("SELECT * FROM search_sends WHERE release_key=%s", (row["release_key"],)),
        }
    try:
        inst = cls.from_config()
        fetched = service.fetch_download(body.token)
        releasegate.check_download(row["protocol"], fetched, title=row["title"], media=media)
        if client == "sabnzbd":
            name = fetched.filename or f"{row['title']}.nzb"
            ref = inst.add_nzb(fetched.content, name, category=body.category)
        else:
            if fetched.final_url:
                inst.add_torrent(url=fetched.final_url, category=body.category)
            else:
                inst.add_torrent(data=fetched.content, category=body.category)
            ref = row["release_key"].split(":", 1)[1] if row["release_key"].startswith("btih:") else None
    except releasegate.Refused as exc:
        _fail(row, exc.message)
        raise _refuse(row, exc, actor=p.actor, client=client) from exc
    except (LookupError, UnsafeTarget) as exc:
        _fail(row, str(exc))
        raise HTTPException(409, detail=str(exc)) from exc
    except Exception as exc:
        msg = redact(f"{type(exc).__name__}: {exc}")
        _fail(row, msg)
        audit(
            p.actor,
            "search.send_failed",
            f"Versturen naar {client} mislukt: {row['title']}",
            target=row["source_id"],
            detail={"error": msg, "release_key": row["release_key"]},
        )
        raise HTTPException(502, detail=f"Versturen naar {client} mislukt: {msg}") from exc
    sent = db.fetchone(
        "UPDATE search_sends SET status='sent', client_ref=%s, updated_at=now() WHERE release_key=%s "
        "RETURNING *",
        (str(ref) if ref else None, row["release_key"]),
    )
    audit(
        p.actor,
        "search.sent",
        f"Verstuurd naar {client}: {row['title']}",
        target=row["source_id"],
        detail={"release_key": row["release_key"], "category": body.category, "client_ref": ref},
    )
    return {"status": "sent", "send": sent}


def _fail(row: dict[str, Any], message: str) -> None:
    get_db().execute(
        "UPDATE search_sends SET status='failed', error=%s, updated_at=now() WHERE release_key=%s",
        (redact(message)[:500], row["release_key"]),
    )


@router.get("/sends")
def sends(limit: int = Query(50, ge=1, le=500), _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {
        "items": get_db().fetchall("SELECT * FROM search_sends ORDER BY updated_at DESC LIMIT %s", (limit,))
    }


# ------------------------------------------------------------------ origin graph
@router.get("/origin-graph")
def origin_graph(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    g = origin.build_graph()
    me = origin.self_node()
    cycles = g.cycles()
    explained = []
    for cyc in cycles:
        how = "Wordt per verzoek doorbroken: "
        if "prowlarr" in cyc:
            how += (
                "verzoeken van Prowlarr bevragen Prowlarr nooit terug; MSO's eigen registratie in Prowlarr "
                "wordt uitgesloten bij elke Prowlarr-zoekopdracht."
            )
        else:
            how += "een bron die terugkomt bij een bezocht knooppunt wordt overgeslagen (cycle_detected)."
        explained.append({"cycle": cyc, "resolution": how})
    return {
        **g.as_dict(),
        "self": me,
        "max_hops": origin.max_hops(),
        "cycles": explained,
        "explicit_edges": get_db().fetchall("SELECT * FROM origin_edges ORDER BY src, dst"),
    }


class EdgeBody(BaseModel):
    src: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,80}$")
    dst: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,80}$")
    note: str = Field("", max_length=200)


@router.post("/origin-graph/edges")
def add_edge(body: EdgeBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    if body.src == body.dst:
        raise HTTPException(422, detail="Een bron kan niet naar zichzelf verwijzen")
    get_db().execute(
        "INSERT INTO origin_edges(src,dst,note,created_by) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (src,dst) DO UPDATE SET note=EXCLUDED.note",
        (body.src, body.dst, body.note, p.actor),
    )
    audit(
        p.actor,
        "search.origin_edge_added",
        f"Bronverbinding {body.src} → {body.dst} vastgelegd",
        detail=body.model_dump(),
    )
    return origin_graph(p)


@router.delete("/origin-graph/edges")
def delete_edge(src: str, dst: str, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    n = get_db().execute("DELETE FROM origin_edges WHERE src=%s AND dst=%s", (src, dst))
    if not n:
        raise HTTPException(404, detail="Verbinding niet gevonden")
    audit(p.actor, "search.origin_edge_removed", f"Bronverbinding {src} → {dst} verwijderd")
    return origin_graph(p)
