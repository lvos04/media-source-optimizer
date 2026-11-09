"""Peil O-11 'Terug naar de Hub' (ADR-0006 punt 1): optional setting systeem.hub_url in the existing registry,
read and written via the existing GET/PUT /api/v1/settings; validated like HUB_URL (contract 1.2 §3.2)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from mso import settings_registry
from mso.settings_registry import REGISTRY, validate_hub_url

D = REGISTRY["systeem.hub_url"]


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        ("", ""),
        ("   ", ""),
        ("http://nas.lan:30080", "http://nas.lan:30080"),
        ("HTTPS://NAS.LAN/", "https://nas.lan"),
        ("http://10.20.30.40:8080", "http://10.20.30.40:8080"),
        ("http://[fd00::12]:30080", "http://[fd00::12]:30080"),
        ("https://hub.example", "https://hub.example"),
    ],
)
def test_valid_hub_urls_are_normalised(raw: str, normalised: str) -> None:
    assert validate_hub_url(D, raw) == normalised


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "ftp://nas.lan",
        "nas.lan:30080",
        "http://",
        "http://nas.lan:30080/pad",
        "http://nas.lan/?q=1",
        "http://nas.lan/#x",
        "http://gebruiker:geheim@nas.lan",
        "http://localhost:30080",
        "http://hub.localhost",
        "http://127.0.0.1:30080",
        "http://[::1]:30080",
        "http://169.254.10.1",
        "http://[fe80::1]",
        "http://0.0.0.0:30080",
        "http://[::]:30080",
        "http://nas.lan:99999",
        "http://nas lan",
        "http://nas.lan\\@evil",
    ],
)
def test_invalid_hub_urls_are_refused(raw: str) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_hub_url(D, raw)
    assert exc.value.status_code == 422
    assert "Hub-adres" in str(exc.value.detail)


def test_definition_is_optional_string_without_restart() -> None:
    assert D.type == "string" and D.default == "" and D.category == "systeem"
    assert D.risk == "advies" and not D.restart and not D.locked
    # the definitions response keeps its shape: no validator field leaks into it
    defs = settings_registry.definitions()
    hub = next(d for d in defs if d["key"] == "systeem.hub_url")
    assert set(hub) == set(defs[0])
    assert "validator" not in hub


def test_hub_url_via_settings_api(admin: Any) -> None:
    body = admin.get("/api/v1/settings").json()
    assert body["values"]["systeem.hub_url"] == ""
    assert any(d["key"] == "systeem.hub_url" for d in body["definitions"])

    r = admin.put("/api/v1/settings", json={"values": {"systeem.hub_url": "http://nas.lan:30080/"}})
    assert r.status_code == 200, r.text
    assert admin.get("/api/v1/settings").json()["values"]["systeem.hub_url"] == "http://nas.lan:30080"

    for bad in ("javascript:alert(1)", "http://nas.lan:30080/pad", "http://127.0.0.1:30080"):
        r = admin.put("/api/v1/settings", json={"values": {"systeem.hub_url": bad}})
        assert r.status_code == 422, bad
        assert "Hub-adres" in r.text
    # refused values never replace the stored one
    assert admin.get("/api/v1/settings").json()["values"]["systeem.hub_url"] == "http://nas.lan:30080"

    assert admin.put("/api/v1/settings", json={"values": {"systeem.hub_url": ""}}).status_code == 200
    assert admin.get("/api/v1/settings").json()["values"]["systeem.hub_url"] == ""
