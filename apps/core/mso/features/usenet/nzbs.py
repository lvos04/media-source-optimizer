"""NZBs whose contents MSO knows — the basis for testing article availability of *actually requested*
articles.

SABnzbd does not export NZBs through its API, so MSO can only test jobs it sent itself with the NZB content
(``SabClient.add_nzb`` or ``record_nzb`` by another module that fetched the NZB) or NZBs the user uploads.
Jobs sent as a URL (``add_url``) are recorded too, but without message-ids: they can't be tested until the
user uploads that NZB. Only a bounded, evenly spread sample of segment message-ids is stored — never the
poster or the whole NZB.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET
from fastapi import HTTPException

from mso.db import get_db, jsonb

MAX_NZB_BYTES = 40 * 1024 * 1024
MAX_STORED_SEGMENTS = 2000
IDEMPOTENT_WINDOW_H = 24


class NzbError(ValueError):
    pass


@dataclass
class ParsedNzb:
    title: str | None
    groups: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    files: int = 0
    segment_total: int = 0
    bytes_total: int = 0
    segments: list[dict[str, Any]] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def spread_sample(items: list[Any], n: int) -> list[Any]:
    """Deterministic, evenly spread sample (covers first/middle/last files instead of only the start)."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def parse_nzb(data: bytes) -> ParsedNzb:
    if not data:
        raise NzbError("leeg NZB-bestand")
    if len(data) > MAX_NZB_BYTES:
        raise NzbError("NZB-bestand is te groot (max 40 MB)")
    try:
        root: Element = SafeET.fromstring(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise NzbError("geen geldig NZB-bestand (XML kon niet worden gelezen)") from exc
    if _local(root.tag) != "nzb":
        raise NzbError("geen NZB: root-element is niet <nzb>")
    title = None
    for el in root.iter():
        if _local(el.tag) == "meta" and el.get("type") in ("name", "title") and (el.text or "").strip():
            title = el.text.strip()[:300]
            break
    groups: set[str] = set()
    dates: list[int] = []
    segs: list[dict[str, Any]] = []
    files = 0
    bytes_total = 0
    for fi, f in enumerate(el for el in root if _local(el.tag) == "file"):
        files += 1
        if (d := f.get("date")) and d.isdigit():
            dates.append(int(d))
        for child in f:
            name = _local(child.tag)
            if name == "groups":
                groups.update((g.text or "").strip() for g in child if (g.text or "").strip())
            elif name == "segments":
                for s in child:
                    mid = (s.text or "").strip().strip("<>")
                    if not mid or "@" not in mid or len(mid) > 250 or any(c.isspace() for c in mid):
                        continue
                    try:
                        b = int(s.get("bytes") or 0)
                        num = int(s.get("number") or 0)
                    except ValueError:
                        b, num = 0, 0
                    bytes_total += max(0, b)
                    segs.append({"m": mid, "b": b, "f": fi, "n": num})
    if not segs:
        raise NzbError("NZB bevat geen artikelen (segments)")
    posted = datetime.fromtimestamp(min(dates), UTC) if dates else None
    return ParsedNzb(
        title=title,
        groups=sorted(groups)[:50],
        posted_at=posted,
        files=files,
        segment_total=len(segs),
        bytes_total=bytes_total,
        segments=spread_sample(segs, MAX_STORED_SEGMENTS),
    )


def recent_send(*, sha256: str) -> str | None:
    """nzo_id of an identical send within the idempotency window (prevents duplicate sends)."""
    return get_db().scalar(
        "SELECT nzo_id FROM usenet_nzbs WHERE sha256=%s AND nzo_id IS NOT NULL AND origin<>'upload' "
        "AND created_at > now() - make_interval(hours => %s) ORDER BY id DESC LIMIT 1",
        (sha256, IDEMPOTENT_WINDOW_H),
    )


def record_send(
    *,
    nzo_id: str | None,
    origin: str,
    title: str,
    category: str | None,
    sha256: str | None,
    parsed: ParsedNzb | None = None,
    url_redacted: str | None = None,
    release_key: str | None = None,
    source_id: str | None = None,
    actor: str = "system",
) -> int:
    p = parsed or ParsedNzb(title=None)
    return int(
        get_db().scalar(
            "INSERT INTO "
            "usenet_nzbs(nzo_id,origin,title,category,release_key,source_id,sha256,url_redacted,groups,"
            "posted_at,files,segment_total,bytes_total,segments,created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (nzo_id) DO UPDATE SET segments=CASE WHEN EXCLUDED.segment_total>0 THEN "
            "EXCLUDED.segments "
            "ELSE usenet_nzbs.segments END, segment_total=GREATEST(usenet_nzbs.segment_total, "
            "EXCLUDED.segment_total), "
            "files=GREATEST(usenet_nzbs.files, EXCLUDED.files), "
            "bytes_total=GREATEST(usenet_nzbs.bytes_total, "
            "EXCLUDED.bytes_total), groups=CASE WHEN EXCLUDED.segment_total>0 THEN EXCLUDED.groups ELSE "
            "usenet_nzbs.groups END, posted_at=COALESCE(EXCLUDED.posted_at, usenet_nzbs.posted_at), "
            "release_key=COALESCE(EXCLUDED.release_key, usenet_nzbs.release_key), "
            "source_id=COALESCE(EXCLUDED.source_id, usenet_nzbs.source_id) RETURNING id",
            (
                nzo_id,
                origin,
                (p.title or title)[:300],
                category,
                release_key,
                source_id,
                sha256,
                url_redacted,
                p.groups,
                p.posted_at,
                p.files,
                p.segment_total,
                p.bytes_total,
                jsonb(p.segments),
                actor,
            ),
        )
    )


def record_nzb(
    nzo_id: str,
    data: bytes,
    *,
    title: str | None = None,
    category: str | None = None,
    release_key: str | None = None,
    source_id: str | None = None,
    actor: str = "system",
) -> int:
    """For other modules that sent a job with ``add_url`` but also hold the NZB bytes: attach the contents so
    article availability can be tested for that job."""
    parsed = parse_nzb(data)
    return record_send(
        nzo_id=nzo_id,
        origin="send_nzb",
        title=title or parsed.title or nzo_id,
        category=category,
        sha256=hashlib.sha256(data).hexdigest(),
        parsed=parsed,
        release_key=release_key,
        source_id=source_id,
        actor=actor,
    )


def upload(filename: str, content_b64: str, *, actor: str, nzo_id: str | None = None) -> dict[str, Any]:
    try:
        data = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, detail="Bestand is geen geldige base64-inhoud") from exc
    try:
        parsed = parse_nzb(data)
    except NzbError as exc:
        raise HTTPException(422, detail=f"NZB afgewezen: {exc}") from exc
    title = (parsed.title or filename.rsplit("/", 1)[-1].removesuffix(".nzb"))[:300] or "upload"
    db = get_db()
    if nzo_id:  # attach to an existing job (e.g. one sent by URL or by another tool)
        nid = record_send(
            nzo_id=nzo_id,
            origin="upload",
            title=title,
            category=None,
            sha256=hashlib.sha256(data).hexdigest(),
            parsed=parsed,
            actor=actor,
        )
    else:
        nid = int(
            db.scalar(
                "INSERT INTO "
                "usenet_nzbs(origin,title,sha256,groups,posted_at,files,segment_total,bytes_total,segments,"
                "created_by) VALUES ('upload',%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    title,
                    hashlib.sha256(data).hexdigest(),
                    parsed.groups,
                    parsed.posted_at,
                    parsed.files,
                    parsed.segment_total,
                    parsed.bytes_total,
                    jsonb(parsed.segments),
                    actor,
                ),
            )
        )
    return public(db.fetchone("SELECT * FROM usenet_nzbs WHERE id=%s", (nid,)))


def get(nzb_id: int) -> dict[str, Any]:
    row = get_db().fetchone("SELECT * FROM usenet_nzbs WHERE id=%s", (nzb_id,))
    if row is None:
        raise HTTPException(404, detail="NZB niet gevonden")
    return dict(row)


def by_nzo(nzo_id: str) -> dict[str, Any] | None:
    row = get_db().fetchone("SELECT * FROM usenet_nzbs WHERE nzo_id=%s", (nzo_id,))
    return dict(row) if row else None


def public(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "nzo_id": row["nzo_id"],
        "origin": row["origin"],
        "title": row["title"],
        "category": row["category"],
        "release_key": row["release_key"],
        "source_id": row["source_id"],
        "groups": row["groups"],
        "posted_at": row["posted_at"],
        "files": row["files"],
        "segment_total": row["segment_total"],
        "bytes_total": row["bytes_total"],
        "segments_known": len(row["segments"] or []),
        "testable": bool(row["segments"]),
        "created_at": row["created_at"],
    }


def list_nzbs(limit: int = 100) -> list[dict[str, Any]]:
    rows = get_db().fetchall("SELECT * FROM usenet_nzbs ORDER BY id DESC LIMIT %s", (limit,))
    return [public(r) for r in rows]
