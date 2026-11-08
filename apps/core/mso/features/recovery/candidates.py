"""Fallback candidates for a wanted item, gated by MediaGuard's quality requirements (never relaxed).

Targets: ``mg:<MediaGuard wanted id>`` (from the read-only cache) or ``manual:<id>`` (standalone entry).
Requirements = category + exact numbering (anime absolute numbering included) + MediaGuard's effective policy
(``GET /api/v1/policy/effective``) + optional *tightening* by the user. Nothing can loosen them.

Usenet↔torrent fallback: a candidate of either protocol is only accepted when it passes the quality gate,
is not blacklisted, is not a fake release (no program or double extension in the name, plausible size for the
claimed resolution — :mod:`mso.domain.releasegate`) and — for torrents — passes the download/seed policy (Slice C's
``mso.features.torrent.policy.check_release`` when present, otherwise a conservative built-in check). The actual
file list is checked once more just before sending (``actions.send``).
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import settings_registry as sr
from mso.db import get_db, jsonb
from mso.domain import categories, releasegate
from mso.domain.release import NormalizedRelease, ParsedTitle, QualityRequirements, check_quality, parse_title
from mso.features.recovery import blacklist

LOGGER = logging.getLogger(__name__)
RE_YEAR_SUFFIX = re.compile(r"\((\d{4})\)\s*$")
TIGHTEN_FIELDS = {
    "min_resolution",
    "max_resolution",
    "require_hdr",
    "forbid_hdr",
    "required_audio_languages",
    "require_dual_audio",
    "required_subtitles",
    "forbid_hardsub",
}


class SearchUnavailable(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, detail="Zoekmachine niet beschikbaar (zoekmodule ontbreekt of is niet geladen)")


@dataclass
class Target:
    media_ref: str
    origin: str  # mediaguard|manual
    title: str  # display title
    query: str  # search text (series/movie title)
    media_category: str
    kind: str  # episode|movie
    season: int | None = None
    episode: int | None = None
    absolute: int | None = None
    year: int | None = None
    imdb_id: str | None = None
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    stored_requirements: dict[str, Any] = field(default_factory=dict)
    mg: dict[str, Any] | None = None


def resolve_target(media_ref: str) -> Target:
    origin, _, ident = media_ref.partition(":")
    if not ident.isdigit():
        raise HTTPException(404, detail="Onbekende verwijzing; gebruik mg:<id> of manual:<id>")
    db = get_db()
    if origin == "mg":
        row = db.fetchone("SELECT * FROM mg_wanted_cache WHERE mg_id=%s", (int(ident),))
        if row is None:
            raise HTTPException(404, detail="MediaGuard-item niet in de cache; synchroniseer eerst")
        if row["media_category"] not in categories.MEDIA_CATEGORIES:
            raise HTTPException(409, detail="Categorie van dit MediaGuard-item is onbekend; geen kandidaten")
        raw = row.get("raw") or {}
        year = None
        if row["kind"] == "movie" and (m := RE_YEAR_SUFFIX.search(row["title"] or "")):
            year = int(m.group(1))
        return Target(
            media_ref=media_ref,
            origin="mediaguard",
            title=row["title"],
            query=row["item_title"] or row["title"],
            media_category=row["media_category"],
            kind=row["kind"],
            season=row["season_number"],
            episode=row["episode_number"],
            absolute=row["absolute_episode_number"],
            year=year,
            imdb_id=raw.get("imdb_id"),
            tvdb_id=raw.get("tvdb_id"),
            tmdb_id=raw.get("tmdb_id"),
            mg=dict(row),
        )
    if origin == "manual":
        row = db.fetchone("SELECT * FROM recovery_manual_wanted WHERE id=%s", (int(ident),))
        if row is None:
            raise HTTPException(404, detail="Handmatig item niet gevonden")
        cat = row["media_category"]
        kind = "episode" if cat.startswith("tv") else "movie"
        code = ""
        if row["season_number"] is not None and row["episode_number"] is not None:
            code = f" S{row['season_number']:02d}E{row['episode_number']:02d}"
        elif row["absolute_episode_number"] is not None:
            code = f" - {row['absolute_episode_number']:02d}"
        elif row["year"]:
            code = f" ({row['year']})"
        return Target(
            media_ref=media_ref,
            origin="manual",
            title=row["title"] + code,
            query=row["title"],
            media_category=cat,
            kind=kind,
            season=row["season_number"],
            episode=row["episode_number"],
            absolute=row["absolute_episode_number"],
            year=row["year"],
            imdb_id=row["imdb_id"],
            tvdb_id=row["tvdb_id"],
            tmdb_id=row["tmdb_id"],
            stored_requirements=row["requirements"] or {},
        )
    raise HTTPException(404, detail="Onbekende verwijzing; gebruik mg:<id> of manual:<id>")


# ------------------------------------------------------------------ requirements
def tighten(req: QualityRequirements, extra: dict[str, Any] | None) -> QualityRequirements:
    """Merge user-supplied requirements; only ever makes them stricter."""
    if not extra:
        return req
    unknown = set(extra) - TIGHTEN_FIELDS
    if unknown:
        raise HTTPException(422, detail=f"Onbekende eis(en): {', '.join(sorted(unknown))}")
    r = dataclasses.replace(req)
    if extra.get("min_resolution"):
        r.min_resolution = max(int(extra["min_resolution"]), r.min_resolution or 0)
    if extra.get("max_resolution"):
        r.max_resolution = min(int(extra["max_resolution"]), r.max_resolution or 10**6)
    for key in ("require_hdr", "forbid_hdr", "required_audio_languages", "required_subtitles"):
        vals = [str(v).lower() for v in (extra.get(key) or [])]
        setattr(r, key, sorted(set(getattr(r, key)) | set(vals)))
    r.require_dual_audio = r.require_dual_audio or bool(extra.get("require_dual_audio"))
    r.forbid_hardsub = r.forbid_hardsub or bool(extra.get("forbid_hardsub"))
    if r.min_resolution and r.max_resolution and r.min_resolution > r.max_resolution:
        raise HTTPException(422, detail="Minimale resolutie is hoger dan maximale resolutie")
    if set(r.require_hdr) & set(r.forbid_hdr):
        raise HTTPException(422, detail="Dezelfde HDR-vorm kan niet vereist én verboden zijn")
    return r


def build_requirements(t: Target, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Returns {req: QualityRequirements, rules: {...}, sources: [...], policy_verified, policy}."""
    anime = t.media_category.endswith("anime")
    req = QualityRequirements(
        media_category=t.media_category,
        season=t.season if t.kind == "episode" else None,
        episodes=[t.episode] if t.kind == "episode" and t.episode is not None else [],
        absolute_episodes=[t.absolute] if anime and t.kind == "episode" and t.absolute is not None else [],
        year=t.year,
    )
    rules: dict[str, Any] = {"max_size_gb": None, "blocked_groups": [], "informational": []}
    sources = ["categorie + exacte nummering"]
    policy = None
    verified = True
    if t.origin == "mediaguard":
        from mso.features.mediaguard import service as mg

        policy = mg.effective_policy(t.mg or {})
        verified = policy is not None
        if policy:
            cfg = policy.get("config") or {}
            req.min_resolution = cfg.get("min_resolution")
            req.max_resolution = cfg.get("max_resolution")
            audio = cfg.get("audio") or {}
            if audio.get("nl_audio") == "required":
                req.required_audio_languages = ["nl"]
            rules["max_size_gb"] = cfg.get("max_size_gb")
            rules["blocked_groups"] = [str(g).lower() for g in cfg.get("blocked_groups") or []]
            if cfg.get("min_custom_format_score") is not None:
                rules["informational"].append(
                    f"Minimale custom-format-score {cfg['min_custom_format_score']}: alleen MediaGuard kan "
                    f"dit "
                    "beoordelen (bij grab)."
                )
            if audio.get("mode") == "anime":
                rules["informational"].append(
                    "Anime-audiovoorkeur ("
                    + str(audio.get("anime_preference"))
                    + ") is een voorkeur in MediaGuard, "
                    "geen harde eis; MediaGuard blijft na een fallback doorzoeken naar de voorkeursversie."
                )
            sources.append(f"MediaGuard-policy '{policy.get('profile_name_nl') or policy.get('profile_id')}'")
        else:
            rules["informational"].append(
                "MediaGuard-policy niet leesbaar: alleen categorie en nummering zijn "
                "gecontroleerd. Handmatig versturen is daarom geblokkeerd."
            )
    else:
        stored = {k: v for k, v in (t.stored_requirements or {}).items() if k in TIGHTEN_FIELDS}
        if stored:
            req = tighten(req, stored)
            sources.append("eisen van het handmatige item")
    if extra:
        req = tighten(req, extra)
        sources.append("extra aanscherping door gebruiker")
    return {"req": req, "rules": rules, "sources": sources, "policy_verified": verified, "policy": policy}


def requirements_view(b: dict[str, Any]) -> dict[str, Any]:
    return {
        **asdict(b["req"]),
        "max_size_gb": b["rules"]["max_size_gb"],
        "blocked_groups": b["rules"]["blocked_groups"],
        "informational": b["rules"]["informational"],
        "sources": b["sources"],
        "policy_verified": b["policy_verified"],
    }


# ------------------------------------------------------------------ evaluation
def _torrent_policy(r: NormalizedRelease) -> tuple[list[str], str]:
    try:
        mod = importlib.import_module("mso.features.torrent.policy")
        fn = getattr(mod, "check_release", None)
    except ModuleNotFoundError:
        fn = None
    if callable(fn):
        return list(fn(r) or []), "torrentbeleid (qBittorrent-module)"
    reasons = []
    if r.seeders is not None and r.seeders <= 0:
        reasons.append("geen seeders: torrent kan niet worden voltooid")
    if r.seeders is None:
        reasons.append("aantal seeders onbekend: beschikbaarheid niet aangetoond")
    return reasons, "basiscontrole (seeders)"


def _fmt_num(req: QualityRequirements) -> str:
    parts = []
    if req.season is not None and req.episodes:
        parts.append(f"S{req.season:02d}E" + "/".join(f"{e:02d}" for e in req.episodes))
    if req.absolute_episodes:
        parts.append("absoluut " + ",".join(str(a) for a in req.absolute_episodes))
    if req.year and req.media_category.startswith("movies"):
        parts.append(str(req.year))
    return " · ".join(parts) or "—"


def _fmt_parsed_num(p: ParsedTitle) -> str:
    parts = []
    if p.season is not None:
        parts.append(
            f"S{p.season:02d}"
            + (
                "E" + "/".join(f"{e:02d}" for e in p.episodes)
                if p.episodes
                else " (volledig seizoen)"
                if p.full_season
                else ""
            )
        )
    if p.absolute_episodes:
        a = p.absolute_episodes
        parts.append("absoluut " + (f"{a[0]}–{a[-1]}" if len(a) > 2 else ",".join(map(str, a))))
    if p.year:
        parts.append(str(p.year))
    return " · ".join(parts) or "onbekend"


def evaluate(r: NormalizedRelease, b: dict[str, Any], *, blacklisted: bool) -> dict[str, Any]:
    """Authoritative verdict = ``check_quality`` + extra MediaGuard rules + blacklist + torrent policy.
    Evidence rows (requirement vs candidate) are computed per dimension for the UI."""
    req: QualityRequirements = b["req"]
    rules = b["rules"]
    p = r.parsed
    reasons = list(check_quality(r, req))
    cat = req.media_category
    base = set(check_quality(r, QualityRequirements(media_category=cat, forbid_hardsub=False)))

    def dim(**kw: Any) -> list[str]:
        q = QualityRequirements(media_category=cat, forbid_hardsub=False, **kw)
        return [x for x in check_quality(r, q) if x not in base]

    ev: list[dict[str, Any]] = []

    def row(key: str, label: str, required: str, actual: str, bad: list[str]) -> None:
        ev.append(
            {
                "key": key,
                "label": label,
                "required": required,
                "actual": actual,
                "ok": not bad,
                "reasons": bad,
            }
        )

    row(
        "category",
        "Categorie",
        cat,
        r.media_category or "onbekend (niet uitgesloten)",
        [x for x in base if x.startswith("verkeerde categorie")],
    )
    row(
        "numbering",
        "Aflevering/film",
        _fmt_num(req),
        _fmt_parsed_num(p),
        dim(season=req.season, episodes=req.episodes, absolute_episodes=req.absolute_episodes, year=req.year),
    )
    if req.min_resolution or req.max_resolution:
        rng = (
            (f"≥ {req.min_resolution}p" if req.min_resolution else "")
            + (" en " if req.min_resolution and req.max_resolution else "")
            + (f"≤ {req.max_resolution}p" if req.max_resolution else "")
        )
        row(
            "resolution",
            "Resolutie",
            rng,
            f"{p.resolution}p" if p.resolution else "onbekend",
            dim(min_resolution=req.min_resolution, max_resolution=req.max_resolution),
        )
    if req.require_hdr or req.forbid_hdr:
        need = ", ".join(h.upper() for h in req.require_hdr) or "—"
        if req.forbid_hdr:
            need += " · niet: " + ", ".join(h.upper() for h in req.forbid_hdr)
        row(
            "hdr",
            "HDR / Dolby Vision",
            need,
            (", ".join(h.upper() for h in p.hdr) or "SDR/onbekend")
            + (f" (DV-profiel {p.dv_profile})" if p.dv_profile else ""),
            dim(require_hdr=req.require_hdr, forbid_hdr=req.forbid_hdr),
        )
    if req.required_audio_languages or req.require_dual_audio:
        need = ", ".join(req.required_audio_languages) or "—"
        if req.require_dual_audio:
            need += " · dual audio"
        actual = ", ".join(p.audio_languages) or "onbekend"
        if p.dual_audio:
            actual += " · dual audio"
        if p.multi_audio:
            actual += " · multi"
        row(
            "audio",
            "Audio",
            need,
            actual,
            dim(
                required_audio_languages=req.required_audio_languages,
                require_dual_audio=req.require_dual_audio,
            ),
        )
    if req.required_subtitles:
        row(
            "subtitles",
            "Ondertitels",
            ", ".join(req.required_subtitles),
            ", ".join(p.subtitles) or "onbekend",
            dim(required_subtitles=req.required_subtitles),
        )
    if req.forbid_hardsub:
        row(
            "hardsub",
            "Geen hardsubs",
            "geen hardsubs",
            "hardsubs" if p.hardsub else "geen hardsubs gevonden",
            ["hardsubs niet toegestaan"] if p.hardsub else [],
        )
    if p.obfuscated:
        row(
            "obfuscation",
            "Herkenbare titel",
            "herleidbare inhoud",
            "geobfusceerd",
            ["geobfusceerde titel: inhoud niet te verifiëren"],
        )
    if rules.get("max_size_gb"):
        size_gb = (r.size_bytes or 0) / 1e9
        bad = (
            [f"groter dan {rules['max_size_gb']} GB"]
            if r.size_bytes and size_gb > rules["max_size_gb"]
            else []
        )
        if not r.size_bytes:
            bad = ["grootte onbekend"]
        row(
            "size",
            "Maximale grootte",
            f"≤ {rules['max_size_gb']} GB",
            f"{size_gb:.1f} GB" if r.size_bytes else "onbekend",
            bad,
        )
        reasons += bad
    if rules.get("blocked_groups"):
        grp = (p.release_group or "").lower()
        bad = (
            [f"releasegroep '{p.release_group}' geblokkeerd in MediaGuard"]
            if grp and grp in rules["blocked_groups"]
            else []
        )
        row("group", "Releasegroep", "niet geblokkeerd", p.release_group or "onbekend", bad)
        reasons += bad
    bl = ["staat op de blacklist (eerder mislukt)"] if blacklisted else []
    row(
        "blacklist",
        "Blacklist",
        "niet eerder mislukt",
        "op blacklist" if blacklisted else "niet op blacklist",
        bl,
    )
    reasons += bl
    fake = releasegate.release_problems(r, media_category=cat)
    row(
        "authenticity",
        "Echte release",
        "geen programma (.exe/.scr/…) in naam of bestanden; grootte past bij de resolutie",
        "nep-release"
        if fake
        else "geen programma in de naam · " + ("grootte plausibel" if r.size_bytes else "grootte onbekend"),
        fake,
    )
    reasons += fake
    if r.protocol == "torrent":
        pol, pol_src = _torrent_policy(r)
        pol = [x for x in pol if x not in fake]  # the fake-release verdict already has its own row
        row(
            "torrent_policy",
            "Download-/seedbeleid",
            pol_src,
            f"seeders {r.seeders if r.seeders is not None else '?'}" + (" · privé" if r.private else ""),
            pol,
        )
        reasons += pol
    # de-duplicate, keep order
    seen: set[str] = set()
    reasons = [x for x in reasons if not (x in seen or seen.add(x))]
    return {"passed": not reasons, "reasons": reasons, "evidence": ev}


def _reliability(source_ids: list[str]) -> dict[str, float]:
    if not source_ids:
        return {}
    rows = get_db().fetchall("SELECT id, reliability FROM sources WHERE id = ANY(%s)", (source_ids,))
    return {r["id"]: float(r["reliability"]) for r in rows if r["reliability"] is not None}


def score(r: NormalizedRelease, reliability: float | None) -> tuple[float, list[str]]:
    why = []
    s = 50.0
    if r.parsed.resolution:
        s += min(r.parsed.resolution, 2160) / 2160 * 10
    s += r.confidence * 10
    if r.protocol == "torrent":
        seeds = r.seeders or 0
        s += min(seeds, 50) / 50 * 15
        why.append(f"{seeds} seeders")
    else:
        age = r.age_days()
        if age is not None:
            s += max(0.0, 10 - age / 365)
            why.append(f"{age:.0f} dagen oud")
        if r.grabs:
            s += min(r.grabs, 100) / 100 * 5
            why.append(f"{r.grabs} grabs")
    if reliability is not None:
        s += reliability * 15
        why.append(f"betrouwbaarheid bron {reliability:.0%}")
    else:
        why.append("betrouwbaarheid bron onbekend")
    return round(s, 1), why


def _summary(r: NormalizedRelease) -> dict[str, Any]:
    return {
        "release_key": r.release_key,
        "protocol": r.protocol,
        "title": r.title,
        "source_id": r.source_id,
        "size_bytes": r.size_bytes,
        "seeders": r.seeders,
        "leechers": r.leechers,
        "grabs": r.grabs,
        "age_days": r.age_days(),
        "private": r.private,
        "freeleech": r.freeleech,
        "also_from": r.extra.get("also_from", []),
        "has_download": bool(r.extra.get("token")) and bool(r.download_url or r.extra.get("magnet")),
        "parsed": {
            "resolution": r.parsed.resolution,
            "hdr": r.parsed.hdr,
            "dv_profile": r.parsed.dv_profile,
            "audio_languages": r.parsed.audio_languages,
            "dual_audio": r.parsed.dual_audio,
            "subtitles": r.parsed.subtitles,
            "release_group": r.parsed.release_group,
            "video_codec": r.parsed.video_codec,
            "source": r.parsed.source,
        },
    }


# ------------------------------------------------------------------ release (de)serialisation
def release_to_json(r: NormalizedRelease) -> dict[str, Any]:
    """Persistable form. Download URLs and magnets are dropped: they can carry indexer API keys or private
    tracker passkeys. Sending goes through Slice A's ``fetch_download(token)`` (secret re-injection
    server-side)."""
    d = asdict(r)
    d["download_url"] = None
    extra = dict(d.get("extra") or {})
    extra.pop("magnet", None)
    d["extra"] = extra
    return d


def release_from_json(d: dict[str, Any]) -> NormalizedRelease:
    names = {f.name for f in dataclasses.fields(NormalizedRelease)}
    data = {k: v for k, v in d.items() if k in names}
    parsed = data.pop("parsed", None) or {}
    pnames = {f.name for f in dataclasses.fields(ParsedTitle)}
    data["parsed"] = ParsedTitle(**{k: v for k, v in parsed.items() if k in pnames})
    for key in ("publish_date", "indexer_tested_at"):
        if isinstance(data.get(key), str):
            try:
                data[key] = datetime.fromisoformat(data[key])
            except ValueError:
                data[key] = None
    return NormalizedRelease(**data)


# ------------------------------------------------------------------ search (Slice A contract, lazy)
def _search_module() -> Any:
    try:
        return importlib.import_module("mso.features.search.service")
    except ModuleNotFoundError as exc:
        raise SearchUnavailable() from exc


def _make_query(mod: Any, t: Target) -> Any:
    cls = getattr(mod, "SearchQuery", None)
    if cls is None or not hasattr(mod, "search"):
        raise SearchUnavailable()
    tv = t.media_category.startswith("tv")
    kw: dict[str, Any] = {
        "q": t.query,
        "media_category": t.media_category,
        "kind": "tvsearch" if tv else "movie",
        "season": t.season if tv else None,
        "episode": t.episode if tv else None,
        "absolute_episode": t.absolute if tv else None,
        "imdb_id": t.imdb_id,
        "tvdb_id": t.tvdb_id,
        "tmdb_id": t.tmdb_id,
        "protocols": None,
        "limit": 100,
        "offset": 0,
    }
    try:
        accepted = set(inspect.signature(cls).parameters)
    except (TypeError, ValueError):
        accepted = set(kw)
    return cls(**{k: v for k, v in kw.items() if k in accepted})


def find_candidates(
    media_ref: str, *, extra: dict[str, Any] | None = None, actor: str = "system"
) -> dict[str, Any]:
    t = resolve_target(media_ref)
    b = build_requirements(t, extra)
    mod = _search_module()
    query = _make_query(mod, t)
    trace: list[str] = []
    try:
        result = mod.search(query, trace=trace, actor=actor)
    except TypeError:
        result = mod.search(query)
    items: list[NormalizedRelease] = list(getattr(result, "items", []) or [])
    for r in items:
        if not r.parsed or (
            r.parsed.confidence == 0 and r.parsed.resolution is None and not r.parsed.episodes
        ):
            r.parsed = parse_title(r.title)
        if r.media_category is None:
            pass  # unknown category is not a rejection by itself; numbering/quality still decide
    banned = blacklist.active_keys([r.release_key for r in items])
    rel = _reliability(sorted({r.source_id for r in items}))
    accepted, rejected = [], []
    ttl = timedelta(hours=float(sr.get("herstel.candidate_ttl_h")))
    now = datetime.now(UTC)
    reqv = requirements_view(b)
    db = get_db()
    with db.tx() as conn:
        conn.execute("DELETE FROM recovery_candidates WHERE media_ref=%s", (media_ref,))
        for r in items:
            v = evaluate(r, b, blacklisted=r.release_key in banned)
            sc, why = score(r, rel.get(r.source_id)) if v["passed"] else (None, [])
            entry = {**_summary(r), "score": sc, "score_basis": why, **v}
            (accepted if v["passed"] else rejected).append(entry)
            conn.execute(
                "INSERT INTO recovery_candidates(media_ref,release_key,protocol,title,source_id,"
                "passed,score,reasons,"
                "evidence,release,requirements,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (media_ref, release_key) DO NOTHING",
                (
                    media_ref,
                    r.release_key,
                    r.protocol,
                    r.title,
                    r.source_id,
                    v["passed"],
                    sc,
                    jsonb(v["reasons"]),
                    jsonb(v["evidence"]),
                    jsonb(release_to_json(r)),
                    jsonb(reqv),
                    now + ttl,
                ),
            )
    accepted.sort(key=lambda e: -(e["score"] or 0))
    sources = [
        s if isinstance(s, dict) else (asdict(s) if dataclasses.is_dataclass(s) else dict(vars(s)))
        for s in getattr(result, "sources", []) or []
    ]
    return {
        "media_ref": media_ref,
        "target": asdict(t) | {"mg": None},
        "requirements": reqv,
        "search": {"trace_id": getattr(result, "trace_id", None), "sources": sources},
        "accepted": accepted,
        "rejected": rejected,
        "counts": {
            "total": len(items),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "blacklisted": len(banned),
        },
        "computed_at": now.isoformat(timespec="seconds"),
    }


def stored_candidates(media_ref: str) -> list[dict[str, Any]]:
    return get_db().fetchall(
        "SELECT id, release_key, protocol, title, source_id, passed, score, reasons, evidence, requirements, "
        "computed_at, expires_at, (expires_at < now()) AS expired, release->'size_bytes' AS size_bytes, "
        "release->'seeders' AS seeders FROM recovery_candidates WHERE media_ref=%s "
        "ORDER BY passed DESC, score DESC NULLS LAST, id",
        (media_ref,),
    )
