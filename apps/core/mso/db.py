"""PostgreSQL access (psycopg 3, sync pool) + versioned SQL migrations with up/down (rollback).

Migration files: ``mso/migrations/NNNN_name.sql`` with two sections::

    -- migrate:up
    ...
    -- migrate:down
    ...

Every migration runs in its own transaction; ``schema_migrations`` records version, name and a
checksum so a modified, already-applied migration is detected instead of silently diverging.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

LOGGER = logging.getLogger(__name__)
MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 10) -> None:
        self.url = url
        self.pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, open=True,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def tx(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        """One transaction; commits on success, rolls back on exception."""
        with self.pool.connection() as conn:
            with conn.transaction():
                yield conn

    def fetchall(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.tx() as conn:
            return list(conn.execute(sql, params).fetchall())

    def fetchone(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self.tx() as conn:
            return conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> Any:
        row = self.fetchone(sql, params)
        if row is None:
            return None
        return next(iter(row.values()))

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> int:
        with self.tx() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount

    def ping(self) -> bool:
        try:
            return self.scalar("SELECT 1") == 1
        except Exception:  # noqa: BLE001
            return False


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value, dumps=lambda v: json.dumps(v, default=str))


# ---------------------------------------------------------------- migrations
@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: str
    down: str
    checksum: str


def load_migrations() -> list[Migration]:
    out: list[Migration] = []
    pkg = resources.files("mso.migrations")
    for entry in sorted(pkg.iterdir(), key=lambda p: p.name):
        m = MIGRATION_RE.match(entry.name)
        if not m:
            continue
        text = entry.read_text(encoding="utf-8")
        if "-- migrate:up" not in text or "-- migrate:down" not in text:
            raise RuntimeError(f"migration {entry.name} mist '-- migrate:up' of '-- migrate:down'")
        up = text.split("-- migrate:up", 1)[1].split("-- migrate:down", 1)[0]
        down = text.split("-- migrate:down", 1)[1]
        out.append(Migration(int(m.group(1)), m.group(2), up.strip(), down.strip(),
                             hashlib.sha256(text.encode()).hexdigest()))
    versions = [m.version for m in out]
    if len(versions) != len(set(versions)):
        raise RuntimeError("dubbele migratieversie")
    return out


def _ensure_table(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL,"
        " applied_at timestamptz NOT NULL DEFAULT now())")


def applied_migrations(db: Database) -> list[dict[str, Any]]:
    with db.tx() as conn:
        _ensure_table(conn)
        return list(conn.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version").fetchall())


def migrate_up(db: Database, target: int | None = None) -> list[int]:
    """Apply pending migrations (up to ``target``). Uses an advisory lock so api+worker never race."""
    done: list[int] = []
    with db.pool.connection() as conn:
        conn.autocommit = True
        conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            with conn.transaction():
                _ensure_table(conn)
            applied = {r["version"]: r for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
            for mig in load_migrations():
                if target is not None and mig.version > target:
                    break
                if mig.version in applied:
                    if applied[mig.version]["checksum"] != mig.checksum:
                        LOGGER.warning("migratie %s gewijzigd na toepassen (checksum wijkt af)", mig.version)
                    continue
                with conn.transaction():
                    conn.execute(mig.up)
                    conn.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES (%s,%s,%s)",
                                 (mig.version, mig.name, mig.checksum))
                LOGGER.info("migratie %04d_%s toegepast", mig.version, mig.name)
                done.append(mig.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(727001)")
            conn.autocommit = False
    return done


def migrate_down(db: Database, target: int) -> list[int]:
    """Roll back applied migrations with version > ``target`` (newest first)."""
    undone: list[int] = []
    by_version = {m.version: m for m in load_migrations()}
    with db.pool.connection() as conn:
        conn.autocommit = True
        conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            rows = conn.execute(
                "SELECT version FROM schema_migrations WHERE version > %s ORDER BY version DESC",
                (target,)).fetchall()
            for row in rows:
                mig = by_version.get(row["version"])
                if mig is None:
                    raise RuntimeError(f"geen down-script voor migratie {row['version']}")
                with conn.transaction():
                    conn.execute(mig.down)
                    conn.execute("DELETE FROM schema_migrations WHERE version=%s", (mig.version,))
                LOGGER.info("migratie %04d_%s teruggedraaid", mig.version, mig.name)
                undone.append(mig.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(727001)")
            conn.autocommit = False
    return undone


def schema_version(db: Database) -> int:
    rows = applied_migrations(db)
    return max((r["version"] for r in rows), default=0)


_db: Database | None = None


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("database niet geïnitialiseerd")
    return _db


def set_db(db: Database | None) -> None:
    global _db
    _db = db
