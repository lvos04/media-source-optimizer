"""1.6.0 WP-E2 (C27): background connector monitoring — background jobs keep connectors.status honest (key refused /
offline / time-out, status_since, last_ok_at), secrets never reach last_error, schedules of a connector that is
down run at most every 15 minutes, a recovery triggers a full round at once, and "Wacht op jou" only after a key
was refused > 15 min or the connector was unreachable > 24 h. Exercised over real HTTP against the mocks."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx
import pytest

from mso import jobs
from mso.connectors import base as cb
from mso.connectors import monitor

SAB_KEY = "SABGEHEIM-0123456789"


@pytest.fixture(autouse=True)
def _reset() -> Any:
    from mso.connectors.qbittorrent import QbitClient

    QbitClient.reset_cache()
    jobs._LAST_SCHEDULED.clear()
    yield
    QbitClient.reset_cache()
    jobs._LAST_SCHEDULED.clear()


@pytest.fixture()
def sab(app: Any, serve: Any, admin: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY)
    url = serve(mock_app)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200
    return mock_app


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _row(db: Any, cid: str) -> dict[str, Any]:
    return db.fetchone("SELECT * FROM connectors WHERE id=%s", (cid,))


def _run(worker: Any, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    j = jobs.enqueue(kind, payload or {}, max_attempts=1)
    worker.drain()
    return worker.db.fetchone("SELECT * FROM jobs WHERE id=%s", (j["id"],))


# ------------------------------------------------------------------ classification (unit)
def test_classify_connector_errors() -> None:
    from mso.connectors.prowlarr import ProwlarrError
    from mso.connectors.qbittorrent import QbitAuthError, QbitError
    from mso.connectors.sabnzbd import SabError

    assert monitor.classify_exception(SabError("SABnzbd weigert de API-sleutel", "auth"))[:2] == ("sabnzbd", "auth")
    assert monitor.classify_exception(SabError("x", "timeout"))[:2] == ("sabnzbd", "timeout")
    assert monitor.classify_exception(SabError("x", "unreachable"))[:2] == ("sabnzbd", "offline")
    assert monitor.classify_exception(SabError("SABnzbd-fout op 'queue': kapot", "api")) is None  # it answered
    assert monitor.classify_exception(QbitAuthError("nee"))[:2] == ("qbittorrent", "auth")
    assert monitor.classify_exception(QbitError("qBittorrent x gaf HTTP 500")) is None
    assert monitor.classify_exception(ProwlarrError("nee", "auth", 401))[:2] == ("prowlarr", "auth")
    assert monitor.classify_exception(ProwlarrError("x", "unavailable"))[:2] == ("prowlarr", "offline")
    assert monitor.classify_exception(ProwlarrError("geen sleutel", "not_configured")) is None
    assert monitor.classify_exception(cb.ConnectorNotConfigured("sabnzbd")) is None
    assert monitor.classify_exception(KeyError("bug")) is None
    # wrapped (raise … from …): the connector error in the chain counts
    try:
        try:
            raise SabError("x", "timeout")
        except SabError as inner:
            raise jobs.PermanentJobError("stop") from inner
    except jobs.PermanentJobError as outer:
        assert monitor.classify_exception(outer)[:2] == ("sabnzbd", "timeout")
    # secrets never leave the classifier
    hit = monitor.classify_exception(SabError("apikey=GEHEIM123 geweigerd", "auth"))
    assert hit and "GEHEIM123" not in hit[2]


def test_human_duration() -> None:
    assert monitor.human_duration(42) == "42 s"
    assert monitor.human_duration(35 * 60) == "35 min"
    assert monitor.human_duration(4 * 3600 + 600) == "4 u 10 min"
    assert monitor.human_duration(2 * 86400 + 4 * 3600 + 59) == "2 d 4 u"


# ------------------------------------------------------------------ SABnzbd over real HTTP
def test_sab_key_refused_then_replaced(app: Any, db: Any, sab: Any, worker: Any, viewer: Any) -> None:
    from mso.features.overview.autopilot import waiting

    job = _run(worker, "usenet.sab_ingest", {"force": True})
    assert job["status"] == "done"
    row = _row(db, "sabnzbd")
    assert row["status"] == "healthy" and row["last_ok_at"] is not None and row["status_since"] is not None
    assert row["failure_kind"] is None and row["last_error"] is None

    sab.state.api_key = "VERVANGEN-IN-SABNZBD"  # the owner replaced the key in SABnzbd
    job = _run(worker, "usenet.sab_ingest", {"force": True})
    assert job["status"] == "failed" and job["connector_id"] == "sabnzbd"
    row = _row(db, "sabnzbd")
    assert row["status"] == "auth_failed" and row["failure_kind"] == "auth"
    assert "API-sleutel" in row["last_error"] and SAB_KEY not in row["last_error"]
    since = row["status_since"]

    # overview: one honest line instead of a count of failed jobs
    o = viewer.get("/api/v1/overview").json()
    item = next(c for c in o["connectors"]["items"] if c["id"] == "sabnzbd")
    assert item["status"] == "auth_failed" and item["down_for_s"] is not None and item["job_failures_24h"] == 1
    assert item["summary"].startswith("SABnzbd: API-sleutel of inloggegevens geweigerd sinds")
    assert not any("mislukte taak" in i for i in o["health"]["issues"])
    assert any(i.startswith("SABnzbd: API-sleutel") for i in o["health"]["issues"])
    assert SAB_KEY not in str(o)

    # < 15 min: no "Wacht op jou" yet; the status_since does not move on repeated failures
    _run(worker, "usenet.sab_ingest", {"force": True})
    assert _row(db, "sabnzbd")["status_since"] == since
    assert not db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key='connector.down:sabnzbd'")

    # > 15 min refused → one "Wacht op jou" item (and an audit row), deduplicated
    db.execute("UPDATE connectors SET status_since = now() - interval '20 minutes' WHERE id='sabnzbd'")
    _run(worker, "usenet.sab_ingest", {"force": True})
    _run(worker, "usenet.sab_ingest", {"force": True})
    recs = db.fetchall("SELECT * FROM recommendations WHERE dedupe_key='connector.down:sabnzbd'")
    assert len(recs) == 1 and recs[0]["status"] == "open" and recs[0]["risk"] == "toestemming_nodig"
    assert "Instellingen" in recs[0]["explanation"] and SAB_KEY not in recs[0]["explanation"]
    assert any(i["id"] == f"rec:{recs[0]['id']}" for i in waiting(db)["items"])
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='connector.down'") == 1

    # key fixed → healthy again, item resolved, recovery audited, and a full round queued at once
    sab.state.api_key = SAB_KEY
    queued_before = db.scalar("SELECT count(*) FROM jobs WHERE status='queued'")
    j = jobs.enqueue("usenet.sab_ingest", {"force": True}, max_attempts=1)
    claimed = jobs.claim(worker.worker_id, kinds=["usenet.sab_ingest"], db=db)
    assert claimed["id"] == j["id"]
    assert jobs.run_one(claimed, db) == "done"
    row = _row(db, "sabnzbd")
    assert row["status"] == "healthy" and row["failure_kind"] is None and row["last_error"] is None
    assert db.scalar("SELECT status FROM recommendations WHERE dedupe_key='connector.down:sabnzbd'") == "resolved"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='connector.recovered'") == 1
    round_kinds = {r["kind"] for r in db.fetchall("SELECT kind FROM jobs WHERE status='queued'")}
    assert {"usenet.sab_stats", "usenet.sab_server_status"} <= round_kinds
    assert db.scalar("SELECT count(*) FROM jobs WHERE status='queued'") > queued_before


def test_sab_offline_alert_only_after_24h(app: Any, db: Any, admin: Any, worker: Any) -> None:
    url = f"http://127.0.0.1:{_closed_port()}"
    assert admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": SAB_KEY}}).status_code \
        == 200
    job = _run(worker, "usenet.sab_ingest", {"force": True})
    assert job["status"] == "failed" and job["connector_id"] == "sabnzbd"
    row = _row(db, "sabnzbd")
    assert row["status"] == "offline" and row["failure_kind"] == "offline"
    db.execute("UPDATE connectors SET status_since = now() - interval '23 hours' WHERE id='sabnzbd'")
    _run(worker, "usenet.sab_ingest", {"force": True})
    assert not db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key='connector.down:sabnzbd'")
    db.execute("UPDATE connectors SET status_since = now() - interval '25 hours' WHERE id='sabnzbd'")
    _run(worker, "usenet.sab_ingest", {"force": True})
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key='connector.down:sabnzbd'")
    assert rec["status"] == "open" and rec["title"] == "SABnzbd is al 1 d 1 u onbereikbaar"
    # dismissed by the owner: stays dismissed during this outage …
    db.execute("UPDATE recommendations SET status='dismissed' WHERE id=%s", (rec["id"],))
    _run(worker, "usenet.sab_ingest", {"force": True})
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "dismissed"
    # … but a disabled connector's alert is resolved by the daily sweep
    db.execute("UPDATE recommendations SET status='open' WHERE id=%s", (rec["id"],))
    db.execute("UPDATE connectors SET enabled=false WHERE id='sabnzbd'")
    monitor.sync_alert("sabnzbd", db=db)
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "resolved"


# ------------------------------------------------------------------ qBittorrent over real HTTP
def test_qbittorrent_offline_timeout_and_wrong_password(app: Any, db: Any, admin: Any, serve: Any,
                                                         worker: Any) -> None:
    from msomocks.qbittorrent import create_app

    from mso.connectors.qbittorrent import QbitClient

    # wrong password (qBittorrent answers 200 "Fails."): a login handshake alone never counts as "werkt"
    url = serve(create_app())
    admin.put("/api/v1/connectors/qbittorrent", json={"url": url, "secrets": {"username": "admin",
                                                                                "password": "fout-wachtwoord"}})
    job = _run(worker, "torrent.ingest")
    assert job["status"] == "failed" and job["connector_id"] == "qbittorrent"
    row = _row(db, "qbittorrent")
    assert row["status"] == "auth_failed" and "fout-wachtwoord" not in (row["last_error"] or "")
    # torrent.autopilot swallows its sub-task errors: it must not flip the status back to healthy
    QbitClient.reset_cache()
    _run(worker, "torrent.autopilot")
    assert _row(db, "qbittorrent")["status"] == "auth_failed"

    # right password → healthy
    QbitClient.reset_cache()
    admin.put("/api/v1/connectors/qbittorrent", json={"secrets": {"password": "adminadmin"}})
    assert _run(worker, "torrent.ingest")["status"] == "done"
    assert _row(db, "qbittorrent")["status"] == "healthy"

    # nothing listening → offline (raw httpx connect error, attributed by URL)
    QbitClient.reset_cache()
    admin.put("/api/v1/connectors/qbittorrent", json={"url": f"http://127.0.0.1:{_closed_port()}"})
    job = _run(worker, "torrent.ingest")
    assert job["status"] == "failed" and job["connector_id"] == "qbittorrent"
    row = _row(db, "qbittorrent")
    assert row["status"] == "offline" and row["failure_kind"] == "offline"

    # answers too slowly → offline with failure_kind timeout
    async def slow(scope: Any, receive: Any, send: Any) -> None:
        await asyncio.sleep(3)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"Ok."})

    QbitClient.reset_cache()
    slow_url = serve(slow)
    db.execute("UPDATE connectors SET url=%s, options='{\"timeout_s\": 0.5}'::jsonb WHERE id='qbittorrent'",
               (slow_url,))
    job = _run(worker, "torrent.ingest")
    assert job["status"] == "failed"
    row = _row(db, "qbittorrent")
    assert row["status"] == "offline" and row["failure_kind"] == "timeout"
    assert "Time-out" in row["last_error"]


# ------------------------------------------------------------------ Prowlarr over real HTTP
def test_prowlarr_key_refused(app: Any, db: Any, serve: Any, worker: Any) -> None:
    from msomocks import prowlarr as mock_prowlarr
    from slice_a_helpers import PROWLARR_KEY, configure_prowlarr

    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    configure_prowlarr(serve(mock))
    assert _run(worker, "prowlarr.sync", {"force": True})["status"] == "done"
    assert _row(db, "prowlarr")["status"] in ("healthy", "degraded")
    mock.state.faults["auth"] = "fail"
    job = _run(worker, "prowlarr.sync", {"force": True})
    assert job["status"] == "failed" and job["connector_id"] == "prowlarr"
    row = _row(db, "prowlarr")
    assert row["status"] == "auth_failed" and PROWLARR_KEY not in (row["last_error"] or "")


# ------------------------------------------------------------------ scheduling
def test_schedules_of_a_down_connector_run_at_most_every_15_min(app: Any, db: Any, sab: Any,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    sched = jobs.Schedule("test.sab_poll", 60, {}, enabled=lambda: cb.is_configured("sabnzbd"))
    free = jobs.Schedule("test.free_poll", 60, {})
    jobs.SCHEDULES.extend([sched, free])
    clock = [1_800_000_000.0]
    monkeypatch.setattr(jobs.time, "time", lambda: clock[0])
    try:
        def count(kind: str) -> int:
            return int(db.scalar("SELECT count(*) FROM jobs WHERE kind=%s", (kind,)))

        jobs.enqueue_due_schedules(db)
        assert count("test.sab_poll") == 1 and jobs.SCHEDULE_DEPS["test.sab_poll"] == {"sabnzbd"}
        assert jobs.SCHEDULE_DEPS["test.free_poll"] == set()
        monitor.record("sabnzbd", False, "offline", "Verbinding met SABnzbd geweigerd", db=db)
        for _ in range(14):  # every minute for 14 minutes: throttled, the independent schedule is not
            clock[0] += 60
            jobs.enqueue_due_schedules(db)
        assert count("test.sab_poll") == 1 and count("test.free_poll") == 15
        clock[0] += 61  # > 15 min since the last run
        jobs.enqueue_due_schedules(db)
        assert count("test.sab_poll") == 2
        # recovery: a full round at once (also for this schedule), even inside the throttle window
        clock[0] += 60
        assert monitor.record("sabnzbd", True, db=db) == "recovered"
        assert jobs.run_connector_round("sabnzbd", db) >= 1
        assert count("test.sab_poll") == 3
        clock[0] += 60
        jobs.enqueue_due_schedules(db)
        assert count("test.sab_poll") == 4  # healthy: every slot again
    finally:
        jobs.SCHEDULES.remove(sched)
        jobs.SCHEDULES.remove(free)


# ------------------------------------------------------------------ manual test + scope rules
def test_manual_test_tracks_since_and_last_ok(app: Any, db: Any) -> None:
    db.execute("UPDATE connectors SET url='http://sab.lan:8080', enabled=true WHERE id='sabnzbd'")
    cb.record_test("sabnzbd", cb.TestResult(True, "healthy", "ok"))
    r1 = _row(db, "sabnzbd")
    assert r1["status_since"] is not None and r1["last_ok_at"] is not None
    cb.record_test("sabnzbd", cb.TestResult(True, "healthy", "ok"))
    r1b = _row(db, "sabnzbd")
    assert r1b["status_since"] == r1["status_since"] and r1b["last_ok_at"] >= r1["last_ok_at"]
    cb.record_test("sabnzbd", cb.TestResult(False, "offline", "Time-out: dienst reageert niet binnen de limiet"))
    r2 = _row(db, "sabnzbd")
    assert r2["status"] == "offline" and r2["failure_kind"] == "timeout" and r2["status_since"] > r1["status_since"]
    assert r2["last_ok_at"] == r1b["last_ok_at"]  # a failed test never moves "laatst gelukt"


def test_no_monitoring_outside_jobs_and_disabled_connectors(app: Any, db: Any, sab: Any) -> None:
    from mso.connectors.sabnzbd import SabClient

    with SabClient.from_config() as c:  # an API request (no job scope): nothing is recorded
        c.queue()
    assert _row(db, "sabnzbd")["last_ok_at"] is None
    db.execute("UPDATE connectors SET enabled=false WHERE id='sabnzbd'")
    assert monitor.record("sabnzbd", False, "offline", "x", db=db) is None
    assert _row(db, "sabnzbd")["status"] != "offline"


def test_degraded_from_test_is_kept_and_mediaguard_is_not_monitored(app: Any, db: Any) -> None:
    db.execute("UPDATE connectors SET url='http://sab.lan:8080', enabled=true, status='degraded' WHERE id='sabnzbd'")
    monitor.record("sabnzbd", True, db=db)
    assert _row(db, "sabnzbd")["status"] == "degraded" and _row(db, "sabnzbd")["last_ok_at"] is not None
    assert "mediaguard" not in monitor.MONITORED
    req = httpx.Request("GET", "http://mg.lan/api")
    assert monitor.classify_exception(httpx.ConnectError("x", request=req)) is None


def test_last_error_is_redacted(app: Any, db: Any) -> None:
    db.execute("UPDATE connectors SET url='http://sab.lan:8080', enabled=true WHERE id='sabnzbd'")
    monitor.record("sabnzbd", False, "auth", "SABnzbd weigert apikey=GEHEIM999 password=hunter2", db=db)
    err = _row(db, "sabnzbd")["last_error"]
    assert "GEHEIM999" not in err and "hunter2" not in err


def test_overview_counts_only_non_outage_job_failures(app: Any, db: Any, viewer: Any) -> None:
    db.execute("UPDATE connectors SET url='http://sab.lan:8080', enabled=true WHERE id='sabnzbd'")
    monitor.record("sabnzbd", False, "offline", "Verbinding met SABnzbd geweigerd", db=db)
    for connector in ("sabnzbd", "sabnzbd", None):
        db.execute("INSERT INTO jobs(kind,status,connector_id,error) VALUES ('test.x','failed',%s,'kapot')",
                   (connector,))
    o = viewer.get("/api/v1/overview").json()
    assert o["connectors"]["outage_job_failures_24h"] == 2
    assert "1 mislukte taak/taken (24 u)" in o["health"]["issues"]
    assert any(i.startswith("SABnzbd onbereikbaar sinds") for i in o["health"]["issues"])


# ------------------------------------------------------------------ review fixes (1.6.0)
async def _redirect_app(scope: Any, receive: Any, send: Any) -> None:
    """An SSO / reverse proxy in front of the API: every call is answered with a redirect to a login page."""
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 302, "headers": [(b"location", b"https://sso.lan/login")]})
    await send({"type": "http.response.body", "body": b""})


def test_redirect_instead_of_an_api_answer_is_down(app: Any, db: Any, admin: Any, serve: Any, worker: Any) -> None:
    url = serve(_redirect_app)
    assert admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": SAB_KEY}}).status_code \
        == 200
    db.execute("UPDATE connectors SET status='healthy', status_since=now() WHERE id='sabnzbd'")
    job = _run(worker, "usenet.sab_ingest", {"force": True})
    assert job["status"] == "failed"
    row = _row(db, "sabnzbd")
    assert row["status"] == "offline" and row["failure_kind"] == "offline" and row["last_ok_at"] is None
    assert "HTTP 302" in row["last_error"] and "inlogpagina" in row["last_error"]
    # unit: only 2xx counts as evidence that the connector works
    token = monitor.begin("test")
    req = httpx.Request("GET", url + "/api")
    monitor.note_response("sabnzbd", req, httpx.Response(304, request=req))
    monitor.end(token, None, db=db)
    assert _row(db, "sabnzbd")["status"] == "offline"


def test_slow_prowlarr_search_timeout_is_not_an_outage(app: Any, db: Any, serve: Any, worker: Any,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    from slice_a_helpers import configure_prowlarr

    from mso.connectors.prowlarr import ProwlarrClient

    async def prowlarr(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        if scope["path"].endswith("/api/v1/search"):
            await asyncio.sleep(3)  # every indexer is slow: Prowlarr itself is fine
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"[]"})

    url = serve(prowlarr)
    configure_prowlarr(url)
    db.execute("UPDATE connectors SET status='healthy', status_since=now() WHERE id='prowlarr'")

    def search_job(ctx: Any) -> dict[str, Any]:
        return {"n": len(ProwlarrClient.from_config(timeout=0.5).search(query="Some.Show.S01E01"))}

    monkeypatch.setitem(jobs.HANDLERS, "test.slow_search", search_job)
    job = _run(worker, "test.slow_search")
    assert job["status"] == "failed" and "time-out" in job["error"] and job["connector_id"] is None
    row = _row(db, "prowlarr")
    assert row["status"] == "healthy" and row["failure_kind"] is None
    # a connect time-out on the same path, or a read time-out on any other call, still means "reageert niet"
    search = httpx.Request("GET", url + "/api/v1/search")
    other = httpx.Request("GET", url + "/api/v1/indexer")
    assert monitor.classify_exception(httpx.ReadTimeout("x", request=search)) is None
    assert monitor.classify_exception(httpx.ConnectTimeout("x", request=search))[:2] == ("prowlarr", "timeout")
    assert monitor.classify_exception(httpx.ReadTimeout("x", request=other))[:2] == ("prowlarr", "timeout")


def test_owner_decision_holds_for_the_outage_and_stale_items_resolve(app: Any, db: Any, admin: Any,
                                                                     worker: Any) -> None:
    url = f"http://127.0.0.1:{_closed_port()}"
    admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": SAB_KEY}})
    _run(worker, "usenet.sab_ingest", {"force": True})
    db.execute("UPDATE connectors SET status_since = now() - interval '25 hours' WHERE id='sabnzbd'")
    _run(worker, "usenet.sab_ingest", {"force": True})
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key='connector.down:sabnzbd'")
    assert rec["status"] == "open"
    # accepted by the owner: the next failing job (≤ 15 min later) neither reopens it nor audits it again
    assert admin.post(f"/api/v1/automation/recommendations/{rec['id']}/accept").status_code == 200
    _run(worker, "usenet.sab_ingest", {"force": True})
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "accepted"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='connector.down'") == 1
    # a new outage (after a recovery) asks again
    db.execute("UPDATE connectors SET status='healthy', status_since=now() WHERE id='sabnzbd'")
    db.execute("UPDATE connectors SET status='offline', status_since=now() - interval '26 hours' WHERE id='sabnzbd'")
    monitor.sync_alert("sabnzbd", db=db)
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "open"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='connector.down'") == 2
    # the owner switches the connector off while it is down: the item goes with the next scheduler tick
    assert admin.put("/api/v1/connectors/sabnzbd", json={"enabled": False}).status_code == 200
    jobs.enqueue_due_schedules(db)
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "resolved"


def test_mediaguard_gets_no_connector_alert(app: Any, db: Any) -> None:
    db.execute("UPDATE connectors SET url='http://mg.lan:8000', enabled=true, status='healthy' WHERE id='mediaguard'")
    cb.record_test("mediaguard", cb.TestResult(False, "auth_failed", "MediaGuard weigert de sleutel"))
    db.execute("UPDATE connectors SET status_since = now() - interval '2 days' WHERE id='mediaguard'")
    cb.record_test("mediaguard", cb.TestResult(False, "auth_failed", "MediaGuard weigert de sleutel"))
    monitor.sync_alert("mediaguard", db=db)
    assert not db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'connector.down:%'")
    assert _row(db, "mediaguard")["failure_kind"] == "auth"  # the status itself is still tracked
