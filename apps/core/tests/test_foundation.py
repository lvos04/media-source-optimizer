"""Phase B: auth, RBAC, CSRF, settings, mode, jobs (restart-safe), change sets (apply/rollback/drift),
vault, redaction, SSRF, migrations up/down, backups (incl. corrupt restore)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mso import changes, jobs, state
from mso.changes import Applier


# ------------------------------------------------------------------ setup + auth
def test_first_run_setup_creates_admin_once(client: TestClient) -> None:
    st = client.get("/api/v1/setup/status").json()
    assert st["setup_required"] is True and st["admin_exists"] is False
    r = client.post("/api/v1/setup/admin", json={"username": "admin", "password": "Sterk-Wachtwoord1"})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    assert client.get("/api/v1/auth/me").json()["user"]["role"] == "admin"
    # second admin via setup is refused
    r2 = TestClient(client.app).post(
        "/api/v1/setup/admin", json={"username": "tweede", "password": "Sterk-Wachtwoord1"}
    )
    assert r2.status_code == 409
    r3 = client.post("/api/v1/setup/complete", headers={"X-CSRF-Token": csrf})
    assert r3.status_code == 200 and r3.json()["setup_required"] is False


def test_weak_password_rejected(client: TestClient) -> None:
    r = client.post("/api/v1/setup/admin", json={"username": "admin", "password": "kort"})
    assert r.status_code == 422


def test_login_logout_and_cookie_flags(app: Any, admin: Any) -> None:
    assert admin.get("/api/v1/auth/me").status_code == 200
    r = admin.post("/api/v1/auth/logout")
    assert r.status_code == 200
    assert admin.get("/api/v1/auth/me").status_code == 401


def test_login_cookie_is_httponly_strict(app: Any, admin: Any) -> None:
    from mso.auth import hash_password
    from mso.db import get_db

    get_db().execute(
        "INSERT INTO users(username,password_hash,role) VALUES ('cookie',%s,'viewer')",
        (hash_password("Wachtwoord123"),),
    )
    r = TestClient(app).post("/api/v1/auth/login", json={"username": "cookie", "password": "Wachtwoord123"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_wrong_password_and_rate_limit(app: Any, admin: Any) -> None:
    c = TestClient(app)
    codes = [
        c.post("/api/v1/auth/login", json={"username": "beheerder", "password": "fout-fout-fout"}).status_code
        for _ in range(12)
    ]
    assert codes[0] == 401
    assert 429 in codes


def test_csrf_required_for_mutations(app: Any, admin: Any) -> None:
    r = admin.post("/api/v1/system/pause", headers={"X-CSRF-Token": "verkeerd"})
    assert r.status_code == 403
    assert admin.post("/api/v1/system/pause").status_code == 200


def test_role_enforcement(viewer: Any, operator: Any, admin: Any) -> None:
    assert viewer.get("/api/v1/settings").status_code == 200
    assert viewer.put("/api/v1/settings", json={"values": {"backup.keep": 3}}).status_code == 403
    assert operator.put("/api/v1/settings", json={"values": {"backup.keep": 3}}).status_code == 403
    assert admin.put("/api/v1/settings", json={"values": {"backup.keep": 3}}).status_code == 200
    assert viewer.post("/api/v1/system/pause").status_code == 403
    assert operator.post("/api/v1/system/pause").status_code == 200
    assert operator.put("/api/v1/system/mode", json={"mode": "manage"}).status_code == 403
    assert viewer.get("/api/v1/users").status_code == 403


def test_unauthenticated_is_401(client: TestClient) -> None:
    for path in ("/api/v1/settings", "/api/v1/audit", "/api/v1/jobs", "/api/v1/connectors", "/api/v1/changes"):
        r = client.get(path)
        assert r.status_code == 401
        assert set(r.json()) == {"detail", "code", "correlation_id"}


def test_last_admin_cannot_be_demoted(admin: Any) -> None:
    uid = admin.get("/api/v1/auth/me").json()["user"]["id"]
    assert admin.patch(f"/api/v1/users/{uid}", json={"role": "viewer"}).status_code == 409


# ------------------------------------------------------------------ settings + integrity locks
def test_locked_integrity_settings_cannot_change(admin: Any) -> None:
    defs = {d["key"]: d for d in admin.get("/api/v1/settings").json()["definitions"]}
    assert defs["integrity.never_delete_files"]["locked"] is True
    r = admin.put("/api/v1/settings", json={"values": {"integrity.never_delete_files": False}})
    assert r.status_code == 409


def test_setting_validation(admin: Any) -> None:
    assert admin.put("/api/v1/settings", json={"values": {"backup.keep": 0}}).status_code == 422
    assert admin.put("/api/v1/settings", json={"values": {"ui.theme": "paars"}}).status_code == 422
    assert admin.put("/api/v1/settings", json={"values": {"bestaat.niet": 1}}).status_code == 422


# ------------------------------------------------------------------ jobs
def test_job_idempotency_and_restart_recovery(app: Any, db: Any) -> None:
    calls: list[int] = []

    @jobs.handler("test.count")
    def _count(ctx: jobs.JobContext) -> dict:
        calls.append(ctx.id)
        return {"n": len(calls)}

    a = jobs.enqueue("test.count", {"x": 1}, idempotency_key="k1")
    b = jobs.enqueue("test.count", {"x": 1}, idempotency_key="k1")
    assert a["id"] == b["id"]
    # simulate a crash: claimed, then the worker dies without finishing
    job = jobs.claim("w-crashed", kinds=["test.count"])
    assert job and job["status"] == "running"
    db.execute("UPDATE jobs SET heartbeat_at = now() - interval '1 hour' WHERE id=%s", (job["id"],))
    assert jobs.recover_stale(db) == 1
    w = jobs.Worker("w-new", db, 0.01)
    assert w.drain() >= 1
    row = db.fetchone("SELECT * FROM jobs WHERE id=%s", (a["id"],))
    assert row["status"] == "done" and calls == [a["id"]]


def test_job_retry_backoff_then_failed(app: Any, db: Any) -> None:
    @jobs.handler("test.boom")
    def _boom(ctx: jobs.JobContext) -> None:
        raise RuntimeError("apikey=GEHEIM123 kapot")

    j = jobs.enqueue("test.boom", max_attempts=2)
    w = jobs.Worker("w", db, 0.01)
    w.drain()
    row = db.fetchone("SELECT * FROM jobs WHERE id=%s", (j["id"],))
    assert row["status"] == "queued" and "GEHEIM123" not in row["error"]
    db.execute("UPDATE jobs SET run_after=now() WHERE id=%s", (j["id"],))
    w.drain()
    assert db.scalar("SELECT status FROM jobs WHERE id=%s", (j["id"],)) == "failed"


def test_scheduled_jobs_are_idempotent_per_slot(app: Any, db: Any) -> None:
    jobs.SCHEDULES.append(jobs.Schedule("test.sched", 3600))
    try:
        jobs.enqueue_due_schedules(db)
        jobs.enqueue_due_schedules(db)
        assert db.scalar("SELECT count(*) FROM jobs WHERE kind='test.sched'") == 1
    finally:
        jobs.SCHEDULES.pop()


# ------------------------------------------------------------------ change sets
class FakeSystem:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {"speed": 100, "conns": 50}
        self.fail_on: str | None = None

    def applier(self) -> Applier:
        def write(path: str, value: Any) -> None:
            if path == self.fail_on:
                raise RuntimeError("schrijven mislukt")
            self.values[path] = value

        def validate(path: str, value: Any) -> None:
            if path == "conns" and value > 500:
                raise ValueError("te veel verbindingen")

        return Applier(read=lambda p: self.values.get(p), write=write, validate=validate)


@pytest.fixture()
def fake() -> Any:
    sysm = FakeSystem()
    changes.register_applier("fake", sysm.applier)
    yield sysm
    changes.APPLIERS.pop("fake", None)


def test_change_requires_manage_mode(admin: Any, fake: FakeSystem) -> None:
    ch = changes.create("fake", "Snelheid", [{"path": "speed", "before": 100, "after": 80}], risk="automatisch_veilig")
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 409 and "alleen-lezen" in r.json()["detail"]
    assert fake.values["speed"] == 100


def test_change_apply_and_rollback(admin: Any, fake: FakeSystem, manage_mode: None) -> None:
    ch = changes.create("fake", "Snelheid", [{"path": "speed", "before": 100, "after": 80}], risk="automatisch_veilig")
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert fake.values["speed"] == 80
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "test"})
    assert r.status_code == 200 and r.json()["status"] == "rolled_back"
    assert fake.values["speed"] == 100


def test_change_drift_detection(admin: Any, fake: FakeSystem, manage_mode: None) -> None:
    ch = changes.create("fake", "Snelheid", [{"path": "speed", "before": 100, "after": 80}], risk="automatisch_veilig")
    fake.values["speed"] = 120  # someone else changed it
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 409 and "Afwijking" in r.json()["detail"]
    assert fake.values["speed"] == 120


def test_change_partial_failure_auto_rolls_back(admin: Any, fake: FakeSystem, manage_mode: None) -> None:
    fake.fail_on = "conns"
    ch = changes.create(
        "fake",
        "Twee",
        [{"path": "speed", "before": 100, "after": 90}, {"path": "conns", "before": 50, "after": 60}],
        risk="automatisch_veilig",
    )
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 502
    assert fake.values == {"speed": 100, "conns": 50}
    assert changes.get(ch["id"])["status"] == "failed"
    actions = [i["action"] for i in admin.get("/api/v1/audit?action=change.").json()["items"]]
    assert "change.auto_restored" in actions


def test_permission_required_changes_need_admin_approval(
    operator: Any, admin: Any, fake: FakeSystem, manage_mode: None
) -> None:
    ch = changes.create("fake", "Risico", [{"path": "conns", "before": 50, "after": 200}], risk="toestemming_nodig")
    assert operator.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 403
    assert operator.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 403
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    assert operator.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 200
    assert fake.values["conns"] == 200


def test_policy_validation_blocks_change(admin: Any, fake: FakeSystem) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        changes.create("fake", "Te veel", [{"path": "conns", "before": 50, "after": 900}], risk="advies")


def test_automatic_apply_only_for_safe_and_not_paused(app: Any, fake: FakeSystem, manage_mode: None) -> None:
    from fastapi import HTTPException

    advice = changes.create("fake", "Advies", [{"path": "speed", "after": 70}], risk="advies")
    with pytest.raises(HTTPException):
        changes.apply(advice["id"], "auto", automatic=True)
    safe = changes.create("fake", "Veilig", [{"path": "speed", "after": 70}], risk="automatisch_veilig")
    state.set_state("paused", True)
    with pytest.raises(HTTPException):
        changes.apply(safe["id"], "auto", automatic=True)
    state.set_state("paused", False)
    assert changes.apply(safe["id"], "auto", automatic=True)["status"] == "applied"


def test_change_dedupe_key(app: Any, fake: FakeSystem) -> None:
    a = changes.create("fake", "A", [{"path": "speed", "after": 1}], risk="advies", dedupe_key="x")
    b = changes.create("fake", "A", [{"path": "speed", "after": 1}], risk="advies", dedupe_key="x")
    assert a["id"] == b["id"]


# ------------------------------------------------------------------ vault, redaction, SSRF
@pytest.fixture()
def testconn(app: Any) -> Any:
    from mso.connectors import base

    spec = base.ConnectorSpec(
        id="testconn",
        kind="test",
        label="Testdienst",
        secret_fields=[base.SecretField("api_key", "API-sleutel")],
        test=lambda cfg: base.TestResult(cfg.secrets.get("api_key") == "SUPERGEHEIM", "healthy", "ok", version="1.0"),
    )
    base.register(spec)
    base.ensure_rows()
    yield spec
    base.SPECS.pop("testconn", None)


def test_vault_encrypts_and_never_exposes(app: Any, db: Any, admin: Any, testconn: Any) -> None:
    from mso.vault import get_vault

    r = admin.put(
        "/api/v1/connectors/testconn", json={"url": "http://192.168.1.20:9696", "secrets": {"api_key": "SUPERGEHEIM"}}
    )
    assert r.status_code == 200 and "SUPERGEHEIM" not in r.text
    raw = db.fetchone("SELECT ciphertext FROM secrets WHERE name='connector.testconn.api_key'")
    assert b"SUPERGEHEIM" not in bytes(raw["ciphertext"])
    assert get_vault().get("connector.testconn.api_key") == "SUPERGEHEIM"
    audit_text = admin.get("/api/v1/audit").text
    assert "SUPERGEHEIM" not in audit_text
    body = admin.get("/api/v1/connectors").text
    assert "SUPERGEHEIM" not in body and "ingesteld" in body


def test_redaction_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    from mso.security import RedactingFilter, redact

    assert "abc123" not in redact("GET /api?apikey=abc123&t=search")
    assert "pw" not in redact("http://user:pw@host/x").split("@")[0].split("//")[1].replace("***", "")
    logger = logging.getLogger("redact-test")
    caplog.handler.addFilter(RedactingFilter())
    logger.warning("token=%s", "XYZSECRET")
    assert "XYZSECRET" not in caplog.text


def test_ssrf_guard() -> None:
    from mso.security import UnsafeTarget, check_url

    for bad in (
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data",
        "gopher://x",
        "http://user:pass@192.168.1.5:8080",
        "http://[fe80::1]/",
    ):
        with pytest.raises(UnsafeTarget):
            check_url(bad)
    assert check_url("http://192.168.1.10:9696/") == "http://192.168.1.10:9696"


def test_ssrf_on_connector_save(admin: Any, testconn: Any) -> None:
    r = admin.put("/api/v1/connectors/testconn", json={"url": "http://169.254.169.254/"})
    assert r.status_code == 422


def test_loopback_blocked_unless_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from mso.config import get_settings
    from mso.security import UnsafeTarget, check_url

    monkeypatch.setattr(get_settings(), "allow_loopback_targets", False)
    with pytest.raises(UnsafeTarget):
        check_url("http://127.0.0.1:8080")


# ------------------------------------------------------------------ audit
def test_audit_trail_records_actions(admin: Any) -> None:
    admin.post("/api/v1/system/pause")
    items = admin.get("/api/v1/audit?action=system.").json()["items"]
    assert any(i["action"] == "system.paused" and i["actor"] == "beheerder" for i in items)


# ------------------------------------------------------------------ migrations
def test_migrations_down_and_up(database_url: str) -> None:
    import psycopg

    from mso.db import Database, load_migrations, migrate_down, migrate_up, schema_version

    name = database_url.rsplit("/", 1)[1] + "_mig"
    base = database_url.rsplit("/", 1)[0]
    with psycopg.connect(base + "/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    db = Database(base + "/" + name, max_size=2)
    try:
        latest = max(m.version for m in load_migrations())
        migrate_up(db)
        assert schema_version(db) == latest
        assert migrate_up(db) == []  # idempotent
        migrate_down(db, 0)
        assert schema_version(db) == 0
        assert db.scalar("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='users'") == 0
        migrate_up(db)
        assert schema_version(db) == latest
    finally:
        db.close()
        with psycopg.connect(base + "/postgres", autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# ------------------------------------------------------------------ backups
def test_backup_and_restore_roundtrip(app: Any, db: Any, admin: Any) -> None:
    from mso import backups
    from mso.vault import get_vault

    get_vault().set("connector.sabnzbd.api_key", "BEHOUDEN")
    admin.put("/api/v1/settings", json={"values": {"backup.keep": 5}})
    b = backups.create_backup(actor="test")
    assert b["includes_secrets"] is False
    admin.put("/api/v1/settings", json={"values": {"backup.keep": 9}})
    result = backups.restore_backup(b["id"], "test")
    assert result["restored"] == b["file"]
    assert db.fetchone("SELECT value FROM settings WHERE key='backup.keep'")["value"] == 5
    # secrets were not in the dump but the current vault content survives the restore
    assert get_vault().get("connector.sabnzbd.api_key") == "BEHOUDEN"


def test_restore_refuses_corrupt_backup(app: Any, db: Any) -> None:
    from mso import backups
    from mso.config import get_settings

    b = backups.create_backup(actor="test")
    path = get_settings().backup_dir / b["file"]
    data = bytearray(path.read_bytes())
    data[100:140] = b"\x00" * 40
    path.write_bytes(bytes(data))
    with pytest.raises(backups.BackupError, match="beschadigd"):
        backups.restore_backup(b["id"], "test")
    path.unlink()
    with pytest.raises(backups.BackupError, match="ontbreekt"):
        backups.restore_backup(b["id"], "test")
    assert db.ping()


def test_restore_api_requires_typed_confirmation(app: Any, admin: Any) -> None:
    from mso import backups

    b = backups.create_backup(actor="test")
    assert admin.post(f"/api/v1/backups/{b['id']}/restore", json={"confirm": "fout"}).status_code == 422
    assert admin.post(f"/api/v1/backups/{b['id']}/restore", json={"confirm": b["file"]}).status_code == 200


def test_setup_complete_sets_mode(admin: Any) -> None:
    r = admin.post("/api/v1/setup/complete", json={"mode": "manage"})
    assert r.status_code == 200
    assert admin.get("/api/v1/system/mode").json()["mode"] == "manage"


def test_health_and_info(admin: Any) -> None:
    h = admin.get("/api/v1/system/health").json()
    assert h["components"][0]["name"] == "database" and h["components"][0]["status"] == "healthy"
    info = admin.get("/api/v1/system/info").json()
    assert info["schema_version"] >= 1


def test_healthz_unauthenticated(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"


def test_backup_env_supports_unix_socket_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from mso import backups
    from mso.config import get_settings

    monkeypatch.setattr(get_settings(), "database_url", "postgresql://mso@/mso?host=/tmp/mso-pg")
    args, env = backups._pg_env()
    assert args[:2] == ["-h", "/tmp/mso-pg"] and env["PGDATABASE"] == "mso" and "-U" in args
