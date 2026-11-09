"""Release gate: MSO never sends a fake (malware-like) release itself.

Fake releases copy real titles and real group names ("Criminal Minds S19E06 1080p WEB h264-GRACE.exe",
"... x265-MeGusta.scr"), so blocking groups does not help. What gives them away is the payload: a media download
never contains a program, script or shortcut, and a 1080p episode is never a few megabytes. The rules are the
same as the post-download scan (:func:`mso.features.torrent.diagnostics.payload_scan`), so a release that passes
this gate is never flagged as a suspicious torrent after it arrives in qBittorrent. One deliberate exception: the
BD-J menu programs of a real Blu-ray disc layout (``BDMV/JAR/*.jar``) are not held against a release here.

- :func:`executable_hint` — programs/scripts/shortcuts and double extensions (``.mkv.exe``) in a release name
  and/or file list (the strong signals; used for search flags and candidate rejection).
- :func:`size_problem` — an absurdly small size for the claimed resolution (≥ 720p episode < 50 MB).
- :func:`check_download` — the pre-send check on what was actually fetched: the .torrent file list (parsed with
  ``crossseed.parse_torrent``), the magnet name, or the file names in an NZB. Raises :class:`Refused`.

Pure functions; nothing here writes anything.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from defusedxml import ElementTree as SafeET

from mso.domain.release import NormalizedRelease, ParsedTitle, is_episodic, magnet_name
from mso.features.torrent import diagnostics

MIB = 1024 * 1024
MIN_RESOLUTION = 720  # sizes are only judged for releases that claim at least 720p
MIN_EPISODE_BYTES = 50 * MIB  # a ≥ 720p episode below this is not a real episode
MIN_MOVIE_BYTES = 200 * MIB  # a ≥ 720p film below this is not a real film
MAX_FILES = 5000  # file names collected from one .torrent (payload_scan itself checks the first 2000)
MAX_NZB_NAMES = 2000
WEAK_EXT = {"zipx"}  # unusual archive: suspicious in a media download, but not a program
_QUOTED_RE = re.compile(r"\"([^\"/\\]{1,300})\"")
# Blu-ray Java (BD-J) menus: every full disc with interactive menus has BDMV/JAR/<id>.jar. Not a PC program (the
# player runs it in its own sandbox), so a disc layout is not a fake.
_BDJ_RE = re.compile(r"(?i)(?:^|[/\\])BDMV[/\\]JAR[/\\](?:[^/\\]+[/\\])*[^/\\]+\.jar$")
_BDMV_RE = re.compile(r"(?i)(?:^|[/\\])BDMV[/\\](?:index\.bdmv|STREAM[/\\][^/\\]+\.m2ts)$")


class Refused(Exception):
    """The pre-send check refuses this download. ``reasons`` are Dutch, ready for "Beleid weigert deze release:
    …"; ``content`` is True when the evidence comes from the fetched file list itself (then the release is a
    proven fake, not just unreadable); ``keys`` are the protocol identities read from the file."""

    def __init__(
        self,
        reasons: list[str],
        *,
        content: bool = False,
        keys: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.content = content
        self.keys = keys or []
        self.evidence = evidence or {}

    @property
    def message(self) -> str:
        return "Beleid weigert deze release: " + "; ".join(self.reasons)


# ------------------------------------------------------------------ names
def _base(name: str) -> str:
    base = re.split(r"[/\\]", (name or "").strip())[-1].strip()
    return base if len(base) <= 120 else "…" + base[-119:]


def is_strong(match: dict[str, Any]) -> bool:
    """A program/script/shortcut (incl. a double extension), as opposed to the weaker archive/password hints."""
    return str(match.get("ext") or "") in diagnostics.EXEC_EXT


def _double(match: dict[str, Any]) -> str | None:
    if not str(match.get("reason") or "").startswith("dubbele extensie"):
        return None
    ex = diagnostics._exts(str(match.get("name") or ""))
    return f".{ex[-2]}.{ex[-1]}" if len(ex) >= 2 else None


def describe(match: dict[str, Any]) -> str:
    """One Dutch reason per match, e.g. ``bevat uitvoerbaar bestand "x.exe"``."""
    ext = str(match.get("ext") or "")
    base = _base(str(match.get("name") or ""))
    double = _double(match)
    if match.get("where") == "naam":
        if double:
            return f"releasenaam vermomt een uitvoerbaar bestand als video ({double})"
        if ext in diagnostics.EXEC_EXT:
            return f"releasenaam is een uitvoerbaar bestand (.{ext})"
        return f"releasenaam is een ongebruikelijk archief (.{ext})"
    if ext in diagnostics.EXEC_EXT:
        return f'bevat uitvoerbaar bestand "{base}"' + (" (dubbele extensie, vermomd als video)" if double else "")
    if ext in WEAK_EXT:
        return f'bevat ongebruikelijk archief "{base}"'
    return f'bevat wachtwoordhint "{base}" naast een archief (beveiligd archief)'


def _file_dicts(files: Iterable[Any] | None) -> list[dict[str, Any]]:
    """``{name}`` dicts for ``payload_scan``, without the BD-J menu programs of a real Blu-ray disc layout
    (``BDMV/JAR/*.jar`` next to ``BDMV/index.bdmv`` or ``BDMV/STREAM/*.m2ts``)."""
    out = [{"name": str(f.get("name") or "")} if isinstance(f, dict) else {"name": str(f)} for f in files or []]
    if any(_BDMV_RE.search(f["name"]) for f in out):
        out = [f for f in out if not _BDJ_RE.search(f["name"])]
    return out


def is_media_release(title: str | None, media_category: str | None = None) -> bool:
    """A media download (known media category or a release-like name): only those are judged, like the
    post-download scan — a software download with a setup.exe is not a fake."""
    return diagnostics.looks_like_media(title or "", media_category)


def _media(title: str | None, media: bool | None, media_category: str | None) -> bool:
    return media if media is not None else is_media_release(title, media_category)


def executable_hint(
    title: str | None,
    files: Iterable[Any] | None = None,
    *,
    media: bool | None = None,
    media_category: str | None = None,
) -> list[dict[str, Any]]:
    """Programs, scripts, shortcuts and double extensions (``.mkv.exe``) in a release name and/or file list.

    Returns the matching ``payload_scan`` entries (``{where: naam|bestand, name, ext, reason}``); empty = clean.
    ``files``: names or ``{name: …}`` dicts. Only media downloads are judged (``media``; default: a known media
    category or a release-like name), exactly like the post-download scan: software with a setup.exe is not a fake.
    """
    scan = diagnostics.payload_scan(title or "", _file_dicts(files), media=_media(title, media, media_category))
    return [m for m in scan["matches"] if is_strong(m)]


def payload_problems(
    name: str, files: Iterable[Any] | None, *, media: bool | None = None, media_category: str | None = None
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Everything the post-download scan would flag (programs, double extensions, unusual archives, password hints
    next to an archive). Returns ``(reasons, matches, files_checked)``."""
    names = list(files or [])
    scan = diagnostics.payload_scan(name or "", _file_dicts(names), media=_media(name, media, media_category))
    matches = list(scan["matches"])
    return [describe(m) for m in matches], matches, min(len(names), diagnostics.PAYLOAD_MAX_FILES)


# ------------------------------------------------------------------ size sanity
def _size_text(n: int) -> str:
    return f"{n / MIB:.1f} MB" if n < 10 * MIB else f"{n / MIB:.0f} MB"


def size_problem(parsed: ParsedTitle, size_bytes: int | None, media_category: str | None = None) -> str | None:
    """An absurd size for the claimed resolution: a ≥ 720p episode under 50 MB or a ≥ 720p film under 200 MB.
    Unknown size or resolution: no verdict (other rules decide)."""
    if not size_bytes or size_bytes <= 0 or not parsed.resolution or parsed.resolution < MIN_RESOLUTION:
        return None
    episode = (bool(parsed.episodes) or bool(parsed.absolute_episodes)) and not parsed.full_season
    movie = not is_episodic(parsed) and (
        (media_category or "").startswith("movies") or (media_category is None and parsed.year is not None)
    )
    if episode and size_bytes < MIN_EPISODE_BYTES:
        what, minimum = "aflevering", MIN_EPISODE_BYTES
    elif movie and size_bytes < MIN_MOVIE_BYTES:
        what, minimum = "film", MIN_MOVIE_BYTES
    else:
        return None
    return (
        f"onwaarschijnlijk klein voor een {parsed.resolution}p-{what} ({_size_text(size_bytes)}; "
        f"minimaal {minimum // MIB} MB verwacht): vrijwel zeker nep"
    )


def release_problems(r: NormalizedRelease, *, media_category: str | None = None) -> list[str]:
    """Title/size verdict for a search result or candidate (no download needed). Empty = no objection."""
    cat = media_category or r.media_category
    out = [describe(m) for m in executable_hint(r.title, media=True if cat else None, media_category=cat)]
    if (sp := size_problem(r.parsed, r.size_bytes, cat)) is not None:
        out.append(sp)
    return out


# ------------------------------------------------------------------ fetched content
def _s(v: Any) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes | bytearray) else str(v)


def _walk_info(info: dict[bytes, Any]) -> tuple[str, list[str], bool, bool]:
    """(name, file paths, has v1 layout, has v2 file tree) of a decoded ``info`` dict — v1, v2 and hybrid."""
    name = _s(info.get(b"name.utf-8") or info.get(b"name") or b"")
    files: list[str] = []
    v1 = False
    if isinstance(info.get(b"files"), list):
        v1 = True
        for f in info[b"files"]:
            if len(files) >= MAX_FILES:
                break
            if not isinstance(f, dict):
                continue
            parts = f.get(b"path.utf-8") or f.get(b"path") or []
            if isinstance(parts, list):
                files.append("/".join([name, *(_s(p) for p in parts if isinstance(p, bytes | bytearray))]))
    elif b"length" in info:
        v1 = True
        files.append(name)
    tree = info.get(b"file tree")
    v2 = isinstance(tree, dict)
    if v2 and not v1:

        def walk(node: dict[bytes, Any], prefix: str) -> None:
            for k, v in node.items():
                if len(files) >= MAX_FILES:
                    return
                if k == b"":
                    files.append(prefix)
                elif isinstance(v, dict):
                    walk(v, f"{prefix}/{_s(k)}")

        walk(tree, name)
    return name, files, v1, v2


def torrent_payload(data: bytes) -> dict[str, Any]:
    """Name, file list and identities (``btih:``/``btmh:``) of a .torrent. Uses ``crossseed.parse_torrent``; v2-only
    and unusual layouts are read from the decoded dict directly. Raises :class:`Refused` when unreadable."""
    from mso.features.torrent import crossseed

    unreadable = {"kind": "unreadable"}
    unchecked = "de bestanden zijn niet te controleren"
    if not data:
        raise Refused([f"leeg torrentbestand ontvangen; {unchecked}"], evidence=unreadable)
    try:
        meta, raw_info = crossseed.bdecode(data)
    except crossseed.BencodeError as exc:
        # no parser detail in the reason: it can quote raw bytes of the file (announce URLs carry passkeys)
        raise Refused([f"geen geldig torrentbestand ontvangen; {unchecked}"], evidence=unreadable) from exc
    info = meta.get(b"info") if isinstance(meta, dict) else None
    if not isinstance(info, dict) or raw_info is None:
        raise Refused([f"torrentbestand zonder bestandsinformatie; {unchecked}"], evidence=unreadable)
    keys: list[str] = []
    try:
        parsed = crossseed.parse_torrent(data)
        name = str(parsed["name"])
        files = [str(n) for n, _ in parsed["files"]][:MAX_FILES]
        keys.append(f"btih:{parsed['infohash']}")
        if isinstance(info.get(b"file tree"), dict):  # hybrid v1+v2
            keys.append(f"btmh:{hashlib.sha256(raw_info).hexdigest()}")
    except (crossseed.BencodeError, KeyError, TypeError, ValueError, AttributeError, IndexError):
        name, files, v1, v2 = _walk_info(info)
        if v1:
            keys.append(f"btih:{hashlib.sha1(raw_info, usedforsecurity=False).hexdigest()}")
        if v2:
            keys.append(f"btmh:{hashlib.sha256(raw_info).hexdigest()}")
    if not files:
        raise Refused([f"torrentbestand zonder bestandenlijst; {unchecked}"], evidence=unreadable)
    return {"name": name, "files": files, "keys": keys}


def nzb_file_names(data: bytes) -> list[str]:
    """File names quoted in the NZB ``<file subject=…>`` attributes (``[1/9] - "Show.S01E01.part01.rar" yEnc
    (1/80)``). Parsed with defusedxml (no entity expansion, no external references); unquoted (obfuscated) subjects
    are skipped. An unreadable NZB yields no names: the SABnzbd connector refuses invalid NZBs itself."""
    try:
        root = SafeET.fromstring(data or b"", forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except Exception:
        return []
    names: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag.rsplit("}", 1)[-1] != "file":
            continue
        for q in _QUOTED_RE.findall(el.get("subject") or ""):
            q = q.strip()
            if q and q not in names:
                names.append(q)
        if len(names) >= MAX_NZB_NAMES:
            break
    return names


@dataclass
class Checked:
    """What the pre-send check looked at (for audit/logging)."""

    kind: str  # files|magnet|nzb
    files_checked: int = 0
    keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "files_checked": self.files_checked, "keys": self.keys}


def _uniq(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def check_download(
    protocol: str,
    fetched: Any,
    *,
    title: str,
    media: bool | None = None,
    media_category: str | None = None,
) -> Checked:
    """Pre-send check on what MSO is about to hand to the download client (``fetched`` = search ``Fetched``:
    ``content`` bytes and/or ``final_url`` for a magnet). Raises :class:`Refused`.

    - .torrent: the file list is parsed and checked like the post-download scan (programs, double extensions,
      unusual archives, password hints next to an archive). An unreadable .torrent is refused: its files cannot
      be checked (qBittorrent could not use it either).
    - magnet: no file list before the metadata arrives → the release title and the magnet's ``dn`` name.
    - NZB: the release title and the file names in the NZB subjects (programs and double extensions)."""
    is_media = _media(title, media, media_category)
    title_hits = executable_hint(title, media=is_media)
    if protocol == "usenet":
        names = nzb_file_names(bytes(getattr(fetched, "content", b"") or b""))
        # an obfuscated title without a category says nothing: release-like file names make it media as well
        hits = executable_hint("", names, media=is_media or any(is_media_release(n) for n in names))
        if title_hits or hits:
            found = title_hits + hits
            raise Refused(
                _uniq([describe(m) for m in found]),
                content=bool(hits),
                evidence={"kind": "nzb", "matches": found[:20], "files_checked": len(names)},
            )
        return Checked("nzb", files_checked=len(names))
    final_url = getattr(fetched, "final_url", None)
    if final_url:
        dn = magnet_name(final_url) or ""
        dn_hits = executable_hint(dn, media=is_media or is_media_release(dn)) if dn else []
        if title_hits or dn_hits:
            found = title_hits + dn_hits
            raise Refused(_uniq([describe(m) for m in found]), evidence={"kind": "magnet", "matches": found[:20]})
        return Checked("magnet")
    payload = torrent_payload(bytes(getattr(fetched, "content", b"") or b""))
    problems, matches, n = payload_problems(
        payload["name"], payload["files"], media=is_media or is_media_release(payload["name"])
    )
    if title_hits or matches:
        found = title_hits + matches
        raise Refused(
            _uniq([describe(m) for m in title_hits] + problems),
            content=bool(matches),
            keys=payload["keys"],
            evidence={"kind": "files", "matches": found[:20], "files_checked": n},
        )
    return Checked("files", files_checked=n, keys=payload["keys"])
