"""Ingestion: qBittorrent → torrent_state/samples, download_outcomes, sources (trackers), observations.

Runs as job ``torrent.ingest`` (every 2 minutes while the connector is configured; operators can trigger it).
Uses incremental ``sync/maindata`` (``rid``) via the cached client; tracker lists are refreshed per torrent
only
when needed (new torrent, state change, incomplete torrents every 5 min, others every 30 min).
Tracker URLs are never stored (they contain passkeys): only the announce host.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from mso import jobs
from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.features.torrent import diagnostics, policy
from mso.features.torrent import settings as ts
from mso.features.torrent.adapters import TorrentClientAdapter, TrackerRow, get_adapter, tracker_host
from mso.security import redact

LOGGER = logging.getLogger(__name__)
SUBJECT = "qbittorrent"
GB = 1024**3
DROP_INFO_KEYS = {"magnet_uri", "tracker", "comment"}
FILE_CHECKS_PER_RUN = 25  # torrents/files calls per ingest (each torrent is checked once, then cached)
PAYLOAD_RECHECK_S = 6 * 3600  # re-read the file list of an unflagged torrent at most this often


def sanitize_info(t: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in t.items() if k not in DROP_INFO_KEYS}
    out["tracker_host"] = tracker_host(t.get("tracker"))
    return out


def primary_host(trackers: list[dict[str, Any]], info: dict[str, Any]) -> str | None:
    real = [t for t in trackers if t.get("kind") == "tracker" and t.get("host")]
    for t in real:
        if t.get("status") == "working":
            return t["host"]
    if real:
        return real[0]["host"]
    return tracker_host(info.get("tracker"))


def comment_host(t: dict[str, Any]) -> str | None:
    """Host of the torrent comment when it is a URL (usually the indexer's info page) — host only, never the
    URL (it may carry a passkey)."""
    c = str(t.get("comment") or "").strip()
    if not c.lower().startswith(("http://", "https://")):
        return None
    return tracker_host(c.split()[0])


def _payload(
    adapter: TorrentClientAdapter,
    h: str,
    t: dict[str, Any],
    prev_payload: dict[str, Any] | None,
    media: bool,
    budget: dict[str, int],
    now: datetime,
) -> dict[str, Any]:
    """Name + file-list integrity scan. The file list (``torrents/files``) is fetched at most once per torrent
    (re-read every few hours while nothing was found) and at most ``FILE_CHECKS_PER_RUN`` times per ingest;
    in between the cached result is reused. The torrent name is checked on every run."""
    name = str(t.get("name") or "")
    cached = prev_payload or {}
    files: list[dict[str, Any]] | None = None
    files_at = cached.get("files_at")
    has_meta = t.get("state") not in ("metaDL", "forcedMetaDL") and int(t.get("total_size") or t.get("size") or 0) > 0
    age = None
    if files_at:
        try:
            age = (now - datetime.fromisoformat(files_at)).total_seconds()
        except ValueError:
            age = None
    due = media and has_meta and (age is None or (not cached.get("file_matches") and age > PAYLOAD_RECHECK_S))
    if due and budget["files"] > 0:
        budget["files"] -= 1
        try:
            files = adapter.files(h)
            files_at = now.isoformat()
        except Exception as exc:
            LOGGER.info("bestandenlijst voor %s niet op te halen: %s", h[:8], redact(str(exc)))
    if files is not None:
        file_scan = diagnostics.payload_scan("", files, media=media)
        file_matches = [m for m in file_scan["matches"] if m["where"] == "bestand"]
        files_checked = file_scan["files_checked"]
    else:
        file_matches = list(cached.get("file_matches") or [])
        files_checked = cached.get("files_checked")
    name_scan = diagnostics.payload_scan(name, [], media=media)
    matches = name_scan["matches"] + file_matches
    return {
        "matches": matches[:20],
        "match_count": len(matches),
        "file_matches": file_matches[:20],
        "files_checked": files_checked,
        "files_at": files_at,
    }


def _ts(epoch: Any) -> datetime | None:
    try:
        v = int(epoch)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(v, UTC) if v > 0 else None


def outcome_for(
    t: dict[str, Any], diag: dict[str, Any], stalled_for_h: float | None, trackers: list[dict[str, Any]]
) -> tuple[str, str | None, str | None]:
    st = t.get("state")
    complete = float(t.get("progress") or 0) >= 1
    codes = {f["code"] for f in diag["findings"]}
    if complete:
        return "success", None, None
    if st == "error":
        if "disk_full" in codes:
            return "failed", "disk_full", "Onvoldoende schijfruimte"
        return "failed", "client_failure", "Foutstatus in qBittorrent"
    if st == "missingFiles":
        return "failed", "client_failure", "Bestanden ontbreken"
    if stalled_for_h is not None and stalled_for_h >= float(ts.get("stalled_fail_hours")):
        if "tracker_error" in codes:
            return (
                "failed",
                "tracker_error",
                diag["primary"]["explanation"][:300] if diag["primary"] else None,
            )
        if "disk_full" in codes:
            return "failed", "disk_full", "Onvoldoende schijfruimte"
        return "failed", "stalled", f"Geen voortgang sinds {stalled_for_h:.0f} uur"
    return "active", None, None


def ingest(adapter: TorrentClientAdapter | None = None) -> dict[str, Any]:
    db = get_db()
    adapter = adapter or get_adapter()
    try:
        snap = adapter.snapshot()
    except Exception as exc:
        db.execute(
            "INSERT INTO torrent_client_snapshot(id, error, updated_at) VALUES (1,%s,now()) "
            "ON CONFLICT (id) DO UPDATE SET error=EXCLUDED.error, updated_at=now()",
            (redact(str(exc))[:500],),
        )
        raise
    now = datetime.now(UTC)
    rules = policy.list_rules(db)
    reserve = int(float(ts.get("disk_reserve_gb")) * GB)
    factor = float(ts.get("false_eta_factor"))
    prev_rows = {
        r["hash"]: r
        for r in db.fetchall(
            "SELECT hash, trackers, trackers_at, private, state, last_progress, last_progress_at, removed, "
            "first_seen, diagnosis->'payload' AS payload "
            "FROM torrent_state"
        )
    }
    samples: dict[str, list[dict[str, Any]]] = {}
    for s in db.fetchall(
        "SELECT * FROM torrent_samples WHERE ts > now() - interval '60 minutes' ORDER BY ts DESC"
    ):
        samples.setdefault(s["hash"], []).append(s)

    sent_from = {
        r["release_key"]: r["source_id"]
        for r in db.fetchall(
            "SELECT release_key, source_id FROM search_sends WHERE client='qbittorrent' AND source_id IS NOT NULL"
        )
    }
    budget = {"files": FILE_CHECKS_PER_RUN}
    suspicious: list[dict[str, Any]] = []
    tracker_agg: dict[str, dict[str, Any]] = {}
    infos: list[dict[str, Any]] = []
    counts = {"torrents": 0, "trackers_refreshed": 0, "failed": 0}
    for h, t in snap.torrents.items():
        counts["torrents"] += 1
        prev = prev_rows.get(h)
        complete = float(t.get("progress") or 0) >= 1
        age = (now - prev["trackers_at"]).total_seconds() if prev and prev["trackers_at"] else None
        need = (
            prev is None
            or age is None
            or prev["state"] != t.get("state")
            or (not complete and age > 300)
            or age > 1800
        )
        trackers = list(prev["trackers"]) if prev else []
        if need:
            try:
                rows = adapter.trackers(h)
                trackers = [r.as_dict() for r in rows]
                counts["trackers_refreshed"] += 1
                trackers_at = now
            except Exception as exc:
                LOGGER.info("trackers voor %s niet op te halen: %s", h[:8], redact(str(exc)))
                rows, trackers_at = None, (prev["trackers_at"] if prev else None)
        else:
            rows, trackers_at = None, prev["trackers_at"]
        private = prev["private"] if prev and prev["private"] is not None else None
        if private is None:
            tr_rows = rows if rows is not None else [TrackerRow(**x) for x in trackers]
            private = adapter.is_private(h, t, tr_rows)
        host = primary_host(trackers, t)
        progress = float(t.get("progress") or 0)
        last_progress_at = prev["last_progress_at"] if prev else None
        if (
            prev is None
            or prev["last_progress"] is None
            or progress > float(prev["last_progress"]) + 1e-9
            or int(t.get("dlspeed") or 0) > 0
            or last_progress_at is None
        ):
            last_progress_at = now
        stalled_for_h = None if complete else (now - last_progress_at).total_seconds() / 3600
        obligation = policy.obligation_for(t, host, private, rules) if complete else None
        if obligation is not None:
            obligation["tracker"] = host
        mcat = ts.media_category(t.get("category"))
        v1 = t.get("infohash_v1") or (h if len(h) == 40 else None)
        v2 = t.get("infohash_v2") or None
        release_key = f"btih:{v1}" if v1 else f"btmh:{v2 or h}"
        media = diagnostics.looks_like_media(str(t.get("name") or ""), mcat)
        payload = _payload(adapter, h, t, prev["payload"] if prev else None, media, budget, now)
        source = {
            "tracker": host,
            "indexer": sent_from.get(release_key),
            "comment_host": comment_host(t),
            "category": t.get("category"),
            "tags": str(t.get("tags") or "")[:200] or None,
        }
        diag = diagnostics.diagnose_torrent(
            t,
            trackers,
            server_state=snap.server_state,
            prefs=snap.preferences,
            samples=samples.get(h, []),
            obligation=obligation,
            reserve_bytes=reserve,
            false_eta_factor=factor,
            stalled_for_h=stalled_for_h,
            payload=payload,
            source=source,
        )
        diag["obligation"] = obligation
        diag["payload"] = payload
        sus = next((x for x in diag["findings"] if x["code"] == "suspicious_payload"), None)
        if sus:
            suspicious.append({"hash": h, "release_key": release_key, "name": str(t.get("name") or h),
                               "finding": sus, **source})
        diag["stalled_for_h"] = None if stalled_for_h is None else round(stalled_for_h, 2)
        info = sanitize_info(t)
        infos.append(t)
        db.execute(
            "INSERT INTO torrent_state(hash, client, name, category, media_category, state, private, "
            "tracker_host, "
            "info, trackers, trackers_at, diagnosis, last_progress, last_progress_at, removed, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,now()) ON CONFLICT (hash) DO UPDATE SET "
            "name=EXCLUDED.name, category=EXCLUDED.category, media_category=EXCLUDED.media_category, "
            "state=EXCLUDED.state, private=EXCLUDED.private, tracker_host=EXCLUDED.tracker_host, "
            "info=EXCLUDED.info, "
            "trackers=EXCLUDED.trackers, trackers_at=EXCLUDED.trackers_at, diagnosis=EXCLUDED.diagnosis, "
            "last_progress=EXCLUDED.last_progress, last_progress_at=EXCLUDED.last_progress_at, "
            "removed=false, "
            "updated_at=now()",
            (
                h,
                snap.client,
                str(t.get("name") or h),
                t.get("category"),
                mcat,
                t.get("state"),
                private,
                host,
                jsonb(info),
                jsonb(trackers),
                trackers_at,
                jsonb(diag),
                progress,
                last_progress_at,
            ),
        )
        if not complete:
            db.execute(
                "INSERT INTO torrent_samples(hash, state, progress, dlspeed, upspeed, amount_left, "
                "num_seeds, "
                "num_leechs, availability, eta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    h,
                    t.get("state"),
                    progress,
                    int(t.get("dlspeed") or 0),
                    int(t.get("upspeed") or 0),
                    int(t.get("amount_left") or 0),
                    int(t.get("num_seeds") or 0),
                    int(t.get("num_leechs") or 0),
                    float(t.get("availability") if t.get("availability") is not None else -1),
                    int(t.get("eta") or 0),
                ),
            )
        # --- download_outcomes
        status, reason, detail = outcome_for(t, diag, stalled_for_h, trackers)
        if sus:
            # a fake/malware release is a failed download whatever its progress — counts against its source
            status, reason = "failed", "false_positive"
            detail = "Verdacht uitvoerbaar bestand in media-download: " + sus["evidence"]["matches"][0]["name"]
        counts["failed"] += status == "failed"
        ref = v1 or v2 or h
        oid = outcomes.upsert_outcome(
            {
                "protocol": "torrent",
                "client": snap.client,
                "client_ref": ref,
                "release_key": release_key,
                "title": str(t.get("name") or h),
                "client_category": t.get("category"),
                "media_category": mcat,
                "source_id": f"tracker:{host}" if host else None,
                "status": status,
                "failure_reason": reason,
                "failure_detail": detail,
                "size_bytes": int(t.get("total_size") or t.get("size") or 0),
                "downloaded_bytes": int(t.get("downloaded") or t.get("completed") or 0),
                "private": private,
                "started_at": _ts(t.get("added_on")),
                "finished_at": _ts(t.get("completion_on")) if complete else None,
                "meta": {
                    "state": t.get("state"),
                    "diagnosis": diag["primary"]["code"] if diag["primary"] else None,
                    "suspicious_payload": bool(sus),
                    "ratio": t.get("ratio"),
                    "seeding_time_s": t.get("seeding_time"),
                },
            },
            db=db,
        )
        if status != "failed":
            db.execute(
                "UPDATE download_outcomes SET failure_reason=NULL, failure_detail=NULL WHERE id=%s", (oid,)
            )
        # --- tracker aggregation
        for tr in trackers:
            if tr.get("kind") != "tracker" or not tr.get("host"):
                continue
            a = tracker_agg.setdefault(
                tr["host"],
                {
                    "torrents": 0,
                    "working": 0,
                    "seeds": 0,
                    "private": False,
                    "last_msg": "",
                    "categories": set(),
                },
            )
            a["torrents"] += 1
            a["working"] += tr.get("status") == "working"
            a["seeds"] += int(tr.get("seeds") or 0)
            a["private"] = a["private"] or bool(private)
            if tr.get("status") != "working" and tr.get("msg"):
                a["last_msg"] = tr["msg"]
            if mcat:
                a["categories"].add(mcat)

    # --- torrents that disappeared from the client (removed by the user/arr; never by MSO)
    gone = [h for h, r in prev_rows.items() if h not in snap.torrents and not r["removed"]]
    for h in gone:
        db.execute("UPDATE torrent_state SET removed=true, updated_at=now() WHERE hash=%s", (h,))
        db.execute(
            "UPDATE download_outcomes SET status='removed', observed_at=now() WHERE client=%s AND "
            "(client_ref=%s OR release_key=%s) AND status='active'",
            (snap.client, h, f"btih:{h}"),
        )

    # --- sources + observations
    for host, a in tracker_agg.items():
        scopes = sorted({"anime" if c.endswith("anime") else "normal" for c in a["categories"]}) or [
            "normal",
            "anime",
        ]
        outcomes.upsert_source(
            f"tracker:{host}",
            "tracker",
            host,
            "qbittorrent",
            scopes=scopes,
            private=a["private"],
            meta={"torrents": a["torrents"], "working": a["working"], "last_error": a["last_msg"][:200]},
            db=db,
        )
        outcomes.observe(
            f"tracker:{host}", "tracker.working_ratio", a["working"] / max(1, a["torrents"]), db=db
        )
        outcomes.observe(f"tracker:{host}", "tracker.seeds", a["seeds"], db=db)
        outcomes.observe(f"tracker:{host}", "tracker.torrents", a["torrents"], db=db)
    s = snap.server_state
    active = [t for t in snap.torrents.values() if t.get("state") in diagnostics.ACTIVE_DL]
    outcomes.observe(SUBJECT, "torrent.dl_speed", s.get("dl_info_speed"), db=db)
    outcomes.observe(SUBJECT, "torrent.up_speed", s.get("up_info_speed"), db=db)
    outcomes.observe(SUBJECT, "torrent.peers", s.get("total_peer_connections"), db=db)
    outcomes.observe(SUBJECT, "torrent.io_queue", s.get("queued_io_jobs"), db=db)
    outcomes.observe(SUBJECT, "torrent.free_space", s.get("free_space_on_disk"), db=db)
    if active:
        stalled = sum(1 for t in active if t.get("state") == "stalledDL")
        outcomes.observe(
            SUBJECT,
            "torrent.dl_speed_active",
            sum(int(t.get("dlspeed") or 0) for t in active),
            {"active": len(active)},
            db=db,
        )
        outcomes.observe(
            SUBJECT, "torrent.stalled_ratio", stalled / len(active), {"active": len(active)}, db=db
        )
        avail = [float(t["availability"]) for t in active if t.get("availability") not in (None, -1)]
        if avail:
            outcomes.observe(SUBJECT, "torrent.availability_min", min(avail), db=db)

    _sync_suspicious_recommendations(suspicious, db)
    findings = diagnostics.diagnose_client(
        s,
        snap.preferences,
        expected_interface=str(ts.get("expected_interface") or ""),
        interfaces=snap.interfaces,
        torrents=infos,
        upload_cap_bps=ts.upload_cap_bps(),
        reserve_bytes=reserve,
        suspicious=suspicious,
        pref_violations=policy.pref_violations(snap.preferences),
    )
    db.execute(
        "INSERT INTO torrent_client_snapshot(id, version, webapi, auth_method, capabilities, server_state, "
        "preferences, interfaces, findings, error, updated_at) VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,NULL,now()) "
        "ON CONFLICT (id) DO UPDATE SET version=EXCLUDED.version, webapi=EXCLUDED.webapi, "
        "auth_method=EXCLUDED.auth_method, capabilities=EXCLUDED.capabilities, "
        "server_state=EXCLUDED.server_state, "
        "preferences=EXCLUDED.preferences, interfaces=EXCLUDED.interfaces, findings=EXCLUDED.findings, "
        "error=NULL, "
        "updated_at=now()",
        (
            snap.version,
            snap.webapi,
            snap.auth_method,
            jsonb(snap.capabilities),
            jsonb({k: v for k, v in s.items() if not str(k).startswith("last_external_address")}),
            jsonb(snap.preferences),
            jsonb(snap.interfaces),
            jsonb(findings),
        ),
    )
    db.execute(
        "DELETE FROM torrent_samples WHERE ts < now() - make_interval(days => %s)",
        (int(ts.get("sample_retention_days")),),
    )
    return {
        **counts,
        "removed": len(gone),
        "trackers": len(tracker_agg),
        "client_findings": len(findings),
        "suspicious": len(suspicious),
        "files_checked": FILE_CHECKS_PER_RUN - budget["files"],
    }


SUSPICIOUS_KEY = "torrent-suspicious:"


def _sync_suspicious_recommendations(suspicious: list[dict[str, Any]], db: Any) -> None:
    """One ``advies`` recommendation per suspicious torrent (dedupe per infohash). Recommendations of torrents
    that are gone or no longer flagged are marked resolved. Nothing is ever deleted or stopped by MSO."""
    keys = []
    for x in suspicious:
        key = SUSPICIOUS_KEY + x["release_key"]
        keys.append(key)
        f = x["finding"]
        src = ", ".join(
            v
            for v in (
                f"tracker {x['tracker']}" if x.get("tracker") else "",
                f"indexer {x['indexer']}" if x.get("indexer") else "",
                f"bronpagina {x['comment_host']}" if x.get("comment_host") else "",
                f"categorie {x['category']}" if x.get("category") else "",
            )
            if v
        )
        outcomes.recommend(
            "torrent",
            f"Verdachte torrent verwijderen: {x['name'][:150]}",
            f["explanation"] + " " + (f["suggestion"] or ""),
            risk="advies",
            subject=f"tracker:{x['tracker']}" if x.get("tracker") else (x.get("indexer") or "qbittorrent"),
            impact="Voorkomt dat malware in je mediabibliotheek of op je netwerk belandt; blokkeren van de bron "
            "voorkomt herhaling." + (f" Herkomst: {src}." if src else ""),
            confidence=f["confidence"],
            dedupe_key=key,
            evidence={
                "hash": x["hash"],
                "release_key": x["release_key"],
                "matches": f["evidence"].get("matches"),
                "source": {k: x.get(k) for k in ("tracker", "indexer", "comment_host", "category", "tags")},
            },
            db=db,
        )
    db.execute(
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE area='torrent' AND status='open' "
        "AND dedupe_key LIKE %s AND NOT (dedupe_key = ANY(%s))",
        (SUSPICIOUS_KEY + "%", keys),
    )


@jobs.handler("torrent.ingest")
def _ingest_job(ctx: jobs.JobContext) -> dict[str, Any]:
    try:
        return ingest()
    except cb.ConnectorNotConfigured as exc:
        raise jobs.PermanentJobError(str(exc)) from exc


jobs.every("torrent.ingest", 120, enabled=lambda: cb.is_configured("qbittorrent"))
