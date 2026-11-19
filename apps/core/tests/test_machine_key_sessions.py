"""Bestaande sessie-API vergelijken met vastgelegde antwoorden van de basis-app."""

import json
import re
import shutil
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from mso import retention, settings_registry
from mso.config import BuildMetadata, get_settings
from mso.features.newznab_server import keys as newznab_keys
from mso.security import limiter
from tests.conftest import USER_FIXTURE_PASSWORD, AuthedClient

FIXTURES = Path(__file__).with_name("fixtures")
ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
EXPECTED_MATRIX_DIFFERENCES = {
    ("get", "/api/v1/system/info"): {
        # Toegevoegde migraties moeten exact in systeeminfo staan.
        "base_version": 607,
        "added_migrations": [{"version": 608, "name": "machine_keys", "applied_at": "<tijd>"},
                             {"version": 609, "name": "automation_event_id", "applied_at": "<tijd>"},
                             {"version": 610, "name": "connector_idempotency", "applied_at": "<tijd>"}],
        # S-P3-210 voegt imageherkomst toe. Zonder buildargumenten is ook de versie onbekend.
        "base_software_version": "1.6.0",
        "build_fields": ("git_sha", "build_time", "channel"),
    },
}


def _signature(response):
    content_type = response.headers.get("content-type", "").split(";")[0]
    body = response.json() if content_type == "application/json" else None
    return [response.status_code, content_type, body.get("code") if isinstance(body, dict) else None,
            sorted(body) if isinstance(body, dict) and response.is_error else None]


def _assert_basis(name, outcomes):
    path = FIXTURES / f"machine_sessions_basis_{name}.json"
    before = json.loads(path.read_text())
    assert outcomes.keys() == before.keys()
    for label, current in outcomes.items():
        assert current == before[label], label


def _stable(value, key=""):
    if isinstance(value, dict):
        return {name: _stable(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_stable(item, key) for item in value]
    if key == "uptime_s" and type(value) is int:
        return "<looptijd>"
    if isinstance(value, str):
        if key == "correlation_id" and re.fullmatch(r"[0-9a-f]{12}", value):
            return "<trace-id>"
        if ISO_TIME.fullmatch(value):
            return "<tijd>"
        if UUID.fullmatch(value):
            return "<uuid>"
        settings = get_settings()
        return value.replace(str(settings.data_dir), "<appdata>").replace(str(settings.work_dir), "<work>")
    return value


def _fingerprint(value):
    # Bewaar de volledige vergelijking zonder sleutels of lange antwoordcatalogi in de fixture.
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _matrix_body(response, operation, caller, installed_schema):
    body = _stable(response.json() if response.headers.get("content-type", "").split(";")[0] == "application/json"
                   else response.text)
    if not response.is_success:
        return body
    if operation == ("get", "/api/v1/auth/me"):
        assert body.get("csrf_token") == caller.csrf, "Sessie geeft niet haar eigen CSRF-token terug"
        body["csrf_token"] = "<sessie-csrf>"
    if operation == ("get", "/api/v1/settings"):
        # De registratievolgorde hangt af van welke modules pytest al heeft geïmporteerd.
        body["definitions"].sort(key=lambda definition: definition["key"])
    if operation == ("get", "/api/v1/connectors"):
        body.sort(key=lambda connector: connector["id"])
    difference = EXPECTED_MATRIX_DIFFERENCES.get(operation)
    if difference:
        assert body["version"] == "onbekend"
        body["version"] = difference["base_software_version"]
        for field in difference["build_fields"]:
            assert body.pop(field) == "onbekend"
        if installed_schema != difference["base_version"]:
            migrations = [m for m in difference["added_migrations"] if m["version"] <= installed_schema]
            assert body["schema_version"] == installed_schema == migrations[-1]["version"]
            assert body["migrations"][-len(migrations):] == migrations
            body["schema_version"] = difference["base_version"]
            del body["migrations"][-len(migrations):]
    return body


def test_existing_http_matrix_against_base(admin, operator, viewer, client, app, db, monkeypatch):
    """Meet het HTTP-antwoord en de auditdelta per oorspronkelijke operatie en identiteit."""
    from mso.features.overview import autopilot, goals
    from mso.features.torrent import api as torrent_api
    from mso.features.usenet import categories

    expected_operations = json.loads((FIXTURES / "native_operations_before_machine_keys.json").read_text())
    operations = [tuple(pair) for pair in expected_operations["operations"]]
    assert len(operations) == 210
    assert set(operations) <= {(method, path) for path, methods in app.openapi()["paths"].items()
                               for method in methods}
    # Uitloggen sluit iedere sessie af en komt daarom als laatste.
    operations.sort(key=lambda pair: pair == ("post", "/api/v1/auth/logout"))
    # De basis-app leverde dagreeksen op 18 november 2026, op de aanmaakdag van de testgebruikers.
    # Gebruik diezelfde invoer zodat ook elke afzonderlijke kalenderdag in de inhoudshash wordt getoetst.
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    db.execute("UPDATE users SET created_at=%s", (now,))

    class MatrixDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    for module in (autopilot, goals, torrent_api, categories):
        monkeypatch.setattr(module, "datetime", MatrixDateTime)
    monkeypatch.setattr(time, "time", now.timestamp)
    monkeypatch.setattr(get_settings(), "build", BuildMetadata("onbekend", "onbekend", "onbekend", "onbekend"))

    # Geef beide apps dezelfde omgevingsmetingen en een vaste synthetische Newznab-sleutel.
    gib = 1024**3
    disk_usage = type(shutil.disk_usage("."))
    monkeypatch.setattr(shutil, "disk_usage", lambda _: disk_usage(100 * gib, 20 * gib, 80 * gib))
    monkeypatch.setattr(retention, "database_usage", lambda _: {
        "size_bytes": 12 * 1024**2, "tables": [{"name": "jobs", "bytes": 81920, "rows": 0}],
    })
    scalar = db.scalar

    def matrix_scalar(query, params=None):
        if query == "SELECT pg_database_size(current_database())":
            return 12 * 1024**2
        if query == "SELECT (now() AT TIME ZONE %s)::date":
            return scalar("SELECT (%s::timestamptz AT TIME ZONE %s)::date", (now, *params))
        return scalar(query, params)

    monkeypatch.setattr(db, "scalar", matrix_scalar)
    monkeypatch.setattr(newznab_keys, "_new_key", lambda: "0" * 32)
    installed_schema = db.scalar("SELECT max(version) FROM schema_migrations")
    last_id = db.scalar("SELECT coalesce(max(id),0) FROM audit_log")
    outcomes = {}
    for name, caller in [("anoniem", client), ("kijker", viewer), ("operator", operator), ("beheerder", admin)]:
        limiter.reset()
        for method, path in operations:
            url = re.sub(r"\{[^}]+\}", "99999999", path)
            if method == "get":
                response = caller.get(url)
            else:
                response = caller.request(method, url, json={})
            body = _matrix_body(response, (method, path), caller, installed_schema)
            entries = db.fetchall(
                "SELECT id,to_jsonb(a) - 'id' AS entry FROM audit_log a WHERE id>%s ORDER BY id", (last_id,),
            )
            if entries:
                last_id = entries[-1]["id"]
            outcomes[f"{name} {method} {path}"] = {
                "http": _signature(response),
                "body": _fingerprint(body),
                "audit": _fingerprint(_stable([row["entry"] for row in entries])),
            }
    _assert_basis("matrix", outcomes)


def test_existing_successful_writes_against_base(admin, operator, viewer, client, app, db):
    """Geldige sessies, realistische bodies, toestand en de auditdelta per handeling."""
    from mso.auth import verify_password

    outcomes = {}
    user_state_query = "SELECT username,role,disabled FROM users ORDER BY username"
    last_audit = db.scalar("SELECT max(id) FROM audit_log")

    def record(label, response):
        nonlocal last_audit
        entries = db.fetchall("SELECT id,actor,action,target,summary,detail FROM audit_log WHERE id>%s ORDER BY id",
                              (last_audit,))
        if entries:
            last_audit = entries[-1]["id"]
        outcomes[label] = {
            "response": _signature(response),
            "audit": [{key: value for key, value in row.items() if key != "id"} for row in entries],
            "sessions": db.scalar("SELECT count(*) FROM sessions"),
            "users": db.fetchall(user_state_query),
            "state": db.fetchall("SELECT key,value FROM system_state WHERE key IN ('mode','paused') ORDER BY key"),
            "login_limit": db.scalar("SELECT value FROM settings WHERE key='security.login_rate_per_min'"),
        }

    limiter.reset()
    credentials = {"username": "kijker", "password": USER_FIXTURE_PASSWORD}
    logged_in = AuthedClient(app, client=("bron-sessieproef", 1))
    answer = logged_in.post("/api/v1/auth/login", json=credentials)
    record("geldige aanmelding", answer)
    assert answer.status_code == 200
    logged_in.csrf = answer.json()["csrf_token"]
    record("sessie met eigen account", logged_in.get("/api/v1/auth/me"))

    new_password = USER_FIXTURE_PASSWORD + "gewijzigd"
    answer = logged_in.post("/api/v1/auth/password", json={
        "current_password": USER_FIXTURE_PASSWORD, "new_password": new_password,
    })
    record("kijker wijzigt eigen wachtwoord", answer)
    assert answer.status_code == 200
    stored = db.scalar("SELECT password_hash FROM users WHERE username='kijker'")
    assert verify_password(new_password, stored) and not verify_password(USER_FIXTURE_PASSWORD, stored)
    record("kijker pauzeert met geldige csrf", viewer.post("/api/v1/system/pause"))
    record("operator pauzeert", operator.post("/api/v1/system/pause"))
    record("operator hervat", operator.post("/api/v1/system/resume"))
    record("beheerder schakelt modus", admin.put("/api/v1/system/mode", json={"mode": "manage"}))
    record("beheerder wijzigt instelling", admin.put("/api/v1/settings", json={
        "values": {"security.login_rate_per_min": 12},
    }))
    record("beheerder maakt gebruiker", admin.post("/api/v1/users", json={
        "username": "proefgebruiker", "password": USER_FIXTURE_PASSWORD, "role": "viewer",
    }))
    uid = db.scalar("SELECT id FROM users WHERE username='proefgebruiker'")
    record("beheerder wijzigt gebruiker", admin.patch(f"/api/v1/users/{uid}", json={"role": "operator"}))
    record("kijker meldt af", logged_in.post("/api/v1/auth/logout"))
    record("afgemelde sessie", logged_in.get("/api/v1/auth/me"))
    record("info met sessie", viewer.get("/api/v1/system/info"))
    record("info zonder sessie", client.get("/api/v1/system/info"))
    _assert_basis("schrijvers", outcomes)


def test_existing_public_routes_and_limits_against_base(app, client, db):
    outcomes = {}
    for path in ("/healthz", "/api/docs", "/api/openapi.json", "/api/v1/setup/status", "/onbekend"):
        response = client.get(path)
        outcomes[path] = _signature(response)
        if path == "/healthz":
            outcomes["healthz.status"] = response.json().get("status")

    limiter.reset()
    attempts = TestClient(app, client=("bron-grensproef", 1))
    for attempt in range(11):
        response = attempts.post("/api/v1/auth/login", json={"username": "afwezig", "password": "onjuist"})
        outcomes[f"menselijke poging {attempt + 1}"] = [_signature(response), response.headers.get("Retry-After")]
    outcomes["opgeslagen pogingen"] = db.fetchall("SELECT username,remote,ok FROM login_attempts ORDER BY id")
    outcomes["audit"] = db.fetchall("SELECT action,actor,detail FROM audit_log ORDER BY id")
    assert response.status_code == 429

    settings_registry.put({"security.api_rate_per_min": 60}, "test")
    admitted = [attempts.get("/api/v1/setup/status").status_code for _ in range(60)]
    limited = attempts.get("/api/v1/setup/status")
    outcomes["algemene API-grens"] = [admitted, _signature(limited), limited.headers.get("Retry-After")]
    _assert_basis("publiek", outcomes)
