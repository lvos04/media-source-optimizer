"""MediaGuard API-key authentication (contract docs/MEDIAGUARD_API_KEYS.md): header X-Api-Key, own role,
no session/CSRF, revocable; graceful message for an older MediaGuard without API keys."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from mg_support import assert_only_allowed_writes, configure, start_mock
from msomocks import mediaguard as mgmock

KEYS = {role: k for k, (_, role) in mgmock.API_KEYS.items()}


def _sync(operator: Any, worker: Any) -> None:
    assert operator.post("/api/v1/mediaguard/sync").status_code == 200
    worker.drain()


def test_api_key_operator_reads_and_requests_search(
    serve: Any, admin: Any, viewer: Any, operator: Any, worker: Any
) -> None:
    mock, url = start_mock(serve)
    configure(admin, url, api_key=KEYS["operator"])
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "connected" and st["auth_method"] == "apikey" and st["role"] == "operator"
    assert st["search"]["available"] is True and not st["warnings"]
    _sync(operator, worker)
    r = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert r.status_code == 200, r.text and r.json()["request"]["status"] == "sent"
    assert len(mock.state.searches) == 1
    # stateless: no login call was ever made with an API key
    assert not [c for c in mock.state.calls if c[1] == "/api/v1/auth/login"]
    assert_only_allowed_writes(mock)


def test_api_key_viewer_is_read_only(serve: Any, admin: Any, viewer: Any, operator: Any, worker: Any) -> None:
    mock, url = start_mock(serve)
    configure(admin, url, api_key=KEYS["viewer"])
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["role"] == "viewer" and st["search"]["available"] is False
    assert any("viewer" in w for w in st["warnings"])
    _sync(operator, worker)
    assert operator.post("/api/v1/mediaguard/wanted/1/search").status_code == 409
    assert not mock.state.searches


@pytest.mark.parametrize("key", ["mgk_bestaatniet0000000000000000000000000000000", KEYS["revoked"]])
def test_invalid_or_revoked_key_is_degraded(serve: Any, admin: Any, viewer: Any, key: str) -> None:
    _, url = start_mock(serve)
    configure(admin, url, api_key=key)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "degraded" and "API-sleutel" in (st["error"] or "")


def test_older_mediaguard_without_api_keys_explains(serve: Any, admin: Any, viewer: Any) -> None:
    _, url = start_mock(serve, no_api_keys=True)
    configure(admin, url, api_key=KEYS["operator"])
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "degraded"
    assert "ondersteunt nog geen API-sleutels" in st["error"]


def test_api_key_preferred_when_several_secrets_set(serve: Any, admin: Any, viewer: Any) -> None:
    from mso.connectors import base as cb
    from mso.connectors import mediaguard as mgc

    _, url = start_mock(serve)
    r = admin.put("/api/v1/connectors/mediaguard", json={
        "url": url, "enabled": True, "options": {},
        "secrets": {"api_key": KEYS["operator"], "username": "mso-operator", "password": "Operator-Wachtwoord1"}})
    assert r.status_code == 200
    assert mgc.auth_method(cb.load_config("mediaguard")) == "apikey"
    assert viewer.get("/api/v1/mediaguard/status?refresh=true").json()["auth_method"] == "apikey"


def test_api_key_never_exposed(serve: Any, admin: Any, viewer: Any, operator: Any, worker: Any,
                               caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _, url = start_mock(serve)
    key = KEYS["operator"]
    configure(admin, url, api_key=key)
    _sync(operator, worker)
    operator.post("/api/v1/mediaguard/wanted/2/search")
    for path in ("/api/v1/connectors", "/api/v1/mediaguard/status", "/api/v1/audit?limit=500",
                 "/api/v1/mediaguard/search-requests", "/api/v1/jobs"):
        assert key not in admin.get(path).text, path
    assert key not in caplog.text


def test_redaction_masks_mediaguard_keys() -> None:
    from mso.security import redact

    key = KEYS["operator"]
    for text in (f"X-Api-Key: {key}", f"sleutel {key} geweigerd", f"{{'api_key': '{key}'}}"):
        assert key not in redact(text)
