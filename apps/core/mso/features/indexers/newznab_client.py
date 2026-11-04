"""Client for external Newznab / Torznab / Spotweb (Newznab-compatible) indexers.

XML is parsed with defusedxml (no entity expansion / external entities). Error documents
(``<error code=".." description=".."/>``) are mapped to distinct statuses so a timeout, credential error or
unsupported category is never reported as an empty result.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as DET

from mso.connectors.base import http_client
from mso.domain import categories as C
from mso.domain.release import NormalizedRelease, infohashes_from_magnet, parse_title
from mso.features.search.models import SearchQuery, SourceError
from mso.security import UnsafeTarget, redact

NS_NEWZNAB = "http://www.newznab.com/DTD/2010/feeds/attributes/"
NS_TORZNAB = "http://torznab.com/schemas/2015/feed"
AUTH_CODES = {100, 101, 102}
PARAM_CODES = {200, 201, 202, 203}
LIMIT_CODES = {429, 500, 501}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def error_status(code: int, description: str) -> str:
    if code in AUTH_CODES:
        return "auth_error"
    if code in LIMIT_CODES:
        return "rate_limited"
    if code in PARAM_CODES:
        return "category_error"
    return "error"


def parse_error(root: Element) -> SourceError | None:
    if _local(root.tag) != "error":
        return None
    code = _int(root.get("code")) or 900
    desc = redact(root.get("description") or "onbekende fout")
    return SourceError(error_status(code, desc), f"Indexerfout {code}: {desc}")


def parse_xml(data: bytes) -> Element:
    try:
        return DET.fromstring(data)
    except Exception as exc:
        raise SourceError("error", f"Onleesbaar XML-antwoord van indexer ({type(exc).__name__})") from exc


def parse_caps(root: Element) -> dict[str, Any]:
    if (err := parse_error(root)) is not None:
        raise err
    if _local(root.tag) != "caps":
        raise SourceError("error", "Geen geldig caps-document")
    out: dict[str, Any] = {"server": {}, "limits": {}, "searching": {}, "categories": []}
    for child in root:
        name = _local(child.tag)
        if name == "server":
            out["server"] = {k: v for k, v in child.attrib.items() if k in ("version", "title", "appversion")}
        elif name == "limits":
            out["limits"] = {k: _int(v) for k, v in child.attrib.items()}
        elif name == "searching":
            for s in child:
                out["searching"][_local(s.tag)] = {
                    "available": (s.get("available") or "no").lower() == "yes",
                    "params": [p.strip() for p in (s.get("supportedParams") or "q").split(",") if p.strip()],
                }
        elif name == "categories":
            for cat in child:
                if _local(cat.tag) != "category":
                    continue
                out["categories"].append(
                    {
                        "id": _int(cat.get("id")),
                        "name": cat.get("name"),
                        "subcats": [
                            {"id": _int(s.get("id")), "name": s.get("name")}
                            for s in cat
                            if _local(s.tag) == "subcat"
                        ],
                    }
                )
    return out


def caps_category_ids(caps: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for c in caps.get("categories") or []:
        if c.get("id") is not None:
            ids.add(int(c["id"]))
        for s in c.get("subcats") or []:
            if s.get("id") is not None:
                ids.add(int(s["id"]))
    return ids


def _freeleech(dvf: str | None, private: bool | None, protocol: str) -> bool | None:
    """Freeleech only on a private torrent tracker with an explicit ``downloadvolumefactor`` (public trackers
    often report 0 — that is not freeleech; a missing factor is never read as 0)."""
    if protocol != "torrent" or not private or dvf is None or str(dvf).strip() == "":
        return None
    try:
        return float(dvf) == 0
    except ValueError:
        return None


def parse_items(
    root: Element, *, source_id: str, origin: str, protocol: str, private: bool | None = None
) -> tuple[list[NormalizedRelease], dict[str, int | None]]:
    """Parse an RSS search response. Returns (releases, {'offset','total'})."""
    if (err := parse_error(root)) is not None:
        raise err
    channel = root.find("channel")
    if _local(root.tag) != "rss" or channel is None:
        raise SourceError("error", "Geen geldig RSS-zoekantwoord")
    paging: dict[str, int | None] = {"offset": None, "total": None}
    out: list[NormalizedRelease] = []
    for el in channel:
        name = _local(el.tag)
        if name == "response":
            paging = {"offset": _int(el.get("offset")), "total": _int(el.get("total"))}
        if name != "item":
            continue
        attrs: dict[str, list[str]] = {}
        title = guid = link = comments = pub = None
        size = None
        enclosure_url = None
        for f in el:
            fname = _local(f.tag)
            if fname == "attr":
                attrs.setdefault(f.get("name") or "", []).append(f.get("value") or "")
            elif fname == "title":
                title = (f.text or "").strip()
            elif fname == "guid":
                guid = (f.text or "").strip()
            elif fname == "link":
                link = (f.text or "").strip()
            elif fname == "comments":
                comments = (f.text or "").strip()
            elif fname == "pubDate":
                pub = (f.text or "").strip()
            elif fname == "size":
                size = _int(f.text)
            elif fname == "enclosure":
                enclosure_url = f.get("url")
                size = size or _int(f.get("length"))
        if not title:
            continue
        a1 = {k: v[0] for k, v in attrs.items()}
        cats = [c for c in (_int(x) for x in attrs.get("category", [])) if c is not None]
        publish = None
        if pub or a1.get("usenetdate"):
            try:
                publish = parsedate_to_datetime(a1.get("usenetdate") or pub or "")
                if publish.tzinfo is None:
                    publish = publish.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                publish = None
        magnet = a1.get("magneturl") or (link if link and link.startswith("magnet:") else None)
        v1 = (a1.get("infohash") or "").lower() or None
        v2 = None
        if magnet:
            m1, m2 = infohashes_from_magnet(magnet)
            v1 = v1 or m1
            v2 = m2
        imdb = a1.get("imdb") or a1.get("imdbid")
        if imdb and not imdb.startswith("tt"):
            imdb = f"tt{int(imdb):07d}" if imdb.isdigit() else imdb
        seeders = _int(a1.get("seeders"))
        peers = _int(a1.get("peers"))
        r = NormalizedRelease(
            protocol=protocol,
            source_id=source_id,
            origin=origin,
            title=title,
            guid=guid or link or enclosure_url or title,
            download_url=enclosure_url or (link if link and not link.startswith("magnet:") else None),
            info_url=comments or None,
            size_bytes=_int(a1.get("size")) or size,
            publish_date=publish,
            categories=cats,
            tvdb_id=_int(a1.get("tvdbid")),
            tmdb_id=_int(a1.get("tmdbid")),
            imdb_id=imdb,
            seeders=seeders,
            leechers=(peers - seeders) if peers is not None and seeders is not None else None,
            grabs=_int(a1.get("grabs")),
            files=_int(a1.get("files")),
            private=private,
            infohash_v1=v1,
            infohash_v2=v2,
            freeleech=_freeleech(a1.get("downloadvolumefactor"), private, protocol),
            parsed=parse_title(title),
        )
        if protocol == "usenet":
            r.nzb_id = None  # indexer guid is scoped per source by release_key()
        if magnet:
            r.extra["magnet"] = magnet
        if a1.get("season"):
            r.extra["season_attr"] = a1["season"]
        if a1.get("episode"):
            r.extra["episode_attr"] = a1["episode"]
        r.extra["upstream_cats"] = cats
        out.append(r)
    return out, paging


def build_params(query: SearchQuery, caps: dict[str, Any] | None, cats: list[int] | None) -> dict[str, str]:
    """Newznab parameters for a query, falling back to t=search when the indexer lacks tv/movie search."""
    searching = (caps or {}).get("searching") or {}
    kind = query.kind
    t = {"search": "search", "tvsearch": "tvsearch", "movie": "movie"}[kind]
    cap_key = {"search": "search", "tvsearch": "tv-search", "movie": "movie-search"}[kind]
    supported = searching.get(cap_key)
    params: dict[str, str] = {}
    q = (query.q or "").strip()
    if caps and kind != "search" and (supported is None or not supported.get("available")):
        t = "search"
        if kind == "tvsearch" and query.season is not None:
            q = f"{q} S{query.season:02d}" + (f"E{query.episode:02d}" if query.episode is not None else "")
        supported_params = {"q"}
    else:
        supported_params = set(
            (supported or {}).get("params") or ["q", "season", "ep", "tvdbid", "imdbid", "tmdbid", "rid"]
        )
    params["t"] = t
    if t == "tvsearch":
        if query.season is not None and "season" in supported_params:
            params["season"] = str(query.season)
        if query.episode is not None and "ep" in supported_params:
            params["ep"] = str(query.episode)
        elif query.absolute_episode is not None and not query.season and "ep" in supported_params:
            params["ep"] = str(query.absolute_episode)
        if query.tvdb_id and "tvdbid" in supported_params:
            params["tvdbid"] = str(query.tvdb_id)
        if query.imdb_id and "imdbid" in supported_params:
            params["imdbid"] = query.imdb_id.removeprefix("tt")
    elif t == "movie":
        if query.imdb_id and "imdbid" in supported_params:
            params["imdbid"] = query.imdb_id.removeprefix("tt")
        if query.tmdb_id and "tmdbid" in supported_params:
            params["tmdbid"] = str(query.tmdb_id)
    if q:
        params["q"] = q
    if cats:
        params["cat"] = ",".join(str(c) for c in cats)
    params["extended"] = "1"
    return params


class NewznabClient:
    def __init__(
        self,
        base_url: str,
        api_path: str,
        api_key: str | None,
        *,
        protocol: str,
        source_id: str,
        origin: str = "direct",
        timeout: float = 15.0,
        private: bool | None = None,
        trace_header: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        path = api_path or "/api"
        self.api_path, _, query = path.partition("?")
        self.fixed_params = dict(parse_qsl(query))  # Spotweb: ?page=newznabapi
        if not self.api_path.startswith("/"):
            self.api_path = "/" + self.api_path
        self.api_key = api_key
        self.protocol = protocol
        self.source_id = source_id
        self.origin = origin
        self.timeout = timeout
        self.private = private
        self.trace_header = trace_header

    def _get(self, params: dict[str, str]) -> tuple[Element, int]:
        p = dict(self.fixed_params)
        p.update(params)
        if self.api_key:
            p["apikey"] = self.api_key
        headers = {"Accept": "application/rss+xml, application/xml, text/xml"}
        if self.trace_header:
            headers["X-MSO-Trace"] = self.trace_header
        start = time.monotonic()
        try:
            with http_client(self.base_url, timeout=self.timeout, headers=headers) as c:
                resp = c.get(self.api_path, params=p)
        except UnsafeTarget as exc:
            raise SourceError("error", f"Indexer-URL geweigerd: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise SourceError("timeout", f"Time-out na {self.timeout:.0f} s") from exc
        except httpx.HTTPError as exc:
            raise SourceError("error", redact(f"Verbinding mislukt: {type(exc).__name__}")) from exc
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code in (401, 403):
            raise SourceError("auth_error", "Indexer weigert de API-sleutel")
        if resp.status_code == 429:
            raise SourceError("rate_limited", "Indexer meldt: te veel verzoeken (429)")
        if resp.status_code >= 400:
            try:  # many indexers send an <error/> body with 4xx/5xx
                root = parse_xml(resp.content)
                if (err := parse_error(root)) is not None:
                    raise err
            except SourceError as exc:
                if exc.status != "error" or "Indexerfout" in exc.message:
                    raise
            raise SourceError("error", f"HTTP {resp.status_code} van indexer")
        return parse_xml(resp.content), latency

    def caps(self) -> tuple[dict[str, Any], int]:
        root, latency = self._get({"t": "caps"})
        return parse_caps(root), latency

    def search(
        self,
        query: SearchQuery,
        *,
        cats: list[int] | None,
        caps: dict[str, Any] | None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[NormalizedRelease], dict[str, int | None]]:
        params = build_params(query, caps, cats)
        params["limit"] = str(limit)
        params["offset"] = str(offset)
        root, _ = self._get(params)
        return parse_items(
            root, source_id=self.source_id, origin=self.origin, protocol=self.protocol, private=self.private
        )

    def rss(self, *, cats: list[int] | None, limit: int = 100) -> list[NormalizedRelease]:
        params = {"t": "search", "limit": str(limit), "extended": "1"}
        if cats:
            params["cat"] = ",".join(str(c) for c in cats)
        root, _ = self._get(params)
        return parse_items(
            root, source_id=self.source_id, origin=self.origin, protocol=self.protocol, private=self.private
        )[0]


def newznab_cats_for(media_category: str | None, caps: dict[str, Any] | None) -> list[int] | None:
    """Newznab categories for a media category, restricted to what the indexer advertises (if known)."""
    if not media_category:
        return None
    wanted = C.newznab_for(media_category)
    if not caps or not caps.get("categories"):
        return wanted
    have = caps_category_ids(caps)
    sel = [c for c in wanted if c in have]
    if not sel:
        # fall back to the parent category if the indexer has it
        parents = {(c // 1000) * 1000 for c in wanted}
        sel = [p for p in sorted(parents) if p in have]
    return sel or wanted


def now_utc() -> datetime:
    return datetime.now(UTC)


def is_auth_like(status: str) -> bool:
    return status == "auth_error"


def safe_host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""
