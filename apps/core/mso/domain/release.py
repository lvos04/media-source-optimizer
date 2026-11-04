"""Protocol-agnostic release normalisation, parsing and dedupe.

Dedupe is never on title alone: the identity of a release is its protocol-level id
(NZB guid / message-id, torrent infohash v1/v2). Titles are only used to *parse* attributes
(resolution, codec, HDR/DV, audio, episode numbering) with an explicit confidence.
"""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass
class ParsedTitle:
    resolution: int | None = None          # 480/576/720/1080/2160
    source: str | None = None              # web|bluray|remux|hdtv|dvd
    video_codec: str | None = None         # h264|h265|av1|vc1|mpeg2
    hdr: list[str] = field(default_factory=list)  # hdr10, hdr10plus, dv, hlg
    dv_profile: str | None = None
    audio_codecs: list[str] = field(default_factory=list)
    audio_languages: list[str] = field(default_factory=list)  # ISO-ish: en, ja, nl, …
    dual_audio: bool = False
    multi_audio: bool = False
    subtitles: list[str] = field(default_factory=list)
    hardsub: bool = False
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    absolute_episodes: list[int] = field(default_factory=list)
    full_season: bool = False
    special: bool = False
    year: int | None = None
    release_group: str | None = None
    repack: bool = False
    proper: bool = False
    anime_style: bool = False              # "[Group] Title - 05" convention
    obfuscated: bool = False               # random-looking title, no parsable info
    confidence: float = 0.0                # 0..1 how much of the title was understood


@dataclass
class NormalizedRelease:
    protocol: str                          # usenet|torrent
    source_id: str                         # sources.id that returned it
    origin: str                            # prowlarr|direct|spotweb|header_index|mso
    title: str
    guid: str                              # indexer guid (stable per indexer)
    download_url: str | None = None
    info_url: str | None = None
    nzb_id: str | None = None
    message_id: str | None = None
    infohash_v1: str | None = None
    infohash_v2: str | None = None
    size_bytes: int | None = None
    publish_date: datetime | None = None
    categories: list[int] = field(default_factory=list)
    media_category: str | None = None      # tv|tv-anime|movies|movies-anime
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    anidb_id: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    grabs: int | None = None
    files: int | None = None
    private: bool | None = None
    freeleech: bool | None = None
    tracker_status: str | None = None
    retention_days: float | None = None
    est_speed_bps: float | None = None
    indexer_tested_at: datetime | None = None
    confidence: float = 0.5
    ttl_s: int = 3600
    false_positive_flags: list[str] = field(default_factory=list)
    parsed: ParsedTitle = field(default_factory=ParsedTitle)
    trace: list[str] = field(default_factory=list)  # origin graph path this result travelled
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def release_key(self) -> str:
        return release_key(self)

    def age_days(self, now: datetime | None = None) -> float | None:
        if self.publish_date is None:
            return None
        now = now or datetime.now(UTC)
        pd = self.publish_date if self.publish_date.tzinfo else self.publish_date.replace(tzinfo=UTC)
        return max(0.0, (now - pd).total_seconds() / 86400)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["release_key"] = self.release_key
        d["age_days"] = self.age_days()
        return d


# ------------------------------------------------------------------ identity
_BTIH_RE = re.compile(r"urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})", re.I)
_BTMH_RE = re.compile(r"urn:btmh:1220([0-9a-fA-F]{64})", re.I)


def infohashes_from_magnet(magnet: str) -> tuple[str | None, str | None]:
    v1 = v2 = None
    if m := _BTIH_RE.search(magnet):
        h = m.group(1)
        if len(h) == 32:  # base32
            h = base64.b32decode(h.upper()).hex()
        v1 = h.lower()
    if m := _BTMH_RE.search(magnet):
        v2 = m.group(1).lower()
    return v1, v2


def release_key(r: NormalizedRelease) -> str:
    """Protocol-level identity. Torrents: infohash (v1 preferred, else v2). Usenet: message-id, nzb id,
    else indexer guid scoped to its source (never the title)."""
    if r.protocol == "torrent":
        if r.infohash_v1:
            return f"btih:{r.infohash_v1.lower()}"
        if r.infohash_v2:
            return f"btmh:{r.infohash_v2.lower()}"
    else:
        if r.message_id:
            return f"mid:{r.message_id.strip('<>').lower()}"
        if r.nzb_id:
            return f"nzb:{r.nzb_id.lower()}"
    digest = hashlib.sha1(f"{r.source_id}|{r.guid}".encode(), usedforsecurity=False).hexdigest()[:20]
    return f"guid:{digest}"


def dedupe(results: list[NormalizedRelease]) -> list[NormalizedRelease]:
    """Merge results with the same protocol identity; keep the richest record and remember all sources."""
    by_key: dict[str, NormalizedRelease] = {}
    for r in results:
        k = r.release_key
        cur = by_key.get(k)
        if cur is None:
            r.extra.setdefault("also_from", [])
            by_key[k] = r
            continue
        cur.extra.setdefault("also_from", [])
        if r.source_id != cur.source_id and r.source_id not in cur.extra["also_from"]:
            cur.extra["also_from"].append(r.source_id)
        if (r.seeders or 0) > (cur.seeders or 0):
            cur.seeders = r.seeders
        cur.confidence = max(cur.confidence, r.confidence)
    return list(by_key.values())


# ------------------------------------------------------------------ title parsing
_LANG_WORDS = {
    "english": "en", "eng": "en", "japanese": "ja", "jap": "ja", "jpn": "ja", "dutch": "nl", "nl": "nl",
    "nld": "nl", "flemish": "nl", "german": "de", "ger": "de", "french": "fr", "fre": "fr", "vostfr": "fr",
    "spanish": "es", "spa": "es", "italian": "it", "ita": "it", "korean": "ko", "kor": "ko",
    "chinese": "zh", "chi": "zh", "mandarin": "zh", "cantonese": "zh",
}
_SEP = r"[\s._\-\[\]()]"
RE_RES = re.compile(r"(?<![0-9])(2160|1080|720|576|480)[pi](?![0-9])|\b(4k|uhd)\b", re.I)
RE_SXXEXX = re.compile(r"\bS(\d{1,2})[\s._-]?E(\d{1,4})(?:[\s._-]?-?E?(\d{1,4}))*", re.I)
RE_MULTI_E = re.compile(r"E(\d{1,4})", re.I)
RE_SEASON_ONLY = re.compile(r"\b(?:S(\d{1,2})(?![\s._-]?E\d)|Season[\s._-]?(\d{1,2}))\b(?!.*\bE\d)", re.I)
RE_ANIME = re.compile(r"^\[(?P<group>[^\]]+)\][\s._]*(?P<title>.+?)[\s._]+-[\s._]+(?P<ep>\d{1,4})(?:v\d)?"
                      r"(?:[\s._]*-[\s._]*(?P<ep2>\d{1,4}))?(?=[\s._\[(]|$)")
RE_ANIME_BATCH = re.compile(r"^\[(?P<group>[^\]]+)\].+?(?P<a>\d{1,4})[\s._]*[-~][\s._]*(?P<b>\d{1,4})[\s._]*"
                            r"(?:\(|\[|batch|complete)", re.I)
RE_YEAR = re.compile(r"(?<![0-9])(19[2-9]\d|20[0-4]\d)(?![0-9p])")
RE_GROUP = re.compile(r"-([A-Za-z0-9]+)(?:\.[a-z0-9]{2,4})?$")
RE_DV_PROFILE = re.compile(r"\bDV(?:[\s._-]?P?(\d)(?:\.(\d))?)?\b|dovi|dolby[\s._-]?vision", re.I)


def _has(text: str, *words: str) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){w}(?![a-z0-9])", text, re.I) for w in words)


def parse_title(title: str) -> ParsedTitle:
    p = ParsedTitle()
    t = title.strip()
    understood = 0
    if m := RE_RES.search(t):
        p.resolution = int(m.group(1)) if m.group(1) else 2160
        understood += 1
    low = t.lower()
    if _has(low, "remux"):
        p.source = "remux"
    elif _has(low, "bluray", "blu-ray", "bdrip", "brrip", "bd"):
        p.source = "bluray"
    elif _has(low, "web-dl", "webdl", "webrip", "web", "amzn", "nf", "dsnp", "hmax", "atvp", "cr"):
        p.source = "web"
    elif _has(low, "hdtv", "pdtv"):
        p.source = "hdtv"
    elif _has(low, "dvdrip", "dvd"):
        p.source = "dvd"
    if p.source:
        understood += 1
    if _has(low, "av1"):
        p.video_codec = "av1"
    elif _has(low, "x265", "h265", "h.265", "hevc"):
        p.video_codec = "h265"
    elif _has(low, "x264", "h264", "h.264", "avc"):
        p.video_codec = "h264"
    elif _has(low, "vc-1", "vc1"):
        p.video_codec = "vc1"
    elif _has(low, "mpeg2", "mpeg-2"):
        p.video_codec = "mpeg2"
    if p.video_codec:
        understood += 1
    if _has(low, "hdr10plus", r"hdr10\+"):
        p.hdr.append("hdr10plus")
    if _has(low, "hdr10", "hdr") and "hdr10plus" not in p.hdr:
        p.hdr.append("hdr10")
    if _has(low, "hlg"):
        p.hdr.append("hlg")
    if m := RE_DV_PROFILE.search(t):
        p.hdr.append("dv")
        if m.groups() and m.group(1):
            p.dv_profile = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
    for codec, words in (("truehd", ("truehd",)), ("atmos", ("atmos",)), ("dts-hd", ("dts-hd", "dtshd", "dts-ma")),
                         ("dts", ("dts",)), ("eac3", ("eac3", "ddp", "dd+", r"ddp5\.1", "e-ac-3")),
                         ("ac3", ("ac3", "dd5.1", r"dd5\.1")), ("aac", ("aac",)), ("flac", ("flac",)),
                         ("opus", ("opus",))):
        if _has(low, *words):
            p.audio_codecs.append(codec)
    p.dual_audio = _has(low, "dual audio", "dual-audio", "dualaudio", "dual")
    p.multi_audio = bool(re.search(r"(?<![a-z0-9])multi(?![a-z0-9])(?![\s._-]?subs?)", low))
    for word, code in _LANG_WORDS.items():
        if len(word) <= 3 and word not in ("eng", "jpn", "nld", "ger", "fre", "spa", "ita", "kor", "chi", "jap"):
            continue
        if _has(low, word) and code not in p.audio_languages:
            p.audio_languages.append(code)
    if _has(low, "nlsubs", "nl subs", "dutch subs", "nlsub"):
        p.subtitles.append("nl")
    if _has(low, "multi-subs?", "multisubs?", r"multi[\s._]subs?"):
        p.subtitles.append("multi")
    p.hardsub = _has(low, "hardsub", "hardsubbed", "hc")
    p.repack = _has(low, "repack", "rerip")
    p.proper = _has(low, "proper")

    # numbering
    if m := RE_SXXEXX.search(t):
        p.season = int(m.group(1))
        p.episodes = sorted({int(x) for x in RE_MULTI_E.findall(m.group(0))})
        if len(p.episodes) == 2 and re.search(r"E\d+\s*-\s*E?\d+", m.group(0), re.I):
            a, b = p.episodes
            p.episodes = list(range(a, b + 1)) if 0 < b - a < 200 else p.episodes
        understood += 2
    elif m := RE_ANIME.match(t):
        p.anime_style = True
        p.release_group = m.group("group").strip()
        a = int(m.group("ep"))
        b = int(m.group("ep2")) if m.group("ep2") else a
        p.absolute_episodes = list(range(a, b + 1)) if 0 <= b - a < 2000 else [a]
        understood += 2
    elif m := RE_ANIME_BATCH.match(t):
        p.anime_style = True
        p.release_group = m.group("group").strip()
        a, b = int(m.group("a")), int(m.group("b"))
        if 0 < b - a < 2000:
            p.absolute_episodes = list(range(a, b + 1))
            p.full_season = True
        understood += 1
    elif m := RE_SEASON_ONLY.search(t):
        p.season = int(m.group(1) or m.group(2))
        p.full_season = True
        understood += 1
    if t.startswith("[") and not p.anime_style and re.match(r"^\[[^\]]+\]", t):
        p.anime_style = True
    if p.season == 0 or _has(low, "special", "ova", "oad", "ona"):
        p.special = True
    if m := RE_YEAR.search(t):
        p.year = int(m.group(1))
    if p.release_group is None and (m := RE_GROUP.search(t)):
        p.release_group = m.group(1)

    # obfuscation: long token with no separators and nothing understood (e.g. 'a8f3k2l0q9...')
    stem = re.sub(r"\.(nzb|mkv|mp4|avi|par2|rar)$", "", t, flags=re.I)
    if understood == 0 and (re.fullmatch(r"[A-Za-z0-9]{16,}", stem) or re.fullmatch(r"[a-f0-9\-]{24,}", stem, re.I)):
        p.obfuscated = True
    p.confidence = round(min(1.0, understood / 6), 2)
    return p


# ------------------------------------------------------------------ quality gates (MediaGuard leading)
@dataclass
class QualityRequirements:
    """Minimum requirements for a fallback candidate. Built from MediaGuard's wanted item when available;
    MSO never relaxes them (integrity.respect_mediaguard_quality is locked)."""
    media_category: str
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    absolute_episodes: list[int] = field(default_factory=list)
    min_resolution: int | None = None
    max_resolution: int | None = None
    require_hdr: list[str] = field(default_factory=list)      # e.g. ["dv"] when the library item is DV
    forbid_hdr: list[str] = field(default_factory=list)
    required_audio_languages: list[str] = field(default_factory=list)
    require_dual_audio: bool = False
    required_subtitles: list[str] = field(default_factory=list)
    forbid_hardsub: bool = True
    year: int | None = None


def check_quality(r: NormalizedRelease, req: QualityRequirements) -> list[str]:
    """Return a list of Dutch rejection reasons (empty = acceptable). Unknown attributes are treated as
    *not proven* for hard requirements (conservative)."""
    p = r.parsed
    reasons: list[str] = []
    if r.media_category and r.media_category != req.media_category:
        reasons.append(f"verkeerde categorie ({r.media_category} ≠ {req.media_category})")
    anime = req.media_category.endswith("anime")
    if req.media_category.startswith("tv"):
        if anime and req.absolute_episodes:
            if p.absolute_episodes:
                if not set(req.absolute_episodes) <= set(p.absolute_episodes):
                    reasons.append("absolute afleveringsnummering komt niet overeen")
            elif p.season is not None and p.episodes:
                if req.season is not None and (p.season != req.season or not set(req.episodes) <= set(p.episodes)):
                    reasons.append("seizoen/aflevering komt niet overeen")
            elif not p.full_season:
                reasons.append("afleveringsnummer onbekend")
        elif req.episodes:
            if p.season is None and not p.absolute_episodes:
                reasons.append("seizoen/aflevering onbekend")
            elif p.season is not None and req.season is not None and p.season != req.season:
                reasons.append(f"verkeerd seizoen (S{p.season:02d})")
            elif p.episodes and not set(req.episodes) <= set(p.episodes):
                reasons.append("verkeerde aflevering")
    if req.year and p.year and abs(p.year - req.year) > 1 and req.media_category.startswith("movies"):
        reasons.append(f"verkeerd jaar ({p.year})")
    if req.min_resolution:
        if p.resolution is None:
            reasons.append("resolutie onbekend")
        elif p.resolution < req.min_resolution:
            reasons.append(f"resolutie te laag ({p.resolution}p)")
    if req.max_resolution and p.resolution and p.resolution > req.max_resolution:
        reasons.append(f"resolutie te hoog ({p.resolution}p)")
    for h in req.require_hdr:
        if h not in p.hdr:
            reasons.append(f"{h.upper()} vereist")
    for h in req.forbid_hdr:
        if h in p.hdr:
            reasons.append(f"{h.upper()} niet toegestaan")
    for lang in req.required_audio_languages:
        if lang not in p.audio_languages and not (p.dual_audio and anime and lang in ("en", "ja")) \
                and not p.multi_audio:
            reasons.append(f"audiotaal '{lang}' niet aangetoond")
    if req.require_dual_audio and not (p.dual_audio or p.multi_audio):
        reasons.append("dual audio vereist")
    for sub in req.required_subtitles:
        if sub not in p.subtitles and "multi" not in p.subtitles:
            reasons.append(f"ondertitels '{sub}' niet aangetoond")
    if req.forbid_hardsub and p.hardsub:
        reasons.append("hardsubs niet toegestaan")
    if p.obfuscated:
        reasons.append("geobfusceerde titel: inhoud niet te verifiëren")
    return reasons


def magnet_name(magnet: str) -> str | None:
    try:
        q = parse_qs(urlsplit(magnet).query)
    except ValueError:
        return None
    return (q.get("dn") or [None])[0]


# ------------------------------------------------------------------ title relevance (search ranking)
# Where the "name" part of a release title ends: numbering, year, resolution or a quality/source token.
_TITLE_CUT_RE = re.compile(
    r"(?<![a-z0-9])(?:S\d{1,2}[\s._-]?E\d{1,4}|S\d{1,2}(?![a-z0-9])|Season[\s._-]?\d|E\d{2,4}(?![a-z0-9])"
    r"|19[2-9]\d|20[0-4]\d|\d{3,4}[pi]|4k|uhd|complete|bluray|blu-ray|bdrip|brrip|bdremux|remux|web-?dl"
    r"|webrip|web|hdtv|dvdrip|dvd|x26[45]|h\.?26[45]|hevc|avc|xvid|proper|repack|multi|dual|dubbed)"
    r"(?![a-z0-9])",
    re.I,
)
_STOPWORDS = {"the", "a", "an"}
_YEAR_TOKEN_RE = re.compile(r"^(19[2-9]\d|20[0-4]\d)$")


def title_part(title: str) -> str:
    """The name part of a release title: without leading ``[group]`` tags and without everything from the
    first numbering/year/quality token on. ``'[HR] Spirited Away (2001) [1080p]'`` → ``'Spirited Away'``;
    ``'Dragon Ball Z S01E32 Spirit Bomb Away 1080p'`` → ``'Dragon Ball Z'``."""
    t = (title or "").strip()
    if m := RE_ANIME.match(t):
        return m.group("title").strip(" -._")
    t = re.sub(r"^(?:\s*[\[(【][^\])】]*[\])】]\s*)+", "", t)
    t = re.sub(r"[._]+", " ", t)
    for m in _TITLE_CUT_RE.finditer(t):
        if m.start() > 0 and t[: m.start()].strip(" -([{"):
            t = t[: m.start()]
            break
    t = re.split(r"[\[({【]", t, maxsplit=1)[0] or t
    return t.strip(" -:,")


def _norm_tokens(text: str) -> list[str]:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "").replace("\u2019", "")
    return [w for w in re.findall(r"[a-z0-9]+", s) if w not in _STOPWORDS]


def query_year(query: str | None) -> int | None:
    """A year typed in the search text (``'Dune 2021'``) or None."""
    years = [int(w) for w in _norm_tokens(query or "") if _YEAR_TOKEN_RE.match(w)]
    return years[-1] if years else None


def title_similarity(query: str | None, title: str) -> float | None:
    """0..1 how well the asked title matches the release's *name part* (normalised token match). None when
    there is no text query (id-only or category browse). Episode titles after ``S01E32`` never count: only
    the series/film name before the numbering/year/quality part is compared."""
    q = [w for w in _norm_tokens(query or "") if not _YEAR_TOKEN_RE.match(w)]
    if not q:
        return None
    t = _norm_tokens(title_part(title))
    if not t:
        return 0.0
    if q == t:
        return 1.0
    pool = list(t)
    matched = 0
    for w in q:
        if w in pool:
            pool.remove(w)
            matched += 1
    if not matched:
        return 0.0
    coverage = matched / len(q)
    precision = matched / len(t)
    return round(coverage * (0.75 + 0.25 * precision), 2)


def is_episodic(p: ParsedTitle) -> bool:
    """Parsed as a TV episode / season pack / absolute-numbered anime episode (never a single film)."""
    return bool(p.episodes or p.absolute_episodes or (p.season is not None and p.full_season))
