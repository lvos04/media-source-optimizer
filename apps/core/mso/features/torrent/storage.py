"""Storage & seeding report (read-only).

- qBittorrent ``free_space_on_disk`` vs the configured reserve and the bytes still to download.
- Per-category usage from qBittorrent's own sizes (no filesystem walk needed).
- Seed-obligation status per torrent (tracker/category rules).
- Optional ``MSO_MEDIA_DIR``: statvfs (incl. ZFS dataset name from /proc/mounts — no zfs commands) and,
only when
  that mount is READ-ONLY, an AV1 hardlink-awareness scan: seeding files with ``st_nlink > 1`` are reported as
  "hardlinked — MediaGuard AV1 must not rewrite; MSO never touches". Nothing is ever opened for writing.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mso import jobs
from mso.config import get_settings
from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.features.torrent import policy
from mso.features.torrent import settings as ts
from mso.features.torrent.adapters import get_adapter
from mso.security import redact

LOGGER = logging.getLogger(__name__)

GB = 1024**3
MAX_SCAN_TORRENTS = 300
MAX_SCAN_FILES = 20000


def _mounts() -> list[tuple[str, str, str, str]]:
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:  # read-only
            return [tuple(line.split()[:4]) for line in fh if len(line.split()) >= 4]  # type: ignore[misc]
    except OSError:
        return []


def mount_info(path: Path) -> dict[str, Any] | None:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    best = None
    p = str(path.resolve())
    for dev, mnt, fstype, opts in _mounts():
        if (p == mnt or p.startswith(mnt.rstrip("/") + "/")) and (best is None or len(mnt) > len(best[1])):
            best = (dev, mnt, fstype, opts)
    read_only = bool(st.f_flag & os.ST_RDONLY)
    return {
        "path": str(path),
        "total": st.f_blocks * st.f_frsize,
        "free": st.f_bavail * st.f_frsize,
        "read_only": read_only,
        "fstype": best[2] if best else None,
        "mountpoint": best[1] if best else None,
        "zfs_dataset": best[0] if best and best[2] == "zfs" else None,
    }


def map_path(qbit_path: str, media_dir: Path) -> Path | None:
    for src, dst in ts.path_map():
        if qbit_path == src or qbit_path.startswith(src + "/"):
            return Path(dst + qbit_path[len(src) :])
    p = Path(qbit_path)
    try:
        p.relative_to(media_dir)
        return p
    except ValueError:
        return None


def hardlink_scan() -> dict[str, Any]:
    """Read-only stat() of seeding files under MSO_MEDIA_DIR. Requires a read-only mount."""
    media = get_settings().media_dir
    if media is None:
        return {
            "status": "niet_ingesteld",
            "message": "MSO_MEDIA_DIR is niet gekoppeld; hardlinkcontrole uit.",
        }
    info = mount_info(media)
    if info is None:
        return {"status": "onbereikbaar", "message": f"{media} is niet leesbaar."}
    if not info["read_only"]:
        return {
            "status": "niet_read_only",
            "mount": info,
            "message": f"{media} is niet read-only gekoppeld; de hardlinkcontrole draait alleen op een "
            "read-only koppeling (MSO schrijft daar nooit, maar dwingt dit af).",
        }
    db = get_db()
    rows = db.fetchall(
        "SELECT hash, name, info, private, tracker_host FROM torrent_state WHERE NOT removed AND "
        "(info->>'progress')::float >= 1 ORDER BY hash LIMIT %s",
        (MAX_SCAN_TORRENTS,),
    )
    adapter = get_adapter()
    files_checked, hardlinked, unmapped = 0, [], 0
    for r in rows:
        save = r["info"].get("save_path") or ""
        try:
            files = adapter.files(r["hash"])
        except Exception as exc:
            LOGGER.info("bestandenlijst van %s niet op te halen: %s", r["hash"][:8], redact(str(exc)))
            continue
        for f in files:
            if files_checked >= MAX_SCAN_FILES:
                break
            local = map_path(f"{save.rstrip('/')}/{f['name']}", media)
            if local is None:
                unmapped += 1
                continue
            try:
                st = os.stat(local, follow_symlinks=False)
            except OSError:
                continue
            files_checked += 1
            if st.st_nlink > 1:
                hardlinked.append(
                    {
                        "hash": r["hash"],
                        "torrent": r["name"],
                        "file": f["name"],
                        "nlink": st.st_nlink,
                        "private": r["private"],
                        "tracker": r["tracker_host"],
                    }
                )
    return {
        "status": "ok",
        "mount": info,
        "files_checked": files_checked,
        "unmapped": unmapped,
        "hardlinked_count": len(hardlinked),
        "hardlinked": hardlinked[:500],
        "message": "Gehardlinkte seedbestanden: MediaGuard AV1 mag deze niet herschrijven; "
        "MSO raakt ze nooit aan.",
        "scanned_at": datetime.now(UTC).isoformat(),
    }


SEEDING_STATES = {"uploading", "forcedUP", "stalledUP", "queuedUP", "checkingUP"}
STOPPED_STATES = {"pausedUP", "stoppedUP", "pausedDL", "stoppedDL"}


def report() -> dict[str, Any]:
    db = get_db()
    snap = db.fetchone("SELECT server_state, updated_at, error FROM torrent_client_snapshot WHERE id=1")
    s = (snap or {}).get("server_state") or {}
    rows = db.fetchall(
        "SELECT hash, name, category, media_category, private, tracker_host, info, diagnosis, state "
        "FROM torrent_state WHERE NOT removed"
    )
    reserve = int(float(ts.get("disk_reserve_gb")) * GB)
    free = s.get("free_space_on_disk")
    remaining = sum(
        int(r["info"].get("amount_left") or 0) for r in rows if float(r["info"].get("progress") or 0) < 1
    )
    disk = None
    if free is not None:
        after = int(free) - remaining
        disk = {
            "free": int(free),
            "remaining_downloads": remaining,
            "free_after_downloads": after,
            "reserve": reserve,
            "status": "kritiek" if after < 0 else ("waarschuwing" if after < reserve else "ok"),
        }
    cats: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r["category"] or "(geen)"
        c = cats.setdefault(
            key,
            {
                "category": key,
                "media_category": r["media_category"],
                "torrents": 0,
                "bytes": 0,
                "seeding": 0,
                "downloading": 0,
                "stopped": 0,
                "private": 0,
            },
        )
        c["torrents"] += 1
        c["bytes"] += int(r["info"].get("total_size") or r["info"].get("size") or 0)
        c["private"] += bool(r["private"])
        # stopped torrents (complete or not) neither seed nor download; error/missing files count nowhere
        if r["state"] in STOPPED_STATES:
            c["stopped"] += 1
        elif r["state"] in SEEDING_STATES:
            c["seeding"] += 1
        elif float(r["info"].get("progress") or 0) < 1:
            c["downloading"] += 1
    rules = policy.list_rules(db)
    obligations = []
    for r in rows:
        if float(r["info"].get("progress") or 0) < 1:
            continue
        ob = policy.obligation_for(r["info"], r["tracker_host"], r["private"], rules)
        if ob["status"] == "geen_regel" and not r["private"]:
            continue
        obligations.append(
            {
                "hash": r["hash"],
                "name": r["name"],
                "tracker": r["tracker_host"],
                "category": r["category"],
                "private": r["private"],
                "state": r["state"],
                **ob,
            }
        )
    obligations.sort(key=lambda o: (o["met"], not o["private"], o["name"]))
    media = get_settings().media_dir
    scan = db.fetchone("SELECT result, updated_at FROM torrent_storage_scan WHERE id=1")
    return {
        "client": {
            "available": snap is not None and not (snap or {}).get("error"),
            "updated_at": (snap or {}).get("updated_at"),
            "error": (snap or {}).get("error"),
        },
        "disk": disk,
        "categories": sorted(cats.values(), key=lambda c: -c["bytes"]),
        "obligations": obligations,
        "obligations_summary": {
            "total": len(obligations),
            "met": sum(1 for o in obligations if o["met"]),
            "open": sum(1 for o in obligations if not o["met"]),
            "private_without_rule": sum(1 for o in obligations if o["warning"]),
        },
        "media": mount_info(media) if media else None,
        "hardlinks": scan["result"] if scan else None,
        "hardlinks_scanned_at": scan["updated_at"] if scan else None,
        "never": [
            "Geen mediabestanden verwijderen, verplaatsen of hernoemen (alleen verdachte torrents na jouw goedkeuring)",
            "Geen seedende of gehardlinkte bestanden herschrijven (MediaGuard AV1-veiligheid)",
            "Geen seeden stoppen vóór de seedverplichting",
        ],
    }


@jobs.handler("torrent.storage_scan")
def _scan_job(ctx: jobs.JobContext) -> dict[str, Any]:
    result = hardlink_scan()
    get_db().execute(
        "INSERT INTO torrent_storage_scan(id, result, updated_at) VALUES (1,%s,now()) "
        "ON CONFLICT (id) DO UPDATE SET result=EXCLUDED.result, updated_at=now()",
        (jsonb(result),),
    )
    return {"status": result["status"], "hardlinked": result.get("hardlinked_count", 0)}


jobs.every(
    "torrent.storage_scan",
    6 * 3600,
    enabled=lambda: cb.is_configured("qbittorrent") and get_settings().media_dir is not None,
)
