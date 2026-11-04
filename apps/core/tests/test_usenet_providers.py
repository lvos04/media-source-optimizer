"""Usenet providers over a real (fake) NNTP server: status classification (expired, auth denied, too many
connections, block exhausted, unreachable, timeout), connection limits, CRUD/RBAC, secrets never exposed."""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator
from typing import Any

import pytest

PASSWORD = "NNTP-WACHTWOORD-geheim-42"
USERNAME = "mso-gebruiker"


@pytest.fixture()
def nntp_factory() -> Iterator[Any]:
    from msomocks import nntp

    servers: list[Any] = []

    def start(**faults: Any) -> Any:
        faults.setdefault("username", USERNAME)
        faults.setdefault("password", PASSWORD)
        s = nntp.start(**faults)
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.stop()


def _add(admin: Any, slug: str, port: int, **extra: Any) -> dict[str, Any]:
    body = {
        "slug": slug,
        "label": slug.title(),
        "host": "127.0.0.1",
        "port": port,
        "ssl": False,
        "connections": 8,
        "username": USERNAME,
        "password": PASSWORD,
    } | extra
    r = admin.post("/api/v1/usenet/providers", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    "mode,status",
    [
        ("ok", "ok"),
        ("expired", "account_expired"),
        ("auth_denied", "auth_failed"),
        ("too_many", "rate_limited"),
        ("block_exhausted", "block_exhausted"),
        ("greeting_502", "auth_failed"),
    ],
)
def test_status_classification(admin: Any, operator: Any, nntp_factory: Any, mode: str, status: str) -> None:
    srv = nntp_factory(mode=mode)
    _add(admin, "prov", srv.port)
    r = operator.post("/api/v1/usenet/providers/prov/test")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == status, res
    assert res["provider"]["status"] == status and res["provider"]["last_check_at"]
    if status == "ok":
        assert "READER" in res["capabilities"] and res["provider"]["last_ok_at"]
    assert PASSWORD not in r.text


def test_wrong_password_is_auth_failed(admin: Any, operator: Any, nntp_factory: Any) -> None:
    srv = nntp_factory()
    _add(admin, "prov", srv.port, password="fout")
    assert operator.post("/api/v1/usenet/providers/prov/test").json()["status"] == "auth_failed"


def test_missing_credentials_is_not_a_refused_login(admin: Any, operator: Any, db: Any, nntp_factory: Any) -> None:
    srv = nntp_factory()
    created = _add(admin, "prov", srv.port, username=None, password=None)
    assert created["status"] == "missing_credentials" and created["credentials_complete"] is False
    assert created["health"]["status"] == "missing_credentials"
    res = operator.post("/api/v1/usenet/providers/prov/test").json()
    assert res["status"] == "missing_credentials" and res["tested"] is False
    assert "Gebruikersnaam/wachtwoord" in res["message"] or "gebruikersnaam/wachtwoord" in res["message"]
    assert res["provider"]["status"] == "missing_credentials"
    assert srv.state.commands == []  # MSO never connected
    assert db.scalar("SELECT count(*) FROM observations WHERE metric='nntp.check_ok'") == 0
    # filling in credentials makes it testable again
    r = admin.put("/api/v1/usenet/providers/prov", json={"username": USERNAME, "password": PASSWORD})
    assert r.json()["status"] == "unknown" and r.json()["credentials_complete"] is True
    assert operator.post("/api/v1/usenet/providers/prov/test").json()["status"] == "ok"
    # clearing them again → missing, not auth_failed
    r = admin.put("/api/v1/usenet/providers/prov", json={"password": ""})
    assert r.json()["status"] == "missing_credentials"


def test_periodic_checks_skip_providers_without_credentials(
    app: Any, admin: Any, operator: Any, worker: Any, nntp_factory: Any
) -> None:
    from mso import jobs
    from mso.features.usenet import providers

    srv = nntp_factory(mode="expired")
    _add(admin, "met", srv.port)
    for slug in ("zonder1", "zonder2"):
        _add(admin, slug, srv.port, username=None, password=None)
    assert providers.due_for_check() == ["met"]
    jobs.enqueue("usenet.provider_checks", {"slugs": ["met", "zonder1", "zonder2"]})
    worker.drain()
    rows = {r["slug"]: r for r in admin.get("/api/v1/usenet/providers").json()["items"]}
    assert rows["met"]["status"] == "account_expired"
    assert rows["zonder1"]["status"] == rows["zonder2"]["status"] == "missing_credentials"
    # the Test button only touches the provider it belongs to
    before = {s: r["last_check_at"] for s, r in rows.items()}
    operator.post("/api/v1/usenet/providers/zonder1/test")
    rows2 = {r["slug"]: r for r in admin.get("/api/v1/usenet/providers").json()["items"]}
    assert {s: r["last_check_at"] for s, r in rows2.items()} == before
    assert rows2["met"]["status"] == "account_expired" and rows2["zonder2"]["status"] == "missing_credentials"


def test_unreachable(admin: Any, operator: Any) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _add(admin, "dicht", port)
    assert operator.post("/api/v1/usenet/providers/dicht/test").json()["status"] == "unreachable"


def test_tls_against_plain_server_is_tls_error(admin: Any, operator: Any, nntp_factory: Any) -> None:
    srv = nntp_factory()
    _add(admin, "tls", srv.port, ssl=True)
    assert operator.post("/api/v1/usenet/providers/tls/test").json()["status"] in ("tls_error", "timeout")


def test_timeout(app: Any, admin: Any, operator: Any, nntp_factory: Any) -> None:
    from mso import settings_registry as sr

    sr.put({"usenet.nntp_timeout_s": 3}, "test")
    srv = nntp_factory(slow=4)
    _add(admin, "traag", srv.port)
    res = operator.post("/api/v1/usenet/providers/traag/test").json()
    assert res["status"] == "timeout"


def test_never_more_connections_than_allowed(app: Any, admin: Any, nntp_factory: Any) -> None:
    import threading

    from mso.features.usenet import providers

    srv = nntp_factory(slow=0.3)
    _add(admin, "limiet", srv.port, connections=1)
    threads = [threading.Thread(target=providers.check, args=("limiet",)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert srv.state.peak == 1
    assert providers.effective_connections(1) == 1 and providers.effective_connections(50) == 1


def test_secrets_and_hosts_never_logged(
    app: Any,
    admin: Any,
    operator: Any,
    viewer: Any,
    db: Any,
    nntp_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    srv = nntp_factory(mode="auth_denied")
    created = _add(admin, "geheim", srv.port)
    assert created["credentials"] == {"username": "ingesteld", "password": "ingesteld"}
    operator.post("/api/v1/usenet/providers/geheim/test")
    for client in (admin, viewer):
        body = client.get("/api/v1/usenet/providers").text
        assert PASSWORD not in body and USERNAME not in body
    audit = admin.get("/api/v1/audit").text
    assert PASSWORD not in audit and USERNAME not in audit and "127.0.0.1" not in audit
    assert PASSWORD not in caplog.text and USERNAME not in caplog.text and "127.0.0.1" not in caplog.text
    raw = db.fetchone("SELECT ciphertext FROM secrets WHERE name='provider.geheim.password'")
    assert PASSWORD.encode() not in bytes(raw["ciphertext"])
    assert "AUTHINFO PASS ***" in srv.state.commands
    # source mirror
    src = db.fetchone("SELECT * FROM sources WHERE id='provider:geheim'")
    assert src["kind"] == "usenet_provider" and src["origin"] == "mso" and "127.0.0.1" not in str(src["meta"])


def test_crud_roles_and_block_mirror(admin: Any, operator: Any, viewer: Any, db: Any) -> None:
    body = {
        "slug": "blok1",
        "label": "Blok 1",
        "kind": "block",
        "host": "127.0.0.1",
        "port": 563,
        "block_size_gb": 500,
    }
    assert viewer.post("/api/v1/usenet/providers", json=body).status_code == 403
    assert operator.post("/api/v1/usenet/providers", json=body).status_code == 403
    r = admin.post("/api/v1/usenet/providers", json=body)
    assert r.status_code == 200 and r.json()["source_id"] == "block:blok1"
    assert r.json()["credentials"] == {"username": "leeg", "password": "leeg"}
    assert db.fetchone("SELECT kind FROM sources WHERE id='block:blok1'")["kind"] == "block_account"
    assert admin.post("/api/v1/usenet/providers", json=body).status_code == 409
    assert admin.post("/api/v1/usenet/providers", json=body | {"slug": "Fout Slug"}).status_code == 422
    assert (
        admin.post(
            "/api/v1/usenet/providers", json=body | {"slug": "meta", "host": "169.254.169.254"}
        ).status_code
        == 422
    )
    r = admin.put("/api/v1/usenet/providers/blok1", json={"kind": "unlimited", "password": "nieuw"})
    assert r.status_code == 200 and r.json()["source_id"] == "provider:blok1"
    assert r.json()["credentials"]["password"] == "ingesteld"
    assert db.fetchone("SELECT enabled FROM sources WHERE id='block:blok1'")["enabled"] is False
    assert admin.delete("/api/v1/usenet/providers/blok1").status_code == 200
    assert db.scalar("SELECT count(*) FROM secrets WHERE name LIKE 'provider.blok1.%'") == 0
    assert viewer.get("/api/v1/usenet/providers").json()["items"] == []
    assert len(viewer.get("/api/v1/usenet/providers/presets").json()["items"]) == 3


def test_periodic_checks_job(app: Any, admin: Any, worker: Any, nntp_factory: Any) -> None:
    from mso import jobs

    srv = nntp_factory(mode="expired")
    _add(admin, "periodiek", srv.port)
    jobs.enqueue("usenet.provider_checks", {})
    worker.drain()
    row = admin.get("/api/v1/usenet/providers").json()["items"][0]
    assert row["status"] == "account_expired"
    # not due again within the interval
    from mso.features.usenet import providers

    assert providers.due_for_check() == []


def test_import_servers_from_sab(app: Any, serve: Any, admin: Any) -> None:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key="k")
    mock_app.state.sab.servers[0]["host"] = "127.0.0.1"  # the other mock hosts don't resolve → skipped
    url = serve(mock_app)
    admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": "k"}})
    r = admin.post("/api/v1/usenet/providers/import-sab")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(i["slug"], i["sab_server"], i["connections"]) for i in items] == [
        ("eweka", "news.eweka.test", 20)
    ]
    assert items[0]["credentials"] == {"username": "leeg", "password": "leeg"}
    assert items[0]["status"] == "missing_credentials"
    # health comes from SABnzbd's own server status until MSO has credentials
    assert items[0]["health"] == {
        "status": "ok",
        "detail": "SABnzbd meldt geen fouten voor deze server.",
        "source": "sabnzbd",
    }
    assert "wachtwoorden" in r.json()["note"]
    assert admin.post("/api/v1/usenet/providers/import-sab").json()["items"] == []  # already linked
    mock_app.state.sab.set_server_error("news.eweka.test", "Server 127.0.0.1 reported: 482 Account expired.")
    admin.get("/api/v1/usenet/sab/servers")  # refreshes SABnzbd's server state
    p = admin.get("/api/v1/usenet/providers").json()["items"][0]
    assert p["health"]["status"] == "account_expired" and p["health"]["source"] == "sabnzbd"
    assert p["sab_status"]["label"] == "Account verlopen"
    assert p["status"] == "missing_credentials"  # MSO's own state is never "auth_failed" without a login
