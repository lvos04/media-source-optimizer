"""HTTP API ``/api/v1/torrent/*`` (GET = viewer, operational = operator, configuration/approval = admin)."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mso import changes, jobs, state
from mso.audit import audit
from mso.auth import Principal, require
from mso.connectors import base as cb
from mso.db import get_db
from mso.features.torrent import applier as _applier  # noqa: F401  (registers the qbittorrent applier)
from mso.features.torrent import crossseed, diagnostics, ingest, policy, storage, tuning
from mso.features.torrent import settings as ts
from mso.features.torrent.adapters import ADAPTERS

router = APIRouter(prefix="/api/v1/torrent", tags=["torrent"])


def _snapshot() -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM torrent_client_snapshot WHERE id=1")


def _connector() -> dict[str, Any]:
    row = get_db().fetchone(
        "SELECT id, url, enabled, status, version, capabilities, last_checked_at, last_error "
        "FROM connectors WHERE id='qbittorrent'"
    )
    configured = bool(row and row["url"] and row["enabled"])
    return {
        "configured": configured,
        "status": row["status"] if row else "unknown",
        "version": row["version"] if row else None,
        "capabilities": row["capabilities"] if row else {},
        "last_checked_at": row["last_checked_at"] if row else None,
        "last_error": row["last_error"] if row else None,
    }


def _stale(snap: dict[str, Any] | None) -> bool:
    return snap is None or (datetime.now(UTC) - snap["updated_at"]).total_seconds() > 600


def torrent_view(r: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    info, diag = r["info"] or {}, r["diagnosis"] or {}
    eta = diag.get("eta") or {}
    out = {
        "hash": r["hash"],
        "name": r["name"],
        "category": r["category"],
        "media_category": r["media_category"],
        "state": r["state"],
        "private": r["private"],
        "tracker_host": r["tracker_host"],
        "progress": info.get("progress"),
        "size": info.get("total_size") or info.get("size"),
        "amount_left": info.get("amount_left"),
        "dlspeed": info.get("dlspeed"),
        "upspeed": info.get("upspeed"),
        "num_seeds": info.get("num_seeds"),
        "num_leechs": info.get("num_leechs"),
        "num_complete": info.get("num_complete"),
        "availability": info.get("availability"),
        "ratio": info.get("ratio"),
        "seeding_time_s": info.get("seeding_time"),
        "eta_s": eta.get("eta_s"),
        "eta_unreliable": bool(eta.get("unreliable")),
        "realistic_eta_s": eta.get("realistic_eta_s"),
        "eta_reasons": eta.get("reasons", []),
        "diagnosis": diag.get("primary"),
        "findings_count": len(diag.get("findings") or []),
        "suspicious": next(
            (f for f in diag.get("findings") or [] if f.get("code") == "suspicious_payload"), None
        ),
        "recheck_suggested": bool(diag.get("recheck_suggested")),
        "recheck_reason": diag.get("recheck_reason"),
        "obligation": diag.get("obligation"),
        "stalled_for_h": diag.get("stalled_for_h"),
        "updated_at": r["updated_at"],
    }
    if full:
        out["findings"] = diag.get("findings") or []
        out["trackers"] = r["trackers"]
        out["save_path"] = info.get("save_path")
        out["added_on"] = info.get("added_on")
        out["up_limit"] = info.get("up_limit")
        out["dl_limit"] = info.get("dl_limit")
        out["ratio_limit"] = info.get("ratio_limit")
        out["seeding_time_limit"] = info.get("seeding_time_limit")
    return out


# ------------------------------------------------------------------ overview / torrents
@router.get("/overview")
def overview(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    db = get_db()
    snap = _snapshot()
    prefs = (snap or {}).get("preferences") or {}
    s = (snap or {}).get("server_state") or {}
    counts = db.fetchall(
        "SELECT diagnosis->'primary'->>'code' AS code, count(*) AS n FROM torrent_state "
        "WHERE NOT removed GROUP BY 1"
    )
    states = db.fetchall("SELECT state, count(*) AS n FROM torrent_state WHERE NOT removed GROUP BY 1")
    suspicious = int(
        db.scalar(
            "SELECT count(*) FROM torrent_state WHERE NOT removed AND EXISTS (SELECT 1 FROM "
            "jsonb_array_elements(COALESCE(diagnosis->'findings', '[]'::jsonb)) f "
            "WHERE f->>'code' = 'suspicious_payload')"
        )
        or 0
    )
    return {
        "connector": _connector(),
        "client": None
        if snap is None
        else {
            "version": snap["version"],
            "webapi": snap["webapi"],
            "auth_method": snap["auth_method"],
            "capabilities": snap["capabilities"],
            "updated_at": snap["updated_at"],
            "error": snap["error"],
            "stale": _stale(snap),
        },
        "transfer": {
            k: s.get(k)
            for k in (
                "dl_info_speed",
                "up_info_speed",
                "dl_rate_limit",
                "up_rate_limit",
                "use_alt_speed_limits",
                "total_peer_connections",
                "free_space_on_disk",
                "queued_io_jobs",
                "average_time_queue",
            )
        },
        "connectivity": diagnostics.connectivity(
            s,
            prefs,
            expected_interface=str(ts.get("expected_interface") or ""),
            interfaces=(snap or {}).get("interfaces") or [],
        )
        if snap
        else None,
        "findings": (snap or {}).get("findings") or [],
        "diagnosis_counts": {(c["code"] or "ok"): c["n"] for c in counts},
        "state_counts": {c["state"]: c["n"] for c in states},
        "suspicious_count": suspicious,
        "mode": state.mode(),
        "paused": state.paused(),
    }


@router.get("/torrents")
def list_torrents(
    state_: str | None = Query(None, alias="state"),
    diagnosis: str | None = None,
    category: str | None = None,
    q: str | None = None,
    include_removed: bool = False,
    limit: int = Query(500, ge=1, le=5000),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    where, params = [], []
    if not include_removed:
        where.append("NOT removed")
    if state_:
        where.append("state = ANY(%s)")
        params.append(state_.split(","))
    if diagnosis:
        where.append("diagnosis->'primary'->>'code' = ANY(%s)")
        params.append(diagnosis.split(","))
    if category:
        where.append("category=%s")
        params.append(category)
    if q:
        where.append("name ILIKE %s")
        params.append(f"%{q}%")
    sql = "SELECT * FROM torrent_state" + (" WHERE " + " AND ".join(where) if where else "")
    sql += (
        " ORDER BY (diagnosis->'primary'->>'severity') = 'critical' DESC, (info->>'progress')::float ASC, "
        "name "
        "LIMIT %s"
    )
    params.append(limit)
    rows = get_db().fetchall(sql, params)
    snap = _snapshot()
    return {
        "items": [torrent_view(r) for r in rows],
        "updated_at": snap["updated_at"] if snap else None,
        "stale": _stale(snap),
        "connector": _connector(),
    }


@router.get("/torrents/{h}")
def get_torrent(h: str, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    r = get_db().fetchone("SELECT * FROM torrent_state WHERE hash=%s", (h.lower(),))
    if r is None:
        raise HTTPException(404, detail="Torrent niet gevonden")
    out = torrent_view(r, full=True)
    out["samples"] = get_db().fetchall(
        "SELECT ts, dlspeed, upspeed, progress, num_seeds, availability, eta FROM torrent_samples WHERE "
        "hash=%s "
        "ORDER BY ts DESC LIMIT 60",
        (r["hash"],),
    )
    return out


@router.post("/refresh")
def refresh(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not cb.is_configured("qbittorrent"):
        raise HTTPException(409, detail="qBittorrent is nog niet gekoppeld")
    slot = int(datetime.now(UTC).timestamp() // 30)
    job = jobs.enqueue(
        "torrent.ingest",
        {},
        idempotency_key=f"torrent.ingest:manual:{slot}",
        max_attempts=1,
        created_by=p.actor,
    )
    return {"job_id": job["id"], "status": job["status"]}


class ActionBody(BaseModel):
    reason: str = Field("", max_length=500)


def _action_preview(h: str, action: Literal["recheck", "reannounce"], actor: str) -> dict[str, Any]:
    r = get_db().fetchone("SELECT * FROM torrent_state WHERE hash=%s AND NOT removed", (h.lower(),))
    if r is None:
        raise HTTPException(404, detail="Torrent niet gevonden")
    diag = r["diagnosis"] or {}
    codes = {f["code"] for f in diag.get("findings") or []}
    if action == "recheck":
        if not diag.get("recheck_suggested"):
            raise HTTPException(
                409,
                detail="Recheck wordt alleen voorgesteld bij bewijs (ontbrekende bestanden of "
                "foutstatus); hier is dat er niet",
            )
        title = f"Recheck: {r['name']}"
        expl = (
            f"Reden: {diag.get('recheck_reason')}. qBittorrent controleert alle stukken tegen de data op "
            "schijf. "
            "Er wordt niets verwijderd of verplaatst; de schijf wordt tijdelijk belast. Niet terug te "
            "draaien "
            "(een controle verandert geen data)."
        )
        risk = "toestemming_nodig"
    else:
        if not codes & {"tracker_error", "tracker_partial"}:
            raise HTTPException(409, detail="Opnieuw aankondigen wordt alleen voorgesteld bij trackerfouten")
        title = f"Opnieuw aankondigen: {r['name']}"
        expl = "Vraagt de tracker(s) opnieuw om peers. Onschadelijk; voegt geen trackers toe."
        risk = "automatisch_veilig"
    return changes.create(
        "qbittorrent",
        title,
        [
            {
                "path": f"torrent:{r['hash']}:{action}",
                "label": title,
                "before": "niet_uitgevoerd",
                "after": "uitgevoerd",
            }
        ],
        risk=risk,
        explanation=expl,
        impact={"Torrent": r["name"], "Status in qBittorrent": r["state"] or "onbekend"},
        actor=actor,
        dedupe_key=f"torrent-{action}:{r['hash']}",
    )


@router.post("/torrents/{h}/recheck-preview")
def recheck_preview(h: str, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    return _action_preview(h, "recheck", p.actor)


@router.post("/torrents/{h}/reannounce-preview")
def reannounce_preview(h: str, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    return _action_preview(h, "reannounce", p.actor)


# ------------------------------------------------------------------ preferences / policy
@router.get("/preferences")
def preferences(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    snap = _snapshot()
    prefs = (snap or {}).get("preferences") or {}
    violations = {v["key"]: v for v in policy.pref_violations(prefs)}
    cap = ts.upload_cap_bps()
    items = []
    for k, m in policy.ALLOWED_PREFS.items():
        lo, hi = policy.pref_bounds(k) if m["kind"] == "int" else (m.get("min"), m.get("max"))
        bounds = None  # None = no judgement (no bound known / not applicable)
        if k in prefs:
            v = prefs.get(k)
            if k in violations:
                bounds = "buiten_grens"
            elif k in ("up_limit", "alt_up_limit") and cap is not None and isinstance(v, int):
                bounds = "buiten_grens" if v <= 0 or v > cap else "ok"
                hi = cap
            elif k in policy.GUARDED_PREFS:
                bounds = "ok"
        items.append(
            {
                "key": k,
                "label": m["label"],
                "kind": m["kind"],
                "value": prefs.get(k),
                "min": lo,
                "max": hi,
                "present": k in prefs,
                "bounds": bounds,
                "target": violations[k]["target"] if k in violations else None,
            }
        )
    return {
        "items": items,
        "out_of_bounds": sum(1 for i in items if i["bounds"] == "buiten_grens"),
        "updated_at": snap["updated_at"] if snap else None,
        "stale": _stale(snap),
    }


@router.get("/policy")
def get_policy(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    snap = _snapshot()
    prefs = (snap or {}).get("preferences") or {}
    torrents = [r["info"] for r in get_db().fetchall("SELECT info FROM torrent_state WHERE NOT removed")]
    return {
        "speed": policy.speed_policy(prefs),
        "queue": policy.queue_policy(prefs, torrents),
        "private": {
            "has_private_torrents": policy.has_private_torrents(),
            "dht": prefs.get("dht"),
            "pex": prefs.get("pex"),
            "lsd": prefs.get("lsd"),
            "add_trackers_enabled": prefs.get("add_trackers_enabled"),
            "note": "qBittorrent respecteert de private-vlag (geen DHT/PeX/LSD voor private torrents). "
            "MSO weigert daarnaast elke wijziging die trackers toevoegt of DHT/PeX/LSD aanzet "
            "zolang er private torrents zijn.",
        },
        "share": {
            "max_ratio_enabled": prefs.get("max_ratio_enabled"),
            "max_ratio": prefs.get("max_ratio"),
            "max_seeding_time_enabled": prefs.get("max_seeding_time_enabled"),
            "max_seeding_time": prefs.get("max_seeding_time"),
            "max_ratio_act": prefs.get("max_ratio_act"),
        },
        "updated_at": snap["updated_at"] if snap else None,
    }


class PrefPreview(BaseModel):
    changes: dict[str, Any] = Field(..., min_length=1, max_length=10)
    reason: str = Field("", max_length=500)


SAFE_KEYS = {
    "max_connec",
    "max_connec_per_torrent",
    "max_uploads",
    "max_uploads_per_torrent",
    "max_active_downloads",
    "max_active_uploads",
    "max_active_torrents",
    "dont_count_slow_torrents",
    "up_limit",
    "alt_up_limit",
}


@router.post("/preferences/preview")
def preferences_preview(body: PrefPreview, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    snap = _snapshot()
    if snap is None or snap["error"]:
        raise HTTPException(409, detail="Geen actuele qBittorrent-gegevens; ververs eerst")
    prefs = snap["preferences"] or {}
    ops = []
    risk = "automatisch_veilig"
    for key, after in body.changes.items():
        meta = policy.ALLOWED_PREFS.get(key)
        if meta is None:
            raise HTTPException(422, detail=f"Instelling '{key}' wordt door MSO niet beheerd")
        before = prefs.get(key)
        if meta["kind"] == "rate" and isinstance(after, int) and not isinstance(after, bool):
            after = ts.kib_floor(after)  # qBittorrent keeps limits in whole KiB/s; round down like it does
        ops.append({"path": f"pref:{key}", "label": meta["label"], "before": before, "after": after})
        small = (
            isinstance(before, int)
            and isinstance(after, int)
            and not isinstance(after, bool)
            and before > 0
            and abs(after - before) / before <= 0.25
        )
        if key not in SAFE_KEYS or not (small or isinstance(after, bool)):
            risk = "toestemming_nodig"
    return changes.create(
        "qbittorrent",
        "qBittorrent-instellingen: " + ", ".join(o["label"] for o in ops),
        ops,
        risk=risk,
        explanation=body.reason or "Handmatig voorstel vanuit Torrentclient.",
        source="user",
        actor=p.actor,
    )


# ------------------------------------------------------------------ tracker rules
class RuleBody(BaseModel):
    tracker_host: str | None = Field(None, max_length=255)
    category: str | None = Field(None, max_length=100)
    label: str = Field("", max_length=100)
    private: bool = False
    min_ratio: float | None = Field(None, ge=0, le=100)
    min_seed_time_h: float | None = Field(None, ge=0, le=100000)
    mode: Literal["either", "both"] = "either"
    allow_cross_seed: bool = False
    notes: str = Field("", max_length=1000)


def _clean_rule(body: RuleBody) -> dict[str, Any]:
    d = body.model_dump()
    host = (d["tracker_host"] or "").strip().lower()
    if "/" in host or ":" in host or "?" in host:
        raise HTTPException(422, detail="Vul alleen de hostnaam van de tracker in (geen URL of passkey)")
    d["tracker_host"] = host or None
    d["category"] = (d["category"] or "").strip() or None
    if not d["tracker_host"] and not d["category"]:
        raise HTTPException(422, detail="Een regel geldt voor een tracker, een categorie of beide")
    if d["allow_cross_seed"] and (not d["tracker_host"] or d["category"]):
        raise HTTPException(422, detail="Cross-seed toestaan kan alleen per tracker (zonder categorie)")
    return d


@router.get("/tracker-rules")
def rules_list(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    known = get_db().fetchall("SELECT id, label, private, meta FROM sources WHERE kind='tracker' ORDER BY id")
    return {
        "items": policy.list_rules(),
        "known_trackers": [
            {
                "host": k["id"].split(":", 1)[1],
                "private": k["private"],
                "torrents": (k["meta"] or {}).get("torrents"),
            }
            for k in known
        ],
    }


@router.post("/tracker-rules")
def rules_create(body: RuleBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    d = _clean_rule(body)
    db = get_db()
    if db.scalar(
        "SELECT count(*) FROM torrent_tracker_rules WHERE COALESCE(tracker_host,'')=%s AND "
        "COALESCE(category,'')=%s",
        (d["tracker_host"] or "", d["category"] or ""),
    ):
        raise HTTPException(409, detail="Er bestaat al een regel voor deze tracker/categorie")
    row = db.fetchone(
        "INSERT INTO torrent_tracker_rules(tracker_host, category, label, private, min_ratio, "
        "min_seed_time_h, mode, "
        "allow_cross_seed, notes) VALUES (%(tracker_host)s,%(category)s,%(label)s,%(private)s,%(min_ratio)s,"
        "%(min_seed_time_h)s,%(mode)s,%(allow_cross_seed)s,%(notes)s) RETURNING *",
        d,
    )
    audit(
        p.actor,
        "torrent.rule_created",
        f"Seedregel {d['tracker_host'] or ''} {d['category'] or ''}".strip(),
        target="torrent",
        detail=d,
    )
    return dict(row)


@router.put("/tracker-rules/{rid}")
def rules_update(rid: int, body: RuleBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    d = _clean_rule(body)
    row = get_db().fetchone(
        "UPDATE torrent_tracker_rules SET tracker_host=%(tracker_host)s, category=%(category)s, "
        "label=%(label)s, "
        "private=%(private)s, min_ratio=%(min_ratio)s, min_seed_time_h=%(min_seed_time_h)s, mode=%(mode)s, "
        "allow_cross_seed=%(allow_cross_seed)s, notes=%(notes)s, updated_at=now() WHERE id=%(id)s "
        "RETURNING *",
        {**d, "id": rid},
    )
    if row is None:
        raise HTTPException(404, detail="Regel niet gevonden")
    audit(p.actor, "torrent.rule_updated", f"Seedregel {rid} bijgewerkt", target="torrent", detail=d)
    return dict(row)


@router.delete("/tracker-rules/{rid}")
def rules_delete(rid: int, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    """Removes the MSO rule record only (nothing in qBittorrent is touched)."""
    n = get_db().execute("DELETE FROM torrent_tracker_rules WHERE id=%s", (rid,))
    if not n:
        raise HTTPException(404, detail="Regel niet gevonden")
    audit(p.actor, "torrent.rule_deleted", f"Seedregel {rid} verwijderd (alleen MSO-regel)", target="torrent")
    return {"ok": True}


@router.get("/obligations")
def obligations(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    rep = storage.report()
    return {"items": rep["obligations"], "summary": rep["obligations_summary"]}


# ------------------------------------------------------------------ tuning
@router.get("/tuning")
def tuning_list(
    limit: int = Query(50, ge=1, le=500), _: Principal = Depends(require("viewer"))
) -> dict[str, Any]:
    tuning.sync_runs()
    rows = get_db().fetchall(
        "SELECT r.*, c.status AS change_status, c.risk AS change_risk FROM torrent_tuning_runs r "
        "LEFT JOIN change_sets c ON c.id=r.change_id ORDER BY r.id DESC LIMIT %s",
        (limit,),
    )
    return {
        "items": [tuning.run_view(r) for r in rows],
        "settings": {
            "autotune_enabled": ts.get("autotune_enabled"),
            "auto_rollback": ts.get("autotune_auto_rollback"),
            "evaluation_hours": ts.get("evaluation_hours"),
            "baseline_hours": ts.get("baseline_hours"),
            "hysteresis_pct": ts.get("tuning_hysteresis_pct"),
            "cooldown_hours": ts.get("tuning_cooldown_hours"),
        },
        "automation_allowed": state.automation_allowed(),
        "mode": state.mode(),
        "paused": state.paused(),
        "open_run": (tuning.open_run() or {}).get("id"),
    }


@router.post("/tuning/propose")
def tuning_propose(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not cb.is_configured("qbittorrent"):
        raise HTTPException(409, detail="qBittorrent is nog niet gekoppeld")
    return tuning.propose(p.actor)


@router.post("/tuning/evaluate")
def tuning_evaluate(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    return {"evaluated": tuning.evaluate()}


# ------------------------------------------------------------------ storage / cross-seed / clients
@router.get("/storage")
def storage_report(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return storage.report()


@router.post("/storage/scan")
def storage_scan(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    slot = int(datetime.now(UTC).timestamp() // 60)
    job = jobs.enqueue(
        "torrent.storage_scan",
        {},
        idempotency_key=f"torrent.storage_scan:{slot}",
        max_attempts=1,
        created_by=p.actor,
    )
    return {"job_id": job["id"], "status": job["status"]}


@router.get("/cross-seed")
def crossseed_list(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    rows = get_db().fetchall(
        "SELECT c.*, s.name AS source_name, cs.status AS change_status FROM "
        "torrent_crossseed_candidates c LEFT JOIN torrent_state s ON s.hash=c.source_hash "
        "LEFT JOIN change_sets cs ON cs.id=c.change_id ORDER BY c.id DESC LIMIT 500"
    )
    allowed = get_db().fetchall(
        "SELECT tracker_host FROM torrent_tracker_rules WHERE allow_cross_seed "
        "AND tracker_host IS NOT NULL AND category IS NULL ORDER BY tracker_host"
    )
    return {
        "enabled": bool(ts.get("cross_seed_enabled")),
        "search_available": crossseed._search_available() is not None,
        "allowed_trackers": [a["tracker_host"] for a in allowed],
        "items": rows,
    }


@router.post("/cross-seed/scan")
def crossseed_scan(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not ts.get("cross_seed_enabled"):
        raise HTTPException(409, detail="Cross-seed staat uit (Instellingen → Torrent)")
    slot = int(datetime.now(UTC).timestamp() // 300)
    job = jobs.enqueue(
        "torrent.cross_seed_scan",
        {},
        idempotency_key=f"torrent.cross_seed_scan:{slot}",
        max_attempts=1,
        created_by=p.actor,
    )
    return {"job_id": job["id"], "status": job["status"]}


class CandidateUpload(BaseModel):
    source_hash: str = Field(..., pattern=r"^[0-9a-fA-F]{40,64}$")
    torrent_base64: str = Field(..., max_length=14_000_000)
    title: str | None = Field(None, max_length=300)


@router.post("/cross-seed/candidates")
def crossseed_upload(body: CandidateUpload, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not ts.get("cross_seed_enabled"):
        raise HTTPException(409, detail="Cross-seed staat uit (Instellingen → Torrent)")
    try:
        data = base64.b64decode(body.torrent_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, detail="Ongeldig torrentbestand (base64)") from exc
    try:
        row = crossseed.add_candidate_from_bytes(body.source_hash.lower(), data, title=body.title)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    audit(
        p.actor,
        "torrent.crossseed_candidate",
        f"Cross-seed-kandidaat {row['title']}",
        target="torrent",
        detail={"tracker": row["tracker_host"], "files": row["files"]},
    )
    return row


@router.post("/cross-seed/{cid}/preview")
def crossseed_preview(cid: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    try:
        return crossseed.preview(cid, p.actor)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/cross-seed/{cid}/reject")
def crossseed_reject(cid: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    n = get_db().execute(
        "UPDATE torrent_crossseed_candidates SET status='rejected', updated_at=now() "
        "WHERE id=%s AND status IN ('candidate','previewed')",
        (cid,),
    )
    if not n:
        raise HTTPException(409, detail="Kandidaat niet (meer) af te wijzen")
    audit(p.actor, "torrent.crossseed_rejected", f"Cross-seed-kandidaat {cid} afgewezen", target="torrent")
    return {"ok": True}


@router.get("/clients")
def clients(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": a.id,
                "label": a.label,
                "implemented": a.implemented,
                "note": a.note,
                "configured": a.implemented and cb.is_configured(a.id),
            }
            for a in ADAPTERS.values()
        ]
    }


# keep the ingest module referenced (its job handlers register on import)
_ = ingest
