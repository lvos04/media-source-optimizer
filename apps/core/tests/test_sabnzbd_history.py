"""PLAN §7 test 4 (part 1): SABnzbd expired account, auth denied, article missing, success → download_outcomes
with evidence-based failure reasons; incremental/conditional polling; per-server measurements."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

SAB_KEY = "SABGEHEIM-0123456789"


@pytest.fixture()
def sab(app: Any, serve: Any, admin: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY)
    url = serve(mock_app)
    assert (
        admin.put(
            "/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": SAB_KEY}}
        ).status_code
        == 200
    )
    return mock_app.state.sab


def _outcomes(db: Any) -> dict[str, dict[str, Any]]:
    return {r["title"]: r for r in db.fetchall("SELECT * FROM download_outcomes WHERE client='sabnzbd'")}


def test_expired_auth_missing_success_are_classified(app: Any, db: Any, sab: Any, worker: Any) -> None:
    from mso import jobs

    jobs.enqueue("usenet.sab_ingest", {"force": True})
    worker.drain()
    rows = _outcomes(db)
    assert len(rows) == 6
    ok = rows["Show.Name.S01E01.1080p.WEB.H264-GRP"]
    assert ok["status"] == "success" and ok["failure_reason"] is None and ok["media_category"] == "tv"
    assert (
        ok["protocol"] == "usenet"
        and ok["client_ref"] == "SABnzbd_nzo_0001"
        and ok["finished_at"] is not None
    )
    expired = rows["Show.Name.S01E02.1080p.WEB.H264-GRP"]
    assert expired["status"] == "failed" and expired["failure_reason"] == "account_expired"
    assert expired["meta"]["classification"]["certainty"] == "zeker"
    assert "481 Account expired" in expired["failure_detail"]
    auth = rows["Anime.Title.S01E03.1080p.WEB.H264-GRP"]
    assert auth["failure_reason"] == "auth_failure" and auth["media_category"] == "tv-anime"
    missing = rows["Movie.Title.2020.2160p.UHD.BluRay.x265-GRP"]
    # articles missing without retention evidence → 'incomplete', never a guessed 'no_retention'
    assert missing["failure_reason"] == "incomplete" and missing["media_category"] == "movies"
    assert rows["Movie.Other.2019.1080p.BluRay.x264-GRP"]["failure_reason"] == "incomplete"
    enc = rows["Anime.Movie.2021.1080p.BluRay.x264-GRP"]
    assert enc["failure_reason"] == "encrypted" and enc["media_category"] == "movies-anime"


def test_incremental_and_conditional_polling(app: Any, db: Any, sab: Any) -> None:
    from msomocks.sabnzbd import _slot

    from mso.features.usenet import history

    first = history.ingest()
    assert first == {"changed": True, "ingested": 6, "failed_new": 5}
    # unchanged history → SAB answers history=false (conditional), nothing processed
    assert history.ingest() == {"changed": False, "ingested": 0}
    sab.add_history(_slot(99, "Show.Name.S02E01.1080p.WEB.H264-GRP", "tv", "missing", int(time.time())))
    third = history.ingest()
    assert third["ingested"] == 1 and third["failed_new"] == 1
    assert len(_outcomes(db)) == 7
    st = db.fetchone("SELECT * FROM usenet_ingest_state WHERE id=1")
    assert st["last_history_update"] == sab.last_history_update and st["ingested_total"] == 7


def test_unknown_sab_category_gets_no_media_category(app: Any, db: Any, sab: Any) -> None:
    from msomocks.sabnzbd import _slot

    from mso.features.usenet import history

    sab.history = [_slot(1, "Some.Software.v1", "software", "success", int(time.time()))]
    history.ingest(force=True)
    row = db.fetchone("SELECT * FROM download_outcomes WHERE client_ref='SABnzbd_nzo_0001'")
    assert row["media_category"] is None and row["client_category"] == "software"


@pytest.mark.parametrize(
    "msg,reason,certainty",
    [
        ("Aborted, cannot be completed - https://sabnzbd.org/not-complete", "incomplete", "zeker"),
        ("Repair failed, not enough repair blocks (481 short)", "incomplete", "zeker"),
        ("Unpacking failed, archive requires a password", "encrypted", "zeker"),
        ("Unpacking failed, write error or disk is full?", "disk_full", "zeker"),
        ("Failed login for server x: 481 Account expired", "account_expired", "zeker"),
        ("Failed login for server x: 482 Authentication failed", "auth_failure", "zeker"),
        ("Server reports: download limit reached for this block", "quota_exceeded", "zeker"),
        ("Unpacking failed, CRC error", "incomplete", "waarschijnlijk"),
        ("Post-processing was aborted (script returned 1)", "client_failure", "zeker"),
        ("Something nobody has seen before", "unknown", "onbekend"),
    ],
)
def test_failure_classifier(msg: str, reason: str, certainty: str) -> None:
    from mso.features.usenet.failures import classify

    c = classify(msg)
    assert c is not None and (c.reason, c.certainty) == (reason, certainty)


def test_no_retention_only_with_evidence() -> None:
    from mso.features.usenet.failures import classify

    msg = "Aborted, cannot be completed - https://sabnzbd.org/not-complete"
    old = datetime.now(UTC) - timedelta(days=4000)
    assert classify(msg, posted_at=old, server_retention_days=[3000, 3500]).reason == "no_retention"
    # one server with unknown retention (0) → no evidence → incomplete
    assert classify(msg, posted_at=old, server_retention_days=[3000, 0]).reason == "incomplete"
    assert classify(msg, posted_at=None, server_retention_days=[3000]).reason == "incomplete"
    assert classify("", []) is None
    assert (
        classify("", [{"name": "Repair", "actions": ["Repair failed, not enough repair blocks"]}]).reason
        == "incomplete"
    )


def test_failure_detail_is_redacted() -> None:
    from mso.features.usenet.failures import classify

    c = classify("Aborted, cannot be completed: https://idx.example/api?apikey=GEHEIM123&t=get")
    assert "GEHEIM123" not in c.detail


def test_per_server_measurements(app: Any, db: Any, viewer: Any, sab: Any) -> None:
    from mso.features.usenet import history

    res = history.snapshot_server_stats()
    assert res["servers"] == 3 and res["rows"] == 21
    r = viewer.get("/api/v1/usenet/sab/servers?period_days=30").json()
    by = {i["name"]: i for i in r["items"]}
    eweka = by["news.eweka.test"]
    assert eweka["period"]["bytes"] == 7 * 10_000_000_000
    assert eweka["period"]["articles_tried"] == 7 * 14_000 and eweka["period"]["success_ratio"] == 0.99
    assert by["news.newshosting.test"]["period"]["success_ratio"] == 0.7
    assert len(eweka["daily"]) == 7 and eweka["total_bytes"] == 7 * 10_000_000_000
    assert "host" not in eweka and "password" not in eweka
    # snapshot is an idempotent upsert per (server, day)
    history.snapshot_server_stats()
    assert db.scalar("SELECT count(*) FROM usenet_server_stats") == 21


def test_refresh_endpoint_roles(viewer: Any, operator: Any, sab: Any) -> None:
    assert viewer.post("/api/v1/usenet/sab/refresh").status_code == 403
    r = operator.post("/api/v1/usenet/sab/refresh", json={"full": True})
    assert r.status_code == 200 and r.json()["history"]["ingested"] == 6
