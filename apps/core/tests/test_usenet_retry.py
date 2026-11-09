"""1.5.0 · Failed downloads caused by failing servers are retried in SABnzbd (mode=retry) once the failing servers
are off and a healthy server remains: one bundle proposal outside Autopilot, automatic in Autopilot, bounded
(per hour/day, once per job), one_way op verified by finding the job back in the queue, audited.

1.6.0 (deliberate change): only when the coverage improved after the job failed — a server recovered
(``usenet_server_autoheal.recovered_at``) or a new server appeared in SABnzbd. The tests below therefore add a
coverage gain (:func:`_gain`) where 1.5.0 retried right after switching the failing servers off."""

from __future__ import annotations

import time
from typing import Any

SAB_KEY = "SABGEHEIM-retry-0123456789"
FAILING = ["news.newshosting.test", "nl.thundernews.test", "reader.usenetbucket.test", "reader.xsnews.test"]


def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, scenario="empty", server_errors="field", **faults)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    assert admin.put("/api/v1/settings", json={"values": {"automation.max_per_hour": 60}}).status_code == 200
    return mock_app.state.sab


def _fail_jobs(sab: Any, n: int, kind: str = "auth") -> list[str]:
    """Failed SABnzbd jobs while the servers fail (+ one release problem that must never be retried)."""
    from msomocks import sabnzbd as mock

    from mso.features.usenet import history, serverstatus

    now = int(time.time())
    ids = []
    for i in range(n):
        slot = mock._slot(100 + i, f"Show.Name.S01E{i + 1:02d}.1080p.WEB.H264-GRP", "tv", kind, now - 60 * (n - i))
        sab.add_history(slot)
        ids.append(slot["nzo_id"])
    sab.add_history(mock._slot(200, "Movie.Other.2019.1080p.BluRay.x264-GRP", "movies", "encrypted", now - 30))
    serverstatus.refresh()
    history.ingest(force=True)
    return ids


def _gain(sab: Any, db: Any, *, kind: str = "new_server") -> None:
    """A coverage gain after the failures: a new server in SABnzbd (after MSO's first observation = baseline), or a
    switched-off server confirmed working again (``recovered_at``, set by the server Autopilot)."""
    from msomocks import sabnzbd as mock

    from mso.connectors.sabnzbd import SabClient
    from mso.features.usenet import coverage

    if kind == "recovered":
        db.execute(
            "INSERT INTO usenet_server_autoheal(server,status,recovered_at) "
            "VALUES ('news.eweka.test','recovered',now()) "
            "ON CONFLICT (server) DO UPDATE SET recovered_at=now()"
        )
        return
    with SabClient.from_config() as c:
        coverage.observe(c.servers())  # baseline: the servers MSO already knew
    sab.servers.append(mock._server("news.newbackbone.test", "Newbackbone", 0, 10))


def _switch_off_failing(sab: Any) -> None:
    from mso.features.usenet import serverstatus

    for s in sab.servers:
        if s["name"] in FAILING:
            s["enable"] = 0
    serverstatus.refresh()


def test_bundle_proposal_outside_autopilot_waits_for_servers_then_applies_verified(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    from mso import state
    from mso.features.usenet import retry

    sab = _start(serve, admin)
    ids = _fail_jobs(sab, 3)
    _gain(sab, db)
    state.set_state("mode", "manage")
    # failing servers still on → wait (retrying now would fail the same way)
    res = retry.propose()
    assert res["status"] == "waiting" and "kapotte server(s) staan nog aan" in res["reason"]
    assert res["count"] == 3 and not sab.retried

    _switch_off_failing(sab)
    r = operator.post("/api/v1/usenet/retries/propose")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3 and body["change_status"] == "preview"
    ch = viewer.get(f"/api/v1/changes/{body['change_id']}").json()
    assert ch["title"] == "3 mislukte downloads opnieuw proberen" and ch["target"] == "sabnzbd_retry"
    assert ch["risk"] == "automatisch_veilig"
    assert sorted(o["path"] for o in ch["ops"]) == sorted(f"retry/{i}" for i in ids)
    assert all(o["one_way"] and o["before"] == "mislukt" and o["after"] == "opnieuw in wachtrij" for o in ch["ops"])
    assert "Eweka" in ch["explanation"] or "eweka" in ch["explanation"]
    assert "server Newbackbone is nieuw ingeschakeld" in ch["explanation"]
    assert not sab.retried  # a proposal only
    # idempotent while open
    assert operator.post("/api/v1/usenet/retries/propose").json()["change_id"] == ch["id"]

    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    applied = r.json()
    assert applied["status"] == "applied" and all(o["result"] == "toegepast" for o in applied["ops"])
    assert sorted(sab.retried) == sorted(ids)
    assert {q["nzo_id"] for q in sab.queue} == set(ids)  # verified: back in the queue
    assert db.scalar("SELECT count(*) FROM usenet_retries WHERE status='retried'") == 3
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='sabnzbd.retry'") == 3
    # rollback is refused (cannot be undone), nothing deleted
    assert operator.post(f"/api/v1/changes/{ch['id']}/rollback", json={}).status_code == 409
    # once per job: nothing left to propose
    assert retry.propose()["status"] in ("nothing", "quota")
    ov = viewer.get("/api/v1/usenet/autopilot").json()["retry"]
    assert ov["counts"].get("retried") == 3 and ov["open_change"] is None
    assert SAB_KEY not in r.text


def test_autopilot_retries_automatically_within_caps_once_per_job(
    app: Any, db: Any, admin: Any, serve: Any
) -> None:
    from mso import state
    from mso.features.usenet import autoheal

    sab = _start(serve, admin)
    ids = _fail_jobs(sab, 4, kind="expired")
    assert admin.put("/api/v1/settings", json={"values": {"usenet.retry_per_hour": 2}}).status_code == 200
    state.set_state("mode", "autopilot")
    # first pass: xsnews (expired) goes off immediately, the others are still on → no retry yet
    out = autoheal.run()
    assert out["retry"]["status"] == "waiting" and not sab.retried
    _switch_off_failing(sab)
    out = autoheal.run()  # 1.6.0: switching the broken servers off adds no articles → no retry yet
    assert out["retry"]["status"] == "waiting" and out["retry"]["kind"] == "coverage" and not sab.retried
    _gain(sab, db, kind="recovered")
    out = autoheal.run()
    assert out["retry"]["status"] == "applied" and out["retry"]["count"] == 2 and out["retry"]["more"] == 2
    assert len(sab.retried) == 2
    out = autoheal.run()  # hour cap reached
    assert out["retry"]["status"] == "quota" and len(sab.retried) == 2
    assert admin.put("/api/v1/settings", json={"values": {"usenet.retry_per_hour": 10}}).status_code == 200
    autoheal.run()
    assert sorted(sab.retried) == sorted(ids)  # every job exactly once
    autoheal.run()
    assert sorted(sab.retried) == sorted(ids)
    assert "SABnzbd_nzo_0200" not in sab.retried  # encrypted archive: a release problem, never retried
    ch = db.fetchone("SELECT * FROM change_sets WHERE target='sabnzbd_retry' AND status='applied' ORDER BY id LIMIT 1")
    assert db.scalar(
        "SELECT count(*) FROM audit_log WHERE action='change.applied' AND change_id=%s "
        "AND (detail->>'automatic')::boolean", (ch["id"],)
    ) == 1
    # outcome of a retried job → verified
    from mso.domain import outcomes

    outcomes.upsert_outcome({"protocol": "usenet", "client": "sabnzbd", "client_ref": ids[0], "title": "x",
                             "status": "success"})
    from mso.features.usenet import retry

    assert retry.sync()["verified"] == 1


def test_day_cap_and_new_nzo_id(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state
    from mso.features.usenet import retry

    sab = _start(serve, admin, retry_new_id=1)
    _fail_jobs(sab, 3)
    _gain(sab, db)
    _switch_off_failing(sab)
    assert admin.put("/api/v1/settings", json={"values": {"usenet.retry_per_day": 1}}).status_code == 200
    state.set_state("mode", "autopilot")
    res = retry.propose()
    assert res["status"] == "applied" and res["count"] == 1
    # SABnzbd gave the job a new id: verification found it in the queue under that id
    row = db.fetchone("SELECT * FROM usenet_retries WHERE status='retried'")
    assert row["new_nzo_id"] and row["new_nzo_id"] != row["nzo_id"]
    assert retry.propose()["status"] == "quota"


def test_verification_failure_marks_change_failed(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state
    from mso.features.usenet import retry

    sab = _start(serve, admin, retry_noop=1)
    _fail_jobs(sab, 1)
    _gain(sab, db)
    _switch_off_failing(sab)
    state.set_state("mode", "autopilot")
    res = retry.propose()
    assert res["change"]["status"] == "failed" and res["change"]["auto_applied"] is False
    assert "verificatie mislukt" in (res["change"]["error"] or "")


def test_no_healthy_server_left_no_retry(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state
    from mso.features.usenet import retry, serverstatus

    sab = _start(serve, admin)
    _fail_jobs(sab, 1)
    for s in sab.servers:
        s["enable"] = 0 if s["name"] != "news.eweka.test" else 1
    sab.set_server_error("news.eweka.test", "Server news.eweka.test reported: 502 Authentication Failed")
    serverstatus.refresh()
    state.set_state("mode", "autopilot")
    res = retry.propose()
    assert res["status"] == "waiting" and not sab.retried


def test_failed_downloads_from_server_problems_are_not_fallback_problems(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    from mso.features.recovery import fallback
    from mso.features.usenet import retry

    sab = _start(serve, admin)
    _fail_jobs(sab, 2)
    _gain(sab, db, kind="recovered")
    # 1.6.0 integration: while a broken server is still on, retrying is blocked (propose() waits), so recovery must
    # not say "MSO laat SABnzbd dit opnieuw proberen" (deliberately changed: the failing servers go off first)
    o = db.fetchone("SELECT * FROM download_outcomes WHERE client='sabnzbd' AND failure_reason='auth_failure' "
                    "ORDER BY id LIMIT 1")
    assert retry.status_for(o) is None
    _switch_off_failing(sab)
    assert retry.status_for(o) == "candidate"
    fallback.process_failed()
    items = viewer.get("/api/v1/recovery/fallbacks").json()
    rows = [i for i in items["items"] if i["failure_reason"] == "auth_failure"]
    assert len(rows) == 2 and all(r["status"] == "retry" for r in rows)
    assert all("opnieuw proberen" in r["reason"] for r in rows)
    assert items["summary"]["by_status"].get("retry") == 2
