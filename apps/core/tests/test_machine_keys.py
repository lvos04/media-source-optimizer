"""Machine-identiteiten blijven rolvrij en alle niet geopende wegen blijven dicht."""

import json
import threading
import time
from pathlib import Path

import pytest


def create(admin, scopes=None, name="Testkoppeling"):
    response = admin.post("/api/v1/machine-keys", json={"name": name, "scopes": scopes or ["read"]})
    assert response.status_code == 200, response.text
    return response.json()


def revoke(admin, key_id, request_id=None, body=None):
    import uuid
    return admin.post(f"/api/v1/machine-keys/{key_id}/revoke", json=body,
                      headers={"Idempotency-Key": str(request_id or uuid.uuid4())})


def test_create_read_revoke(admin, client, db):
    made = create(admin)
    headers = {"X-Api-Key": made["key"]}
    assert client.get("/api/v1/overview", headers=headers).status_code == 200
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    assert client.get("/onbekend", headers=headers).status_code == 403
    assert client.get("/api/v1/overview?force=false", headers=headers).status_code == 403
    assert revoke(admin, made["item"]["id"]).status_code == 200
    assert client.get("/api/v1/overview", headers=headers).status_code == 401


def test_no_implicit_read(admin, client):
    made = create(admin, ["changes:approve"])
    assert client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code == 403


def test_duplicate_headers_and_uppercase_force_are_denied(admin, client):
    key = create(admin)["key"]
    headers = [("X-Api-Key", key), ("X-Api-Key", "ongeldig")]
    assert client.get("/api/v1/overview", headers=headers).status_code == 401
    assert client.get("/api/v1/overview?FORCE=false", headers={"X-Api-Key": key}).status_code == 403


def test_principal_checks_route_without_middleware(admin):
    from fastapi import HTTPException, Request

    from mso.auth import principal_from_request

    key = create(admin)["key"]
    request = Request({"type": "http", "method": "GET", "path": "/api/v1/users",
                       "headers": [(b"x-api-key", key.encode())]})
    with pytest.raises(HTTPException) as denied:
        principal_from_request(request)
    assert denied.value.status_code == 403


def test_only_admin_can_manage_keys_and_each_operation_is_audited(admin, operator, db):
    body = {"name": "Geen beheer voor operator", "scopes": ["read"]}
    assert operator.post("/api/v1/machine-keys", json=body).status_code == 403
    assert operator.get("/api/v1/machine-keys").status_code == 403
    made = create(admin)
    key_id = made["item"]["id"]
    assert revoke(operator, key_id).status_code == 403
    listed = admin.get("/api/v1/machine-keys")
    assert listed.status_code == 200
    assert revoke(admin, key_id).status_code == 200
    entries = db.fetchall("SELECT actor,action,target,summary,detail FROM audit_log "
                          "WHERE action LIKE 'machine_keys.%' ORDER BY id")
    assert [entry["action"] for entry in entries] == [
        "machine_keys.create", "machine_keys.list", "machine_keys.revoke",
    ]
    assert all(entry["actor"] == "beheerder" for entry in entries)
    assert [entry["target"] for entry in entries] == [key_id, None, key_id]
    key_hash = db.scalar("SELECT key_hash FROM machine_keys WHERE id=%s", (key_id,))
    audit_text = json.dumps(entries)
    assert made["key"] not in audit_text and key_hash not in audit_text


def test_hash_comparison_and_no_cookie_fallback(admin, client, monkeypatch):
    from mso import machine_keys

    made = create(admin)
    original = machine_keys.hmac.compare_digest
    lengths = []

    def measured(a, b):
        lengths.append((len(a), len(b)))
        return original(a, b)

    monkeypatch.setattr(machine_keys.hmac, "compare_digest", measured)
    assert client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code == 200
    wrong = made["key"][:-1] + ("a" if made["key"][-1] != "a" else "b")
    assert client.get("/api/v1/overview", headers={"X-Api-Key": wrong}).status_code == 401
    assert admin.get("/api/v1/overview", headers={"X-Api-Key": ""}).status_code == 401
    assert lengths and all(pair == (64, 64) for pair in lengths)


def test_redaction_layer_on_machine_secret(admin):
    from mso.audit import _clean
    from mso.security import redact

    key = create(admin)["key"]
    for text in [key, f"X-Api-Key: {key}", f"sleutel {key} geweigerd", f'{{"api_key":"{key}"}}']:
        assert key not in redact(text)
        assert key not in json.dumps(_clean({"message": text, "X-Api-Key": key}))


@pytest.mark.parametrize("scopes", [None, [], {}, "read", ["read", "onbekend"], [1]])
def test_corrupt_scopes_fail_closed(admin, client, db, scopes):
    from mso.db import jsonb
    made = create(admin)
    db.execute("UPDATE machine_keys SET scopes=%s WHERE id=%s", (jsonb(scopes), made["item"]["id"]))
    assert client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code == 403


def test_all_writes_denied(admin, client, db, database_bytes, monkeypatch):
    from mso import machine_keys

    made = create(admin)
    export = Path(__file__).resolve().parents[3] / "docs/openapi/media-source-optimizer.openapi.json"
    schema = json.loads(export.read_text())
    tested = []
    checked = []
    original = machine_keys.authorize

    def tracked(request, principal):
        checked.append((request.scope["method"].lower(), request.scope["machine_route"].path))
        return original(request, principal)

    monkeypatch.setattr(machine_keys, "authorize", tracked)

    def example(shape, field=""):
        if "$ref" in shape:
            return example(schema["components"]["schemas"][shape["$ref"].rsplit("/", 1)[1]], field)
        if "anyOf" in shape:
            return example(next(s for s in shape["anyOf"] if s.get("type") != "null"), field)
        if "enum" in shape:
            return shape["enum"][0]
        if shape.get("type") == "object":
            return {key: example(shape["properties"][key], key) for key in shape.get("required", [])}
        if shape.get("type") == "array":
            return [example(shape.get("items", {}), field)] * max(1, shape.get("minItems", 0))
        if shape.get("type") in {"integer", "number"}:
            return max(1, shape.get("minimum", 1))
        if shape.get("type") == "boolean":
            return False
        values = {"mode": "read_only", "role": "viewer", "window": "24h", "api_path": "/api",
                  "release_key": "nzb:fixture", "source_hash": "a" * 40, "password": "Testtesttest1",
                  "new_password": "Testtesttest1", "url": "https://dienst.invalid"}
        if shape.get("format") == "date":
            return "2026-01-01"
        if shape.get("format") == "date-time":
            return "2026-01-01T00:00:00Z"
        return values.get(field, "fixture" * max(1, (shape.get("minLength", 1) + 6) // 7))

    before = database_bytes(db)
    for path, operations in schema["paths"].items():
        import re
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in operations:
            if method not in {"post", "put", "patch", "delete"}:
                continue
            operation = operations[method]
            content = operation.get("requestBody", {}).get("content", {})
            shape = next(iter(content.values()), {}).get("schema", {})
            body = {"name": "Schrijfproef", "scopes": ["read"]} if path == "/api/v1/machine-keys" else example(shape)
            response = client.request(method, url, headers={"X-Api-Key": made["key"]}, json=body)
            assert response.status_code == 403, (method, path, response.status_code)
            tested.append((method, path))
    assert len(tested) >= 100
    assert sorted(checked) == sorted(tested)
    assert database_bytes(db) == before


def test_invalid_creation_and_unique_names(admin):
    assert admin.post("/api/v1/machine-keys", json={"name": "Test", "scopes": ["unknown"]}).status_code == 422
    assert admin.post("/api/v1/machine-keys", json={"name": "Test", "scopes": []}).status_code == 422
    create(admin)
    assert admin.post("/api/v1/machine-keys", json={"name": " testkoppeling ", "scopes": ["read"]}).status_code == 409


def test_revoke_idempotency_and_contract_errors(admin, db, app):
    import uuid
    made = create(admin)
    key_id = made["item"]["id"]
    missing = admin.post(f"/api/v1/machine-keys/{key_id}/revoke")
    assert missing.status_code == 400 and missing.json()["code"] == "idempotency_key_required"
    for field in ["message", "details", "trace_id", "detail", "code"]:
        assert field in missing.json()
    uuid.UUID(missing.json()["trace_id"])
    request_id = uuid.uuid4()
    first = revoke(admin, key_id, request_id)
    replay = revoke(admin, key_id, request_id)
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json() and replay.headers["Idempotent-Replay"] == "true"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='machine_keys.revoke'") == 1
    changed = revoke(admin, key_id, request_id, {"andere": "invoer"})
    assert changed.status_code == 422 and changed.json()["code"] == "idempotency_key_reused"
    second = create(admin, name="Tweede")
    assert revoke(admin, second["item"]["id"], request_id).status_code == 422
    assert db.scalar("SELECT revoked_at FROM machine_keys WHERE id=%s", (second["item"]["id"],)) is None
    operation = app.openapi()["paths"]["/api/v1/machine-keys/{key_id}/revoke"]["post"]
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in operation["parameters"])


def test_concurrent_revoke_idempotency(admin, db):
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    made = create(admin)
    request_id = uuid.uuid4()
    start = threading.Barrier(2)

    def run():
        start.wait(timeout=10)
        return revoke(admin, made["item"]["id"], request_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: run(), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='machine_keys.revoke'") == 1


def test_all_new_mutations_require_idempotency_or_documented_secret_exception(app):
    exceptions = {
        ("post", "/api/v1/machine-keys"): "Bewuste eenmalige uitgifte van een nieuw geheim (contract §5).",
        ("post", "/connector/v1/propose"): "Uitgeschakeld met 501 capability_unavailable (contract §5).",
    }

    baseline = json.loads((Path(__file__).parent / "fixtures/native_operations_before_machine_keys.json").read_text())
    legacy = {tuple(pair) for pair in baseline["operations"]}
    checked = set()
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            if method not in {"post", "put", "patch", "delete"}:
                continue
            if (method, path) in legacy:
                # Bestaande native routes behouden hun contract (§2.3 regel 1).
                continue
            checked.add((method, path))
            if (method, path) in exceptions:
                assert operation["x-idempotency-exception"] == exceptions[(method, path)]
            else:
                assert "x-idempotency-exception" not in operation
                assert any(p["name"] == "Idempotency-Key" and p["required"]
                           for p in operation.get("parameters", [])), (method, path)
    assert checked == {
        ("post", "/api/v1/machine-keys/{key_id}/revoke"),
        ("post", "/connector/v1/apply"), ("post", "/connector/v1/rollback"), *exceptions,
    }


def test_every_unopened_get_is_denied_without_side_effects(admin, client, db, app, database_bytes):
    import re

    from mso.machine_keys import READ_ROUTES

    made = create(admin)
    before = database_bytes(db)
    denied = []
    for path, operations in app.openapi()["paths"].items():
        if "get" in operations and path not in READ_ROUTES:
            response = client.get(re.sub(r"\{[^}]+\}", "99999999", path), headers={"X-Api-Key": made["key"]})
            assert response.status_code == 403, path
            denied.append(path)
    assert len(denied) >= 30
    assert database_bytes(db) == before


def test_known_sensitive_and_side_effect_gets_stay_closed(admin, client, db, database_bytes):
    made = create(admin)
    before = database_bytes(db)
    for path in ["/api/v1/users", "/api/v1/machine-keys", "/api/v1/settings", "/api/v1/backups",
                 "/api/v1/costs/services", "/api/v1/newznab/endpoint", "/api/v1/torrent/tuning",
                 "/api/v1/mediaguard/status?refresh=true", "/api/v1/usenet/sab/servers",
                 "/api/v1/usenet/categories/diagnosis?refresh=true"]:
        assert client.get(path, headers={"X-Api-Key": made["key"]}).status_code == 403, path
    assert database_bytes(db) == before


def test_opened_reads_only_update_usage_time(admin, client, db, database_bytes):
    import re

    from mso.machine_keys import READ_ROUTES

    made = create(admin)
    before = database_bytes(db)
    for path in sorted(READ_ROUTES):
        response = client.get(re.sub(r"\{[^}]+\}", "99999999", path), headers={"X-Api-Key": made["key"]})
        assert response.status_code in {200, 404}, (path, response.status_code)
    after = database_bytes(db)
    assert {k: v for k, v in before.items() if k != "machine_keys"} == {
        k: v for k, v in after.items() if k != "machine_keys"}
    assert db.scalar("SELECT last_used_at FROM machine_keys WHERE id=%s", (made["item"]["id"],)) is not None


@pytest.mark.parametrize("underscore", [4, 23, 39, 42])
def test_secret_never_returned_or_logged(admin, client, db, caplog, database_bytes, monkeypatch, underscore):
    import base64
    from urllib.parse import quote

    from mso import machine_keys

    deterministic = "A" * underscore + "_" + "B" * (42 - underscore)
    monkeypatch.setattr(machine_keys.secrets, "token_urlsafe", lambda _: deterministic)
    made = create(admin)
    key = made["key"]
    secret = machine_keys.KEY_RE.fullmatch(key).group(2)
    listed = admin.get("/api/v1/machine-keys").json()
    assert listed["items"][0]["last_four"] == secret[-4:]
    assert set(listed["items"][0]) == {"id", "name", "scopes", "created_at", "last_used_at", "revoked_at", "last_four"}
    client.post("/api/v1/users", headers={"X-Api-Key": key}, json={})
    snapshot = json.dumps({k: v.decode() for k, v in database_bytes(db).items()})
    for value in [key, quote(key), base64.b64encode(key.encode()).decode(), secret]:
        assert value not in snapshot + json.dumps(listed) + caplog.text


def test_existing_sessions(admin, viewer, operator, client):
    assert admin.get("/api/v1/auth/me").json()["user"]["role"] == "admin"
    assert viewer.get("/api/v1/overview").status_code == 200
    assert operator.get("/api/v1/users").status_code == 403
    assert client.get("/api/v1/overview").status_code == 401
    assert viewer.get("/api/v1/machine-keys").status_code == 403


def test_role_free_and_internal_admin_denied(admin, client, app):
    from fastapi import HTTPException
    from starlette.requests import Request

    from mso.auth import principal_from_request, require
    from mso.machine_keys import READ_ROUTES
    made = create(admin)
    request = Request({"type": "http", "method": "GET", "path": "/api/v1/overview", "query_string": b"",
                       "headers": [(b"x-api-key", made["key"].encode())],
                       "machine_route": type("Route", (), {"path": "/api/v1/overview"})()})
    p = principal_from_request(request)
    assert p.machine and p.role is None and p.user_id is None and not p.can("viewer")
    with pytest.raises(HTTPException) as denied:
        require("admin")(p)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        require("viewer")(p)
    assert denied.value.status_code == 403
    assert READ_ROUTES <= set(app.openapi()["paths"])


def test_internal_key_management_denied(admin, db, database_bytes):
    import uuid

    from fastapi import HTTPException

    from mso.auth import Principal
    from mso.machine_keys import MachineKeyCreate, create_key, list_keys, revoke_key

    made = create(admin)
    machine = Principal("machine", None, machine=True, scopes=frozenset({"read", "changes:approve"}))
    before = database_bytes(db)
    operations = [lambda: list_keys(machine),
                  lambda: create_key(MachineKeyCreate(name="Interne omweg", scopes=["read"]), machine),
                  lambda: revoke_key(uuid.UUID(made["item"]["id"]), machine)]
    for operation in operations:
        with pytest.raises(HTTPException) as denied:
            operation()
        assert denied.value.status_code == 403
    assert database_bytes(db) == before


def test_alias_public_and_websocket_denied(admin, client, app):
    from fastapi import APIRouter
    from starlette.websockets import WebSocketDisconnect

    from mso.features.overview.api import overview
    router = APIRouter()
    router.add_api_route("/machine-alias-probe", overview, methods=["GET"])
    app.include_router(router)
    try:
        made = create(admin)
        headers = {"X-Api-Key": made["key"]}
        paths = ("/machine-alias-probe", "/healthz", "/api/openapi.json", "/api/v1/setup/status", "/api/v1/auth/me")
        for path in paths:
            assert client.get(path, headers=headers).status_code == 403
        with (
            pytest.raises(WebSocketDisconnect) as denied,
            client.websocket_connect("/machine-ws-probe", headers=headers),
        ):
            pytest.fail("Websocket stond open")
        assert denied.value.code == 4403
    finally:
        # Verwijder uitsluitend deze eigen testrouter, ook als de proef faalt.
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "original_router", None) is not router]


@pytest.mark.parametrize("endpoint_error", [False, True])
def test_revoke_during_request(admin, client, db, monkeypatch, endpoint_error):
    from fastapi import HTTPException

    from mso.features.overview import api
    made = create(admin)
    entered, release = threading.Event(), threading.Event()
    original = api.connectors_block
    def blocked(database):
        entered.set()
        assert release.wait(10)
        if endpoint_error:
            raise HTTPException(409, "Fout in leesroute van de proef")
        return original(database)
    monkeypatch.setattr(api, "connectors_block", blocked)
    result = []
    thread = threading.Thread(target=lambda: result.append(client.get(
        "/api/v1/overview", headers={"X-Api-Key": made["key"]})))
    thread.start()
    try:
        assert entered.wait(10)
        assert revoke(admin, made["item"]["id"]).status_code == 200
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert result[0].status_code == 401
    assert db.scalar("SELECT last_used_at FROM machine_keys WHERE id=%s", (made["item"]["id"],)) is None


def test_revoke_during_usage_update(admin, client, monkeypatch):
    from mso import machine_keys

    made = create(admin)
    entered, release = threading.Event(), threading.Event()

    def blocked(_):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(machine_keys, "touch", blocked)
    result = []
    thread = threading.Thread(target=lambda: result.append(client.get(
        "/api/v1/overview", headers={"X-Api-Key": made["key"]})))
    thread.start()
    try:
        assert entered.wait(10)
        assert revoke(admin, made["item"]["id"]).status_code == 200
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and result[0].status_code == 401


def test_new_management_body_limit_before_auth(client):
    response = client.post("/api/v1/machine-keys", content=b"x" * 1_048_577)
    assert response.status_code == 413
    assert response.json()["details"]["max_bytes"] == 1_048_576
    chunks = iter([b"x" * 700_000, b"x" * 700_000])
    response = client.post("/api/v1/machine-keys", content=chunks, headers={"X-Api-Key": "ongeldig"})
    assert response.status_code == 413
    assert client.post("/api/v1/machine-keys", content=b"{}", headers={
        "Content-Length": "1048577"}).status_code == 413


def test_create_and_revoke_visible_in_other_process(admin, client, db):
    import select
    import subprocess
    import sys

    made = create(admin)
    code = '''import sys
from fastapi import HTTPException
from starlette.requests import HTTPConnection
from mso.db import Database, set_db
from mso.machine_keys import authenticate, touch
db=Database(sys.argv[1]); set_db(db)
try:
    key=sys.stdin.readline().strip()
    for line in sys.stdin:
        try:
            request=HTTPConnection({'type':'http','headers':[(b'x-api-key',key.encode())]})
            principal=authenticate(request)
            assert principal.machine and principal.role is None
            touch(principal.machine_key_id)
            print('rolvrije machine herkend', flush=True)
        except HTTPException as exc: print(exc.status_code, flush=True)
finally: db.close()
'''
    process = subprocess.Popen([sys.executable, "-c", code, db.url], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        process.stdin.write(made["key"] + "\n")

        def check():
            process.stdin.write("controleer\n")
            process.stdin.flush()
            assert select.select([process.stdout], [], [], 15)[0], "Tweede proces antwoordde niet"
            return process.stdout.readline().strip()

        assert check() == "rolvrije machine herkend"
        assert db.scalar("SELECT last_used_at FROM machine_keys WHERE id=%s", (made["item"]["id"],)) is not None
        assert revoke(admin, made["item"]["id"]).status_code == 200
        assert check() == "401"
        process.stdin.close()
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_last_used_does_not_wait_for_locked_row(admin, client, db):
    made = create(admin)
    with db.tx() as conn:
        conn.execute("SELECT id FROM machine_keys WHERE id=%s FOR UPDATE", (made["item"]["id"],))
        start = time.monotonic()
        response = client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]})
        assert response.status_code == 200
        assert time.monotonic() - start < 2
    assert db.scalar("SELECT last_used_at FROM machine_keys WHERE id=%s", (made["item"]["id"],)) is None
    assert client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code == 200
    assert db.scalar("SELECT last_used_at FROM machine_keys WHERE id=%s", (made["item"]["id"],)) is not None


def test_rate_limit_shared_by_processes(admin, client, db):
    import subprocess
    import sys

    from mso import settings_registry
    settings_registry.put({"security.login_rate_per_min": 3}, "test")
    code = '''import sys
from starlette.requests import HTTPConnection
from fastapi import HTTPException
from mso.db import Database, set_db
from mso.machine_keys import authenticate
db=Database(sys.argv[1]); set_db(db)
request=HTTPConnection({"type":"http","headers":[(b"x-api-key",b"ongeldig")],"client":("testclient",1)})
try: authenticate(request)
except HTTPException as exc: print(exc.status_code)
finally: db.close()
'''
    statuses = []
    for _ in range(4):
        result = subprocess.run([sys.executable, "-c", code, db.url], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        statuses.append(result.stdout.strip())
    assert statuses == ["401", "401", "401", "429"]
    response = client.get("/api/v1/overview", headers={"X-Api-Key": "ongeldig", "X-Forwarded-For": "ignored"})
    assert response.status_code == 429 and int(response.headers["Retry-After"]) >= 1


def test_failed_key_identity_limit_across_sources(admin, app):
    from fastapi.testclient import TestClient

    from mso import settings_registry

    made = create(admin)
    settings_registry.put({"security.login_rate_per_min": 3}, "test")
    wrong = made["key"][:-1] + ("a" if made["key"][-1] != "a" else "b")
    statuses = []
    for number in range(4):
        caller = TestClient(app, client=(f"testbron-{number}", 1))
        statuses.append(caller.get("/api/v1/overview", headers={"X-Api-Key": wrong}).status_code)
    assert statuses == [401, 401, 401, 429]


def test_migration_roundtrip_preserves_existing_tables(admin, operator, db, database_bytes):
    from mso.db import check_schema_compatibility, migrate_down, migrate_up

    assert operator.post("/api/v1/system/pause").status_code == 200
    assert admin.get("/api/v1/newznab/endpoint").status_code == 200
    made = create(admin)
    new_tables = {"machine_keys", "machine_key_requests", "machine_auth_limits", "connector_idempotency",
                  "resource_samples", "sab_send_receipts", "download_receipt_targets", "download_receipt_origin",
                  "schema_migrations"}
    before = database_bytes(db)
    try:
        assert migrate_down(db, 607) == [613, 612, 611, 610, 609, 608]
        assert db.scalar("SELECT to_regclass('machine_keys')") is None
        assert db.scalar("SELECT to_regclass('connector_idempotency')") is None
        assert db.scalar("SELECT to_regclass('sab_send_receipts')") is None
        assert {name: data for name, data in before.items() if name not in new_tables} == {
            name: data for name, data in database_bytes(db).items() if name != "schema_migrations"
        }
        assert admin.get("/api/v1/auth/me").status_code == 200
    finally:
        assert migrate_up(db) == [608, 609, 610, 611, 612, 613]
    assert db.scalar("SELECT count(*) FROM connector_idempotency") == 0
    check_schema_compatibility(db)
    after = database_bytes(db)
    assert {name: data for name, data in before.items() if name not in new_tables} == {
        name: data for name, data in after.items() if name not in new_tables
    }
    assert made["key"] not in str(after)


def test_backup_restore_never_reactivates_revoked_or_old_scopes(admin, client, db):
    from mso import backups
    from mso.db import jsonb
    from mso.machine_keys import security_snapshot

    made = create(admin)
    kept = create(admin, name="Blijft actueel")
    backup = backups.create_backup(actor="test")
    assert revoke(admin, made["item"]["id"]).status_code == 200
    db.execute("UPDATE machine_keys SET scopes=%s WHERE id=%s", (jsonb(["changes:approve"]), kept["item"]["id"]))
    after_dump = create(admin, name="Na de back-up")
    before = security_snapshot(db)
    backups.restore_backup(backup["id"], "test")
    assert security_snapshot(db) == before
    assert client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code == 401
    assert client.get("/api/v1/overview", headers={"X-Api-Key": kept["key"]}).status_code == 403
    assert client.get("/api/v1/overview", headers={"X-Api-Key": after_dump["key"]}).status_code == 200


def test_machine_reads_closed_during_restore(admin, client, db, monkeypatch):
    from mso import backups

    made = create(admin)
    assert revoke(admin, made["item"]["id"]).status_code == 200

    def intermediate(*args):
        # Een teruggezette tabel kan tijdelijk een oudere, actieve rij bevatten.
        db.execute("UPDATE machine_keys SET revoked_at=NULL WHERE id=%s", (made["item"]["id"],))
        status = client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code
        assert status == 403
        from mso.machine_keys import restore_security_snapshot
        restore_security_snapshot(args[2], db)
        return {}

    monkeypatch.setattr(backups, "_restore_backup", intermediate)
    backups.restore_backup(0, "test")
    assert client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code == 401


def test_revoke_during_backup_restore_is_not_lost(admin, client, db, monkeypatch):
    from mso import backups
    from mso.machine_keys import ADMIN_LOCK

    made = create(admin)
    backup = backups.create_backup(actor="test")
    entered, release, revoke_done = threading.Event(), threading.Event(), threading.Event()
    original = backups._restore_backup
    restored, revoked = [], []

    def blocked(*args):
        entered.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(backups, "_restore_backup", blocked)
    restore_thread = threading.Thread(target=lambda: restored.append(backups.restore_backup(backup["id"], "test")))

    def revoke_now():
        try:
            revoked.append(revoke(admin, made["item"]["id"]))
        finally:
            revoke_done.set()

    revoke_thread = threading.Thread(target=revoke_now)
    restore_thread.start()
    try:
        assert entered.wait(10)
        revoke_thread.start()
        deadline = time.monotonic() + 10
        while not revoke_done.is_set():
            waiting = db.scalar("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=%s "
                                "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                                "AND NOT granted", (ADMIN_LOCK,))
            if waiting:
                break
            assert time.monotonic() < deadline, "Intrekken bereikte de herstelgrens niet"
            time.sleep(0.01)
    finally:
        release.set()
        restore_thread.join(30)
        if revoke_thread.ident is not None:
            revoke_thread.join(30)
    assert not restore_thread.is_alive() and not revoke_thread.is_alive()
    assert restored and revoked[0].status_code == 200
    assert client.get("/api/v1/overview", headers={"X-Api-Key": made["key"]}).status_code == 401


def test_newer_schema_refused_after_machine_migration(db, database_bytes):
    from mso.db import NewerDatabaseError, check_schema_compatibility, load_migrations

    future = max(m.version for m in load_migrations()) + 1
    db.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES (%s,'toekomst','proef')", (future,))
    try:
        before = database_bytes(db)
        with pytest.raises(NewerDatabaseError):
            check_schema_compatibility(db)
        assert database_bytes(db) == before
    finally:
        db.execute("DELETE FROM schema_migrations WHERE version=%s", (future,))
