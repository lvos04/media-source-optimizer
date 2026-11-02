"""Optional cross-seed (default off): only on proven identical payload.

1. For complete torrents in qBittorrent (whose own tracker allows cross-seeding), search via Slice A
   (``mso.features.search.service.search``, imported lazily — without it the scan reports 'niet beschikbaar').
2. Only torrent results with the same total size are considered; the .torrent is fetched and parsed (bencode).
3. Match = the exact multiset of (relative path, size) of every file is identical to the files qBittorrent has
   for the existing torrent, and the candidate's announce host has a rule with ``allow_cross_seed``.
4. A candidate becomes a change-set preview (``toestemming_nodig``): add *paused*, ``skip_checking=false``
   (qBittorrent hash-verifies the pieces against the existing data), same save path,
   ``contentLayout=Original``,
   never moving/renaming seed data. The .torrent (contains the passkey) is kept encrypted in the vault.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

from mso import changes, jobs
from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.features.torrent import policy
from mso.features.torrent import settings as ts
from mso.features.torrent.adapters import get_adapter, tracker_host
from mso.features.torrent.applier import crossseed_secret
from mso.security import redact
from mso.vault import get_vault

LOGGER = logging.getLogger(__name__)
MAX_TORRENT_BYTES = 10 * 1024 * 1024


class BencodeError(ValueError):
    pass


def bdecode(data: bytes) -> tuple[Any, bytes | None]:
    """Decode a .torrent; returns (value, raw bencoded ``info`` dict bytes)."""
    raw_info: list[bytes] = []

    def dec(i: int, depth: int) -> tuple[Any, int]:
        if depth > 64:
            raise BencodeError("te diep genest")
        if i >= len(data):
            raise BencodeError("onverwacht einde")
        c = data[i : i + 1]
        if c == b"i":
            end = data.index(b"e", i)
            return int(data[i + 1 : end]), end + 1
        if c == b"l":
            i += 1
            out = []
            while data[i : i + 1] != b"e":
                v, i = dec(i, depth + 1)
                out.append(v)
            return out, i + 1
        if c == b"d":
            i += 1
            d: dict[bytes, Any] = {}
            while data[i : i + 1] != b"e":
                k, i = dec(i, depth + 1)
                if not isinstance(k, bytes):
                    raise BencodeError("sleutel is geen string")
                start = i
                v, i = dec(i, depth + 1)
                d[k] = v
                if k == b"info" and depth == 0:
                    raw_info.append(data[start:i])
            return d, i + 1
        if c.isdigit():
            colon = data.index(b":", i)
            n = int(data[i:colon])
            if n < 0 or colon + 1 + n > len(data):
                raise BencodeError("ongeldige stringlengte")
            return data[colon + 1 : colon + 1 + n], colon + 1 + n
        raise BencodeError("ongeldig teken")

    try:
        value, end = dec(0, 0)
    except (ValueError, IndexError) as exc:
        raise BencodeError(f"ongeldig torrentbestand: {exc}") from exc
    return value, (raw_info[0] if raw_info else None)


def bencode(v: Any) -> bytes:
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, str):
        v = v.encode()
    if isinstance(v, bytes):
        return b"%d:%s" % (len(v), v)
    if isinstance(v, list):
        return b"l" + b"".join(bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        items = sorted(((k.encode() if isinstance(k, str) else k), val) for k, val in v.items())
        return b"d" + b"".join(bencode(k) + bencode(val) for k, val in items) + b"e"
    raise TypeError(type(v))


def parse_torrent(data: bytes) -> dict[str, Any]:
    meta, raw = bdecode(data)
    if not isinstance(meta, dict) or raw is None or b"info" not in meta:
        raise BencodeError("geen info-woordenboek")
    info = meta[b"info"]
    name = info.get(b"name", b"").decode("utf-8", "replace")
    files: list[tuple[str, int]] = []
    if b"files" in info:
        for f in info[b"files"]:
            path = "/".join(p.decode("utf-8", "replace") for p in f[b"path"])
            files.append((f"{name}/{path}", int(f[b"length"])))
    elif b"length" in info:
        files.append((name, int(info[b"length"])))
    elif b"file tree" in info:
        raise BencodeError("v2-only torrents worden (nog) niet ondersteund voor cross-seed")
    announce = meta.get(b"announce", b"").decode("utf-8", "replace") or None
    if not announce and meta.get(b"announce-list"):
        announce = meta[b"announce-list"][0][0].decode("utf-8", "replace")
    return {
        "infohash": hashlib.sha1(raw).hexdigest(),  # noqa: S324 (BitTorrent v1 infohash)
        "name": name,
        "files": files,
        "total_size": sum(s for _, s in files),
        "private": int(info.get(b"private", 0)) == 1,
        "tracker_host": tracker_host(announce),
        "piece_length": info.get(b"piece length"),
    }


def payload_matches(existing: list[dict[str, Any]], candidate: list[tuple[str, int]]) -> dict[str, Any]:
    a = sorted((str(f["name"]), int(f["size"])) for f in existing)
    b = sorted(candidate)
    if a == b:
        return {"match": True, "files": len(a)}
    missing = sorted(set(b) - set(a))[:5]
    return {"match": False, "files": len(b), "differences": [{"name": n, "size": s} for n, s in missing]}


def _search_available() -> Any:
    try:
        from mso.features.search import service  # type: ignore[import-not-found]
    except ImportError:
        return None
    return service


def _fetch_torrent(service: Any, release: Any) -> bytes | None:
    fetch = getattr(service, "fetch_download", None)
    if fetch is not None:
        try:
            got = fetch(release)
            data = got[0] if isinstance(got, tuple) else got
            return data if isinstance(data, bytes | bytearray) else None
        except Exception as exc:
            LOGGER.info("download via zoekdienst mislukt: %s", redact(str(exc)))
            return None
    url = getattr(release, "download_url", None)
    if not url or url.startswith("magnet:"):
        return None
    with cb.http_client(url, timeout=30) as http:
        r = http.get("")
        if r.status_code != 200 or len(r.content) > MAX_TORRENT_BYTES:
            return None
        return r.content


def scan(limit: int | None = None) -> dict[str, Any]:
    if not ts.get("cross_seed_enabled"):
        return {"status": "uit", "message": "Cross-seed staat uit in de instellingen", "candidates": 0}
    service = _search_available()
    if service is None:
        return {
            "status": "niet_beschikbaar",
            "message": "Zoekdienst (indexers) is niet beschikbaar",
            "candidates": 0,
        }
    db = get_db()
    adapter = get_adapter()
    limit = limit or int(ts.get("cross_seed_max_per_run"))
    rows = db.fetchall(
        "SELECT * FROM torrent_state WHERE NOT removed AND (info->>'progress')::float >= 1 "
        "AND state NOT IN ('missingFiles','error') ORDER BY updated_at DESC"
    )
    found, checked = 0, 0
    for row in rows:
        if checked >= limit:
            break
        if not policy.crossseed_allowed(row["tracker_host"]):
            continue
        checked += 1
        info = row["info"]
        size = int(info.get("total_size") or info.get("size") or 0)
        try:
            existing_files = adapter.files(row["hash"])
            query = service.SearchQuery(
                q=row["name"],
                media_category=row["media_category"],
                kind="search",
                protocols={"torrent"},
                limit=50,
            )
            result = service.search(query, actor="cross-seed")
        except Exception as exc:
            LOGGER.info("cross-seed zoeken mislukt voor %s: %s", row["hash"][:8], redact(str(exc)))
            continue
        for rel in getattr(result, "items", []):
            if getattr(rel, "protocol", None) != "torrent":
                continue
            if rel.size_bytes and int(rel.size_bytes) != size:
                continue
            if rel.infohash_v1 and rel.infohash_v1.lower() == row["hash"]:
                continue
            data = _fetch_torrent(service, rel)
            if not data:
                continue
            try:
                parsed = parse_torrent(data)
            except BencodeError:
                continue
            if parsed["infohash"] == row["hash"] or parsed["total_size"] != size:
                continue
            m = payload_matches(existing_files, parsed["files"])
            if not m["match"] or not policy.crossseed_allowed(parsed["tracker_host"]):
                continue
            if db.scalar("SELECT count(*) FROM torrent_state WHERE hash=%s", (parsed["infohash"],)):
                continue
            cid = db.scalar(
                "INSERT INTO torrent_crossseed_candidates(source_hash, candidate_hash, tracker_host, title, "
                "release_source, files, total_size, save_path, category, match) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (candidate_hash) DO NOTHING RETURNING id",
                (
                    row["hash"],
                    parsed["infohash"],
                    parsed["tracker_host"],
                    rel.title,
                    getattr(rel, "source_id", None),
                    m["files"],
                    parsed["total_size"],
                    info.get("save_path"),
                    row["category"],
                    jsonb(
                        {
                            "files": m["files"],
                            "piece_length": parsed["piece_length"],
                            "private": parsed["private"],
                        }
                    ),
                ),
            )
            if cid:
                get_vault().set(crossseed_secret(int(cid)), base64.b64encode(data).decode())
                found += 1
    return {"status": "ok", "checked": checked, "candidates": found}


def add_candidate_from_bytes(
    source_hash: str, data: bytes, *, title: str | None = None, release_source: str | None = None
) -> dict[str, Any]:
    """Register a candidate from a user-supplied .torrent (same checks as the scan)."""
    db = get_db()
    row = db.fetchone("SELECT * FROM torrent_state WHERE hash=%s AND NOT removed", (source_hash,))
    if row is None:
        raise ValueError("bron-torrent niet gevonden")
    if float(row["info"].get("progress") or 0) < 1:
        raise ValueError("bron-torrent is nog niet compleet")
    parsed = parse_torrent(data)
    if parsed["infohash"] == source_hash:
        raise ValueError("dit is dezelfde torrent")
    m = payload_matches(get_adapter().files(source_hash), parsed["files"])
    if not m["match"]:
        raise ValueError("payload is niet identiek (bestandsnamen/groottes wijken af)")
    if not policy.crossseed_allowed(parsed["tracker_host"]):
        raise ValueError(
            f"cross-seeden is niet toegestaan voor tracker {parsed['tracker_host'] or 'onbekend'}"
        )
    if not policy.crossseed_allowed(row["tracker_host"]):
        raise ValueError(f"cross-seeden vanaf tracker {row['tracker_host'] or 'onbekend'} is niet toegestaan")
    cid = db.scalar(
        "INSERT INTO torrent_crossseed_candidates(source_hash, candidate_hash, tracker_host, title, "
        "release_source, "
        "files, total_size, save_path, category, match) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (candidate_hash) DO UPDATE SET updated_at=now() RETURNING id",
        (
            source_hash,
            parsed["infohash"],
            parsed["tracker_host"],
            title or parsed["name"],
            release_source,
            m["files"],
            parsed["total_size"],
            row["info"].get("save_path"),
            row["category"],
            jsonb(
                {"files": m["files"], "piece_length": parsed["piece_length"], "private": parsed["private"]}
            ),
        ),
    )
    get_vault().set(crossseed_secret(int(cid)), base64.b64encode(data).decode())
    return dict(db.fetchone("SELECT * FROM torrent_crossseed_candidates WHERE id=%s", (cid,)))


def preview(cid: int, actor: str) -> dict[str, Any]:
    db = get_db()
    row = db.fetchone("SELECT * FROM torrent_crossseed_candidates WHERE id=%s", (cid,))
    if row is None:
        raise ValueError("kandidaat niet gevonden")
    ch = changes.create(
        "qbittorrent",
        f"Cross-seed toevoegen (gepauzeerd): {row['title']}",
        [
            {
                "path": f"crossseed:{cid}",
                "label": f"Torrent voor {row['tracker_host']}",
                "before": "afwezig",
                "after": "toegevoegd_gepauzeerd",
            }
        ],
        risk="toestemming_nodig",
        explanation=(
            f"Identieke payload ({row['files']} bestand(en), zelfde namen en groottes) als een complete "
            "torrent. Wordt gepauzeerd toegevoegd op hetzelfde opslagpad; qBittorrent controleert eerst "
            "alle "
            f"stukken (geen skip-check). Seeddata wordt niet verplaatst of hernoemd. Terugdraaien kan niet "
            f"automatisch: MSO verwijdert nooit torrents."
        ),
        impact={"tracker": row["tracker_host"], "save_path": row["save_path"], "files": row["files"]},
        source="cross-seed",
        actor=actor,
        dedupe_key=f"torrent-crossseed:{cid}",
    )
    db.execute(
        "UPDATE torrent_crossseed_candidates SET status='previewed', change_id=%s, updated_at=now() WHERE "
        "id=%s",
        (ch["id"], cid),
    )
    return ch


@jobs.handler("torrent.cross_seed_scan")
def _scan_job(ctx: jobs.JobContext) -> dict[str, Any]:
    return scan()


jobs.every(
    "torrent.cross_seed_scan",
    6 * 3600,
    enabled=lambda: cb.is_configured("qbittorrent") and bool(ts.get("cross_seed_enabled")),
)
