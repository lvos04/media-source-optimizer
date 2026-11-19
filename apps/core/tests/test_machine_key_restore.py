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


def _interrupt_restore(db, backup_id, interruption="procesdood", exitcode=86):
    settings = get_settings()
    result = subprocess.run(
        [sys.executable, "-c", RESTORE_PROCESS, db.url, str(settings.data_dir), str(settings.work_dir),
         str(backup_id), interruption], capture_output=True, timeout=90, check=False,
    )
    assert result.returncode == exitcode
    db.pool.check()


@pytest.mark.parametrize("interruption,exitcode", [("procesdood", 86), ("signaal", 87)])
def test_interrupted_restore_keeps_current_permissions(admin, client, db, interruption, exitcode):
    revoked = create(admin)
    changed = create(admin, name="Gewijzigde rechten")
    backup = backups.create_backup(actor="test")
    assert revoke(admin, revoked["item"]["id"]).status_code == 200
    db.execute("UPDATE machine_keys SET scopes=%s WHERE id=%s",
               (jsonb(["changes:approve"]), changed["item"]["id"]))
    before = security_snapshot(db)
    _interrupt_restore(db, backup["id"], interruption, exitcode)
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


@pytest.mark.parametrize(
    "schema", [607, 608, 609, 610, 611],
    ids=["voor-sleutelbeheer", "voor-event-id", "voor-change-hash", "voor-opslagmeting", "gewone-back-up"],
)
def test_restore_keep_tables_stay_out_of_dumps(admin, db, schema):
    expected = {607: [611, 610, 609, 608], 608: [611, 610, 609], 609: [611, 610], 610: [611], 611: []}[schema]
    try:
        assert migrate_down(db, schema) == expected
        original = backups.create_backup(actor="test")
    finally:
        assert migrate_up(db) == list(reversed(expected))
    create(admin)

    _interrupt_restore(db, original["id"])
    keep_tables = ("mso_restore_machine_keep", "mso_restore_audit_keep",
                   "mso_restore_change_request_keep", "mso_restore_change_approval_keep")
    if schema < 609:
        keep_tables += ("mso_restore_event_keep",)
    assert all(db.scalar("SELECT to_regclass(%s)", (table,)) for table in keep_tables)
    if schema >= 608:
        dump = backups.create_backup(kind="auto", actor="test")
    init_runtime(db)
    if schema == 607:
        [dump] = db.fetchall("SELECT file FROM backups WHERE kind='pre-upgrade' AND schema_version=607")

    path = get_settings().backup_dir / dump["file"]
    listing = subprocess.run([backups._bin("pg_restore"), "--list", str(path)],
                             capture_output=True, text=True, timeout=120, check=True).stdout.split()
    assert "schema_migrations" in listing
    for table in keep_tables:
        assert table not in listing
        assert db.scalar("SELECT to_regclass(%s)", (table,)) is None


def test_open_restore_blocks_key_management(admin, client, db):
    active = create(admin)
    revoked = create(admin, name="Al ingetrokken")
    cursor = admin.get("/connector/v1/events").json()["next_cursor"]
    backup = backups.create_backup(actor="test")
    assert revoke(admin, revoked["item"]["id"]).status_code == 200

    _interrupt_restore(db, backup["id"])
    login = admin.post("/api/v1/auth/login", json={"username": "beheerder", "password": USER_FIXTURE_PASSWORD})
    assert login.status_code == 200
    admin.csrf = login.json()["csrf_token"]
    pending = admin.get("/connector/v1/events", params={"cursor": cursor})
    assert pending.status_code == 503 and pending.json()["code"] == "dependency_unavailable"
    listed = admin.get("/api/v1/machine-keys")
    created = admin.post("/api/v1/machine-keys", json={"name": "Nieuwe koppeling", "scopes": ["read"]})
    denied = revoke(admin, active["item"]["id"])
    init_runtime(db)
    expired = admin.get("/connector/v1/events", params={"cursor": cursor})
    assert expired.status_code == 409 and expired.json()["code"] == "cursor_expired"
    assert expired.json()["details"] == {"restart": True}

    for response in (listed, created, denied):
        assert response.status_code == 409
        assert response.json()["code"] == "machine_key_restore_pending"
    assert client.get("/api/v1/overview", headers={"X-Api-Key": revoked["key"]}).status_code == 401
    assert client.get("/api/v1/overview", headers={"X-Api-Key": active["key"]}).status_code == 200

    assert revoke(admin, active["item"]["id"]).status_code == 200
    init_runtime(db)
    assert client.get("/api/v1/overview", headers={"X-Api-Key": active["key"]}).status_code == 401


def test_migration_failure_retains_keys_from_current_schema(admin, client, db, monkeypatch):
    try:
        assert migrate_down(db, 607) == [611, 610, 609, 608]
        backup = backups.create_backup(actor="test")
    finally:
        assert migrate_up(db) == [608, 609, 610, 611]
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


def test_restore_audit_counts_discarded_and_retained_keys(admin, client, db):
    expired = create(admin, name="Alleen in de back-up")
    backup = backups.create_backup(actor="test")
    db.execute("DELETE FROM machine_keys WHERE id=%s", (expired["item"]["id"],))
    backups.restore_backup(backup["id"], "test")
    assert db.scalar("SELECT count(*) FROM machine_keys") == 0
    assert client.get("/api/v1/overview", headers={"X-Api-Key": expired["key"]}).status_code == 401
    detail = db.scalar("SELECT detail FROM audit_log WHERE action='backup.restored'")
    assert detail["machine_keys_discarded"] == 1
    assert detail["machine_keys_kept"] == 0
