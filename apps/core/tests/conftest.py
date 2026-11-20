"""Test harness.

- A fresh PostgreSQL database per test session (``MSO_TEST_DATABASE_URL`` points at a server; default the
  local test cluster on 127.0.0.1:55432). Tables are truncated between tests (fast isolation).
- ``client`` = anonymous TestClient; ``admin``/``operator``/``viewer`` = logged-in clients that send CSRF.
- ``serve`` runs any ASGI app (e.g. a mock Prowlarr) on a real local port and returns its base URL, so
  connectors are exercised over real HTTP exactly like in the container stack.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import uvicorn
from fastapi.testclient import TestClient
from psycopg import sql

os.environ.setdefault("MSO_ALLOW_LOOPBACK", "1")
# mock services (fake Prowlarr/SABnzbd/qBittorrent/MediaGuard/NNTP/indexers) live in apps/mocks/msomocks
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mocks"))
os.environ.setdefault("MSO_TESTING", "1")

BASE_URL = os.environ.get("MSO_TEST_DATABASE_URL", "postgresql://mso@127.0.0.1:55432/postgres")
KEEP_TABLES = {"schema_migrations", "system_state", "ai_model_prices", "download_receipt_origin"}
USER_FIXTURE_PASSWORD = "Wachtwoord123"  # gitleaks:allow — synthetische inlogfixture voor wegwerpdatabases


@pytest.fixture()
def confirmed_search_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beproef het schrijfpad aan de hand van een fixture met bekende zoekroute."""
    from mso.connectors import mediaguard

    probe = mediaguard.probe

    def confirmed(cfg: Any) -> dict:
        result = probe(cfg)
        result["capabilities"]["wanted_search"] = True
        return result

    monkeypatch.setattr(mediaguard, "probe", confirmed)


@pytest.fixture()
def database_bytes() -> Callable[[Any], dict[str, bytes]]:
    """Vaste byteweergave van alle publieke tabelrijen, inclusief migratietijden en controlesommen."""
    def snapshot(db: Any) -> dict[str, bytes]:
        tables = db.fetchall("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        result = {}
        for table in tables:
            name = table["tablename"]
            rows = db.fetchall(sql.SQL("SELECT to_jsonb(t) AS row FROM {} AS t ORDER BY to_jsonb(t)::text")
                               .format(sql.Identifier("public", name)))
            result[name] = json.dumps([r["row"] for r in rows], sort_keys=True, ensure_ascii=False).encode()
        return result

    return snapshot


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    name = f"mso_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(BASE_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = BASE_URL.rsplit("/", 1)[0] + "/" + name
    yield url
    with psycopg.connect(BASE_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def runtime(database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    from mso import config
    from mso.app import create_app

    data = tmp_path_factory.mktemp("appdata")
    work = tmp_path_factory.mktemp("work")
    config.set_settings(config.Settings(database_url=database_url, data_dir=Path(data), work_dir=Path(work),
                                        allow_loopback_targets=True, testing=True))
    app = create_app()
    yield app


def _truncate() -> None:
    from mso import settings_registry
    from mso.db import get_db
    db = get_db()
    tables = [r["tablename"] for r in db.fetchall("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
    tables = [t for t in tables if t not in KEEP_TABLES]
    if tables:
        db.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
    db.execute("UPDATE system_state SET value='\"read_only\"' WHERE key='mode'")
    db.execute("UPDATE system_state SET value='false' WHERE key IN ('paused','setup_completed')")
    db.execute("DELETE FROM system_state WHERE key NOT IN ('mode','paused','setup_completed')")
    from mso.connectors import base as connectors
    connectors.ensure_rows()
    settings_registry.refresh_api_rate_limit(db)


@pytest.fixture()
def app(runtime: Any) -> Iterator[Any]:
    from mso.security import limiter
    _truncate()
    limiter.reset()
    yield runtime


@pytest.fixture()
def db(app: Any) -> Any:
    from mso.db import get_db
    return get_db()


@pytest.fixture()
def client(app: Any) -> TestClient:
    return TestClient(app)


class AuthedClient(TestClient):
    csrf: str = ""

    def request(self, method: str, url: str, **kw: Any):  # type: ignore[override]
        headers = dict(kw.pop("headers", None) or {})
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return super().request(method, url, headers=headers, **kw)


def make_user(app: Any, username: str, role: str, password: str = USER_FIXTURE_PASSWORD) -> AuthedClient:
    from mso.auth import hash_password
    from mso.db import get_db
    get_db().execute("INSERT INTO users(username,password_hash,role) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                     (username, hash_password(password), role))
    c = AuthedClient(app)
    r = c.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.csrf = r.json()["csrf_token"]
    return c


@pytest.fixture()
def admin(app: Any) -> AuthedClient:
    return make_user(app, "beheerder", "admin")


@pytest.fixture()
def operator(app: Any) -> AuthedClient:
    return make_user(app, "operator", "operator")


@pytest.fixture()
def viewer(app: Any) -> AuthedClient:
    return make_user(app, "kijker", "viewer")


@pytest.fixture()
def manage_mode(app: Any) -> None:
    from mso import state
    state.set_state("mode", "manage")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def serve() -> Iterator[Callable[[Any], str]]:
    """Start lokale HTTP-mocks en sluit hun luisterpoorten en threads na deze test."""
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(asgi_app: Any) -> str:
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(asgi_app, host="127.0.0.1", port=port, log_level="warning",
                                               lifespan="off"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        for _ in range(200):
            if server.started or not thread.is_alive():
                break
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("De lokale HTTP-mock is niet gestart.")
        return f"http://127.0.0.1:{port}"

    try:
        yield start
    finally:
        for server, _ in servers:
            server.should_exit = True
        for _, thread in servers:
            thread.join(timeout=15)
            assert not thread.is_alive(), "De lokale HTTP-mock is niet gestopt."


@pytest.fixture()
def worker(app: Any) -> Any:
    from mso import jobs
    from mso.db import get_db
    return jobs.Worker("test-worker", get_db(), 0.01)
