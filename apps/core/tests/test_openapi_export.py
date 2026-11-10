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


def test_afwijking_wordt_gezien(runtime: Any) -> None:
    """Negatief: één extra pad in het schema geeft een andere export (de vergelijking is niet blind)."""
    schema = json.loads(json.dumps(runtime.openapi()))
    schema["paths"]["/api/v1/bestaat-niet"] = {"get": {"responses": {"200": {"description": "x"}}}}
    assert serialiseer(schema) != EXPORT.read_text(encoding="utf-8")
