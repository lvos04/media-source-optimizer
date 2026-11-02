"""Tuning loop with guardrails: baseline → ONE reversible parameter change → 24–48 h measurement →
keep/rollback.

- Only one open run at a time; a cooldown (+ jitter) between steps; a parameter that was rolled back is frozen
  for 7 days and a parameter may not be moved in the opposite direction within 7 days (anti-oscillation).
- Decisions use the median of client observations (``observations`` subject ``qbittorrent``) with hysteresis:
  only a change larger than ``torrent.tuning_hysteresis_pct`` counts as better/worse; too few samples →
  ``inconclusive`` (the change is kept, nothing is claimed).
- Evaluation time = ``torrent.evaluation_hours`` ± jitter, clamped to 24–48 h.
- Auto-apply only when ``torrent.autotune_enabled`` AND ``state.automation_allowed()`` (beheermodus, not
paused)
  AND the change is ``automatisch_veilig`` (small in-bounds step). Automatic rollback on degradation only when
  automation is allowed (and ``torrent.autotune_auto_rollback``); otherwise a recommendation is created.
- Guardrail runs (e.g. capping upload to the safe share of the line) are never auto-rolled back.
"""

from __future__ import annotations

import logging
import random
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes, jobs, state
from mso.audit import audit
from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.features.torrent import policy
from mso.features.torrent import settings as ts

LOGGER = logging.getLogger(__name__)
SUBJECT = "qbittorrent"
OPEN = ("proposed", "evaluating", "degraded_pending")
SETTLE_H = 1.0
FREEZE_DAYS = 7
METRICS = {
    "torrent.dl_speed_active": {"label": "Downloadsnelheid actieve torrents", "unit": "B/s", "higher": True},
    "torrent.io_queue": {"label": "Schijf-I/O-wachtrij", "unit": "jobs", "higher": False},
    "torrent.stalled_ratio": {"label": "Aandeel vastgelopen downloads", "unit": "ratio", "higher": False},
}


# ------------------------------------------------------------------ measurements
def metric_stats(metric: str, start: datetime, end: datetime) -> dict[str, Any]:
    rows = get_db().fetchall(
        "SELECT value FROM observations WHERE subject=%s AND metric=%s AND ts >= %s AND ts <= %s "
        "AND value IS NOT NULL",
        (SUBJECT, metric, start, end),
    )
    vals = [float(r["value"]) for r in rows]
    return {
        "metric": metric,
        "n": len(vals),
        "median": statistics.median(vals) if vals else None,
        "mean": statistics.fmean(vals) if vals else None,
        "from": start.isoformat(),
        "to": end.isoformat(),
    }


def compare(
    baseline: dict[str, Any],
    after: dict[str, Any],
    *,
    higher_is_better: bool,
    hysteresis_pct: float,
    min_samples: int,
) -> dict[str, Any]:
    if (
        (baseline.get("n") or 0) < min_samples
        or (after.get("n") or 0) < min_samples
        or baseline.get("median") is None
        or after.get("median") is None
    ):
        return {
            "verdict": "inconclusive",
            "delta_pct": None,
            "why": f"te weinig metingen (basislijn {baseline.get('n') or 0}, na {after.get('n') or 0}, "
            f"minimaal {min_samples})",
        }
    b, a = float(baseline["median"]), float(after["median"])
    if b == 0:
        delta = 0.0 if a == 0 else 100.0
    else:
        delta = (a - b) / abs(b) * 100
    signed = delta if higher_is_better else -delta
    if signed >= hysteresis_pct:
        verdict = "better"
    elif signed <= -hysteresis_pct:
        verdict = "worse"
    else:
        verdict = "neutral"
    return {
        "verdict": verdict,
        "delta_pct": round(delta, 1),
        "why": f"mediaan {b:.4g} → {a:.4g} ({delta:+.1f}%, hysterese ±{hysteresis_pct:g}%)",
    }


def _jittered_eval_hours() -> float:
    base = float(ts.get("evaluation_hours"))
    j = float(ts.get("tuning_jitter_pct")) / 100
    return round(min(48.0, max(24.0, base * (1 + random.uniform(-j, j)))), 2)


# ------------------------------------------------------------------ proposal
def _snapshot() -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM torrent_client_snapshot WHERE id=1 AND error IS NULL")


def _frozen(param: str, direction: str) -> str | None:
    db = get_db()
    last = db.fetchone(
        "SELECT * FROM torrent_tuning_runs WHERE param=%s AND status <> 'discarded' "
        "AND created_at > now() - make_interval(days => %s) ORDER BY id DESC LIMIT 1",
        (param, FREEZE_DAYS),
    )
    if last is None:
        return None
    if last["status"] == "rolled_back":
        return "teruggedraaid in de afgelopen 7 dagen"
    if last["direction"] and direction != "set" and last["direction"] != direction:
        return "tegengestelde richting binnen 7 dagen (anti-oscillatie)"
    return None


def candidates(snap: dict[str, Any], torrents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic, evidence-based candidate changes, highest priority first."""
    prefs = snap["preferences"] or {}
    s = snap["server_state"] or {}
    out: list[dict[str, Any]] = []
    cap = ts.upload_cap_bps()
    up = prefs.get("up_limit")
    if cap is not None and up is not None and (int(up) == 0 or int(up) > cap):
        out.append(
            {
                "param": "pref:up_limit",
                "label": "Globale uploadlimiet",
                "before": up,
                "after": cap,
                "direction": "down",
                "kind": "guardrail",
                "metric": "torrent.dl_speed_active",
                "risk": "toestemming_nodig" if int(up) == 0 or int(up) > cap * 1.25 else "automatisch_veilig",
                "why": f"Upload staat {'onbeperkt' if int(up) == 0 else 'boven'} de veilige grens van "
                f"{ts.get('upload_cap_pct')}% van je lijn; een volle uplink remt ook downloads.",
            }
        )
    now = datetime.now(UTC)
    io = metric_stats("torrent.io_queue", now - timedelta(hours=2), now)
    mad = prefs.get("max_active_downloads")
    if (
        isinstance(mad, int)
        and mad > 1
        and (
            (io["median"] or 0) > 128 or float(str(s.get("write_cache_overload") or "0").rstrip("%") or 0) > 0
        )
    ):
        out.append(
            {
                "param": "pref:max_active_downloads",
                "label": "Maximum actieve downloads",
                "before": mad,
                "after": mad - 1,
                "direction": "down",
                "kind": "disk_io",
                "metric": "torrent.io_queue",
                "risk": "automatisch_veilig",
                "why": f"Schijf-I/O-wachtrij is aanhoudend hoog (mediaan {io['median']}); "
                "één download minder tegelijk ontlast de opslag.",
            }
        )
    q = policy.queue_policy(prefs, torrents)
    for sug in q["suggestions"]:
        out.append(
            {
                "param": f"pref:{sug['key']}",
                "label": "Trage torrents niet meetellen",
                "before": prefs.get(sug["key"]),
                "after": sug["after"],
                "direction": "set",
                "kind": "tuning",
                "metric": "torrent.dl_speed_active",
                "risk": "automatisch_veilig",
                "why": sug["why"],
            }
        )
    mc = prefs.get("max_connec")
    peers = metric_stats("torrent.peers", now - timedelta(hours=2), now)
    cap_c = int(ts.get("max_connections_cap"))
    if (
        isinstance(mc, int)
        and 0 < mc < cap_c
        and peers["median"] is not None
        and peers["median"] >= 0.9 * mc
        and s.get("connection_status") == "connected"
    ):
        new = min(cap_c, int(round(mc * 1.2)))
        out.append(
            {
                "param": "pref:max_connec",
                "label": "Maximum verbindingen (globaal)",
                "before": mc,
                "after": new,
                "direction": "up",
                "kind": "tuning",
                "metric": "torrent.dl_speed_active",
                "risk": "automatisch_veilig",
                "why": f"Het verbindingsmaximum ({mc}) wordt aanhoudend bereikt "
                f"(mediaan {peers['median']:.0f}).",
            }
        )
    down = ts.line_down_bps()
    act = metric_stats("torrent.dl_speed_active", now - timedelta(hours=2), now)
    if (
        isinstance(mad, int)
        and mad < 10
        and q["queued_downloads"]
        and q["stalled_downloads"] == 0
        and down
        and act["median"] is not None
        and act["median"] < 0.5 * down
        and (io["median"] or 0) <= 32
    ):
        out.append(
            {
                "param": "pref:max_active_downloads",
                "label": "Maximum actieve downloads",
                "before": mad,
                "after": mad + 1,
                "direction": "up",
                "kind": "tuning",
                "metric": "torrent.dl_speed_active",
                "risk": "automatisch_veilig",
                "why": f"Er wachten {q['queued_downloads']} downloads terwijl de lijn voor minder dan "
                "de helft benut wordt en de schijf rustig is.",
            }
        )
    return out


def open_run() -> dict[str, Any] | None:
    return get_db().fetchone(
        "SELECT * FROM torrent_tuning_runs WHERE status = ANY(%s) ORDER BY id DESC LIMIT 1", (list(OPEN),)
    )


def propose(actor: str = "system") -> dict[str, Any]:
    """Create at most one proposal. Returns {run, change} or {reason}."""
    db = get_db()
    if open_run():
        return {"reason": "Er loopt al een tuningstap; eerst die evalueren."}
    last = db.fetchone(
        "SELECT * FROM torrent_tuning_runs WHERE evaluated_at IS NOT NULL ORDER BY evaluated_at DESC LIMIT 1"
    )
    if last and actor == "system":
        cool = float(ts.get("tuning_cooldown_hours")) * (
            1 + random.uniform(0, float(ts.get("tuning_jitter_pct")) / 100)
        )
        if last["evaluated_at"] > datetime.now(UTC) - timedelta(hours=cool):
            return {"reason": "Rustperiode na de vorige tuningstap loopt nog."}
    snap = _snapshot()
    if snap is None or snap["updated_at"] < datetime.now(UTC) - timedelta(minutes=15):
        return {"reason": "Geen actuele gegevens van qBittorrent (eerst verversen)."}
    torrents = [r["info"] for r in db.fetchall("SELECT info FROM torrent_state WHERE NOT removed")]
    min_n = int(ts.get("tuning_min_samples"))
    skipped = []
    for c in candidates(snap, torrents):
        why_frozen = _frozen(c["param"], c["direction"])
        if why_frozen:
            skipped.append(f"{c['label']}: {why_frozen}")
            continue
        try:
            policy.validate(c["param"], c["after"])
        except ValueError as exc:
            skipped.append(f"{c['label']}: {exc}")
            continue
        now = datetime.now(UTC)
        baseline = metric_stats(c["metric"], now - timedelta(hours=float(ts.get("baseline_hours"))), now)
        if c["kind"] != "guardrail" and baseline["n"] < min_n:
            skipped.append(f"{c['label']}: te weinig metingen voor een basislijn ({baseline['n']}/{min_n})")
            continue
        return _create_run(c, baseline, actor)
    return {
        "reason": "Geen tuningstap nodig op basis van de huidige metingen."
        if not skipped
        else "Geen tuningstap mogelijk: " + "; ".join(skipped)
    }


def _create_run(c: dict[str, Any], baseline: dict[str, Any], actor: str) -> dict[str, Any]:
    db = get_db()
    eval_h = _jittered_eval_hours()
    meta = METRICS[c["metric"]]
    ch = changes.create(
        "qbittorrent",
        f"Tuning: {c['label']} {c['before']} → {c['after']}",
        [{"path": c["param"], "label": c["label"], "before": c["before"], "after": c["after"]}],
        risk=c["risk"],
        explanation=c["why"] + f" Na {eval_h:g} uur wordt {meta['label'].lower()} vergeleken met de "
        "basislijn; bij verslechtering volgt terugdraaien.",
        impact={"metric": c["metric"], "baseline": baseline, "evaluate_after_h": eval_h, "kind": c["kind"]},
        source="tuning",
        actor=actor,
        dedupe_key=f"torrent-tuning:{c['param']}",
        evaluate_after_h=eval_h,
    )
    run = db.fetchone(
        "INSERT INTO torrent_tuning_runs(change_id, param, kind, direction, before_value, after_value, "
        "metric, "
        "higher_is_better, baseline, reason, evaluate_after_h) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "RETURNING *",
        (
            ch["id"],
            c["param"],
            c["kind"],
            c["direction"],
            jsonb(c["before"]),
            jsonb(c["after"]),
            c["metric"],
            meta["higher"],
            jsonb(baseline),
            c["why"],
            eval_h,
        ),
    )
    audit(
        actor,
        "torrent.tuning_proposed",
        f"Tuningvoorstel: {c['label']} {c['before']} → {c['after']}",
        target="qbittorrent",
        change_id=ch["id"],
        detail={"risk": c["risk"], "metric": c["metric"]},
    )
    auto = None
    if ts.get("autotune_enabled") and state.automation_allowed() and c["risk"] == "automatisch_veilig":
        try:
            changes.apply(ch["id"], "autotune", automatic=True)
            auto = "toegepast"
        except HTTPException as exc:
            auto = f"niet automatisch toegepast: {exc.detail}"
    sync_runs()
    return {
        "run": run_view(get_db().fetchone("SELECT * FROM torrent_tuning_runs WHERE id=%s", (run["id"],))),
        "change": changes.get(ch["id"]),
        "auto": auto,
    }


# ------------------------------------------------------------------ evaluation
def sync_runs() -> None:
    """Follow the linked change set's lifecycle (applied/discarded/rolled back by a user)."""
    db = get_db()
    for r in db.fetchall(
        "SELECT r.*, c.status AS cstatus, c.applied_at AS capplied FROM torrent_tuning_runs r "
        "LEFT JOIN change_sets c ON c.id=r.change_id WHERE r.status = ANY(%s)",
        (list(OPEN),),
    ):
        cs = r["cstatus"]
        if r["status"] == "proposed" and cs == "applied":
            db.execute(
                "UPDATE torrent_tuning_runs SET status='evaluating', applied_at=%s WHERE id=%s",
                (r["capplied"], r["id"]),
            )
        elif cs in ("discarded", "failed") or cs is None:
            db.execute(
                "UPDATE torrent_tuning_runs SET status='discarded', note=%s WHERE id=%s",
                (f"wijziging {cs or 'verdwenen'}", r["id"]),
            )
        elif cs == "rolled_back":
            db.execute(
                "UPDATE torrent_tuning_runs SET status='rolled_back', evaluated_at=COALESCE(evaluated_at, "
                "now()), "
                "note=CASE WHEN note='' THEN 'handmatig teruggedraaid' ELSE note END WHERE id=%s",
                (r["id"],),
            )


def evaluate(now: datetime | None = None) -> list[dict[str, Any]]:
    sync_runs()
    db = get_db()
    now = now or datetime.now(UTC)
    hyst = float(ts.get("tuning_hysteresis_pct"))
    min_n = int(ts.get("tuning_min_samples"))
    done = []
    for r in db.fetchall(
        "SELECT * FROM torrent_tuning_runs WHERE status='evaluating' AND applied_at IS NOT NULL"
    ):
        due = r["applied_at"] + timedelta(hours=float(r["evaluate_after_h"]))
        if due > now:
            continue
        after = metric_stats(r["metric"], r["applied_at"] + timedelta(hours=SETTLE_H), now)
        cmp_ = compare(
            r["baseline"],
            after,
            higher_is_better=r["higher_is_better"],
            hysteresis_pct=hyst,
            min_samples=min_n,
        )
        result = {**after, **cmp_}
        status, note = "kept", ""
        if cmp_["verdict"] == "worse":
            if r["kind"] == "guardrail":
                status, note = "kept", "verslechtering gemeten, maar dit is een beleidsgrens en blijft staan"
            elif state.automation_allowed() and ts.get("autotune_auto_rollback"):
                try:
                    changes.rollback(r["change_id"], "autotune", f"verslechtering {cmp_['why']}")
                    status, note = "rolled_back", "automatisch teruggedraaid: " + cmp_["why"]
                except HTTPException as exc:
                    status, note = "degraded_pending", f"terugdraaien mislukt: {exc.detail}"
            else:
                status, note = (
                    "degraded_pending",
                    "verslechtering gemeten; terugdraaien vereist een gebruiker "
                    "(automatisering niet toegestaan)",
                )
            if status == "degraded_pending":
                outcomes.recommend(
                    "torrent",
                    "Tuningstap terugdraaien",
                    f"Na {r['param']} is {METRICS[r['metric']]['label'].lower()} verslechterd "
                    f"({cmp_['why']}).",
                    risk="toestemming_nodig",
                    subject="qbittorrent",
                    confidence=0.7,
                    change_id=r["change_id"],
                    dedupe_key=f"torrent-tuning-rollback:{r['id']}",
                    evidence=result,
                )
        elif cmp_["verdict"] == "better":
            note = "verbetering gemeten: " + cmp_["why"]
        elif cmp_["verdict"] == "neutral":
            status, note = "inconclusive", "geen meetbaar effect binnen de hysterese; wijziging blijft staan"
        else:
            status, note = "inconclusive", cmp_["why"] + "; wijziging blijft staan, geen conclusie"
        db.execute(
            "UPDATE torrent_tuning_runs SET status=%s, result=%s, note=%s, evaluated_at=now() WHERE id=%s",
            (status, jsonb(result), note, r["id"]),
        )
        audit(
            "autotune",
            "torrent.tuning_evaluated",
            f"Tuning {r['param']}: {status} ({note})",
            target="qbittorrent",
            change_id=r["change_id"],
            detail={"verdict": cmp_["verdict"], "delta_pct": cmp_["delta_pct"]},
        )
        done.append({"id": r["id"], "status": status, "note": note})
    return done


def run_view(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    meta = METRICS.get(r["metric"], {})
    out["metric_label"] = meta.get("label", r["metric"])
    out["metric_unit"] = meta.get("unit")
    if r["applied_at"]:
        due = r["applied_at"] + timedelta(hours=float(r["evaluate_after_h"]))
        out["evaluate_at"] = due.isoformat()
        if r["status"] == "evaluating":
            now = datetime.now(UTC)
            interim = metric_stats(r["metric"], r["applied_at"] + timedelta(hours=SETTLE_H), now)
            out["interim"] = {
                **interim,
                **compare(
                    r["baseline"],
                    interim,
                    higher_is_better=r["higher_is_better"],
                    hysteresis_pct=float(ts.get("tuning_hysteresis_pct")),
                    min_samples=int(ts.get("tuning_min_samples")),
                ),
            }
            out["seconds_left"] = max(0, int((due - now).total_seconds()))
    return out


@jobs.handler("torrent.tune")
def _tune_job(ctx: jobs.JobContext) -> dict[str, Any]:
    res = propose("system")
    return {"reason": res.get("reason"), "run_id": (res.get("run") or {}).get("id"), "auto": res.get("auto")}


@jobs.handler("torrent.tune_evaluate")
def _eval_job(ctx: jobs.JobContext) -> dict[str, Any]:
    return {"evaluated": evaluate()}


jobs.every("torrent.tune", 3600, enabled=lambda: cb.is_configured("qbittorrent"))
jobs.every("torrent.tune_evaluate", 1800, enabled=lambda: cb.is_configured("qbittorrent"))
