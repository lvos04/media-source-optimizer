"""Newznab / Torznab XML documents (caps, RSS 2.0 results with ``newznab:attr``/``torznab:attr``, errors)."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any
from xml.etree import ElementTree as ET

from mso.config import VERSION
from mso.domain import categories as C
from mso.domain.release import NormalizedRelease

NS = {
    "newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/",
    "torznab": "http://torznab.com/schemas/2015/feed",
}
ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("newznab", NS["newznab"])
ET.register_namespace("torznab", NS["torznab"])
ET.register_namespace("atom", ATOM)

CATEGORY_TREE: list[tuple[int, str, list[tuple[int, str]]]] = [
    (
        2000,
        "Movies",
        [
            (2010, "Movies/Foreign"),
            (2020, "Movies/Other"),
            (2030, "Movies/SD"),
            (2040, "Movies/HD"),
            (2045, "Movies/UHD"),
            (2050, "Movies/BluRay"),
            (2060, "Movies/3D"),
            (2070, "Movies/DVD"),
            (2080, "Movies/WEB-DL"),
        ],
    ),
    (
        5000,
        "TV",
        [
            (5010, "TV/WEB-DL"),
            (5020, "TV/Foreign"),
            (5030, "TV/SD"),
            (5040, "TV/HD"),
            (5045, "TV/UHD"),
            (5050, "TV/Other"),
            (5060, "TV/Sport"),
            (5070, "TV/Anime"),
            (5080, "TV/Documentary"),
        ],
    ),
]
CAT_NAMES = {cid: name for parent, pname, subs in CATEGORY_TREE for cid, name in [(parent, pname), *subs]}

# Newznab error codes (spec) — description texts as commonly used by Newznab servers
ERRORS = {
    100: "Incorrect user credentials",
    101: "Account suspended",
    102: "Insufficient privileges/not authorized",
    200: "Missing parameter",
    201: "Incorrect parameter",
    202: "No such function",
    203: "Function not available",
    300: "No such item",
    500: "Request limit reached",
    501: "Download limit reached",
    900: "Unknown error",
    910: "API disabled",
}


def _doc(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def error(code: int, detail: str | None = None) -> bytes:
    desc = ERRORS.get(code, "Unknown error")
    if detail:
        desc = f"{desc} ({detail})"
    return _doc(ET.Element("error", {"code": str(code), "description": desc}))


def caps(*, torznab: bool, max_limit: int, default_limit: int = 50) -> bytes:
    root = ET.Element("caps")
    ET.SubElement(
        root,
        "server",
        {
            "version": "1.0",
            "title": "Media Source Optimizer",
            "appversion": VERSION,
            "strapline": "Bronzoeker voor MediaGuard-bibliotheken",
        },
    )
    ET.SubElement(root, "limits", {"max": str(max_limit), "default": str(min(default_limit, max_limit))})
    ET.SubElement(root, "registration", {"available": "no", "open": "no"})
    s = ET.SubElement(root, "searching")
    ET.SubElement(s, "search", {"available": "yes", "supportedParams": "q"})
    ET.SubElement(s, "tv-search", {"available": "yes", "supportedParams": "q,season,ep,tvdbid,imdbid,rid"})
    ET.SubElement(s, "movie-search", {"available": "yes", "supportedParams": "q,imdbid,tmdbid"})
    ET.SubElement(s, "audio-search", {"available": "no", "supportedParams": "q"})
    ET.SubElement(s, "book-search", {"available": "no", "supportedParams": "q"})
    cats = ET.SubElement(root, "categories")
    for parent, pname, subs in CATEGORY_TREE:
        c = ET.SubElement(cats, "category", {"id": str(parent), "name": pname})
        for cid, name in subs:
            ET.SubElement(c, "subcat", {"id": str(cid), "name": name})
    _ = torznab
    return _doc(root)


def item_categories(r: NormalizedRelease) -> list[int]:
    """Standard categories for output; guarantee the parent that matches the MediaGuard category."""
    cats = [c for c in dict.fromkeys(int(x) for x in r.categories) if c in CAT_NAMES]
    mc = r.media_category
    must: list[int] = []
    if mc == "tv-anime":
        must = [C.TV_ANIME, C.TV]
    elif mc == "tv":
        must = [C.TV]
    elif mc in ("movies", "movies-anime"):
        must = [C.MOVIES]
    for m in must:
        if m not in cats:
            cats.append(m)
    if mc in ("tv", "movies"):
        cats = [c for c in cats if c != C.TV_ANIME]  # normal results never advertised as anime
    if mc == "movies-anime" and C.TV_ANIME not in cats and any(c == C.TV_ANIME for c in r.categories):
        cats.append(C.TV_ANIME)
    if not cats:
        cats = [C.TV] if (r.parsed.season is not None or r.parsed.absolute_episodes) else [C.MOVIES]
    return cats


def rss(
    items: list[tuple[NormalizedRelease, str]],
    *,
    torznab: bool,
    offset: int,
    total: int,
    self_url: str,
    title: str = "Media Source Optimizer",
) -> bytes:
    """``items``: (release, download link through MSO)."""
    ns = NS["torznab"] if torznab else NS["newznab"]
    root = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(root, "channel")
    ET.SubElement(ch, f"{{{ATOM}}}link", {"href": self_url, "rel": "self", "type": "application/rss+xml"})
    ET.SubElement(ch, "title").text = title
    ET.SubElement(ch, "description").text = (
        "Media Source Optimizer " + ("Torznab" if torznab else "Newznab") + "-feed"
    )
    ET.SubElement(ch, "link").text = self_url.split("?", 1)[0]
    ET.SubElement(ch, "language").text = "nl-nl"
    ET.SubElement(ch, f"{{{ns}}}response", {"offset": str(offset), "total": str(total)})
    for r, link in items:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = r.title
        ET.SubElement(it, "guid", {"isPermaLink": "false"}).text = r.release_key
        ET.SubElement(it, "link").text = link
        if info := r.extra.get("public_info_url"):
            ET.SubElement(it, "comments").text = info
        pub = r.publish_date or datetime.now(UTC)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
        ET.SubElement(it, "pubDate").text = format_datetime(pub)
        cats = item_categories(r)
        ET.SubElement(it, "category").text = CAT_NAMES.get(cats[0], "Other").replace("/", " > ")
        size = int(r.size_bytes or 0)
        ET.SubElement(it, "size").text = str(size)
        ET.SubElement(it, "description").text = r.title
        ET.SubElement(
            it,
            "enclosure",
            {
                "url": link,
                "length": str(size),
                "type": "application/x-bittorrent" if torznab else "application/x-nzb",
            },
        )

        def attr(name: str, value: Any, it: ET.Element = it) -> None:
            if value is None or value == "":
                return
            ET.SubElement(it, f"{{{ns}}}attr", {"name": name, "value": str(value)})

        for c in cats:
            attr("category", c)
        attr("size", size)
        attr("guid", r.release_key)
        if r.tvdb_id:
            attr("tvdbid", r.tvdb_id)
        if r.tmdb_id:
            attr("tmdbid", r.tmdb_id)
        if r.imdb_id:
            attr("imdb", r.imdb_id.removeprefix("tt"))
            attr("imdbid", r.imdb_id)
        if r.parsed.season is not None:
            attr("season", r.parsed.season)
        if r.parsed.episodes:
            attr("episode", r.parsed.episodes[0])
        elif r.parsed.absolute_episodes:
            attr("episode", r.parsed.absolute_episodes[0])
        if r.grabs is not None:
            attr("grabs", r.grabs)
        if r.files is not None:
            attr("files", r.files)
        if not torznab:
            attr("usenetdate", format_datetime(pub))
        else:
            seeders = r.seeders if r.seeders is not None else 0
            attr("seeders", seeders)
            attr("peers", seeders + (r.leechers or 0))
            if r.infohash_v1:
                attr("infohash", r.infohash_v1)
            if magnet := r.extra.get("public_magnet"):
                attr("magneturl", magnet)
            attr("downloadvolumefactor", 0 if r.freeleech else 1)
            attr("uploadvolumefactor", 1)
    return _doc(root)
