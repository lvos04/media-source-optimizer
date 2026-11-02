"""User-configured direct indexers (Newznab, Torznab, Spotweb) — config, rate limit/quota/backoff, health.

API keys live only in the vault (``indexer.<slug>.api_key``). Each indexer is also a row in the shared
``sources`` table (``direct:<slug>`` or ``spotweb:<slug>``) so scoring, recovery and costs see it.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from mso import jobs
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.domain.release import NormalizedRelease
from mso.features.indexers.newznab_client import NewznabClient, newznab_cats_for
from mso.features.search import settings as S
from mso.features.search.models import SearchQuery, SourceError
from mso.security import check_url, limiter, redact
from mso.vault import get_vault

LOGGER = logging.getLogger(__name__)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
KIND_PROTOCOL = {"newznab": "usenet", "spotweb": "usenet", "torznab": "torrent"}
KIND_SOURCE = {"newznab": "nzb_indexer", "spotweb": "spotweb", "torznab": "torrent_indexer"}


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return s or "indexer"


def vault_name(slug: str) -> str:
    return f"indexer.{slug}.api_key"


def source_id(row: dict[str, Any]) -> str:
    return f"{'spotweb' if row['kind'] == 'spotweb' else 'direct'}:{row['slug']}"


def protocol_of(row: dict[str, Any]) -> str:
    return KIND_PROTOCOL[row["kind"]]


def list_rows(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM direct_indexers" + (" WHERE enabled" if enabled_only else "") + " ORDER BY label"
    return get_db().fetchall(sql)


def get_row(slug: str) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM direct_indexers WHERE slug=%s", (slug,))


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    caps = row.get("caps") or {}
    return {
        "slug": row["slug"],
        "source_id": source_id(row),
        "kind": row["kind"],
        "protocol": protocol_of(row),
        "label": row["label"],
        "url": row["url"],
        "api_path": row["api_path"],
        "enabled": row["enabled"],
        "scopes": list(row["scopes"] or []),
        "private": row["private"],
        "rate_per_min": row["rate_per_min"],
        "daily_quota": row["daily_quota"],
        "timeout_s": row["timeout_s"],
        "status": row["status"],
        "last_error": row["last_error"],
        "consecutive_failures": row["consecutive_failures"],
        "backoff_until": row["backoff_until"],
        "caps_checked_at": row["caps_checked_at"],
        "caps": {
            "server": caps.get("server"),
            "searching": caps.get("searching"),
            "limits": caps.get("limits"),
            "category_count": len(caps.get("categories") or []),
        },
        "api_key": get_vault().status(vault_name(row["slug"])),
        "queries_today": usage_today(source_id(row))["queries"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def sync_source(row: dict[str, Any]) -> None:
    outcomes.upsert_source(
        source_id(row),
        KIND_SOURCE[row["kind"]],
        row["label"],
        "direct",
        scopes=list(row["scopes"] or ["normal", "anime"]),
        private=bool(row["private"]),
        enabled=bool(row["enabled"]),
        meta={
            "direct_kind": row["kind"],
            "protocol": protocol_of(row),
            "slug": row["slug"],
            "untagged": False,
        },
    )


def validate_url(url: str) -> str:
    return check_url(url, purpose="indexer")


def save(
    values: dict[str, Any], *, slug: str | None = None, api_key: str | None = None, clear_key: bool = False
) -> dict[str, Any]:
    db = get_db()
    if slug is None:
        raise ValueError("slug vereist")
    cols = [
        "kind",
        "label",
        "url",
        "api_path",
        "enabled",
        "scopes",
        "private",
        "rate_per_min",
        "daily_quota",
        "timeout_s",
    ]
    existing = get_row(slug)
    if existing is None:
        data = {c: values[c] for c in cols if c in values and values[c] is not None}
        names = ["slug", *data]
        db.execute(
            f"INSERT INTO direct_indexers({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})",
            [slug, *data.values()],
        )
    else:
        data = {c: values[c] for c in cols if c in values and values[c] is not None}
        if data:
            sets = ", ".join(f"{c}=%s" for c in data)
            db.execute(
                f"UPDATE direct_indexers SET {sets}, updated_at=now() WHERE slug=%s", [*data.values(), slug]
            )
        if any(c in data for c in ("url", "api_path", "kind")):
            db.execute(
                "UPDATE direct_indexers SET caps='{}'::jsonb, caps_checked_at=NULL, status='unknown', "
                "consecutive_failures=0, backoff_until=NULL WHERE slug=%s",
                (slug,),
            )
    if api_key:
        get_vault().set(vault_name(slug), api_key)
    elif clear_key:
        get_vault().set(vault_name(slug), None)
    row = get_row(slug)
    assert row is not None
    sync_source(row)
    return row


def delete(slug: str) -> bool:
    row = get_row(slug)
    if row is None:
        return False
    db = get_db()
    db.execute("DELETE FROM direct_indexers WHERE slug=%s", (slug,))
    get_vault().set(vault_name(slug), None)
    db.execute(
        "UPDATE sources SET enabled=false, meta = meta || '{\"removed\": true}'::jsonb, updated_at=now() "
        "WHERE id=%s",
        (source_id(row),),
    )
    return True


# ------------------------------------------------------------------ usage, rate limit, backoff
def usage_today(sid: str) -> dict[str, int]:
    row = get_db().fetchone(
        "SELECT queries, grabs FROM indexer_usage WHERE source_id=%s AND day=CURRENT_DATE", (sid,)
    )
    return {"queries": int(row["queries"]) if row else 0, "grabs": int(row["grabs"]) if row else 0}


def count_usage(sid: str, *, queries: int = 0, grabs: int = 0) -> None:
    get_db().execute(
        "INSERT INTO indexer_usage(source_id,day,queries,grabs) VALUES (%s,CURRENT_DATE,%s,%s) "
        "ON CONFLICT (source_id,day) DO UPDATE SET queries=indexer_usage.queries+EXCLUDED.queries, "
        "grabs=indexer_usage.grabs+EXCLUDED.grabs",
        (sid, queries, grabs),
    )


def check_limits(row: dict[str, Any]) -> None:
    """Raise SourceError('backoff'|'rate_limited') when this indexer must not be queried now."""
    if row.get("backoff_until") and row["backoff_until"] > datetime.now(UTC):
        raise SourceError(
            "backoff",
            f"Wacht na {row['consecutive_failures']} fout(en) tot "
            f"{row['backoff_until'].astimezone(UTC):%H:%M} UTC",
        )
    sid = source_id(row)
    if row["daily_quota"] and usage_today(sid)["queries"] >= row["daily_quota"]:
        raise SourceError("rate_limited", f"Dagquotum van {row['daily_quota']} verzoeken bereikt")
    ok, wait = limiter.hit("indexer", sid, int(row["rate_per_min"]), 60)
    if not ok:
        raise SourceError(
            "rate_limited", f"Limiet {row['rate_per_min']}/min bereikt; over {int(wait) + 1} s weer"
        )


def record_success(row: dict[str, Any]) -> None:
    if row["consecutive_failures"] or row["status"] != "healthy":
        get_db().execute(
            "UPDATE direct_indexers SET status='healthy', consecutive_failures=0, backoff_until=NULL, "
            "last_error=NULL WHERE slug=%s",
            (row["slug"],),
        )


def record_failure(row: dict[str, Any], status: str, message: str) -> None:
    """Exponential backoff on infrastructure failures (not on query-specific category errors)."""
    if status in ("category_error", "rate_limited", "backoff"):
        return
    n = int(row["consecutive_failures"]) + 1
    cap = int(S.get("indexers.backoff_max_min", 360))
    minutes = min(cap, 2 ** (n - 1)) if n >= 2 else 0
    until = datetime.now(UTC) + timedelta(minutes=minutes) if minutes else None
    new_status = (
        "auth_failed" if status == "auth_error" else ("offline" if status == "timeout" else "degraded")
    )
    get_db().execute(
        "UPDATE direct_indexers SET status=%s, consecutive_failures=%s, backoff_until=%s, "
        "last_error=%s WHERE slug=%s",
        (new_status, n, until, redact(message)[:500], row["slug"]),
    )


# ------------------------------------------------------------------ client / search
def client_for(
    row: dict[str, Any],
    *,
    trace_header: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> NewznabClient:
    key = api_key if api_key is not None else get_vault().get(vault_name(row["slug"]))
    return NewznabClient(
        row["url"],
        row["api_path"],
        key,
        protocol=protocol_of(row),
        source_id=source_id(row),
        origin="direct",
        timeout=timeout or float(row["timeout_s"]),
        private=bool(row["private"]),
        trace_header=trace_header,
    )


def ensure_caps(row: dict[str, Any], client: NewznabClient) -> dict[str, Any]:
    caps = row.get("caps") or {}
    fresh = row.get("caps_checked_at") and row["caps_checked_at"] > datetime.now(UTC) - timedelta(hours=24)
    if caps and fresh:
        return caps
    try:
        caps, _ = client.caps()
    except SourceError:
        return caps  # search anyway with spec defaults; the search itself will classify the failure
    get_db().execute(
        "UPDATE direct_indexers SET caps=%s, caps_checked_at=now() WHERE slug=%s", (jsonb(caps), row["slug"])
    )
    return caps


def search(
    row: dict[str, Any],
    query: SearchQuery,
    *,
    trace_header: str | None,
    limit: int,
    timeout: float | None = None,
) -> list[NormalizedRelease]:
    check_limits(row)
    client = client_for(row, trace_header=trace_header, timeout=timeout)
    caps = ensure_caps(row, client)
    cats = query.newznab_cats or newznab_cats_for(query.media_category, caps)
    sid = source_id(row)
    count_usage(sid, queries=1)
    try:
        items, _ = client.search(query, cats=cats, caps=caps, limit=limit, offset=0)
    except SourceError as exc:
        record_failure(row, exc.status, exc.message)
        raise
    record_success(row)
    if row["kind"] == "spotweb":
        for r in items:
            r.origin = "spotweb"
    return items


def test(row: dict[str, Any], *, api_key: str | None = None) -> dict[str, Any]:
    """Caps + a small search: version, capabilities and whether search answers (never 'empty' as failure)."""
    try:
        validate_url(row["url"])
    except ValueError as exc:
        return {"ok": False, "status": "offline", "message": str(exc)}
    client = client_for(row, api_key=api_key, timeout=min(float(row.get("timeout_s") or 15), 20))
    try:
        caps, latency = client.caps()
    except SourceError as exc:
        status = {"auth_error": "auth_failed", "timeout": "offline"}.get(exc.status, "degraded")
        return {"ok": False, "status": status, "message": exc.message, "error_status": exc.status}
    searching = caps.get("searching") or {}
    probe: dict[str, Any] = {}
    try:
        items = client.rss(cats=None, limit=5)
        probe = {"ok": True, "count": len(items)}
    except SourceError as exc:
        probe = {"ok": False, "status": exc.status, "message": exc.message}
    server = caps.get("server") or {}
    msg = (
        f"{server.get('title') or row.get('label') or 'Indexer'}"
        f"{' ' + server['version'] if server.get('version') else ''} · "
        f"{len(caps.get('categories') or [])} hoofdcategorieën · zoeken: "
        + ", ".join(k for k, v in searching.items() if v.get("available"))
    )
    if not probe.get("ok"):
        msg += f" · testzoekopdracht mislukt: {probe.get('message')}"
    return {
        "ok": bool(probe.get("ok")),
        "status": "healthy" if probe.get("ok") else "degraded",
        "message": msg,
        "latency_ms": latency,
        "version": server.get("version"),
        "capabilities": {
            "searching": searching,
            "limits": caps.get("limits"),
            "categories": [c.get("id") for c in caps.get("categories") or []],
            "probe": probe,
        },
        "caps": caps,
    }


# ------------------------------------------------------------------ periodic health check
@jobs.handler("indexers.health")
def health_job(ctx: jobs.JobContext) -> dict[str, Any]:
    results: dict[str, str] = {}
    interval = timedelta(minutes=int(S.get("indexers.health_interval_min", 15)))
    cutoff = datetime.now(UTC) - interval + timedelta(seconds=30)
    rows = [
        r
        for r in list_rows(enabled_only=True)
        if ctx.payload.get("force") or not r["caps_checked_at"] or r["caps_checked_at"] < cutoff
    ]
    for i, row in enumerate(rows):
        sid = source_id(row)
        client = client_for(row)
        try:
            caps, latency = client.caps()
        except SourceError as exc:
            record_failure(row, exc.status, exc.message)
            outcomes.observe(sid, "health.ok", 0.0, {"status": exc.status})
            results[sid] = exc.status
        else:
            get_db().execute(
                "UPDATE direct_indexers SET caps=%s, caps_checked_at=now() WHERE slug=%s",
                (jsonb(caps), row["slug"]),
            )
            record_success(row)
            outcomes.observe(sid, "health.ok", 1.0)
            outcomes.observe(sid, "health.latency_ms", float(latency))
            results[sid] = "ok"
        ctx.progress((i + 1) / max(1, len(rows)), f"{i + 1}/{len(rows)} indexers gecontroleerd")
    return {"checked": len(rows), "results": results}


def _health_enabled() -> bool:
    try:
        return bool(get_db().scalar("SELECT count(*) FROM direct_indexers WHERE enabled"))
    except Exception:
        return False


jobs.every("indexers.health", 300, enabled=_health_enabled)  # per-indexer interval: setting
