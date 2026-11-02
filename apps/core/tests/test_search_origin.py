"""Slice A — anti-loop / origin graph. Regression: a mock Prowlarr that has MSO registered as an indexer
calls MSO
back; MSO (even when allowed to use Prowlarr as a Newznab backend) must never query that Prowlarr again.
Covers detection by consumer key, by User-Agent, by in-flight question matching, by explicit X-MSO-Trace
header,
hop limits, and the exclusion of MSO's own registration from Prowlarr searches."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from msomocks import newznab as mock_nz
from msomocks import prowlarr as mock_prowlarr
from slice_a_helpers import NZ_KEY, PROWLARR_KEY, add_direct, configure_prowlarr, report, search, set_settings

from mso.features.search import origin


# ------------------------------------------------------------------ unit
def test_trace_header_roundtrip_and_sanitising() -> None:
    t = origin.Trace(trace_id="abc123", hop_count=1, visited=["prowlarr", "mso"])
    p = origin.parse_header(t.header())
    assert p and p.trace_id == "abc123" and p.hop_count == 1 and p.visited == ["prowlarr", "mso"]
    evil = origin.parse_header("x<script>;hop=999;visited=a b,ok-node,<bad>")
    assert evil and evil.trace_id == "xscript" and evil.hop_count == 99 and evil.visited == ["ok-node"]
    assert origin.parse_header("") is None


def test_start_refuses_cycles_and_hop_limit(app: Any) -> None:
    assert origin.start(None).visited == ["mso"]
    assert origin.start(["prowlarr"]).visited == ["prowlarr", "mso"]
    with pytest.raises(origin.LoopRefused) as e:
        origin.start(["mso", "prowlarr"])
    assert e.value.code == "cycle_detected"
    with pytest.raises(origin.LoopRefused) as e:
        origin.start(["a", "b", "c"])
    assert e.value.code == "hop_limit"


def test_inbound_caller_detection(app: Any) -> None:
    t = origin.inbound(header=None, consumer_node="prowlarr", user_agent="curl", fingerprint="x")
    assert t.visited == ["prowlarr", "mso"] and t.hop_count == 1 and t.caller == "prowlarr"
    t = origin.inbound(
        header=None, consumer_node="external", user_agent="Prowlarr/1.2 (linux)", fingerprint="x"
    )
    assert set(t.visited) == {"external", "prowlarr", "mso"}
    with origin.outbound("prowlarr", "dune|||||||"):
        t = origin.inbound(
            header=None, consumer_node="external", user_agent="Go-http-client", fingerprint="dune|||||||"
        )
        assert "prowlarr" in t.visited
    t = origin.inbound(
        header=None, consumer_node="external", user_agent="Go-http-client", fingerprint="dune|||||||"
    )
    assert "prowlarr" not in t.visited
    with pytest.raises(origin.LoopRefused) as e:
        origin.inbound(
            header="t1;hop=1;visited=mso,prowlarr", consumer_node=None, user_agent=None, fingerprint=None
        )
    assert e.value.code == "cycle_detected"
    with pytest.raises(origin.LoopRefused) as e:
        origin.inbound(
            header="t1;hop=2;visited=other-mso", consumer_node=None, user_agent=None, fingerprint=None
        )
    assert e.value.code == "hop_limit"


def test_graph_reachability_blocks_targets_that_call_back() -> None:
    g = origin.Graph()
    g.hubs.add("prowlarr")
    for s, d in (
        ("mso", "prowlarr"),
        ("prowlarr", "prowlarr:1"),
        ("prowlarr", "prowlarr:9"),
        ("prowlarr:9", "mso"),
        ("mso", "direct:proxy"),
        ("direct:proxy", "prowlarr"),
        ("mso", "direct:plain"),
    ):
        g.add(s, d)
    ui = origin.Trace(visited=["mso"])
    assert origin.check_target(g, ui, "prowlarr").allowed
    allowed, excluded = origin.hub_children(g, ui, "prowlarr")
    assert allowed == ["prowlarr:1"] and excluded[0][0] == "prowlarr:9"
    from_prowlarr = origin.Trace(visited=["prowlarr", "mso"], hop_count=1)
    assert origin.check_target(g, from_prowlarr, "prowlarr").reason == "cycle_detected"
    d = origin.check_target(g, from_prowlarr, "direct:proxy")
    assert not d.allowed and d.path == ["direct:proxy", "prowlarr"]
    assert origin.check_target(g, from_prowlarr, "direct:plain").allowed
    assert any(set(c) >= {"mso", "prowlarr"} for c in g.cycles())


# ------------------------------------------------------------------ integration over real HTTP
@pytest.fixture()
def loop_setup(app: Any, admin: Any, serve: Any) -> dict[str, Any]:
    """MSO served for real; mock Prowlarr with MSO's Newznab endpoint registered as an indexer (proxy)."""
    from mso.features.newznab_server import keys

    mso_url = serve(app)
    set_settings(
        {"newznab.public_url": mso_url, "newznab.backends": ["direct", "prowlarr"], "search.timeout_s": 10}
    )
    nz_url = serve(mock_nz.create_app(apikey=NZ_KEY))
    add_direct(admin, nz_url, label="Direct NZB")
    prowlarr = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    configure_prowlarr(serve(prowlarr))
    key = keys.default_key()
    prowlarr.state.data["indexers"].append(
        {
            "id": 20,
            "name": "Mijn optimizer",
            "implementation": "Newznab",
            "protocol": "usenet",
            "privacy": "private",
            "enable": True,
            "tags": [1, 2],
            "fields": mock_prowlarr._fields(mso_url + "/newznab", "/api", key),
        }
    )
    return {"mso_url": mso_url, "prowlarr": prowlarr, "key": key}


def test_no_loop_when_prowlarr_calls_mso_back(viewer: Any, loop_setup: dict[str, Any]) -> None:
    prowlarr = loop_setup["prowlarr"]
    res = search(viewer, q="Expanse", category="tv")
    # MSO asked Prowlarr once; Prowlarr proxied to MSO once; MSO did NOT ask Prowlarr again
    assert prowlarr.state.search_calls == 1
    assert len(prowlarr.state.callbacks) == 1 and prowlarr.state.max_active == 1
    assert report(res, "prowlarr")["status"] == "ok"
    assert report(res, "direct:direct-nzb")["status"] == "ok"
    # results relayed by Prowlarr from MSO are there, but nothing recursed
    assert any(i["source_id"] == "prowlarr:20" for i in res["items"])


def test_inbound_from_prowlarr_never_queries_prowlarr(loop_setup: dict[str, Any]) -> None:
    prowlarr = loop_setup["prowlarr"]
    r = httpx.get(
        loop_setup["mso_url"] + "/newznab/api",
        params={"t": "search", "q": "Expanse", "cat": "5000", "apikey": loop_setup["key"]},
        headers={"User-Agent": mock_prowlarr.UA},
        timeout=30,
    )
    assert r.status_code == 200 and b"<rss" in r.content and b"Expanse" in r.content
    assert prowlarr.state.search_calls == 0


def test_unknown_caller_detected_by_inflight_question(
    viewer: Any, admin: Any, loop_setup: dict[str, Any]
) -> None:
    """Worst case: Prowlarr uses a key MSO doesn't know as Prowlarr's and a generic User-Agent."""
    prowlarr = loop_setup["prowlarr"]
    other = admin.post("/api/v1/newznab/consumers", json={"label": "Onbekend", "node": "external"}).json()
    prowlarr.state.data["indexers"][-1]["fields"] = mock_prowlarr._fields(
        loop_setup["mso_url"] + "/newznab", "/api", other["apikey"]
    )
    prowlarr.state.faults["user_agent"] = "Go-http-client/1.1"
    search(viewer, q="Expanse", category="tv")
    assert prowlarr.state.search_calls == 1 and prowlarr.state.max_active == 1
    assert len(prowlarr.state.callbacks) == 1


def test_own_registration_excluded_after_sync(
    viewer: Any, operator: Any, loop_setup: dict[str, Any], db: Any
) -> None:
    prowlarr = loop_setup["prowlarr"]
    assert operator.post("/api/v1/indexers/prowlarr/sync").status_code == 200
    src = db.fetchone("SELECT * FROM sources WHERE id='prowlarr:20'")
    assert src["meta"]["is_mso"] is True and src["enabled"] is False
    res = search(viewer, q="Expanse", category="tv")
    assert prowlarr.state.callbacks == []  # Prowlarr was asked with explicit indexerIds, MSO's not among them
    call = next(c for c in prowlarr.state.calls if c["path"] == "/api/v1/search")
    assert call["query"].get("indexerIds") != "20"
    rep = report(res, "prowlarr:20")
    assert rep["status"] == "skipped_cycle" and "cycle_detected" in rep["message"]
    graph = viewer.get("/api/v1/search/origin-graph").json()
    assert {"src": "prowlarr:20", "dst": "mso", "note": "MSO-registratie in Prowlarr"} in graph["edges"]
    assert graph["cycles"] and "Prowlarr" in graph["cycles"][0]["resolution"]


def test_inbound_trace_header_refusals(loop_setup: dict[str, Any]) -> None:
    url, key = loop_setup["mso_url"] + "/torznab/api", loop_setup["key"]
    r = httpx.get(
        url,
        params={"t": "search", "q": "x", "apikey": key},
        headers={"X-MSO-Trace": "t9;hop=1;visited=mso"},
        timeout=10,
    )
    assert r.status_code == 508 and r.headers["X-MSO-Refusal"] == "cycle_detected"
    assert b'code="900"' in r.content and b"cycle_detected" in r.content
    r = httpx.get(
        url,
        params={"t": "search", "q": "x", "apikey": key},
        headers={"X-MSO-Trace": "t9;hop=4;visited=elders"},
        timeout=10,
    )
    assert r.status_code == 508 and r.headers["X-MSO-Refusal"] == "hop_limit"
    assert loop_setup["prowlarr"].state.search_calls == 0


def test_direct_indexer_that_is_prowlarr_is_skipped_for_prowlarr_callers(
    app: Any, admin: Any, serve: Any
) -> None:
    """A 'direct' Newznab URL that is really Prowlarr's per-indexer proxy is a hidden edge back to
    Prowlarr."""
    from mso.features.search import service
    from mso.features.search.models import SearchQuery

    prowlarr = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    p_url = serve(prowlarr)
    configure_prowlarr(p_url)
    add_direct(admin, p_url, label="Prowlarr proxy", api_path="/1/api")
    tr = origin.inbound(header=None, consumer_node="prowlarr", user_agent=None, fingerprint="q")
    res = service.search(
        SearchQuery(q="Expanse", kind="search"), origin_trace=tr, allowed_kinds={"direct", "prowlarr"}
    )
    st = {s.source_id: s for s in res.sources}
    assert st["prowlarr"].status == "skipped_cycle"
    assert (
        st["direct:prowlarr-proxy"].status == "skipped_cycle"
        and "prowlarr" in st["direct:prowlarr-proxy"].message
    )
    assert prowlarr.state.search_calls == 0


def test_explicit_origin_edges_admin_only(viewer: Any, admin: Any, app: Any) -> None:
    body = {"src": "direct:x", "dst": "prowlarr", "note": "is eigenlijk Prowlarr"}
    assert viewer.post("/api/v1/search/origin-graph/edges", json=body).status_code == 403
    g = admin.post("/api/v1/search/origin-graph/edges", json=body).json()
    assert {"src": "direct:x", "dst": "prowlarr", "note": "is eigenlijk Prowlarr"} in g["edges"]
    assert admin.post("/api/v1/search/origin-graph/edges", json={"src": "a", "dst": "a"}).status_code == 422
    assert (
        admin.delete(
            "/api/v1/search/origin-graph/edges", params={"src": "direct:x", "dst": "prowlarr"}
        ).status_code
        == 200
    )
