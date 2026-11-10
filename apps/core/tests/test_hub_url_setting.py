"""Peil O-11 'Terug naar de Hub' (ADR-0006 punt 1): optional setting systeem.hub_url in the existing registry,
read and written via the existing GET/PUT /api/v1/settings; validated like HUB_URL (contract 1.3 §2.3 regel 5 + §3.2,
C1.3-MSO-02, ADR-0009)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from mso import settings_registry
from mso.settings_registry import REGISTRY, validate_hub_url

D = REGISTRY["systeem.hub_url"]


#: shared with apps/web/tests/unit/hub.test.ts: the UI shows the link only when the core would accept the value
VECTORS = json.loads((Path(__file__).resolve().parents[3] / "tests" / "vectoren" / "hub_url.json").read_text("utf-8"))


@pytest.mark.parametrize(("raw", "normalised"), [("", ""), ("   ", ""), *VECTORS["geldig"]])
def test_valid_hub_urls_are_normalised(raw: str, normalised: str) -> None:
    assert validate_hub_url(D, raw) == normalised


@pytest.mark.parametrize("raw", VECTORS["ongeldig"])
def test_invalid_hub_urls_are_refused(raw: str) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_hub_url(D, raw)
    assert exc.value.status_code == 422
    assert str(exc.value.detail).startswith("Hub-adres")


@pytest.mark.parametrize(
    "raw", ["http://127.1", "http://2130706433", "http://0x7f000001", "http://010.0.0.1", "http://0", "http://nas.123"]
)
def test_non_canonical_ipv4_gets_its_own_explanation(raw: str) -> None:
    """Contract 1.3 §3.2: shortened, decimal, octal or hex IPv4 forms are invalid; the message says how to write it."""
    with pytest.raises(HTTPException) as exc:
        validate_hub_url(D, raw)
    assert exc.value.detail == settings_registry.HUB_URL_IP_ERROR


@pytest.mark.parametrize(
    "raw", ["http://localhost", "http://localhost.", "http://127.0.0.1", "http://[::ffff:127.0.0.1]", "http://0.0.0.0"]
)
def test_loopback_and_unspecified_get_their_own_explanation(raw: str) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_hub_url(D, raw)
    assert exc.value.detail == settings_registry.HUB_URL_BLOCKED_ERROR


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


def test_hub_url_only_admin_may_change_it(admin: Any, operator: Any, viewer: Any, client: Any) -> None:
    """Negative paths (QA Peil-ronde): every logged-in role may READ the Hub address (the link is shown to all), but
    only an admin may change it; anonymous gets 401 for both. A refused write never changes the stored value."""
    r = admin.put("/api/v1/settings", json={"values": {"systeem.hub_url": "http://nas.lan:30080"}})
    assert r.status_code == 200, r.text
    for who in (operator, viewer):
        assert who.get("/api/v1/settings").json()["values"]["systeem.hub_url"] == "http://nas.lan:30080"
        r = who.put("/api/v1/settings", json={"values": {"systeem.hub_url": "http://andere-hub.lan:30080"}})
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "forbidden"
    assert client.get("/api/v1/settings").status_code == 401
    r = client.put("/api/v1/settings", json={"values": {"systeem.hub_url": "http://andere-hub.lan:30080"}})
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "unauthorized"
    assert admin.get("/api/v1/settings").json()["values"]["systeem.hub_url"] == "http://nas.lan:30080"


def test_refused_hub_url_uses_the_api_error_format(admin: Any) -> None:
    """A 422 for an invalid address has the existing /api/v1 error body (ADR-0003: detail, code, correlation_id) and
    echoes neither the refused value nor a stack trace."""
    r = admin.put("/api/v1/settings", json={"values": {"systeem.hub_url": "http://gebruiker:geheim@nas.lan"}})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "invalid" and body["correlation_id"]
    assert body["detail"].startswith("Hub-adres")
    assert "geheim" not in r.text and "Traceback" not in r.text


def test_value_stored_under_older_rules_is_kept_but_refused_on_save(admin: Any) -> None:
    """C1.3-MSO-02: a value that 1.2 accepted but 1.3 refuses (e.g. a decimal IPv4 form) is not silently changed or
    removed — the admin still sees it in Instellingen — but the UI shows no link (apps/web/lib/hub.ts) and saving it
    again gives the 422 with the explanation. No migration touches other settings."""
    from mso.db import get_db, jsonb

    get_db().execute(
        "INSERT INTO settings(key,value,updated_at,updated_by) VALUES (%s,%s,now(),%s)",
        ("systeem.hub_url", jsonb("http://2130706433:30080"), "test"),
    )
    assert admin.get("/api/v1/settings").json()["values"]["systeem.hub_url"] == "http://2130706433:30080"
    r = admin.put("/api/v1/settings", json={"values": {"systeem.hub_url": "http://2130706433:30080"}})
    assert r.status_code == 422
    assert r.json()["detail"] == settings_registry.HUB_URL_IP_ERROR
    r = admin.put("/api/v1/settings", json={"values": {"systeem.hub_url": "http://10.20.30.40:30080"}})
    assert r.status_code == 200, r.text
    assert admin.get("/api/v1/settings").json()["values"]["systeem.hub_url"] == "http://10.20.30.40:30080"
