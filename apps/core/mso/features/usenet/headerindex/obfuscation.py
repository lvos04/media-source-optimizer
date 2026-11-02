"""Release-name extraction from Usenet subjects and honest obfuscation detection.

Typical subjects::

    [01/53] - "Show.Name.S01E02.1080p.WEB.H264-GRP.part01.rar" yEnc (1/137)
    "a8f3k2l0q9z7x6c5v4b3n2m1.par2" yEnc (1/1)
    Kd8sLq02nVx7Pz4T yEnc (12/90)

Obfuscated posts (random names) can't be matched to a title from headers alone — that information lives only
in NZBs from indexers. MSO measures the share instead of pretending otherwise.
"""

from __future__ import annotations

import re

from mso.domain.release import parse_title

_QUOTED = re.compile(r'"([^"]{1,300})"')
_NOISE = re.compile(r"\byEnc\b.*$|\[\d+/\d+\]|\(\d+/\d+\)|^\s*-\s*|\s+-\s*$", re.I)
_EXT = re.compile(
    r"(\.vol\d+\+\d+\.par2|\.par2|\.part\d+\.rar|\.r\d{2,3}|\.rar|\.7z(\.\d{3})?|\.zip(\.\d{3})?|\.\d{3}|"
    r"\.nfo|\.sfv|\.nzb|\.mkv|\.mp4|\.avi|\.m2ts|\.srt|\.jpg|\.png|\.txt)$",
    re.I,
)
_RANDOM = re.compile(r"[A-Za-z0-9]{12,}")
_HEXISH = re.compile(r"[a-f0-9][a-f0-9\-]{19,}", re.I)


def extract_name(subject: str) -> str:
    m = _QUOTED.search(subject)
    name = m.group(1) if m else _NOISE.sub("", subject)
    name = name.strip().strip("-").strip()
    for _ in range(3):  # .part01.rar / .vol00+01.par2 / .7z.001
        new = _EXT.sub("", name)
        if new == name:
            break
        name = new
    return name.strip()[:300] or subject.strip()[:300]


def is_obfuscated(name: str) -> bool:
    stem = name.strip()
    if not stem:
        return True
    if _HEXISH.fullmatch(stem):
        return True
    if _RANDOM.fullmatch(stem) and re.search(r"\d", stem) and re.search(r"[A-Za-z]", stem):
        return True
    return parse_title(stem).obfuscated


def is_usable(name: str) -> bool:
    """Usable for search: not obfuscated and at least two attributes understood (e.g. SxxEyy + resolution)."""
    if is_obfuscated(name):
        return False
    return parse_title(name).confidence >= 0.33
