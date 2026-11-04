"""Regressions from the real-hardware test report (qBittorrent 5.3.0 / WebAPI 2.16.2):

- P2: qBittorrent keeps speed limits in whole KiB/s (200000000 → 199999488); verify-after-write must not fail
  and auto-roll back a correct write; MSO proposes multiples of 1024.
- P3: malware-like torrents ("... h264-GRACE.exe", "... x265-MeGusta.scr") are flagged, counted, recommended
  (advies, dedupe per infohash) and blamed on their source — never deleted.
- P8: extreme connection/upload-slot values are brought back within the caps by tuning (largest first).
- Seeding counts: stopped complete torrents are not "seeding".
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mso import changes
from mso.connectors.qbittorrent import QbitClient

GB = 1024**3


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    QbitClient.reset_cache()
    yield
    QbitClient.reset_cache()


class Qb:
    def __init__(self, app: Any) -> None:
        self.app = app

    @property
    def st(self) -> Any:
        return self.app.state.qb

    def hash(self, prefix: str) -> str:
        return next(h for h, t in self.st.torrents.items() if t["name"].startswith(prefix))

    def calls(self, needle: str) -> list[Any]:
        return [c for c in self.st.calls if needle in c[1]]


@pytest.fixture()
def qb(admin: Any, serve: Any) -> Qb:
    from msomocks.qbittorrent import create_app

    app = create_app(version="5.3.0", webapi="2.16.2")
    url = serve(app)
    r = admin.put(
        "/api/v1/connectors/qbittorrent",
        json={"url": url, "secrets": {"username": "admin", "password": "adminadmin"}},
    )
    assert r.status_code == 200
    return Qb(app)


def ingest() -> dict[str, Any]:
    from mso.features.torrent.ingest import ingest as run

    return run()


def set_settings(admin: Any, **values: Any) -> None:
    r = admin.put("/api/v1/settings", json={"values": {f"torrent.{k}": v for k, v in values.items()}})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ P2 KiB rounding
def test_mock_rounds_limits_like_real_qbittorrent(qb: Qb) -> None:
    client = QbitClient.cached()
    client.set_preferences({"up_limit": 200_000_000})
    assert client.preferences()["up_limit"] == 199_999_488
    h = qb.hash("Gewone.Film")
    client.set_download_limit([h], 1_000_000)
    assert client.torrent(h)["dl_limit"] == 999_424


def test_upload_limit_proposal_applies_without_verification_failure(
    qb: Qb, admin: Any, operator: Any, manage_mode: None
) -> None:
    set_settings(admin, line_up_mbit=2000.0)  # the report's setup: 80 % of 2000 Mbit/s = 200 000 000 B/s
    ingest()
    body = operator.post("/api/v1/torrent/tuning/propose").json()
    run, ch = body["run"], body["change"]
    assert run["param"] == "pref:up_limit" and run["after_value"] % 1024 == 0
    assert run["after_value"] == 199_999_488 and ch["risk"] == "toestemming_nodig"
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied" and qb.st.prefs["up_limit"] == 199_999_488
    # readable impact: flat Dutch labels with plain strings (no nested JSON)
    assert all(isinstance(v, str) for v in ch["impact"].values()) and "Basislijn" in ch["impact"]
    # rollback restores the exact old value
    assert admin.post(f"/api/v1/changes/{ch['id']}/rollback").status_code == 200
    assert qb.st.prefs["up_limit"] == 0


def test_every_rate_path_tolerates_kib_rounding(qb: Qb, admin: Any, manage_mode: None) -> None:
    """Change sets created elsewhere (non-multiples of 1024) still verify: global, alt and per-torrent limits."""
    set_settings(admin, line_up_mbit=2000.0)
    ingest()
    h = qb.hash("Gewone.Film")
    ops = [
        {"path": "pref:alt_up_limit", "label": "Alternatieve uploadlimiet", "before": 10240, "after": 150_000_000},
        {"path": "pref:alt_dl_limit", "label": "Alternatieve downloadlimiet", "before": 10240, "after": 3_000_001},
        {"path": "pref:dl_limit", "label": "Globale downloadlimiet", "before": 0, "after": 50_000_000},
        {"path": f"torrent:{h}:up_limit", "label": "Upload torrent", "before": 0, "after": 1_000_000},
        {"path": f"torrent:{h}:dl_limit", "label": "Download torrent", "before": 0, "after": 2_500_000},
    ]
    ch = changes.create("qbittorrent", "Limieten", ops, risk="automatisch_veilig")
    out = changes.apply(ch["id"], "test")
    assert out["status"] == "applied", out
    assert qb.st.prefs["alt_up_limit"] == 150_000_000 // 1024 * 1024
    assert qb.st.prefs["alt_dl_limit"] == 3_000_001 // 1024 * 1024
    assert qb.st.torrents[h]["up_limit"] == 999_424 and qb.st.torrents[h]["dl_limit"] == 2_500_000 // 1024 * 1024
    changes.rollback(ch["id"], "test")
    assert qb.st.prefs["alt_up_limit"] == 10240 and qb.st.torrents[h]["dl_limit"] == 0


def test_real_mismatch_still_fails_verification(qb: Qb, admin: Any, manage_mode: None, monkeypatch: Any) -> None:
    """The tolerance is < 1 KiB only: a client that ignores the write is still caught and rolled back."""
    from fastapi import HTTPException

    ingest()
    client = QbitClient.cached()
    monkeypatch.setattr(client, "set_preferences", lambda values: None)  # write silently ignored
    ch = changes.create(
        "qbittorrent",
        "dl",
        [{"path": "pref:dl_limit", "label": "Globale downloadlimiet", "before": 0, "after": 10_240_000}],
        risk="automatisch_veilig",
    )
    with pytest.raises(HTTPException):
        changes.apply(ch["id"], "test")
    assert changes.get(ch["id"])["status"] == "failed"


def test_rate_helpers() -> None:
    from mso.features.torrent import policy
    from mso.features.torrent.settings import kib_floor

    assert kib_floor(200_000_000) == 199_999_488 and kib_floor(0) == 0 and kib_floor(-1) == -1
    assert policy.rate_matches(200_000_000, 199_999_488)
    assert not policy.rate_matches(200_000_000, 199_998_000)  # more than 1 KiB off
    assert not policy.rate_matches(1000, 0) and not policy.rate_matches(0, 1000)  # unlimited ≠ a real limit
    assert policy.rate_matches(0, -1)  # both "no limit" in qBittorrent
    assert policy.is_rate_path("pref:up_limit") and policy.is_rate_path("torrent:" + "a" * 40 + ":dl_limit")
    assert not policy.is_rate_path("pref:max_connec") and not policy.is_rate_path("transfer:alt_speed")


# ------------------------------------------------------------------ P8 extreme preference values
def test_extreme_prefs_brought_back_within_caps(
    qb: Qb, admin: Any, operator: Any, viewer: Any, manage_mode: None
) -> None:
    qb.st.prefs.update(
        {"max_connec": 50000, "max_connec_per_torrent": 100000, "max_uploads": 200000, "max_uploads_per_torrent": 40000}
    )
    ingest()  # no line capacity, no measurements
    prefs = {p["key"]: p for p in viewer.get("/api/v1/torrent/preferences").json()["items"]}
    for k in ("max_connec", "max_connec_per_torrent", "max_uploads", "max_uploads_per_torrent"):
        assert prefs[k]["bounds"] == "buiten_grens", k
    assert prefs["max_connec"]["max"] == 1000 and prefs["max_connec_per_torrent"]["target"] == 200
    assert prefs["up_limit"]["bounds"] is None  # line capacity unknown → no judgement
    pol = viewer.get("/api/v1/torrent/policy").json()["speed"]
    assert [v["key"] for v in pol["out_of_bounds"]][0] == "max_connec_per_torrent"
    codes = {f["code"] for f in viewer.get("/api/v1/torrent/overview").json()["findings"]}
    assert "prefs_out_of_bounds" in codes

    body = operator.post("/api/v1/torrent/tuning/propose").json()
    assert "run" in body, body
    # largest violation first: per torrent 100000 / cap 200 = 500× (vs 400×, 400×, 50×)
    assert body["run"]["param"] == "pref:max_connec_per_torrent" and body["run"]["after_value"] == 200
    assert body["run"]["kind"] == "guardrail" and body["change"]["risk"] == "toestemming_nodig"
    assert "buiten de veilige grens" in body["run"]["reason"]
    cid = body["change"]["id"]
    assert admin.post(f"/api/v1/changes/{cid}/approve").status_code == 200
    assert admin.post(f"/api/v1/changes/{cid}/apply").status_code == 200
    assert qb.st.prefs["max_connec_per_torrent"] == 200


def test_unlimited_slots_count_as_violation(qb: Qb, operator: Any) -> None:
    qb.st.prefs.update({"max_uploads": -1})
    ingest()
    body = operator.post("/api/v1/torrent/tuning/propose").json()
    assert body["run"]["param"] == "pref:max_uploads" and body["run"]["after_value"] == 500
    assert "onbeperkt" in body["run"]["reason"]


# ------------------------------------------------------------------ P3 malware-like payloads
def test_payload_scan_rules() -> None:
    from mso.features.torrent.diagnostics import looks_like_media, payload_scan

    def flagged(name: str, files: list[str] | None = None, media: bool = True) -> list[str]:
        scan = payload_scan(name, [{"name": f} for f in files or []], media=media)
        return [m["ext"] for m in scan["matches"]]

    assert flagged("Criminal Minds S19E06 1080p WEB h264-GRACE.exe") == ["exe"]
    assert flagged("House of the Dragon S03E05 1080p HEVC x265-MeGusta.scr") == ["scr"]
    assert flagged("Show S01E01 1080p WEB.zipx") == ["zipx"]
    assert flagged("Show.S01E01.1080p.WEB.h264-GRP", ["Show/Show.S01E01.mkv.exe"]) == ["exe"]
    assert flagged("Show.S01E01", ["Show/a.mkv", "Show/Watch Now.lnk", "Show/run.ps1"]) == ["lnk", "ps1"]
    assert flagged("Show.S01E01.1080p.WEB.h264-GRP", ["Show/Show.S01E01.mkv", "Show/RARBG.txt"]) == []
    assert flagged("Movie 2020 1080p - site.com") == []  # site tag, not a program
    assert flagged("Movie.2020.1080p.mkv.com") == ["com"]  # double extension
    # password hint only together with an archive
    assert flagged("Movie.2020.1080p", ["Movie/movie.rar", "Movie/Password.txt"]) == ["txt"]
    assert flagged("Movie.2020.1080p", ["Movie/movie.mkv", "Movie/Password.txt"]) == []
    # software outside media categories is not judged
    assert flagged("SomeApp 2.0", ["SomeApp/setup.exe"], media=False) == []
    assert looks_like_media("Some Show S01E02 720p", None) and not looks_like_media("SomeApp 2.0", None)


def _add_suspicious(qb: Qb) -> dict[str, str]:
    from msomocks.qbittorrent import _torrent

    pub = "https://tracker.publiek.example/announce"
    tr = [{"url": pub, "status": 2, "tier": 0, "num_peers": 3, "num_seeds": 9, "num_leeches": 3,
           "num_downloaded": 1, "msg": ""}]
    names = {
        "exe": "Criminal Minds S19E06 1080p WEB h264-GRACE.exe",
        "scr": "House of the Dragon S03E05 1080p HEVC x265-MeGusta.scr",
        "file": "Clean.Name.S01E01.1080p.WEB.h264-GRP",
        "app": "SomeApp 2.0 Portable",
    }
    for key, n in names.items():
        t = _torrent(
            n,
            category="software" if key == "app" else "tv-sonarr",
            size=GB,
            progress=1.0,
            state="stalledUP",
            tracker=pub,
            private=False,
            comment="https://indexer.voorbeeld.example/details/42?passkey=GEHEIM123" if key == "exe" else "",
        )
        files = {
            "exe": [(n, GB)],
            "scr": [(n, GB)],
            "file": [(f"{n}/{n}.mkv.exe", GB), (f"{n}/sample.mkv", 1000)],
            "app": [(f"{n}/setup.exe", GB)],
        }[key]
        qb.st.add(t, tr, files)
    return {k: qb.hash(v) for k, v in names.items()}


def test_suspicious_torrents_flagged_counted_recommended_never_deleted(
    qb: Qb, viewer: Any, db: Any, manage_mode: None
) -> None:
    hashes = _add_suspicious(qb)
    db.execute(
        "INSERT INTO search_sends(release_key, client, category, title, source_id, status, actor) "
        "VALUES (%s,'qbittorrent','tv','x','prowlarr:7','sent','admin')",
        ("btih:" + hashes["scr"],),
    )
    res = ingest()
    assert res["suspicious"] == 3
    rows = {r["hash"]: r for r in viewer.get("/api/v1/torrent/torrents").json()["items"]}
    for k in ("exe", "scr", "file"):
        d = rows[hashes[k]]["diagnosis"]
        assert d["code"] == "suspicious_payload" and d["severity"] == "critical", k
        assert d["title"] == "Verdacht bestand (uitvoerbaar) in media-download"
        assert rows[hashes[k]]["suspicious"]
    assert rows[hashes["app"]]["diagnosis"] is None or rows[hashes["app"]]["diagnosis"]["code"] != (
        "suspicious_payload"
    )
    # source is recorded for blame: tracker, indexer (MSO send), comment host (never the URL/passkey)
    detail = viewer.get(f"/api/v1/torrent/torrents/{hashes['exe']}").json()
    src = next(f for f in detail["findings"] if f["code"] == "suspicious_payload")["evidence"]["source"]
    assert src["tracker"] == "tracker.publiek.example" and src["comment_host"] == "indexer.voorbeeld.example"
    assert src["category"] == "tv-sonarr"
    scr = viewer.get(f"/api/v1/torrent/torrents/{hashes['scr']}").json()
    assert next(f for f in scr["findings"] if f["code"] == "suspicious_payload")["evidence"]["source"]["indexer"] == (
        "prowlarr:7"
    )
    dump = json.dumps([dict(r) for r in db.fetchall("SELECT info, diagnosis FROM torrent_state")], default=str)
    assert "GEHEIM123" not in dump
    # overview: count + client-wide finding
    ov = viewer.get("/api/v1/torrent/overview").json()
    assert ov["suspicious_count"] == 3 and ov["diagnosis_counts"]["suspicious_payload"] == 3
    assert ov["findings"][0]["code"] == "suspicious_torrents"
    # outcome counts against the source
    out = db.fetchone("SELECT * FROM download_outcomes WHERE client_ref=%s", (hashes["exe"],))
    assert out["status"] == "failed" and out["failure_reason"] == "false_positive"
    assert out["source_id"] == "tracker:tracker.publiek.example"
    # one advies recommendation per infohash, idempotent across ingests; file lists fetched once
    files_calls = len(qb.calls("torrents/files"))
    ingest()
    assert len(qb.calls("torrents/files")) == files_calls
    recs = db.fetchall("SELECT * FROM recommendations WHERE dedupe_key LIKE 'torrent-suspicious:%'")
    assert len(recs) == 3 and {r["risk"] for r in recs} == {"advies"} and {r["area"] for r in recs} == {"torrent"}
    # removed by the user in qBittorrent → recommendation resolved
    del qb.st.torrents[hashes["exe"]]
    ingest()
    st = {r["dedupe_key"]: r["status"] for r in db.fetchall("SELECT dedupe_key, status FROM recommendations")}
    assert st["torrent-suspicious:btih:" + hashes["exe"]] == "resolved"
    assert st["torrent-suspicious:btih:" + hashes["scr"]] == "open"
    assert not qb.calls("/delete") and not qb.calls("/stop") and not qb.calls("/pause")


def test_file_checks_are_bounded_per_run(qb: Qb, monkeypatch: Any) -> None:
    from mso.features.torrent import ingest as ing

    monkeypatch.setattr(ing, "FILE_CHECKS_PER_RUN", 2)
    _add_suspicious(qb)
    before = len(qb.calls("torrents/files"))
    assert ingest()["files_checked"] == 2
    assert len(qb.calls("torrents/files")) - before == 2


# ------------------------------------------------------------------ seeding vs stopped
def test_stopped_complete_not_counted_as_seeding(qb: Qb, viewer: Any) -> None:
    qb.st.torrents[qb.hash("Publieke.Serie")]["state"] = "stoppedUP"
    ingest()
    cats = {c["category"]: c for c in viewer.get("/api/v1/torrent/storage").json()["categories"]}
    tv = cats["tv"]  # Publieke.Serie (stopped), Andere.Serie (downloading), Kapotte.Serie (missingFiles)
    assert tv["stopped"] == 1 and tv["seeding"] == 0
