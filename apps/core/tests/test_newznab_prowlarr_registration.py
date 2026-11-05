"""Slice A — registering MSO in Prowlarr only when it makes sense, and passing Prowlarr's add-indexer test.

Real Prowlarr tests a new Newznab/Torznab indexer with a text-less ``t=search`` (RSS-style, with the caps
categories) and refuses it with HTTP 400 "Query successful, but no results were returned from your indexer"
when nothing comes back. MSO never asks Prowlarr back (loop protection), so:
- without own sources for a protocol the registration is refused up front (clear Dutch reason), per protocol;
- with own sources, the text-less query is answered with the latest *real* releases of those sources.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from defusedxml import ElementTree as DET
from msomocks import newznab as mock_nz
from msomocks import prowlarr as mock_prowlarr
from slice_a_helpers import NZ_KEY, PROWLARR_KEY, TZ_KEY, add_direct, configure_prowlarr, set_settings

PROWLARR_UA = mock_prowlarr.UA
CAPS_CATS = "2000,2010,2020,2030,2040,2045,2050,2060,2070,2080,5000,5010,5020,5030,5040,5045,5050,5060,5070,5080"


@pytest.fixture()
def prowlarr(app: Any, serve: Any) -> Any:
    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY, validate_on_add=1)
    configure_prowlarr(serve(mock))
    return mock


def _key(admin: Any) -> str:
    info = admin.get("/api/v1/newznab/endpoint").json()
    return admin.post(f"/api/v1/newznab/consumers/{info['default_consumer']['id']}/reveal").json()["apikey"]


def test_registration_refused_without_own_sources(admin: Any, prowlarr: Any) -> None:
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    info = admin.get("/api/v1/newznab/endpoint").json()
    for kind in ("newznab", "torznab"):
        assert info["registration"][kind]["ready"] is False
        assert info["registration"][kind]["reason"].startswith("MSO heeft nog geen eigen bronnen")
    r = admin.post("/api/v1/indexers/prowlarr/register")
    assert r.status_code == 422, r.text
    assert "MSO heeft nog geen eigen bronnen achter dit endpoint" in r.json()["detail"]
    assert "header-index" in r.json()["detail"]
    # nothing proposed, nothing written to Prowlarr
    assert admin.get("/api/v1/changes", params={"target": "prowlarr"}).json().get("items", []) == []
    assert not [c for c in prowlarr.state.calls if c["method"] != "GET"]


def test_registration_is_per_protocol(admin: Any, prowlarr: Any, serve: Any) -> None:
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    add_direct(admin, serve(mock_nz.create_app(apikey=NZ_KEY)), label="Eigen NZB")
    info = admin.get("/api/v1/newznab/endpoint").json()["registration"]
    assert info["newznab"]["ready"] is True and info["newznab"]["sources"][0]["label"] == "Eigen NZB"
    assert info["torznab"]["ready"] is False and "Torznab-indexer" in info["torznab"]["reason"]
    res = admin.post("/api/v1/indexers/prowlarr/register").json()
    assert res["status"] == "preview"
    assert [op["path"] for op in res["change"]["ops"]] == ["indexer:newznab"]
    assert "torznab" in res["not_ready"]
    assert res["change"]["impact"]["Niet geregistreerd"]["Torznab"].startswith("MSO heeft nog geen")
    r = admin.post("/api/v1/indexers/prowlarr/register", json={"kinds": ["torznab"]})
    assert r.status_code == 422 and "Torznab-indexer" in r.json()["detail"]


def test_header_index_alone_is_not_enough(admin: Any, prowlarr: Any) -> None:
    """A backend that cannot answer a text-less query (no ``rss``) would still fail Prowlarr's test."""
    from mso.features.search.backends import register_backend, unregister_backend

    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    register_backend(
        "testhi", lambda q: [], label="Test-index", kind="headerindex", protocols={"usenet"}, active=lambda: True
    )
    try:
        info = admin.get("/api/v1/newznab/endpoint").json()["registration"]["newznab"]
        assert info["ready"] is False and "Test-index" in info["reason"]
        assert admin.post("/api/v1/indexers/prowlarr/register").status_code == 422
    finally:
        unregister_backend("testhi")


@pytest.mark.parametrize("with_cat", [True, False])
def test_textless_rss_query_returns_latest_real_items(
    admin: Any, client: Any, serve: Any, with_cat: bool
) -> None:
    nz = mock_nz.create_app(apikey=NZ_KEY)
    add_direct(admin, serve(nz), label="Eigen NZB")
    key = _key(admin)
    params = {"t": "search", "extended": "1", "limit": "100", "offset": "0", "apikey": key}
    if with_cat:
        params["cat"] = CAPS_CATS
    r = client.get("/newznab/api", params=params, headers={"User-Agent": PROWLARR_UA})
    assert r.status_code == 200, r.text
    items = DET.fromstring(r.content).findall("./channel/item")
    assert len(items) >= 1
    titles = {i.findtext("title") for i in items}
    assert titles <= {f["title"] for f in mock_nz.FIXTURES}  # real upstream releases, nothing synthesised
    up = nz.state.calls[-1]["params"]
    assert up["t"] == "search" and "q" not in up


def test_prowlarr_add_indexer_validation_passes(
    app: Any, admin: Any, serve: Any, prowlarr: Any, manage_mode: None
) -> None:
    """End to end over real HTTP: MSO served, Prowlarr (mock) runs its add-indexer test against it."""
    mso_url = serve(app)
    set_settings({"newznab.public_url": mso_url})
    add_direct(admin, serve(mock_nz.create_app(apikey=NZ_KEY)), label="Eigen NZB")
    add_direct(
        admin,
        serve(mock_nz.create_app(apikey=TZ_KEY, protocol="torrent")),
        kind="torznab",
        key=TZ_KEY,
        label="Eigen torrent",
    )
    ch = admin.post("/api/v1/indexers/prowlarr/register").json()["change"]
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    assert len(prowlarr.state.validations) == 2
    for v in prowlarr.state.validations:
        assert "q" not in v["params"] and v["params"]["t"] == "search" and v["params"]["cat"]
        assert v.get("items", 0) >= 1, v
    regs = [i for i in prowlarr.state.data["indexers"] if i["name"].startswith("Media Source Optimizer")]
    assert {i["implementation"] for i in regs} == {"Newznab", "Torznab"}
    # Prowlarr's own search through the new MSO indexer never makes MSO query Prowlarr again
    assert prowlarr.state.search_calls == 0


def test_prowlarr_refusal_is_explained_and_restored(
    app: Any, admin: Any, serve: Any, prowlarr: Any, manage_mode: None
) -> None:
    """Own source exists but returns nothing right now → Prowlarr refuses; MSO explains it in Dutch."""
    mso_url = serve(app)
    set_settings({"newznab.public_url": mso_url})
    nz = mock_nz.create_app(apikey=NZ_KEY)
    add_direct(admin, serve(nz), label="Eigen NZB")
    ch = admin.post("/api/v1/indexers/prowlarr/register", json={"kinds": ["newznab"]}).json()["change"]
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    nz.state.faults["empty"] = 1
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code >= 400
    row = admin.get(f"/api/v1/changes/{ch['id']}").json()
    assert row["status"] == "failed"
    assert "testzoekopdracht (zonder zoekterm)" in row["error"] and "Eigen NZB" in row["error"]
    assert not [i for i in prowlarr.state.data["indexers"] if i["name"].startswith("Media Source Optimizer")]


def test_apply_refused_when_sources_disappeared(admin: Any, serve: Any, prowlarr: Any, manage_mode: None) -> None:
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    d = add_direct(admin, serve(mock_nz.create_app(apikey=NZ_KEY)), label="Eigen NZB")
    ch = admin.post("/api/v1/indexers/prowlarr/register", json={"kinds": ["newznab"]}).json()["change"]
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    admin.patch(f"/api/v1/indexers/direct/{d['slug']}", json={"enabled": False})
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 422 and "MSO heeft nog geen eigen bronnen" in r.json()["detail"]
    assert not [c for c in prowlarr.state.calls if c["method"] == "POST"]


def test_validation_mock_matches_prowlarr_wording(serve: Any) -> None:
    """Guard for the mock itself: an unreachable/empty indexer gets Prowlarr's exact refusal text."""
    nz = mock_nz.create_app(apikey=NZ_KEY, empty=1)
    url = serve(nz)
    p = mock_prowlarr.create_app(api_key=PROWLARR_KEY, validate_on_add=1)
    purl = serve(p)
    body = {
        "name": "x",
        "implementation": "Newznab",
        "enable": True,
        "fields": mock_prowlarr._fields(url, "/api", NZ_KEY),
    }
    r = httpx.post(purl + "/api/v1/indexer", json=body, headers={"X-Api-Key": PROWLARR_KEY})
    assert r.status_code == 400 and "no results were returned from your indexer" in r.text
