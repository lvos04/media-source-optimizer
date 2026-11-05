"""Prowlarr API v1 connector (header ``X-Api-Key``).

Read side: status/version, health, indexers, indexerstatus, indexerstats, history, tags, applications and
search. Errors are classified (auth / timeout / category / http / unavailable / malformed) so the search
aggregator can tell a timeout or a category error apart from an empty result.

Write side: exactly ONE kind of write is ever proposed — registering MSO's own Newznab/Torznab endpoint as an
indexer in Prowlarr — through the change-set applier ``prowlarr`` (preview, approval, rollback). MSO never
creates, renames or re-assigns tags and never touches Applications: those belong to MediaGuard's reconcile.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from mso.connectors import base as cb
from mso.domain.release import NormalizedRelease, infohashes_from_magnet, parse_title
from mso.security import UnsafeTarget, redact

LOGGER = logging.getLogger(__name__)
PROWLARR_ID = "prowlarr"
MSO_INDEXER_PREFIX = "Media Source Optimizer"


class ProwlarrError(Exception):
    """``code``: auth | timeout | category | http | unavailable | malformed | not_configured."""

    def __init__(self, message: str, code: str = "http", status: int | None = None) -> None:
        super().__init__(redact(message))
        self.message = redact(message)
        self.code = code
        self.status = status


def _search_status(code: str) -> str:
    return {"auth": "auth_error", "timeout": "timeout", "category": "category_error"}.get(code, "error")


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_config(cls, *, timeout: float = 15.0) -> ProwlarrClient:
        cfg = cb.load_config(PROWLARR_ID)
        key = cfg.secrets.get("api_key")
        if not key:
            raise ProwlarrError("API-sleutel van Prowlarr ontbreekt", "not_configured")
        return cls(cfg.url, key, timeout=timeout)

    # -------------------------------------------------------------- transport
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        h = {"X-Api-Key": self.api_key, "Accept": "application/json"}
        h.update(headers or {})
        try:
            with cb.http_client(self.base_url, timeout=timeout or self.timeout, headers=h) as c:
                resp = c.request(method, f"/api/v1/{path.lstrip('/')}", params=params, json=json)
        except UnsafeTarget as exc:
            raise ProwlarrError(f"Prowlarr-URL geweigerd: {exc}", "unavailable") from exc
        except httpx.TimeoutException as exc:
            raise ProwlarrError(
                f"Prowlarr reageert niet binnen {timeout or self.timeout:.0f} s (time-out)", "timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Prowlarr onbereikbaar: {type(exc).__name__}", "unavailable") from exc
        body: Any = None
        if resp.content:
            try:
                body = resp.json()
            except ValueError as exc:
                if resp.status_code < 400:
                    raise ProwlarrError(
                        "Prowlarr gaf geen geldige JSON terug", "malformed", resp.status_code
                    ) from exc
                body = {"message": resp.text[:300]}
        if resp.status_code in (401, 403):
            raise ProwlarrError("Prowlarr weigert de API-sleutel", "auth", resp.status_code)
        if resp.status_code >= 400:
            if isinstance(body, list):
                msg = "; ".join(str(e.get("errorMessage", "")) for e in body[:5] if isinstance(e, dict))
            elif isinstance(body, dict):
                msg = str(body.get("message") or body.get("error") or "")
            else:
                msg = ""
            code = "category" if resp.status_code == 400 and "categor" in msg.lower() else "http"
            raise ProwlarrError(
                f"Prowlarr {method} {path}: HTTP {resp.status_code} {msg}".strip(), code, resp.status_code
            )
        return body

    # -------------------------------------------------------------- reads
    def status(self) -> dict[str, Any]:
        return self.request("GET", "system/status") or {}

    def health(self) -> list[dict[str, Any]]:
        return self.request("GET", "health") or []

    def indexers(self) -> list[dict[str, Any]]:
        return self.request("GET", "indexer") or []

    def indexer_status(self) -> list[dict[str, Any]]:
        return self.request("GET", "indexerstatus") or []

    def indexer_stats(self) -> dict[str, Any]:
        return self.request("GET", "indexerstats") or {}

    def history(self, page_size: int = 50) -> dict[str, Any]:
        return (
            self.request(
                "GET",
                "history",
                params={"page": 1, "pageSize": page_size, "sortKey": "date", "sortDirection": "descending"},
            )
            or {}
        )

    def tags(self) -> list[dict[str, Any]]:
        return self.request("GET", "tag") or []

    def applications(self) -> list[dict[str, Any]]:
        return self.request("GET", "applications") or []

    def indexer_schema(self) -> list[dict[str, Any]]:
        return self.request("GET", "indexer/schema") or []

    def search(
        self,
        *,
        query: str,
        type: str = "search",
        categories: list[int] | None = None,
        indexer_ids: list[int] | None = None,
        limit: int = 100,
        offset: int = 0,
        trace_header: str | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [
            ("query", query),
            ("type", type),
            ("limit", limit),
            ("offset", offset),
        ]
        params += [("categories", c) for c in categories or []]
        params += [("indexerIds", i) for i in indexer_ids or []]
        headers = {"X-MSO-Trace": trace_header} if trace_header else None
        return self.request("GET", "search", params=params, headers=headers, timeout=timeout) or []

    # -------------------------------------------------------------- the one write (via change set only)
    # No ``forceSave=true``: Prowlarr's own validation (incl. its test query) must decide; MSO never bypasses it.
    def create_indexer(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "indexer", json=body) or {}

    def update_indexer(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", f"indexer/{int(body['id'])}", json=body) or {}

    def delete_indexer(self, indexer_id: int) -> None:
        self.request("DELETE", f"indexer/{int(indexer_id)}")


# ------------------------------------------------------------------ helpers
def field_value(rec: dict[str, Any], name: str) -> Any:
    for f in rec.get("fields") or []:
        if f.get("name") == name:
            return f.get("value")
    return None


def norm_url(url: Any) -> str:
    return str(url or "").strip().rstrip("/").lower()


def prowlarr_query(
    *,
    q: str | None,
    season: int | None = None,
    episode: int | None = None,
    imdb_id: str | None = None,
    tvdb_id: int | None = None,
    tmdb_id: int | None = None,
) -> str:
    """Prowlarr's search API takes ids/season/episode as ``{Token:value}`` inside ``query``."""
    parts = [(q or "").strip()]
    if imdb_id:
        parts.append(f"{{ImdbId:{imdb_id}}}")
    if tvdb_id:
        parts.append(f"{{TvdbId:{tvdb_id}}}")
    if tmdb_id:
        parts.append(f"{{TmdbId:{tmdb_id}}}")
    if season is not None:
        parts.append(f"{{Season:{season:02d}}}")
    if episode is not None:
        parts.append(f"{{Episode:{episode:02d}}}")
    return " ".join(p for p in parts if p)


def normalize_result(rec: dict[str, Any], *, private_ids: set[int] | None = None) -> NormalizedRelease:
    proto = "torrent" if str(rec.get("protocol", "")).lower() == "torrent" else "usenet"
    iid = rec.get("indexerId")
    cats = []
    for c in rec.get("categories") or []:
        cid = c.get("id") if isinstance(c, dict) else c
        try:
            cats.append(int(cid))
        except (TypeError, ValueError):
            continue
    publish = None
    if rec.get("publishDate"):
        try:
            publish = datetime.fromisoformat(str(rec["publishDate"]).replace("Z", "+00:00"))
            if publish.tzinfo is None:
                publish = publish.replace(tzinfo=UTC)
        except ValueError:
            publish = None
    magnet = rec.get("magnetUrl")
    v1 = (rec.get("infoHash") or "").lower() or None
    v2 = None
    if magnet:
        m1, m2 = infohashes_from_magnet(magnet)
        v1 = v1 or m1
        v2 = m2
    imdb = rec.get("imdbId")
    if isinstance(imdb, int) or (isinstance(imdb, str) and imdb.isdigit()):
        imdb = f"tt{int(imdb):07d}" if int(imdb) else None
    title = str(rec.get("title") or rec.get("sortTitle") or "")
    r = NormalizedRelease(
        protocol=proto,
        source_id=f"prowlarr:{iid}",
        origin="prowlarr",
        title=title,
        guid=str(rec.get("guid") or rec.get("downloadUrl") or title),
        download_url=rec.get("downloadUrl"),
        info_url=rec.get("infoUrl"),
        size_bytes=rec.get("size"),
        publish_date=publish,
        categories=cats,
        tvdb_id=rec.get("tvdbId") or None,
        tmdb_id=rec.get("tmdbId") or None,
        imdb_id=imdb or None,
        seeders=rec.get("seeders"),
        leechers=rec.get("leechers"),
        grabs=rec.get("grabs"),
        files=rec.get("files"),
        infohash_v1=v1,
        infohash_v2=v2,
        private=(iid in private_ids) if private_ids is not None and iid is not None else None,
        parsed=parse_title(title),
    )
    if magnet:
        r.extra["magnet"] = magnet
    r.extra["indexer"] = rec.get("indexer")
    r.extra["upstream_cats"] = cats
    r.freeleech = freeleech_of(rec, r.private)
    return r


def freeleech_of(rec: dict[str, Any], private: bool | None) -> bool | None:
    """Freeleech only means something on a private (ratio) tracker. Prowlarr's Cardigann definitions give
    public trackers ``downloadVolumeFactor: 0`` (and so the 'freeleech' flag) — that is not freeleech. So:
    True only for a private indexer with an explicit ``downloadVolumeFactor == 0`` or freeleech flag; a
    missing factor is never read as 0; public/unknown indexers → None."""
    if private is not True or str(rec.get("protocol", "")).lower() != "torrent":
        return None
    flags = set()
    for f in rec.get("indexerFlags") or []:
        flags.add(str(f.get("name") if isinstance(f, dict) else f).strip().lower())
    dvf = rec.get("downloadVolumeFactor")
    factor: float | None
    try:
        factor = float(dvf) if dvf is not None and dvf != "" else None
    except (TypeError, ValueError):
        factor = None
    if (factor is not None and factor == 0) or "freeleech" in flags:
        return True
    if factor is not None:
        return False
    return None


# ------------------------------------------------------------------ connector test
ENDPOINTS = ("health", "indexer", "indexerstatus", "indexerstats", "history", "tag", "applications")


def _test(cfg: cb.ConnectorConfig) -> cb.TestResult:
    key = cfg.secrets.get("api_key")
    if not key:
        return cb.TestResult(False, "auth_failed", "Vul de API-sleutel van Prowlarr in")
    client = ProwlarrClient(cfg.url, key, timeout=10)
    start = time.monotonic()
    try:
        st = client.status()
    except ProwlarrError as exc:
        status = {"auth": "auth_failed", "timeout": "offline", "unavailable": "offline"}.get(
            exc.code, "degraded"
        )
        return cb.TestResult(False, status, exc.message)
    latency = int((time.monotonic() - start) * 1000)
    caps: dict[str, Any] = {"endpoints": {}}
    for ep in ENDPOINTS:
        try:
            data = client.request(
                "GET",
                ep if ep != "history" else "history",
                params={"pageSize": 1} if ep == "history" else None,
            )
            caps["endpoints"][ep] = True
            if ep == "indexer":
                caps["indexer_count"] = len(data or [])
                caps["indexers_enabled"] = sum(1 for i in data or [] if i.get("enable"))
                caps["protocols"] = sorted({str(i.get("protocol")) for i in data or [] if i.get("protocol")})
            if ep == "health":
                caps["health_issues"] = len(data or [])
            if ep == "tag":
                labels = {str(t.get("label", "")).lower() for t in data or []}
                caps["scope_tags"] = sorted(labels & {"anime", "normal"})
        except ProwlarrError:
            caps["endpoints"][ep] = False
    caps["search"] = True  # verified lazily by the first real search (no query spam on test)
    ok_eps = sum(caps["endpoints"].values())
    status = "healthy" if ok_eps == len(ENDPOINTS) and not caps.get("health_issues") else "degraded"
    msg = (
        f"Prowlarr {st.get('version', '?')} · {caps.get('indexer_count', 0)} indexers · "
        f"{ok_eps}/{len(ENDPOINTS)} endpoints bereikbaar"
    )
    if caps.get("health_issues"):
        msg += f" · {caps['health_issues']} gezondheidsmelding(en)"
    return cb.TestResult(
        True, status, msg, version=str(st.get("version") or "") or None, capabilities=caps, latency_ms=latency
    )


cb.register(
    cb.ConnectorSpec(
        id=PROWLARR_ID,
        kind="indexer_manager",
        label="Prowlarr",
        secret_fields=[cb.SecretField("api_key", "API-sleutel")],
        test=_test,
        optional=True,
        description="Indexers, statistieken en zoeken via Prowlarr (API v1). MSO wijzigt nooit tags of "
        "applicaties; die beheert MediaGuard.",
        default_url="",
    )
)

# The change-set applier and the source sync live in mso.features.indexers.prowlarr_sync (they need settings
# and the Newznab server's consumer keys). Import here so registering the connector also registers them.
from mso.features.indexers import prowlarr_sync as _prowlarr_sync  # noqa: E402,F401
