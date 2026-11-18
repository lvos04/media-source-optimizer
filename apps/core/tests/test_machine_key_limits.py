"""Menselijke aanmelding, machine-pogingen en de algemene API-grens werken onafhankelijk."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from mso import machine_keys, security, settings_registry
from mso.security import limiter
from tests.conftest import USER_FIXTURE_PASSWORD
from tests.test_machine_keys import create


def test_machine_failures_do_not_block_human_or_valid_machine_key(admin, app, db):
    made = create(admin)
    key = made["key"]
    public_id, secret = machine_keys.KEY_RE.fullmatch(key).groups()
    wrong = key[:-1] + ("a" if key[-1] != "a" else "b")
    rejected_secret = machine_keys.KEY_RE.fullmatch(wrong).group(2)
    source = "gedeelde-testbron"
    caller = TestClient(app, client=(source, 1))
    settings_registry.put({"security.login_rate_per_min": 10}, "test")
    limiter.reset()

    def attempt():
        return caller.get("/api/v1/overview", headers={"X-Api-Key": wrong})

    assert [attempt().status_code for _ in range(10)] == [401] * 10
    limited = attempt()
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    counters = db.fetchall("SELECT * FROM machine_auth_limits ORDER BY bucket")
    assert [attempt().status_code for _ in range(12)] == [429] * 12
    assert counters == db.fetchall("SELECT * FROM machine_auth_limits ORDER BY bucket")
    audits = db.fetchall("SELECT action,target,detail FROM audit_log WHERE action IN "
                         "('machine_keys.auth_failed','machine_keys.rate_limited') ORDER BY id")
    assert [row["action"] for row in audits] == ["machine_keys.auth_failed"] * 10 + ["machine_keys.rate_limited"]
    assert all(row["detail"]["remote"] == source and row["target"].replace("-", "") == public_id for row in audits)
    assert all(secret not in str(row) and rejected_secret not in str(row) for row in audits)

    allowed = caller.get("/api/v1/overview", headers={"X-Api-Key": key})
    assert allowed.status_code == 200
    login = caller.post("/api/v1/auth/login", json={"username": "beheerder", "password": USER_FIXTURE_PASSWORD})
    assert login.status_code == 200


def test_general_machine_rate_limit_precedes_authentication(admin, app, db, monkeypatch):
    made = create(admin)
    source = "bron-api-bucket"
    caller = TestClient(app, client=(source, 1))
    limit = 60
    settings_registry.put({"security.api_rate_per_min": limit}, "test")
    monkeypatch.setattr(security, "time", SimpleNamespace(monotonic=lambda: 100.0))
    for _ in range(limit):
        assert limiter.hit("api", source, limit, 60)[0]

    def unexpected(*args):
        raise AssertionError("Databasewerk bereikt vóór de algemene aanvraaggrens")

    with monkeypatch.context() as patch:
        patch.setattr(machine_keys, "authenticate", unexpected)
        patch.setattr(db, "tx", unexpected)
        rejected = caller.get("/api/v1/overview", headers={"X-Api-Key": made["key"]})
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "61"
    assert rejected.json()["detail"] == "Te veel verzoeken; wacht 61 s"
    assert not db.scalar("SELECT count(*) FROM machine_auth_limits")

    existing = caller.get("/api/v1/overview", headers={"X-Correlation-Id": "proef-aanvraaggrens"})
    assert existing.status_code == 429
    assert "Retry-After" not in existing.headers
    assert existing.content == (
        b'{"detail":"Te veel verzoeken; wacht 61 s","code":"rate_limited","correlation_id":"proef-aanvraaggrens"}'
    )


def test_management_general_limit_returns_retry_after(admin, db):
    limit = 60
    settings_registry.put({"security.api_rate_per_min": limit}, "test")
    user_id = db.scalar("SELECT id FROM users WHERE username='beheerder'")
    for _ in range(limit):
        assert limiter.hit("api.user", str(user_id), limit, 60)[0]
    listed = admin.get("/api/v1/machine-keys")
    assert listed.status_code == 429 and int(listed.headers["Retry-After"]) >= 1
    existing = admin.get("/api/v1/settings")
    assert existing.status_code == 429 and "Retry-After" not in existing.headers


def test_general_machine_rate_limit_follows_setting_and_restore(admin, app):
    from mso import backups

    made = create(admin)
    key = {"X-Api-Key": made["key"]}
    source = "bron-api-back-up"
    caller = TestClient(app, client=(source, 1))
    settings_registry.put({"security.api_rate_per_min": 60}, "test")
    backup = backups.create_backup(actor="test")
    settings_registry.put({"security.api_rate_per_min": 120}, "test")
    for _ in range(60):
        assert limiter.hit("api", source, 120, 60)[0]
    assert caller.get("/api/v1/overview", headers=key).status_code == 200
    backups.restore_backup(backup["id"], "test")
    assert caller.get("/api/v1/overview", headers=key).status_code == 429
