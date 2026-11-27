"""Vaste negatieve proeven voor de MediaGuard-koppeling (S-P3-265)."""

import runpy
import subprocess
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import Response
from fastapi.responses import JSONResponse
from mg_support import configure, start_mock
from msomocks import mediaguard as mg_fixture
from test_mediaguard_apikey import KEYS
from test_torrent_cleanup import _approve_apply, _setup
from test_torrent_cleanup import qb as qb

from mso.connectors import base, mediaguard
from mso.features.mediaguard import compat, service
from mso.features.recovery import blacklist


def matrix_status(probe, route):
    matrix = compat.matrix(probe["capabilities"], source=probe["capability_source"])
    return next(rij["live"] for rij in matrix["routes"] if rij["id"] == route)


@pytest.mark.parametrize("rol", ["viewer", "operator"])
def test_onbevestigde_blokkeerroute_ontbreekt_in_voorstel(qb, admin, operator, viewer, serve, manage_mode, rol):
    item = _setup(qb)["exe"]
    mock, url = start_mock(serve)
    instantie = next(iter(mock.state.queues))
    mock.state.queues[instantie] = [{"id": 1, "downloadId": item, "title": "Testitem"}]
    configure(admin, url, api_key=KEYS[rol])
    respons = viewer.get("/api/v1/mediaguard/status?refresh=true")
    assert respons.status_code == 200
    status = respons.json()
    rij = next(rij for rij in status["compat"]["routes"] if rij["id"] == "instance_queue_block")
    assert status["role"] == rol and rij["live"] == "onbekend"
    voorstel = operator.post("/api/v1/torrent/suspicious/cleanup-preview", json={"hashes": [item]})
    assert voorstel.is_success
    acties = [op["path"].split(":")[0] for op in voorstel.json()["ops"]]
    assert all(method == "GET" for method, _, _ in mock.state.calls)
    assert acties == ["cleanup", "blacklist", "torrent"], (
        f"rol={rol}; route={rij['live']}; voorstel={','.join(acties)}"
    )
    assert _approve_apply(admin, voorstel.json()["id"]).status_code == 200
    assert blacklist.is_blacklisted("btih:" + item)
    assert all(method == "GET" for method, _, _ in mock.state.calls)


def test_onherkenbaar_wanted_antwoord_bevestigt_geen_route(monkeypatch):
    def antwoord(request):
        assert request.method == "GET"
        data = {
            "/api/v1/health": {"status": "ok", "version": "1.2.2"},
            "/api/v1/auth/me": {"role": "viewer"},
            "/api/v1/status": {"emergency_stop": {"active": False}},
            "/api/v1/wanted": {"detail": "Tijdelijk niet beschikbaar"},
        }
        if request.url.path in data:
            return httpx.Response(200, json=data[request.url.path])
        return httpx.Response(404, json={"detail": "Not Found"})

    def lokaal(url, **opties):
        return httpx.Client(base_url=url, transport=httpx.MockTransport(antwoord), **opties)

    monkeypatch.setattr(base, "http_client", lokaal)
    cfg = base.ConnectorConfig(mediaguard.CID, "http://fixture.invalid", {"api_key": KEYS["viewer"]}, {})
    probe = mediaguard.probe(cfg)
    rij = matrix_status(probe, "wanted_list")
    status = mediaguard.result_from_probe(probe).status
    assert rij == "onbekend" and status == "degraded", f"wanted={rij}; verbinding={status}"


def test_onherkenbaar_wanted_antwoord_behoudt_cache(admin, viewer, operator, serve, worker):
    mock = mg_fixture.create_app()
    mock.state.wanted_onleesbaar = False

    @mock.middleware("http")
    async def vervang_wanted(request, call_next):
        if mock.state.wanted_onleesbaar and request.url.path == "/api/v1/wanted":
            return JSONResponse({"detail": "Tijdelijk niet beschikbaar"})
        return await call_next(request)

    configure(admin, serve(mock), api_key=KEYS["viewer"])

    def synchroniseer():
        assert operator.post("/api/v1/mediaguard/sync").is_success
        worker.drain()
        respons = viewer.get("/api/v1/mediaguard/wanted")
        assert respons.status_code == 200
        return respons.json()["total"]

    voor = synchroniseer()
    assert voor > 0
    mock.state.wanted_onleesbaar = True
    na = synchroniseer()
    status = viewer.get("/api/v1/mediaguard/status").json()
    sync = status["last_sync"]
    assert na == voor and sync["ok"] is False, f"cache={voor}->{na}; sync_ok={sync['ok']}; volledig={sync['complete']}"


@pytest.mark.parametrize("fout", ["foutobject", "geen-json", "geen-totaal", "verkeerd-totaal", "ongeldig-item"])
def test_onleesbare_volgpagina_behoudt_de_hele_cache(admin, serve, monkeypatch, db, fout):
    mock = mg_fixture.create_app()
    mock.state.fout = False
    foute_paginas = []

    @mock.middleware("http")
    async def vervang_volgpagina(request, call_next):
        if mock.state.fout and request.url.path == "/api/v1/wanted" and request.query_params.get("page") == "2":
            foute_paginas.append(request.query_params["page"])
            if fout == "geen-json":
                return Response("Geen JSON", media_type="text/plain")
            return JSONResponse({
                "foutobject": {"detail": "Tijdelijk niet beschikbaar"},
                "geen-totaal": {"items": []},
                "verkeerd-totaal": {"items": [], "total": "0"},
                "ongeldig-item": {"items": [{"id": True}, {"id": 9}], "total": 4},
            }[fout])
        return await call_next(request)

    configure(admin, serve(mock), api_key=KEYS["viewer"])
    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    assert service.sync()["ok"]
    query = "SELECT * FROM mg_wanted_cache ORDER BY mg_id"
    voor = db.fetchall(query)
    vorige_sync = service.snap_data("sync_state")
    for item in mock.state.wanted:
        item["title"] = "Gewijzigd testitem"
    mock.state.fout = True
    sync = service.sync()
    assert foute_paginas == ["2"]
    assert sync["ok"] is False and sync["complete"] is False and sync["next_page"] == 1
    assert db.fetchall(query) == voor
    assert service.snap_data("sync_state")["last_success_at"] == vorige_sync["last_success_at"]
    assert service.status()["capabilities"]["wanted_list"] is None
    mock.state.fout = False
    assert service.sync()["ok"]
    assert all(row["title"] == "Gewijzigd testitem" for row in db.fetchall(query))


def test_geldige_lege_wanted_pagina_vervangt_cache(admin, serve, db):
    mock, url = start_mock(serve)
    configure(admin, url, api_key=KEYS["viewer"])
    assert service.sync()["cached"] > 0
    mock.state.wanted.clear()
    sync = service.sync()
    assert sync["ok"] and sync["complete"] and sync["total_open"] == 0
    assert db.scalar("SELECT count(*) FROM mg_wanted_cache") == 0
    assert service.status()["capabilities"]["wanted_list"] is True


def test_openapi_matrix_toont_beheerdersbeperking():
    [rij] = [r for r in compat.matrix({"openapi": False}, source="probe")["routes"] if r["id"] == "openapi"]
    assert rij["role"] == "admin" and rij["live"] == "alleen_voor_beheerder"


@pytest.mark.parametrize("rol", ["viewer", "operator"])
def test_onbevestigde_zoekroute_weigert_voor_verzenden(admin, viewer, operator, serve, rol):
    mock, url = start_mock(serve)
    configure(admin, url, api_key=KEYS[rol])
    status = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert status["role"] == rol and status["search"]["available"] is False
    assert next(rij["live"] for rij in status["compat"]["routes"] if rij["id"] == "wanted_search") == "onbekend"
    respons = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert respons.status_code == 409
    melding = respons.json()["detail"]
    assert ("niet bevestigen" if rol == "operator" else "rol 'viewer'") in melding
    assert all(method == "GET" for method, _, _ in mock.state.calls)


def test_zoekroute_wordt_vlak_voor_verzenden_herbevestigd(admin, operator, serve, monkeypatch):
    mock, url = start_mock(serve)
    configure(admin, url, api_key=KEYS["operator"])
    probe = mediaguard.probe
    bevestigd = True

    def meet(cfg):
        result = probe(cfg)
        result["capabilities"]["wanted_search"] = True if bevestigd else None
        return result

    monkeypatch.setattr(mediaguard, "probe", meet)
    assert service.sync()["ok"] and service.status()["search"]["available"]
    bevestigd = False
    respons = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert respons.status_code == 409
    assert "niet bevestigen" in respons.json()["detail"]
    assert all(method == "GET" for method, _, _ in mock.state.calls)


def test_onbereikbare_database_verstuurt_geen_zoekverzoek(admin, serve, monkeypatch):
    mock, url = start_mock(serve)
    configure(admin, url, api_key=KEYS["operator"])

    def onbeschikbaar():
        raise psycopg.OperationalError("Lokale testdatabase onbeschikbaar")

    monkeypatch.setattr(service, "get_db", onbeschikbaar)
    with pytest.raises(psycopg.OperationalError):
        service.request_search(1, actor="test")
    assert all(method == "GET" for method, _, _ in mock.state.calls)


@pytest.fixture()
def contract(monkeypatch):
    stack = Path(__file__).resolve().parents[3] / "tests" / "stack"
    monkeypatch.syspath_prepend(str(stack))
    return runpy.run_path(str(stack / "mediaguard_contract.py"))


def test_image_niet_beschikbaar_stopt_ci_met_77(contract, monkeypatch, capsys):
    acties = []

    def weiger_ophalen(commando, **opties):
        acties.append(commando)
        return subprocess.CompletedProcess(commando, 1)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(subprocess, "run", weiger_ophalen)
    assert contract["main"]() == 77
    assert acties == [["docker", "pull", contract["IMAGE"]]]
    assert capsys.readouterr().out.startswith("::warning::OVERGESLAGEN: MediaGuard-core 1.2.2")


def test_schrijflijst_is_gelijk_aan_basis():
    assert [(method, pattern.pattern) for method, pattern in mediaguard.ALLOWED_WRITES] == [
        ("POST", r"^/api/v1/auth/login$"),
        ("POST", r"^/api/v1/wanted/\d+/search$"),
        ("DELETE", r"^/api/v1/instances/[A-Za-z0-9_.-]+/queue/\d+$"),
    ]


@pytest.mark.parametrize(("verwacht", "gemeten"), [(401, 200), (403, 200), (200, 403)])
def test_contract_weigert_afwijkende_status(contract, verwacht, gemeten):
    aanvraag = httpx.Request("GET", "http://fixture.invalid/api/v1/openapi.json")
    respons = httpx.Response(gemeten, json={}, request=aanvraag)
    with pytest.raises(AssertionError, match=f"geeft HTTP {gemeten}; verwacht HTTP {verwacht}"):
        contract["response_json"](respons, verwacht)


def test_contractstack_is_intern_en_gebruikt_tijdelijke_data(contract):
    stack = contract["stack"]()
    assert stack["networks"] == {"app": {"internal": True}}
    services = stack["services"]
    assert set(services) == {"mediaguard", "mock-radarr"}
    for container in services.values():
        assert container["networks"] == ["app"] and "ports" not in container
        assert container["image"] == contract["IMAGE"]
    mg = services["mediaguard"]
    assert "volumes" not in mg and {v.split(":")[0] for v in mg["tmpfs"]} == {"/config", "/work"}
    assert mg["environment"]["MG_DEV_AUTH_BYPASS"] == "0"
    assert services["mock-radarr"]["volumes"] == [f"{contract['ROOT'] / 'apps' / 'mocks'}:/fixture:ro"]


@pytest.mark.parametrize(("method", "route"), [
    ("GET", "/wanted/1"), ("GET", "/quota"), ("POST", "/wanted/1/search"), ("POST", "/probe"),
])
def test_geweigerde_bestemming_is_422_en_behoudt_voortgang(
    admin, operator, serve, db, database_bytes, monkeypatch, confirmed_search_route, method, route,
):
    from mso.security import UnsafeTarget

    mock, url = start_mock(serve)
    configure(admin, url, api_key=KEYS["operator"])
    assert service.sync()["ok"]
    db.execute("DELETE FROM mg_snapshots WHERE key='quota'")
    before = database_bytes(db)
    calls = list(mock.state.calls)

    def geweigerd(*args, **kwargs):
        raise UnsafeTarget("interne proefbestemming")

    monkeypatch.setattr(base, "http_client", geweigerd)
    response = operator.request(method, "/api/v1/mediaguard" + route)
    assert response.status_code == 422
    assert "interne proefbestemming" not in response.text
    after = database_bytes(db)
    for table in ("mg_snapshots", "mg_wanted_cache"):
        assert after[table] == before[table]
    assert mock.state.calls == calls
    assert db.scalar("SELECT count(*) FROM mg_search_requests WHERE status='pending'") == 0
