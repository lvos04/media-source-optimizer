"""Ook een onderbroken gegevensherstel bewaart actuele scopes en intrekkingen."""

import subprocess
import sys

import pytest

from mso import backups
from mso.app import init_runtime
from mso.config import get_settings
from mso.db import jsonb, migrate_down, migrate_up
from mso.machine_keys import security_snapshot
from tests.conftest import USER_FIXTURE_PASSWORD
from tests.test_machine_keys import create, revoke

RESTORE_PROCESS = """
import os
import sys
from pathlib import Path

from mso import backups, config
from mso.db import Database, set_db

db = Database(sys.argv[1])
set_db(db)
config.set_settings(config.Settings(database_url=db.url, data_dir=Path(sys.argv[2]),
                                    work_dir=Path(sys.argv[3]), testing=True))
original = backups.subprocess.run

def interrupt_after_restore(args, **kwargs):
    result = original(args, **kwargs)
    if Path(args[0]).name == "pg_restore" and "--single-transaction" in args:
        result.check_returncode()
        if sys.argv[5] == "procesdood":
            os._exit(86)
        raise KeyboardInterrupt
    return result

backups.subprocess.run = interrupt_after_restore
try:
    backups.restore_backup(int(sys.argv[4]), "test")
except KeyboardInterrupt:
    sys.exit(87)
"""


@pytest.mark.parametrize("interruption,exitcode", [("procesdood", 86), ("signaal", 87)])
def test_interrupted_restore_keeps_current_permissions(admin, client, db, interruption, exitcode):
    revoked = create(admin)
    changed = create(admin, name="Gewijzigde rechten")
    backup = backups.create_backup(actor="test")
    assert revoke(admin, revoked["item"]["id"]).status_code == 200
    db.execute("UPDATE machine_keys SET scopes=%s WHERE id=%s",
               (jsonb(["changes:approve"]), changed["item"]["id"]))
    before = security_snapshot(db)
    settings = get_settings()
    result = subprocess.run(
        [sys.executable, "-c", RESTORE_PROCESS, db.url, str(settings.data_dir), str(settings.work_dir),
         str(backup["id"]), interruption], capture_output=True, timeout=90, check=False,
    )
    assert result.returncode == exitcode
    db.pool.check()
    if interruption == "procesdood":
        closed = client.get("/api/v1/overview", headers={"X-Api-Key": revoked["key"]})
        assert closed.status_code == 403
        assert "herstel" in closed.json()["detail"]
    login = client.post("/api/v1/auth/login", json={"username": "beheerder", "password": USER_FIXTURE_PASSWORD})
    assert login.status_code == 200

    init_runtime(db)
    assert security_snapshot(db) == before
    assert client.get("/api/v1/overview", headers={"X-Api-Key": revoked["key"]}).status_code == 401
    assert client.get("/api/v1/overview", headers={"X-Api-Key": changed["key"]}).status_code == 403
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='backup.machine_keys_restore_recovered'") == 1


def test_migration_failure_retains_keys_from_current_schema(admin, client, db, monkeypatch):
    assert migrate_down(db, 607) == [608]
    backup = backups.create_backup(actor="test")
    assert migrate_up(db) == [608]
    active = create(admin)
    revoked = create(admin, name="Ingetrokken")
    assert revoke(admin, revoked["item"]["id"]).status_code == 200
    before = security_snapshot(db)

    def failed_migration(*args):
        raise RuntimeError("Schema bijwerken onderbroken in de proef")

    with monkeypatch.context() as patch:
        patch.setattr(backups, "migrate_up", failed_migration)
        with pytest.raises(backups.BackupError, match="schema bijwerken mislukte"):
            backups.restore_backup(backup["id"], "test")

    closed = client.get("/api/v1/overview", headers={"X-Api-Key": active["key"]})
    assert closed.status_code == 403 and closed.json()["code"] == "machine_key_restore_pending"
    init_runtime(db)
    assert security_snapshot(db) == before
    assert client.get("/api/v1/overview", headers={"X-Api-Key": active["key"]}).status_code == 200
    assert client.get("/api/v1/overview", headers={"X-Api-Key": revoked["key"]}).status_code == 401


def test_failed_restore_immediately_preserves_current_keys(admin, client, db, monkeypatch):
    key = create(admin)
    backup = backups.create_backup(actor="test")
    assert revoke(admin, key["item"]["id"]).status_code == 200
    assert create(admin, name="Aangemaakt na de back-up")
    before = security_snapshot(db)

    def fail_after_restore(*args):
        raise backups.BackupError("Herstelproef onderbroken")

    monkeypatch.setattr(backups, "_keep_state", fail_after_restore)
    with pytest.raises(backups.BackupError, match="Herstelproef onderbroken"):
        backups.restore_backup(backup["id"], "test")

    assert security_snapshot(db) == before
    assert client.get("/api/v1/overview", headers={"X-Api-Key": key["key"]}).status_code == 401
