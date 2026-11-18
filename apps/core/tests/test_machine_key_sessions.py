"""Bestaande sessie-API vergelijken met vastgelegde antwoorden van de basis-app."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from mso import settings_registry
from mso.security import limiter
from tests.conftest import USER_FIXTURE_PASSWORD, AuthedClient

FIXTURES = Path(__file__).with_name("fixtures")


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


def test_existing_http_matrix_against_base(admin, operator, viewer, client, app):
    """Meet de status en het foutformaat van de 210 oorspronkelijke operaties per identiteit."""
    expected_operations = json.loads((FIXTURES / "native_operations_before_machine_keys.json").read_text())
    operations = [tuple(pair) for pair in expected_operations["operations"]]
    assert len(operations) == 210
    assert set(operations) <= {(method, path) for path, methods in app.openapi()["paths"].items()
                               for method in methods}
    # Uitloggen sluit iedere sessie af en komt daarom als laatste.
    operations.sort(key=lambda pair: pair == ("post", "/api/v1/auth/logout"))
    outcomes = {}
    for name, caller in [("anoniem", client), ("kijker", viewer), ("operator", operator), ("beheerder", admin)]:
        limiter.reset()
        for method, path in operations:
            url = re.sub(r"\{[^}]+\}", "99999999", path)
            if method == "get":
                response = caller.get(url)
            else:
                response = caller.request(method, url, json={})
            outcomes[f"{name} {method} {path}"] = _signature(response)
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
