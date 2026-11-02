"""Mock Newznab / Torznab indexer (test double; never part of the production stack).

Implements ``/api?t=caps|search|tvsearch|movie|get`` per the Newznab spec (RSS 2.0 + ``newznab:attr`` or
``torznab:attr``), with fixture releases for all four MediaGuard categories (normal TV, anime TV, normal
movies,
anime movies) plus a German dub for language-filter tests.

``create_app(apikey='nz-test-key', protocol='usenet'|'torrent', flavor='newznab'|'spotweb', **faults)``.
Faults (also mutable at runtime through ``app.state.faults``): ``auth=fail`` (every key rejected → error 100),
``timeout=<seconds>`` (search sleeps), ``malformed=1`` (broken XML), ``error500=1`` (HTTP 500),
``category_error=1`` (error 201 when ``cat`` is given), ``empty=1`` (no items), ``ratelimit=1`` (error 500
'Request limit reached', HTTP 429), ``no_tvsearch=1`` (caps without tv-search). Every call is recorded in
``app.state.calls`` (apikey removed) with User-Agent and X-MSO-Trace.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import FastAPI, Request, Response

NS_NZ = "http://www.newznab.com/DTD/2010/feeds/attributes/"
NS_TZ = "http://torznab.com/schemas/2015/feed"

FIXTURES: list[dict[str, Any]] = [
    {
        "id": "tv-expanse-s02e05",
        "title": "The.Expanse.S02E05.1080p.WEB-DL.DDP5.1.H.264-NTb",
        "cats": [5000, 5040],
        "size": 2_400_000_000,
        "tvdbid": 280619,
        "imdb": "tt3230854",
        "season": 2,
        "ep": 5,
        "age_d": 3,
        "grabs": 120,
    },
    {
        "id": "tv-expanse-s02e06",
        "title": "The.Expanse.S02E06.720p.HDTV.x264-KILLERS",
        "cats": [5000, 5030],
        "size": 900_000_000,
        "tvdbid": 280619,
        "imdb": "tt3230854",
        "season": 2,
        "ep": 6,
        "age_d": 2,
        "grabs": 40,
    },
    {
        "id": "tv-expanse-s02e05-german",
        "title": "The.Expanse.S02E05.German.1080p.WEB.x264-WvF",
        "cats": [5000, 5040],
        "size": 2_000_000_000,
        "tvdbid": 280619,
        "imdb": "tt3230854",
        "season": 2,
        "ep": 5,
        "age_d": 3,
        "grabs": 7,
    },
    {
        "id": "anime-frieren-05",
        "title": "[SubsPlease] Sousou no Frieren - 05 (1080p) [A1B2C3D4].mkv",
        "cats": [5070, 5000],
        "size": 1_400_000_000,
        "tvdbid": 424536,
        "absolute": 5,
        "age_d": 30,
        "grabs": 300,
    },
    {
        "id": "anime-frieren-06",
        "title": "[Erai-raws] Sousou no Frieren - 06 [1080p][Multiple Subtitle]",
        "cats": [5070],
        "size": 1_300_000_000,
        "tvdbid": 424536,
        "absolute": 6,
        "age_d": 25,
        "grabs": 150,
    },
    {
        "id": "movie-dune2-remux",
        "title": "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos-FGT",
        "cats": [2000, 2045],
        "size": 78_000_000_000,
        "imdb": "tt15239678",
        "tmdbid": 693134,
        "age_d": 60,
        "grabs": 90,
    },
    {
        "id": "movie-dune2-web",
        "title": "Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.H.264-FLUX",
        "cats": [2000, 2040],
        "size": 7_800_000_000,
        "imdb": "tt15239678",
        "tmdbid": 693134,
        "age_d": 70,
        "grabs": 500,
    },
    {
        "id": "amovie-suzume",
        "title": "[Judas] Suzume (2022) [BD 1080p HEVC x265 10bit][Dual-Audio][Eng-Subs]",
        "cats": [2000, 2040, 5070],
        "size": 3_100_000_000,
        "imdb": "tt16428256",
        "tmdbid": 916224,
        "age_d": 200,
        "grabs": 80,
    },
]
CAT_NAMES = {
    2000: "Movies",
    2040: "Movies/HD",
    2045: "Movies/UHD",
    5000: "TV",
    5030: "TV/SD",
    5040: "TV/HD",
    5070: "TV/Anime",
}


def infohash(fid: str) -> str:
    return hashlib.sha1(fid.encode(), usedforsecurity=False).hexdigest()


def torrent_bytes(fid: str, title: str, private: bool = False) -> bytes:
    name = title.encode()
    info = (
        b"d6:lengthi1048576e4:name"
        + str(len(name)).encode()
        + b":"
        + name
        + b"12:piece lengthi262144e6:pieces20:"
        + b"\x00" * 20
        + (b"7:privatei1e" if private else b"")
        + b"e"
    )
    return b"d8:announce30:http://tracker.invalid/announce4:info" + info + b"e"


def nzb_bytes(fid: str, title: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" '
        f'"http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">\n<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
        f'<head><meta type="title">{title}</meta></head><file poster="mock@example.invalid" '
        f'date="1700000000" '
        f'subject="{fid}"><groups><group>alt.binaries.test</group></groups><segments>'
        f'<segment bytes="1000" number="1">{fid}@mock.invalid</segment></segments></file></nzb>'
    ).encode()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def match(
    f: dict[str, Any],
    *,
    q: str | None,
    cats: list[int],
    season: int | None,
    ep: int | None,
    tvdbid: int | None,
    imdbid: str | None,
    tmdbid: int | None,
    func: str,
) -> bool:
    if q:
        title = _norm(f["title"])
        if not all(tok in title.split() or tok in title for tok in _norm(q).split()):
            return False
    if cats:
        have = set(f["cats"]) | {(c // 1000) * 1000 for c in f["cats"]}
        want = set(cats)
        if not have & want:
            return False
    if func == "tvsearch" and not any(5000 <= c < 6000 for c in f["cats"]):
        return False
    if func == "movie" and not any(2000 <= c < 3000 for c in f["cats"]):
        return False
    if season is not None and f.get("season") != season:
        return False
    if ep is not None:
        if season is None and f.get("absolute") is not None:
            if f["absolute"] != ep:
                return False
        elif f.get("ep") != ep:
            return False
    if tvdbid and f.get("tvdbid") != tvdbid:
        return False
    if imdbid and (f.get("imdb") or "").removeprefix("tt").lstrip("0") != imdbid.removeprefix("tt").lstrip(
        "0"
    ):
        return False
    if tmdbid and f.get("tmdbid") != tmdbid:
        return False
    return True


def _error(code: int, desc: str, status: int = 200) -> Response:
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<error code="{code}" description="{desc}"/>'
    return Response(body, media_type="application/xml", status_code=status)


def caps_xml(*, protocol: str, flavor: str, no_tvsearch: bool = False) -> bytes:
    root = ET.Element("caps")
    ET.SubElement(
        root, "server", {"version": "1.0", "title": "Spotweb" if flavor == "spotweb" else "MockIndexer"}
    )
    ET.SubElement(root, "limits", {"max": "100", "default": "50"})
    s = ET.SubElement(root, "searching")
    ET.SubElement(s, "search", {"available": "yes", "supportedParams": "q"})
    ET.SubElement(
        s,
        "tv-search",
        {"available": "no" if no_tvsearch else "yes", "supportedParams": "q,season,ep,tvdbid,imdbid"},
    )
    ET.SubElement(s, "movie-search", {"available": "yes", "supportedParams": "q,imdbid,tmdbid"})
    cats = ET.SubElement(root, "categories")
    m = ET.SubElement(cats, "category", {"id": "2000", "name": "Movies"})
    for cid in (2040, 2045):
        ET.SubElement(m, "subcat", {"id": str(cid), "name": CAT_NAMES[cid]})
    t = ET.SubElement(cats, "category", {"id": "5000", "name": "TV"})
    for cid in (5030, 5040, 5070):
        ET.SubElement(t, "subcat", {"id": str(cid), "name": CAT_NAMES[cid]})
    _ = protocol
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def rss_xml(
    items: list[dict[str, Any]],
    *,
    protocol: str,
    base: str,
    apikey: str,
    offset: int,
    total: int,
    private: bool = False,
    api_path: str = "/api",
) -> bytes:
    ns = NS_TZ if protocol == "torrent" else NS_NZ
    ET.register_namespace("newznab", NS_NZ)
    ET.register_namespace("torznab", NS_TZ)
    root = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(root, "channel")
    ET.SubElement(ch, "title").text = "Mock"
    ET.SubElement(ch, f"{{{ns}}}response", {"offset": str(offset), "total": str(total)})
    now = datetime.now(UTC)
    for f in items:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = f["title"]
        ET.SubElement(it, "guid", {"isPermaLink": "true"}).text = f"{base}/details/{f['id']}"
        sep = "&" if "?" in api_path else "?"
        link = f"{base}{api_path}{sep}t=get&id={f['id']}&apikey={apikey}"
        ET.SubElement(it, "link").text = link
        ET.SubElement(it, "comments").text = f"{base}/details/{f['id']}"
        pub = format_datetime(now - timedelta(days=f["age_d"]))
        ET.SubElement(it, "pubDate").text = pub
        ET.SubElement(it, "size").text = str(f["size"])
        ET.SubElement(
            it,
            "enclosure",
            {
                "url": link,
                "length": str(f["size"]),
                "type": "application/x-bittorrent" if protocol == "torrent" else "application/x-nzb",
            },
        )

        def attr(n: str, v: Any, it: ET.Element = it) -> None:
            ET.SubElement(it, f"{{{ns}}}attr", {"name": n, "value": str(v)})

        for c in f["cats"]:
            attr("category", c)
        attr("size", f["size"])
        if f.get("tvdbid"):
            attr("tvdbid", f["tvdbid"])
        if f.get("imdb"):
            attr("imdb", f["imdb"].removeprefix("tt"))
        if f.get("tmdbid"):
            attr("tmdbid", f["tmdbid"])
        if f.get("season") is not None:
            attr("season", f["season"])
        if f.get("ep") is not None:
            attr("episode", f["ep"])
        attr("grabs", f.get("grabs", 0))
        if protocol == "torrent":
            h = infohash(f["id"])
            seeders = f.get("seeders", 25)
            attr("seeders", seeders)
            attr("peers", seeders + 5)
            attr("infohash", h)
            tracker = (
                "http://tracker.invalid/announce?passkey=SECRETPASSKEY"
                if private
                else "udp://tracker.invalid:1337"
            )
            attr("magneturl", f"magnet:?xt=urn:btih:{h}&dn={f['id']}&tr={tracker}")
            attr("downloadvolumefactor", 1)
            attr("uploadvolumefactor", 1)
        else:
            attr("usenetdate", pub)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def create_app(
    apikey: str = "nz-test-key",
    protocol: str = "usenet",
    flavor: str = "newznab",
    private: str | bool = False,
    **faults: Any,
) -> FastAPI:
    app = FastAPI(title=f"mock {flavor}")
    app.state.faults = dict(faults)
    app.state.calls = []
    app.state.apikey = apikey
    app.state.fixtures = [dict(f) for f in FIXTURES]
    is_private = str(private).lower() in ("1", "true", "yes")
    api_path = "/index.php?page=newznabapi" if flavor == "spotweb" else "/api"

    def handle(request: Request) -> Response:
        fl = app.state.faults
        p = dict(request.query_params)
        if flavor == "spotweb" and p.get("page") != "newznabapi":
            return Response("Spotweb", media_type="text/html")
        key = p.pop("apikey", None)
        app.state.calls.append(
            {
                "params": p,
                "ua": request.headers.get("user-agent"),
                "trace": request.headers.get("x-mso-trace"),
            }
        )
        t = p.get("t", "")
        if fl.get("error500"):
            return Response("Internal Server Error", status_code=500)
        if t == "caps":
            return Response(
                caps_xml(protocol=protocol, flavor=flavor, no_tvsearch=bool(fl.get("no_tvsearch"))),
                media_type="application/xml",
            )
        if fl.get("auth") == "fail" or key != app.state.apikey:
            return _error(100, "Incorrect user credentials")
        if fl.get("ratelimit"):
            return _error(500, "Request limit reached", 429)
        if t == "get":
            f = next((x for x in app.state.fixtures if x["id"] == p.get("id")), None)
            if f is None:
                return _error(300, "No such item")
            if protocol == "torrent":
                return Response(
                    torrent_bytes(f["id"], f["title"], is_private), media_type="application/x-bittorrent"
                )
            return Response(
                nzb_bytes(f["id"], f["title"]),
                media_type="application/x-nzb",
                headers={"Content-Disposition": f'attachment; filename="{f["id"]}.nzb"'},
            )
        if t not in ("search", "tvsearch", "movie"):
            return _error(202, "No such function")
        if t == "tvsearch" and fl.get("no_tvsearch"):
            return _error(203, "Function not available")
        if fl.get("timeout"):
            time.sleep(float(fl["timeout"]))
        if fl.get("malformed"):
            return Response(
                b"<?xml version='1.0'?><rss><channel><item><title>kapot", media_type="application/xml"
            )
        cats = []
        if p.get("cat"):
            if fl.get("category_error"):
                return _error(201, "Incorrect parameter (cat)")
            try:
                cats = [int(c) for c in p["cat"].split(",") if c]
            except ValueError:
                return _error(201, "Incorrect parameter (cat)")

        def num(name: str) -> int | None:
            v = p.get(name)
            return int(v) if v and v.isdigit() else None

        items = (
            []
            if fl.get("empty")
            else [
                f
                for f in app.state.fixtures
                if match(
                    f,
                    q=p.get("q"),
                    cats=cats,
                    season=num("season"),
                    ep=num("ep"),
                    tvdbid=num("tvdbid"),
                    imdbid=p.get("imdbid"),
                    tmdbid=num("tmdbid"),
                    func=t,
                )
            ]
        )
        offset, limit = num("offset") or 0, num("limit") or 50
        page = items[offset : offset + limit]
        base = str(request.base_url).rstrip("/")
        return Response(
            rss_xml(
                page,
                protocol=protocol,
                base=base,
                apikey=key or "",
                offset=offset,
                total=len(items),
                private=is_private,
                api_path=api_path,
            ),
            media_type="application/rss+xml",
        )

    if flavor == "spotweb":
        app.add_api_route("/index.php", handle, methods=["GET"])
    else:
        app.add_api_route("/api", handle, methods=["GET"])
    return app
