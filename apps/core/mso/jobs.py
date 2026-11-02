"""Persistent, restart-safe job queue on PostgreSQL.

- ``enqueue(kind, payload, idempotency_key=...)`` is idempotent: an existing job with the same key is
  returned instead of creating a duplicate.
- Workers claim with ``FOR UPDATE SKIP LOCKED`` and heartbeat while running. On worker start,
  ``recover_stale()`` re-queues jobs whose lease expired (crash/restart) — they resume from the DB.
- Failures retry with exponential backoff (+ jitter) until ``max_attempts``; then ``failed``.
- Handlers register with ``@handler("kind")`` and receive a ``JobContext`` (progress, heartbeat,
  cancellation check).
"""

from __future__ import annotations

import logging
import random
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from mso.db import Database, get_db, jsonb
from mso.security import redact

LOGGER = logging.getLogger(__name__)
LEASE_S = 120
HANDLERS: dict[str, Callable[[JobContext], Any]] = {}


class JobCancelled(Exception):
    pass


class PermanentJobError(Exception):
    """Raise for errors that must not be retried (bad input, policy refusal)."""


def handler(kind: str) -> Callable[[Callable[[JobContext], Any]], Callable[[JobContext], Any]]:
    def deco(fn: Callable[[JobContext], Any]) -> Callable[[JobContext], Any]:
        HANDLERS[kind] = fn
        return fn
    return deco


@dataclass
class JobContext:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    db: Database
    _last_beat: float = field(default_factory=time.monotonic)

    def progress(self, value: float | None, summary: str | None = None) -> None:
        self.db.execute(
            "UPDATE jobs SET progress=%s, summary=COALESCE(%s, summary), heartbeat_at=now(), updated_at=now() "
            "WHERE id=%s", (value, summary, self.id))
        self._last_beat = time.monotonic()
        self.check_cancelled()

    def heartbeat(self) -> None:
        if time.monotonic() - self._last_beat > 10:
            self.db.execute("UPDATE jobs SET heartbeat_at=now() WHERE id=%s", (self.id,))
            self._last_beat = time.monotonic()
            self.check_cancelled()

    def check_cancelled(self) -> None:
        if self.db.scalar("SELECT status FROM jobs WHERE id=%s", (self.id,)) == "cancelled":
            raise JobCancelled()


def enqueue(kind: str, payload: dict[str, Any] | None = None, *, idempotency_key: str | None = None,
            max_attempts: int = 3, delay_s: float = 0, created_by: str | None = None,
            db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    run_after = datetime.now(UTC) + timedelta(seconds=delay_s)
    with db.tx() as conn:
        if idempotency_key:
            existing = conn.execute("SELECT * FROM jobs WHERE idempotency_key=%s", (idempotency_key,)).fetchone()
            if existing is not None:
                return dict(existing)
        row = conn.execute(
            "INSERT INTO jobs(kind,payload,idempotency_key,max_attempts,run_after,created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (idempotency_key) DO NOTHING RETURNING *",
            (kind, jsonb(payload or {}), idempotency_key, max_attempts, run_after, created_by)).fetchone()
        if row is None:  # lost a race on the idempotency key
            row = conn.execute("SELECT * FROM jobs WHERE idempotency_key=%s", (idempotency_key,)).fetchone()
        return dict(row)


def recover_stale(db: Database | None = None, lease_s: int = LEASE_S) -> int:
    """Re-queue running jobs whose heartbeat expired (worker crashed or container restarted)."""
    db = db or get_db()
    n = db.execute(
        "UPDATE jobs SET status='queued', locked_by=NULL, updated_at=now(), "
        "summary=COALESCE(summary,'') || ' · hervat na herstart' "
        "WHERE status='running' AND (heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => %s))",
        (lease_s,))
    if n:
        LOGGER.warning("%d onderbroken job(s) opnieuw in de wachtrij gezet", n)
    return n


def claim(worker_id: str, kinds: list[str] | None = None, db: Database | None = None) -> dict[str, Any] | None:
    db = db or get_db()
    kinds = kinds or list(HANDLERS)
    if not kinds:
        return None
    with db.tx() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' AND run_after <= now() AND kind = ANY(%s) "
            "ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1", (kinds,)).fetchone()
        if row is None:
            return None
        job = conn.execute(
            "UPDATE jobs SET status='running', locked_by=%s, heartbeat_at=now(), attempts=attempts+1, "
            "updated_at=now() WHERE id=%s RETURNING *", (worker_id, row["id"])).fetchone()
        return dict(job)


def run_one(job: dict[str, Any], db: Database | None = None) -> str:
    db = db or get_db()
    fn = HANDLERS.get(job["kind"])
    ctx = JobContext(id=job["id"], kind=job["kind"], payload=job["payload"] or {}, attempts=job["attempts"], db=db)
    if fn is None:
        db.execute("UPDATE jobs SET status='failed', error=%s, updated_at=now() WHERE id=%s",
                   (f"geen handler voor '{job['kind']}'", job["id"]))
        return "failed"
    try:
        result = fn(ctx)
    except JobCancelled:
        db.execute("UPDATE jobs SET locked_by=NULL, updated_at=now() WHERE id=%s", (job["id"],))
        return "cancelled"
    except Exception as exc:  # noqa: BLE001
        msg = redact(f"{type(exc).__name__}: {exc}")
        LOGGER.warning("job %s (%s) mislukt: %s", job["id"], job["kind"], msg)
        LOGGER.debug("%s", redact(traceback.format_exc()))
        permanent = isinstance(exc, PermanentJobError)
        if not permanent and job["attempts"] < job["max_attempts"]:
            backoff = min(3600, (2 ** job["attempts"]) * 15) * (0.8 + random.random() * 0.4)
            db.execute(
                "UPDATE jobs SET status='queued', locked_by=NULL, error=%s, "
                "run_after=now() + make_interval(secs => %s), updated_at=now() WHERE id=%s AND status='running'",
                (msg, backoff, job["id"]))
            return "retry"
        db.execute("UPDATE jobs SET status='failed', locked_by=NULL, error=%s, updated_at=now() "
                   "WHERE id=%s AND status='running'", (msg, job["id"]))
        return "failed"
    db.execute(
        "UPDATE jobs SET status='done', locked_by=NULL, progress=1, result=%s, error=NULL, updated_at=now() "
        "WHERE id=%s AND status='running'", (jsonb(result if result is not None else {}), job["id"]))
    return "done"


def cancel(job_id: int, db: Database | None = None) -> bool:
    db = db or get_db()
    return db.execute("UPDATE jobs SET status='cancelled', updated_at=now() "
                      "WHERE id=%s AND status IN ('queued','running')", (job_id,)) > 0


def retry(job_id: int, db: Database | None = None) -> bool:
    db = db or get_db()
    return db.execute("UPDATE jobs SET status='queued', attempts=0, run_after=now(), error=NULL, updated_at=now() "
                      "WHERE id=%s AND status IN ('failed','cancelled')", (job_id,)) > 0


# ------------------------------------------------------------------ periodic schedules
@dataclass
class Schedule:
    kind: str
    every_s: int
    payload: dict[str, Any] = field(default_factory=dict)
    enabled: Callable[[], bool] = lambda: True


SCHEDULES: list[Schedule] = []


def every(kind: str, seconds: int, payload: dict[str, Any] | None = None,
          enabled: Callable[[], bool] | None = None) -> None:
    SCHEDULES.append(Schedule(kind, seconds, payload or {}, enabled or (lambda: True)))


def enqueue_due_schedules(db: Database | None = None) -> int:
    """Idempotent per time slot: the key ``sched:<kind>:<slot>`` prevents duplicate runs across restarts."""
    db = db or get_db()
    n = 0
    now = int(time.time())
    for sched in SCHEDULES:
        try:
            if not sched.enabled():
                continue
        except Exception:  # noqa: BLE001
            continue
        slot = now // sched.every_s
        before = db.scalar("SELECT count(*) FROM jobs WHERE idempotency_key=%s", (f"sched:{sched.kind}:{slot}",))
        if not before:
            enqueue(sched.kind, sched.payload, idempotency_key=f"sched:{sched.kind}:{slot}", max_attempts=1,
                    created_by="scheduler", db=db)
            n += 1
    return n


class Worker:
    def __init__(self, worker_id: str, db: Database | None = None, poll_s: float = 2.0) -> None:
        self.worker_id = worker_id
        self.db = db or get_db()
        self.poll_s = poll_s
        self.stop_event = threading.Event()

    def run_forever(self) -> None:
        recover_stale(self.db)
        last_sched = 0.0
        while not self.stop_event.is_set():
            try:
                if time.monotonic() - last_sched > 15:
                    enqueue_due_schedules(self.db)
                    recover_stale(self.db)
                    last_sched = time.monotonic()
                job = claim(self.worker_id, db=self.db)
                if job is None:
                    self.stop_event.wait(self.poll_s)
                    continue
                run_one(job, self.db)
            except Exception:  # noqa: BLE001
                LOGGER.exception("workerlus fout; opnieuw proberen")
                self.stop_event.wait(self.poll_s)

    def drain(self, max_jobs: int = 100) -> int:
        """Run queued jobs synchronously (tests)."""
        n = 0
        while n < max_jobs:
            job = claim(self.worker_id, db=self.db)
            if job is None:
                break
            run_one(job, self.db)
            n += 1
        return n
