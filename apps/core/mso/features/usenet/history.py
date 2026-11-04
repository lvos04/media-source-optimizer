"""SABnzbd history → ``download_outcomes`` (incremental) and passive per-server statistics.

Polling is conditional: SABnzbd's ``last_history_update`` counter is passed back, so an unchanged history
costs
one tiny request. Only slots newer than the last ingested ``completed`` time (or still post-processing) are
upserted; the upsert itself is idempotent on (client, nzo_id).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mso import jobs
from mso import settings_registry as sr
from mso.connectors import base as cb
from mso.connectors.sabnzbd import SabClient, SabError
from mso.db import get_db
from mso.domain import outcomes
from mso.domain.categories import MEDIA_CATEGORIES
from mso.features.usenet import failures, nzbs, serverstatus
from mso.security import redact

LOGGER = logging.getLogger(__name__)
FINAL = {"Completed": "success", "Failed": "failed"}
STATS_DAYS = 35


NONE_VALUES = {"none", "geen", "-", ""}
_ANIME = re.compile(r"anime", re.I)
_TV = re.compile(r"(^|[^a-z])tv|series|serie\b|sonarr|shows?\b|episodes?|televis", re.I)
_MOVIES = re.compile(r"movie|film|radarr|cinema", re.I)


def category_map() -> dict[str, str | None]:
    """Explicit mapping from ``usenet.category_map`` (``sab=media``; ``sab=none`` = explicitly no category)."""
    out: dict[str, str | None] = {}
    for line in sr.get("usenet.category_map") or []:
        if "=" not in line:
            continue
        sab, media = (p.strip() for p in line.split("=", 1))
        if not sab:
            continue
        if media in MEDIA_CATEGORIES:
            out[sab.lower()] = media
        elif media.lower() in NONE_VALUES:
            out[sab.lower()] = None
    return out


def auto_category(sab_cat: str | None) -> str | None:
    """Name heuristic for SABnzbd categories without an explicit mapping (tv/series/sonarr → tv,
    anime + tv/sonarr → tv-anime, movies/films/radarr → movies, anime + movies/radarr → movies-anime,
    plain "anime" → tv-anime; ``*``/default and everything else → none)."""
    name = (sab_cat or "").strip().lower()
    if not name or name in ("*", "default", "standaard"):
        return None
    if name in MEDIA_CATEGORIES:
        return name
    anime, tv, movies = bool(_ANIME.search(name)), bool(_TV.search(name)), bool(_MOVIES.search(name))
    if anime and movies:
        return "movies-anime"
    if anime:
        return "tv-anime"
    if movies and not tv:
        return "movies"
    if tv and not movies:
        return "tv"
    return None


def resolve_category(sab_cat: str | None, mapping: dict[str, str | None] | None = None) -> tuple[str | None, str]:
    """→ (media category, source): source ``handmatig`` (explicit setting), ``automatisch`` (name heuristic)
    or ``geen``."""
    if not sab_cat:
        return None, "geen"
    mapping = category_map() if mapping is None else mapping
    key = sab_cat.lower()
    if key in mapping:
        return mapping[key], "handmatig"
    auto = auto_category(sab_cat)
    return auto, "automatisch" if auto else "geen"


def media_category(sab_cat: str | None, mapping: dict[str, str | None] | None = None) -> str | None:
    return resolve_category(sab_cat, mapping)[0]


def _state() -> dict[str, Any]:
    db = get_db()
    db.execute("INSERT INTO usenet_ingest_state(id) VALUES (1) ON CONFLICT DO NOTHING")
    return dict(db.fetchone("SELECT * FROM usenet_ingest_state WHERE id=1"))


def map_slot(
    slot: dict[str, Any],
    *,
    mapping: dict[str, str | None],
    retention: list[int] | None,
    server_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status = FINAL.get(str(slot.get("status")), "active")
    nzo_id = str(slot.get("nzo_id"))
    known = nzbs.by_nzo(nzo_id)
    completed = int(slot.get("completed") or 0)
    finished = datetime.fromtimestamp(completed, UTC) if completed and status != "active" else None
    started = None
    if finished and slot.get("download_time"):
        started = finished - timedelta(seconds=int(slot.get("download_time") or 0))
    meta: dict[str, Any] = {"sab_status": slot.get("status")}
    reason = detail = None
    if status == "failed":
        cls = failures.classify(
            slot.get("fail_message"),
            slot.get("stage_log"),
            posted_at=known["posted_at"] if known else None,
            server_retention_days=retention,
        )
        cls = cls or failures.Classification("unknown", "onbekend", "geen foutmelding van SABnzbd", "")
        if server_events:
            cls = failures.with_server_context(cls, serverstatus.problems_at(finished, server_events))
        reason, detail = cls.reason, cls.detail
        meta["classification"] = {"certainty": cls.certainty, "evidence": cls.evidence}
        if cls.likely_cause:
            meta["classification"]["likely_cause"] = cls.likely_cause
    elif slot.get("fail_message"):
        meta["sab_message"] = redact(str(slot["fail_message"]))[:300]
    cat = slot.get("category")
    return {
        "protocol": "usenet",
        "client": "sabnzbd",
        "client_ref": nzo_id,
        "release_key": known["release_key"] if known else None,
        "title": str(slot.get("name") or slot.get("nzb_name") or nzo_id)[:500],
        "client_category": cat,
        "media_category": media_category(cat, mapping)
        or (media_category(known["category"], mapping) if known else None),
        "source_id": known["source_id"] if known else None,
        "status": status,
        "failure_reason": reason,
        "failure_detail": detail,
        "size_bytes": int(slot.get("bytes") or 0) or None,
        "downloaded_bytes": int(slot.get("downloaded") or 0)
        or (int(slot.get("bytes") or 0) if status == "success" else None),
        "started_at": started,
        "finished_at": finished,
        "meta": meta,
    }


def ingest(*, force: bool = False) -> dict[str, Any]:
    st = _state()
    db = get_db()
    limit = int(sr.get("usenet.history_limit"))
    with SabClient.from_config() as client:
        try:
            hist = client.history(
                limit=limit, last_history_update=None if force else st["last_history_update"]
            )
        except SabError as exc:
            db.execute(
                "UPDATE usenet_ingest_state SET last_run_at=now(), last_error=%s WHERE id=1", (str(exc),)
            )
            raise
        if hist is None:
            db.execute("UPDATE usenet_ingest_state SET last_run_at=now(), last_error=NULL WHERE id=1")
            return {"changed": False, "ingested": 0}
        slots = list(hist.get("slots") or [])
        last_completed = int(st["last_completed"] or 0)
        todo = [
            s
            for s in slots
            if force
            or FINAL.get(str(s.get("status"))) is None
            or int(s.get("completed") or 0) > last_completed
        ]
        retention: list[int] | None = None
        events: list[dict[str, Any]] = []
        if any(str(s.get("status")) == "Failed" for s in todo):
            try:
                retention = [s["retention"] for s in client.servers() if s["enable"]]
            except SabError:
                retention = None
            try:  # SABnzbd's own server errors → likely cause of unspecific failures
                serverstatus.refresh(client)
            except SabError as exc:
                LOGGER.info("SABnzbd-serverstatus niet beschikbaar: %s", exc)
            events = serverstatus.load_events()
    mapping = category_map()
    new_failed: list[str] = []
    for slot in todo:
        row = map_slot(slot, mapping=mapping, retention=retention, server_events=events)
        before = db.fetchone(
            "SELECT status FROM download_outcomes WHERE client='sabnzbd' AND client_ref=%s",
            (row["client_ref"],),
        )
        outcomes.upsert_outcome(row)
        if row["status"] == "failed" and (before is None or before["status"] != "failed"):
            new_failed.append(row["client_ref"])
            outcomes.observe("sabnzbd", "usenet.download_failed", 1.0, {"reason": row["failure_reason"]})
        elif row["status"] == "success" and (before is None or before["status"] != "success"):
            outcomes.observe("sabnzbd", "usenet.download_ok", 1.0)
    max_completed = max(
        [int(s.get("completed") or 0) for s in slots if FINAL.get(str(s.get("status")))]
        + [int(st["last_completed"] or 0)]
    )
    db.execute(
        "UPDATE usenet_ingest_state SET last_history_update=%s, last_completed=%s, last_run_at=now(), "
        "last_error=NULL, ingested_total=ingested_total+%s WHERE id=1",
        (_int_or_none(hist.get("last_history_update")), max_completed, len(todo)),
    )
    if new_failed and sr.get("usenet.auto_test_failed"):
        from mso.features.usenet import articles

        for nzo in new_failed:
            articles.maybe_test_failed(nzo)
    return {"changed": True, "ingested": len(todo), "failed_new": len(new_failed)}


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ passive server statistics
def snapshot_server_stats() -> dict[str, Any]:
    with SabClient.from_config() as client:
        stats = client.server_stats()
    servers = stats.get("servers") or {}
    db = get_db()
    cutoff = date.today() - timedelta(days=STATS_DAYS)
    rows = 0
    for name, s in servers.items():
        daily = s.get("daily") or {}
        tried = s.get("articles_tried") or {}
        success = s.get("articles_success") or {}
        for day_s in set(daily) | set(tried) | set(success):
            try:
                day = date.fromisoformat(day_s)
            except ValueError:
                continue
            if day < cutoff:
                continue
            db.execute(
                "INSERT INTO usenet_server_stats(server,day,bytes,articles_tried,articles_success) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (server,day) DO UPDATE SET bytes=EXCLUDED.bytes, "
                "articles_tried=EXCLUDED.articles_tried, articles_success=EXCLUDED.articles_success, "
                "observed_at=now()",
                (
                    name,
                    day,
                    int(daily.get(day_s) or 0),
                    _int_or_none(tried.get(day_s)),
                    _int_or_none(success.get(day_s)),
                ),
            )
            rows += 1
        outcomes.observe("sabnzbd", "sab.server_total_bytes", float(s.get("total") or 0), {"server": name})
    db.execute("INSERT INTO usenet_ingest_state(id) VALUES (1) ON CONFLICT DO NOTHING")
    db.execute("UPDATE usenet_ingest_state SET last_stats_at=now() WHERE id=1")
    return {"servers": len(servers), "rows": rows}


def server_totals() -> dict[str, int]:
    """Latest SABnzbd all-time byte counter per server (since SABnzbd's last statistics reset)."""
    rows = get_db().fetchall(
        "SELECT DISTINCT ON (labels->>'server') labels->>'server' AS server, value FROM observations "
        "WHERE subject='sabnzbd' AND metric='sab.server_total_bytes' ORDER BY labels->>'server', ts DESC"
    )
    return {r["server"]: int(r["value"] or 0) for r in rows}


# ------------------------------------------------------------------ jobs
def _due(column: str, every_s: float) -> bool:
    st = _state()
    last = st.get(column)
    return last is None or (datetime.now(UTC) - last).total_seconds() >= every_s - 5


@jobs.handler("usenet.sab_ingest")
def _job_ingest(ctx: jobs.JobContext) -> dict[str, Any]:
    if not ctx.payload.get("force") and not _due("last_run_at", float(sr.get("usenet.history_poll_s"))):
        return {"skipped": "niet aan de beurt"}
    return ingest(force=bool(ctx.payload.get("full")))


@jobs.handler("usenet.sab_stats")
def _job_stats(ctx: jobs.JobContext) -> dict[str, Any]:
    if not ctx.payload.get("force") and not _due(
        "last_stats_at", float(sr.get("usenet.stats_poll_min")) * 60
    ):
        return {"skipped": "niet aan de beurt"}
    return snapshot_server_stats()


def _sab_configured() -> bool:
    return cb.is_configured("sabnzbd")


jobs.every("usenet.sab_ingest", 60, enabled=_sab_configured)
jobs.every("usenet.sab_stats", 600, enabled=_sab_configured)


def queue_view(limit: int = 100) -> dict[str, Any]:
    with SabClient.from_config() as client:
        q = client.queue(limit=limit)
    mapping = category_map()
    items = []
    for s in q.get("slots") or []:
        items.append(
            {
                "nzo_id": s.get("nzo_id"),
                "name": s.get("filename") or s.get("name"),
                "category": s.get("cat"),
                "media_category": media_category(s.get("cat"), mapping),
                "category_source": resolve_category(s.get("cat"), mapping)[1],
                "status": s.get("status"),
                "percentage": _float(s.get("percentage")),
                "size_mb": _float(s.get("mb")),
                "left_mb": _float(s.get("mbleft")),
                "timeleft": s.get("timeleft"),
                "priority": s.get("priority"),
            }
        )
    return {
        "items": items,
        "status": q.get("status"),
        "paused": bool(q.get("paused")),
        "speed_kbps": _float(q.get("kbpersec")),
        "left_mb": _float(q.get("mbleft")),
        "total": int(q.get("noofslots") or len(items)),
        "observed_at": datetime.now(UTC),
    }


def history_view(limit: int = 50, failed_only: bool = False) -> dict[str, Any]:
    with SabClient.from_config() as client:
        hist = client.history(limit=limit, failed_only=failed_only) or {}
    mapping = category_map()
    events = serverstatus.load_events()
    items = []
    for s in hist.get("slots") or []:
        row = map_slot(s, mapping=mapping, retention=None, server_events=events)
        known = nzbs.by_nzo(row["client_ref"])
        cls = row["meta"].get("classification") or {}
        cause = cls.get("likely_cause")
        items.append(
            {
                "nzo_id": row["client_ref"],
                "name": row["title"],
                "category": row["client_category"],
                "media_category": row["media_category"],
                "category_source": resolve_category(row["client_category"], mapping)[1]
                if row["media_category"] == media_category(row["client_category"], mapping)
                else "nzb",
                "status": row["status"],
                "sab_status": s.get("status"),
                "failure_reason": row["failure_reason"],
                "failure_label": outcomes.FAILURE_REASONS.get(row["failure_reason"] or "", None),
                "failure_detail": row["failure_detail"],
                "certainty": cls.get("certainty"),
                "evidence": cls.get("evidence"),
                "likely_cause": {
                    **cause,
                    "label": outcomes.FAILURE_REASONS.get(cause["reason"], cause["reason"]),
                }
                if cause
                else None,
                "size_bytes": row["size_bytes"],
                "finished_at": row["finished_at"],
                "nzb_id": known["id"] if known else None,
                "testable": bool(known and known["segments"]),
            }
        )
    return {
        "items": items,
        "total": int(hist.get("noofslots") or len(items)),
        "observed_at": datetime.now(UTC),
    }


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ingest_status() -> dict[str, Any]:
    st = _state()
    return {
        "last_run_at": st["last_run_at"],
        "last_stats_at": st["last_stats_at"],
        "last_error": st["last_error"],
        "ingested_total": st["ingested_total"],
        "last_completed": datetime.fromtimestamp(st["last_completed"], UTC) if st["last_completed"] else None,
    }
