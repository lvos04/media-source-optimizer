"""Slice A — search relevance and indexer scope (test report 2026-11-04: P7, P9, D1, D2):

* title similarity is dominant; series episodes never rank (or appear) in film categories;
* untagged Prowlarr indexers get scopes derived from their categories; non-library indexers and indexers that
  Prowlarr's health check names as broken are skipped and reported per indexer;
* the source report breaks Prowlarr down per indexer while keeping the aggregate row;
* freeleech only on private trackers with an explicit factor/flag.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from msomocks import prowlarr as mock_prowlarr
from slice_a_helpers import PROWLARR_KEY, configure_prowlarr, report, search, set_settings

from mso.domain.release import NormalizedRelease, is_episodic, parse_title, title_part, title_similarity

DBZ = "Dragon Ball Z S01E32 Spirit Bomb Away 1080p BluRay 10bit EAC3 5 1 x265-iVy"
GIBIATE = "[Anime Time] Gibiate - S01E01 - Spirited Away [1080p][HEVC 10bit x265][Multi-Sub][Opus] [00057b92]"
FILMS = [
    "[HR] Spirited Away (2001) [Disney BluRay 1080p x265 Dual Audio] HR DR",
    "Spirited Away 2001 1080p bdrip x265 5 1 AAC FINKLEROY",
    "Spirited.Away.2001.1080p.DUAL.BluRay.HEVC.x265.5.1-BONE",
    "[AnimeRoll.Net] Spirited Away (2001) [1080p x265 HEVC 10bit BluRay Dual Audio AAC 5.1]",
]


# ------------------------------------------------------------------ pure helpers
def test_title_part_and_similarity_for_the_reported_titles() -> None:
    assert title_part(DBZ) == "Dragon Ball Z"
    assert title_part(GIBIATE) == "Gibiate"
    for t in FILMS:
        assert title_part(t) == "Spirited Away", t
        assert title_similarity("Spirited Away", t) == 1.0
    # the episode title after the numbering never counts as the name
    assert title_similarity("Spirited Away", DBZ) == 0.0
    assert title_similarity("Spirited Away", GIBIATE) == 0.0
    assert title_similarity("spirited away 2001", FILMS[1]) == 1.0  # year in the query is not a title token
    assert title_similarity("The Expanse", "The.Expanse.S02E05.1080p.WEB-DL.DDP5.1.H.264-NTb") == 1.0
    assert 0.8 <= title_similarity("Frieren", "[SubsPlease] Sousou no Frieren - 05 (1080p) [A1B2C3D4].mkv") < 1
    assert title_similarity("Dune Part Two", "Dune.2021.2160p.WEB-DL") < 0.5
    assert title_similarity(None, FILMS[0]) is None
    assert title_part("1917.2019.1080p.BluRay.x264-SPARKS") == "1917"
    assert is_episodic(parse_title(DBZ)) and is_episodic(parse_title(GIBIATE))
    assert not any(is_episodic(parse_title(t)) for t in FILMS)


def _rel(title: str, cats: list[int], guid: str, **kw: Any) -> NormalizedRelease:
    return NormalizedRelease(
        protocol="usenet",
        source_id="headerindex:rel",
        origin="header_index",
        title=title,
        guid=guid,
        message_id=f"<{guid}@x>",
        categories=cats,
        parsed=parse_title(title),
        **kw,
    )


@pytest.fixture()
def backend(app: Any) -> Any:
    from mso.features.search.backends import register_backend, unregister_backend

    holder: dict[str, list[NormalizedRelease]] = {"items": []}
    register_backend("headerindex:rel", lambda q: list(holder["items"]), label="Relevantie-test")
    yield holder
    unregister_backend("headerindex:rel")


def test_spirited_away_in_anime_films_ranks_the_film_first(viewer: Any, backend: Any) -> None:
    backend["items"] = [
        _rel(DBZ, [5070], "dbz"),
        _rel(GIBIATE, [5070], "gib"),
        _rel("[Erai-raws] Spirited Away - 01 [1080p]", [5070], "abs"),  # absolute numbering: demoted
        *[_rel(t, [2040, 5070], f"film{i}") for i, t in enumerate(FILMS)],
    ]
    res = search(viewer, q="Spirited Away", category="movies-anime")
    titles = [i["title"] for i in res["items"]]
    assert DBZ not in titles and GIBIATE not in titles  # S01E.. is never a film
    assert titles[: len(FILMS)] == [t for t in titles if t in FILMS] and set(titles[: len(FILMS)]) == set(FILMS)
    films = [i for i in res["items"] if i["title"] in FILMS]
    assert all(i["confidence"] >= 0.8 for i in films)
    abs_ep = next(i for i in res["items"] if i["title"].startswith("[Erai-raws]"))
    assert "aflevering_geen_film" in abs_ep["false_positive_flags"] and abs_ep["confidence"] < 0.5
    rep = report(res, "headerindex:rel")
    assert "serie-aflevering in filmcategorie: 2" in rep["message"]


def test_title_similarity_dominates_for_movies(viewer: Any, backend: Any) -> None:
    backend["items"] = [
        _rel("Spirit.Untamed.2021.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos-FGT", [2045], "a"),
        _rel("Spirited.Away.2001.720p.BluRay.x264-AMIABLE", [2040], "b"),
        _rel("Spirited.Away.1080p.WEB.x264-NOYEAR", [2040], "c"),
    ]
    res = search(viewer, q="Spirited Away", category="movies")
    titles = [i["title"] for i in res["items"]]
    assert titles[0] == "Spirited.Away.2001.720p.BluRay.x264-AMIABLE"  # title + year beat a richer title
    assert titles[-1].startswith("Spirit.Untamed")
    assert "titel_wijkt_af" in res["items"][-1]["false_positive_flags"]
    # a year typed in the query must match
    res = search(viewer, q="Spirited Away 1999", category="movies")
    flagged = next(i for i in res["items"] if "AMIABLE" in i["title"])
    assert "jaar_wijkt_af" in flagged["false_positive_flags"]


def test_tv_episode_request_demotes_results_without_numbering(viewer: Any, backend: Any) -> None:
    backend["items"] = [
        _rel("The.Expanse.2015.1080p.BluRay.x264-GRP", [5040], "x"),
        _rel("The.Expanse.S02E05.720p.HDTV.x264-KILLERS", [5040], "y"),
    ]
    res = search(viewer, q="The Expanse", category="tv", season=2, episode=5)
    assert res["items"][0]["title"].startswith("The.Expanse.S02E05")
    last = res["items"][-1]
    assert "nummering_ontbreekt" in last["false_positive_flags"]
    assert last["confidence"] < res["items"][0]["confidence"]


# ------------------------------------------------------------------ freeleech
def test_freeleech_only_on_private_trackers_with_explicit_factor() -> None:
    from mso.connectors.prowlarr import normalize_result

    base = {"protocol": "torrent", "title": "X.2001.1080p", "guid": "g", "indexerId": 7}
    public = dict(base, downloadVolumeFactor=0, indexerFlags=["freeleech"])
    assert normalize_result(public, private_ids=set()).freeleech is None  # TorrentDownload case
    assert normalize_result(public, private_ids=None).freeleech is None  # privacy unknown
    assert normalize_result(dict(base, downloadVolumeFactor=0), private_ids={7}).freeleech is True
    assert normalize_result(dict(base, downloadVolumeFactor=1), private_ids={7}).freeleech is False
    assert normalize_result(dict(base), private_ids={7}).freeleech is None  # missing factor ≠ 0
    assert normalize_result(dict(base, indexerFlags=["freeleech"]), private_ids={7}).freeleech is True
    usenet = dict(base, protocol="usenet", downloadVolumeFactor=0)
    assert normalize_result(usenet, private_ids={7}).freeleech is None

    from mso.features.indexers.newznab_client import _freeleech

    assert _freeleech("0", False, "torrent") is None
    assert _freeleech("0", True, "torrent") is True
    assert _freeleech(None, True, "torrent") is None
    assert _freeleech("0.5", True, "torrent") is False


# ------------------------------------------------------------------ scope derivation + Prowlarr health
def test_derive_scope_from_categories() -> None:
    from mso.features.indexers.prowlarr_sync import derive_scope

    tmap = {1: "anime", 2: "normal"}
    xxx = {"tags": [], "capabilities": mock_prowlarr.caps((6000, "XXX", [(6010, "XXX/DVD")]))}
    d = derive_scope(xxx, tmap)
    assert d["library_relevant"] is False and "XXX" in d["relevance_reason"]
    books = {"tags": [], "capabilities": mock_prowlarr.caps((7000, "Books", [(7020, "Books/EBook")]))}
    assert derive_scope(books, tmap)["library_relevant"] is False
    roms = {"tags": [], "capabilities": mock_prowlarr.caps((1000, "Console", [(1080, "Console/PS3")]))}
    assert derive_scope(roms, tmap)["library_relevant"] is False
    general = {"tags": [], "capabilities": mock_prowlarr.caps(mock_prowlarr.TV_CAPS, mock_prowlarr.MOVIE_CAPS)}
    d = derive_scope(general, tmap)
    assert d["scopes"] == ["normal", "anime"] and d["scope_source"] == "derived" and d["library_relevant"]
    anime_only = {"tags": [], "capabilities": mock_prowlarr.caps(mock_prowlarr.ANIME_CAPS)}
    assert derive_scope(anime_only, tmap)["scopes"] == ["anime"]
    tagged = {"tags": [2], "capabilities": mock_prowlarr.caps(mock_prowlarr.ANIME_CAPS)}
    d = derive_scope(tagged, tmap)
    assert d["scopes"] == ["normal"] and d["scope_source"] == "tag"  # MediaGuard's tag wins
    unknown = {"tags": []}
    d = derive_scope(unknown, tmap)
    assert d["scope_source"] == "default" and d["library_relevant"] is None


def test_parse_health_messages() -> None:
    from mso.features.indexers.prowlarr_sync import parse_health

    names = {13: "Badass Torrents", 19: "Elitetorrent-wf", 27: "UnionDHT", 4: "TorrentLeech", 2: "Nyaa.si"}
    health = [
        {
            "source": "IndexerNoDefinitionCheck",
            "type": "warning",
            "message": "Indexers have no definition and will not work: Badass Torrents, Elitetorrent-wf, "
            "UnionDHT. Please remove and (or) re-add to Prowlarr.",
        },
        {"source": "IndexerStatusCheck", "type": "warning", "message": "Indexers unavailable due to failures: Nyaa.si"},
        {"source": "IndexerProxyStatusCheck", "type": "error", "message": "Proxies unavailable due to failures: x"},
        {"source": "UpdateCheck", "type": "warning", "message": "Cannot install update"},
    ]
    out = parse_health(health, names)
    assert set(out) == {13, 19, 27, 2}
    assert out[13][0]["kind"] == "no_definition" and out[13][0]["broken"] is True
    assert out[2][0]["kind"] == "failing"
    all_down = {"source": "IndexerStatusCheck", "message": "All indexers are unavailable due to failures"}
    everyone = parse_health([all_down], names)
    assert set(everyone) == set(names)


@pytest.fixture()
def prowlarr(app: Any, serve: Any) -> Any:
    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    data = mock.state.data
    data["indexers"] += [
        {
            "id": 5,
            "name": "0Magnet",
            "protocol": "torrent",
            "privacy": "public",
            "enable": True,
            "tags": [],
            "implementation": "Cardigann",
            "definitionName": "0magnet",
            "capabilities": mock_prowlarr.caps((6000, "XXX", [(6010, "XXX/DVD")])),
            "fields": [],
            "_fixtures": [],
        },
        {
            "id": 6,
            "name": "Badass Torrents",
            "protocol": "torrent",
            "privacy": "public",
            "enable": True,
            "tags": [],
            "implementation": "Cardigann",
            "definitionName": "badasstorrents",
            "capabilities": mock_prowlarr.caps(mock_prowlarr.TV_CAPS, mock_prowlarr.MOVIE_CAPS),
            "fields": [],
            "_fixtures": [],
        },
    ]
    data["health"] = [
        {
            "source": "IndexerNoDefinitionCheck",
            "type": "warning",
            "message": "Indexers have no definition and will not work: Badass Torrents. Please remove and (or) "
            "re-add to Prowlarr.",
        }
    ]
    data["indexer_status"] = []
    configure_prowlarr(serve(mock))
    return mock


def test_sync_marks_irrelevant_and_broken_indexers(operator: Any, viewer: Any, prowlarr: Any, db: Any) -> None:
    assert operator.post("/api/v1/indexers/prowlarr/sync").status_code == 200
    items = {i["id"]: i for i in viewer.get("/api/v1/indexers").json()["items"]}
    magnet = items["prowlarr:5"]
    assert magnet["library_relevant"] is False and "XXX" in magnet["relevance_reason"]
    assert magnet["scope_source"] == "derived"
    bad = items["prowlarr:6"]
    assert bad["prowlarr_broken"] is True and "no definition" in bad["prowlarr_health"][0]["message"]
    assert items["prowlarr:1"]["scope_source"] == "tag" and items["prowlarr:1"]["prowlarr_broken"] is False
    assert items["prowlarr:4"]["scope_source"] == "derived" and items["prowlarr:4"]["scopes"] == ["normal", "anime"]
    # the untagged-indexer advice is not given for an indexer that is irrelevant for the library anyway
    view = viewer.get("/api/v1/indexers/prowlarr/view").json()
    assert ("indexer_untagged", "prowlarr:5") not in {(c["code"], c["subject"]) for c in view["conflicts"]}


def test_source_report_per_prowlarr_indexer(operator: Any, viewer: Any, prowlarr: Any, db: Any) -> None:
    operator.post("/api/v1/indexers/prowlarr/sync")
    res = search(viewer, q="Expanse", category="tv")
    agg = report(res, "prowlarr")
    assert agg["status"] == "ok" and agg["parent"] is None
    assert "1 werken niet volgens Prowlarr" in agg["label"] and "1 niet relevant" in agg["label"]
    kids = {s["source_id"]: s for s in res["sources"] if s.get("parent") == "prowlarr"}
    assert kids["prowlarr:1"]["status"] == "ok" and kids["prowlarr:1"]["count"] >= 1
    assert kids["prowlarr:1"]["label"] == "NZBgeek"
    assert kids["prowlarr:5"]["status"] == "skipped_scope" and "Niet relevant" in kids["prowlarr:5"]["message"]
    assert kids["prowlarr:6"]["status"] == "unavailable" and "no definition" in kids["prowlarr:6"]["message"]
    assert kids["prowlarr:2"]["status"] == "skipped_scope"  # anime-tagged Nyaa for a normal series search
    # children directly follow the aggregate row
    order = [s["source_id"] for s in res["sources"]]
    i = order.index("prowlarr")
    assert set(order[i + 1 : i + 1 + len(kids)]) == set(kids)
    # status reflects the last observation: queried indexers have an observation now
    items = {x["id"]: x for x in viewer.get("/api/v1/indexers").json()["items"]}
    assert items["prowlarr:1"]["stats"]["last_status"] == "ok"
    assert items["prowlarr:6"]["stats"] is None  # never asked


def test_per_indexer_failure_from_indexerstatus(operator: Any, viewer: Any, prowlarr: Any) -> None:
    operator.post("/api/v1/indexers/prowlarr/sync")
    prowlarr.state.data["indexer_status"] = [
        {"indexerId": 3, "mostRecentFailure": datetime.now(UTC).isoformat(), "disabledTill": None},
        {"indexerId": 1, "mostRecentFailure": "2020-01-01T00:00:00Z"},  # old failure: not this search
    ]
    res = search(viewer, q="Expanse", category="tv")
    kids = {s["source_id"]: s for s in res["sources"] if s.get("parent") == "prowlarr"}
    assert kids["prowlarr:3"]["status"] == "error" and "Prowlarr meldt een fout" in kids["prowlarr:3"]["message"]
    assert kids["prowlarr:1"]["status"] == "ok"


def test_failing_indexers_can_be_included_by_setting(operator: Any, viewer: Any, prowlarr: Any) -> None:
    set_settings({"search.prowlarr_skip_failing": False, "search.prowlarr_skip_irrelevant": False})
    operator.post("/api/v1/indexers/prowlarr/sync")
    res = search(viewer, q="Expanse", category="tv")
    kids = {s["source_id"]: s for s in res["sources"] if s.get("parent") == "prowlarr"}
    assert kids["prowlarr:6"]["status"] == "empty" and kids["prowlarr:5"]["status"] == "empty"


def test_prowlarr_freeleech_and_slow_report(operator: Any, viewer: Any, prowlarr: Any) -> None:
    prowlarr.state.data["health"] = []
    operator.post("/api/v1/indexers/prowlarr/sync")
    res = search(viewer, q="Expanse", category="tv")
    tl = [i for i in res["items"] if i["source_id"] == "prowlarr:4"]
    assert tl and all(i["freeleech"] is True for i in tl)  # private TorrentLeech, explicit factor 0
    res = search(viewer, category="tv-anime")
    nyaa = [i for i in res["items"] if i["source_id"] == "prowlarr:2"]
    assert nyaa and all(i["freeleech"] is None for i in nyaa)  # public tracker: never "Freeleech"
    set_settings({"search.timeout_s": 6})
    prowlarr.state.faults["timeout"] = "3.2"
    res = search(viewer, q="Expanse", category="tv")
    agg = report(res, "prowlarr")
    assert agg["status"] == "ok" and agg["message"].startswith("Traag:") and "max. 6 s" in agg["message"]
