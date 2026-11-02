"""Mock Prowlarr API v1 (test double; never part of the production stack).

Endpoints: ``/api/v1/system/status``, ``health``, ``indexer`` (GET/POST/PUT/DELETE), ``indexer/schema``,
``indexerstatus``, ``indexerstats``, ``history``, ``tag``, ``applications``, ``search`` and the download proxy
``/{indexerId}/download?apikey=..&link=..``. Auth via ``X-Api-Key`` header or ``apikey`` query.

Default state mirrors a MediaGuard-managed Prowlarr: tags ``anime`` (1) / ``normal`` (2), four "MediaGuard
· …"
applications, and fixture indexers (normal usenet, anime torrent, an anime-named indexer tagged only
``normal``, and an untagged private tracker).

**Loop simulation**: any indexer added through the API (Newznab/Torznab implementation with a ``baseUrl``
field,
e.g. MSO's own endpoint) is *proxied* like real Prowlarr does: a search fans out to ``{baseUrl}{apiPath}``
with
``apikey`` and a ``Prowlarr/…`` User-Agent (the ``X-MSO-Trace`` header is NOT forwarded, exactly like
Prowlarr).
If MSO then searched this Prowlarr again, the recursion would continue; ``app.state.max_active`` records the
deepest nesting, and nesting beyond 6 returns HTTP 508 so a broken guard fails the test instead of hanging.

Faults (``create_app(**faults)`` or ``app.state.faults``): ``auth=fail``, ``timeout=<seconds>`` (search),
``category_error=1`` (search → HTTP 400 category validation), ``error500=1`` (search → 500), ``malformed=1``,
``user_agent=<ua>`` (User-Agent used when proxying to Newznab indexers; default ``Prowlarr/…``).
"""

from __future__ import annotations

import re
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from defusedxml import ElementTree as DET
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from msomocks import newznab as nz

VERSION = "1.24.3.4754"
UA = f"Prowlarr/{VERSION} (ubuntu 22.04)"


def _fields(base_url: str = "", api_path: str = "/api", api_key: str = "") -> list[dict[str, Any]]:
    return [
        {"name": "baseUrl", "value": base_url},
        {"name": "apiPath", "value": api_path},
        {"name": "apiKey", "value": api_key},
        {"name": "additionalParameters", "value": ""},
    ]


def default_state() -> dict[str, Any]:
    normal_usenet = [f["id"] for f in nz.FIXTURES if 5070 not in f["cats"]]
    anime = [f["id"] for f in nz.FIXTURES if 5070 in f["cats"]]
    return {
        "tags": [{"id": 1, "label": "anime"}, {"id": 2, "label": "normal"}],
        "indexers": [
            {
                "id": 1,
                "name": "NZBgeek",
                "protocol": "usenet",
                "privacy": "private",
                "enable": True,
                "tags": [2],
                "implementation": "Newznab",
                "definitionName": "nzbgeek",
                "priority": 25,
                "appProfileId": 1,
                "fields": _fields("https://api.nzbgeek.info"),
                "_fixtures": normal_usenet,
            },
            {
                "id": 2,
                "name": "Nyaa.si",
                "protocol": "torrent",
                "privacy": "public",
                "enable": True,
                "tags": [1],
                "implementation": "Cardigann",
                "definitionName": "nyaasi",
                "priority": 25,
                "appProfileId": 1,
                "fields": [],
                "_fixtures": anime,
            },
            {
                "id": 3,
                "name": "AnimeTosho",
                "protocol": "usenet",
                "privacy": "public",
                "enable": True,
                "tags": [2],
                "implementation": "Newznab",
                "definitionName": "animetosho",
                "priority": 25,
                "appProfileId": 1,
                "fields": _fields("https://feed.animetosho.org"),
                "_fixtures": anime,
            },
            {
                "id": 4,
                "name": "TorrentLeech",
                "protocol": "torrent",
                "privacy": "private",
                "enable": True,
                "tags": [],
                "implementation": "Cardigann",
                "definitionName": "torrentleech",
                "priority": 25,
                "appProfileId": 1,
                "fields": [],
                "_fixtures": [f for f in normal_usenet],
            },
        ],
        "applications": [
            {
                "id": 1,
                "name": "MediaGuard · Sonarr",
                "implementation": "Sonarr",
                "syncLevel": "fullSync",
                "tags": [2],
            },
            {
                "id": 2,
                "name": "MediaGuard · Sonarr Anime",
                "implementation": "Sonarr",
                "syncLevel": "fullSync",
                "tags": [1],
            },
            {
                "id": 3,
                "name": "MediaGuard · Radarr",
                "implementation": "Radarr",
                "syncLevel": "fullSync",
                "tags": [2],
            },
            {
                "id": 4,
                "name": "MediaGuard · Radarr Anime",
                "implementation": "Radarr",
                "syncLevel": "fullSync",
                "tags": [1],
            },
        ],
        "next_id": 10,
    }


def _public(idx: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in idx.items() if not k.startswith("_")}
    out["fields"] = [
        dict(f, value="********") if f.get("name") == "apiKey" and f.get("value") else dict(f)
        for f in idx.get("fields") or []
    ]
    return out


def _field(idx: dict[str, Any], name: str) -> Any:
    return next((f.get("value") for f in idx.get("fields") or [] if f.get("name") == name), None)


def _parse_query(q: str) -> tuple[str, dict[str, str]]:
    tokens = dict(re.findall(r"\{(\w+):([^}]*)\}", q))
    text = re.sub(r"\{\w+:[^}]*\}", " ", q).strip()
    return text, tokens


def create_app(api_key: str = "prowlarr-test-key", **faults: Any) -> FastAPI:
    app = FastAPI(title="mock prowlarr")
    app.state.faults = dict(faults)
    app.state.data = default_state()
    app.state.calls = []
    app.state.search_calls = 0
    app.state.active = 0
    app.state.max_active = 0
    app.state.callbacks = []
    app.state.callback_errors = []
    lock = threading.Lock()

    def authed(request: Request) -> bool:
        if app.state.faults.get("auth") == "fail":
            return False
        key = request.headers.get("x-api-key") or request.query_params.get("apikey")
        return key == api_key

    def unauthorized() -> Response:
        return Response("Unauthorized", status_code=401)

    @app.middleware("http")
    async def record(request: Request, call_next):  # type: ignore[no-untyped-def]
        app.state.calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": {k: v for k, v in request.query_params.items() if k != "apikey"},
                "trace": request.headers.get("x-mso-trace"),
            }
        )
        return await call_next(request)

    @app.get("/api/v1/system/status")
    def status(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return {"appName": "Prowlarr", "version": VERSION, "branch": "master", "osName": "ubuntu"}

    @app.get("/api/v1/health")
    def health(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return [
            {
                "source": "IndexerStatusCheck",
                "type": "warning",
                "message": "Indexers unavailable due to failures: TorrentLeech",
                "wikiUrl": "",
            }
        ]

    @app.get("/api/v1/tag")
    def tags(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return app.state.data["tags"]

    @app.get("/api/v1/applications")
    def applications(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return app.state.data["applications"]

    @app.get("/api/v1/indexer")
    def indexers(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return [_public(i) for i in app.state.data["indexers"]]

    @app.get("/api/v1/indexer/schema")
    def schema(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return [
            {
                "implementation": impl,
                "implementationName": impl,
                "configContract": f"{impl}Settings",
                "protocol": proto,
                "enable": True,
                "appProfileId": 1,
                "priority": 25,
                "tags": [],
                "fields": _fields(),
                "presets": [{"name": "preset"}],
            }
            for impl, proto in (("Newznab", "usenet"), ("Torznab", "torrent"))
        ]

    @app.post("/api/v1/indexer")
    async def indexer_create(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        body = await request.json()
        with lock:
            body["id"] = app.state.data["next_id"]
            app.state.data["next_id"] += 1
        app.state.data["indexers"].append(body)
        return JSONResponse(_public(body), status_code=201)

    @app.put("/api/v1/indexer/{iid}")
    async def indexer_update(iid: int, request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        body = await request.json()
        for i, idx in enumerate(app.state.data["indexers"]):
            if idx["id"] == iid:
                keep = {k: v for k, v in idx.items() if k.startswith("_")}
                for f in body.get("fields") or []:  # masked key in a PUT keeps the stored key (like Prowlarr)
                    if f.get("name") == "apiKey" and f.get("value") == "********":
                        f["value"] = _field(idx, "apiKey")
                app.state.data["indexers"][i] = {**body, **keep, "id": iid}
                return _public(app.state.data["indexers"][i])
        return JSONResponse({"message": "NotFound"}, status_code=404)

    @app.delete("/api/v1/indexer/{iid}")
    def indexer_delete(iid: int, request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        before = len(app.state.data["indexers"])
        app.state.data["indexers"] = [i for i in app.state.data["indexers"] if i["id"] != iid]
        if len(app.state.data["indexers"]) == before:
            return JSONResponse({"message": "NotFound"}, status_code=404)
        return Response(status_code=200)

    @app.get("/api/v1/indexerstatus")
    def indexer_status(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        return [{"indexerId": 4, "disabledTill": until, "mostRecentFailure": datetime.now(UTC).isoformat()}]

    @app.get("/api/v1/indexerstats")
    def indexer_stats(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return {
            "indexers": [
                {
                    "indexerId": i["id"],
                    "indexerName": i["name"],
                    "averageResponseTime": 300 + 100 * i["id"],
                    "numberOfQueries": 100,
                    "numberOfGrabs": 10,
                    "numberOfFailedQueries": 2 * i["id"],
                    "numberOfFailedGrabs": 0,
                }
                for i in app.state.data["indexers"]
            ]
        }

    @app.get("/api/v1/history")
    def history(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        return {
            "page": 1,
            "pageSize": 20,
            "totalRecords": 1,
            "records": [
                {
                    "id": 1,
                    "indexerId": 1,
                    "eventType": "releaseGrabbed",
                    "successful": True,
                    "date": datetime.now(UTC).isoformat(),
                    "data": {"title": nz.FIXTURES[0]["title"]},
                }
            ],
        }

    def _proxy(
        idx: dict[str, Any], *, text: str, tokens: dict[str, str], stype: str, cats: list[int]
    ) -> list[dict]:
        """Real Prowlarr behaviour for a generic Newznab/Torznab indexer: forward the query to its baseUrl."""
        base = str(_field(idx, "baseUrl") or "").rstrip("/")
        params: dict[str, str] = {
            "t": {"tvsearch": "tvsearch", "movie": "movie"}.get(stype, "search"),
            "apikey": str(_field(idx, "apiKey") or ""),
            "extended": "1",
        }
        if text:
            params["q"] = text
        if cats:
            params["cat"] = ",".join(str(c) for c in cats)
        if "Season" in tokens:
            params["season"] = str(int(tokens["Season"]))
        if "Episode" in tokens:
            params["ep"] = str(int(tokens["Episode"]))
        if "ImdbId" in tokens:
            params["imdbid"] = tokens["ImdbId"].removeprefix("tt")
        app.state.callbacks.append(
            {"indexer": idx["id"], "url": base, "params": {k: v for k, v in params.items() if k != "apikey"}}
        )
        ua = app.state.faults.get("user_agent") or UA
        r = httpx.get(
            base + (_field(idx, "apiPath") or "/api"), params=params, headers={"User-Agent": ua}, timeout=20
        )
        if r.status_code != 200:
            raise RuntimeError(f"indexer {idx['name']} HTTP {r.status_code}")
        root = DET.fromstring(r.content)
        if root.tag == "error":
            raise RuntimeError(f"indexer {idx['name']} error {root.get('code')}")
        out = []
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            attrs = {a.get("name"): a.get("value") for a in item if a.tag.endswith("}attr")}
            cat_ids = [
                int(a.get("value")) for a in item if a.tag.endswith("}attr") and a.get("name") == "category"
            ]
            out.append(
                {
                    "guid": item.findtext("guid"),
                    "indexerId": idx["id"],
                    "indexer": idx["name"],
                    "title": title,
                    "size": int(item.findtext("size") or 0),
                    "publishDate": datetime.now(UTC).isoformat(),
                    "downloadUrl": item.findtext("link"),
                    "protocol": idx.get("protocol", "usenet"),
                    "categories": [{"id": c, "name": nz.CAT_NAMES.get(c, "")} for c in cat_ids],
                    "infoHash": attrs.get("infohash"),
                    "seeders": int(attrs["seeders"]) if attrs.get("seeders") else None,
                }
            )
        return out

    @app.get("/api/v1/search")
    def search(request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        fl = app.state.faults
        app.state.search_calls += 1
        with lock:
            app.state.active += 1
            app.state.max_active = max(app.state.max_active, app.state.active)
            depth = app.state.active
        try:
            if depth > 6:
                return Response("Loop Detected (mock guard)", status_code=508)
            if fl.get("error500"):
                return JSONResponse({"message": "boom"}, status_code=500)
            if fl.get("timeout"):
                time.sleep(float(fl["timeout"]))
            if fl.get("malformed"):
                return Response("<html>not json", media_type="application/json")
            qp = request.query_params
            cats = [int(c) for c in qp.getlist("categories") if c.isdigit()]
            if fl.get("category_error") and cats:
                return JSONResponse(
                    [{"propertyName": "Categories", "errorMessage": "Invalid categories for indexer"}],
                    status_code=400,
                )
            ids = [int(i) for i in qp.getlist("indexerIds") if i.lstrip("-").isdigit()]
            text, tokens = _parse_query(qp.get("query", ""))
            stype = qp.get("type", "search")
            season = int(tokens["Season"]) if "Season" in tokens else None
            ep = int(tokens["Episode"]) if "Episode" in tokens else None
            results: list[dict[str, Any]] = []
            base = str(request.base_url).rstrip("/")
            for idx in app.state.data["indexers"]:
                if not idx.get("enable") or (ids and idx["id"] not in ids):
                    continue
                if "_fixtures" not in idx:
                    if idx.get("implementation") in ("Newznab", "Torznab") and _field(idx, "baseUrl"):
                        try:
                            results += _proxy(idx, text=text, tokens=tokens, stype=stype, cats=cats)
                        except Exception as exc:  # noqa: BLE001  — Prowlarr skips failing indexers
                            app.state.callback_errors.append(f"{idx['name']}: {exc}")
                    continue
                for f in nz.FIXTURES:
                    if f["id"] not in idx["_fixtures"]:
                        continue
                    if not nz.match(
                        f,
                        q=text,
                        cats=cats,
                        season=season,
                        ep=ep,
                        tvdbid=int(tokens["TvdbId"]) if "TvdbId" in tokens else None,
                        imdbid=tokens.get("ImdbId"),
                        tmdbid=int(tokens["TmdbId"]) if "TmdbId" in tokens else None,
                        func=stype,
                    ):
                        continue
                    torrent = idx["protocol"] == "torrent"
                    h = nz.infohash(f["id"])
                    results.append(
                        {
                            "guid": f"{base}/guid/{idx['id']}/{f['id']}",
                            "indexerId": idx["id"],
                            "indexer": idx["name"],
                            "title": f["title"],
                            "size": f["size"],
                            "publishDate": (datetime.now(UTC) - timedelta(days=f["age_d"])).isoformat(),
                            "downloadUrl": f"{base}/{idx['id']}/download?apikey={api_key}&link={f['id']}",
                            "infoUrl": f"{base}/info/{f['id']}",
                            "protocol": idx["protocol"],
                            "categories": [{"id": c, "name": nz.CAT_NAMES.get(c, "")} for c in f["cats"]],
                            "imdbId": int(f["imdb"][2:]) if f.get("imdb") else 0,
                            "tvdbId": f.get("tvdbid", 0),
                            "tmdbId": f.get("tmdbid", 0),
                            "grabs": f.get("grabs"),
                            "seeders": 12 if torrent else None,
                            "leechers": 3 if torrent else None,
                            "infoHash": h if torrent else None,
                            "magnetUrl": f"magnet:?xt=urn:btih:{h}&dn={f['id']}" if torrent else None,
                            "indexerFlags": ["freeleech"] if torrent and idx["id"] == 2 else [],
                        }
                    )
            off, lim = int(qp.get("offset", 0) or 0), int(qp.get("limit", 100) or 100)
            return results[off : off + lim]
        finally:
            with lock:
                app.state.active -= 1

    @app.get("/{iid}/download")
    def download(iid: int, request: Request) -> Any:
        if not authed(request):
            return unauthorized()
        link = request.query_params.get("link", "")
        f = next((x for x in nz.FIXTURES if x["id"] == link), None)
        idx = next((i for i in app.state.data["indexers"] if i["id"] == iid), None)
        if f is None or idx is None:
            return Response("Not found", status_code=404)
        if idx["protocol"] == "torrent":
            return Response(
                nz.torrent_bytes(f["id"], f["title"], idx.get("privacy") == "private"),
                media_type="application/x-bittorrent",
            )
        return Response(
            nz.nzb_bytes(f["id"], f["title"]),
            media_type="application/x-nzb",
            headers={"Content-Disposition": f'attachment; filename="{f["id"]}.nzb"'},
        )

    return app
