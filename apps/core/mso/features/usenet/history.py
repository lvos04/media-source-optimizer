"""SABnzbd history → ``download_outcomes`` (incremental) and passive per-server statistics.

Polling is conditional: SABnzbd's ``last_history_update`` counter is passed back, so an unchanged history
costs
one tiny request. Only slots newer than the last ingested ``completed`` time (or still post-processing) are
upserted; the upsert itself is idempotent on (client, nzo_id).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mso import jobs, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.connectors import base as cb
from mso.connectors.sabnzbd import SabClient, SabError
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.domain.categories import MEDIA_CATEGORIES
from mso.features.usenet import failures, nzbs, serverstatus, stagelog, unpack
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
    active_env: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One SABnzbd history slot → a ``download_outcomes`` row. ``active_env`` = the active repeated unpack/environment
    errors (:func:`mso.features.usenet.unpack.active_map`): a new download with that error is labelled as SABnzbd/the
    storage right away instead of as a release problem."""
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
        facts = stagelog.parse(slot.get("stage_log"), slot.get("fail_message"))
        cls = failures.classify(
            slot.get("fail_message"),
            slot.get("stage_log"),
            posted_at=known["posted_at"] if known else None,
            server_retention_days=retention,
            facts=facts,
        )
        cls = cls or failures.Classification("unknown", "onbekend", "geen foutmelding van SABnzbd", "")
        if server_events:
            cls = failures.with_server_context(cls, serverstatus.problems_at(finished, server_events), facts)
        reason, detail = cls.reason, cls.detail
        meta["classification"] = {"certainty": cls.certainty, "evidence": cls.evidence}
        if cls.likely_cause:
            meta["classification"]["likely_cause"] = cls.likely_cause
        env = (active_env or {}).get(facts.signature or "") if facts is not None else None
        if env is not None and reason == "unpack_failed":
            reason, meta["classification"] = unpack.relabeled(facts.as_dict(), env)  # type: ignore[union-attr]
        # 1.6.0: SABnzbd's processing log as facts; version 2 marks rows classified with them
        meta["classifier_version"] = stagelog.CLASSIFIER_VERSION
        if facts is not None:
            meta["stage"] = facts.as_dict()
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
    active_env = unpack.active_map() if any(str(s.get("status")) == "Failed" for s in todo) else None
    new_failed: list[str] = []
    for slot in todo:
        row = map_slot(slot, mapping=mapping, retention=retention, server_events=events, active_env=active_env)
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
    if new_failed:  # before the (slow) article tests: recovery must see the relabelled downloads
        try:
            unpack.check()
        except Exception:  # pragma: no cover - the advisory never breaks the ingest
            LOGGER.info("controle uitpakfouten overgeslagen", exc_info=True)
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
def articles_found(tried: Any, failed: Any) -> int | None:
    """Articles SABnzbd found on a server: ``tried − failed``. SABnzbd's ``server_stats`` field ``articles_success``
    holds the number of FAILED articles (``sabnzbd/api.py`` ``_api_server_stats`` → ``BPSMeter.amounts`` →
    ``article_stats_failed``), so MSO stores the difference as ``usenet_server_stats.articles_success``."""
    t, f = _int_or_none(tried), _int_or_none(failed)
    if t is None or f is None:
        return None
    return max(t - f, 0)


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
        failed = s.get("articles_success") or {}  # SABnzbd: failed articles, despite the name
        for day_s in set(daily) | set(tried) | set(failed):
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
                    articles_found(tried.get(day_s), failed.get(day_s)),
                ),
            )
            rows += 1
        outcomes.observe("sabnzbd", "sab.server_total_bytes", float(s.get("total") or 0), {"server": name})
    db.execute("INSERT INTO usenet_ingest_state(id) VALUES (1) ON CONFLICT DO NOTHING")
    db.execute("UPDATE usenet_ingest_state SET last_stats_at=now() WHERE id=1")
    return {"servers": len(servers), "rows": rows}


def server_totals() -> dict[str, int]:
    """Latest SABnzbd all-time byte counter per server (since SABnzbd's last statistics reset). When SABnzbd sent no
    statistics for longer than the raw retention window, the newest hourly rollup (its maximum) is the last value."""
    rows = get_db().fetchall(
        "SELECT DISTINCT ON (server) server, value FROM ("
        "SELECT labels->>'server' AS server, value, ts FROM observations "
        "WHERE subject='sabnzbd' AND metric='sab.server_total_bytes' UNION ALL "
        "SELECT labels->>'server', max, hour FROM observations_hourly "
        "WHERE subject='sabnzbd' AND metric='sab.server_total_bytes'"
        ") t ORDER BY server, ts DESC"
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


# ------------------------------------------------------------------ 1.6.0: reclassify the SABnzbd history
RECLASSIFY_DONE = "usenet.reclassify_v2"
RECLASSIFY_PAGE = 500
RECLASSIFY_MAX = 5000  # slots per source (history / archive)
_CLS_KEYS = ("classification", "stage", "classifier_version")


def _failed_slots(client: SabClient) -> tuple[dict[str, dict[str, Any]], bool]:
    """Failed jobs in SABnzbd's history and — SABnzbd ≥ 4 keeps removed jobs in an archive (``archive=1``) — in its
    archive. The history wins when a job is in both. Returns ``({nzo_id: slot}, archive_read)``."""
    try:
        major = int(str(client.version()).split(".", 1)[0] or 0)
    except (SabError, ValueError):
        major = 0
    out: dict[str, dict[str, Any]] = {}
    archive_read = False
    for archive in (False, True) if major >= 4 else (False,):
        start = 0
        while start < RECLASSIFY_MAX:
            try:
                hist = client.history(start=start, limit=RECLASSIFY_PAGE, failed_only=True, archive=archive) or {}
            except SabError:
                if archive:  # the archive is a bonus; the history itself is required
                    break
                raise
            slots = [s for s in hist.get("slots") or [] if str(s.get("status")) == "Failed"]
            archive_read = archive_read or archive
            for slot in slots:
                out.setdefault(str(slot.get("nzo_id")), slot)
            if len(hist.get("slots") or []) < RECLASSIFY_PAGE:
                break
            start += RECLASSIFY_PAGE
    return out, archive_read


def _cause(meta: dict[str, Any] | None) -> str | None:
    return (((meta or {}).get("classification") or {}).get("likely_cause") or {}).get("reason")


def reclassify(actor: str = "system", *, trigger: str = "upgrade") -> dict[str, Any]:
    """Classify every failed SABnzbd download MSO knows again with the 1.6.0 rules (stage-log facts), from SABnzbd's
    history and archive. Rows SABnzbd no longer has keep their label (``classifier_version`` 1 or absent). Audited
    as ``usenet.reclassified`` with the counts; afterwards the unpack advisory and the failed-download recovery look
    at the new labels."""
    db = get_db()
    with SabClient.from_config() as client:
        slots, archive_read = _failed_slots(client)
        try:
            retention: list[int] | None = [s["retention"] for s in client.servers() if s["enable"]]
        except SabError:
            retention = None
        try:  # SABnzbd's current server errors become events first (as in the ingest)
            serverstatus.refresh(client)
        except SabError as exc:
            LOGGER.info("SABnzbd-serverstatus niet beschikbaar: %s", exc)
    events = serverstatus.load_events(days=400)
    mapping = category_map()
    active_env = unpack.active_map()
    counts = {"checked": 0, "changed": 0, "unchanged": 0, "not_in_sab": 0, "data_complete": 0,
              "server_cause_removed": 0, "server_cause_kept": 0}
    old: dict[int, tuple[str, str | None]] = {}
    for r in db.fetchall(
        "SELECT id, client_ref, failure_reason, failure_detail, meta FROM download_outcomes "
        "WHERE client='sabnzbd' AND status='failed' ORDER BY id"
    ):
        slot = slots.get(r["client_ref"])
        if slot is None:
            counts["not_in_sab"] += 1
            continue
        new = map_slot(slot, mapping=mapping, retention=retention, server_events=events, active_env=active_env)
        if new["status"] != "failed":
            continue
        old_meta = dict(r["meta"] or {})
        new_meta = {k: v for k, v in old_meta.items() if k not in _CLS_KEYS}
        new_meta.update({k: new["meta"][k] for k in _CLS_KEYS if k in new["meta"]})
        old[r["id"]] = (r["failure_reason"] or "unknown", _cause(old_meta))
        db.execute(
            "UPDATE download_outcomes SET failure_reason=%s, failure_detail=%s, meta=%s WHERE id=%s",
            (new["failure_reason"], new["failure_detail"], jsonb(new_meta), r["id"]),
        )
    # the unpack advisory first: a repeated unpack/environment error relabels its downloads (not a release problem)
    unpack_result = unpack.check()
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    for r in db.fetchall("SELECT id, failure_reason, meta FROM download_outcomes WHERE id = ANY(%s)", (list(old),)):
        old_reason, old_cause = old[r["id"]]
        new_reason, new_cause = r["failure_reason"] or "unknown", _cause(r["meta"])
        counts["checked"] += 1
        before[old_reason] += 1
        after[new_reason] += 1
        if ((r["meta"] or {}).get("stage") or {}).get("data_complete") is True:
            counts["data_complete"] += 1
        if old_cause and not new_cause:
            counts["server_cause_removed"] += 1
        elif new_cause:
            counts["server_cause_kept"] += 1
        counts["changed" if old_reason != new_reason or old_cause != new_cause else "unchanged"] += 1
    result = {
        **counts,
        "archive_read": archive_read,
        "sab_failed_jobs": len(slots),
        "reasons_before": dict(before),
        "reasons_after": dict(after),
        "environment_relabeled": unpack_result.get("relabeled", 0),
        "trigger": trigger,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    state.set_state(RECLASSIFY_DONE, result)
    audit(
        actor,
        "usenet.reclassified",
        f"SABnzbd-historie opnieuw beoordeeld: {counts['changed']} van {counts['checked']} mislukte downloads kregen "
        f"een andere oorzaak; {counts['not_in_sab']} staan niet meer in SABnzbd en houden hun oude label",
        target="sabnzbd",
        detail=result,
    )
    result["unpack"] = unpack_result
    try:
        from mso.features.recovery import fallback
    except ImportError:  # recovery module disabled
        return result
    try:
        result["recovery_rechecked"] = fallback.reevaluate()
    except Exception:  # pragma: no cover - never let this break the reclassification
        LOGGER.info("herbeoordeling herstel overgeslagen", exc_info=True)
    return result


def reclassify_status() -> dict[str, Any] | None:
    return state.get_state(RECLASSIFY_DONE, None)


@jobs.handler("usenet.reclassify")
def _job_reclassify(ctx: jobs.JobContext) -> dict[str, Any]:
    """Once after the upgrade to 1.6.0 (until it succeeded once), and on request (``force``)."""
    if not ctx.payload.get("force") and state.get_state(RECLASSIFY_DONE):
        return {"skipped": "al gedaan"}
    return reclassify(str(ctx.payload.get("actor") or "system"), trigger=str(ctx.payload.get("trigger") or "upgrade"))


def _reclassify_due() -> bool:
    return _sab_configured() and not state.get_state(RECLASSIFY_DONE)


jobs.every("usenet.reclassify", 3600, enabled=_reclassify_due)


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
    slots = hist.get("slots") or []
    active_env = unpack.active_map() if any(str(s.get("status")) == "Failed" for s in slots) else None
    items = []
    for s in slots:
        row = map_slot(s, mapping=mapping, retention=None, server_events=events, active_env=active_env)
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
                    "label": cause.get("label") or outcomes.FAILURE_REASONS.get(cause["reason"], cause["reason"]),
                    "servers": [
                        {**sv, "label": sv.get("label") or serverstatus.LABELS.get(sv["status"], sv["status"])}
                        for sv in cause.get("servers") or []
                    ],
                }
                if cause
                else None,
                "facts": _public_facts(row["meta"].get("stage"), cls) if row["status"] == "failed" else None,
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


def _public_facts(stage: dict[str, Any] | None, cls: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The stage-log facts shown per failed download (Downloads → Usenet). ``environment``: SABnzbd/the storage, not
    the release — when SABnzbd's line names such a cause, or (an unpack-program error, whose line names none) when the
    same line came back for several releases (``repeated``, :mod:`mso.features.usenet.unpack`)."""
    if not stage:
        return None
    problem = stage.get("problem")
    rep = (cls or {}).get("repeated") or None
    return {
        "data_complete": stage.get("data_complete"),
        "missing_articles": stage.get("missing_articles"),
        "malformed_articles": stage.get("malformed_articles"),
        "aborted": bool(stage.get("aborted")),
        "repair": stage.get("repair"),
        "blocks_short": stage.get("blocks_short"),
        "unpack": stage.get("unpack"),
        "unpack_line": stage.get("unpack_line"),
        "problem": problem,
        "problem_label": stagelog.PROBLEM_LABELS.get(problem or ""),
        "environment": problem in stagelog.CERTAIN_ENVIRONMENT or bool(rep and problem in stagelog.ENVIRONMENT),
        "repeated": {"count": rep.get("count"), "releases": rep.get("releases")} if rep else None,
        "age_days": stage.get("age_days"),
        "servers": stage.get("servers") or [],
        "summary": stagelog.summary_nl(stage),
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
