"""Reliability score per source (``sources.reliability`` 0..1) — reliability first, speed last.

score = availability × (1 − false-positive rate) × latency factor

* availability: share of searches that answered (ok or empty) among ok+empty+timeout+error+auth_error over the
  window; category errors are query-specific and not counted against the source;
* false-positive rate: download outcomes with ``failure_reason='false_positive'`` attributed to the source,
  plus results the user flagged as false positive (observation ``search.false_positive``);
* latency factor: only a mild penalty (1.0 at ≤2 s median, down to 0.85 at ≥15 s) so a slow but reliable
  indexer still ranks above a fast flaky one.

The number of samples is stored in ``sources.meta.reliability`` so the UI can show observation coverage; with
fewer than 5 samples the score stays NULL ("onvoldoende gegevens").
"""

from __future__ import annotations

from typing import Any

from mso import jobs
from mso.db import get_db, jsonb
from mso.domain.outcomes import outcome_at
from mso.features.search import settings as S

MIN_SAMPLES = 5


def latency_factor(median_ms: float | None) -> float:
    if median_ms is None or median_ms <= 2000:
        return 1.0
    if median_ms >= 15000:
        return 0.85
    return round(1.0 - 0.15 * (median_ms - 2000) / 13000, 4)


def score(ok: int, failed: int, fp: int, grabs: int, median_ms: float | None) -> float | None:
    total = ok + failed
    if total < MIN_SAMPLES:
        return None
    availability = ok / total
    fp_rate = fp / grabs if grabs else 0.0
    return round(max(0.0, min(1.0, availability * (1 - min(1.0, fp_rate)) * latency_factor(median_ms))), 4)


def compute_all() -> dict[str, Any]:
    db = get_db()
    days = int(S.get("indexers.reliability_window_d", 14))
    rows = db.fetchall(
        "SELECT subject, "
        " count(*) FILTER (WHERE metric='search.ok' AND value=1) AS ok, "
        " count(*) FILTER (WHERE metric='search.empty' AND value=1) AS empty, "
        " count(*) FILTER (WHERE metric='search.error' AND value=1 "
        "                  AND coalesce(labels->>'status','error') <> 'category_error') AS failed, "
        " count(*) FILTER (WHERE metric='search.false_positive') AS fp_flags, "
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY value) FILTER (WHERE metric='search.latency_ms') "
        "AS p50 "
        "FROM observations WHERE ts > now() - make_interval(days => %s) "
        "  AND metric IN "
        "('search.ok','search.empty','search.error','search.latency_ms','search.false_positive') "
        "GROUP BY subject",
        (days,),
    )
    fps = {
        r["source_id"]: (int(r["fp"]), int(r["n"]))
        for r in db.fetchall(
            "SELECT source_id, count(*) FILTER (WHERE failure_reason='false_positive') AS fp, count(*) AS n "
            "FROM download_outcomes WHERE source_id IS NOT NULL AND status IN ('success','failed') "
            f"AND {outcome_at()} > now() - make_interval(days => %s) GROUP BY source_id",
            (days,),
        )
    }
    updated = {}
    for r in rows:
        sid = r["subject"]
        ok = int(r["ok"]) + int(r["empty"])
        failed = int(r["failed"])
        fp_out, grabs = fps.get(sid, (0, 0))
        fp = fp_out + int(r["fp_flags"])
        grabs = max(grabs, fp)
        val = score(ok, failed, fp, grabs, r["p50"])
        meta = {
            "reliability": {
                "ok": ok,
                "failed": failed,
                "false_positives": fp,
                "grabs": grabs,
                "median_latency_ms": round(r["p50"]) if r["p50"] is not None else None,
                "window_days": days,
                "samples": ok + failed,
            }
        }
        n = db.execute(
            "UPDATE sources SET reliability=%s, meta = meta || %s, updated_at=now() WHERE id=%s",
            (val, jsonb(meta), sid),
        )
        if n:
            updated[sid] = val
    return {"updated": len(updated), "scores": updated}


@jobs.handler("indexers.reliability")
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    return compute_all()


jobs.every("indexers.reliability", 3600)
