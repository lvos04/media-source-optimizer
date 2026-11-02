"""PLAN §7 test 8 — matrix tv / tv-anime / movies / movies-anime × audio/HDR/DV rules, and the API can never
bypass the quality policy (MediaGuard requirements are leading and never relaxed)."""

from __future__ import annotations

from typing import Any

import pytest
from mg_support import configure, install_clients, install_search, release, remove_search, start_mock


def _manual(operator: Any, **kw: Any) -> str:
    r = operator.post("/api/v1/recovery/wanted", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["media_ref"]


def _cands(operator: Any, ref: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    r = operator.post(f"/api/v1/recovery/items/{ref}/candidates", json={"extra": extra} if extra else {})
    assert r.status_code == 200, r.text
    return r.json()


def _verdicts(res: dict[str, Any]) -> dict[str, list[str]]:
    out = {c["title"]: [] for c in res["accepted"]}
    out.update({c["title"]: c["reasons"] for c in res["rejected"]})
    return out


# ------------------------------------------------------------------ matrix
MATRIX = [
    # (category, manual item, requirements, title -> expected pass?, expected reason fragment)
    (
        "tv",
        {"title": "Lost Signal", "season_number": 2, "episode_number": 5},
        {},
        [
            ("Lost.Signal.S02E05.1080p.WEB-DL.DDP5.1.H.264-GRP", True, None),
            ("Lost.Signal.S02E06.1080p.WEB-DL.DDP5.1.H.264-GRP", False, "verkeerde aflevering"),
            ("Lost.Signal.S01E05.1080p.WEB-DL.DDP5.1.H.264-GRP", False, "verkeerd seizoen"),
        ],
    ),
    (
        "tv",
        {"title": "Lost Signal", "season_number": 2, "episode_number": 5},
        {"required_audio_languages": ["nl"], "required_subtitles": ["nl"]},
        [
            ("Lost.Signal.S02E05.1080p.WEB-DL.DUTCH.NLSubs.H.264-GRP", True, None),
            ("Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP", False, "audiotaal 'nl'"),
        ],
    ),
    (
        "tv-anime",
        {"title": "Sky Blade", "season_number": 1, "episode_number": 13, "absolute_episode_number": 13},
        {"require_dual_audio": True},
        [
            ("[SubsPlease] Sky Blade - 13 (1080p) [Dual Audio]", True, None),
            ("[SubsPlease] Sky Blade - 13 (1080p)", False, "dual audio vereist"),
            ("[SubsPlease] Sky Blade - 14 (1080p) [Dual Audio]", False, "absolute afleveringsnummering"),
            ("[Grp] Sky Blade - 13 (1080p) [Dual Audio] [HC]", False, "hardsubs"),
        ],
    ),
    (
        "movies",
        {"title": "The Long Night", "year": 2019},
        {"min_resolution": 2160, "require_hdr": ["dv"]},
        [
            ("The.Long.Night.2019.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos-GRP", True, None),
            ("The.Long.Night.2019.2160p.UHD.BluRay.HDR10.HEVC-GRP", False, "DV vereist"),
            ("The.Long.Night.2019.1080p.BluRay.x264-GRP", False, "resolutie te laag"),
            ("The.Long.Night.1987.2160p.UHD.BluRay.DV.HEVC-GRP", False, "verkeerd jaar"),
        ],
    ),
    (
        "movies",
        {"title": "The Long Night", "year": 2019},
        {"forbid_hdr": ["dv"]},
        [
            ("The.Long.Night.2019.2160p.UHD.BluRay.HDR10.HEVC-GRP", True, None),
            ("The.Long.Night.2019.2160p.UHD.BluRay.DV.HDR.HEVC-GRP", False, "DV niet toegestaan"),
        ],
    ),
    (
        "movies-anime",
        {"title": "Garden of Words", "year": 2013},
        {"required_audio_languages": ["ja"], "required_subtitles": ["nl"]},
        [
            ("Garden.of.Words.2013.1080p.BluRay.x264.Japanese.NLSubs-GRP", True, None),
            ("Garden.of.Words.2013.1080p.BluRay.x264.Japanese-GRP", False, "ondertitels 'nl'"),
            ("Garden.of.Words.2013.1080p.BluRay.x264.English.NLSubs-GRP", False, "audiotaal 'ja'"),
        ],
    ),
]


@pytest.mark.parametrize("cat,item,reqs,cases", MATRIX)
def test_quality_matrix(
    cat: str, item: dict[str, Any], reqs: dict[str, Any], cases: list[Any], operator: Any, monkeypatch: Any
) -> None:
    install_search(monkeypatch, [release(t) for t, _, _ in cases])
    ref = _manual(operator, media_category=cat, requirements=reqs, **item)
    res = _cands(operator, ref)
    verdicts = _verdicts(res)
    for title, ok, fragment in cases:
        reasons = verdicts[title]
        assert (not reasons) is ok, (title, reasons)
        if fragment:
            assert any(fragment in r for r in reasons), (title, reasons)
    # every candidate has evidence rows (requirement vs candidate) for the UI
    for c in res["accepted"] + res["rejected"]:
        assert any(e["key"] == "numbering" for e in c["evidence"])


def test_wrong_category_is_rejected(operator: Any, monkeypatch: Any) -> None:
    install_search(
        monkeypatch,
        [
            release("[Grp] Sky Blade - 13 (1080p)", cat="tv-anime"),
            release("Sky.Blade.S01E13.1080p.WEB.H264-GRP", cat="tv"),
        ],
    )
    ref = _manual(operator, media_category="tv", title="Sky Blade", season_number=1, episode_number=13)
    v = _verdicts(_cands(operator, ref))
    assert any("verkeerde categorie" in r for r in v["[Grp] Sky Blade - 13 (1080p)"])
    assert v["Sky.Blade.S01E13.1080p.WEB.H264-GRP"] == []


# ------------------------------------------------------------------ protocol fallback & torrent policy
def test_usenet_torrent_fallback_requires_quality_and_policy(operator: Any, monkeypatch: Any) -> None:
    install_search(
        monkeypatch,
        [
            release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP", protocol="usenet", key="u1"),
            release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-TOR", protocol="torrent", seeders=40, key="t1"),
            release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-DEAD", protocol="torrent", seeders=0, key="t2"),
            release("Lost.Signal.S02E05.720p.WEB-DL.H.264-LOW", protocol="torrent", seeders=99, key="t3"),
        ],
    )
    ref = _manual(
        operator,
        media_category="tv",
        title="Lost Signal",
        season_number=2,
        episode_number=5,
        requirements={"min_resolution": 1080},
    )
    res = _cands(operator, ref)
    acc = {c["title"]: c for c in res["accepted"]}
    assert set(acc) == {
        "Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP",
        "Lost.Signal.S02E05.1080p.WEB-DL.H.264-TOR",
    }
    v = _verdicts(res)
    assert any("geen seeders" in r for r in v["Lost.Signal.S02E05.1080p.WEB-DL.H.264-DEAD"])
    assert any("resolutie te laag" in r for r in v["Lost.Signal.S02E05.720p.WEB-DL.H.264-LOW"])
    assert all(c["score"] is not None for c in res["accepted"])
    assert res["accepted"][0]["score"] >= res["accepted"][-1]["score"]


def test_blacklisted_release_excluded(operator: Any, monkeypatch: Any) -> None:
    from mso.features.recovery import blacklist

    r1 = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP", key="bad")
    install_search(monkeypatch, [r1])
    blacklist.add(r1.release_key, "incompleet", 24)
    ref = _manual(operator, media_category="tv", title="Lost Signal", season_number=2, episode_number=5)
    res = _cands(operator, ref)
    assert not res["accepted"] and res["counts"]["blacklisted"] == 1
    assert any("blacklist" in r for r in res["rejected"][0]["reasons"])


def test_search_engine_missing_returns_409(operator: Any, monkeypatch: Any) -> None:
    remove_search(monkeypatch)
    ref = _manual(operator, media_category="movies", title="X", year=2020)
    r = operator.post(f"/api/v1/recovery/items/{ref}/candidates", json={})
    assert r.status_code == 409 and "zoekmachine niet beschikbaar" in r.json()["detail"].lower()


# ------------------------------------------------------------------ no policy bypass through the API
def test_send_refuses_unevaluated_or_failing_candidates(operator: Any, viewer: Any, monkeypatch: Any) -> None:
    good = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP", key="good")
    bad = release("Lost.Signal.S02E06.1080p.WEB-DL.H.264-GRP", key="bad")
    install_search(monkeypatch, [good, bad])
    sent = install_clients(monkeypatch)
    ref = _manual(operator, media_category="tv", title="Lost Signal", season_number=2, episode_number=5)
    _cands(operator, ref)
    # a release MSO never evaluated for this item cannot be injected
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": "nzb:injected"})
    assert r.status_code == 404
    # failing candidate refused (server-side re-evaluation)
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": bad.release_key})
    assert r.status_code == 422 and "verkeerde aflevering" in r.json()["detail"]
    assert (
        viewer.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": good.release_key}).status_code
        == 403
    )
    assert not sent["sab"]
    # passing candidate goes to SABnzbd in a separate MSO category; idempotent
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": good.release_key})
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is False and sent["sab"][0]["category"] == "mso-tv"
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": good.release_key})
    assert r.json()["duplicate"] is True and len(sent["sab"]) == 1


def test_requirements_cannot_be_relaxed(operator: Any, monkeypatch: Any) -> None:
    install_search(monkeypatch, [release("The.Long.Night.2019.1080p.BluRay.x264-GRP")])
    ref = _manual(
        operator,
        media_category="movies",
        title="The Long Night",
        year=2019,
        requirements={"min_resolution": 2160},
    )
    # lower minimum in 'extra' does not loosen anything
    res = _cands(operator, ref, {"min_resolution": 720})
    assert res["requirements"]["min_resolution"] == 2160 and not res["accepted"]
    # unknown/relaxing fields are refused outright
    for extra in (
        {"forbid_hardsub": None, "allow_hardsub": True},
        {"relax": ["quality"]},
        {"min_resolution": 1},
    ):
        r = operator.post(f"/api/v1/recovery/items/{ref}/candidates", json={"extra": extra})
        assert r.status_code == 422, (extra, r.text)
    r = operator.post(
        "/api/v1/recovery/wanted",
        json={"title": "Y", "media_category": "movies", "requirements": {"ignore_quality": True}},
    )
    assert r.status_code == 422


def test_blacklisted_after_evaluation_cannot_be_sent(operator: Any, monkeypatch: Any) -> None:
    from mso.features.recovery import blacklist

    good = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP", key="later-bad")
    install_search(monkeypatch, [good])
    sent = install_clients(monkeypatch)
    ref = _manual(operator, media_category="tv", title="Lost Signal", season_number=2, episode_number=5)
    assert _cands(operator, ref)["accepted"]
    blacklist.add(good.release_key, "mislukt", 24)
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": good.release_key})
    assert r.status_code == 422 and "blacklist" in r.json()["detail"]
    assert not sent["sab"]


# ---------------------------------------------------- MediaGuard items: requirements are leading
def _sync(operator: Any, worker: Any) -> None:
    assert operator.post("/api/v1/mediaguard/sync").status_code == 200
    worker.drain()


def test_mediaguard_policy_is_leading_and_send_refused_when_connected(
    serve: Any, admin: Any, operator: Any, worker: Any, monkeypatch: Any
) -> None:
    mock, url = start_mock(serve)
    configure(admin, url)
    mock.state.policy_overrides["radarr-films"] = {
        "min_resolution": 2160,
        "audio": {"nl_audio": "required"},
        "blocked_groups": ["BADGRP"],
        "max_size_gb": 60,
    }
    _sync(operator, worker)
    cases = [
        release("The.Long.Night.2019.2160p.UHD.BluRay.DUTCH.HEVC-GRP", key="ok", size_gb=40),
        release("The.Long.Night.2019.2160p.UHD.BluRay.HEVC-GRP", key="nonl", size_gb=40),
        release("The.Long.Night.2019.1080p.BluRay.DUTCH.x264-GRP", key="low", size_gb=10),
        release("The.Long.Night.2019.2160p.UHD.BluRay.DUTCH.HEVC-BADGRP", key="blocked", size_gb=40),
        release("The.Long.Night.2019.2160p.UHD.BluRay.REMUX.DUTCH.HEVC-GRP", key="huge", size_gb=80),
    ]
    install_search(monkeypatch, cases)
    sent = install_clients(monkeypatch)
    res = _cands(operator, "mg:6")
    assert res["requirements"]["policy_verified"] is True
    assert res["requirements"]["min_resolution"] == 2160 and res["requirements"][
        "required_audio_languages"
    ] == ["nl"]
    v = _verdicts(res)
    assert v["The.Long.Night.2019.2160p.UHD.BluRay.DUTCH.HEVC-GRP"] == []
    assert any("audiotaal 'nl'" in r for r in v["The.Long.Night.2019.2160p.UHD.BluRay.HEVC-GRP"])
    assert any("te laag" in r for r in v["The.Long.Night.2019.1080p.BluRay.DUTCH.x264-GRP"])
    assert any("geblokkeerd" in r for r in v["The.Long.Night.2019.2160p.UHD.BluRay.DUTCH.HEVC-BADGRP"])
    assert any("groter dan" in r for r in v["The.Long.Night.2019.2160p.UHD.BluRay.REMUX.DUTCH.HEVC-GRP"])
    good_key = res["accepted"][0]["release_key"]
    # connected: MediaGuard owns the item → manual send refused, official search route offered instead
    item = operator.get("/api/v1/recovery/items/mg:6").json()
    assert item["actions"]["request_search"]["available"] is True
    assert item["actions"]["manual_send"]["available"] is False
    r = operator.post("/api/v1/recovery/items/mg:6/send", json={"release_key": good_key})
    assert r.status_code == 409
    # even when allowed, a candidate failing the (current) MediaGuard policy is refused at send time
    admin.put("/api/v1/settings", json={"values": {"herstel.allow_manual_send_connected": True}})
    bad_key = next(
        c["release_key"] for c in res["rejected"] if "HEVC-GRP" in c["title"] and "DUTCH" not in c["title"]
    )
    assert operator.post("/api/v1/recovery/items/mg:6/send", json={"release_key": bad_key}).status_code == 422
    # MediaGuard tightens its policy after evaluation → the earlier "accepted" release is now refused too
    mock.state.policy_overrides["radarr-films"]["max_size_gb"] = 20
    from mso.db import get_db

    get_db().execute("DELETE FROM mg_snapshots WHERE key LIKE 'policy:%%'")
    r = operator.post("/api/v1/recovery/items/mg:6/send", json={"release_key": good_key})
    assert r.status_code == 422 and "groter dan" in r.json()["detail"]
    assert not sent["sab"] and not sent["qbit"]
    from mg_support import assert_only_allowed_writes

    assert_only_allowed_writes(mock)


def test_mediaguard_policy_unreadable_blocks_send(
    serve: Any, admin: Any, operator: Any, worker: Any, monkeypatch: Any
) -> None:
    mock, url = start_mock(serve, old_version=1)  # no /policy/effective
    configure(admin, url)
    _sync(operator, worker)
    install_search(monkeypatch, [release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP", key="x")])
    sent = install_clients(monkeypatch)
    res = _cands(operator, "mg:1")
    assert res["requirements"]["policy_verified"] is False
    admin.put("/api/v1/settings", json={"values": {"herstel.allow_manual_send_connected": True}})
    r = operator.post(
        "/api/v1/recovery/items/mg:1/send", json={"release_key": res["accepted"][0]["release_key"]}
    )
    assert r.status_code == 409 and "niet leesbaar" in r.json()["detail"]
    assert not sent["sab"]


def test_anime_mediaguard_item_uses_absolute_numbering(
    serve: Any, admin: Any, operator: Any, worker: Any, monkeypatch: Any
) -> None:
    mock, url = start_mock(serve)
    configure(admin, url)
    _sync(operator, worker)
    install_search(
        monkeypatch,
        [
            release("[SubsPlease] Sky Blade - 26 (1080p) [Dual Audio]", key="a26"),
            release("[SubsPlease] Sky Blade - 25 (1080p) [Dual Audio]", key="a25"),
            release("Sky.Blade.S02E01.1080p.WEB.H264-GRP", key="se"),
        ],
    )
    res = _cands(operator, "mg:5")  # S02E01 = absolute 26
    v = _verdicts(res)
    assert v["[SubsPlease] Sky Blade - 26 (1080p) [Dual Audio]"] == []
    assert v["Sky.Blade.S02E01.1080p.WEB.H264-GRP"] == []
    assert any("absolute" in r for r in v["[SubsPlease] Sky Blade - 25 (1080p) [Dual Audio]"])
    assert any("voorkeur" in i for i in res["requirements"]["informational"])


def test_candidate_storage_drops_secret_urls_and_torrent_send(
    operator: Any, db: Any, monkeypatch: Any
) -> None:
    tor = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-TOR", protocol="torrent", seeders=12, key="tor1")
    tor.download_url = "http://tracker.invalid/dl/tor1.torrent?passkey=PASSKEY0123456789"
    tor.extra["magnet"] = "magnet:?xt=urn:btih:abc&tr=http://tracker.invalid/announce/PASSKEY0123456789"
    nzb = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-NZB", key="nzb1")
    nzb.download_url = "http://indexer.invalid/api?t=get&id=1&apikey=APIKEY0123456789"
    install_search(monkeypatch, [tor, nzb])
    sent = install_clients(monkeypatch)
    ref = _manual(operator, media_category="tv", title="Lost Signal", season_number=2, episode_number=5)
    res = _cands(operator, ref)
    assert len(res["accepted"]) == 2 and all(c["has_download"] for c in res["accepted"])
    stored = db.scalar("SELECT string_agg(release::text, ' ') FROM recovery_candidates")
    assert "PASSKEY0123456789" not in stored and "APIKEY0123456789" not in stored
    assert "PASSKEY0123456789" not in operator.get(f"/api/v1/recovery/items/{ref}").text
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": tor.release_key})
    assert r.status_code == 200, r.text
    assert sent["qbit"][0]["category"] == "mso-tv" and sent["qbit"][0]["tags"] == ["mso-herstel"]
    assert r.json()["send"]["client_ref"] == tor.infohash_v1
