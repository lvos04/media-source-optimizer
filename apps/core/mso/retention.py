"""Daily maintenance ``system.retention``: MSO's own history stays small and fast for months and years.

What it cleans (settings under Instellingen → Systeem; defaults in brackets):

- finished background jobs (done/cancelled) older than ``retention.jobs_done_days`` [14 d], always keeping the newest
  ``retention.jobs_keep_per_kind`` [50] per kind; failed jobs older than ``retention.jobs_failed_days`` [60 d];
- raw ``observations`` older than ``retention.observations_raw_days`` [30 d] are first rolled up into
  ``observations_hourly`` (min/max/avg/median/sum/count per hour), which is kept ``retention.observations_hourly_days``
  [400 d]. The raw window is automatically extended when an evaluation or a score looks further back
  (``raw_retention``), so no evaluation ever loses its baseline;
- ``automation_log`` [180 d] and ``login_attempts`` [90 d];
- safety backups (``backups.prune_safety``) and stale connector alerts.

The audit log is evidence and is never cleaned. Afterwards ``VACUUM (ANALYZE)`` runs on the cleaned tables (outside a
transaction, which VACUUM requires); it never locks a table for writers. Only MSO's own rows are removed: nothing
in SABnzbd, qBittorrent, Prowlarr or MediaGuard and no file is touched.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from mso import backups, jobs, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.auth import Principal, require
from mso.db import Database, get_db

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/system", tags=["systeem"])
STATE_KEY = "system.retention_last"
CAT = "systeem"
MAX_RUN_S = 20 * 60  # one run never takes longer; the rest follows the next day
VACUUM_TABLES = ("jobs", "observations", "observations_hourly", "automation_log", "login_attempts")

sr.define("retention.enabled", "Dagelijks opruimen", "bool", True, category=CAT, risk="automatisch_veilig",
          help="Ruimt elke dag MSO's eigen oude taken, metingen en logregels op en houdt de database klein. Raakt "
          "nooit iets in SABnzbd, qBittorrent, Prowlarr of MediaGuard en nooit bestanden. Het auditlogboek blijft "
          "altijd bewaard.")
sr.define("retention.jobs_done_days", "Afgeronde achtergrondtaken bewaren", "int", 14, min=1, max=365,
          unit="dagen", category=CAT, help="Van elke taaksoort blijven de nieuwste altijd staan (zie hieronder).")
sr.define("retention.jobs_keep_per_kind", "Altijd bewaren per taaksoort", "int", 50, min=1, max=1000,
          unit="taken", category=CAT, advanced=True)
sr.define("retention.jobs_failed_days", "Mislukte achtergrondtaken bewaren", "int", 60, min=7, max=730,
          unit="dagen", category=CAT)
sr.define("retention.observations_raw_days", "Losse metingen bewaren", "int", 30, min=7, max=365, unit="dagen",
          category=CAT, help="Oudere metingen worden samengevat per uur (minimum, maximum, gemiddelde, mediaan). "
          "MSO verlengt dit vanzelf als een evaluatie of score verder terugkijkt.")
sr.define("retention.observations_hourly_days", "Uursamenvattingen bewaren", "int", 400, min=30, max=3650,
          unit="dagen", category=CAT)
sr.define("retention.automation_log_days", "Automatiseringslogboek bewaren", "int", 180, min=7, max=3650,
          unit="dagen", category=CAT)
sr.define("retention.login_attempts_days", "Inlogpogingen bewaren", "int", 90, min=7, max=3650, unit="dagen",
          category=CAT)
sr.define("retention.vacuum", "Database daarna opschonen (VACUUM ANALYZE)", "bool", True, category=CAT,
          advanced=True, help="Maakt vrijgekomen ruimte herbruikbaar en houdt de querystatistieken actueel.")


def _setting(key: str, default: Any) -> Any:
    try:
        return sr.get(key)
    except KeyError:  # module not loaded (MSO_FEATURES subset)
        return default


def raw_retention(db: Database | None = None) -> dict[str, Any]:
    """Effective raw-observation window: the setting, extended to the longest look-back of the readers
    (automatic-change evaluations: period + baseline; indexer reliability score)."""
    configured = int(_setting("retention.observations_raw_days", 30))
    needs = []
    eval_h = float(_setting("automation.evaluation_hours", 24))
    base_d = float(_setting("automation.evaluation_baseline_days", 7))
    # evaluations run after max(evaluation_hours, 7 d) (the longest own period a proposal uses) and compare with a
    # baseline of up to baseline_days before applying, plus one day margin
    needs.append({"reader": "Evaluatie van automatische wijzigingen",
                  "days": int(math.ceil(max(eval_h / 24, 7) + base_d + 1))})
    needs.append({"reader": "Betrouwbaarheidsscore indexers",
                  "days": int(_setting("indexers.reliability_window_d", 14)) + 1})
    effective = max([configured, *[n["days"] for n in needs]])
    return {"configured_days": configured, "effective_days": effective, "extended": effective > configured,
            "readers": needs}


# ------------------------------------------------------------------ steps
def purge_jobs(db: Database) -> dict[str, int]:
    done_days = int(_setting("retention.jobs_done_days", 14))
    keep = int(_setting("retention.jobs_keep_per_kind", 50))
    failed_days = int(_setting("retention.jobs_failed_days", 60))
    done = db.execute(
        "DELETE FROM jobs WHERE status IN ('done','cancelled') AND updated_at < now() - make_interval(days => %s) "
        "AND id NOT IN (SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY kind ORDER BY id DESC) AS rn "
        "FROM jobs WHERE status IN ('done','cancelled')) k WHERE rn <= %s)", (done_days, keep))
    failed = db.execute(
        "DELETE FROM jobs WHERE status='failed' AND updated_at < now() - make_interval(days => %s)", (failed_days,))
    return {"jobs_done": done, "jobs_failed": failed}


_ROLLUP_SQL = (
    "INSERT INTO observations_hourly(hour,subject,metric,labels,n,n_values,min,max,avg,median,sum) "
    "SELECT date_trunc('hour', ts), subject, metric, labels, count(*), count(value), min(value), max(value), "
    "avg(value), percentile_cont(0.5) WITHIN GROUP (ORDER BY value), sum(value) "
    "FROM observations WHERE ts < %s GROUP BY 1, 2, 3, 4 "
    "ON CONFLICT (subject, metric, hour, labels) DO UPDATE SET "
    "n = observations_hourly.n + EXCLUDED.n, "
    "n_values = observations_hourly.n_values + EXCLUDED.n_values, "
    "min = LEAST(observations_hourly.min, EXCLUDED.min), "
    "max = GREATEST(observations_hourly.max, EXCLUDED.max), "
    "sum = CASE WHEN observations_hourly.sum IS NULL THEN EXCLUDED.sum WHEN EXCLUDED.sum IS NULL "
    "THEN observations_hourly.sum ELSE observations_hourly.sum + EXCLUDED.sum END, "
    "avg = CASE WHEN observations_hourly.n_values + EXCLUDED.n_values > 0 THEN "
    "(COALESCE(observations_hourly.sum, 0) + COALESCE(EXCLUDED.sum, 0)) "
    "/ (observations_hourly.n_values + EXCLUDED.n_values) END, "
    "median = COALESCE(observations_hourly.median, EXCLUDED.median)"
)


def rollup_observations(db: Database, *, deadline: float | None = None, ctx: jobs.JobContext | None = None,
                        now: datetime | None = None) -> dict[str, int]:
    """Roll raw observations older than the effective raw window up per hour, then delete them. One day per
    transaction (oldest first; an hour is always rolled up completely), so a crash never loses or doubles data."""
    days = raw_retention(db)["effective_days"]
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    oldest = db.scalar("SELECT min(ts) FROM observations")
    raw_deleted = buckets = 0
    while oldest is not None and oldest < cutoff:
        if deadline is not None and time.monotonic() > deadline:
            break
        chunk_end = min(oldest.replace(minute=0, second=0, microsecond=0) + timedelta(days=1), cutoff)
        with db.tx() as conn:
            buckets += conn.execute(_ROLLUP_SQL, (chunk_end,)).rowcount
            raw_deleted += conn.execute("DELETE FROM observations WHERE ts < %s", (chunk_end,)).rowcount
        if ctx is not None:
            ctx.heartbeat()
        oldest = db.scalar("SELECT min(ts) FROM observations")
    hourly_days = int(_setting("retention.observations_hourly_days", 400))
    hourly_deleted = db.execute("DELETE FROM observations_hourly WHERE hour < %s",
                                (now - timedelta(days=hourly_days),))
    return {"observations_raw": raw_deleted, "observations_hourly_written": buckets,
            "observations_hourly": hourly_deleted}


def purge_logs(db: Database) -> dict[str, int]:
    return {
        "automation_log": db.execute("DELETE FROM automation_log WHERE ts < now() - make_interval(days => %s)",
                                     (int(_setting("retention.automation_log_days", 180)),)),
        "login_attempts": db.execute("DELETE FROM login_attempts WHERE ts < now() - make_interval(days => %s)",
                                     (int(_setting("retention.login_attempts_days", 90)),)),
    }


def vacuum(db: Database, tables: list[str]) -> list[str]:
    """``VACUUM (ANALYZE)`` cannot run inside a transaction: use one pooled connection in autocommit."""
    done = []
    with db.pool.connection() as conn:
        conn.autocommit = True
        try:
            for t in tables:
                if t not in VACUUM_TABLES:  # fixed allow-list: never a name from outside
                    continue
                conn.execute(f"VACUUM (ANALYZE) {t}")
                done.append(t)
        finally:
            conn.autocommit = False
    return done


def _sweep_connector_alerts(db: Database) -> None:
    from mso.connectors import monitor

    for r in db.fetchall("SELECT id FROM connectors"):
        try:
            monitor.sync_alert(r["id"], db=db)
        except Exception as exc:
            LOGGER.info("koppelingsmelding %s niet bijgewerkt: %s", r["id"], type(exc).__name__)


def run(db: Database | None = None, *, actor: str = "systeem", ctx: jobs.JobContext | None = None) -> dict[str, Any]:
    db = db or get_db()
    started = time.monotonic()
    deadline = started + MAX_RUN_S
    deleted: dict[str, int] = {}
    deleted |= purge_jobs(db)
    deleted |= rollup_observations(db, deadline=deadline, ctx=ctx)
    deleted |= purge_logs(db)
    deleted["backups"] = backups.prune_safety(db)
    _sweep_connector_alerts(db)
    touched = [t for t, keys in (("jobs", ("jobs_done", "jobs_failed")),
                                 ("observations", ("observations_raw",)),
                                 ("observations_hourly", ("observations_hourly_written", "observations_hourly")),
                                 ("automation_log", ("automation_log",)),
                                 ("login_attempts", ("login_attempts",)))
               if any(deleted.get(k) for k in keys)]
    vacuumed: list[str] = []
    if touched and bool(_setting("retention.vacuum", True)):
        try:
            vacuumed = vacuum(db, touched)
        except Exception as exc:  # maintenance must never fail the clean-up itself
            LOGGER.warning("VACUUM mislukt: %s", type(exc).__name__)
    raw = raw_retention(db)
    removed = sum(v for k, v in deleted.items() if k != "observations_hourly_written")
    result = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 1),
        "deleted": deleted,
        "removed_total": removed,
        "vacuumed": vacuumed,
        "raw_days": raw["effective_days"],
        "complete": time.monotonic() < deadline,
    }
    state.set_state(STATE_KEY, result, db)
    if removed:
        audit(actor, "system.retention",
              f"Onderhoud: {deleted.get('jobs_done', 0) + deleted.get('jobs_failed', 0)} oude taken, "
              f"{deleted.get('observations_raw', 0)} losse metingen (samengevat per uur) en "
              f"{deleted.get('automation_log', 0) + deleted.get('login_attempts', 0)} logregels opgeruimd",
              detail=result)
    return result


def summary_line(res: dict[str, Any]) -> str:
    d = res.get("deleted") or {}
    return (f"{d.get('jobs_done', 0) + d.get('jobs_failed', 0)} taken, {d.get('observations_raw', 0)} metingen, "
            f"{d.get('automation_log', 0) + d.get('login_attempts', 0)} logregels opgeruimd"
            + (f"; VACUUM op {len(res.get('vacuumed') or [])} tabel(len)" if res.get("vacuumed") else ""))


@jobs.handler("system.retention")
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    res = run(actor=ctx.payload.get("actor", "systeem"), ctx=ctx)
    ctx.progress(1, summary_line(res))
    return res


jobs.every("system.retention", 24 * 3600, enabled=lambda: bool(_setting("retention.enabled", True)))


# ------------------------------------------------------------------ API
def database_usage(db: Database) -> dict[str, Any]:
    tables = db.fetchall(
        "SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes, c.reltuples::bigint AS rows "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 2 DESC LIMIT 6")
    return {"size_bytes": int(db.scalar("SELECT pg_database_size(current_database())") or 0),
            "tables": [{"name": t["name"], "bytes": int(t["bytes"] or 0), "rows": max(int(t["rows"] or 0), 0)}
                       for t in tables]}


@router.get("/maintenance")
def maintenance(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    db = get_db()
    kinds = {r["kind"]: {"count": int(r["n"]), "bytes": int(r["bytes"] or 0)}
             for r in db.fetchall("SELECT kind, count(*) AS n, sum(size_bytes) AS bytes FROM backups GROUP BY kind")}
    return {
        "retention": {
            "enabled": bool(_setting("retention.enabled", True)),
            "last": state.get_state(STATE_KEY, None, db),
            "raw": raw_retention(db),
            "settings": {k: _setting(k, None) for k in (
                "retention.jobs_done_days", "retention.jobs_keep_per_kind", "retention.jobs_failed_days",
                "retention.observations_raw_days", "retention.observations_hourly_days",
                "retention.automation_log_days", "retention.login_attempts_days")},
        },
        "database": database_usage(db),
        "backups": {"space": backups.space_status(db), "kinds": kinds,
                    "excluded_tables": list(backups.REFETCHABLE),
                    "safety": {"pre_restore_days": backups.SAFETY_DAYS, "pre_restore_keep": backups.SAFETY_KEEP,
                               "pre_upgrade_keep": backups.UPGRADE_KEEP}},
    }


@router.post("/maintenance/run")
def maintenance_run(p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    job = jobs.enqueue("system.retention", {"actor": p.actor}, idempotency_key=f"retention:{int(time.time() // 60)}",
                       max_attempts=1, created_by=p.actor)
    audit(p.actor, "system.retention_requested", "Onderhoud (opruimen) handmatig gestart")
    return {"job_id": job["id"], "status": job["status"]}


__all__ = ["raw_retention", "rollup_observations", "router", "run"]
