"""Schemaweigering bewaart tabelbytes en herstelbestanden, ook met twee processen."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
import pytest
import test_foundation
import test_maintenance
from test_foundation import _file_bytes, _newer_schema

schema_db = test_foundation.schema_db
old_db = test_maintenance.old_db
swap_db = test_maintenance.swap_db


@pytest.mark.parametrize("command", [["migrate"], ["backup", "--pre-upgrade"], ["api"], ["worker"]])
def test_newer_schema_after_wait_refuses_without_writes(schema_db: Any, database_bytes: Any,
                                                       command: list[str]) -> None:
    from mso.backups import PRE_UPGRADE_LOCK
    from mso.config import get_settings
    from mso.db import load_migrations

    latest = max(m.version for m in load_migrations())
    schema_db.execute("DELETE FROM schema_migrations WHERE version=%s", (latest,))
    settings = get_settings()
    env = dict(os.environ, MSO_DATABASE_URL=schema_db.url, MSO_DATA_DIR=str(settings.data_dir),
               MSO_WORK_DIR=str(settings.work_dir))
    proc = None
    try:
        with psycopg.connect(schema_db.url, autocommit=True) as lock:
            lock.execute("SELECT pg_advisory_lock(%s)", (PRE_UPGRADE_LOCK,))
            try:
                proc = subprocess.Popen([sys.executable, "-m", "mso", *command], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 20
                while True:
                    # Synchronisatie op de werkelijk wachtende back-uplock; geen slaap als tijdsaanname.
                    blocked = lock.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=%s "
                        "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                        "AND NOT granted)", (PRE_UPGRADE_LOCK,)).fetchone()[0]
                    if blocked:
                        break
                    assert proc.poll() is None, proc.communicate()
                    assert time.monotonic() < deadline, "CLI bereikte de back-uplock niet"
                lock.execute("SELECT pg_advisory_lock(727001)")
                lock.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES (%s,'toekomst','proef')",
                             (latest + 1,))
                lock.execute("SELECT pg_advisory_unlock(727001)")
                before, files = database_bytes(schema_db), _file_bytes(settings.data_dir)
                assert before["users"] != b"[]" and len(files) == 5
            finally:
                lock.execute("SELECT pg_advisory_unlock(%s)", (PRE_UPGRADE_LOCK,))
            out, err = proc.communicate(timeout=30)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.communicate(timeout=5)
    assert proc.returncode == 79, (out, err)
    assert "database is van een nieuwere MSO-versie" in err and "Traceback" not in err
    assert database_bytes(schema_db) == before
    assert _file_bytes(settings.data_dir) == files
    assert schema_db.scalar("SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                            "AND objid IN (727001,727002) AND database=(SELECT oid FROM pg_database "
                            "WHERE datname=current_database())") == 0


def _tree_metadata(root: Path) -> dict:
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        st = path.stat()
        result[str(path.relative_to(root))] = (
            st.st_mode, st.st_uid, st.st_gid, st.st_size, st.st_mtime_ns, st.st_ctime_ns,
            path.read_bytes() if path.is_file() else None)
    return result


@pytest.mark.parametrize("command", [["migrate"], ["backup", "--pre-upgrade"], ["api"], ["worker"]])
def test_backup_and_migration_share_lock(app: Any, old_db: Any, swap_db: Any,
                                        monkeypatch: Any, command: list[str]) -> None:
    import signal
    import threading

    import uvicorn

    from mso import backups, jobs
    from mso.__main__ import main
    from mso.vault import get_vault, set_vault

    def assert_locked() -> None:
        with psycopg.connect(old_db.url, autocommit=True) as probe:
            for key in (727001, backups.PRE_UPGRADE_LOCK):
                acquired = probe.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
                if acquired:
                    probe.execute("SELECT pg_advisory_unlock(%s)", (key,))
                assert not acquired, "back-up en migratie moeten samen vergrendeld blijven"

    dumped = []
    original_dump, original_backup = backups._dump, backups.pre_upgrade_backup

    def dump(**kwargs: Any) -> Path:
        assert_locked()
        dumped.append(True)
        return original_dump(**kwargs)

    def backup(*args: Any, **kwargs: Any) -> dict:
        result = original_backup(*args, **kwargs)
        assert result["status"] == "created", result
        assert_locked()  # Ook het gat tussen de back-up en migrate_up moet dicht zijn.
        return result

    class Worker:
        def __init__(self, *_: Any) -> None:
            self.worker_id = "schema-proef"
            self.stop_event = threading.Event()
            self.stop_event.set()

        def run_forever(self) -> None:
            pass

    monkeypatch.setattr(backups, "_dump", dump)
    monkeypatch.setattr(backups, "pre_upgrade_backup", backup)
    monkeypatch.setattr(uvicorn, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(jobs, "Worker", Worker)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    vault = get_vault()
    try:
        main(command)
        assert dumped == [True]
    finally:
        from mso.db import get_db

        if get_db() is not old_db:
            get_db().close()
        set_vault(vault)


@pytest.mark.parametrize("existing_dirs", [False, True])
def test_supervisor_real_prepare_dirs_refusal_preserves_tree(schema_db: Any, tmp_path: Path,
                                                            monkeypatch: Any, existing_dirs: bool) -> None:
    module, data, trace = _supervisor(schema_db, tmp_path, monkeypatch)
    pgdata = data / "postgres"
    pgdata.mkdir(mode=0o700)
    (pgdata / "PG_VERSION").write_text("16\n")
    if existing_dirs:
        for name in ("backups", "secrets", "work"):
            (data / name).mkdir(mode=0o700)
            (data / name / "bewaren").write_bytes(b"ongewijzigd")
    _newer_schema(schema_db, gap=True)
    before = _tree_metadata(data)
    module.STOP.set()
    module.main()
    assert _tree_metadata(data) == before
    assert trace == ["database-start", "database-stop"]


@pytest.mark.parametrize("fresh", [False, True])
def test_supervisor_real_prepare_dirs_compatible_start(schema_db: Any, tmp_path: Path,
                                                      monkeypatch: Any, fresh: bool) -> None:
    from mso.db import migrate_down

    module, data, trace = _supervisor(schema_db, tmp_path, monkeypatch)
    if fresh:
        migrate_down(schema_db, 0)
        schema_db.execute("DROP TABLE schema_migrations")
    else:
        (data / "postgres").mkdir(mode=0o700)
        (data / "postgres" / "PG_VERSION").write_text("16\n")

    def run_once(name: str, cmd: list[str], *_: Any) -> int:
        trace.append(name)
        return 0

    monkeypatch.setattr(module, "run_once", run_once)
    module.STOP.set()
    module.main()
    assert all((data / name).is_dir() for name in ("postgres", "backups", "secrets", "work"))
    assert trace.count("database-start") == 1 and trace.count("database-stop") == 1
    assert ("initdb" in trace) == fresh
    assert "back-up vóór upgrade" in trace and "migraties" in trace


@pytest.mark.parametrize("traversable", [False, True])
def test_database_preparation_keeps_traversable_parent_metadata(tmp_path: Path, monkeypatch: Any,
                                                               traversable: bool) -> None:
    from types import SimpleNamespace

    module, data, _ = _supervisor(SimpleNamespace(url=""), tmp_path, monkeypatch)
    pgdata = data / "postgres"
    pgdata.mkdir(mode=0o700)
    (data / "backups").mkdir()
    (data / "backups" / "bewaren.dump").write_bytes(b"bewaren")
    data.chmod(0o755 if traversable else 0o700)
    ownership: list[Path] = []
    monkeypatch.setattr(module, "UID", os.getuid() + 1)
    monkeypatch.setattr(module, "GID", os.getgid() + 1)
    monkeypatch.setattr(module.os, "getuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda path, *_: ownership.append(Path(path)))
    monkeypatch.setattr(module.os, "lchown", lambda path, *_: ownership.append(Path(path)))
    before = _tree_metadata(data)
    module.prepare_dirs(database_only=True)
    # Root mag de database-eigenaar herstellen; een al toegankelijke bovenmap hoeft niet te veranderen.
    assert ownership and (data in ownership) is not traversable
    assert all(p in (data, pgdata) for p in ownership if p.is_relative_to(data))
    assert _tree_metadata(data) == before


def test_supervisor_refusal_closes_pool_and_drops_privileges(tmp_path: Path, monkeypatch: Any) -> None:
    from types import SimpleNamespace

    from mso.db import NewerDatabaseError

    module, _, trace = _supervisor(SimpleNamespace(url=""), tmp_path, monkeypatch)
    monkeypatch.setattr(module, "init_database", lambda: None)
    monkeypatch.setattr(module, "Database", lambda *_a, **_k: SimpleNamespace(close=lambda: trace.append("db-close")))
    monkeypatch.setattr(module, "demote", lambda: trace.append("demote"))

    def refuse(_: Any) -> None:
        raise NewerDatabaseError()

    def wait(pg: Any) -> None:
        assert trace == ["database-start", "db-close", "demote"]
        pg.stop()

    monkeypatch.setattr(module, "check_schema_compatibility", refuse)
    monkeypatch.setattr(module, "wait_on_newer_schema", wait)
    module.main()
    assert trace == ["database-start", "db-close", "demote", "database-stop"]


def _supervisor(db: Any, tmp_path: Path, monkeypatch: Any) -> tuple[Any, Path, list[str]]:
    path = Path(__file__).resolve().parents[3] / "deploy/allinone/supervisor.py"
    spec = importlib.util.spec_from_file_location("real_dirs_supervisor", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "appdata"
    data.mkdir()
    socket = tmp_path / "socket"
    socket.mkdir()
    for name, value in {"DATA": data, "PGDATA": data / "postgres", "WORK": data / "work",
                        "SOCKET_DIR": socket, "DB_URL": db.url,
                        "UID": os.getuid(), "GID": os.getgid()}.items():
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module, "demote", lambda: None)
    monkeypatch.setattr(module, "wait_for_db", lambda *_: None)
    monkeypatch.setattr(module.signal, "signal", lambda *_: None)
    trace: list[str] = []

    class Child:
        def __init__(self, name: str, *_: Any, **__: Any) -> None:
            self.name = name

        def start(self) -> None:
            trace.append(self.name + "-start")

        def stop(self, *_: Any) -> None:
            trace.append(self.name + "-stop")

    monkeypatch.setattr(module, "Child", Child)
    return module, data, trace
