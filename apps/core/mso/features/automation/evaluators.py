"""Controle na een wijziging: post-apply evaluators and the shared comparison helpers.

An evaluator is registered per change target (``sabnzbd``, ``qbittorrent`` …)::

    from mso.features.automation.evaluators import register_evaluator, compare, measure, evaluation

    def my_eval(change: dict) -> dict:
        w = windows(change)
        m = measure("download_speed", w, label="Downloadsnelheid actieve torrents", unit="B/s",
                    baseline=observation_series("qbittorrent", "torrent.dl_speed_active", *w.baseline),
                    current=observation_series("qbittorrent", "torrent.dl_speed_active", *w.current),
                    higher_is_better=True)
        return evaluation([m])

    register_evaluator("qbittorrent", my_eval)

The engine passes ``change["_interim"] = True`` when it only computes a live interim view for the UI (GET);
evaluators must not change state in that case.

``not_applicable`` (stored as ``niet_van_toepassing``) means "this change is not measured" (e.g. a policy limit);
it never leads to a rollback.

An evaluator returns either a legacy string (``keep`` | ``rollback`` | ``unknown``), a verdict string
(``better`` | ``same`` | ``worse`` | ``insufficient``), a ``(verdict, details)`` tuple or an
:func:`evaluation` dict. The engine normalises all of them to one stored outcome
(``beter`` | ``gelijk`` | ``slechter`` | ``onvoldoende``) in ``change_sets.impact.evaluation``.

Built-in measures (all read MSO's own tables, nothing is invented):
- ``failure_ratio``     — share of failed downloads in ``download_outcomes`` (lower is better)
- ``time_to_completion`` — median started_at→finished_at of successful downloads (lower is better)
- ``download_speed``    — median of ``observations`` ``qbittorrent / torrent.dl_speed_active`` (higher is better)
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

Evaluator = Callable[[dict[str, Any]], Any]
EVALUATORS: dict[str, Evaluator] = {}
RESULTS = ("keep", "rollback", "unknown")  # legacy evaluator return values (still accepted)
VERDICTS = ("better", "same", "worse", "insufficient", "not_applicable")
OUTCOME = {"better": "beter", "same": "gelijk", "worse": "slechter", "insufficient": "onvoldoende",
           "not_applicable": "niet_van_toepassing"}
OUTCOME_LABEL = {
    "beter": "Beter",
    "gelijk": "Gelijk",
    "slechter": "Slechter",
    "onvoldoende": "Onvoldoende data",
    "niet_van_toepassing": "Niet van toepassing",
}
_LEGACY = {"keep": "same", "rollback": "worse", "unknown": "insufficient"}


def register_evaluator(target: str, fn: Evaluator) -> None:
    EVALUATORS[target] = fn


# ------------------------------------------------------------------ comparison
def _stat(series: Sequence[float], stat: str) -> float | None:
    vals = [float(v) for v in series if v is not None]
    if not vals:
        return None
    return statistics.fmean(vals) if stat == "mean" else float(statistics.median(vals))


def compare(
    baseline_series: Sequence[float],
    eval_series: Sequence[float],
    higher_is_better: bool,
    hysteresis_pct: float,
    *,
    min_samples: int = 5,
    stat: str = "median",
) -> tuple[str, dict[str, Any]]:
    """Compare a measurement series after the change with the baseline series before it.

    Only a relative difference of at least ``hysteresis_pct`` counts as better/worse; fewer than
    ``min_samples`` values on either side gives ``insufficient``. ``stat`` is ``median`` (default, robust)
    or ``mean`` (use it for 0/1 series such as failed/succeeded). Returns ``(verdict, details)`` where verdict
    is ``better`` | ``same`` | ``worse`` | ``insufficient`` and details holds the numbers (Dutch ``why``)."""
    nb, na = len([v for v in baseline_series if v is not None]), len([v for v in eval_series if v is not None])
    b, a = _stat(baseline_series, stat), _stat(eval_series, stat)
    details: dict[str, Any] = {
        "stat": stat,
        "baseline": {"n": nb, "value": b},
        "current": {"n": na, "value": a},
        "delta_pct": None,
        "hysteresis_pct": hysteresis_pct,
        "higher_is_better": higher_is_better,
        "min_samples": min_samples,
    }
    if nb < min_samples or na < min_samples or a is None or b is None:
        details["why"] = f"te weinig metingen (basislijn {nb}, na de wijziging {na}, minimaal {min_samples} elk)"
        return "insufficient", details
    if b == 0:
        delta = 0.0 if a == 0 else 100.0
    else:
        delta = (a - b) / abs(b) * 100
    signed = delta if higher_is_better else -delta
    verdict = "better" if signed >= hysteresis_pct else "worse" if signed <= -hysteresis_pct else "same"
    word = "mediaan" if stat == "median" else "gemiddelde"
    details["delta_pct"] = round(delta, 1)
    details["why"] = f"{word} {b:.4g} → {a:.4g} ({delta:+.1f}%, hysterese ±{hysteresis_pct:g}%)"
    return verdict, details


# ------------------------------------------------------------------ windows and series
@dataclass(frozen=True)
class Windows:
    baseline: tuple[datetime, datetime]
    current: tuple[datetime, datetime]

    def as_dict(self) -> dict[str, str]:
        return {
            "baseline_from": self.baseline[0].isoformat(),
            "baseline_to": self.baseline[1].isoformat(),
            "current_from": self.current[0].isoformat(),
            "current_to": self.current[1].isoformat(),
        }


def windows(
    change: dict[str, Any], *, now: datetime | None = None, max_baseline_days: float = 7, settle_h: float = 0
) -> Windows:
    """Baseline = the period of equal length just before ``applied_at`` (capped at ``max_baseline_days``);
    current = ``applied_at`` (+ ``settle_h``) → now."""
    now = now or datetime.now(UTC)
    applied = change.get("applied_at") or now
    if isinstance(applied, str):
        applied = datetime.fromisoformat(applied)
    length = max(now - applied, timedelta(hours=1))
    length = min(length, timedelta(days=max_baseline_days))
    return Windows(baseline=(applied - length, applied), current=(applied + timedelta(hours=settle_h), now))


def observation_series(subject: str, metric: str, start: datetime, end: datetime) -> list[float]:
    from mso.db import get_db

    rows = get_db().fetchall(
        "SELECT value FROM observations WHERE subject=%s AND metric=%s AND ts >= %s AND ts <= %s "
        "AND value IS NOT NULL ORDER BY ts",
        (subject, metric, start, end),
    )
    return [float(r["value"]) for r in rows]


def _outcome_filter(protocol: str | None, client: str | None) -> tuple[str, list[Any]]:
    sql, params = "", []
    if protocol:
        sql += " AND protocol=%s"
        params.append(protocol)
    if client:
        sql += " AND client=%s"
        params.append(client)
    return sql, params


def failure_series(
    start: datetime, end: datetime, *, protocol: str | None = None, client: str | None = None
) -> list[float]:
    """1.0 per failed and 0.0 per successful download finished (or failed) in the window (mean = failure ratio).
    A failure counts at the moment it became failed, so a torrent stuck in the client is not counted again in
    every later window."""
    from mso.db import get_db
    from mso.domain.outcomes import outcome_at

    extra, params = _outcome_filter(protocol, client)
    at = outcome_at()
    rows = get_db().fetchall(
        f"SELECT status FROM download_outcomes WHERE status IN ('success','failed') "
        f"AND {at} >= %s AND {at} < %s" + extra,
        [start, end, *params],
    )
    return [1.0 if r["status"] == "failed" else 0.0 for r in rows]


def completion_series(
    start: datetime, end: datetime, *, protocol: str | None = None, client: str | None = None
) -> list[float]:
    """Seconds from started_at to finished_at of successful downloads finished in the window."""
    from mso.db import get_db

    extra, params = _outcome_filter(protocol, client)
    rows = get_db().fetchall(
        "SELECT EXTRACT(EPOCH FROM (finished_at - started_at)) AS s FROM download_outcomes "
        "WHERE status='success' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
        "AND finished_at >= started_at AND finished_at >= %s AND finished_at < %s" + extra,
        [start, end, *params],
    )
    return [float(r["s"]) for r in rows]


# ------------------------------------------------------------------ results
def measure(
    key: str,
    *,
    label: str,
    unit: str,
    baseline: Sequence[float],
    current: Sequence[float],
    higher_is_better: bool,
    hysteresis_pct: float,
    min_samples: int,
    stat: str = "median",
    scope: str = "",
) -> dict[str, Any]:
    """One compared measure in the stored shape (shown as 'basislijn → nu' in Automatisering)."""
    verdict, d = compare(
        baseline, current, higher_is_better, hysteresis_pct, min_samples=min_samples, stat=stat
    )
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "scope": scope,
        "verdict": verdict,
        "outcome": OUTCOME[verdict],
        **d,
    }


def combine(measures: Sequence[dict[str, Any]]) -> str:
    """Conclusive measures decide: only worse → worse, only better → better, mixed or neutral → same;
    no conclusive measure → insufficient."""
    verdicts = {m["verdict"] for m in measures if m.get("verdict") != "insufficient"}
    if not verdicts:
        return "insufficient"
    if "worse" in verdicts and "better" not in verdicts:
        return "worse"
    if "better" in verdicts and "worse" not in verdicts:
        return "better"
    return "same"


def evaluation(measures: Sequence[dict[str, Any]], message: str = "", **extra: Any) -> dict[str, Any]:
    verdict = combine(measures)
    return {"verdict": verdict, "measures": list(measures), "message": message, **extra}


def normalise(raw: Any) -> dict[str, Any]:
    """Any supported evaluator return value → ``{verdict, measures, message, ...}``."""
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[0], str):
        verdict, details = raw
        details = dict(details or {})
        verdict = _LEGACY.get(verdict, verdict)
        if verdict not in VERDICTS:
            raise ValueError(f"onbekende evaluatie-uitkomst '{verdict}'")
        measures = details.pop("measures", None) or ([details] if details else [])
        return {"verdict": verdict, "measures": measures, "message": str(details.get("why") or "")}
    if isinstance(raw, str):
        verdict = _LEGACY.get(raw, raw)
        if verdict not in VERDICTS:
            raise ValueError(f"onbekende evaluatie-uitkomst '{raw}'")
        return {"verdict": verdict, "measures": [], "message": "", "legacy": raw if raw in RESULTS else None}
    if isinstance(raw, dict):
        out = dict(raw)
        v = _LEGACY.get(str(out.get("verdict") or ""), out.get("verdict"))
        if v not in VERDICTS:
            v = combine(out.get("measures") or [])
        out["verdict"] = v
        out.setdefault("measures", [])
        out.setdefault("message", "")
        return out
    raise ValueError(f"onbekende evaluatie-uitkomst '{raw!r}'")


# ------------------------------------------------------------------ built-in evaluators
def _settings() -> tuple[float, int, float]:
    from mso import settings_registry as sr

    return (
        float(sr.get("automation.evaluation_hysteresis_pct")),
        int(sr.get("automation.evaluation_min_samples")),
        float(sr.get("automation.evaluation_baseline_days")),
    )


PROTOCOL_OF_TARGET = {"sabnzbd": "usenet", "nntp": "usenet", "qbittorrent": "torrent",
                      "transmission": "torrent", "deluge": "torrent"}
CLIENT_OF_TARGET = {"sabnzbd": "sabnzbd", "qbittorrent": "qbittorrent",
                    "transmission": "transmission", "deluge": "deluge"}


def outcome_measures(
    change: dict[str, Any], *, protocol: str | None, client: str | None, now: datetime | None = None
) -> list[dict[str, Any]]:
    hyst, min_n, base_days = _settings()
    w = windows(change, now=now, max_baseline_days=base_days)
    scope = client or (f"alle {protocol}-downloads" if protocol else "alle downloads")
    kw = {"protocol": protocol, "client": client}
    return [
        measure(
            "failure_ratio",
            label="Faalratio downloads",
            unit="ratio",
            baseline=failure_series(*w.baseline, **kw),
            current=failure_series(*w.current, **kw),
            higher_is_better=False,
            hysteresis_pct=hyst,
            min_samples=min_n,
            stat="mean",
            scope=scope,
        ),
        measure(
            "time_to_completion",
            label="Tijd tot voltooiing (mediaan)",
            unit="s",
            baseline=completion_series(*w.baseline, **kw),
            current=completion_series(*w.current, **kw),
            higher_is_better=False,
            hysteresis_pct=hyst,
            min_samples=min_n,
            scope=scope,
        ),
    ]


def speed_measure(change: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    hyst, min_n, base_days = _settings()
    w = windows(change, now=now, max_baseline_days=base_days)
    return measure(
        "download_speed",
        label="Downloadsnelheid actieve torrents",
        unit="B/s",
        baseline=observation_series("qbittorrent", "torrent.dl_speed_active", *w.baseline),
        current=observation_series("qbittorrent", "torrent.dl_speed_active", *w.current),
        higher_is_better=True,
        hysteresis_pct=hyst,
        min_samples=min_n,
        scope="qbittorrent",
    )


_ON = {"1", "true", "on", "yes", "aan"}
_OFF = {"0", "false", "off", "no", "uit"}


def flag(value: Any) -> bool | None:
    """On/off value as stored by an applier (bool, int 0/1 as SABnzbd uses, or a word), else None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        return True if v in _ON else False if v in _OFF else None
    return None


def reenabled_servers(change: dict[str, Any]) -> list[str]:
    """SABnzbd servers this change switched on (``servers/<name>/enable`` 0 → 1) when that is ALL it did."""
    if change.get("target") != "sabnzbd":
        return []
    names = []
    for op in change.get("ops") or []:
        path = str(op.get("path") or "")
        if not (path.startswith("servers/") and path.endswith("/enable")) or flag(op.get("after")) is not True:
            return []
        names.append(path[len("servers/"):-len("/enable")])
    return names


def server_enable_evaluation(change: dict[str, Any], servers: list[str]) -> dict[str, Any]:
    """A re-enabled SABnzbd server is judged on its OWN status since the switch-on (SABnzbd's per-server errors and
    status, :mod:`mso.features.usenet.serverstatus`), never on the global failure ratio. Never rolled back by the
    engine: the server watch (autoheal) switches a failing server off again itself, with its own backoff."""
    from mso.db import get_db

    try:
        from mso.features.usenet import serverstatus
    except Exception:  # usenet module disabled
        return evaluation([], "SABnzbd-serverstatus is niet beschikbaar; niet beoordeeld.", auto_rollback=False,
                          auto_rollback_reason="de serverbewaking regelt deze server", propose_rollback=False)
    applied = change.get("applied_at") or datetime.now(UTC)
    if isinstance(applied, str):
        applied = datetime.fromisoformat(applied)
    current = serverstatus.current()
    events = get_db().fetchall(
        "SELECT server, status, message, last_at FROM usenet_sab_server_events WHERE server = ANY(%s) "
        "AND status = ANY(%s) AND last_at > %s ORDER BY last_at DESC",
        (servers, sorted(serverstatus.FAILING), applied),
    )
    measures: list[dict[str, Any]] = []
    worse: list[str] = []
    for name in servers:
        st = current.get(name) or {}
        label = serverstatus.short_name(st.get("displayname") or name)
        err = next((e for e in events if e["server"] == name), None)
        status = st.get("status")
        if err is not None:
            verdict, value = "worse", 0.0
            why = (f"SABnzbd meldt sinds het inschakelen: {serverstatus.LABELS.get(err['status'], err['status'])}"
                   + (f" (“{str(err['message'])[:160]}”)" if err.get("message") else ""))
        elif status in serverstatus.FAILING:
            verdict, value = "worse", 0.0
            why = f"SABnzbd meldt nu: {serverstatus.LABELS.get(status, status)}"
        elif status == "ok":
            verdict, value = "better", 1.0
            why = "werkt: SABnzbd meldt geen fouten sinds het inschakelen"
        elif status == "disabled":
            verdict, value = "insufficient", None
            why = "staat inmiddels weer uit"
        else:
            verdict, value = "insufficient", None
            why = "SABnzbd geeft (nog) geen status voor deze server"
        if verdict == "worse":
            worse.append(label)
        measures.append({
            "key": f"server_status:{name}", "label": f"Serverstatus {label}", "unit": "status", "scope": "sabnzbd",
            "verdict": verdict, "outcome": OUTCOME[verdict], "stat": None, "higher_is_better": True,
            "baseline": {"n": 1, "value": 0.0}, "current": {"n": 0 if value is None else 1, "value": value},
            "delta_pct": None, "why": why,
        })
    if worse:
        message = (f"{', '.join(worse)} geeft na het inschakelen opnieuw fouten. De serverbewaking regelt dit: "
                   "in Autopilot gaat de server vanzelf weer uit (met een langere wachttijd), anders staat er een "
                   "voorstel om hem uit te schakelen. Deze wijziging wordt niet op de algemene faalratio beoordeeld "
                   "en niet automatisch teruggedraaid.")
    else:
        message = ("Beoordeeld op de eigen status van de server in SABnzbd (niet op de algemene faalratio van alle "
                   "downloads).")
    return evaluation(measures, message, auto_rollback=False, propose_rollback=False,
                      auto_rollback_reason="een heringeschakelde server wordt door de serverbewaking beoordeeld",
                      windows=windows(change, max_baseline_days=_settings()[2]).as_dict(), server_status=True)


def sabnzbd_evaluator(change: dict[str, Any]) -> dict[str, Any]:
    servers = reenabled_servers(change)
    if servers:
        return server_enable_evaluation(change, servers)
    ms = outcome_measures(change, protocol="usenet", client="sabnzbd")
    return evaluation(ms, windows=windows(change, max_baseline_days=_settings()[2]).as_dict())


def generic_evaluator(change: dict[str, Any]) -> dict[str, Any]:
    """Fallback for every target without its own evaluator: download outcomes of the target's protocol/client
    (all downloads when the target has none, e.g. Prowlarr), plus torrent speed for torrent clients."""
    target = change.get("target") or ""
    protocol, client = PROTOCOL_OF_TARGET.get(target), CLIENT_OF_TARGET.get(target)
    ms = outcome_measures(change, protocol=protocol, client=client)
    if protocol == "torrent":
        ms.insert(0, speed_measure(change))
    return evaluation(ms, windows=windows(change, max_baseline_days=_settings()[2]).as_dict(), generic=True)


register_evaluator("sabnzbd", sabnzbd_evaluator)
