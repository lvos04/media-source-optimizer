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
  AND the change is ``automatisch_veilig`` (small in-bounds step). Automatic rollback on degradation (by the
  automation engine) only when automation is allowed and ``torrent.autotune_auto_rollback``; otherwise the engine
  creates a recommendation to roll back.
- Guardrail runs (e.g. capping upload to the safe share of the line) are never evaluated or rolled back: they
  are policy bounds, not optimisations. When several guardrails are violated at once, ONE bundle change brings
  them all to their bound (``toestemming_nodig``, no measurement window, rolled back as a whole).
- Autopilot (``state.autopilot()`` / ``automation.autopilot.enabled('torrent')``): guardrails (single or bundle) are
  reversible policy bounds → ``automatisch_veilig`` and applied at once (impact "beleidsgrens", no measurement); an
  older ``toestemming_nodig`` guardrail proposal still waiting is replaced. Per-value tuning steps are then applied
  automatically too (even with ``torrent.autotune_enabled`` off), with evaluation + automatic rollback as usual.
  Outside Autopilot nothing changes.
- Evaluation has ONE path: the automation engine (``automation.evaluate``) calls the evaluator registered here
  for target ``qbittorrent`` (``evaluate_change``). It records the verdict on the tuning run and in the change's
  impact; rolling back (automatically or as a proposal) is done only by the engine, so never twice.
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
from mso.features.automation import register_evaluator
from mso.features.torrent import cleanup, policy
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
    guardrails: list[dict[str, Any]] = []
    if cap is not None and up is not None and (int(up) == 0 or int(up) > cap):
        guardrails.append(
            {
                "param": "pref:up_limit",
                "label": "Globale uploadlimiet",
                "before": up,
                "after": cap,  # already a whole number of KiB/s (qBittorrent's unit)
                "direction": "down",
                "kind": "guardrail",
                "metric": "torrent.dl_speed_active",
                "risk": "toestemming_nodig" if int(up) == 0 or int(up) > cap * 1.25 else "automatisch_veilig",
                "severity": float("inf") if int(up) == 0 else int(up) / cap,
                "why": f"Upload staat {'onbeperkt' if int(up) == 0 else 'boven'} de veilige grens van "
                f"{ts.get('upload_cap_pct')}% van je lijn; een volle uplink remt ook downloads.",
            }
        )
    # extreme connection / upload-slot values: brought back within the caps even without measurements or a
    # declared line capacity (a guardrail, not an optimisation), one parameter at a time, largest first
    for v in policy.pref_violations(prefs):
        unlimited = v["value"] <= 0
        bound = (
            f"de bovengrens van {v['max']}"
            if unlimited or (v["max"] is not None and v["value"] > v["max"])
            else f"het minimum van {v['min']}"
        )
        guardrails.append(
            {
                "param": f"pref:{v['key']}",
                "label": v["label"],
                "before": v["value"],
                "after": v["target"],
                "direction": "down" if v["target"] < v["value"] or unlimited else "up",
                "kind": "guardrail",
                "metric": "torrent.dl_speed_active",
                "risk": "toestemming_nodig",
                "severity": v["severity"],
                "why": f"{v['label']} staat in qBittorrent op "
                f"{'onbeperkt' if unlimited else v['value']}, buiten de veilige grens "
                f"({v['min'] if v['min'] is not None else '…'}–{v['max'] if v['max'] is not None else '…'}). "
                "Zulke extreme waarden kosten geheugen en CPU, overbelasten router/NAT-tabel en VPN, en "
                f"leveren geen snelheid op. MSO stelt voor terug te gaan naar {bound}.",
            }
        )
    guardrails.sort(key=lambda c: c["severity"], reverse=True)  # stable: up_limit first on equal severity
    out.extend(guardrails)
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
    if _autopilot():
        _replace_waiting_guardrail()
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
    cands = candidates(snap, torrents)
    bundle = []
    for c in cands:
        if c["kind"] != "guardrail":
            continue
        try:
            policy.validate(c["param"], c["after"])
        except ValueError as exc:
            skipped.append(f"{c['label']}: {exc}")
            continue
        bundle.append(c)
    if len(bundle) >= 2 and not _frozen(BUNDLE_PARAM, "down"):
        return _create_bundle(bundle, actor)
    for c in cands:
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


def _autopilot() -> bool:
    """Strictly Autopilot mode (not paused): only then are guardrails applied without approval."""
    return state.autopilot()


def _replace_waiting_guardrail() -> None:
    """Autopilot: a guardrail proposal from before Autopilot (``toestemming_nodig``, still waiting) is discarded
    so a fresh ``automatisch_veilig`` one can be applied at once."""
    for r in get_db().fetchall(
        "SELECT r.id, c.id AS cid FROM torrent_tuning_runs r JOIN change_sets c ON c.id=r.change_id "
        "WHERE r.status='proposed' AND r.kind='guardrail' AND c.status='preview' AND c.risk='toestemming_nodig'"
    ):
        changes.discard(r["cid"], "automatisering")
    sync_runs()


def _auto_apply(change_id: int) -> str:
    if _autopilot():  # through the shared Autopilot helper (automation log, hourly cap)
        from mso.features.torrent.autopilot import apply_auto

        auto, reason = apply_auto(change_id)
        return auto or f"niet automatisch toegepast: {reason}"
    try:
        changes.apply(change_id, "autotune", automatic=True)
        return "toegepast"
    except HTTPException as exc:
        return f"niet automatisch toegepast: {exc.detail}"


KIND_LABEL = {"guardrail": "beleidsgrens", "tuning": "tuning", "disk_io": "schijf-I/O"}
BUNDLE_PARAM = "bundle:guardrails"
NO_MEASUREMENT = "beleidsgrens, geen meting"


def _fmt_value(v: Any) -> str:
    return "onbeperkt" if isinstance(v, int) and not isinstance(v, bool) and v <= 0 else str(v)


def _create_bundle(cands: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    """ONE change that brings every out-of-bounds guardrail value to its bound at once."""
    db = get_db()
    auto_mode = _autopilot()
    risk = "automatisch_veilig" if auto_mode else "toestemming_nodig"
    ops = [{"path": c["param"], "label": c["label"], "before": c["before"], "after": c["after"]} for c in cands]
    summary = ", ".join(f"{c['label']} {_fmt_value(c['before'])} → {c['after']}" for c in cands)
    ch = changes.create(
        "qbittorrent",
        f"Veilige grenzen: {len(cands)} qBittorrent-instellingen in één keer terug binnen de grens",
        ops,
        risk=risk,
        explanation="Meerdere instellingen staan buiten de veilige grens: "
        + summary
        + ". Zulke extreme waarden kosten geheugen en CPU, overbelasten router/NAT-tabel en VPN, en leveren geen "
        "snelheid op. Dit voorstel zet ze allemaal tegelijk op de grens. Het is een beleidsgrens: er volgt geen "
        "meetperiode; terugdraaien zet alle waarden in één keer terug. Daarna gaat de gewone tuning per waarde "
        "verder.",
        impact={
            "Soort": "beleidsgrens (bundel)",
            "Instellingen": [f"{c['label']}: {_fmt_value(c['before'])} → {c['after']}" for c in cands],
            "Evaluatie": "geen meting: een beleidsgrens blijft staan",
            "Terugdraaien": "als geheel (alle waarden terug naar de huidige stand)",
            **({"Automatisch": "ja, Autopilot (terug te draaien, binnen het beleid)"} if auto_mode else {}),
        },
        source="tuning",
        actor=actor,
        dedupe_key=f"torrent-tuning:{BUNDLE_PARAM}",
    )
    run = db.fetchone(
        "INSERT INTO torrent_tuning_runs(change_id, param, kind, direction, before_value, after_value, metric, "
        "higher_is_better, baseline, reason, evaluate_after_h) VALUES (%s,%s,'guardrail','down',%s,%s,%s,true,"
        "%s,%s,0) RETURNING *",
        (
            ch["id"],
            BUNDLE_PARAM,
            jsonb({c["param"]: c["before"] for c in cands}),
            jsonb({c["param"]: c["after"] for c in cands}),
            "torrent.dl_speed_active",
            jsonb({}),
            "Meerdere instellingen buiten de veilige grens: " + summary,
        ),
    )
    audit(
        actor,
        "torrent.tuning_proposed",
        f"Bundelvoorstel veilige grenzen: {summary}",
        target="qbittorrent",
        change_id=ch["id"],
        detail={"risk": risk, "params": [c["param"] for c in cands]},
    )
    auto = _auto_apply(ch["id"]) if auto_mode else None
    sync_runs()
    return {
        "run": run_view(get_db().fetchone("SELECT * FROM torrent_tuning_runs WHERE id=%s", (run["id"],))),
        "change": changes.get(ch["id"]),
        "auto": auto,
        "bundle": True,
    }


def _fmt_metric(metric: str, value: float | None) -> str:
    from mso.features.torrent.diagnostics import human_rate

    if value is None:
        return "geen metingen"
    unit = METRICS[metric]["unit"]
    if unit == "B/s":
        return human_rate(value)
    if unit == "ratio":
        return f"{value * 100:.0f}%"
    return f"{value:g} {unit}"


def _impact(c: dict[str, Any], baseline: dict[str, Any], eval_h: float) -> dict[str, str]:
    """Human-readable impact (Dutch labels, plain strings) — shown as-is by every change preview."""
    meta = METRICS[c["metric"]]
    n = int(baseline.get("n") or 0)
    base = (
        f"mediaan {_fmt_metric(c['metric'], baseline.get('median'))} ({n} metingen)"
        if n
        else "nog geen metingen (een beleidsgrens heeft geen basislijn nodig)"
    )
    if c["kind"] == "guardrail":
        return {
            "Soort": KIND_LABEL["guardrail"],
            "Evaluatie": "geen meting: een beleidsgrens blijft staan",
        }
    return {
        "Soort": KIND_LABEL.get(c["kind"], c["kind"]),
        "Gemeten maatstaf": f"{meta['label']} ({'hoger' if meta['higher'] else 'lager'} is beter)",
        "Basislijn": base,
        "Evaluatie": f"na {eval_h:g} uur (Automatisering); bij verslechtering volgt terugdraaien",
    }


def _create_run(c: dict[str, Any], baseline: dict[str, Any], actor: str) -> dict[str, Any]:
    db = get_db()
    guard = c["kind"] == "guardrail"
    auto_mode = _autopilot()
    if guard and auto_mode:
        c = {**c, "risk": "automatisch_veilig"}  # a policy bound is reversible: Autopilot applies it at once
    eval_h = 0.0 if guard else _jittered_eval_hours()
    meta = METRICS[c["metric"]]
    ch = changes.create(
        "qbittorrent",
        f"Tuning: {c['label']} {_fmt_value(c['before'])} → {c['after']}",
        [{"path": c["param"], "label": c["label"], "before": c["before"], "after": c["after"]}],
        risk=c["risk"],
        explanation=c["why"]
        + (
            " Dit is een beleidsgrens: er volgt geen meting en de waarde blijft staan."
            if guard
            else f" Na {eval_h:g} uur wordt {meta['label'].lower()} vergeleken met de basislijn; "
            "bij verslechtering volgt terugdraaien."
        ),
        impact=_impact(c, baseline, eval_h),
        source="tuning",
        actor=actor,
        dedupe_key=f"torrent-tuning:{c['param']}",
        evaluate_after_h=None if guard else eval_h,
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
    if c["risk"] == "automatisch_veilig" and (
        auto_mode or (ts.get("autotune_enabled") and state.automation_allowed())
    ):
        auto = _auto_apply(ch["id"])
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
        if r["kind"] == "guardrail" and cs == "applied" and r["status"] in ("proposed", "evaluating"):
            db.execute(
                "UPDATE torrent_tuning_runs SET status='kept', applied_at=COALESCE(applied_at, %s), "
                "evaluated_at=now(), note=%s WHERE id=%s",
                (r["capplied"], NO_MEASUREMENT + ": waarde blijft staan", r["id"]),
            )
        elif r["status"] == "proposed" and cs == "applied":
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
    # a finished step (kept / no conclusion, e.g. a guardrail bundle) that a user rolled back later
    db.execute(
        "UPDATE torrent_tuning_runs r SET status='rolled_back', note=CASE WHEN r.note='' THEN "
        "'handmatig teruggedraaid' ELSE r.note || ' · later teruggedraaid' END FROM change_sets c "
        "WHERE c.id=r.change_id AND c.status='rolled_back' AND r.status IN ('kept','inconclusive')"
    )


VERDICT_RUN = {  # evaluator verdict → tuning run status / note prefix
    "better": ("kept", "verbetering gemeten"),
    "same": ("inconclusive", "geen meetbaar effect binnen de hysterese; wijziging blijft staan"),
    "worse": ("degraded_pending", "verslechtering gemeten"),
    "insufficient": ("inconclusive", "onvoldoende metingen; wijziging blijft staan, geen conclusie"),
}
STATUS_NL = {
    "kept": "behouden",
    "inconclusive": "onbeslist",
    "degraded_pending": "verslechterd, terugdraaien via Automatisering",
    "rolled_back": "teruggedraaid",
}
ONE_SHOT = ("crossseed:", ":recheck", ":reannounce")


def _not_measured(reason: str, verdict: str = "not_applicable") -> dict[str, Any]:
    return {
        "verdict": verdict,
        "measures": [],
        "message": f"Niet geëvalueerd: {reason}.",
        "auto_rollback": False,
        "auto_rollback_reason": reason,
    }


def evaluate_change(change: dict[str, Any]) -> dict[str, Any]:
    """Evaluator for target ``qbittorrent`` (registered with the automation engine — the ONE evaluation path).

    Tuning steps: download speed of active torrents (the run's metric) and time to completion, baseline (the
    run's stored baseline window) vs the window after applying (+1 h settle), with
    ``torrent.tuning_hysteresis_pct``. Returns the engine's evaluation dict (verdict better|same|worse|
    insufficient + measures); never rolls back itself — the engine does, once. Guardrails/bundles are not
    measured ("beleidsgrens, geen meting"); deletions never reach an evaluator (the engine skips them)."""
    from mso.features.automation import evaluators as ev

    interim = bool(change.get("_interim"))  # live UI preview: compute only, never write state
    if not interim:
        sync_runs()
    db = get_db()
    run = db.fetchone(
        "SELECT * FROM torrent_tuning_runs WHERE change_id=%s ORDER BY id DESC LIMIT 1", (change["id"],)
    )
    if run is None:
        paths = [str(op.get("path") or "") for op in change.get("ops") or []]
        if any(x in p for p in paths for x in ONE_SHOT):
            return _not_measured("eenmalige actie, geen meting")
        if any(cleanup.is_cleanup_path(p) for p in paths) or any(
            p.startswith("pref:excluded_file_names") for p in paths
        ):
            return _not_measured(NO_MEASUREMENT)
        return ev.generic_evaluator(change)  # manual preference change: generic torrent measures
    if run["kind"] == "guardrail" or run["param"] == BUNDLE_PARAM:
        if run["status"] in OPEN and not interim:
            db.execute(
                "UPDATE torrent_tuning_runs SET status='kept', note=%s, evaluated_at=now() WHERE id=%s",
                (NO_MEASUREMENT + ": waarde blijft staan", run["id"]),
            )
        return _not_measured(NO_MEASUREMENT)
    applied_at = run["applied_at"] or change.get("applied_at")
    if applied_at is None:
        return _not_measured("wijziging is niet toegepast", "insufficient")
    now = datetime.now(UTC)
    hyst = float(ts.get("tuning_hysteresis_pct"))
    min_n = int(ts.get("tuning_min_samples"))
    base = run["baseline"] or {}
    try:
        b_from = datetime.fromisoformat(base["from"])
        b_to = datetime.fromisoformat(base["to"])
    except (KeyError, TypeError, ValueError):
        b_from, b_to = applied_at - timedelta(hours=float(ts.get("baseline_hours"))), applied_at
    cur = (applied_at + timedelta(hours=SETTLE_H), now)
    meta = METRICS.get(run["metric"], {"label": run["metric"], "unit": "", "higher": True})
    primary = ev.measure(
        run["metric"],
        label=meta["label"],
        unit=meta["unit"],
        baseline=ev.observation_series(SUBJECT, run["metric"], b_from, b_to),
        current=ev.observation_series(SUBJECT, run["metric"], *cur),
        higher_is_better=bool(run["higher_is_better"]),
        hysteresis_pct=hyst,
        min_samples=min_n,
        scope="qbittorrent",
    )
    completion = ev.measure(
        "time_to_completion",
        label="Tijd tot voltooiing (mediaan)",
        unit="s",
        baseline=ev.completion_series(applied_at - timedelta(hours=float(ts.get("baseline_hours"))), applied_at,
                                      protocol="torrent", client="qbittorrent"),
        current=ev.completion_series(*cur, protocol="torrent", client="qbittorrent"),
        higher_is_better=False,
        hysteresis_pct=hyst,
        min_samples=COMPLETION_MIN_SAMPLES,
        scope="qbittorrent",
    )
    # the run's own metric decides; time to completion only tips a neutral result to 'worse'
    verdict = primary["verdict"]
    if verdict == "same" and completion["verdict"] == "worse":
        verdict = "worse"
    status, note = VERDICT_RUN[verdict]
    if verdict in ("better", "worse"):
        note = f"{note}: {primary.get('why')}"
    elif verdict == "insufficient":
        note = f"{primary.get('why')}; wijziging blijft staan, geen conclusie"
    result = {
        "verdict": verdict,
        "n": primary["current"]["n"],
        "median": primary["current"]["value"],
        "delta_pct": primary.get("delta_pct"),
        "why": primary.get("why"),
        "completion": {k: completion.get(k) for k in ("verdict", "delta_pct", "why")},
    }
    if not interim:
        db.execute(
            "UPDATE torrent_tuning_runs SET status=%s, result=%s, note=%s, evaluated_at=now() WHERE id=%s",
            (status, jsonb(result), note, run["id"]),
        )
        audit(
            "autotune",
            "torrent.tuning_evaluated",
            f"Tuning {run['param']}: {STATUS_NL[status]} ({note})",
            target="qbittorrent",
            change_id=change["id"],
            detail={"verdict": verdict, "status": status, "delta_pct": primary.get("delta_pct")},
        )
    return ev.evaluation(
        [primary, completion],
        message=f"Tuningstap {run['param']}: {note}",
        verdict=verdict,
        windows={"baseline_from": b_from.isoformat(), "baseline_to": b_to.isoformat(),
                 "current_from": cur[0].isoformat(), "current_to": cur[1].isoformat()},
    )


COMPLETION_MIN_SAMPLES = 3


def evaluate() -> list[dict[str, Any]]:
    """Evaluate due tuning steps now, through the automation engine (the one evaluation path)."""
    from mso.features.automation import engine

    sync_runs()
    db = get_db()
    due = [
        r["id"]
        for r in db.fetchall(
            "SELECT r.id FROM torrent_tuning_runs r JOIN change_sets c ON c.id=r.change_id WHERE r.status="
            "'evaluating' AND c.status='applied' AND c.evaluate_after IS NOT NULL AND c.evaluate_after <= now() "
            "AND NOT (c.impact ? 'evaluation')"
        )
    ]
    if not due:
        return []
    engine.evaluate_due(db)
    sync_runs()
    return [
        {"id": r["id"], "status": r["status"], "note": r["note"]}
        for r in db.fetchall("SELECT * FROM torrent_tuning_runs WHERE id = ANY(%s) ORDER BY id", (due,))
    ]


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
    # evaluation itself runs in the automation engine's job (``automation.evaluate``); this only follows the
    # lifecycle of the linked change sets so a finished step frees the slot for the next proposal
    sync_runs()
    return {"synced": True}


register_evaluator("qbittorrent", evaluate_change)


jobs.every("torrent.tune", 3600, enabled=lambda: cb.is_configured("qbittorrent"))
jobs.every("torrent.tune_evaluate", 1800, enabled=lambda: cb.is_configured("qbittorrent"))
