"""FastAPI application factory.

Feature modules are auto-discovered: every module in ``mso.connectors`` and every package/module in
``mso.features`` is imported (registering connectors, settings, job handlers, appliers); any module
attribute ``router`` (or list ``routers``) is mounted. Feature code never needs to edit this file.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection

from mso import settings_registry
from mso.config import VERSION, get_settings
from mso.connector_api.errors import ConnectorRequestMiddleware, ErrorBody, error_response, is_connector
from mso.connector_api.status import Readiness, readiness
from mso.connectors import base as connectors
from mso.db import Database, check_schema_compatibility, get_db, migrate_up, set_db, upgrade_lock
from mso.security import configure_logging, limiter, redact
from mso.vault import Vault, set_vault

LOGGER = logging.getLogger(__name__)
CORE_MODULES = ["mso.api.foundation", "mso.backups", "mso.retention", "mso.machine_keys", "mso.connector_api.router"]


def _allowed(name: str) -> bool:
    """``MSO_FEATURES`` (comma list of module-name prefixes) limits discovery — used to test one feature in
    isolation. Unset = load everything (production)."""
    allow = [p.strip() for p in os.environ.get("MSO_FEATURES", "").split(",") if p.strip()]
    if not allow or name == "mso.connectors.base":
        return True
    return any(name.startswith(p) or name.startswith(f"mso.connectors.{p}") or name.startswith(f"mso.features.{p}")
               for p in allow)


def iter_feature_modules() -> Iterator[ModuleType]:
    for name in CORE_MODULES:
        yield importlib.import_module(name)
    for pkg_name in ("mso.connectors", "mso.features"):
        try:
            pkg = importlib.import_module(pkg_name)
        except ModuleNotFoundError:
            continue
        for info in sorted(pkgutil.walk_packages(pkg.__path__, pkg_name + "."), key=lambda i: i.name):
            if _allowed(info.name):
                yield importlib.import_module(info.name)


def load_modules() -> list[ModuleType]:
    return list(iter_feature_modules())


def init_runtime(db: Database | None = None, *, migrate: bool = True) -> Database:
    """Shared by API and worker: DB pool, migrations, vault, connector rows.

    Pending migrations get a ``pre-upgrade`` backup first (only when there is something to keep and enough free
    space; never blocks the start)."""
    settings = get_settings()
    own_db = db is None
    db = db or Database(settings.database_url)
    try:
        check_schema_compatibility(db)
        with upgrade_lock(db):
            check_schema_compatibility(db)
            set_db(db)
            if migrate:
                from mso import backups

                backups.pre_upgrade_backup(db)
                migrate_up(db)
                backups.recover_machine_keys_on_start(db)
                try:
                    backups.flush_pre_upgrade(db)
                except Exception as exc:
                    LOGGER.warning("audit van de back-up vóór de upgrade niet geschreven: %s", type(exc).__name__)
            set_vault(Vault(db))
            load_modules()
            connectors.ensure_rows()
            settings_registry.refresh_api_rate_limit(db)
    except Exception:
        if own_db:
            db.close()
        raise
    return db


def _error(status: int, detail: Any, code: str, request: HTTPConnection, *,
           safe_details: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    from mso.machine_keys import has_machine_header

    if is_connector(request.url.path):
        if status == 401:
            safe_details = {"reason": "invalid_token"}
        if status == 403:
            safe_details = {"reason": "not_allowed"}
        safe_headers = {key: value for key, value in (headers or {}).items()
                        if key.lower() in {"allow", "retry-after"}}
        return error_response(status, code, safe_details, safe_headers)
    cid = getattr(request.state, "correlation_id", None) or uuid.uuid4().hex[:12]
    body = {"detail": detail, "code": code, "correlation_id": cid}
    if has_machine_header(request) or request.url.path.startswith("/api/v1/machine-keys"):
        body.update(message=detail, details={"reason": code} if status in {401, 403} else {},
                    trace_id=str(uuid.uuid4()))
    response_headers = {"X-Correlation-Id": cid}
    if status in {429, 503} and headers and "Retry-After" in headers:
        response_headers["Retry-After"] = headers["Retry-After"]
    return JSONResponse(body, status_code=status, headers=response_headers)


class GuardMiddleware(BaseHTTPMiddleware):
    """Correlation id, security headers and per-client API rate limit."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        from mso.machine_keys import has_machine_header

        path = request.url.path
        connector = is_connector(path)
        public_probe = path in {"/readyz", "/healthz"}
        client_cid = "" if connector or public_probe else request.headers.get("X-Correlation-Id", "")[:32]
        request.state.correlation_id = client_cid or uuid.uuid4().hex[:12]
        machine = has_machine_header(request)
        native_api = path.startswith("/api/") and path not in ("/api/v1/auth/login", "/api/v1/setup/admin")
        if machine or connector or native_api:
            remote = request.client.host if request.client else "?"
            per_min = settings_registry.machine_api_rate_limit(get_db())
            bucket = "connector" if connector else "api"
            ok, wait = limiter.hit(bucket, remote, per_min, 60)
            if not ok:
                seconds = int(wait) + 1
                response = _error(429, f"Te veel verzoeken; wacht {seconds} s", "rate_limited", request)
                if machine or connector or path == "/api/v1/machine-keys" or path.startswith("/api/v1/machine-keys/"):
                    response.headers["Retry-After"] = str(seconds)
                return response
            request.state.user_rate_limit = (bucket + ".user", per_min)
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


def create_app(db: Database | None = None, *, migrate: bool = True) -> FastAPI:
    configure_logging()
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        if db is None:
            get_db().close()

    init_runtime(db, migrate=migrate)
    app = FastAPI(title="Media Source Optimizer", version=VERSION, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Content-Type", "X-CSRF-Token"])
    from mso.machine_keys import MachineKeyMiddleware
    app.add_middleware(MachineKeyMiddleware, routes=app.router.routes)
    app.add_middleware(GuardMiddleware)
    app.add_middleware(ConnectorRequestMiddleware)

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid",
                429: "rate_limited", 502: "upstream_error"}.get(exc.status_code, "error")
        return _error(exc.status_code, exc.detail, getattr(exc, "code", code), request,
                      safe_details=getattr(exc, "safe_details", None), headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def routing_exc(request: Request, exc: StarletteHTTPException):
        if is_connector(request.url.path):
            return _error(exc.status_code, None, "", request, headers=exc.headers)
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError) -> JSONResponse:
        if is_connector(request.url.path):
            fields = {"cursor", "limit", "type", "id"}
            errors = [{"veld": part, "reden": "ongeldig"} for part in sorted({
                part for error in exc.errors() for part in error["loc"]
                if isinstance(part, str) and part in fields
            })]
            if any(e["type"] == "extra_forbidden" for e in exc.errors()):
                area = "body" if any(e["loc"][0] == "body" for e in exc.errors()) else "query"
                errors.append({"veld": area, "reden": "onbekend veld"})
            return _error(422, None, "validation_error", request, safe_details={"fouten": errors})
        if request.url.path.startswith("/api/v1/machine-keys"):
            if any("idempotency-key" in str(e.get("loc", ())).lower() for e in exc.errors()):
                return _error(400, "Idempotency-Key ontbreekt of is ongeldig", "idempotency_key_required", request)
            scope_error = any("scopes" in e.get("loc", ()) for e in exc.errors())
            code, detail = (("unknown_scope", "Onbekend recht (scope). Toegestaan: read, changes:approve.")
                            if scope_error else ("invalid_name",
                                                 "Kies een naam van 1 tot 80 tekens zonder besturingstekens."))
            return _error(422, detail, code, request)
        errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return _error(422, errors, "invalid", request)

    @app.exception_handler(connectors.ConnectorNotConfigured)
    async def not_configured(request: Request, exc: connectors.ConnectorNotConfigured) -> JSONResponse:
        return _error(409, str(exc), "connector_not_configured", request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.error("onverwachte fout op %s: %s", request.url.path, redact(f"{type(exc).__name__}: {exc}"))
        return _error(500, "Interne fout; zie logboek met correlation-id", "internal", request)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        ok = get_db().ping()
        if not ok:
            raise HTTPException(503, detail="database onbereikbaar")
        return {"status": "ok", "version": VERSION}

    @app.get("/readyz", responses={413: {"model": ErrorBody, "description": "payload_too_large"},
                                   503: {"model": Readiness, "description": "Eigen afhankelijkheid onbeschikbaar"}})
    def readyz(response: Response) -> Readiness:
        result = readiness()
        if result.status != "ready":
            response.status_code = 503
            response.headers["Retry-After"] = "1"
        return result

    for module in load_modules():
        routers = list(getattr(module, "routers", []) or [])
        if getattr(module, "router", None) is not None:
            routers.append(module.router)
        for r in routers:
            app.include_router(r)
    original_openapi = app.openapi

    def machine_openapi() -> dict[str, Any]:
        from mso.machine_keys import CHANGE_WRITE_ROUTES, IDEMPOTENCY_EXCEPTIONS, READ_ROUTES
        schema = original_openapi()
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["MachineKey"] = {"type": "apiKey", "in": "header", "name": "X-Api-Key",
                                 "description": "Rolvrije machine-sleutel. Alleen expliciet geopende scopes; "
                                 "changes:approve opent uitsluitend approve en apply van change sets."}
        schemes["SessionCookie"] = {"type": "apiKey", "in": "cookie", "name": "mso_session"}
        for path, operations in schema["paths"].items():
            if path in READ_ROUTES and "get" in operations:
                operations["get"]["security"] = [{"SessionCookie": []}, {"MachineKey": []}]
                operations["get"]["x-machine-scopes"] = ["read"]
            if path in CHANGE_WRITE_ROUTES and "post" in operations:
                operation = operations["post"]
                operation["security"] = [{"SessionCookie": []}, {"MachineKey": []}]
                operation["x-machine-scopes"] = ["changes:approve"]
                operation["description"] = (
                    "Voor machine-sleutels zijn expected_preview_hash en Idempotency-Key verplicht.")
            if is_connector(path) and "post" in operations:
                operation = operations["post"]
                operation["security"] = [{"SessionCookie": []}]
                if ("post", path) in IDEMPOTENCY_EXCEPTIONS:
                    operation["x-idempotency-exception"] = IDEMPOTENCY_EXCEPTIONS[("post", path)]
                else:
                    operation["security"] = []
                    operation["description"] = (
                        "Authenticatie via de Ed25519-handtekening van ExecutionRequest "
                        "met een gepinde beleidssleutel.")
                    for parameter in operation["parameters"]:
                        if parameter["name"] == "Idempotency-Key":
                            parameter["required"] = True
            if path.startswith("/api/v1/machine-keys"):
                for method, operation in operations.items():
                    operation["security"] = [{"SessionCookie": []}]
                    if (method, path) in IDEMPOTENCY_EXCEPTIONS:
                        operation["x-idempotency-exception"] = IDEMPOTENCY_EXCEPTIONS[(method, path)]
                    for code, description in {
                            400: "idempotency_key_required of idempotency_key_invalid",
                            401: "Niet ingelogd", 403: "admin_required: alleen menselijke beheerder",
                            409: "duplicate_name: naam bestaat al",
                            413: "payload_too_large: maximaal 1 MiB",
                            422: "invalid_name, scope_required, unknown_scope of idempotency_key_reused",
                            429: "rate_limited: wacht volgens Retry-After"}.items():
                        operation["responses"].setdefault(str(code), {})["description"] = description
        return schema

    app.openapi = machine_openapi
    return app
