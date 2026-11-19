"""Contractproeven voor de alleen-lezen connector van MSO."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from conftest import USER_FIXTURE_PASSWORD
from fastapi import FastAPI
from test_machine_keys import create, revoke

from mso import retention
from mso.audit import audit
from mso.config import get_settings
from mso.db import jsonb, migrate_down, migrate_up
from mso.vault import get_vault

PREFIX = "/connector/v1"


def fout(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["code"] == code
    assert body["message"]
    assert isinstance(body["details"], dict)
    UUID(body["trace_id"])
    return body["details"]


@pytest.mark.parametrize("path", ["/discover", "/health", "/items", "/events", "/evidence/audit:{id}"])
def test_a_toegang(path, admin, viewer, client):
    event = audit("systeem", "test", "Voorbeeld")
    route = PREFIX + path.format(id=event)
    lees = create(admin)["key"]
    beperkt = create(admin, ["changes:approve"], name="Beperkt")["key"]
    fout(client.get(route), 401, "unauthorized")
    fout(client.get(route, headers={"X-Api-Key": beperkt}), 403, "forbidden")
    fout(viewer.get(route, headers={"X-Api-Key": "ongeldig"}), 401, "unauthorized")
    ingetrokken = create(admin, name="Ingetrokken")
    assert revoke(admin, ingetrokken["item"]["id"]).status_code == 200
    fout(viewer.get(route, headers={"X-Api-Key": ingetrokken["key"]}), 401, "unauthorized")
    assert viewer.get(route).status_code == 200
    assert client.get(route, headers={"X-Api-Key": lees}).status_code == 200
    assert client.get("/api/v1/system/maintenance", headers={"X-Api-Key": lees}).status_code == 403


def _hartslag(db, at):
    db.execute("INSERT INTO system_state(key,value,updated_at) VALUES ('worker_heartbeat','{}',%s)", (at,))


def test_b_versheid_en_slechtste_check(viewer, db):
    now = datetime.now(UTC).replace(microsecond=0)
    oud = now - timedelta(seconds=901)
    _hartslag(db, now)
    db.execute("INSERT INTO backups(file,created_at) VALUES ('test',%s)", (now,))
    db.execute("UPDATE connectors SET enabled=true,status='healthy',last_checked_at=%s WHERE id='sabnzbd'", (oud,))
    assert viewer.get(PREFIX + "/discover").json()["limits"]["max_age_s"]["connector"] == 900

    def health():
        response = viewer.get(PREFIX + "/health")
        assert response.status_code == 200
        body = response.json()
        return body, {c["name"]: c for c in body["checks"]}

    body, checks = health()
    assert body["status"] == "unknown"
    assert body["reason_code"] == "meting_verouderd"
    assert checks["connector.sabnzbd"]["observed_at"] == oud.isoformat().replace("+00:00", "Z")
    assert checks["worker"]["observed_at"] == now.isoformat().replace("+00:00", "Z")
    assert checks["jobs"]["detail"] == {"wachtrij": 0, "bezig": 0, "laatste_job_klaar": None}
    db.execute("UPDATE connectors SET last_checked_at=%s WHERE id='sabnzbd'", (now,))
    assert health()[0]["status"] == "healthy"
    db.execute("UPDATE connectors SET status='auth_failed' WHERE id='sabnzbd'")
    db.execute("UPDATE backups SET created_at=now()-interval '3 days'")
    body, checks = health()
    assert body["status"] == "degraded"
    assert checks["backup"]["status"] == "degraded"
    assert checks["connector.sabnzbd"]["status"] == "degraded"
    db.execute("UPDATE connectors SET status='offline' WHERE id='sabnzbd'")
    assert health()[0]["status"] == "down"
    db.execute("UPDATE connectors SET last_checked_at=NULL WHERE id='sabnzbd'")
    assert health()[1]["connector.sabnzbd"]["observed_at"] is None
    assert health()[1]["connector.sabnzbd"]["status"] == "unknown"


def test_items_pagineert_stabiel_en_bewaakt_type(viewer, db):
    db.execute("INSERT INTO change_sets(target,title,risk) VALUES ('test','Test','advies')")
    url = PREFIX + "/items"
    all_items = viewer.get(url).json()["items"]
    cursor = None
    paged = []
    for _ in all_items:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        result = viewer.get(url, params=params).json()
        paged.extend(result["items"])
        cursor = result["next_cursor"]
    assert len(paged) > 1 and cursor is None
    assert [item["id"] for item in paged] == [item["id"] for item in all_items]
    first = viewer.get(url, params={"limit": 1}).json()
    fout(viewer.get(url, params={"cursor": first["next_cursor"], "type": "change_set"}), 422, "validation_error")
    fout(viewer.get(url, params={"limit": 201}), 422, "validation_error")


def _poll(client, cursor=None, limit=200):
    params = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return client.get(PREFIX + "/events", params=params)


def _bronnummer(event):
    bron, nummer, kenmerk = event["payload"]["id"].split(":")
    assert UUID(kenmerk) == UUID(event["event_id"])
    assert event["payload"]["id"] == event["source_event_id"]
    return f"{bron}:{nummer}"


def test_bestaande_waarneming_behoudt_event_id_na_upgrade(viewer, db):
    at = datetime.now(UTC)
    try:
        assert migrate_down(db, 608) == [611, 610, 609]
        nummer = db.scalar("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',%s) RETURNING id", (at,))
    finally:
        assert migrate_up(db) == [609, 610, 611]
    legacy = f"automation:{nummer}"
    expected = uuid5(NAMESPACE_URL, f"urn:media-source-optimizer:{legacy}:{at.isoformat()}")
    event = next(e for e in _poll(viewer).json()["events"] if _bronnummer(e) == legacy)
    assert event["event_id"] == str(expected)
    assert event["source_event_id"] == f"{legacy}:{expected.hex}"
    assert viewer.get(f"{PREFIX}/evidence/{legacy}").json() == {**event["payload"], "id": legacy}


def test_upgrade_herkent_eerder_native_herstel(viewer, db):
    cursor = _poll(viewer).json()["next_cursor"]
    try:
        assert migrate_down(db, 608) == [611, 610, 609]
        nummer = audit("systeem", "backup.restored", "Back-up teruggezet met het vorige schema")
    finally:
        assert migrate_up(db) == [609, 610, 611]
    assert fout(_poll(viewer, cursor), 409, "cursor_expired") == {"restart": True}
    fout(viewer.get(f"{PREFIX}/evidence/audit:{nummer}"), 404, "not_found")
    event = next(e for e in _poll(viewer).json()["events"] if _bronnummer(e) == f"audit:{nummer}")
    assert viewer.get(f"{PREFIX}/evidence/{event['payload']['id']}").json() == event["payload"]


def test_c_cursor_en_retentie(viewer, db):
    db.execute("TRUNCATE audit_log RESTART IDENTITY")
    empty = _poll(viewer).json()
    assert empty["events"] == [] and empty["next_cursor"]
    db.execute("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',now()-interval '1 year')")
    audit("systeem", "test", "Voorbeeld")
    first = _poll(viewer, empty["next_cursor"], limit=1).json()
    assert first["events"] and first["next_cursor"] != empty["next_cursor"]
    assert retention.purge_logs(db)["automation_log"] == 1
    assert fout(_poll(viewer, empty["next_cursor"]), 409, "cursor_expired") == {"restart": True}
    reset = _poll(viewer).json()
    assert [_bronnummer(row) for row in reset["events"]] == ["audit:1"]
    assert _poll(viewer, reset["next_cursor"]).json() == {"events": [], "next_cursor": reset["next_cursor"]}
    fout(_poll(viewer, "ongeldige cursor"), 422, "validation_error")


def test_d_stabiele_ids_en_later_zichtbare_transactie(viewer, db):
    db.execute("TRUNCATE audit_log RESTART IDENTITY")
    at = datetime.now(UTC)
    db.execute("INSERT INTO audit_log(actor,action,summary,ts) VALUES ('systeem','test','',%s)", (at,))
    db.execute("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',%s)", (at,))
    first = _poll(viewer).json()
    assert first == _poll(viewer).json()
    events = first["events"]
    assert {_bronnummer(e) for e in events} == {"audit:1", "automation:1"}
    assert len({e["source_event_id"] for e in events}) == 2
    assert len({e["event_id"] for e in events}) == 2
    for event in events:
        assert set(event) == {"schema_version", "event_id", "source_app", "source_event_id", "event_type",
                              "entity", "timestamp_utc", "trace_id", "correlation_id", "actor",
                              "classification", "payload", "payload_ref"}
        UUID(event["event_id"])
        UUID(event["trace_id"])
        assert event["schema_version"] == "1.0" and event["timestamp_utc"].endswith("Z")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with db.tx() as pending:
            pending.execute("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',%s)", (at,))
            db.execute("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',%s)", (at,))
            inflight = executor.submit(_poll, viewer, first["next_cursor"])
            during = inflight.result(timeout=5)
            fout(during, 503, "dependency_unavailable")
            assert during.headers["Retry-After"]
    page = _poll(viewer, first["next_cursor"], limit=1).json()
    next_page = _poll(viewer, page["next_cursor"], limit=1).json()
    assert [_bronnummer(e) for e in page["events"] + next_page["events"]] == ["automation:2", "automation:3"]
    db.execute("UPDATE automation_log SET ts=ts + interval '1 second' WHERE id=3")
    fout(_poll(viewer, next_page["next_cursor"]), 409, "cursor_expired")


@pytest.mark.parametrize("action", ["propose", "apply", "rollback"])
def test_e_post_uitgeschakeld(action, admin, viewer, client, db, database_bytes, serve):
    mock = FastAPI()
    calls = []

    @mock.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def onverwachte_aanroep(path: str):
        calls.append(path)
        return {}

    db.execute("UPDATE connectors SET enabled=true, url=%s", (serve(mock),))
    key = create(admin)["key"]
    route = f"{PREFIX}/{action}"
    before = database_bytes(db)
    fout(viewer.post(route, json={}), 501, "capability_unavailable")
    fout(client.post(route, headers={"X-Api-Key": key}, json={}), 403, "forbidden")
    fout(viewer.post(route, headers={"X-CSRF-Token": "ongeldig"}, json={}), 403, "forbidden")
    assert database_bytes(db) == before
    assert calls == []


@pytest.mark.parametrize("chunked", [False, True])
def test_e_lichaamsgrens_voor_auth(client, chunked):
    content = b"x" * 1_048_577
    if chunked:
        content = iter((content[:-1], content[-1:]))
    response = client.post(PREFIX + "/apply", content=content, headers={"X-Api-Key": "ongeldig"})
    assert fout(response, 413, "payload_too_large") == {"max_bytes": 1_048_576}


def test_f_geen_geheimen_in_bereikbare_projecties(admin, viewer, db, caplog, monkeypatch):
    key = create(admin)["key"]
    password = USER_FIXTURE_PASSWORD
    get_vault().set("tests.connector.password", password)
    text = f"vrije tekst {password} {key}"
    detail = jsonb({"password": password, "genest": [{"waarde": text}]})
    change = db.scalar(
        "INSERT INTO change_sets(target,title,explanation,risk,impact) "
        "VALUES (%s,%s,%s,'advies',%s) RETURNING id", (text, text, text, detail))
    evidence = db.scalar("INSERT INTO audit_log(actor,action,target,summary,detail,change_id) "
                         "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id", (text, text, text, text, detail, change))
    db.execute("INSERT INTO automation_log(kind,area,result,message) VALUES ('run',%s,%s,%s)", (text, text, text))
    items = viewer.get(PREFIX + "/items", params={"type": "change_set"})
    events = _poll(viewer)
    bewijs = viewer.get(f"{PREFIX}/evidence/audit:{evidence}")
    for response in [items, events, bewijs]:
        assert response.status_code == 200
    assert items.json()["items"][0]["deep_link"] == f"/automatisering?tab=wijzigingen&change={change}"
    event = next(e for e in events.json()["events"] if _bronnummer(e) == f"audit:{evidence}")
    assert bewijs.json() == {**event["payload"], "id": f"audit:{evidence}"}
    assert viewer.get(f"{PREFIX}/evidence/{event['source_event_id']}").json() == event["payload"]
    invalid = viewer.get(PREFIX + "/items", params={"type": text}, headers={"X-Correlation-Id": password})
    fout(invalid, 422, "validation_error")
    assert invalid.headers.get("X-Correlation-Id") != password
    missing = viewer.get(PREFIX + "/bestaat-niet")
    fout(missing, 404, "not_found")
    fout(viewer.delete(PREFIX + "/discover"), 405, "method_not_allowed")
    def broken():
        raise RuntimeError(text)
    monkeypatch.setattr("mso.connector_api.router.collect_health", broken)
    error = viewer.get(PREFIX + "/health")
    fout(error, 500, "internal")
    assert "Onverwachte connectorfout: RuntimeError" in caplog.text
    for secret in [key, password]:
        assert secret.encode() not in b"".join(r.content for r in [items, events, bewijs, invalid, missing, error])
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "missing", ["database", "schema", "schema_checksum", "vault", "worker", "worker_oud", "ruimte"],
)
def test_readyz_bewaakt_eigen_afhankelijkheden(client, db, monkeypatch, missing):
    _hartslag(db, datetime.now(UTC))
    assert client.get("/readyz").json()["status"] == "ready"
    if missing == "database":
        monkeypatch.setattr(db, "ping", lambda: False)
    schema_row = db.fetchone("SELECT * FROM schema_migrations ORDER BY version DESC LIMIT 1")
    if missing == "schema":
        db.execute("DELETE FROM schema_migrations WHERE version=%(version)s", schema_row)
    if missing == "schema_checksum":
        db.execute("UPDATE schema_migrations SET checksum='' WHERE version=%(version)s", schema_row)
    if missing == "vault":
        monkeypatch.setattr(type(get_settings()), "secret_key_file", property(lambda s: s.data_dir / "ontbreekt"))
    if missing == "worker":
        db.execute("DELETE FROM system_state WHERE key='worker_heartbeat'")
    if missing == "worker_oud":
        db.execute("UPDATE system_state SET updated_at=now()-interval '91 seconds' WHERE key='worker_heartbeat'")
    if missing == "ruimte":
        monkeypatch.setattr("mso.backups.space_status", lambda db: {"ok": False, "free_bytes": 0})
    try:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert set(response.json()) == {"status", "checks"}
        assert all(isinstance(check, bool) for check in response.json()["checks"].values())
    finally:
        if missing in {"schema", "schema_checksum"}:
            db.execute("INSERT INTO schema_migrations(version,name,checksum,applied_at) "
                       "VALUES (%(version)s,%(name)s,%(checksum)s,%(applied_at)s) "
                       "ON CONFLICT (version) DO UPDATE SET checksum=EXCLUDED.checksum", schema_row)


@pytest.fixture()
def signed_change(app, manage_mode, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_change_safety import HUB_RATE_PATH, Fake

    from mso import changes

    source = Fake()
    source.values[HUB_RATE_PATH] = 100
    monkeypatch.setitem(changes.APPLIERS, "qbittorrent", source.applier)
    ch = changes.create("qbittorrent", "Snelheidslimiet", [
        {"path": HUB_RATE_PATH, "before": 100, "after": 80},
    ], risk="toestemming_nodig")
    return ch, source, Ed25519PrivateKey.generate()


def _signature(key, body):
    import base64
    import json

    payload = {k: v for k, v in body.items() if k != "signature"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {**payload, "signature": base64.urlsafe_b64encode(key.sign(canonical)).decode().rstrip("=")}


def _execution(ch, key, *, rollback=False, now=None):
    import hashlib
    import json
    from uuid import uuid4

    action = "mso.qbittorrent.speed_limits"
    now = now or datetime.now(UTC).replace(microsecond=0)
    def at(value):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    etag = ch["resource_etag"] if rollback else ch["preview_hash"]
    parameters = {"expected_prev" if rollback else "expected_preview_hash": etag}
    target = {"app_id": "media-source-optimizer", "change_id": ch["id"] if "id" in ch else ch["change_id"]}
    policy = "sha256:" + "a" * 64
    proposal_id = str(uuid4())
    parameters_hash = "sha256:" + hashlib.sha256(json.dumps(parameters, separators=(",", ":")).encode()).hexdigest()
    return _signature(key, {
        "proposal": {"idempotency_key": proposal_id, "action_id": action, "target": target, "parameters": parameters,
                     "actor": {"type": "user", "id": "test"}, "expected_version": etag,
                     "reason": "Lokale fixture", "risk_claim": "R2", "evidence_ids": [], "expires_in_seconds": 300},
        "approval": {"approval_id": str(uuid4()), "request_id": proposal_id, "preview_hash": ch["preview_hash"],
                     "resource_etag": etag, "policy_hash": policy, "risk_class": "R2", "approved_by": "test",
                     "approved_at": at(now), "expires_at": at(now + timedelta(minutes=10)),
                     "one_time_nonce": str(uuid4()), "backup_checkpoint_id": None,
                     "action_scope": {"action_id": action, "target": target, "parameters_sha256": parameters_hash}},
        "policy_decision": {"risk_class": "R2", "rule_ids": ["test"], "policy_hash": policy},
        "issued_at": at(now), "expires_at": at(now + timedelta(minutes=5)), "nonce": str(uuid4()),
    })


def _pin(key, monkeypatch):
    import base64
    import json

    public = base64.urlsafe_b64encode(key.public_key().public_bytes_raw()).decode().rstrip("=")
    monkeypatch.setattr(get_settings(), "hub_policy_public_key", json.dumps({
        "kty": "OKP", "crv": "Ed25519", "x": public,
    }), raising=False)
    monkeypatch.setattr(get_settings(), "hub_policy_hash", "sha256:" + "a" * 64, raising=False)


def _connector_headers():
    from uuid import uuid4

    return {"Idempotency-Key": str(uuid4())}


def test_signed_apply_rollback_and_replay(client, viewer, db, monkeypatch, signed_change):
    from test_change_safety import HUB_RATE_PATH

    ch, source, key = signed_change
    _pin(key, monkeypatch)
    discovery = viewer.get(PREFIX + "/discover").json()
    assert discovery["capabilities"] == ["read", "apply", "rollback"]
    assert {a["action_id"] for a in discovery["actions"]} == {
        "mso.sabnzbd.server_limits", "mso.qbittorrent.speed_limits",
    }
    headers = _connector_headers()
    execution = _execution(ch, key)
    response = client.post(PREFIX + "/apply", json=execution, headers=headers)
    assert response.status_code == 200
    applied = response.json()
    assert applied["status"] == "applied"
    assert applied["readback"] == {"verified": True, "results": ["toegepast"]}
    replay = client.post(PREFIX + "/apply", json=execution, headers=headers)
    assert replay.json() == applied
    assert replay.headers["Idempotent-Replay"] == "true"
    fout(client.post(PREFIX + "/apply", json=execution, headers=_connector_headers()), 409, "nonce_reused")
    rollback = _execution(applied, key, rollback=True)
    response = client.post(PREFIX + "/rollback", json=rollback, headers=_connector_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "rolled_back"
    assert response.json()["after_hash"] == applied["before_hash"]
    assert response.json()["before_hash"] == applied["after_hash"]
    assert source.writes == [(HUB_RATE_PATH, 80), (HUB_RATE_PATH, 100)]
    assert db.scalar("SELECT count(*) FROM audit_log WHERE change_id=%s AND action='change.applied'", (ch["id"],)) == 1


@pytest.mark.parametrize("variant,status,code", [
    ("geen_handtekening", 422, "validation_error"), ("verkeerde_sleutel", 401, "unauthorized"),
    ("verlopen", 409, "approval_expired"), ("policy", 403, "forbidden"), ("etag", 412, "precondition_failed"),
    ("preview", 409, "preview_changed"), ("force_query", 422, "validation_error"),
    ("force_body", 422, "validation_error"),
])
def test_signed_execution_refuses_without_writes(client, db, database_bytes, monkeypatch, signed_change, variant,
                                                  status, code):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    ch, source, key = signed_change
    _pin(key, monkeypatch)
    route = PREFIX + "/apply"
    fixture = {**ch}
    if variant == "preview":
        fixture["preview_hash"] = "sha256:" + "b" * 64
    execution = _execution(fixture, Ed25519PrivateKey.generate() if variant == "verkeerde_sleutel" else key,
                           now=datetime.now(UTC) - timedelta(minutes=16) if variant == "verlopen" else None)
    if variant == "geen_handtekening":
        del execution["signature"]
    elif variant == "policy":
        monkeypatch.setattr(get_settings(), "hub_policy_hash", "sha256:" + "b" * 64)
    elif variant == "etag":
        execution["proposal"]["expected_version"] = execution["approval"]["resource_etag"] = "sha256:" + "b" * 64
        execution = _signature(key, execution)
    elif variant == "force_query":
        route += "?force=true"
    elif variant == "force_body":
        execution = _signature(key, {**execution, "force": True})
    before = database_bytes(db)
    fout(client.post(route, json=execution, headers=_connector_headers()), status, code)
    assert database_bytes(db) == before
    assert source.writes == []


def test_signed_apply_recovers_result_after_source_write(app, client, db, monkeypatch, signed_change):
    from fastapi.testclient import TestClient
    from test_change_safety import HUB_RATE_PATH

    from mso import changes

    ch, source, key = signed_change
    _pin(key, monkeypatch)
    headers, execution = _connector_headers(), _execution(ch, key)
    failing = TestClient(app, raise_server_exceptions=False)

    def interrupt(*args, **kwargs):
        raise RuntimeError("Gecontroleerde onderbreking vóór de resultaattransactie")

    with monkeypatch.context() as patch:
        patch.setattr(changes, "_finish", interrupt)
        assert failing.post(PREFIX + "/apply", json=execution, headers=headers).status_code == 500
    assert changes.get(ch["id"])["status"] == "applying"
    fout(client.post(PREFIX + "/apply", json=execution, headers=headers), 409, "idempotency_in_progress")
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "afgerond"}
    replay = client.post(PREFIX + "/apply", json=execution, headers=headers)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json()["status"] == "applied"
    assert source.writes == [(HUB_RATE_PATH, 80)]
    assert db.scalar("SELECT count(*) FROM audit_log WHERE change_id=%s AND action='change.applied'", (ch["id"],)) == 1


def test_restore_does_not_reopen_consumed_nonce(client, db, monkeypatch, signed_change):
    from test_change_safety import HUB_RATE_PATH

    from mso import backups

    ch, source, key = signed_change
    _pin(key, monkeypatch)
    snapshot = backups.create_backup(actor="test")
    headers, execution = _connector_headers(), _execution(ch, key)
    assert client.post(PREFIX + "/apply", json=execution, headers=headers).status_code == 200
    backups.restore_backup(snapshot["id"], "test")
    assert db.scalar("SELECT status FROM change_sets WHERE id=%s", (ch["id"],)) == "preview"
    fout(client.post(PREFIX + "/apply", json=execution, headers=headers), 409, "conflict")
    fout(client.post(PREFIX + "/apply", json=execution, headers=_connector_headers()), 409, "nonce_reused")
    assert source.writes == [(HUB_RATE_PATH, 80)]
