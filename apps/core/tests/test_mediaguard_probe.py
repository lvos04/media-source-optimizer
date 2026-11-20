"""Routedetectie met de rechten van de MediaGuard-koppeling."""

from __future__ import annotations

import httpx
import pytest

from mso.connectors import base, mediaguard
from mso.features.mediaguard import compat, service


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_read_probe_confirms_only_accessible_routes(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    calls = []
    responses = {
        "/api/v1/health": (200, {"version": "1.2.2"}),
        "/api/v1/auth/me": (200, {"role": role}),
        "/api/v1/status": (200, {"emergency_stop": {"active": False}}),
        "/api/v1/wanted": (200, {"items": [{"id": 1}]}),
        "/api/v1/wanted/1": (200, {"id": 1}),
        "/api/v1/wanted/1/diagnosis": (404, {"detail": "Nog geen diagnose opgeslagen"}),
        "/api/v1/instances": (200, [{"id": "fixture"}]),
        "/api/v1/instances/fixture/health": (403, {"detail": "Onvoldoende rechten"}),
        "/api/v1/wanted/kpis": (401, {"detail": "Niet ingelogd"}),
        "/api/v1/wanted/reasons": (503, {"detail": "Tijdelijk onbeschikbaar"}),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status, body = responses.get(request.url.path, (404, {"detail": "Not Found"}))
        return httpx.Response(status, json=body)

    def client(url: str, **options: object) -> httpx.Client:
        return httpx.Client(base_url=url, transport=httpx.MockTransport(handle), **options)

    monkeypatch.setattr(base, "http_client", client)
    monkeypatch.setattr(service, "configured", lambda: True)
    cfg = base.ConnectorConfig(
        id="mediaguard", url="http://fixture.invalid", enabled=True, options={}, secrets={"api_key": "fixture"}
    )
    probe = mediaguard.probe(cfg)
    caps = probe["capabilities"]
    assert probe["capability_source"] == "probe" and probe["role"] == role
    assert compat.by_id("openapi").role == "admin"
    assert caps["openapi"] is False
    assert caps["wanted_list"] is True and caps["wanted_item"] is True and caps["wanted_diagnosis"] is True
    assert caps["wanted_search"] is None and caps["instance_queue_block"] is None
    assert caps["instance_health"] is None and caps["wanted_kpis"] is None and caps["wanted_reasons"] is None
    assert caps["instance_queue"] is False
    assert service.can_request_search(probe)[0] is False
    assert all(request.method == "GET" and "{" not in request.url.path for request in calls)
    assert all(request.url.path != "/api/v1/openapi.json" for request in calls)
    assert all(
        request.headers.get("x-api-key") == "fixture" for request in calls if request.url.path != "/api/v1/health"
    )
    diagnosis = next(request for request in calls if request.url.path.endswith("/diagnosis"))
    assert dict(diagnosis.url.params) == {"cached": "true"}


def test_openapi_snapshot_cannot_enable_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "configured", lambda: True)
    caps = {"auth_me": True, "wanted_list": True, "wanted_search": True}
    snapshot = {"auth_ok": True, "reachable": True, "role": "operator", "capability_source": "openapi",
                "capabilities": caps}
    assert service.can_request_search(snapshot)[0] is False
    matrix = compat.matrix(caps, source="openapi")
    assert matrix["live_source"] is None and all(row["live"] == "onbekend" for row in matrix["routes"])
