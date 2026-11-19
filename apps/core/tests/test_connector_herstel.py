"""Regressies voor aanvraaggrenzen, bronmetingen en native gegevensherstel."""

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from conftest import USER_FIXTURE_PASSWORD, make_user
from cryptography.exceptions import InvalidTag
from test_connector_api import PREFIX, _hartslag, _poll, fout

from mso import backups
from mso.app import init_runtime
from mso.audit import audit
from mso.connector_api.errors import MAX_BYTES
from mso.connectors import base as connectors
from mso.db import migrate_down, migrate_up, schema_version
from mso.security import limiter
from mso.vault import get_vault


@pytest.mark.parametrize("stream", [False, True])
def test_readyz_lichaamsgrens(client, db, stream):
    _hartslag(db, datetime.now(UTC))

    def request(size):
        body = b"x" * size
        content = iter((body[:MAX_BYTES], body[MAX_BYTES:])) if stream else body
        return client.request("GET", "/readyz", content=content)

    assert request(MAX_BYTES).status_code == 200
    assert fout(request(MAX_BYTES + 1), 413, "payload_too_large") == {"max_bytes": MAX_BYTES}


@pytest.mark.parametrize("path,bucket", [(PREFIX + "/discover", "connector"), ("/api/v1/overview", "api")])
def test_onbevestigde_sessie_telt_op_bron(client, viewer, app, db, monkeypatch, path, bucket):
    other_session = make_user(app, "kijker", "viewer")
    user_id = db.scalar("SELECT id FROM users WHERE username='kijker'")
    calls = []

    def observe(group, key, *args):
        calls.append((group, key))
        return True, 0

    monkeypatch.setattr(limiter, "hit", observe)
    client.cookies.set("mso_session", "ongeldig")
    assert client.get(path).status_code == 401
    assert calls == [(bucket, "testclient")]
    for session in (viewer, other_session):
        calls.clear()
        assert session.get(path).status_code == 200
        assert calls == [(bucket, "testclient"), (bucket + ".user", str(user_id))]


def test_kluis_toetst_ook_ouder_record(client, db):
    _hartslag(db, datetime.now(UTC))
    db.execute("UPDATE connectors SET enabled=true,url=%s WHERE id='sabnzbd'", ("http://localhost",))
    vault = get_vault()
    required = connectors.secret_name("sabnzbd", "api_key")
    vault.set(required, USER_FIXTURE_PASSWORD)
    vault.set("test.laatste", USER_FIXTURE_PASSWORD)
    assert client.get("/readyz").status_code == 200
    db.execute("UPDATE secrets SET ciphertext=''::bytea, updated_at=now()-interval '1 day' "
               "WHERE name=%s", (required,))
    with pytest.raises(InvalidTag):
        connectors.load_config("sabnzbd")
    response = client.get("/readyz")
    assert response.status_code == 503 and response.json()["checks"]["vault"] is False
    assert USER_FIXTURE_PASSWORD not in response.text


def test_readyz_correlatie_komt_van_de_dienst(client, db):
    ids = []
    for expected in (503, 200):
        if expected == 200:
            _hartslag(db, datetime.now(UTC))
        response = client.get("/readyz", headers={"X-Correlation-Id": "meegegeven-kenmerk"})
        assert response.status_code == expected
        ids.append(response.headers["X-Correlation-Id"])
    assert all(ids) and len(set(ids)) == 2 and "meegegeven-kenmerk" not in ids


def test_healthz_schermt_verzoekkenmerk_af_bij_databasefout(client, db, monkeypatch):
    headers = {"X-Correlation-Id": USER_FIXTURE_PASSWORD}
    gezond = client.get("/healthz", headers=headers)
    monkeypatch.setattr(db, "ping", lambda: False)
    storing = client.get("/healthz", headers=headers)
    assert (gezond.status_code, storing.status_code) == (200, 503)
    for antwoord in (gezond, storing):
        if USER_FIXTURE_PASSWORD in antwoord.headers["X-Correlation-Id"] or USER_FIXTURE_PASSWORD in antwoord.text:
            pytest.fail(f"De livenessrespons bij HTTP {antwoord.status_code} bevat het verzoekkenmerk.")
    assert storing.json()["correlation_id"] == storing.headers["X-Correlation-Id"]
    assert storing.headers["X-Correlation-Id"] != gezond.headers["X-Correlation-Id"]


@pytest.mark.parametrize("route", ["discover", "health", "items", "events", "evidence"])
def test_leesroute_weigert_onbekend_queryveld(viewer, route):
    if route == "evidence":
        event = audit("test", "controle", "Voorbeeld")
        route = f"evidence/audit:{event}"
    response = viewer.get(f"{PREFIX}/{route}", params={"onbekend": "fixture"})
    details = fout(response, 422, "validation_error")
    assert details["fouten"] and "fixture" not in response.text


def test_toekomstige_hartslag_is_geen_meting(client, viewer, db):
    _hartslag(db, datetime.now(UTC) + timedelta(days=1))
    db.execute("INSERT INTO backups(file) VALUES ('test')")
    ready = client.get("/readyz")
    health = viewer.get(PREFIX + "/health").json()
    worker = next(c for c in health["checks"] if c["name"] == "worker")
    assert ready.status_code == 503 and not ready.json()["checks"]["worker"]
    assert worker["status"] == health["status"] == "unknown"


def test_health_draagt_oudste_bronmeting(viewer, db):
    now = datetime.now(UTC)
    at = now - timedelta(seconds=901)
    _hartslag(db, now)
    db.execute("INSERT INTO backups(file) VALUES ('test')")
    db.execute("UPDATE connectors SET enabled=true,status='healthy',last_checked_at=%s WHERE id='sabnzbd'", (at,))
    health = viewer.get(PREFIX + "/health").json()
    check = next(c for c in health["checks"] if c["name"] == "connector.sabnzbd")
    assert (health["status"], health["reason_code"]) == ("unknown", "meting_verouderd")
    assert datetime.fromisoformat(health["observed_at"]) == datetime.fromisoformat(check["observed_at"]) == at


def test_achterstallige_taak_blijft_zichtbaar(viewer, db):
    db.execute("INSERT INTO jobs(kind,status,run_after) VALUES ('test','queued',now()+interval '1 hour')")
    db.execute("INSERT INTO jobs(kind,status) VALUES ('andere','done')")

    def jobs():
        return next(c for c in viewer.get(PREFIX + "/health").json()["checks"] if c["name"] == "jobs")

    assert jobs()["status"] == "healthy"
    db.execute("UPDATE jobs SET run_after=now()-interval '1 hour' WHERE status='queued'")
    check = jobs()
    assert check["detail"]["wachtrij"] == 1 and check["detail"]["laatste_job_klaar"]
    assert check["status"] == "degraded"


@pytest.mark.parametrize("onderdeel", ["cursor", "bewijs"])
def test_native_herstel_begrenst_eventhistorie(viewer, app, db, onderdeel):
    at = datetime.now(UTC)
    kept = db.scalar("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',%s) RETURNING id", (at,))
    snapshot = backups.create_backup(actor="test")
    page = _poll(viewer).json()
    kept_ref = next(e["payload"]["id"] for e in page["events"] if e["payload"]["source"] == "automation_log")
    gone = db.scalar("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',%s) RETURNING id", (at,))
    event = next(e for e in _poll(viewer, page["next_cursor"]).json()["events"]
                 if e["payload"]["source"] == "automation_log")
    old_ref = event["payload"]["id"]
    assert viewer.get(f"{PREFIX}/evidence/{old_ref}").status_code == 200
    backups.restore_backup(snapshot["id"], "test")
    reader = make_user(app, "kijker", "viewer")
    if onderdeel == "cursor":
        assert fout(_poll(reader, page["next_cursor"]), 409, "cursor_expired") == {"restart": True}
        assert _poll(reader).status_code == 200
        return
    assert reader.get(f"{PREFIX}/evidence/{kept_ref}").status_code == 200
    assert kept < gone
    # Zelfde nummer én dezelfde brontijd, maar een waarneming uit de nieuwe historie.
    db.execute("INSERT INTO automation_log(id,kind,result,ts) VALUES (%s,'run','ok',%s)", (gone, at))
    fout(reader.get(f"{PREFIX}/evidence/{old_ref}"), 404, "not_found")
    fout(reader.get(f"{PREFIX}/evidence/automation:{gone}"), 404, "not_found")
    replacement = [e for e in _poll(reader).json()["events"] if e["payload"]["source"] == "automation_log"][-1]
    assert replacement["event_id"] != event["event_id"]
    assert reader.get(f"{PREFIX}/evidence/{replacement['payload']['id']}").json() == replacement["payload"]


@pytest.mark.parametrize("herstart", [False, True], ids=["direct", "na-herstart"])
def test_eventidentiteit_na_mislukt_herstel_oud_schema(viewer, app, db, monkeypatch, herstart):
    at = datetime.now(UTC)
    try:
        assert migrate_down(db, 608) == [612, 611, 610, 609]
        row_id = db.scalar("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',%s) RETURNING id",
                           (at,))
        snapshot = backups.create_backup(actor="test")
    finally:
        assert migrate_up(db) == [609, 610, 611, 612]
    # De oude dump en de nieuwe gebeurtenis hebben hetzelfde nummer en dezelfde tijd.
    db.execute("DELETE FROM automation_log WHERE id=%s", (row_id,))
    db.execute("INSERT INTO automation_log(id,kind,result,ts) VALUES (%s,'run','ok',%s)", (row_id, at))

    def event(reader):
        return next(e for e in _poll(reader).json()["events"] if e["payload"]["source"] == "automation_log")

    voor = event(viewer)
    bewijs = f"{PREFIX}/evidence/{voor['source_event_id']}"
    assert viewer.get(bewijs).json() == voor["payload"]
    run = subprocess.run

    def weiger_herstel(args, **kwargs):
        if Path(args[0]).name == "pg_restore" and "--single-transaction" in args:
            assert schema_version(db) == 608
            return subprocess.CompletedProcess(args, 1, stderr="gecontroleerde archieffout")
        return run(args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", weiger_herstel)
        if herstart:
            def migratie_onderbroken(_db):
                raise RuntimeError("Gecontroleerde onderbreking van het schemaherstel")

            patch.setattr(backups, "migrate_up", migratie_onderbroken)
        with pytest.raises(backups.BackupError, match="gecontroleerde archieffout"):
            backups.restore_backup(snapshot["id"], "test")
    if herstart:
        try:
            assert schema_version(db) == 608
            fout(_poll(viewer), 503, "dependency_unavailable")
        finally:
            init_runtime(db)
    assert schema_version(db) == 612
    na = event(viewer)
    assert (viewer.get(bewijs).status_code, na["event_id"], na["source_event_id"]) == (
        200, voor["event_id"], voor["source_event_id"])
    assert viewer.get(bewijs).json() == voor["payload"]

    backups.restore_backup(snapshot["id"], "test")
    reader = make_user(app, "kijker", "viewer")
    fout(reader.get(bewijs), 404, "not_found")
    teruggezet = event(reader)
    assert teruggezet["event_id"] != voor["event_id"]
    assert reader.get(f"{PREFIX}/evidence/{teruggezet['source_event_id']}").json() == teruggezet["payload"]


def test_auditprobleem_en_herstel_hebben_hetzelfde_onderwerp(viewer):
    for action in ("backup.pre_upgrade_failed", "backup.restored", USER_FIXTURE_PASSWORD):
        audit("test", action, USER_FIXTURE_PASSWORD, target=USER_FIXTURE_PASSWORD)
    response = _poll(viewer)
    failed, recovered, unknown = response.json()["events"][-3:]
    assert failed["event_type"].split(".")[-1] == "failed"
    assert recovered["event_type"].split(".")[-1] == "restored"
    assert failed["event_type"].rsplit(".", 1)[0] == recovered["event_type"].rsplit(".", 1)[0]
    assert failed["entity"] == recovered["entity"] and failed["entity"]["type"] != "log"
    assert unknown["event_type"] == "mso.audit.recorded"
    for event in (failed, recovered, unknown):
        UUID(event["event_id"])
        proof = viewer.get(f"{PREFIX}/evidence/{event['payload']['id']}")
        assert proof.json() == event["payload"]
        assert proof.json()["event_type"] == event["event_type"] and proof.json()["entity"] == event["entity"]
        assert USER_FIXTURE_PASSWORD not in proof.text
    assert USER_FIXTURE_PASSWORD not in response.text


def test_contractproef_weigert_extra_uitzondering(app, monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace

    from test_machine_keys import test_all_new_mutations_require_idempotency_or_documented_secret_exception as toets

    from mso.machine_keys import IDEMPOTENCY_EXCEPTIONS

    schema = deepcopy(app.openapi())
    path = "/connector/v1/testuitzondering"
    monkeypatch.setitem(IDEMPOTENCY_EXCEPTIONS, ("post", path), "test")
    schema["paths"][path] = {"post": {"x-idempotency-exception": "test"}}
    with pytest.raises(AssertionError):
        toets(SimpleNamespace(openapi=lambda: schema))
