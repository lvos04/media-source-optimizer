"""1.4.0 — the Newznab endpoint with MSO's *own* sources: Spotweb (direct config) and the header index (a search
backend with ``rss=True`` and ``active``). End to end over real HTTP: Prowlarr's add-indexer validation (text-less
query) gets ≥1 real item and "Registreer in Prowlarr" becomes available; without an active own source it stays
disabled with the explanation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from msomocks import prowlarr as mock_prowlarr
from msomocks import spotweb as mock_spotweb
from slice_a_helpers import PROWLARR_KEY, add_direct, configure_prowlarr, set_settings

from mso.domain.release import NormalizedRelease, parse_title

SPOT_KEY = "spot-test-key"


@pytest.fixture()
def prowlarr(app: Any, serve: Any) -> Any:
    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY, validate_on_add=1)
    configure_prowlarr(serve(mock))
    return mock


def _register_and_apply(admin: Any) -> dict[str, Any]:
    res = admin.post("/api/v1/indexers/prowlarr/register", json={"kinds": ["newznab"]})
    assert res.status_code == 200, res.text
    ch = res.json()["change"]
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    return r.json()


def test_spotweb_alone_makes_registration_work(
    app: Any, admin: Any, serve: Any, prowlarr: Any, manage_mode: None
) -> None:
    mso_url = serve(app)
    set_settings({"newznab.public_url": mso_url})
    info = admin.get("/api/v1/newznab/endpoint").json()["registration"]["newznab"]
    assert info["ready"] is False and "Spotweb" in info["reason"]
    add_direct(
        admin,
        serve(mock_spotweb.create_app(apikey=SPOT_KEY)),
        kind="spotweb",
        key=SPOT_KEY,
        label="Eigen Spotweb",
        api_path="/index.php?page=newznabapi",
    )
    info = admin.get("/api/v1/newznab/endpoint").json()["registration"]["newznab"]
    assert info["ready"] is True and [s["label"] for s in info["sources"]] == ["Eigen Spotweb"]
    _register_and_apply(admin)
    v = prowlarr.state.validations[-1]
    assert "q" not in v["params"] and v["params"]["t"] == "search"
    assert v.get("items", 0) >= 1, v
    assert prowlarr.state.search_calls == 0  # loop protection: MSO never asked Prowlarr back


def _hi_release(i: int) -> NormalizedRelease:
    title = f"Header.Index.Show.S01E0{i}.1080p.WEB-DL.H.264-GRP"
    return NormalizedRelease(
        protocol="usenet",
        source_id="headerindex:test",
        origin="header_index",
        title=title,
        guid=f"hi-{i}",
        message_id=f"<hi{i}@mso.test>",
        size_bytes=1_500_000_000,
        publish_date=datetime.now(UTC) - timedelta(hours=i),
        parsed=parse_title(title),
    )


@pytest.fixture()
def header_index() -> Any:
    """A header-index backend exactly as Slice B registers it in 1.4.0 (``rss=True, active=enabled``)."""
    from mso.features.search.backends import register_backend, unregister_backend

    state = {"enabled": True, "queries": []}

    def fn(q: Any) -> list[NormalizedRelease]:
        state["queries"].append(q.q)
        return [_hi_release(1), _hi_release(2)]

    def fetch(row: dict[str, Any]) -> Any:
        from mso.features.search.urls import Fetched

        state.setdefault("fetched", []).append(row["title"])
        return Fetched(b"<nzb>built from headers</nzb>", "application/x-nzb", f"{row['title']}.nzb")

    register_backend(
        "hitest",
        fn,
        label="Eigen header-index",
        source_id="headerindex:test",
        kind="headerindex",
        protocols={"usenet"},
        rss=True,
        active=lambda: state["enabled"],
        fetch=fetch,
    )
    yield state
    unregister_backend("hitest")


def test_header_index_with_rss_makes_registration_work(
    app: Any, admin: Any, serve: Any, prowlarr: Any, manage_mode: None, header_index: Any
) -> None:
    mso_url = serve(app)
    set_settings({"newznab.public_url": mso_url})
    info = admin.get("/api/v1/newznab/endpoint").json()["registration"]["newznab"]
    assert info["ready"] is True and "Eigen header-index" in [s["label"] for s in info["sources"]]
    _register_and_apply(admin)
    v = prowlarr.state.validations[-1]
    assert v.get("items", 0) >= 1, v
    assert None in header_index["queries"]  # answered the text-less (RSS-style) query itself


def test_header_index_switched_off_keeps_registration_disabled(admin: Any, prowlarr: Any, header_index: Any) -> None:
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    header_index["enabled"] = False
    info = admin.get("/api/v1/newznab/endpoint").json()["registration"]["newznab"]
    assert info["ready"] is False and info["reason"].startswith("MSO heeft nog geen eigen bronnen")
    r = admin.post("/api/v1/indexers/prowlarr/register", json={"kinds": ["newznab"]})
    assert r.status_code == 422 and "Spotweb" in r.json()["detail"]
    assert not [c for c in prowlarr.state.calls if c["method"] != "GET"]


def test_header_index_grab_uses_backend_fetch(admin: Any, client: Any, header_index: Any) -> None:
    """A header-index result has no download URL: a grab through the endpoint uses the backend's ``fetch``."""
    from defusedxml import ElementTree as DET
    from msomocks import prowlarr as mp

    info = admin.get("/api/v1/newznab/endpoint").json()
    key = admin.post(f"/api/v1/newznab/consumers/{info['default_consumer']['id']}/reveal").json()["apikey"]
    r = client.get(
        "/newznab/api", params={"t": "search", "apikey": key, "cat": "2000,5000"}, headers={"User-Agent": mp.UA}
    )
    assert r.status_code == 200, r.text
    items = DET.fromstring(r.content).findall("./channel/item")
    assert len(items) >= 1
    link = items[0].find("link").text
    path = link.split("/newznab/api", 1)[1]
    g = client.get("/newznab/api" + path, headers={"User-Agent": mp.UA})
    assert g.status_code == 200, g.text
    assert g.content == b"<nzb>built from headers</nzb>" and header_index["fetched"]
