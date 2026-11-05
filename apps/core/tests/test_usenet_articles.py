"""Article availability on actually requested articles (STAT per provider), unique contribution with coverage
and
confidence, provider_contribution() contract for Slice E, failure-triggered tests, NNTP client details."""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from typing import Any

import pytest

USER, PASSWORD = "u", "p-geheim"
SAB_KEY = "SABGEHEIM-0123456789"


def make_nzb(ids: list[str], title: str = "Show.Name.S01E07.1080p.WEB.H264-GRP") -> bytes:
    segs = "".join(f'<segment bytes="700000" number="{i + 1}">{m}</segment>' for i, m in enumerate(ids))
    return (
        f'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><head>'
        f'<meta type="name">{title}</meta></head><file poster="x@example.invalid" date="1700000000" '
        f'subject="s"><groups><group>alt.binaries.mso.test</group></groups><segments>{segs}</segments>'
        f"</file></nzb>"
    ).encode()


@pytest.fixture()
def two_providers(app: Any, admin: Any) -> Iterator[tuple[Any, Any]]:
    """Eweka has seg1..seg80; Newshosting has seg21..seg100 → overlap 21..80, unique 1..20 / 81..100."""
    from msomocks import nntp

    a = nntp.start(username=USER, password=PASSWORD, articles=[f"seg{i}@mock" for i in range(1, 81)])
    b = nntp.start(username=USER, password=PASSWORD, articles=[f"seg{i}@mock" for i in range(21, 101)])
    for slug, srv, server in (("eweka", a, "news.eweka.test"), ("newshosting", b, "news.newshosting.test")):
        r = admin.post(
            "/api/v1/usenet/providers",
            json={
                "slug": slug,
                "label": slug.title(),
                "host": "127.0.0.1",
                "port": srv.port,
                "ssl": False,
                "connections": 4,
                "sab_server": server,
                "username": USER,
                "password": PASSWORD,
            },
        )
        assert r.status_code == 200, r.text
    yield a, b
    a.stop()
    b.stop()


def _upload(client: Any, ids: list[str]) -> dict[str, Any]:
    r = client.post(
        "/api/v1/usenet/nzbs",
        json={"filename": "test.nzb", "content_b64": base64.b64encode(make_nzb(ids)).decode()},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_article_availability_and_unique_contribution(
    admin: Any, operator: Any, viewer: Any, worker: Any, two_providers: Any
) -> None:
    a, b = two_providers
    nzb = _upload(operator, [f"seg{i}@mock" for i in range(1, 101)])
    assert nzb["testable"] and nzb["segment_total"] == 100
    r = operator.post("/api/v1/usenet/article-tests", json={"nzb_id": nzb["id"], "sample_size": 100})
    assert r.status_code == 422  # explicit confirmation required
    assert (
        viewer.post("/api/v1/usenet/article-tests", json={"nzb_id": nzb["id"], "confirm": True}).status_code
        == 403
    )
    t = operator.post(
        "/api/v1/usenet/article-tests", json={"nzb_id": nzb["id"], "sample_size": 100, "confirm": True}
    ).json()
    assert t["status"] == "queued" and sorted(t["providers"]) == ["eweka", "newshosting"]
    worker.drain()
    done = viewer.get(f"/api/v1/usenet/article-tests/{t['id']}").json()
    assert done["status"] == "done"
    p = done["summary"]["providers"]
    assert (p["eweka"]["checked"], p["eweka"]["available"], p["eweka"]["unique_available"]) == (100, 80, 20)
    assert (
        p["newshosting"]["checked"],
        p["newshosting"]["available"],
        p["newshosting"]["unique_available"],
    ) == (100, 80, 20)
    assert done["summary"]["missing_everywhere"] == 0 and done["summary"]["comparable_articles"] == 100
    # STAT only, at most one connection each
    assert a.state.peak == 1 and b.state.peak == 1
    assert any(c.startswith("STAT <seg1@mock>") for c in a.state.commands)
    assert not any(c.startswith(("ARTICLE", "BODY")) for c in a.state.commands)

    contrib = viewer.get("/api/v1/usenet/contribution?period_days=30").json()
    by = {c["slug"]: c for c in contrib["items"]}
    e = by["eweka"]
    assert set(e) >= {
        "source_id",
        "label",
        "articles_checked",
        "available",
        "unique_available",
        "bytes_downloaded",
        "failures_by_reason",
        "coverage",
        "confidence",
        "method",
    }
    assert e["source_id"] == "provider:eweka" and e["articles_checked"] == 100 and e["available"] == 80
    assert e["unique_available"] == 20 and e["coverage"] == 1.0
    assert e["confidence"] == "low"  # one NZB only
    assert e["method"] == "explicit_test" and e["failures_by_reason"]["article_missing"] == 20
    assert e["bytes_downloaded"] is None  # no passive SAB data → no bytes claim
    assert any("geen garantie" in n.lower() for n in contrib["notes"])


def test_sampling_is_bounded_and_spread(app: Any, operator: Any, worker: Any, two_providers: Any) -> None:
    a, _ = two_providers
    nzb = _upload(operator, [f"seg{i}@mock" for i in range(1, 101)])
    t = operator.post(
        "/api/v1/usenet/article-tests",
        json={"nzb_id": nzb["id"], "sample_size": 10, "providers": ["eweka"], "confirm": True},
    ).json()
    worker.drain()
    stats = [c for c in a.state.commands if c.startswith("STAT")]
    assert len(stats) == 10
    assert "STAT <seg1@mock>" in stats and "STAT <seg91@mock>" in stats  # spread over the whole NZB
    res = operator.get(f"/api/v1/usenet/article-tests/{t['id']}").json()["summary"]["providers"]["eweka"]
    assert res["unique_available"] is None  # only one provider → no uniqueness claim


def test_provider_error_during_test_is_classified(app: Any, admin: Any, operator: Any, worker: Any) -> None:
    from msomocks import nntp

    srv = nntp.start(mode="expired", username=USER, password=PASSWORD)
    try:
        admin.post(
            "/api/v1/usenet/providers",
            json={
                "slug": "oud",
                "label": "Oud",
                "host": "127.0.0.1",
                "port": srv.port,
                "ssl": False,
                "username": USER,
                "password": PASSWORD,
            },
        )
        nzb = _upload(operator, ["seg1@mock", "seg2@mock"])
        t = operator.post("/api/v1/usenet/article-tests", json={"nzb_id": nzb["id"], "confirm": True}).json()
        worker.drain()
        done = operator.get(f"/api/v1/usenet/article-tests/{t['id']}").json()
        assert done["status"] == "failed"
        assert done["summary"]["providers"]["oud"]["status"] == "account_expired"
    finally:
        srv.stop()


def test_failed_download_triggers_test_when_opted_in(
    app: Any, serve: Any, admin: Any, db: Any, worker: Any, two_providers: Any
) -> None:
    from msomocks import sabnzbd as mock
    from msomocks.sabnzbd import _slot

    from mso import settings_registry as sr
    from mso.connectors.sabnzbd import SabClient
    from mso.features.usenet import history

    mock_app = mock.create_app(api_key=SAB_KEY, scenario="empty")
    admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    sr.put({"usenet.auto_test_failed": True, "usenet.auto_sample_after_send": False}, "test")
    with SabClient.from_config() as c:
        nzo = c.add_nzb(make_nzb([f"seg{i}@mock" for i in range(1, 31)]), "x.nzb", category="tv")
    slot = _slot(1, "Show.Name.S01E07.1080p.WEB.H264-GRP", "tv", "missing", int(time.time()))
    slot["nzo_id"] = nzo
    mock_app.state.sab.add_history(slot)
    history.ingest()
    test = db.fetchone("SELECT * FROM usenet_article_tests")
    assert test is not None and test["trigger"] == "failure"
    worker.drain()
    assert db.fetchone("SELECT status FROM usenet_article_tests")["status"] == "done"


def test_url_sent_job_not_testable_until_upload(app: Any, db: Any, operator: Any, two_providers: Any) -> None:
    from mso.features.usenet import nzbs

    nid = nzbs.record_send(nzo_id="SABnzbd_nzo_77", origin="send_url", title="x", category="tv", sha256="h")
    r = operator.post("/api/v1/usenet/article-tests", json={"nzb_id": nid, "confirm": True})
    assert r.status_code == 409 and "Upload" in r.json()["detail"]
    # attach the NZB to that job
    r = operator.post(
        "/api/v1/usenet/nzbs",
        json={
            "filename": "x.nzb",
            "nzo_id": "SABnzbd_nzo_77",
            "content_b64": base64.b64encode(make_nzb(["seg5@mock"])).decode(),
        },
    )
    assert r.status_code == 200 and r.json()["id"] == nid and r.json()["testable"]


def test_bad_upload_rejected(operator: Any) -> None:
    r = operator.post("/api/v1/usenet/nzbs", json={"filename": "x.nzb", "content_b64": "!!!"})
    assert r.status_code == 422
    r = operator.post(
        "/api/v1/usenet/nzbs",
        json={"filename": "x.nzb", "content_b64": base64.b64encode(b"<nzb></nzb>").decode()},
    )
    assert r.status_code == 422


def test_passive_contribution_from_sab_stats(app: Any, serve: Any, admin: Any, two_providers: Any) -> None:
    from msomocks import sabnzbd as mock

    from mso.features.usenet import history, metrics

    admin.put(
        "/api/v1/connectors/sabnzbd",
        json={"url": serve(mock.create_app(api_key=SAB_KEY)), "secrets": {"api_key": SAB_KEY}},
    )
    history.snapshot_server_stats()
    by = {c["slug"]: c for c in metrics.provider_contribution(30)}
    nh = by["newshosting"]
    assert nh["method"] == "passive" and nh["confidence"] == "low"
    assert nh["articles_checked"] == 0 and nh["unique_available"] is None  # never inferred from bytes
    assert nh["bytes_downloaded"] == 7 * 120_000_000
    assert nh["passive"]["success_ratio"] == 0.7
    assert nh["failures_by_reason"]["article_missing_passive"] == 7 * 42


# ------------------------------------------------------------------ NNTP client
def test_nntp_client_commands_and_limits() -> None:
    from msomocks import nntp

    from mso.features.usenet.nntp import NNTPClient, NNTPError

    srv = nntp.start(username=USER, password=PASSWORD, articles=["a@x"])
    try:
        c = NNTPClient("127.0.0.1", srv.port, use_ssl=False, timeout=5)
        c.connect()
        assert "AUTHINFO USER" in c.capabilities()
        with pytest.raises(NNTPError) as exc:
            c.group("alt.binaries.mso.test")  # before auth
        assert exc.value.code == 480
        c.login(USER, PASSWORD)
        count, first, last, _ = c.group("alt.binaries.mso.test")
        assert (count, first, last) == (300, 1, 300)
        over = c.over(291, 300)
        assert len(over) == 10 and over[-1].number == 300 and over[-1].message_id == "hdr299@mock"
        assert c.stat("a@x") is True and c.stat("<missing@x>") is False
        assert c.head("a@x")[0] == "Message-ID: <a@x>"
        with pytest.raises(NNTPError):
            c.stat("bad id\r\nQUIT")
        with pytest.raises(NNTPError):
            c.over(1, 300, max_bytes=500)  # response larger than the budget → stop and drop connection
        c.quit()
    finally:
        srv.stop()


def test_nntp_classification() -> None:
    from mso.features.usenet.nntp import classify_response

    assert classify_response(481, "Account expired", "auth") == "account_expired"
    assert classify_response(502, "Your account has expired", "auth") == "account_expired"
    assert classify_response(481, "Authentication failed", "auth") == "auth_failed"
    assert classify_response(482, "Authentication rejected", "auth") == "auth_failed"
    assert classify_response(400, "Too many connections", "greeting") == "rate_limited"
    assert classify_response(502, "Too many connections for user", "auth") == "rate_limited"
    assert classify_response(502, "Connection limit exceeded", "auth") == "rate_limited"
    assert classify_response(481, "Block exhausted, 0 bytes remaining", "auth") == "block_exhausted"
    assert classify_response(502, "Download limit reached", "auth") == "block_exhausted"
    assert classify_response(502, "Access denied", "greeting") == "auth_failed"


def test_nntp_timeout_is_bounded() -> None:
    from msomocks import nntp

    from mso.features.usenet.nntp import check_login

    srv = nntp.start(slow=3)
    try:
        t0 = time.monotonic()
        res = check_login("127.0.0.1", srv.port, use_ssl=False, username="user", password="pass", timeout=0.5)
        assert res["status"] == "timeout"
        assert time.monotonic() - t0 < 2.5
    finally:
        srv.stop()
