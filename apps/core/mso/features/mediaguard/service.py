"""MediaGuard integration engine: capability probe, read-only sync into a TTL cache, and the single write
MSO performs — "request search" (``POST /api/v1/wanted/{id}/search``) with MSO quota, MediaGuard quota
check, idempotency per item + window, exponential backoff and audit.

Modes:
- ``standalone``: not configured, or configured but unreachable — every MSO feature keeps working.
- ``degraded``:   reachable but authentication failed, required routes missing, or emergency stop active.
- ``connected``:  reachable, authenticated, required read routes present.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import settings_registry as sr
from mso import state
from mso.audit import audit
from mso.connectors import base as cb
from mso.connectors import mediaguard as mgc
from mso.db import get_db, jsonb
from mso.features.mediaguard import compat
from mso.features.mediaguard import settings as _settings  # noqa: F401  (registers settings)
from mso.security import redact

LOGGER = logging.getLogger(__name__)
API = mgc.API
MEDIA_TYPE_TO_CATEGORY = {"tv": "tv", "anime": "tv-anime", "movie": "movies", "anime_movie": "movies-anime"}
PROBE_MAX_AGE_S = 300
PAGE_SIZE = 200


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


# ------------------------------------------------------------------ snapshots
def snap_get(key: str) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM mg_snapshots WHERE key=%s", (key,))


def snap_put(key: str, data: Any, *, ttl_s: float | None = None, etag: str | None = None) -> None:
    expires = _now() + timedelta(seconds=ttl_s) if ttl_s else None
    get_db().execute(
        "INSERT INTO mg_snapshots(key,data,etag,fetched_at,expires_at) VALUES (%s,%s,%s,now(),%s) "
        "ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data, etag=EXCLUDED.etag, fetched_at=now(), "
        "expires_at=EXCLUDED.expires_at",
        (key, jsonb(data), etag, expires),
    )


def snap_data(key: str, default: Any = None) -> Any:
    row = snap_get(key)
    return default if row is None else row["data"]


# ------------------------------------------------------------------ connection / probe
def configured() -> bool:
    return cb.is_configured(mgc.CID)


def client() -> mgc.MediaGuardClient:
    return mgc.MediaGuardClient.from_config()


def refresh_probe(*, force: bool = False) -> dict[str, Any] | None:
    """Return the latest capability probe (cached up to 5 minutes)."""
    if not configured():
        return None
    row = snap_get("probe")
    if row is not None and not force and (_now() - row["fetched_at"]).total_seconds() < PROBE_MAX_AGE_S:
        return row["data"]
    p = mgc.probe(cb.load_config(mgc.CID))
    snap_put("probe", p)
    cb.record_test(mgc.CID, mgc.result_from_probe(p))
    return p


def _mode(p: dict[str, Any] | None) -> tuple[str, str]:
    if not configured():
        return "standalone", "MediaGuard is niet gekoppeld; MSO werkt zelfstandig."
    if p is None or not p.get("reachable"):
        return "standalone", "MediaGuard is niet bereikbaar; MSO werkt zelfstandig verder."
    if not p.get("auth_ok"):
        return "degraded", "MediaGuard bereikbaar, maar authenticatie mislukt."
    caps = p.get("capabilities") or {}
    missing = [r.label for r in compat.ROUTES if r.required and caps.get(r.id) is not True]
    if missing:
        return "degraded", f"Vereiste MediaGuard-routes ontbreken: {', '.join(missing)}."
    if p.get("emergency_stop"):
        return "degraded", "MediaGuard-noodstop is actief; MSO vraagt geen zoekacties aan."
    return "connected", "Verbonden via de gedocumenteerde MediaGuard-API."


def mode() -> str:
    p = snap_data("probe") if configured() else None
    return _mode(p)[0]


def can_request_search(p: dict[str, Any] | None = None) -> tuple[bool, str]:
    p = p if p is not None else (snap_data("probe") if configured() else None)
    m, why = _mode(p)
    if m != "connected":
        return False, why
    if (p.get("capabilities") or {}).get("wanted_search") is not True:
        return (
            False,
            "Deze MediaGuard-versie biedt 'zoek opnieuw' (POST /api/v1/wanted/{id}/search) niet aan.",
        )
    if p.get("role") not in ("operator", "admin"):
        return (
            False,
            "De MediaGuard-gebruiker van MSO heeft rol 'viewer'; 'operator' is nodig om te laten zoeken.",
        )
    return True, ""


def status(*, refresh: bool = False) -> dict[str, Any]:
    p = None
    if configured():
        try:
            p = refresh_probe(force=refresh)
        except cb.ConnectorNotConfigured:
            p = None
    m, why = _mode(p)
    caps = (p or {}).get("capabilities") or {}
    sync_state = snap_data("sync_state", {}) or {}
    ok, search_why = can_request_search(p) if p else (False, why)
    return {
        "mode": m,
        "reason": why,
        "configured": configured(),
        "version": (p or {}).get("version"),
        "role": (p or {}).get("role"),
        "auth_method": (p or {}).get("auth_method"),
        "emergency_stop": (p or {}).get("emergency_stop"),
        "reachable": bool((p or {}).get("reachable")),
        "latency_ms": (p or {}).get("latency_ms"),
        "checked_at": (p or {}).get("checked_at"),
        "warnings": (p or {}).get("warnings", []),
        "error": (p or {}).get("error"),
        "capabilities": caps,
        "compat": compat.matrix(
            caps if p else None,
            source=(p or {}).get("capability_source"),
            checked_at=(p or {}).get("checked_at"),
        ),
        "last_sync": sync_state or None,
        "search": {
            "available": ok,
            "reason": search_why or None,
            "auto_enabled": bool(sr.get("mediaguard.auto_search_requests")),
            "automation_allowed": state.automation_allowed(),
        },
        "quota": quota_overview(),
    }


# ------------------------------------------------------------------ sync (read-only)
def _conditional_get(
    c: mgc.MediaGuardClient,
    key: str,
    path: str,
    params: dict[str, Any] | None = None,
    ttl_s: float | None = None,
) -> Any:
    """GET with If-None-Match when MediaGuard ever returns an ETag (0.1.0 does not; then TTL applies)."""
    prev = snap_get(key)
    headers = {"If-None-Match": prev["etag"]} if prev is not None and prev.get("etag") else None
    r = c.request("GET", path, params=params, headers=headers)
    if r.status_code == 304 and prev is not None:
        snap_put(key, prev["data"], ttl_s=ttl_s, etag=prev["etag"])
        return prev["data"]
    c.raise_for(r)
    data = r.json()
    snap_put(key, data, ttl_s=ttl_s, etag=r.headers.get("ETag"))
    return data


def _row_from_item(item: dict[str, Any], expires: datetime) -> tuple[Any, ...]:
    mt = item.get("media_type")
    return (
        int(item["id"]),
        str(item.get("instance_id") or ""),
        mt,
        MEDIA_TYPE_TO_CATEGORY.get(str(mt)),
        str(item.get("kind") or ""),
        item.get("reason"),
        str(item.get("title") or ""),
        item.get("item_title"),
        item.get("item_provider_id"),
        item.get("provider_id"),
        item.get("season_number"),
        item.get("episode_number"),
        item.get("absolute_episode_number"),
        item.get("air_date_utc"),
        item.get("status"),
        item.get("attempts"),
        item.get("last_searched_at"),
        item.get("last_reason_code"),
        jsonb(item),
        expires,
    )


def sync(*, force: bool = False, actor: str = "system") -> dict[str, Any]:
    """Read-only sync of wanted items (paged), KPIs, reasons and backfill quota into the cache."""
    if not configured():
        return {"skipped": "standalone", "message": "MediaGuard is niet gekoppeld"}
    sync_state = snap_data("sync_state", {}) or {}
    backoff_until = sync_state.get("backoff_until")
    if backoff_until and not force and datetime.fromisoformat(backoff_until) > _now():
        return {"skipped": "backoff", "message": f"MediaGuard vroeg om te wachten tot {backoff_until}"}
    p = refresh_probe(force=True)
    m, why = _mode(p)
    started = time.monotonic()
    result: dict[str, Any] = {
        "started_at": _iso(_now()),
        "mode": m,
        "items": 0,
        "pages": 0,
        "complete": False,
    }
    if m == "standalone" or not (p or {}).get("auth_ok"):
        result.update(ok=False, error=why, finished_at=_iso(_now()))
        snap_put("sync_state", {**sync_state, **result, "last_error": why})
        return result
    caps = p.get("capabilities") or {}
    ttl_s = int(sr.get("mediaguard.cache_ttl_min")) * 60
    c = client()
    db = get_db()
    seen: list[int] = []
    try:
        max_pages = int(sr.get("mediaguard.sync_max_pages"))
        expires = _now() + timedelta(seconds=ttl_s)
        page = 1
        while page <= max_pages:
            data = c.get(
                f"{API}/wanted", {"status": "open", "page": page, "page_size": PAGE_SIZE, "sort": "priority"}
            )
            items = data.get("items") or []
            with db.tx() as conn:
                for item in items:
                    conn.execute(
                        "INSERT INTO mg_wanted_cache(mg_id,instance_id,media_type,media_category,"
                        "kind,reason,title,"
                        "item_title,item_provider_id,provider_id,season_number,episode_number,"
                        "absolute_episode_number,air_date_utc,status,attempts,last_searched_at,last_reason_code,raw,"
                        "expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (mg_id) DO UPDATE SET instance_id=EXCLUDED.instance_id, "
                        "media_type=EXCLUDED.media_type, media_category=EXCLUDED.media_category, "
                        "kind=EXCLUDED.kind, "
                        "reason=EXCLUDED.reason, title=EXCLUDED.title, item_title=EXCLUDED.item_title, "
                        "item_provider_id=EXCLUDED.item_provider_id, provider_id=EXCLUDED.provider_id, "
                        "season_number=EXCLUDED.season_number, episode_number=EXCLUDED.episode_number, "
                        "absolute_episode_number=EXCLUDED.absolute_episode_number, "
                        "air_date_utc=EXCLUDED.air_date_utc, status=EXCLUDED.status, "
                        "attempts=EXCLUDED.attempts, "
                        "last_searched_at=EXCLUDED.last_searched_at, "
                        "last_reason_code=EXCLUDED.last_reason_code, "
                        "raw=EXCLUDED.raw, fetched_at=now(), expires_at=EXCLUDED.expires_at",
                        _row_from_item(item, expires),
                    )
                    seen.append(int(item["id"]))
            result["pages"] = page
            total = int(data.get("total") or 0)
            if page * PAGE_SIZE >= total or not items:
                result["complete"] = True
                break
            page += 1
        result["items"] = len(seen)
        result["total_open"] = total
        if result["complete"]:
            # items MediaGuard no longer reports as open are dropped from the (non-authoritative) cache
            db.execute("DELETE FROM mg_wanted_cache WHERE NOT (mg_id = ANY(%s))", (seen,))
        if caps.get("wanted_kpis"):
            _conditional_get(c, "kpis", f"{API}/wanted/kpis", ttl_s=ttl_s)
        reasons_row = snap_get("reasons")
        if caps.get("wanted_reasons") and (
            reasons_row is None or (_now() - reasons_row["fetched_at"]).total_seconds() > 86400 or force
        ):
            _conditional_get(c, "reasons", f"{API}/wanted/reasons", ttl_s=86400)
        if caps.get("backfill_quota"):
            _conditional_get(c, "quota", f"{API}/wanted/backfill/quota", ttl_s=300)
        result.update(ok=True, error=None)
    except mgc.MediaGuardError as exc:
        result.update(ok=False, error=str(exc))
        if exc.code == "quota" or exc.status == 429:
            result["backoff_until"] = _iso(_now() + timedelta(minutes=15))
    result["finished_at"] = _iso(_now())
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    new_state = {
        **result,
        "last_success_at": result["finished_at"] if result.get("ok") else sync_state.get("last_success_at"),
        "last_error": result.get("error"),
    }
    if not result.get("backoff_until"):
        new_state.pop("backoff_until", None)
    snap_put("sync_state", new_state)
    if actor != "system":
        audit(
            actor,
            "mediaguard.sync",
            f"MediaGuard-synchronisatie: {result['items']} items",
            target="mediaguard",
            detail={"ok": result.get("ok"), "error": result.get("error")},
        )
    return result


def cached_wanted(
    *,
    media_category: str | None = None,
    q: str | None = None,
    reason_code: str | None = None,
    page: int = 1,
    page_size: int = 50,
    offset: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """One page of the cached wanted list. ``offset``/``limit`` override ``page``/``page_size`` (used when
    the recovery list merges manual items in front of the MediaGuard items)."""
    off = (page - 1) * page_size if offset is None else max(0, offset)
    lim = page_size if limit is None else max(0, limit)
    where, params = ["1=1"], []
    if media_category:
        where.append("media_category=%s")
        params.append(media_category)
    if reason_code:
        where.append("last_reason_code=%s")
        params.append(reason_code)
    if q:
        where.append("title ILIKE %s")
        params.append(f"%{q}%")
    db = get_db()
    sql_where = " WHERE " + " AND ".join(where)
    total = int(db.scalar("SELECT count(*) FROM mg_wanted_cache" + sql_where, params) or 0)
    rows = (
        db.fetchall(
            "SELECT mg_id, instance_id, media_type, media_category, kind, reason, title, item_title, "
            "season_number, episode_number, absolute_episode_number, air_date_utc, status, attempts, "
            "last_searched_at, last_reason_code, fetched_at, expires_at FROM mg_wanted_cache"
            + sql_where
            + " ORDER BY (raw->>'priority')::int DESC NULLS LAST, mg_id LIMIT %s OFFSET %s",
            [*params, lim, off],
        )
        if lim
        else []
    )
    now = _now()
    for r in rows:
        r["stale"] = r["expires_at"] < now
        r["media_ref"] = f"mg:{r['mg_id']}"
    oldest = db.scalar("SELECT min(fetched_at) FROM mg_wanted_cache")
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "fetched_at": oldest,
        "stale": any(r["stale"] for r in rows),
    }


def cache_item(mg_id: int) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM mg_wanted_cache WHERE mg_id=%s", (mg_id,))


def live_diagnosis(mg_id: int) -> dict[str, Any] | None:
    """Last stored MediaGuard diagnosis (``cached=true`` — no live calls, no MediaGuard quota)."""
    p = snap_data("probe") if configured() else None
    if _mode(p)[0] == "standalone" or not (p or {}).get("capabilities", {}).get("wanted_diagnosis", True):
        return None
    try:
        return client().get(f"{API}/wanted/{int(mg_id)}/diagnosis", {"cached": "true"})
    except mgc.MediaGuardError as exc:
        if exc.code in ("not_found", "route_missing"):
            return None
        raise


def effective_policy(item: dict[str, Any]) -> dict[str, Any] | None:
    """MediaGuard's effective quality policy for this wanted item (cached per item with the cache TTL)."""
    key = f"policy:{item['instance_id']}:{item['kind']}:{item.get('item_provider_id')}"
    row = snap_get(key)
    if row is not None and row.get("expires_at") and row["expires_at"] > _now():
        return row["data"]
    p = snap_data("probe") if configured() else None
    if _mode(p)[0] == "standalone" or (p or {}).get("capabilities", {}).get("policy_effective") is not True:
        return row["data"] if row is not None else None
    params: dict[str, Any] = {"instance_id": item["instance_id"]}
    if item.get("media_type"):
        params["media_type"] = item["media_type"]
    if item.get("item_provider_id") is not None:
        params["series_id" if item["kind"] == "episode" else "movie_id"] = int(item["item_provider_id"])
    try:
        data = client().get(f"{API}/policy/effective", params)
    except mgc.MediaGuardError:
        return row["data"] if row is not None else None
    snap_put(key, data, ttl_s=int(sr.get("mediaguard.cache_ttl_min")) * 60)
    return data


# ------------------------------------------------------------------ quota
def _mso_counts() -> tuple[int, int]:
    db = get_db()
    hour = int(
        db.scalar(
            "SELECT count(*) FROM mg_search_requests WHERE status IN ('sent','pending') "
            "AND requested_at > now() - interval '1 hour'"
        )
        or 0
    )
    day = int(
        db.scalar(
            "SELECT count(*) FROM mg_search_requests WHERE status IN ('sent','pending') "
            "AND requested_at > now() - interval '1 day'"
        )
        or 0
    )
    return hour, day


def quota_overview() -> dict[str, Any]:
    hour, day = _mso_counts()
    hmax, dmax = (
        int(sr.get("mediaguard.search_quota_per_hour")),
        int(sr.get("mediaguard.search_quota_per_day")),
    )
    row = snap_get("quota")
    return {
        "mso": {
            "hour_used": hour,
            "hour_max": hmax,
            "day_used": day,
            "day_max": dmax,
            "remaining": max(0, min(hmax - hour, dmax - day)),
        },
        "reserve": int(sr.get("mediaguard.quota_reserve")),
        "mediaguard": row["data"] if row is not None else None,
        "mediaguard_fetched_at": row["fetched_at"] if row is not None else None,
    }


def _live_mg_quota(c: mgc.MediaGuardClient, instance_id: str) -> dict[str, Any] | None:
    data = c.get(f"{API}/wanted/backfill/quota")
    snap_put("quota", data, ttl_s=300)
    q = data.get(instance_id) if isinstance(data, dict) else None
    return q if isinstance(q, dict) else None


def _refresh_quota_after_search(
    c: mgc.MediaGuardClient, instance_id: str, *, before: dict[str, Any], issued: Any = None
) -> None:
    """After a successful search request the cached MediaGuard quota (read just before the request) is one
    search behind. Re-read it (read-only GET; MediaGuard's own numbers win). Only if that read fails, count
    the search locally in the snapshot so the UI does not keep showing the pre-request value."""
    try:
        if _live_mg_quota(c, instance_id) is not None:
            return
    except Exception as exc:  # best effort: the search itself was sent; never turn that into an error
        LOGGER.info("MediaGuard-quotum na zoekverzoek niet opnieuw leesbaar: %s", redact(str(exc)))
    if issued == 0:  # MediaGuard issued nothing (e.g. already queued): nothing was used
        return
    row = snap_get("quota")
    data = dict(row["data"]) if row is not None and isinstance(row["data"], dict) else {}
    q = dict(data.get(instance_id) or before)
    for k in ("hour_used", "day_used"):
        if isinstance(q.get(k), int):
            q[k] += 1
    if isinstance(q.get("remaining"), int):
        q["remaining"] = max(0, q["remaining"] - 1)
    data[instance_id] = q
    snap_put("quota", data, ttl_s=300, etag=row["etag"] if row is not None else None)


# ------------------------------------------------------------------ request search (the only write)
def _backoff_row(mg_id: int) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM mg_search_backoff WHERE mg_id=%s", (mg_id,))


def _register_failure(mg_id: int) -> datetime:
    db = get_db()
    row = _backoff_row(mg_id)
    failures = (row["failures"] if row else 0) + 1
    base = int(sr.get("mediaguard.search_backoff_base_min")) * 60
    cap = int(sr.get("mediaguard.search_backoff_max_h")) * 3600
    nxt = _now() + timedelta(seconds=min(cap, base * 2 ** (failures - 1)))
    db.execute(
        "INSERT INTO mg_search_backoff(mg_id,failures,last_failure_at,next_allowed_at) VALUES "
        "(%s,%s,now(),%s) ON CONFLICT (mg_id) DO UPDATE SET failures=EXCLUDED.failures, "
        "last_failure_at=now(), next_allowed_at=EXCLUDED.next_allowed_at",
        (mg_id, failures, nxt),
    )
    return nxt


def _finish(
    req_id: int,
    key: str,
    status: str,
    *,
    http_status: int | None = None,
    response: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Store the outcome. Failed/refused requests release the idempotency key so a later attempt (after
    backoff/quota) is possible; sent requests keep it for the window."""
    new_key = key if status in ("sent", "pending") else f"{key}:{status}:{req_id}"
    return dict(
        get_db().fetchone(
            "UPDATE mg_search_requests SET status=%s, http_status=%s, response=%s, error=%s, "
            "idempotency_key=%s "
            "WHERE id=%s RETURNING *",
            (status, http_status, jsonb(response or {}), redact(error) if error else None, new_key, req_id),
        )
    )


def request_search(mg_id: int, *, actor: str, trigger: str = "manual") -> dict[str, Any]:
    """Ask MediaGuard to search one wanted item via its official route. Raises HTTPException (Dutch)."""
    if trigger == "auto" and not (sr.get("mediaguard.auto_search_requests") and state.automation_allowed()):
        raise HTTPException(
            409, detail="Automatische zoekverzoeken staan uit of automatisering is niet toegestaan"
        )
    p = refresh_probe()
    ok, why = can_request_search(p)
    if not ok:
        raise HTTPException(409, detail=why)
    item = cache_item(mg_id)
    if item is None:
        raise HTTPException(404, detail="Item niet (meer) in de MediaGuard-cache; synchroniseer eerst")
    window = int(sr.get("mediaguard.search_idempotency_window_min")) * 60
    key = f"mg-search:{mg_id}:{int(time.time() // window)}"
    db = get_db()
    row = db.fetchone(
        "INSERT INTO mg_search_requests(mg_id,instance_id,idempotency_key,trigger,status,actor) "
        "VALUES (%s,%s,%s,%s,'pending',%s) ON CONFLICT (idempotency_key) DO NOTHING RETURNING *",
        (mg_id, item["instance_id"], key, trigger, actor),
    )
    if row is None:
        existing = db.fetchone("SELECT * FROM mg_search_requests WHERE idempotency_key=%s", (key,))
        return {
            "duplicate": True,
            "request": existing,
            "quota": quota_overview(),
            "message": "Al aangevraagd binnen het idempotentievenster; niet opnieuw verstuurd.",
        }
    req_id = int(row["id"])
    # backoff after repeated failures
    bo = _backoff_row(mg_id)
    if bo and bo["next_allowed_at"] and bo["next_allowed_at"] > _now():
        msg = f"Wacht tot {_iso(bo['next_allowed_at'])} (na {bo['failures']} mislukte verzoeken)"
        _finish(req_id, key, "refused_quota", error=msg)
        raise HTTPException(429, detail=msg)
    # MSO quota (pending row of this request is excluded by subtracting one)
    hour, day = _mso_counts()
    hour, day = hour - 1, day - 1
    if hour >= int(sr.get("mediaguard.search_quota_per_hour")) or day >= int(
        sr.get("mediaguard.search_quota_per_day")
    ):
        msg = "MSO-zoekquotum voor MediaGuard is op; probeer later opnieuw"
        _finish(req_id, key, "refused_quota", error=msg)
        raise HTTPException(429, detail=msg)
    c = client()
    # MediaGuard quota: never exceed what is left, always keep a reserve for MediaGuard's own backfill
    reserve = int(sr.get("mediaguard.quota_reserve"))
    try:
        mgq = (
            _live_mg_quota(c, item["instance_id"])
            if (p.get("capabilities") or {}).get("backfill_quota")
            else None
        )
    except mgc.MediaGuardError as exc:
        _finish(req_id, key, "failed", error=str(exc))
        _register_failure(mg_id)
        raise HTTPException(502, detail=f"MediaGuard-quotum niet leesbaar: {exc}") from exc
    if mgq is None:
        msg = "MediaGuard-quotum is niet uit te lezen; MSO vraagt dan geen zoekactie aan"
        _finish(req_id, key, "refused_mediaguard_quota", error=msg)
        raise HTTPException(409, detail=msg)
    if int(mgq.get("remaining", 0)) <= reserve:
        msg = (
            f"MediaGuard-quotum voor {item['instance_id']} heeft {mgq.get('remaining', 0)} over "
            f"(reserve {reserve} voor MediaGuard's eigen backfill)"
        )
        _finish(req_id, key, "refused_mediaguard_quota", response={"quota": mgq}, error=msg)
        raise HTTPException(429, detail=msg)
    try:
        resp = c.request_search(mg_id)
    except mgc.MediaGuardError as exc:
        if exc.code == "quota":
            _finish(
                req_id,
                key,
                "refused_mediaguard_quota",
                http_status=exc.status,
                response={"detail": exc.detail},
                error=str(exc),
            )
            _register_failure(mg_id)
            raise HTTPException(429, detail=str(exc)) from exc
        _finish(req_id, key, "failed", http_status=exc.status, error=str(exc))
        nxt = _register_failure(mg_id)
        audit(
            actor,
            "mediaguard.search_failed",
            f"Zoekverzoek aan MediaGuard mislukt: {item['title']}",
            target="mediaguard",
            detail={"mg_id": mg_id, "error": str(exc), "next_allowed_at": _iso(nxt)},
        )
        code = 404 if exc.code == "not_found" else 502
        raise HTTPException(code, detail=f"MediaGuard: {exc}") from exc
    summary = {
        k: resp.get(k)
        for k in ("instance_id", "run_id", "issued", "issued_wanted_ids", "skipped_in_queue", "errors")
    }
    done = _finish(req_id, key, "sent", http_status=200, response=summary)
    db.execute("DELETE FROM mg_search_backoff WHERE mg_id=%s", (mg_id,))
    _refresh_quota_after_search(c, item["instance_id"], before=mgq, issued=summary.get("issued"))
    audit(
        actor,
        "mediaguard.search_requested",
        f"MediaGuard gevraagd opnieuw te zoeken: {item['title']}"
        + (" (automatisch)" if trigger == "auto" else ""),
        target="mediaguard",
        detail={
            "mg_id": mg_id,
            "instance_id": item["instance_id"],
            "trigger": trigger,
            "issued": summary.get("issued"),
        },
    )
    return {
        "duplicate": False,
        "request": done,
        "quota": quota_overview(),
        "message": "MediaGuard zoekt opnieuw via de eigen route (telt mee in het MediaGuard-quotum).",
    }


def search_requests(limit: int = 100, mg_id: int | None = None) -> list[dict[str, Any]]:
    if mg_id is not None:
        return get_db().fetchall(
            "SELECT * FROM mg_search_requests WHERE mg_id=%s ORDER BY id DESC LIMIT %s", (mg_id, limit)
        )
    return get_db().fetchall("SELECT * FROM mg_search_requests ORDER BY id DESC LIMIT %s", (limit,))
