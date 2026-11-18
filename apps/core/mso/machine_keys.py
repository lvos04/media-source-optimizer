"""Rolvrije machine-identiteiten; een expliciete leeslijst en centraal gesloten toegang."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.routing import Match

from mso.audit import audit
from mso.auth import Principal, current_principal, token_hash
from mso.db import Database, get_db, jsonb
from mso.security import limiter

SCOPES = frozenset({"read", "changes:approve"})
IDEMPOTENCY_EXCEPTIONS = {
    ("post", "/api/v1/machine-keys"): "Bewuste eenmalige uitgifte van een nieuw geheim (contract §5).",
}
KEY_RE = re.compile(r"mso_mk_([0-9a-f]{32})_([A-Za-z0-9_-]{43})", re.ASCII)
PUBLIC_FIELDS = ("id", "name", "scopes", "created_at", "last_used_at", "revoked_at", "last_four")
ADMIN_LOCK = 727008


def has_machine_header(request: HTTPConnection) -> bool:
    values = request.headers.getlist("x-api-key")
    if request.url.path.startswith(("/newznab/", "/torznab/")):
        return any(value.startswith("mso_mk_") for value in values)
    return bool(values)


@contextmanager
def administration_lock(db: Database) -> Iterator[None]:
    """Herstel en sleutelbeheer delen één slot; gewone leesverzoeken nemen dit nooit."""
    with db.pool.connection() as conn:
        conn.autocommit = True
        try:
            conn.execute("SELECT pg_advisory_lock(%s)", (ADMIN_LOCK,))
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADMIN_LOCK,))
            conn.autocommit = False


def security_snapshot(db: Database) -> dict[str, list[dict[str, Any]]]:
    if db.scalar("SELECT to_regclass('machine_keys')") is None:
        return {"machine_keys": [], "machine_key_requests": []}
    return {"machine_keys": db.fetchall("SELECT * FROM machine_keys ORDER BY id"),
            "machine_key_requests": db.fetchall("SELECT * FROM machine_key_requests ORDER BY request_id")}


def restore_security_snapshot(snapshot: dict[str, list[dict[str, Any]]], db: Database) -> None:
    """Een gegevensherstel activeert nooit een oude sleutel of oudere rechten opnieuw."""
    if db.scalar("SELECT to_regclass('machine_keys')") is None or security_snapshot(db) == snapshot:
        return
    with db.tx() as conn:
        conn.execute("DELETE FROM machine_key_requests")
        conn.execute("DELETE FROM machine_keys")
        for table, fields in [("machine_keys", (*PUBLIC_FIELDS, "key_hash")),
                              ("machine_key_requests", ("request_id", "key_id", "actor", "body_hash", "response",
                                                        "created_at"))]:
            for row in snapshot[table]:
                values = [jsonb(row[f]) if f in {"scopes", "response"} and row[f] is not None else row[f]
                          for f in fields]
                placeholders = ",".join(["%s"] * len(fields))
                conn.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES ({placeholders})", values)


class MachineKeyError(HTTPException):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(status, detail)
        self.code = code


class MachineKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(strict=True, description="Unieke naam na trimmen, 1–80 tekens zonder besturingstekens")
    scopes: list[str] = Field(strict=True, description="Expliciet gekozen: read en/of changes:approve",
                              json_schema_extra={"minItems": 1, "items": {"type": "string", "enum": sorted(SCOPES)}})


class MachineKeyView(BaseModel):
    id: uuid.UUID
    name: str
    scopes: list[str] | None
    last_four: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class MachineKeyList(BaseModel):
    items: list[MachineKeyView]


class MachineKeyCreated(BaseModel):
    item: MachineKeyView
    key: str = Field(description="Eenmalige waarde; wordt nooit opgeslagen of later teruggegeven")


class MachineKeyRevoked(BaseModel):
    item: MachineKeyView
    already_revoked: bool


class MachineKeyErrorView(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    trace_id: uuid.UUID
    detail: str
    correlation_id: str


def _size_error() -> JSONResponse:
    cid = uuid.uuid4().hex[:12]
    return JSONResponse({"detail": "Verzoek is groter dan 1 MiB", "code": "payload_too_large",
                         "message": "Verzoek is groter dan 1 MiB", "details": {"max_bytes": 1_048_576},
                         "trace_id": str(uuid.uuid4()), "correlation_id": cid}, status_code=413,
                        headers={"Connection": "close", "Cache-Control": "no-store", "X-Correlation-Id": cid})


# Nieuwe routes worden nooit automatisch geopend. Geen GET die importeert, synchroniseert,
# cache ververst, een geheim leest/maakt of een externe opdracht start.
READ_ROUTES = frozenset({
    "/api/v1/overview", "/api/v1/overview/goals", "/api/v1/overview/autopilot", "/api/v1/overview/waiting",
    "/api/v1/system/mode", "/api/v1/system/health", "/api/v1/system/info",
    "/api/v1/audit", "/api/v1/jobs", "/api/v1/jobs/{job_id}",
    "/api/v1/changes", "/api/v1/changes/{change_id}", "/api/v1/connectors",
    "/api/v1/automation/recommendations", "/api/v1/automation/status", "/api/v1/automation/hold",
    "/api/v1/automation/safety", "/api/v1/automation/schedules",
    "/api/v1/mediaguard/wanted", "/api/v1/mediaguard/compat", "/api/v1/mediaguard/kpis",
    "/api/v1/mediaguard/reasons", "/api/v1/mediaguard/search-requests",
    "/api/v1/usenet/failure-reasons", "/api/v1/usenet/sab/server-status",
    "/api/v1/usenet/providers/presets", "/api/v1/usenet/nzbs", "/api/v1/usenet/article-tests",
    "/api/v1/usenet/article-tests/{test_id}", "/api/v1/usenet/sab/reclassify",
    "/api/v1/usenet/sab/server-tests", "/api/v1/usenet/stat-budget",
    "/api/v1/search/sends", "/api/v1/torrent/clients",
})


def scopes_or_none(value: Any) -> frozenset[str] | None:
    if not isinstance(value, list) or not value or any(not isinstance(s, str) or s not in SCOPES for s in value):
        return None
    return frozenset(value)


def public(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in PUBLIC_FIELDS}
    out["scopes"] = sorted(scopes_or_none(row["scopes"]) or []) if row["scopes"] == [] or scopes_or_none(
        row["scopes"]) is not None else None
    return out


def _failed(request: HTTPConnection) -> None:
    from mso import settings_registry
    remote = request.client.host if request.client else "?"
    limit = int(settings_registry.get("security.login_rate_per_min"))
    # De bestaande inlogbucket blijft gedeeld binnen het API-proces. De extra
    # databaseteller handhaaft dezelfde grens ook tussen API en worker/processen.
    allowed, wait = limiter.hit("login-ip", remote, limit, 60)
    public_id = re.match(r"mso_mk_([0-9a-f]{32})_", request.headers.get("x-api-key", ""))
    identity = "machine:" + (public_id[1] if public_id else "unknown")
    allowed_id, wait_id = limiter.hit("login-user", identity, limit, 60)
    buckets = ["ip:" + hashlib.sha256(remote.encode()).hexdigest(),
               "key:" + hashlib.sha256(identity.encode()).hexdigest()]
    attempts, shared_wait = 0, 0.0
    with get_db().tx() as conn:
        conn.execute("DELETE FROM machine_auth_limits WHERE started_at < now() - interval '60 seconds'")
        for bucket in buckets:
            row = conn.execute(
                "INSERT INTO machine_auth_limits(bucket,started_at,attempts) VALUES (%s,now(),1) "
                "ON CONFLICT (bucket) DO UPDATE SET attempts=machine_auth_limits.attempts+1 "
                "RETURNING attempts, extract(epoch FROM started_at+interval '60 seconds'-now()) AS wait", (bucket,)
            ).fetchone()
            attempts = max(attempts, row["attempts"])
            shared_wait = max(shared_wait, float(row["wait"]))
    if not allowed or not allowed_id or attempts > limit:
        audit("machine", "machine_keys.rate_limited", "Machine-aanmelding tijdelijk begrensd.")
        raise HTTPException(429, "Te veel mislukte pogingen. Wacht even en probeer het opnieuw.",
                            headers={"Retry-After": str(max(1, math.ceil(max(wait, wait_id, shared_wait))))})
    audit("machine", "machine_keys.auth_failed", "Aanmelden met machine-sleutel geweigerd.")
    raise HTTPException(401, "Ongeldige of ingetrokken machine-sleutel")


def _read_key(key_id: str | uuid.UUID) -> dict[str, Any] | None:
    # Een herstel kan tussentijds oudere autorisatierijen terugzetten. Lezen
    # wacht nooit op dat herstel en gebruikt die tijdelijke toestand niet.
    with get_db().tx() as conn:
        if not conn.execute("SELECT pg_try_advisory_xact_lock_shared(%s) AS available", (ADMIN_LOCK,)).fetchone()[
                "available"]:
            raise HTTPException(403, "Machine-aanmelding is tijdelijk gesloten tijdens sleutelbeheer of herstel")
        return conn.execute("SELECT id,key_hash,scopes,revoked_at FROM machine_keys WHERE id=%s", (key_id,)).fetchone()


def authenticate(request: HTTPConnection) -> Principal:
    values = request.headers.getlist("x-api-key")
    raw = values[0] if len(values) == 1 else ""
    match = KEY_RE.fullmatch(raw)
    row = _read_key(uuid.UUID(match[1])) if match else None
    expected = row["key_hash"] if row else "0" * 64
    good = hmac.compare_digest(token_hash(raw), expected)
    if not good or row is None or row["revoked_at"] is not None:
        _failed(request)
    return Principal(username=f"machine:{row['id']}", role=None, machine=True,
                     machine_key_id=str(row["id"]), scopes=scopes_or_none(row["scopes"]) or frozenset())


def authorize(request: HTTPConnection, principal: Principal) -> None:
    route = request.scope.get("machine_route")
    if (request.scope["type"] != "http" or request.scope.get("method") != "GET"
            or route is None or route.path not in READ_ROUTES or "read" not in principal.scopes
            or any(k.lower() == "force" for k in request.query_params)):
        raise HTTPException(403, "Deze route is niet toegestaan met de scopes van de machine-sleutel")


def check_active(key_id: str, scopes: frozenset[str]) -> bool:
    try:
        row = _read_key(key_id)
    except HTTPException:
        return False
    return bool(row and row["revoked_at"] is None and scopes_or_none(row["scopes"]) == scopes)


def touch(key_id: str) -> None:
    """Telemetrie is best effort: geen wachtrij op een bezette pool of sleutelrij."""
    try:
        with get_db().pool.connection(timeout=0.01) as conn, conn.transaction():
            conn.execute("SET LOCAL statement_timeout='25ms'")
            conn.execute("SET LOCAL lock_timeout='1ms'")
            conn.execute(
                "WITH available AS (SELECT id FROM machine_keys WHERE id=%s AND revoked_at IS NULL "
                "FOR UPDATE SKIP LOCKED) UPDATE machine_keys SET last_used_at=now() "
                "WHERE id IN (SELECT id FROM available)", (key_id,))
    except Exception:
        return  # Een ontbrekende gebruikstijd trekt geen toestemming in en maakt lezen niet rood.


class MachineKeyMiddleware:
    """Alle ingangen, inclusief publieke routes, onbekende paden en websockets."""

    def __init__(self, app: Any, routes: list[Any]) -> None:
        self.app, self.routes = app, routes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/v1/machine-keys"):
            # Nieuwe beheerroutes: contractgrens vóór auth/validatie, ook chunked.
            declared = Headers(scope=scope).get("content-length", "0")
            significant = declared.lstrip("0")
            if declared.isdecimal() and (len(significant) > 7 or len(significant) == 7 and significant > "1048576"):
                await _size_error()(scope, receive, send)
                return
            chunks = []
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > 1_048_576:
                    await _size_error()(scope, receive, send)
                    return
                chunks.append(chunk)
                if not message.get("more_body"):
                    break
            original_receive = receive
            delivered = False

            async def replay() -> Any:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await original_receive()

            receive = replay
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        if not has_machine_header(connection):
            await self.app(scope, receive, send)
            return

        async def reject(exc: HTTPException) -> None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            from mso.app import _error
            code = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}.get(exc.status_code, "error")
            response = _error(exc.status_code, exc.detail, code, connection)
            response.headers.update({"Cache-Control": "no-store", **(exc.headers or {})})
            await response(scope, receive, send)

        try:
            principal = await run_in_threadpool(authenticate, connection)
            # Routerherkenning vóór dependencies en bodyvalidatie. Een nieuwe alias krijgt
            # geen rechten via dezelfde endpointfunctie.
            for branch in self.routes:
                candidates = (branch.effective_route_contexts()
                              if hasattr(branch, "effective_route_contexts") else [branch])
                for route in candidates:
                    match, _ = route.matches(scope)
                    if match is Match.FULL:
                        scope["machine_route"] = route
                        break
                if "machine_route" in scope:
                    break
            authorize(connection, principal)
        except HTTPException as exc:
            await reject(exc)
            return

        rejected = False

        async def guarded_send(message: Any) -> None:
            nonlocal rejected
            if message["type"] == "http.response.start":
                if message["status"] < 400:
                    await run_in_threadpool(touch, principal.machine_key_id)
                if not await run_in_threadpool(check_active, principal.machine_key_id, principal.scopes):
                    rejected = True
                    await reject(HTTPException(401, "Ongeldige of ingetrokken machine-sleutel"))
                    return
            if not rejected:
                await send(message)

        await self.app(scope, receive, guarded_send)


router = APIRouter(prefix="/api/v1/machine-keys", tags=["Machine-sleutels"], responses={
    code: {"model": MachineKeyErrorView} for code in [400, 401, 403, 404, 409, 413, 422, 429, 500]})


def admin_only(p: Principal = Depends(current_principal)) -> Principal:
    if p.machine or not p.can("admin"):
        raise MachineKeyError(403, "admin_required", "Alleen beheerders kunnen machine-sleutels beheren.")
    return p


@router.get("", response_model=MachineKeyList)
def list_keys(p: Principal = Depends(admin_only)) -> dict[str, Any]:
    p = admin_only(p)
    rows = get_db().fetchall("SELECT * FROM machine_keys ORDER BY created_at DESC,id")
    audit(p.actor, "machine_keys.list", "Machine-sleutellijst bekeken.")
    return {"items": [public(row) for row in rows]}


@router.post("", response_model=MachineKeyCreated)
def create_key(body: MachineKeyCreate, p: Principal = Depends(admin_only)) -> dict[str, Any]:
    p = admin_only(p)
    name, scopes = body.name, body.scopes
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(
            unicodedata.category(c).startswith("C") for c in name):
        raise MachineKeyError(422, "invalid_name", "Kies een naam van 1 tot 80 tekens zonder besturingstekens.")
    if scopes == []:
        raise MachineKeyError(422, "scope_required", "Kies minstens één recht (scope).")
    if scopes_or_none(scopes) is None:
        raise MachineKeyError(422, "unknown_scope", "Onbekend recht (scope). Toegestaan: read, changes:approve.")
    key_id = uuid.uuid4()
    key = f"mso_mk_{key_id.hex}_{secrets.token_urlsafe(32)}"
    try:
        with get_db().tx() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADMIN_LOCK,))
            row = conn.execute(
                "INSERT INTO machine_keys(id,name,key_hash,last_four,scopes) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (key_id, name.strip(), token_hash(key), key[-4:], jsonb(sorted(set(scopes))))).fetchone()
            # Audit en aanmaak delen één transactie; geen sleutel of hash aangeboden aan audit.
            conn.execute("INSERT INTO audit_log(actor,action,target,summary,detail) VALUES (%s,%s,%s,%s,%s)",
                         (p.actor, "machine_keys.create", str(key_id), "Machine-sleutel aangemaakt.", jsonb({})))
    except psycopg.errors.UniqueViolation:
        raise MachineKeyError(409, "duplicate_name",
                              "Er bestaat al een machine-sleutel met deze naam. Kies een andere naam.") from None
    return {"item": jsonable_encoder(public(row)), "key": key}


@router.post("/{key_id}/revoke", response_model=MachineKeyRevoked)
def revoke_key(key_id: uuid.UUID, p: Principal = Depends(admin_only), response: Response = None,
               idempotency_key: str = Header(..., alias="Idempotency-Key", json_schema_extra={"format": "uuid"}),
               body: Any = Body(default=None)) -> dict[str, Any]:
    p = admin_only(p)
    try:
        request_id = uuid.UUID(idempotency_key)
    except (ValueError, TypeError, AttributeError):
        raise MachineKeyError(400, "idempotency_key_invalid", "Idempotency-Key moet een UUID zijn") from None
    body_hash = token_hash(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    with get_db().tx() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADMIN_LOCK,))
        old = conn.execute("SELECT * FROM machine_keys WHERE id=%s FOR UPDATE", (key_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Machine-sleutel niet gevonden")
        conn.execute("INSERT INTO machine_key_requests(request_id,key_id,actor,body_hash) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (request_id) DO NOTHING", (request_id, key_id, p.actor, body_hash))
        saved = conn.execute("SELECT * FROM machine_key_requests WHERE request_id=%s", (request_id,)).fetchone()
        if saved["key_id"] != key_id or saved["actor"] != p.actor or saved["body_hash"] != body_hash:
            raise MachineKeyError(422, "idempotency_key_reused", "Deze Idempotency-Key hoort bij een ander verzoek")
        if saved["response"] is not None:
            if response is not None:
                response.headers["Idempotent-Replay"] = "true"
            return saved["response"]
        row = conn.execute("UPDATE machine_keys SET revoked_at=coalesce(revoked_at,now()) WHERE id=%s RETURNING *",
                           (key_id,)).fetchone()
        conn.execute("INSERT INTO audit_log(actor,action,target,summary,detail) VALUES (%s,%s,%s,%s,%s)",
                     (p.actor, "machine_keys.revoke", str(key_id), "Machine-sleutel ingetrokken.", jsonb({})))
        result = {"item": public(row), "already_revoked": old["revoked_at"] is not None}
        conn.execute("UPDATE machine_key_requests SET response=%s WHERE request_id=%s", (jsonb(result), request_id))
    return result
