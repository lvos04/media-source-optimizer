"""Build an NZB from the stored headers of one header-index collection.

A *collection* is every non-obfuscated header whose derived release name (``release_name``, see
``obfuscation.extract_name``) is the same, case-insensitively. Within a collection each file is identified by its
subject without the trailing yEnc part counter (``… yEnc (5/130)``); the counter gives the segment number. The
NZB therefore contains exactly the articles MSO saw in the whitelisted groups during the retention window:
real message-ids, sizes and groups. The poster is never stored, so ``poster`` is left empty.

Honesty: a header index only sees what it indexed. When parts of a post were outside the window or budget the NZB
is incomplete; ``completeness`` says how many of the announced segments were found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

from mso.db import get_db

NZB_NS = "http://www.newzbin.com/DTD/2003/nzb"
DOCTYPE = '<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">'
MAX_HEADERS = 50_000
_PART = re.compile(r"\((\d{1,6})/(\d{1,6})\)\s*$")
_MID_OK = re.compile(r"^[^\s<>]{1,250}$")


class CollectionNotFound(LookupError):
    pass


@dataclass
class _File:
    subject: str
    total: int = 0
    date: int = 0
    groups: set[str] = field(default_factory=set)
    segments: dict[int, tuple[str, int]] = field(default_factory=dict)


def split_subject(subject: str) -> tuple[str, int, int]:
    """``'[1/9] - "x.part01.rar" yEnc (5/130)'`` → ``('[1/9] - "x.part01.rar" yEnc', 5, 130)``."""
    s = (subject or "").strip()
    m = _PART.search(s)
    if not m:
        return s, 1, 1
    return s[: m.start()].rstrip(), max(1, int(m.group(1))), max(1, int(m.group(2)))


def build(collection: str) -> tuple[bytes, dict[str, Any]]:
    """NZB bytes + ``{title, files, segments, expected_segments, completeness, groups, bytes}``.
    Raises ``CollectionNotFound`` when nothing (non-obfuscated) is stored for this collection."""
    key = (collection or "").strip().lower()
    if not key:
        raise CollectionNotFound("lege collectie")
    rows = get_db().fetchall(
        "SELECT grp, message_id, subject, release_name, bytes, posted_at FROM usenet_hi_headers "
        "WHERE lower(release_name)=%s AND NOT obfuscated ORDER BY posted_at NULLS LAST, article_num LIMIT %s",
        (key, MAX_HEADERS),
    )
    if not rows:
        raise CollectionNotFound(collection)
    title = rows[0]["release_name"]
    files: dict[str, _File] = {}
    for r in rows:
        mid = (r["message_id"] or "").strip().strip("<>")
        if not _MID_OK.fullmatch(mid) or "@" not in mid:
            continue
        base, num, total = split_subject(r["subject"])
        f = files.setdefault(base, _File(subject=base))
        f.total = max(f.total, total)
        f.groups.add(r["grp"])
        posted: datetime | None = r["posted_at"]
        if posted is not None:
            ts = int(posted.timestamp())
            f.date = ts if not f.date else min(f.date, ts)
        f.segments.setdefault(num, (mid, int(r["bytes"] or 0)))
    files = {k: v for k, v in files.items() if v.segments}
    if not files:
        raise CollectionNotFound(collection)

    ET.register_namespace("", NZB_NS)
    root = ET.Element(f"{{{NZB_NS}}}nzb")
    head = ET.SubElement(root, f"{{{NZB_NS}}}head")
    meta = ET.SubElement(head, f"{{{NZB_NS}}}meta", {"type": "name"})
    meta.text = title
    meta_src = ET.SubElement(head, f"{{{NZB_NS}}}meta", {"type": "category"})
    meta_src.text = "MSO header-index"
    n_seg = expected = total_bytes = 0
    all_groups: set[str] = set()
    for f in sorted(files.values(), key=lambda x: x.subject.lower()):
        # full subject as it was posted (with the part counter of the whole file)
        subject = f"{f.subject} (1/{f.total})" if f.total else f.subject
        el = ET.SubElement(
            root, f"{{{NZB_NS}}}file", {"poster": "", "date": str(f.date or 0), "subject": subject}
        )
        g_el = ET.SubElement(el, f"{{{NZB_NS}}}groups")
        for g in sorted(f.groups):
            ET.SubElement(g_el, f"{{{NZB_NS}}}group").text = g
        s_el = ET.SubElement(el, f"{{{NZB_NS}}}segments")
        for num in sorted(f.segments):
            mid, b = f.segments[num]
            ET.SubElement(s_el, f"{{{NZB_NS}}}segment", {"bytes": str(b), "number": str(num)}).text = mid
            total_bytes += b
        n_seg += len(f.segments)
        expected += max(f.total, len(f.segments))
        all_groups |= f.groups
    body = ET.tostring(root, encoding="unicode")
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n{DOCTYPE}\n{body}\n'.encode()
    info = {
        "title": title,
        "files": len(files),
        "segments": n_seg,
        "expected_segments": expected,
        "completeness": round(n_seg / expected, 4) if expected else None,
        "groups": sorted(all_groups),
        "bytes": total_bytes,
    }
    return xml, info


def filename_for(title: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._ \-\[\]()]", "_", title or "headerindex")[:180] or "headerindex"
    return f"{safe}.nzb"
