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

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mso import settings_registry
from mso.config import VERSION, get_settings
from mso.connectors import base as connectors
from mso.db import Database, get_db, migrate_up, set_db
from mso.security import configure_logging, limiter, redact
from mso.vault import Vault, set_vault

LOGGER = logging.getLogger(__name__)
CORE_MODULES = ["mso.api.foundation", "mso.backups"]


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
    """Shared by API and worker: DB pool, migrations, vault, connector rows."""
    settings = get_settings()
    db = db or Database(settings.database_url)
    set_db(db)
    if migrate:
        migrate_up(db)
    set_vault(Vault(db))
    load_modules()
    connectors.ensure_rows()
    return db


def _error(status: int, detail: Any, code: str, request: Request) -> JSONResponse:
    cid = getattr(request.state, "correlation_id", None) or uuid.uuid4().hex[:12]
    return JSONResponse({"detail": detail, "code": code, "correlation_id": cid}, status_code=status,
                        headers={"X-Correlation-Id": cid})


class GuardMiddleware(BaseHTTPMiddleware):
    """Correlation id, security headers and per-client API rate limit."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.correlation_id = request.headers.get("X-Correlation-Id", "")[:32] or uuid.uuid4().hex[:12]
        path = request.url.path
        if path.startswith("/api/") and path not in ("/api/v1/auth/login", "/api/v1/setup/admin"):
            key = request.cookies.get("mso_session") or (request.client.host if request.client else "?")
            try:
                per_min = int(settings_registry.get("security.api_rate_per_min"))
            except Exception:
                per_min = 600
            ok, wait = limiter.hit("api", key, per_min, 60)
            if not ok:
                return _error(429, f"Te veel verzoeken; wacht {int(wait) + 1} s", "rate_limited", request)
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
    app.add_middleware(GuardMiddleware)

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid",
                429: "rate_limited", 502: "upstream_error"}.get(exc.status_code, "error")
        return _error(exc.status_code, exc.detail, code, request)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError) -> JSONResponse:
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

    for module in load_modules():
        routers = list(getattr(module, "routers", []) or [])
        if getattr(module, "router", None) is not None:
            routers.append(module.router)
        for r in routers:
            app.include_router(r)
    return app
