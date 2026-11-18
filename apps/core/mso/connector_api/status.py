"""Onafhankelijke readiness en actuele signalen uit MSO."""

from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
from psycopg_pool import PoolTimeout
from pydantic import AwareDatetime, BaseModel

from mso import backups
from mso.connectors.base import SPECS
from mso.db import Database, get_db, load_migrations
from mso.vault import get_vault

CONNECTOR_MAX_AGE = 900
WORKER_MAX_AGE = 90
BACKUP_MAX_AGE = 2 * 86400
JOB_STALL_AGE = 900
SEVERITY = ("healthy", "unknown", "degraded", "down")
CONNECTOR_STATUS = {"healthy": "healthy", "degraded": "degraded", "offline": "down", "auth_failed": "degraded"}
Status = Literal["healthy", "unknown", "degraded", "down"]


class Check(BaseModel):
    name: str
    status: Status
    observed_at: AwareDatetime | None
    detail: dict[str, Any]


class Health(BaseModel):
    status: Status
    observed_at: AwareDatetime
    reason_code: str | None
    checks: list[Check]


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]


def worker_at(db: Database) -> datetime | None:
    return as_utc(db.scalar("SELECT updated_at FROM system_state WHERE key='worker_heartbeat'"))


def as_utc(at: datetime | None) -> datetime | None:
    return at.astimezone(UTC) if at is not None else None


def fresh(at: datetime | None, now: datetime, max_age: int) -> bool:
    return at is not None and 0 <= (now - at).total_seconds() <= max_age


def measurement_reason(at: datetime | None, now: datetime, max_age: int) -> str | None:
    if at is None:
        return "meting_ontbreekt"
    if at > now:
        return "meting_in_toekomst"
    return None if fresh(at, now, max_age) else "meting_verouderd"


def connector_status(status: str, at: datetime | None, now: datetime) -> tuple[Status, str | None]:
    reason = measurement_reason(at, now, CONNECTOR_MAX_AGE)
    if reason:
        return "unknown", reason
    return CONNECTOR_STATUS.get(status, "unknown"), None


def _schema_ready(db: Database) -> bool:
    applied = db.fetchall("SELECT version, checksum FROM schema_migrations")
    return {row["version"]: row["checksum"] for row in applied} == {m.version: m.checksum for m in load_migrations()}


def readiness() -> Readiness:
    db = get_db()
    checks = dict.fromkeys(("database", "schema", "vault", "worker", "space"), False)
    checks["database"] = db.ping()
    if checks["database"]:
        try:
            checks["schema"] = _schema_ready(db)
            if checks["schema"]:
                checks["vault"] = get_vault().key_file_is_current()
                checks["worker"] = fresh(worker_at(db), datetime.now(UTC), WORKER_MAX_AGE)
                space = backups.space_status(db)
                checks["space"] = space["ok"] and space["free_bytes"] is not None
        except (psycopg.Error, PoolTimeout):
            pass
    return Readiness(status="ready" if all(checks.values()) else "not_ready", checks=checks)


def _jobs(db: Database) -> Check:
    row = db.fetchone(
        """SELECT count(*) FILTER (WHERE status='queued') AS queued,
                  count(*) FILTER (WHERE status='running') AS running,
                  max(updated_at) FILTER (WHERE status='done') AS last_done,
                  min(run_after) FILTER (WHERE status='queued') AS waiting_since,
                  now() AS measured_at
           FROM jobs""")
    now = row["measured_at"].astimezone(UTC)
    stalled = row["waiting_since"] is not None and (now - row["waiting_since"]).total_seconds() > JOB_STALL_AGE
    last = row["last_done"]
    last_utc = last.astimezone(UTC).isoformat().replace("+00:00", "Z") if last else None
    return Check(name="jobs", status="degraded" if stalled else "healthy", observed_at=now,
                 detail={"wachtrij": row["queued"], "bezig": row["running"], "laatste_job_klaar": last_utc})


def collect_health() -> Health:
    db = get_db()
    ok = db.ping()
    checks = [Check(name="database", status="healthy" if ok else "down", observed_at=datetime.now(UTC), detail={})]
    if ok:
        hb = worker_at(db)
        now = datetime.now(UTC)
        worker_reason = measurement_reason(hb, now, WORKER_MAX_AGE)
        checks.append(Check(name="worker", status="unknown" if worker_reason else "healthy", observed_at=hb,
                            detail={"reason_code": worker_reason} if worker_reason else {}))
        checks.append(_jobs(db))
        last_backup = as_utc(db.scalar("SELECT max(created_at) FROM backups"))
        now = datetime.now(UTC)
        reason = measurement_reason(last_backup, now, BACKUP_MAX_AGE)
        backup_reasons = {"meting_ontbreekt": "backup_ontbreekt", "meting_verouderd": "backup_verouderd"}
        backup_reason = backup_reasons.get(reason, reason)
        checks.append(Check(name="backup", status="degraded" if backup_reason == "backup_verouderd" else
                            "unknown" if backup_reason else "healthy", observed_at=now,
                            detail={"reason_code": backup_reason, "laatste_backup": last_backup}))
        for row in db.fetchall("SELECT id,last_checked_at,status FROM connectors WHERE enabled ORDER BY id"):
            # Alleen geregistreerde connector-ID's worden onderdeel van het contract.
            if row["id"] not in SPECS:
                continue
            at = as_utc(row["last_checked_at"])
            state, reason = connector_status(row["status"], at, datetime.now(UTC))
            checks.append(Check(name="connector." + row["id"], status=state, observed_at=at,
                                detail={"reason_code": reason} if reason else {}))
    worst = max(checks, key=lambda c: (SEVERITY.index(c.status), c.detail.get("reason_code") == "meting_verouderd"))
    reason = None if worst.status == "healthy" else worst.detail.get("reason_code") or worst.name.replace(".", "_")
    observed_at = min(check.observed_at for check in checks if check.observed_at is not None)
    return Health(status=worst.status, observed_at=observed_at, reason_code=reason, checks=checks)
