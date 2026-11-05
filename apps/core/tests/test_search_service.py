"""Slice A — search aggregator: four-category matrix, anime/normal scope separation, per-source status
(timeout/error/category_error/auth/empty distinguished), dedupe by identity, language filter, pagination,
Prowlarr search, compare/flag, send-to-client (idempotent, blacklist, absent client), pluggable backends."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from msomocks import newznab as mock_nz
from msomocks import prowlarr as mock_prowlarr
from slice_a_helpers import (
    NZ_KEY,
    PROWLARR_KEY,
    TZ_KEY,
    add_direct,
    configure_prowlarr,
    report,
    search,
    set_settings,
)


@pytest.fixture()
def indexers(admin: Any, serve: Any) -> dict[str, Any]:
    nz = mock_nz.create_app(apikey=NZ_KEY)
    tz = mock_nz.create_app(apikey=TZ_KEY, protocol="torrent")
    nz_url, tz_url = serve(nz), serve(tz)
    add_direct(admin, nz_url, label="NZB mix")
    add_direct(admin, tz_url, kind="torznab", key=TZ_KEY, label="Torznab mix")
    return {"nz": nz, "tz": tz, "nz_url": nz_url, "tz_url": tz_url}


MATRIX = {
    "tv": ({"The.Expanse.S02E05.1080p.WEB-DL.DDP5.1.H.264-NTb"}, "Frieren"),
    "tv-anime": ({"[SubsPlease] Sousou no Frieren - 05 (1080p) [A1B2C3D4].mkv"}, "Expanse"),
    "movies": ({"Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos-FGT"}, "Suzume"),
    "movies-anime": ({"[Judas] Suzume (2022) [BD 1080p HEVC x265 10bit][Dual-Audio][Eng-Subs]"}, "Dune"),
}


@pytest.mark.parametrize("category", list(MATRIX))
def test_four_category_matrix(viewer: Any, indexers: dict[str, Any], category: str) -> None:
    must, never = MATRIX[category]
    res = search(viewer, category=category)
    titles = {i["title"] for i in res["items"]}
    assert must <= titles, titles
    assert not [t for t in titles if never in t]
    assert {i["media_category"] for i in res["items"]} == {category}
    for i in res["items"]:  # identity per protocol, never title
        assert i["release_key"].startswith("btih:" if i["protocol"] == "torrent" else "guid:")
    # the Newznab categories asked upstream match the MediaGuard mapping
    sent = indexers["nz"].state.calls[-1]["params"]["cat"]
    from mso.domain import categories as C

    assert set(int(c) for c in sent.split(",")) <= set(C.newznab_for(category))


def test_anime_and_normal_scopes_are_kept_apart(admin: Any, viewer: Any, serve: Any) -> None:
    url = serve(mock_nz.create_app(apikey=NZ_KEY))
    add_direct(admin, url, label="Alleen anime", scopes=["anime"])
    add_direct(admin, url, label="Alleen normaal", scopes=["normal"])
    res = search(viewer, category="tv")
    assert report(res, "direct:alleen-anime")["status"] == "skipped_scope"
    assert report(res, "direct:alleen-normaal")["status"] == "ok"
    res = search(viewer, category="tv-anime")
    assert report(res, "direct:alleen-normaal")["status"] == "skipped_scope"
    assert {i["source_id"] for i in res["items"]} == {"direct:alleen-anime"}
    # free search: anime results from the anime-only indexer are classified as anime
    res = search(viewer, q="Frieren")
    assert {i["media_category"] for i in res["items"]} == {"tv-anime"}


def test_tvsearch_season_episode_and_ids(viewer: Any, indexers: dict[str, Any]) -> None:
    res = search(viewer, category="tv", q="Expanse", season=2, episode=6)
    assert [i["title"] for i in res["items"] if i["protocol"] == "usenet"] == [
        "The.Expanse.S02E06.720p.HDTV.x264-KILLERS"
    ]
    params = indexers["nz"].state.calls[-1]["params"]
    assert params["t"] == "tvsearch" and params["season"] == "2" and params["ep"] == "6"
    res = search(viewer, category="movies", imdb="tt15239678")
    assert indexers["nz"].state.calls[-1]["params"]["imdbid"] == "15239678"
    assert all("Dune" in i["title"] for i in res["items"]) and res["items"]
    # absolute anime numbering
    res = search(viewer, category="tv-anime", q="Frieren", absolute=6)
    assert {i["title"] for i in res["items"] if i["protocol"] == "usenet"} == {
        "[Erai-raws] Sousou no Frieren - 06 [1080p][Multiple Subtitle]"
    }


def test_falls_back_to_search_when_tvsearch_unsupported(admin: Any, viewer: Any, serve: Any) -> None:
    m = mock_nz.create_app(apikey=NZ_KEY, no_tvsearch="1")
    add_direct(admin, serve(m), label="Basis")
    res = search(viewer, category="tv", q="Expanse", season=2, episode=5)
    assert report(res, "direct:basis")["status"] == "ok"
    p = m.state.calls[-1]["params"]
    assert p["t"] == "search" and "S02E05" in p["q"]


@pytest.mark.parametrize(
    "fault,status",
    [
        ("error500", "error"),
        ("malformed", "error"),
        ("category_error", "category_error"),
        ("empty", "empty"),
        ("ratelimit", "rate_limited"),
        ("auth", "auth_error"),
    ],
)
def test_status_distinguishes_failures_from_empty(
    viewer: Any, indexers: dict[str, Any], fault: str, status: str
) -> None:
    indexers["nz"].state.faults[fault] = "fail" if fault == "auth" else "1"
    res = search(viewer, category="tv", q="Expanse")
    rep = report(res, "direct:nzb-mix")
    assert rep["status"] == status, rep
    assert report(res, "direct:torznab-mix")["status"] == "ok"  # other sources unaffected
    assert NZ_KEY not in str(res)


def test_timeout_is_reported_as_timeout(viewer: Any, indexers: dict[str, Any], db: Any) -> None:
    set_settings({"search.timeout_s": 2})
    indexers["nz"].state.faults["timeout"] = "3.5"
    res = search(viewer, q="Expanse")
    rep = report(res, "direct:nzb-mix")
    assert rep["status"] == "timeout" and rep["latency_ms"] >= 1900
    assert report(res, "direct:torznab-mix")["status"] == "ok"
    row = db.fetchone(
        "SELECT labels FROM observations WHERE subject='direct:nzb-mix' AND metric='search.error' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert row["labels"]["status"] == "timeout"


def test_observations_written_per_source(viewer: Any, indexers: dict[str, Any], db: Any) -> None:
    search(viewer, q="Expanse")
    metrics = {
        r["metric"] for r in db.fetchall("SELECT metric FROM observations WHERE subject='direct:nzb-mix'")
    }
    assert {"search.latency_ms", "search.ok", "search.empty", "search.error"} <= metrics


def test_dedupe_by_infohash_not_title(admin: Any, viewer: Any, indexers: dict[str, Any]) -> None:
    add_direct(admin, indexers["tz_url"], kind="torznab", key=TZ_KEY, label="Torznab kopie")
    add_direct(admin, indexers["nz_url"], label="NZB kopie")
    res = search(viewer, q="Expanse", category="tv")
    torrents = [i for i in res["items"] if i["protocol"] == "torrent"]
    assert len({i["release_key"] for i in torrents}) == len(torrents)
    assert all(
        set(i["also_from"]) | {i["source_id"]} == {"direct:torznab-mix", "direct:torznab-kopie"}
        for i in torrents
    )
    usenet = [i for i in res["items"] if i["protocol"] == "usenet"]
    # same title from two different NZB indexers is NOT merged (no title-only dedupe)
    assert len(usenet) == 2 * len({i["title"] for i in usenet})


def test_language_filter(viewer: Any, indexers: dict[str, Any]) -> None:
    titles = {i["title"] for i in search(viewer, q="Expanse", category="tv", season=2, episode=5)["items"]}
    assert "The.Expanse.S02E05.German.1080p.WEB.x264-WvF" in titles
    set_settings({"search.excluded_languages": ["de"]})
    res = search(viewer, q="Expanse", category="tv", season=2, episode=5)
    assert "The.Expanse.S02E05.German.1080p.WEB.x264-WvF" not in {i["title"] for i in res["items"]}
    assert "weggefilterd" in report(res, "direct:nzb-mix")["message"]
    set_settings({"search.excluded_languages": [], "search.required_languages": ["en"]})
    res = search(viewer, q="Expanse", category="tv", season=2, episode=5)
    assert any("taal_onbekend" in i["false_positive_flags"] for i in res["items"])


def test_false_positive_flags_and_confidence(viewer: Any, indexers: dict[str, Any]) -> None:
    res = search(viewer, q="Expanse", category="tv", season=3)
    # the mock ignores nothing: season filter returns none → ask without season but flag mismatches
    res = search(viewer, q="Expanse", category="tv", tvdb=280619)
    assert res["items"] and all(i["confidence"] > 0.5 for i in res["items"])
    from mso.domain.release import NormalizedRelease, parse_title
    from mso.features.search import service
    from mso.features.search.models import SearchQuery

    r = NormalizedRelease(
        protocol="usenet",
        source_id="x",
        origin="direct",
        title="Show.S03E01.1080p.WEB-GRP",
        guid="g",
        tvdb_id=1,
        parsed=parse_title("Show.S03E01.1080p.WEB-GRP"),
    )
    service._flags(r, SearchQuery(q="Show", season=2, episode=1, tvdb_id=2))
    assert {"seizoen_wijkt_af", "tvdb_wijkt_af"} <= set(r.false_positive_flags) and r.confidence < 0.4


def test_pagination(viewer: Any, indexers: dict[str, Any]) -> None:
    full = search(viewer, q="Expanse", limit=50)
    assert full["total"] >= 4
    p1 = search(viewer, q="Expanse", limit=2, offset=0)
    p2 = search(viewer, q="Expanse", limit=2, offset=2)
    assert p1["total"] == p2["total"] == full["total"]
    assert len(p1["items"]) == 2 and not {i["release_key"] for i in p1["items"]} & {
        i["release_key"] for i in p2["items"]
    }
    assert viewer.get("/api/v1/search", params={"q": "x", "limit": 1000}).status_code == 422


def test_search_requires_input_and_role(client: Any, viewer: Any) -> None:
    assert client.get("/api/v1/search", params={"q": "x"}).status_code == 401
    assert viewer.get("/api/v1/search").status_code == 422
    assert viewer.get("/api/v1/search", params={"q": "x", "category": "anime"}).status_code == 422


def test_prowlarr_search_normalised_without_secrets(viewer: Any, operator: Any, serve: Any, db: Any) -> None:
    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    mock.state.data["health"] = []  # TorrentLeech healthy here (failing indexers: test_search_relevance)
    configure_prowlarr(serve(mock))
    operator.post("/api/v1/indexers/prowlarr/sync")
    r = viewer.get("/api/v1/search", params={"q": "Expanse", "category": "tv"})
    res = r.json()
    assert PROWLARR_KEY not in r.text
    assert report(res, "prowlarr")["status"] == "ok"
    srcs = {i["source_id"] for i in res["items"]}
    assert srcs <= {"prowlarr:1", "prowlarr:4"}  # anime-only Nyaa (2) and anime-tagged… never for 'tv'
    call = next(c for c in reversed(mock.state.calls) if c["path"] == "/api/v1/search")
    assert call["trace"] and ";hop=0;visited=mso" in call["trace"]
    from mso.domain import categories as C

    # ids passed explicitly (scope filter: NZBgeek=normal, TorrentLeech=untagged→both); AnimeTosho tagged
    # normal
    priv = [i for i in res["items"] if i["source_id"] == "prowlarr:4"]
    assert priv and all(i["private"] for i in priv) and all(i["magnet_url"] is None for i in priv)
    assert all("download_url" not in i for i in res["items"])
    _ = C
    # anime search uses only anime-scoped Prowlarr indexers (+ untagged)
    res = search(viewer, category="tv-anime")
    assert {i["source_id"] for i in res["items"]} <= {"prowlarr:2", "prowlarr:4"}
    assert all(i["media_category"] == "tv-anime" for i in res["items"])


@pytest.mark.parametrize(
    "fault,status",
    [
        ("timeout", "timeout"),
        ("category_error", "category_error"),
        ("error500", "error"),
        ("malformed", "error"),
        ("auth", "auth_error"),
    ],
)
def test_prowlarr_failure_classification(viewer: Any, serve: Any, fault: str, status: str) -> None:
    set_settings({"search.timeout_s": 2, "search.prowlarr_auto_retry": False})
    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    configure_prowlarr(serve(mock))
    mock.state.faults[fault] = {"timeout": "3", "auth": "fail"}.get(fault, "1")
    res = search(viewer, q="Expanse", category="tv")
    assert report(res, "prowlarr")["status"] == status


# ------------------------------------------------------------------ compare / flag
def test_compare_candidates_and_flag(viewer: Any, operator: Any, indexers: dict[str, Any], db: Any) -> None:
    res = search(viewer, q="Dune", category="movies")
    tokens = [i["token"] for i in res["items"][:2]]
    r = viewer.post(
        "/api/v1/search/compare",
        json={
            "tokens": tokens,
            "requirements": {"media_category": "movies", "min_resolution": 2160, "require_hdr": ["dv"]},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "resolution" in body["differs"]
    by_res = {c["fields"]["resolution"]: c for c in body["candidates"]}
    assert by_res[2160]["rejections"] == [] and by_res[1080]["rejections"]
    assert viewer.post("/api/v1/search/compare", json={"tokens": ["onbekend", "x"]}).status_code == 404
    assert viewer.post("/api/v1/search/flag", json={"token": tokens[0]}).status_code == 403
    assert operator.post("/api/v1/search/flag", json={"token": tokens[0], "reason": "nep"}).status_code == 200
    assert db.scalar("SELECT count(*) FROM observations WHERE metric='search.false_positive'") == 1


# ------------------------------------------------------------------ send to client
class FakeSab:
    added: list[tuple[bytes, str, str]] = []

    @classmethod
    def from_config(cls) -> FakeSab:
        return cls()

    def add_nzb(self, data: bytes, filename: str, *, category: str) -> str:
        FakeSab.added.append((data, filename, category))
        return "SABnzbd_nzo_abc"

    def categories(self) -> list[str]:
        return ["tv", "tv-anime", "movies", "movies-anime"]


class FakeQbit:
    added: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls) -> FakeQbit:
        return cls()

    def add_torrent(self, **kw: Any) -> None:
        FakeQbit.added.append(kw)

    def categories(self) -> list[str]:
        return ["tv", "movies"]


@pytest.fixture()
def fake_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSab.added, FakeQbit.added = [], []
    monkeypatch.setitem(sys.modules, "mso.connectors.sabnzbd", types.SimpleNamespace(SabClient=FakeSab))
    monkeypatch.setitem(sys.modules, "mso.connectors.qbittorrent", types.SimpleNamespace(QbitClient=FakeQbit))


def test_send_nzb_idempotent_and_audited(
    viewer: Any, operator: Any, indexers: dict[str, Any], fake_clients: None, db: Any
) -> None:
    res = search(viewer, q="Expanse", category="tv", protocol="usenet")
    item = res["items"][0]
    assert (
        viewer.post("/api/v1/search/send", json={"token": item["token"], "category": "tv"}).status_code == 403
    )
    r = operator.post("/api/v1/search/send", json={"token": item["token"], "category": "tv"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent" and r.json()["send"]["client_ref"] == "SABnzbd_nzo_abc"
    data, filename, cat = FakeSab.added[0]
    assert data.startswith(b"<?xml") and b"<nzb" in data and cat == "tv" and filename.endswith(".nzb")
    again = operator.post("/api/v1/search/send", json={"token": item["token"], "category": "tv"}).json()
    assert again["status"] == "already_sent" and len(FakeSab.added) == 1
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='search.sent'") == 1
    assert NZ_KEY not in str(db.fetchall("SELECT * FROM audit_log")) + str(
        db.fetchall("SELECT * FROM search_sends")
    )
    # protocol mismatch refused
    r = operator.post(
        "/api/v1/search/send", json={"token": item["token"], "client": "qbittorrent", "category": "tv"}
    )
    assert r.status_code == 422
    targets = viewer.get("/api/v1/search/send-targets").json()["items"]
    assert {t["client"]: t["available"] for t in targets} == {"sabnzbd": True, "qbittorrent": True}


def test_send_torrent_uses_file_and_keeps_private(
    viewer: Any, operator: Any, indexers: dict[str, Any], fake_clients: None
) -> None:
    item = search(viewer, q="Dune", category="movies", protocol="torrent")["items"][0]
    r = operator.post("/api/v1/search/send", json={"token": item["token"], "category": "movies"})
    assert r.status_code == 200, r.text
    kw = FakeQbit.added[0]
    assert kw["category"] == "movies" and kw["data"].startswith(b"d8:announce")
    assert r.json()["send"]["client_ref"] == item["release_key"].split(":", 1)[1]


def test_send_refused_when_blacklisted_or_client_absent(
    viewer: Any, operator: Any, indexers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = search(viewer, q="Expanse", category="tv", protocol="usenet")["items"][0]
    monkeypatch.setitem(sys.modules, "mso.connectors.sabnzbd", None)  # import → ImportError
    r = operator.post("/api/v1/search/send", json={"token": item["token"], "category": "tv"})
    assert r.status_code == 409 and r.json()["detail"] == "downloadclient niet beschikbaar"
    from mso.features.search import service

    monkeypatch.setattr(service, "is_blacklisted", lambda key: True)
    r = operator.post("/api/v1/search/send", json={"token": item["token"], "category": "tv"})
    assert r.status_code == 409 and "blokkeerlijst" in r.json()["detail"]
    assert (
        operator.post("/api/v1/search/send", json={"token": "verlopen-token", "category": "tv"}).status_code
        == 404
    )


def test_blacklist_module_is_respected_when_present(
    viewer: Any, indexers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    blocked: set[str] = set()
    fake = types.SimpleNamespace(is_blacklisted=lambda key: key in blocked)
    try:
        pkg: Any = importlib.import_module("mso.features.recovery")
    except ImportError:
        pkg = types.ModuleType("mso.features.recovery")
        monkeypatch.setitem(sys.modules, "mso.features.recovery", pkg)
    monkeypatch.setitem(sys.modules, "mso.features.recovery.blacklist", fake)
    monkeypatch.setattr(pkg, "blacklist", fake, raising=False)
    first = search(viewer, q="Dune", category="movies", protocol="torrent")["items"][0]
    blocked.add(first["release_key"])
    again = search(viewer, q="Dune", category="movies", protocol="torrent")["items"]
    flagged = next(i for i in again if i["release_key"] == first["release_key"])
    assert (
        "geblokkeerd" in flagged["false_positive_flags"] and again[-1]["release_key"] == first["release_key"]
    )


# ------------------------------------------------------------------ pluggable backends
def test_pluggable_backend(viewer: Any, app: Any) -> None:
    from mso.domain.release import NormalizedRelease, parse_title
    from mso.features.search.backends import SourceError, register_backend, unregister_backend

    def headerindex(query: Any) -> list[NormalizedRelease]:
        t = "Some.Show.S01E01.1080p.WEB-DL-GRP"
        return [
            NormalizedRelease(
                protocol="usenet",
                source_id="headerindex:local",
                origin="header_index",
                title=t,
                guid="<abc@news>",
                message_id="<abc@news>",
                categories=[5040],
                parsed=parse_title(t),
            )
        ]

    def broken(query: Any) -> list[NormalizedRelease]:
        raise SourceError("timeout", "NNTP-server reageert niet")

    register_backend("headerindex:local", headerindex, label="Eigen header-index")
    register_backend("headerindex:kapot", broken, label="Kapotte index")
    try:
        res = search(viewer, q="Some Show", category="tv")
        assert res["items"][0]["release_key"] == "mid:abc@news"
        assert report(res, "headerindex:local")["status"] == "ok"
        assert report(res, "headerindex:kapot")["status"] == "timeout"
        res = search(viewer, q="x", category="tv", protocol="torrent")
        assert not [s for s in res["sources"] if s["source_id"].startswith("headerindex")]
    finally:
        unregister_backend("headerindex:local")
        unregister_backend("headerindex:kapot")
