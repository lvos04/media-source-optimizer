"""Search aggregator: fan-out over Prowlarr, direct indexers/Spotweb and pluggable backends with per-source
timeouts and an honest per-source status report, then normalise, classify (4 MediaGuard categories, anime kept
apart via source scopes), language-filter, flag likely false positives, dedupe by protocol identity (never by
title) and paginate.

Contract (AGENT_BRIEF): ``search(query, *, trace=None, actor='system') -> SearchResult``.
"""

from __future__ import annotations

import logging
import math
import secrets
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.domain import categories as C
from mso.domain import outcomes
from mso.domain.release import NormalizedRelease, dedupe
from mso.features.search import origin
from mso.features.search import settings as S
from mso.features.search.backends import BACKENDS
from mso.features.search.models import SearchQuery, SearchResult, SourceError, SourceReport
from mso.features.search.urls import (
    MAX_NZB_BYTES,
    MAX_TORRENT_BYTES,
    Fetched,
    fill_secret,
    public_link,
    redact_magnet,
    safe_fetch,
    strip_secret,
)
from mso.security import redact
from mso.vault import get_vault

__all__ = [
    "SearchQuery",
    "SearchResult",
    "SourceReport",
    "search",
    "fetch_download",
    "get_cached",
    "item_view",
    "is_blacklisted",
]
LOGGER = logging.getLogger(__name__)
QUERIED_STATUSES = {"ok", "empty", "timeout", "error", "category_error", "auth_error"}
MISMATCH_FLAGS = {
    "seizoen_wijkt_af",
    "aflevering_wijkt_af",
    "absolute_wijkt_af",
    "imdb_wijkt_af",
    "tvdb_wijkt_af",
    "tmdb_wijkt_af",
    "jaar_wijkt_af",
}


@dataclass
class _Task:
    source_id: str
    label: str
    scopes: tuple[str, ...]
    run: Any  # () -> list[NormalizedRelease]
    target: str = ""


def is_blacklisted(release_key: str) -> bool:
    """Slice D's blacklist when installed; absent module → nothing is blacklisted."""
    try:
        from mso.features.recovery import blacklist  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        return bool(blacklist.is_blacklisted(release_key))
    except Exception:  # noqa: BLE001
        LOGGER.warning("blacklist-controle mislukt voor %s", release_key)
        return False


def _source_rows() -> dict[str, dict[str, Any]]:
    return {
        r["id"]: r
        for r in get_db().fetchall(
            "SELECT id, kind, label, origin, scopes, private, enabled, reliability, meta FROM sources"
        )
    }


def _protocol_ok(query: SearchQuery, protocol: str) -> bool:
    return not query.protocols or protocol in query.protocols


def _scope_ok(query: SearchQuery, scopes: tuple[str, ...] | list[str]) -> bool:
    return not query.media_category or C.scope_allows(list(scopes), query.media_category)


# ------------------------------------------------------------------ planning
def _plan(
    query: SearchQuery, trace: origin.Trace, allowed_kinds: set[str] | None
) -> tuple[list[_Task], list[SourceReport]]:
    from mso.features.indexers import registry

    g = origin.build_graph()
    sources = _source_rows()
    tasks: list[_Task] = []
    skipped: list[SourceReport] = []
    timeout = float(S.get("search.timeout_s", 15))
    per_source = int(S.get("search.per_source_limit", 100))

    # Prowlarr hub
    if (
        cb.is_configured("prowlarr")
        and S.get("search.prowlarr_enabled", True)
        and (allowed_kinds is None or "prowlarr" in allowed_kinds)
    ):
        dec = origin.check_target(g, trace, "prowlarr")
        if not dec.allowed:
            skipped.append(
                SourceReport("prowlarr", "Prowlarr", "skipped_cycle", message=f"{dec.reason}: {dec.message}")
            )
        else:
            from mso.features.indexers.prowlarr_sync import mso_indexer_ids

            allowed, excluded = origin.hub_children(g, trace, "prowlarr")
            mso_ids = mso_indexer_ids()
            for child, why in excluded:
                skipped.append(
                    SourceReport(
                        child,
                        (sources.get(child) or {}).get("label") or child,
                        "skipped_cycle",
                        message=f"cycle_detected: {why}",
                    )
                )
            ids: list[int] = []
            scope_skipped = 0
            for child in allowed:
                src = sources.get(child)
                if not src or not src["enabled"]:
                    continue
                cid = int(child.split(":", 1)[1])
                if cid in mso_ids:
                    skipped.append(
                        SourceReport(
                            child,
                            src["label"],
                            "skipped_cycle",
                            message="cycle_detected: MSO-registratie in Prowlarr",
                        )
                    )
                    continue
                proto = "torrent" if src["kind"] == "torrent_indexer" else "usenet"
                if not _protocol_ok(query, proto):
                    continue
                if not _scope_ok(query, src["scopes"]):
                    scope_skipped += 1
                    continue
                ids.append(cid)
            known_children = any(s.startswith("prowlarr:") for s in sources)
            if known_children and not ids:
                skipped.append(
                    SourceReport(
                        "prowlarr",
                        "Prowlarr",
                        "skipped_scope",
                        message=f"Geen Prowlarr-indexer met passend bereik/protocol "
                        f"({scope_skipped} buiten bereik)",
                    )
                )
            else:
                privates = {
                    int(s.split(":")[1])
                    for s, r in sources.items()
                    if s.startswith("prowlarr:") and r["private"]
                }
                note = (
                    f"{len(ids)} indexers" if known_children else "alle indexers (nog niet gesynchroniseerd)"
                )
                if scope_skipped:
                    note += f"; {scope_skipped} buiten bereik ({C.scope_for(query.media_category or 'tv')})"

                def run_prowlarr(
                    ids: list[int] | None = ids if known_children else None,
                    privates: set[int] = privates,
                    mso_ids: set[int] = mso_ids,
                ) -> list[NormalizedRelease]:
                    return _search_prowlarr(query, trace, ids, privates, mso_ids, timeout, per_source)

                tasks.append(
                    _Task(
                        "prowlarr", f"Prowlarr ({note})", ("normal", "anime"), run_prowlarr, target="prowlarr"
                    )
                )

    # direct indexers + spotweb
    for row in registry.list_rows(enabled_only=True):
        sid = registry.source_id(row)
        kind = "spotweb" if row["kind"] == "spotweb" else "direct"
        if allowed_kinds is not None and kind not in allowed_kinds:
            continue
        if not _protocol_ok(query, registry.protocol_of(row)):
            continue
        if not _scope_ok(query, row["scopes"]):
            skipped.append(
                SourceReport(
                    sid,
                    row["label"],
                    "skipped_scope",
                    message=f"Bereik {', '.join(row['scopes'])} past niet bij {query.media_category}",
                )
            )
            continue
        dec = origin.check_target(g, trace, sid)
        if not dec.allowed:
            skipped.append(
                SourceReport(sid, row["label"], "skipped_cycle", message=f"{dec.reason}: {dec.message}")
            )
            continue

        def run_direct(row: dict[str, Any] = row) -> list[NormalizedRelease]:
            return registry.search(row, query, trace_header=trace.header(), limit=per_source, timeout=timeout)

        tasks.append(_Task(sid, row["label"], tuple(row["scopes"]), run_direct, target=sid))

    # pluggable backends (e.g. Slice B header index)
    for b in list(BACKENDS.values()):
        if allowed_kinds is not None and b.kind not in allowed_kinds:
            continue
        if query.protocols and not (b.protocols & query.protocols):
            continue
        if not _scope_ok(query, b.scopes):
            skipped.append(SourceReport(b.source_id, b.label, "skipped_scope"))
            continue
        dec = origin.check_target(g, trace, b.source_id)
        if not dec.allowed:
            skipped.append(
                SourceReport(b.source_id, b.label, "skipped_cycle", message=f"{dec.reason}: {dec.message}")
            )
            continue
        tasks.append(_Task(b.source_id, b.label, b.scopes, lambda b=b: b.fn(query), target=b.source_id))
    return tasks, skipped


def _search_prowlarr(
    query: SearchQuery,
    trace: origin.Trace,
    ids: list[int] | None,
    privates: set[int],
    mso_ids: set[int],
    timeout: float,
    limit: int,
) -> list[NormalizedRelease]:
    from mso.connectors.prowlarr import ProwlarrClient, ProwlarrError, normalize_result, prowlarr_query

    cats = query.newznab_cats or (C.newznab_for(query.media_category) if query.media_category else None)
    try:
        client = ProwlarrClient.from_config(timeout=timeout)
        with origin.outbound("prowlarr", query.fingerprint()):
            recs = client.search(
                query=prowlarr_query(
                    q=query.q,
                    season=query.season,
                    episode=query.episode
                    if query.episode is not None
                    else (
                        query.absolute_episode if query.kind == "tvsearch" and query.season is None else None
                    ),
                    imdb_id=query.imdb_id,
                    tvdb_id=query.tvdb_id,
                    tmdb_id=query.tmdb_id,
                ),
                type=query.kind,
                categories=cats,
                indexer_ids=ids,
                limit=limit,
                trace_header=trace.header(),
            )
    except ProwlarrError as exc:
        status = {
            "auth": "auth_error",
            "timeout": "timeout",
            "category": "category_error",
            "not_configured": "error",
        }.get(exc.code, "error")
        raise SourceError(status, exc.message) from exc
    out = []
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        try:
            if int(rec.get("indexerId") or -1) in mso_ids:
                continue  # never relay MSO's own results back (would be a loop artefact)
        except (TypeError, ValueError):
            pass
        r = normalize_result(rec, private_ids=privates)
        if _protocol_ok(query, r.protocol):
            out.append(r)
    return out


# ------------------------------------------------------------------ post-processing
def _classify(r: NormalizedRelease, query: SearchQuery, scopes: list[str] | tuple[str, ...]) -> str | None:
    """Returns the media category or None when the result must be dropped (anime/normal kept apart)."""
    anime_evidence = C.TV_ANIME in set(r.categories) or (list(scopes) == ["anime"]) or r.parsed.anime_style
    is_movie = None
    if query.kind == "movie":
        is_movie = True
    elif query.kind == "tvsearch":
        is_movie = False
    mc = C.media_category_from(
        r.categories, anime_hint=True if anime_evidence else None, is_movie_hint=is_movie
    )
    want = query.media_category
    if not want:
        return mc or (None if not r.categories else mc)
    if mc == want:
        return mc
    want_def = C.CATEGORIES[want]
    if mc is None:
        r.false_positive_flags.append("categorie_onzeker")
        return want
    have = C.CATEGORIES[mc]
    if have.kind != want_def.kind:
        if want == "movies-anime" and mc == "tv-anime" and C.TV_ANIME in r.categories:
            return want  # anime films are published under 5070 too (MediaGuard RADARR_ANIME_EXTRA)
        return None
    if have.anime and not want_def.anime:
        return None  # anime result in a normal search: kept apart
    if want_def.anime and not have.anime:
        if list(scopes) == ["normal"]:
            return None
        if not (query.q or query.imdb_id or query.tvdb_id or query.tmdb_id):
            return None  # browsing a category: without anime evidence a mixed-indexer result stays out
        r.false_positive_flags.append("categorie_onzeker")  # specific title asked, no anime evidence in cats
        return want
    return want


def _languages_ok(r: NormalizedRelease, required: list[str], excluded: list[str]) -> bool:
    p = r.parsed
    langs = set(p.audio_languages)
    if p.dual_audio or p.multi_audio:
        return True
    if not langs:
        if required:
            r.false_positive_flags.append("taal_onbekend")
        return True
    if required and not (langs & set(required)):
        return False
    if excluded and (langs & set(excluded)) and not (langs & set(required)):
        return False
    return True


def _flags(r: NormalizedRelease, query: SearchQuery) -> None:
    p = r.parsed
    f = r.false_positive_flags
    if query.season is not None and p.season is not None and p.season != query.season:
        f.append("seizoen_wijkt_af")
    if query.episode is not None and p.episodes and query.episode not in p.episodes and not p.full_season:
        f.append("aflevering_wijkt_af")
    if (
        query.absolute_episode is not None
        and p.absolute_episodes
        and query.absolute_episode not in p.absolute_episodes
    ):
        f.append("absolute_wijkt_af")
    if query.imdb_id and r.imdb_id and r.imdb_id.lower() != query.imdb_id.lower():
        f.append("imdb_wijkt_af")
    if query.tvdb_id and r.tvdb_id and int(r.tvdb_id) != int(query.tvdb_id):
        f.append("tvdb_wijkt_af")
    if query.tmdb_id and r.tmdb_id and int(r.tmdb_id) != int(query.tmdb_id):
        f.append("tmdb_wijkt_af")
    if p.obfuscated:
        f.append("geobfusceerd")
    if r.size_bytes is not None and 0 < r.size_bytes < 20 * 1024 * 1024:
        f.append("verdacht_klein")
    if r.protocol == "torrent" and r.seeders == 0:
        f.append("geen_seeders")
    if is_blacklisted(r.release_key):
        f.append("geblokkeerd")
    ids_match = bool(
        (query.imdb_id and r.imdb_id and "imdb_wijkt_af" not in f)
        or (query.tvdb_id and r.tvdb_id and "tvdb_wijkt_af" not in f)
        or (query.tmdb_id and r.tmdb_id and "tmdb_wijkt_af" not in f)
    )
    conf = 0.3 + 0.5 * p.confidence + (0.2 if ids_match else 0.0)
    conf -= 0.25 * len([x for x in f if x in MISMATCH_FLAGS])
    conf -= 0.1 * len(
        [x for x in f if x in ("categorie_onzeker", "geobfusceerd", "verdacht_klein", "taal_onbekend")]
    )
    r.confidence = round(max(0.0, min(1.0, conf)), 2)
    r.false_positive_flags = sorted(set(f))


def _rank(r: NormalizedRelease, sources: dict[str, dict[str, Any]]) -> float:
    rel = (sources.get(r.source_id) or {}).get("reliability")
    rel = 0.6 if rel is None else float(rel)
    s = r.confidence * (0.5 + 0.5 * rel)
    if r.protocol == "torrent" and r.seeders:
        s += min(0.1, math.log10(1 + r.seeders) / 40)
    if "geblokkeerd" in r.false_positive_flags:
        s -= 1
    return s


def _cache(items: list[NormalizedRelease], sources: dict[str, dict[str, Any]]) -> None:
    if not items:
        return
    ttl = int(S.get("search.result_ttl_h", 24))
    expires = datetime.now(UTC) + timedelta(hours=ttl)
    secrets_by_source: dict[str, str | None] = {}
    rows = []
    for r in items:
        token = secrets.token_urlsafe(12)
        r.extra["token"] = token
        sec = secrets_by_source.setdefault(r.source_id, _source_secret(r.source_id))
        url = strip_secret(r.download_url, sec)
        private = (
            r.private if r.private is not None else bool((sources.get(r.source_id) or {}).get("private"))
        )
        # private-tracker magnets carry passkeys: only keep one when it is the sole way to download
        magnet = strip_secret(r.extra.get("magnet"), sec) if (not private or not url) else None
        data = item_view(r, sources, include_token=True)
        rows.append(
            (
                token,
                r.release_key,
                r.source_id,
                r.protocol,
                r.title[:1000],
                url,
                magnet,
                r.media_category,
                private,
                jsonb(data),
                expires,
            )
        )
    with get_db().tx() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO release_cache(token,release_key,source_id,protocol,title,download_url,magnet,"
                "media_category,private,data,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )
        conn.execute("DELETE FROM release_cache WHERE expires_at < now()")


def _source_secret(source_id: str) -> str | None:
    try:
        if source_id.startswith("prowlarr:") or source_id == "prowlarr":
            return cb.load_config("prowlarr", require_enabled=False).secrets.get("api_key")
        if source_id.startswith(("direct:", "spotweb:")):
            return get_vault().get(f"indexer.{source_id.split(':', 1)[1]}.api_key")
    except cb.ConnectorNotConfigured:
        return None
    return None


def item_view(
    r: NormalizedRelease, sources: dict[str, dict[str, Any]] | None = None, *, include_token: bool = True
) -> dict[str, Any]:
    """API/UI representation: never upstream download URLs or credentials."""
    d = r.as_dict()
    src = (sources or {}).get(r.source_id) or {}
    private = r.private if r.private is not None else bool(src.get("private"))
    d.pop("download_url", None)
    d["info_url"] = public_link(r.info_url)
    extra = dict(d.get("extra") or {})
    magnet = extra.pop("magnet", None)
    extra.pop("token", None)
    d["extra"] = extra
    d["magnet_url"] = redact_magnet(magnet, private)
    d["private"] = private
    d["source_label"] = src.get("label") or r.source_id
    d["source_reliability"] = src.get("reliability")
    d["also_from"] = extra.get("also_from", [])
    d["publish_date"] = r.publish_date.isoformat() if r.publish_date else None
    d["indexer_tested_at"] = r.indexer_tested_at.isoformat() if r.indexer_tested_at else None
    if include_token:
        d["token"] = r.extra.get("token")
    return d


def _record(report: SourceReport) -> None:
    if report.status not in QUERIED_STATUSES:
        return
    db = get_db()
    labels = {"status": report.status}
    if report.latency_ms is not None:
        outcomes.observe(report.source_id, "search.latency_ms", float(report.latency_ms), labels, db=db)
    outcomes.observe(report.source_id, "search.ok", 1.0 if report.status == "ok" else 0.0, labels, db=db)
    outcomes.observe(
        report.source_id, "search.empty", 1.0 if report.status == "empty" else 0.0, labels, db=db
    )
    outcomes.observe(
        report.source_id, "search.error", 0.0 if report.status in ("ok", "empty") else 1.0, labels, db=db
    )


# ------------------------------------------------------------------ main entry
def search(
    query: SearchQuery,
    *,
    trace: list[str] | None = None,
    actor: str = "system",
    origin_trace: origin.Trace | None = None,
    allowed_kinds: set[str] | None = None,
    sort: str = "relevance",
) -> SearchResult:
    """Fan out, merge and page. ``trace`` = origin nodes already visited before this node (anti-loop);
    ``origin_trace`` is the full inbound trace from the Newznab server. Raises origin.LoopRefused."""
    if query.media_category and query.media_category not in C.CATEGORIES:
        raise ValueError(f"onbekende categorie {query.media_category}")
    tr = origin_trace or origin.start(trace)
    tasks, reports = _plan(query, tr, allowed_kinds)
    timeout = float(S.get("search.timeout_s", 15))
    results: dict[str, list[NormalizedRelease]] = {}
    by_task: dict[str, SourceReport] = {}
    if tasks:
        pool = ThreadPoolExecutor(max_workers=min(16, len(tasks)), thread_name_prefix="mso-search")
        started: dict[str, float] = {}
        futures: dict[Future[Any], _Task] = {}
        for t in tasks:
            started[t.source_id] = time.monotonic()
            futures[pool.submit(t.run)] = t
        done, not_done = wait(futures, timeout=timeout + 5)
        for fut, t in futures.items():
            latency = int((time.monotonic() - started[t.source_id]) * 1000)
            if fut in not_done:
                by_task[t.source_id] = SourceReport(
                    t.source_id,
                    t.label,
                    "timeout",
                    latency_ms=latency,
                    message=f"Geen antwoord binnen {timeout:.0f} s",
                )
                continue
            try:
                items = fut.result()
            except SourceError as exc:
                by_task[t.source_id] = SourceReport(
                    t.source_id, t.label, exc.status, latency_ms=latency, message=redact(exc.message)
                )
                continue
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("zoekbron %s faalde: %s", t.source_id, redact(f"{type(exc).__name__}: {exc}"))
                by_task[t.source_id] = SourceReport(
                    t.source_id, t.label, "error", latency_ms=latency, message=redact(f"{type(exc).__name__}")
                )
                continue
            results[t.source_id] = list(items or [])
            by_task[t.source_id] = SourceReport(
                t.source_id, t.label, "ok" if items else "empty", count=len(items or []), latency_ms=latency
            )
        pool.shutdown(wait=False, cancel_futures=True)
    sources = _source_rows()
    required = list(
        dict.fromkeys(list(S.get("search.required_languages", []) or []) + list(query.languages or []))
    )
    excluded = list(S.get("search.excluded_languages", []) or [])
    merged: list[NormalizedRelease] = []
    for sid, items in results.items():
        dropped = 0
        for r in items:
            scopes = (sources.get(r.source_id) or {}).get("scopes") or ["normal", "anime"]
            if not _scope_ok(query, scopes):
                dropped += 1
                continue
            mc = _classify(r, query, scopes)
            if query.media_category and mc is None:
                dropped += 1
                continue
            r.media_category = mc
            if not _languages_ok(r, required, excluded):
                dropped += 1
                continue
            r.trace = [*tr.visited, sid] + ([r.source_id] if r.source_id != sid else [])
            _flags(r, query)
            merged.append(r)
        if dropped and sid in by_task:
            by_task[sid].message = (
                by_task[sid].message + " " if by_task[sid].message else ""
            ) + f"{dropped} resultaat/resultaten weggefilterd (categorie/bereik/taal)"
    for t in tasks:
        rep = by_task[t.source_id]
        reports.append(rep)
        _record(rep)
        if t.source_id == "prowlarr" and t.source_id in results:
            per_idx: dict[str, int] = {}
            for r in results["prowlarr"]:
                per_idx[r.source_id] = per_idx.get(r.source_id, 0) + 1
            for sid, n in per_idx.items():
                _record(SourceReport(sid, sid, "ok", count=n))
    items = dedupe(merged)
    if sort == "age":
        items.sort(key=lambda r: r.publish_date or datetime.min.replace(tzinfo=UTC), reverse=True)
    elif sort == "size":
        items.sort(key=lambda r: r.size_bytes or 0, reverse=True)
    elif sort == "seeders":
        items.sort(key=lambda r: r.seeders or 0, reverse=True)
    else:
        items.sort(
            key=lambda r: (_rank(r, sources), r.publish_date or datetime.min.replace(tzinfo=UTC)),
            reverse=True,
        )
    total = len(items)
    page = items[query.offset : query.offset + query.limit]
    _cache(page, sources)
    return SearchResult(
        items=page,
        sources=reports,
        trace_id=tr.trace_id,
        total=total,
        offset=query.offset,
        limit=query.limit,
        trace=list(tr.visited),
    )


# ------------------------------------------------------------------ cache + download passthrough
def get_cached(token: str) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM release_cache WHERE token=%s AND expires_at > now()", (token,))


def fetch_download(token: str) -> Fetched:
    """Fetch the NZB/.torrent for a cached result server-side (SSRF guard, size cap, secret re-injection).
    For magnet-only results returns ``Fetched(final_url=magnet)``."""
    row = get_cached(token)
    if row is None:
        raise LookupError("Resultaat niet (meer) beschikbaar; zoek opnieuw")
    if row["download_url"]:
        url = fill_secret(row["download_url"], _source_secret(row["source_id"]))
        cap = MAX_NZB_BYTES if row["protocol"] == "usenet" else MAX_TORRENT_BYTES
        f = safe_fetch(url, max_bytes=cap)
        if row["source_id"].startswith(("direct:", "spotweb:")):
            from mso.features.indexers import registry

            registry.count_usage(row["source_id"], grabs=1)
        return f
    if row["magnet"]:
        magnet = fill_secret(row["magnet"], _source_secret(row["source_id"]))
        return Fetched(b"", "application/x-bittorrent-magnet", None, final_url=magnet)
    raise LookupError("Geen downloadlink bij dit resultaat")
