"""Field report P4/E2/E3: SABnzbd's own per-server errors (status + warnings) are classified, persisted, shown
per server, turned into one advisory per failing server and a disable proposal; failed downloads with an
unspecific "Unpacking failed" are related to those server problems; SABnzbd categories are mapped by name."""

from __future__ import annotations

import time
from typing import Any

import pytest

SAB_KEY = "SABGEHEIM-0123456789"


def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    url = serve(mock_app)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200
    return mock_app.state.sab


@pytest.mark.parametrize(
    "text,status",
    [
        ("Server reader.usenetbucket.com reported: 502 Authentication Failed", "auth_failed"),
        ("Server reader.xsnews.nl reported: 482 Account expired.", "account_expired"),
        ("Server news.newshosting.com reported: 502 Authentication Failed", "auth_failed"),
        (
            "Server nl.thundernews.com reported: 502 Access Denied. Please check your login/pw.",
            "access_denied",
        ),
        ("Failed login for server news.x.test: 481 Authentication failed", "auth_failed"),
        ("Cannot connect to server news.x.test [Connection refused]", "unreachable"),
        ("Server news.x.test reported: 400 Too many connections", "rate_limited"),
        ("Server news.x.test will be ignored for 10 minutes", None),
        ("", None),
    ],
)
def test_classify_sab_server_messages(text: str, status: str | None) -> None:
    from mso.features.usenet.serverstatus import classify_message

    assert classify_message(text) == status


def test_short_names() -> None:
    from mso.features.usenet.serverstatus import short_name

    assert short_name("reader.xsnews.nl") == "xsnews"
    assert short_name("nl.thundernews.com") == "thundernews"
    assert short_name("news.eweka.nl") == "eweka"
    assert short_name("Eweka") == "Eweka"


def test_report_scenario_servers_recommendations_and_proposal(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    sab = _start(serve, admin, server_errors="report")
    r = viewer.get("/api/v1/usenet/sab/servers")
    assert r.status_code == 200, r.text
    by = {i["name"]: i for i in r.json()["items"]}
    assert by["news.eweka.test"]["sab_status"]["status"] == "ok"
    nh = by["news.newshosting.test"]["sab_status"]
    assert nh["status"] == "auth_failed" and nh["label"] == "Inloggen geweigerd" and nh["source"] == "sabnzbd"
    assert "502 Authentication Failed" in nh["message"]
    blk = by["block.example.test"]["sab_status"]
    assert blk["status"] == "account_expired" and "verlopen" in blk["explanation"]
    # one open advisory per failing server (shown on the overview)
    recs = db.fetchall(
        "SELECT * FROM recommendations WHERE status='open' AND dedupe_key LIKE 'usenet.sab_server:%%' "
        "ORDER BY dedupe_key"
    )
    assert [r["dedupe_key"] for r in recs] == [
        "usenet.sab_server:block.example.test",
        "usenet.sab_server:news.newshosting.test",
    ]
    assert all(r["risk"] == "advies" and r["area"] == "usenet" for r in recs)
    assert recs[0]["title"].startswith("Account verlopen bij Blok — ")
    assert "schakel de server uit in SABnzbd" in recs[0]["title"]
    # the priority suggestion never says "no change" while servers fail
    sug = viewer.get("/api/v1/usenet/recommendations").json()
    ops = {o["path"]: o for o in sug["ops"]}
    assert ops["servers/news.newshosting.test/enable"]["after"] == 0
    assert ops["servers/block.example.test/enable"]["reason"].startswith("account verlopen")
    assert {i["server"]: i["status"] for i in sug["issues"]} == {
        "news.newshosting.test": "auth_failed",
        "block.example.test": "account_expired",
    }
    assert "2 van de 3 ingeschakelde SABnzbd-servers geven fouten" in sug["notes"][0]
    ch = admin.post("/api/v1/usenet/recommendations/priority").json()
    assert ch["risk"] == "toestemming_nodig" and ch["target"] == "sabnzbd"
    assert ("servers/block.example.test/enable", 1, 0) in [(o["path"], o["before"], o["after"]) for o in ch["ops"]]
    # usenet overview lists the failing servers
    ov = viewer.get("/api/v1/usenet/overview").json()
    assert {f["server"] for f in ov["sab_servers"]["failing"]} == {"news.newshosting.test", "block.example.test"}
    # problem gone in SABnzbd → advisories resolved
    sab.server_errors.clear()
    sab.warnings.clear()
    viewer.get("/api/v1/usenet/sab/servers")
    q = "SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'usenet.sab_server:%%' AND status=%s"
    assert db.scalar(q, ("open",)) == 0
    assert db.scalar(q, ("resolved",)) == 2


def test_warnings_only_old_sab(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso.features.usenet import serverstatus

    sab = _start(serve, admin, status_mode="none")
    sab.warnings.append(
        {"text": "Server news.newshosting.test reported: 482 Account expired.", "type": "WARNING",
         "time": int(time.time()) - 60}
    )
    sab.warnings.append({"text": "Something unrelated", "type": "WARNING", "time": int(time.time())})
    res = serverstatus.refresh()
    assert res["status_available"] is False and res["failing"] == ["news.newshosting.test"]
    cur = serverstatus.current()
    assert cur["news.newshosting.test"]["source"] == "warnings"
    assert cur["news.eweka.test"]["status"] == "unknown"  # no status mode → no claim either way
    # an old warning (outside the window) is history, not the current state
    sab.warnings[0]["time"] = int(time.time()) - 3 * 86400
    serverstatus.refresh()
    assert serverstatus.current()["news.newshosting.test"]["status"] == "unknown"
    assert db.scalar("SELECT count(*) FROM usenet_sab_server_events") == 2


def test_fullstatus_fallback_and_disabled_server(app: Any, admin: Any, serve: Any) -> None:
    from mso.features.usenet import serverstatus

    sab = _start(serve, admin, status_mode="fullstatus")
    sab.servers[0]["enable"] = 0
    sab.server_inactive.add("news.newshosting.test")
    res = serverstatus.refresh()
    assert res["status_available"] is True
    cur = serverstatus.current()
    assert cur["news.eweka.test"]["status"] == "disabled"
    assert cur["news.newshosting.test"]["status"] == "sab_blocked"
    assert cur["block.example.test"]["status"] == "ok"


def test_unpack_failed_with_complete_data_is_not_blamed_on_servers(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    """1.6.0 (deliberately changed from 1.5.0's "unpack failed related to server problems"): the field report's
    "Unpacking failed, see logfile" jobs had complete data (SABnzbd's stage log: no missing articles, quick check OK).
    A server that cannot log in delivers no data, so it is no longer attached as the likely cause."""
    from mso.features.usenet import history

    sab = _start(serve, admin, scenario="unpack", server_errors="report")
    history.ingest(force=True)
    rows = db.fetchall("SELECT * FROM download_outcomes WHERE client='sabnzbd' ORDER BY client_ref")
    assert len(rows) == 2
    for r in rows:
        assert r["failure_reason"] == "unpack_failed"
        cls = r["meta"]["classification"]
        assert cls["certainty"] == "zeker" and "likely_cause" not in cls
        assert cls["evidence"].startswith("Data was compleet; uitpakken mislukte: Unpacking failed, see logfile")
        assert "serverfouten" not in cls["evidence"]
        assert r["meta"]["classifier_version"] == 2
        assert r["meta"]["stage"]["data_complete"] is True and r["meta"]["stage"]["missing_articles"] == 0
        assert r["meta"]["stage"]["repair"] == "niet_nodig"
        assert r["failure_detail"] == "Unpacking failed, see logfile"  # original kept as detail
    h = viewer.get("/api/v1/usenet/sab/history").json()["items"][0]
    assert h["failure_label"] == "Uitpakken mislukt" and h["certainty"] == "zeker"
    assert h["likely_cause"] is None
    assert h["facts"]["data_complete"] is True and h["facts"]["unpack_line"] == "Unpacking failed, see logfile"
    assert h["facts"]["summary"] == "Data compleet · uitpakken mislukt: Unpacking failed, see logfile"
    assert h["category"] == "*" and h["media_category"] is None
    assert sab is not None


def test_missing_articles_related_to_server_problems(app: Any, db: Any, admin: Any, viewer: Any, serve: Any) -> None:
    """Articles were missing (stage log) while SABnzbd reported login/account problems → the servers are the likely
    cause, named per server."""
    from msomocks import sabnzbd as mock

    from mso.features.usenet import history

    sab = _start(serve, admin, scenario="empty", server_errors="report")
    sab.add_history(mock._slot(1, "Movie.Title.2020.1080p.BluRay.x264-GRP", "movies", "repair", int(time.time())))
    history.ingest(force=True)
    r = db.fetchone("SELECT * FROM download_outcomes WHERE client='sabnzbd'")
    assert r["failure_reason"] == "incomplete"
    cls = r["meta"]["classification"]
    assert cls["likely_cause"]["label"] == "Inlog- of accountproblemen bij servers"
    assert cls["evidence"].startswith("312 artikelen ontbraken · herstel mislukt (12 blokken tekort) terwijl SABnzbd")
    assert "Blok: account verlopen" in cls["evidence"]
    h = viewer.get("/api/v1/usenet/sab/history").json()["items"][0]
    per_server = {s["server"]: s["label"] for s in h["likely_cause"]["servers"]}
    assert per_server == {"news.newshosting.test": "Inloggen geweigerd", "block.example.test": "Account verlopen"}
    assert h["facts"]["data_complete"] is False and h["facts"]["missing_articles"] == 312
    assert h["facts"]["repair"] == "mislukt" and h["facts"]["blocks_short"] == 12


def test_unpack_failed_without_server_problems_is_not_client_failure(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    _start(serve, admin, scenario="unpack")
    h = viewer.get("/api/v1/usenet/sab/history").json()["items"][0]
    assert h["failure_reason"] == "unpack_failed" and h["failure_label"] == "Uitpakken mislukt"
    assert h["certainty"] == "zeker" and h["likely_cause"] is None
    assert "noemt geen oorzaak" in h["evidence"]


def test_generic_unpack_message_uses_stage_log() -> None:
    from mso.features.usenet.failures import classify

    c = classify(
        "Unpacking failed, see logfile",
        [{"name": "Repair", "actions": ["Repair failed, not enough repair blocks (3 short)"]}],
    )
    assert c.reason == "incomplete" and "verwerkingslog" in c.evidence


@pytest.mark.parametrize(
    "cat,media",
    [
        ("tv", "tv"),
        ("Series", "tv"),
        ("sonarr", "tv"),
        ("tv-sonarr", "tv"),
        ("anime", "tv-anime"),
        ("sonarr-anime", "tv-anime"),
        ("anime-tv", "tv-anime"),
        ("movies", "movies"),
        ("Films", "movies"),
        ("radarr", "movies"),
        ("radarr-anime", "movies-anime"),
        ("anime-movies", "movies-anime"),
        ("*", None),
        ("default", None),
        ("software", None),
        ("music", None),
    ],
)
def test_category_heuristic(cat: str, media: str | None) -> None:
    from mso.features.usenet.history import auto_category

    assert auto_category(cat) == media


def test_category_map_endpoint(app: Any, admin: Any, operator: Any, viewer: Any, serve: Any) -> None:
    from mso import settings_registry as sr

    _start(serve, admin)
    cats = viewer.get("/api/v1/usenet/sab/categories").json()
    by = {c["name"]: c for c in cats["items"]}
    assert by["tv"] == {"name": "tv", "media_category": "tv", "source": "automatisch", "auto": "tv"}
    assert by["*"]["media_category"] is None and by["software"]["source"] == "geen"
    body = {"items": [{"name": "software", "media_category": "movies"}, {"name": "tv", "media_category": None},
                      {"name": "movies", "media_category": "movies"}]}
    assert operator.put("/api/v1/usenet/sab/category-map", json=body).status_code == 403
    r = admin.put("/api/v1/usenet/sab/category-map", json=body)
    assert r.status_code == 200, r.text
    # only deviations from the heuristic are stored
    assert sr.get("usenet.category_map") == ["software=movies", "tv=none"]
    by = {c["name"]: c for c in r.json()["items"]}
    assert by["software"]["source"] == "handmatig" and by["software"]["media_category"] == "movies"
    assert by["tv"]["source"] == "handmatig" and by["tv"]["media_category"] is None
    assert by["movies"]["source"] == "automatisch"
    bad = {"items": [{"name": "x", "media_category": "boeken"}]}
    assert admin.put("/api/v1/usenet/sab/category-map", json=bad).status_code == 422


def test_low_article_success_is_flagged(app: Any, admin: Any, viewer: Any, serve: Any) -> None:
    """1.6.0: SABnzbd's ``articles_success`` field holds the FAILED articles (sabnzbd/api.py _api_server_stats →
    BPSMeter.amounts → article_stats_failed). The field report's "Eweka 0,4 % gevonden" (434.000 tried, 1.736 in that
    field) is really 99,6 % found and is not flagged (deliberately changed fixture); a server that really misses most
    articles still is."""
    from mso.features.usenet import history

    sab = _start(serve, admin)
    for d in sab.stats["news.eweka.test"]["articles_tried"]:
        sab.stats["news.eweka.test"]["articles_tried"][d] = 434_000
        sab.stats["news.eweka.test"]["articles_failed"][d] = 1_736
    for d in sab.stats["block.example.test"]["articles_tried"]:
        sab.stats["block.example.test"]["articles_tried"][d] = 20_000
        sab.stats["block.example.test"]["articles_failed"][d] = 19_920
    history.snapshot_server_stats()
    by = {i["name"]: i for i in viewer.get("/api/v1/usenet/sab/servers").json()["items"]}
    assert by["news.eweka.test"]["low_article_success"] is False
    assert by["news.eweka.test"]["period"]["success_ratio"] == 0.996
    assert by["news.eweka.test"]["period"]["articles_success"] == 7 * (434_000 - 1_736)
    assert by["news.newshosting.test"]["low_article_success"] is False  # too few tries
    assert by["block.example.test"]["low_article_success"] is True
    assert by["block.example.test"]["period"]["success_ratio"] == 0.004
    sug = viewer.get("/api/v1/usenet/recommendations").json()
    low = [i for i in sug["insights"] if i["kind"] == "low_success"]
    assert len(low) == 1 and "0,4%" in low[0]["text"] and "140.000" in low[0]["text"] and "Blok" in low[0]["text"]


def test_connector_test_mentions_failing_servers(app: Any, admin: Any, serve: Any) -> None:
    _start(serve, admin, server_errors="report")
    r = admin.post("/api/v1/connectors/sabnzbd/test").json()
    assert r["ok"] is True and "2 server(s) melden fouten" in r["message"]
