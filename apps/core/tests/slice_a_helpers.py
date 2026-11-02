"""Helpers for Slice A tests (indexers, search, Newznab/Torznab server). Not a test module."""

from __future__ import annotations

from typing import Any

PROWLARR_KEY = "prowlarr-test-key-0123456789"
NZ_KEY = "nz-test-key-abcdef123456"
TZ_KEY = "tz-test-key-abcdef654321"
SPOT_KEY = "spot-test-key-777888999"


def configure_prowlarr(url: str, key: str = PROWLARR_KEY) -> None:
    from mso.connectors import base as cb
    from mso.db import get_db
    from mso.vault import get_vault

    get_db().execute("UPDATE connectors SET url=%s, enabled=true WHERE id='prowlarr'", (url,))
    get_vault().set(cb.secret_name("prowlarr", "api_key"), key)


def set_settings(values: dict[str, Any]) -> None:
    from mso import settings_registry as sr

    sr.put(values, "test")


def add_direct(
    admin: Any,
    url: str,
    *,
    kind: str = "newznab",
    key: str = NZ_KEY,
    label: str | None = None,
    scopes: list[str] | None = None,
    api_path: str = "/api",
    **extra: Any,
) -> dict[str, Any]:
    body = {
        "kind": kind,
        "label": label or f"Mock {kind}",
        "url": url,
        "api_path": api_path,
        "api_key": key,
        "scopes": scopes or ["normal", "anime"],
        **extra,
    }
    r = admin.post("/api/v1/indexers/direct", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def search(client: Any, **params: Any) -> dict[str, Any]:
    r = client.get("/api/v1/search", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def report(res: dict[str, Any], source_id: str) -> dict[str, Any]:
    return next(s for s in res["sources"] if s["source_id"] == source_id)
