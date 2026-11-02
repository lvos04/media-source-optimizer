"""Optional header index: opt-in, whitelist, bounded XOVER, storage budget enforcement (stop + alert), TTL
purge,
runs-per-day limit, obfuscation detection/ratios, low-confidence search results."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

USER, PASSWORD = "u", "p-geheim"
GROUP = "alt.binaries.mso.test"


@pytest.fixture()
def nntp_srv(app: Any, admin: Any) -> Iterator[Any]:
    from msomocks import nntp

    srv = nntp.start(username=USER, password=PASSWORD)
    r = admin.post(
        "/api/v1/usenet/providers",
        json={
            "slug": "eweka",
            "label": "Eweka",
            "host": "127.0.0.1",
            "port": srv.port,
            "ssl": False,
            "username": USER,
            "password": PASSWORD,
        },
    )
    assert r.status_code == 200
    yield srv
    srv.stop()


def _enable(admin: Any, **extra: Any) -> Any:
    body = {"enabled": True, "acknowledge_limits": True, "groups": [GROUP], "provider": "eweka"} | extra
    return admin.put("/api/v1/usenet/headerindex/config", json=body)


@pytest.mark.parametrize(
    "subject,obfuscated,usable",
    [
        ('[3/45] - "Show.Name.S01E02.1080p.WEB.H264-GRP.part02.rar" yEnc (5/130)', False, True),
        ('"Movie.Title.2010.2160p.UHD.BluRay.x265-GRP.vol00+01.par2" yEnc (1/1)', False, True),
        ('[1/40] - "a9f3c2b1e8d7c6b5a4f3e2d1.part01.rar" yEnc (1/120)', True, False),
        ("Kd8sLq02nVx7Pz4T9mWq yEnc (12/90)", True, False),
        ('"3f2504e0-4f89-11d3-9a0c-0305e82c3301.7z.001" yEnc (1/50)', True, False),
        ('"holiday photos.zip" yEnc (1/3)', False, False),
    ],
)
def test_obfuscation_detection(subject: str, obfuscated: bool, usable: bool) -> None:
    from mso.features.usenet.headerindex.obfuscation import extract_name, is_obfuscated, is_usable

    name = extract_name(subject)
    assert is_obfuscated(name) is obfuscated, name
    assert is_usable(name) is usable, name


def test_opt_in_requires_acknowledgement_and_scope(admin: Any, operator: Any, nntp_srv: Any) -> None:
    r = admin.put(
        "/api/v1/usenet/headerindex/config", json={"enabled": True, "groups": [GROUP], "provider": "eweka"}
    )
    assert r.status_code == 422 and "geen indexer of provider vervangt" in r.json()["detail"]
    r = admin.put("/api/v1/usenet/headerindex/config", json={"enabled": True, "acknowledge_limits": True})
    assert r.status_code == 422  # no groups/provider yet
    r = admin.put("/api/v1/usenet/headerindex/config", json={"groups": ["alt.binaries.*"]})
    assert r.status_code == 422  # wildcards not allowed
    assert operator.put("/api/v1/usenet/headerindex/config", json={"groups": [GROUP]}).status_code == 403
    assert operator.post("/api/v1/usenet/headerindex/run").status_code == 409  # off
    r = _enable(admin)
    assert r.status_code == 200 and r.json()["config"]["enabled"] is True
    assert "vervangt geen indexer" in r.json()["disclaimer"]


def test_run_is_bounded_and_incremental(admin: Any, viewer: Any, nntp_srv: Any) -> None:
    from mso.features.usenet.headerindex import engine

    _enable(admin, max_headers_per_run=500)
    from mso import settings_registry as sr

    sr.put({"usenet.headerindex.max_headers_per_run": 500}, "test")
    res = engine.run()
    assert res["status"] == "done" and res["headers_stored"] == 300
    overs = [c for c in nntp_srv.state.commands if c.startswith(("OVER", "XOVER"))]
    assert overs == ["OVER 1-300"]
    # second run only asks for new articles (none) → no OVER at all
    nntp_srv.state.commands.clear()
    res2 = engine.run()
    assert res2["headers_stored"] == 0
    assert not [c for c in nntp_srv.state.commands if c.startswith("OVER")]
    st = viewer.get("/api/v1/usenet/headerindex").json()
    m = st["metrics"]
    assert m["headers"] == 300
    assert 0.3 <= m["obfuscated_subject_ratio"] <= 0.35
    # 100 unique obfuscated names + 20 episode names + 20 movie names → 40/140 usable
    assert m["releases"] == 140 and m["usable_ratio_new"] == round(40 / 140, 4)
    assert m["obfuscated_ratio"] == round(100 / 140, 4)
    assert st["usage"]["estimated_bytes"] > 0 and st["usage"]["budget_reached"] is False


def test_first_run_reads_only_newest_within_budget(app: Any, admin: Any, nntp_srv: Any) -> None:
    from mso import settings_registry as sr
    from mso.features.usenet.headerindex import engine

    _enable(admin)
    sr.put({"usenet.headerindex.max_headers_per_run": 500}, "test")
    # pretend the budget allows only 100 by using a two-group config where the second group doesn't exist
    sr.put({"usenet.headerindex.groups": [GROUP, "alt.binaries.bestaat.niet"]}, "test")
    res = engine.run()
    assert res["headers_stored"] == 250  # 500 / 2 groups → newest 250 of 300
    assert [c for c in nntp_srv.state.commands if c.startswith("OVER")] == ["OVER 51-300"]
    assert "411" not in str(res)


def test_storage_budget_stops_and_alerts(
    app: Any, admin: Any, db: Any, nntp_srv: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mso.features.usenet.headerindex import engine

    _enable(admin, max_storage_mb=16)
    monkeypatch.setattr(engine, "INDEX_FACTOR", 400.0)  # each row now counts ~100 KB → budget after ~150 rows
    res = engine.run()
    assert res["status"] == "budget_reached"
    assert 0 < res["headers_stored"] < 300
    u = engine.usage()
    assert u["estimated_bytes"] <= u["budget_bytes"]
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key='usenet.headerindex.budget'")
    assert rec is not None and rec["status"] == "open"
    # next run refuses to start while over budget
    nntp_srv.state.commands.clear()
    monkeypatch.setattr(engine, "INDEX_FACTOR", 10_000.0)
    assert engine.run()["status"] == "budget_reached"
    assert not [c for c in nntp_srv.state.commands if c.startswith("OVER")]
    # purge all → alert resolved
    r = admin.post("/api/v1/usenet/headerindex/purge", json={"scope": "all"})
    assert r.status_code == 200 and r.json()["usage"]["headers"] == 0
    assert (
        db.fetchone("SELECT status FROM recommendations WHERE dedupe_key='usenet.headerindex.budget'")[
            "status"
        ]
        == "resolved"
    )


def test_ttl_purge_and_runs_per_day(app: Any, admin: Any, operator: Any, db: Any, nntp_srv: Any) -> None:
    from mso import settings_registry as sr
    from mso.features.usenet.headerindex import engine

    _enable(admin)
    sr.put({"usenet.headerindex.max_runs_per_day": 1}, "test")
    assert engine.run()["status"] == "done"
    assert engine.run()["status"] == "skipped"  # max runs per day
    db.execute("UPDATE usenet_hi_headers SET posted_at = now() - interval '30 days' WHERE article_num <= 100")
    assert operator.post("/api/v1/usenet/headerindex/purge", json={"scope": "expired"}).status_code == 403
    r = admin.post("/api/v1/usenet/headerindex/purge", json={"scope": "expired"}).json()
    assert r["deleted"] == 100 and r["usage"]["headers"] == 200


def test_search_returns_low_confidence_header_results(
    app: Any, admin: Any, viewer: Any, nntp_srv: Any
) -> None:
    from mso.features.usenet.headerindex import engine, search_backend

    _enable(admin)
    engine.run()
    items = viewer.get("/api/v1/usenet/headerindex/search?q=Show Name S01E02").json()["items"]
    assert items and all(i["origin"] == "header_index" for i in items)
    first = items[0]
    assert first["message_id"].endswith("@mock") and first["confidence"] <= 0.3
    assert first["download_url"] is None and first["release_key"].startswith("mid:")
    assert first["source_id"] == "headerindex:own"
    # obfuscated posts never show up
    assert not viewer.get("/api/v1/usenet/headerindex/search?q=a9f3c2b1e8d7").json()["items"]

    class Q:
        q = "Movie Title 2160p"
        limit = 5
        protocols = {"usenet"}

    assert 0 < len(search_backend(Q())) <= 5
    Q.protocols = {"torrent"}
    assert search_backend(Q()) == []


def test_registered_as_search_backend(app: Any) -> None:
    pytest.importorskip("mso.features.search.backends")
    from mso.features.search.backends import BACKENDS
    from mso.features.usenet.headerindex import register_search_backend

    assert register_search_backend() is True
    b = BACKENDS["headerindex"]
    assert b.source_id == "headerindex:own" and b.protocols == {"usenet"}


def test_disabled_index_returns_nothing(app: Any) -> None:
    from mso.features.usenet.headerindex import engine, search_backend

    assert engine.search("anything") == []
    search_mod = pytest.importorskip("mso.features.search.models")
    with pytest.raises(search_mod.SourceError) as exc:
        search_backend(q="anything")
    assert exc.value.status == "skipped_scope"
    assert engine.run()["status"] == "skipped"
