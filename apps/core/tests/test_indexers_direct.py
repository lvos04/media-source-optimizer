"""Slice A — direct Newznab/Torznab/Spotweb indexers: CRUD (admin), vault-only keys, SSRF guard, test button,
caps parsing, rate limit / quota / backoff, health job, reliability score."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from msomocks import newznab as mock_nz
from msomocks import spotweb as mock_spot
from slice_a_helpers import NZ_KEY, SPOT_KEY, TZ_KEY, add_direct, report, search


@pytest.fixture()
def nz(app: Any, serve: Any) -> tuple[Any, str]:
    m = mock_nz.create_app(apikey=NZ_KEY)
    return m, serve(m)


def test_crud_keeps_key_in_vault_only(admin: Any, viewer: Any, nz: tuple[Any, str], db: Any) -> None:
    _, url = nz
    created = add_direct(admin, url, label="NZB Mock", scopes=["normal"])
    assert created["slug"] == "nzb-mock" and created["api_key"] == "ingesteld"
    assert created["source_id"] == "direct:nzb-mock" and created["protocol"] == "usenet"
    from mso.vault import get_vault

    assert get_vault().get("indexer.nzb-mock.api_key") == NZ_KEY
    src = db.fetchone("SELECT * FROM sources WHERE id='direct:nzb-mock'")
    assert src["kind"] == "nzb_indexer" and src["origin"] == "direct" and src["scopes"] == ["normal"]
    listing = viewer.get("/api/v1/indexers/direct").json()
    assert NZ_KEY not in str(listing) and listing["items"][0]["label"] == "NZB Mock"
    assert NZ_KEY not in str(db.fetchall("SELECT * FROM audit_log"))
    # duplicate label gets a new slug; explicit duplicate slug refused
    assert add_direct(admin, url, label="NZB Mock")["slug"] == "nzb-mock-2"
    r = admin.post(
        "/api/v1/indexers/direct", json={"kind": "newznab", "label": "x", "url": url, "slug": "nzb-mock"}
    )
    assert r.status_code == 409
    r = admin.patch(
        "/api/v1/indexers/direct/nzb-mock", json={"scopes": ["anime", "normal"], "rate_per_min": 5}
    )
    assert (
        r.status_code == 200 and r.json()["scopes"] == ["normal", "anime"] and r.json()["rate_per_min"] == 5
    )
    r = admin.patch("/api/v1/indexers/direct/nzb-mock", json={"api_key": ""})
    assert r.json()["api_key"] == "leeg"
    assert (
        viewer.post("/api/v1/indexers/direct", json={"kind": "newznab", "label": "x", "url": url}).status_code
        == 403
    )
    assert admin.delete("/api/v1/indexers/direct/nzb-mock").status_code == 200
    assert db.fetchone("SELECT enabled FROM sources WHERE id='direct:nzb-mock'")["enabled"] is False
    assert get_vault().get("indexer.nzb-mock.api_key") is None


@pytest.mark.parametrize(
    "bad",
    [
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
        "gopher://x.lan/",
        "http://user:pw@indexer.lan/",
        "http://[fe80::1]/",
    ],
)
def test_ssrf_guard_on_indexer_urls(admin: Any, bad: str) -> None:
    r = admin.post("/api/v1/indexers/direct", json={"kind": "newznab", "label": "x", "url": bad})
    assert r.status_code == 422
    r = admin.post("/api/v1/indexers/direct/test", json={"kind": "newznab", "url": bad})
    assert r.status_code == 422


def test_test_button_caps_and_auth(admin: Any, operator: Any, nz: tuple[Any, str]) -> None:
    mock, url = nz
    created = add_direct(admin, url)
    res = operator.post(f"/api/v1/indexers/direct/{created['slug']}/test").json()
    assert res["ok"] is True and res["status"] == "healthy"
    assert res["capabilities"]["searching"]["tv-search"]["available"] is True
    assert 5000 in res["capabilities"]["categories"]
    # unsaved settings (dialog) with wrong key
    res = admin.post(
        "/api/v1/indexers/direct/test", json={"kind": "newznab", "url": url, "api_key": "fout"}
    ).json()
    assert res["ok"] is False and res["status"] == "degraded" and "100" in res["message"]
    mock.state.faults["error500"] = "1"
    res = operator.post(f"/api/v1/indexers/direct/{created['slug']}/test").json()
    assert res["ok"] is False and NZ_KEY not in str(res)


def test_spotweb_kind_with_page_parameter(admin: Any, viewer: Any, serve: Any, db: Any) -> None:
    url = serve(mock_spot.create_app(apikey=SPOT_KEY))
    sw = add_direct(
        admin, url, kind="spotweb", key=SPOT_KEY, label="Spotweb thuis", api_path="/index.php?page=newznabapi"
    )
    assert sw["source_id"] == "spotweb:spotweb-thuis"
    assert db.fetchone("SELECT kind FROM sources WHERE id='spotweb:spotweb-thuis'")["kind"] == "spotweb"
    assert admin.post("/api/v1/indexers/direct/spotweb-thuis/test").json()["ok"] is True
    res = search(viewer, q="Expanse", category="tv")
    assert report(res, "spotweb:spotweb-thuis")["status"] == "ok"
    assert all(i["origin"] == "spotweb" for i in res["items"])


def test_rate_limit_quota_and_backoff(admin: Any, viewer: Any, nz: tuple[Any, str], db: Any) -> None:
    mock, url = nz
    add_direct(admin, url, label="Snel", rate_per_min=1)
    assert report(search(viewer, q="Expanse"), "direct:snel")["status"] == "ok"
    rep = report(search(viewer, q="Expanse"), "direct:snel")
    assert rep["status"] == "rate_limited" and "1/min" in rep["message"]
    add_direct(admin, url, label="Quotum", daily_quota=1)
    assert report(search(viewer, q="Dune"), "direct:quotum")["status"] == "ok"
    assert report(search(viewer, q="Dune2"), "direct:quotum")["status"] == "rate_limited"
    # backoff after consecutive infrastructure failures
    add_direct(admin, url, label="Wankel")
    mock.state.faults["error500"] = "1"
    assert report(search(viewer, q="a"), "direct:wankel")["status"] == "error"
    assert report(search(viewer, q="b"), "direct:wankel")["status"] == "error"
    row = db.fetchone("SELECT * FROM direct_indexers WHERE slug='wankel'")
    assert row["consecutive_failures"] == 2 and row["backoff_until"] is not None
    assert report(search(viewer, q="c"), "direct:wankel")["status"] == "backoff"
    mock.state.faults.clear()
    db.execute("UPDATE direct_indexers SET backoff_until=now() - interval '1 minute' WHERE slug='wankel'")
    assert report(search(viewer, q="Expanse"), "direct:wankel")["status"] == "ok"
    assert (
        db.fetchone("SELECT consecutive_failures FROM direct_indexers WHERE slug='wankel'")[
            "consecutive_failures"
        ]
        == 0
    )


def test_health_job_records_observations(
    admin: Any, worker: Any, nz: tuple[Any, str], db: Any, caplog: Any
) -> None:
    caplog.set_level(logging.DEBUG)
    from mso import jobs

    mock, url = nz
    add_direct(admin, url, label="Gezond")
    add_direct(admin, url, label="Kapot", key="verkeerd")
    mock.state.faults.clear()
    jobs.enqueue("indexers.health", {"force": True})
    worker.drain()
    obs = {
        r["subject"]: r["value"]
        for r in db.fetchall("SELECT subject, value FROM observations WHERE metric='health.ok'")
    }
    assert obs["direct:gezond"] == 1.0
    assert db.fetchone("SELECT caps FROM direct_indexers WHERE slug='gezond'")["caps"]["searching"]
    # caps does not need a key in Newznab → the bad key shows up on search as auth_error
    viewer_res = search(admin, q="Expanse")
    assert report(viewer_res, "direct:kapot")["status"] == "auth_error"
    assert NZ_KEY not in caplog.text and "verkeerd" not in caplog.text


def test_reliability_scoring_prefers_reliability_over_speed(app: Any, db: Any) -> None:
    from mso.domain import outcomes
    from mso.features.indexers import reliability

    outcomes.upsert_source("direct:fast-flaky", "nzb_indexer", "Snel maar wankel", "direct")
    outcomes.upsert_source("direct:slow-solid", "nzb_indexer", "Traag maar stabiel", "direct")
    outcomes.upsert_source("direct:few", "nzb_indexer", "Weinig data", "direct")
    for i in range(10):
        ok = i % 2 == 0
        outcomes.observe("direct:fast-flaky", "search.ok", 1.0 if ok else 0.0)
        outcomes.observe("direct:fast-flaky", "search.error", 0.0 if ok else 1.0, {"status": "timeout"})
        outcomes.observe("direct:fast-flaky", "search.latency_ms", 200)
        outcomes.observe("direct:slow-solid", "search.ok", 1.0)
        outcomes.observe("direct:slow-solid", "search.error", 0.0)
        outcomes.observe("direct:slow-solid", "search.latency_ms", 9000)
    # category errors are query-specific: not held against the source
    for _ in range(5):
        outcomes.observe("direct:slow-solid", "search.error", 1.0, {"status": "category_error"})
    outcomes.observe("direct:few", "search.ok", 1.0)
    res = reliability.compute_all()
    scores = res["scores"]
    assert scores["direct:slow-solid"] > scores["direct:fast-flaky"]
    assert scores["direct:slow-solid"] > 0.85 and scores["direct:fast-flaky"] == 0.5
    assert scores["direct:few"] is None  # not enough observations: honest "unknown"
    meta = db.fetchone("SELECT meta FROM sources WHERE id='direct:slow-solid'")["meta"]["reliability"]
    assert meta["samples"] == 10 and meta["median_latency_ms"] == 9000
    # false-positive flags lower the score
    for _ in range(5):
        outcomes.observe("direct:slow-solid", "search.false_positive", 1.0)
    assert reliability.compute_all()["scores"]["direct:slow-solid"] < scores["direct:slow-solid"]


def test_torznab_private_flag_and_listing(admin: Any, viewer: Any, serve: Any) -> None:
    url = serve(mock_nz.create_app(apikey=TZ_KEY, protocol="torrent", private="1"))
    add_direct(admin, url, kind="torznab", key=TZ_KEY, label="Prive tracker", private=True)
    items = viewer.get("/api/v1/indexers", params={"protocol": "torrent"}).json()["items"]
    t = next(i for i in items if i["id"] == "direct:prive-tracker")
    assert t["private"] is True and t["kind"] == "torrent_indexer" and t["direct"]["api_key"] == "ingesteld"
    assert not [i for i in items if i["protocol"] != "torrent"]
