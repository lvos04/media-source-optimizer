"""OpenAPI-export actueel (contract §5 en §10, DELTA_MATRIX P3-14; review contract 1.3).

``docs/openapi/media-source-optimizer.openapi.json`` is de momentopname van de echte API. Deze test bouwt de app en
vergelijkt het schema byte voor byte met het bestand, zodat een gewijzigde route, parameter of respons niet ongemerkt
naast een verouderde export bestaat. Bijwerken na een bewuste API-wijziging:

    MSO_OPENAPI_BIJWERKEN=1 python -m pytest -q tests/test_openapi_export.py

Daarna de wijziging in de export controleren en meecommitten (HARDE EIS: bestaande routes niet breken, §2.3 regel 1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

EXPORT = Path(__file__).resolve().parents[3] / "docs" / "openapi" / "media-source-optimizer.openapi.json"


def serialiseer(schema: dict[str, Any]) -> str:
    """Vaste vorm van de export: gesorteerde sleutels, inspringing 1, UTF-8, één regeleinde aan het eind."""
    return json.dumps(schema, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def test_openapi_export_is_actueel(runtime: Any) -> None:
    actueel = serialiseer(runtime.openapi())
    if os.environ.get("MSO_OPENAPI_BIJWERKEN") == "1":
        EXPORT.write_text(actueel, encoding="utf-8")
    opgeslagen = EXPORT.read_text(encoding="utf-8")
    assert actueel == opgeslagen, (
        "docs/openapi/media-source-optimizer.openapi.json wijkt af van de API. Bewuste wijziging? Draai "
        "MSO_OPENAPI_BIJWERKEN=1 python -m pytest -q tests/test_openapi_export.py en commit de nieuwe export."
    )


def test_export_noemt_versie_en_paden() -> None:
    """De export zelf is geen lege of afgekapte momentopname."""
    schema = json.loads(EXPORT.read_text(encoding="utf-8"))
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["version"]
    assert len(schema["paths"]) >= 180
    assert "/api/v1/settings" in schema["paths"]


def test_export_connectorcontract() -> None:
    paths = json.loads(EXPORT.read_text(encoding="utf-8"))["paths"]
    for route in ("discover", "health", "items", "events", "evidence/{id}"):
        operation = paths[f"/connector/v1/{route}"]["get"]
        assert operation["security"] == [{"SessionCookie": []}, {"MachineKey": []}]
        assert operation["x-machine-scopes"] == ["read"]
        for status in ("401", "403", "413", "422", "429", "503"):
            schema = operation["responses"][status]["content"]["application/json"]["schema"]
            assert schema["$ref"] == "#/components/schemas/ErrorBody"
    assert paths["/connector/v1/events"]["get"]["responses"]["409"]["description"].startswith("cursor_expired")
    propose = paths["/connector/v1/propose"]["post"]
    assert propose["security"] == [{"SessionCookie": []}]
    assert propose["x-idempotency-exception"]
    assert propose["responses"]["501"]["description"] == "capability_unavailable"
    for route in ("apply", "rollback"):
        operation = paths[f"/connector/v1/{route}"]["post"]
        assert operation["security"] == []
        assert "gepin" in operation["description"]
        assert "x-idempotency-exception" not in operation
        assert operation["responses"]["501"]["description"] == "capability_unavailable"
        assert "Idempotent-Replay" in operation["responses"]["200"]["headers"]
        request = operation["requestBody"]["content"]["application/json"]["schema"]
        assert request["$ref"] == "#/components/schemas/ExecutionRequest"
        assert next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")["required"]
    for route in ("approve", "apply"):
        operation = paths[f"/api/v1/changes/{{change_id}}/{route}"]["post"]
        assert operation["security"] == [{"SessionCookie": []}, {"MachineKey": []}]
        assert operation["x-machine-scopes"] == ["changes:approve"]
        assert "preview_changed" in operation["responses"]["409"]["description"]
    assert "security" not in paths["/readyz"]["get"]
    assert "/healthz" not in paths
    assert paths["/api/v1/overview"]["get"]["x-machine-scopes"] == ["read"]
    assert "x-machine-scopes" not in paths["/api/v1/users"]["get"]


def test_afwijking_wordt_gezien(runtime: Any) -> None:
    """Negatief: één extra pad in het schema geeft een andere export (de vergelijking is niet blind)."""
    schema = json.loads(json.dumps(runtime.openapi()))
    schema["paths"]["/api/v1/bestaat-niet"] = {"get": {"responses": {"200": {"description": "x"}}}}
    assert serialiseer(schema) != EXPORT.read_text(encoding="utf-8")


def test_leesbewijs_behoudt_dezelfde_export_bij_andere_http_statusnamen(runtime, monkeypatch) -> None:
    from http.client import responses

    # Python 3.13 gebruikt de nieuwere omschrijvingen van deze twee HTTP-statussen.
    for te_groot, ongeldig in (("Request Entity Too Large", "Unprocessable Entity"),
                               ("Content Too Large", "Unprocessable Content")):
        monkeypatch.setitem(responses, 413, te_groot)
        monkeypatch.setitem(responses, 422, ongeldig)
        runtime.openapi_schema = None
        assert serialiseer(runtime.openapi()) == EXPORT.read_text(encoding="utf-8")
