"""1.6.0 WP-A · Usenet: the real cause, and retrying only when it can help — over real HTTP against the SABnzbd mock.

- reclassification of the SABnzbd history (incl. the archive, SABnzbd ≥ 4) after the upgrade and on request;
- one in-app advisory "SABnzbd kan downloads niet uitpakken" when the same environment error repeats;
- coverage gains (new server / recovered server) as the only retry trigger, per job "failed before the gain";
- unpack failures with complete data are never retried;
- the effectiveness brake (≥ 10 finished retries in 14 days and < 30 % success → pause; resume on coverage gain or
  by an operator)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

SAB_KEY = "SABGEHEIM-cause-0123456789"
FAILING = ["news.newshosting.test", "nl.thundernews.test", "reader.usenetbucket.test", "reader.xsnews.test"]


def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    faults.setdefault("scenario", "empty")
    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    assert admin.put("/api/v1/settings", json={"values": {"automation.max_per_hour": 60}}).status_code == 200
    return mock_app.state.sab


def _add(sab: Any, i: int, kind: str, *, ago_s: int = 60, name: str | None = None) -> str:
    from msomocks import sabnzbd as mock

    slot = mock._slot(i, name or f"Show.Name.S01E{i:02d}.1080p.WEB.H264-GRP{i}", "tv", kind, int(time.time()) - ago_s)
    sab.add_history(slot)
    return slot["nzo_id"]


def _observe() -> None:
    from mso.connectors.sabnzbd import SabClient
    from mso.features.usenet import coverage

    with SabClient.from_config() as c:
        coverage.observe(c.servers())


def _new_server(sab: Any, name: str = "news.newbackbone.test", display: str = "Newbackbone") -> None:
    from msomocks import sabnzbd as mock

    sab.servers.append(mock._server(name, display, 0, 10))


# ------------------------------------------------------------------ reclassification
def _old_label(db: Any, nzo: str, title: str, reason: str = "unpack_failed") -> None:
    """A row as 1.5.0 stored it: timing-only server cause, no facts, no classifier_version."""
    from mso.domain import outcomes

    outcomes.upsert_outcome({
        "protocol": "usenet", "client": "sabnzbd", "client_ref": nzo, "title": title, "status": "failed",
        "failure_reason": reason, "failure_detail": "Unpacking failed, see logfile",
        "finished_at": datetime.now(UTC) - timedelta(hours=1),
        "meta": {"sab_status": "Failed", "classification": {
            "certainty": "waarschijnlijk", "evidence": "Uitpakken mislukt terwijl SABnzbd rond dat moment serverfouten",
            "likely_cause": {"reason": "account_expired", "label": "Account verlopen", "certainty": "waarschijnlijk",
                             "servers": [{"server": "x", "name": "X", "status": "account_expired",
                                          "label": "Account verlopen"}]}}},
    })


def test_reclassify_history_and_archive_on_request(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    sab = _start(serve, admin, server_errors="report")
    a = _add(sab, 1, "unpack")
    b = _add(sab, 2, "unpack")
    c = _add(sab, 3, "repair")
    for nzo, title in ((a, "A"), (b, "B"), (c, "C"), ("SABnzbd_nzo_gone", "Gone")):
        _old_label(db, nzo, title, "incomplete" if nzo == c else "unpack_failed")
    sab.archive_job(b)  # removed from SABnzbd's history, kept in its archive (SABnzbd ≥ 4)

    assert viewer.post("/api/v1/usenet/sab/reclassify").status_code == 403
    r = operator.post("/api/v1/usenet/sab/reclassify")
    assert r.status_code == 200, r.text
    last = r.json()["last"]
    assert last["checked"] == 3 and last["not_in_sab"] == 1 and last["archive_read"] is True
    # C keeps a server cause, now named per server (mixed states) instead of 1.5.0's "account verlopen"
    assert last["changed"] == 3 and last["server_cause_removed"] == 2 and last["server_cause_kept"] == 1
    assert last["data_complete"] == 2
    assert last["reasons_after"] == {"unpack_failed": 2, "incomplete": 1}
    rows = {r["client_ref"]: r for r in db.fetchall("SELECT * FROM download_outcomes WHERE client='sabnzbd'")}
    for nzo in (a, b):
        meta = rows[nzo]["meta"]
        assert meta["classifier_version"] == 2 and meta["stage"]["data_complete"] is True
        assert "likely_cause" not in meta["classification"]
        assert meta["classification"]["evidence"].startswith("Data was compleet; uitpakken mislukte:")
        assert meta["sab_status"] == "Failed"  # other meta kept
    assert rows[c]["meta"]["stage"]["repair"] == "mislukt"
    assert rows[c]["meta"]["classification"]["likely_cause"]  # missing articles while servers failed: kept
    gone = rows["SABnzbd_nzo_gone"]["meta"]  # no longer in SABnzbd: old label, recognisable
    assert "classifier_version" not in gone and gone["classification"]["likely_cause"]["reason"] == "account_expired"
    audit = db.fetchone("SELECT * FROM audit_log WHERE action='usenet.reclassified'")
    assert audit["actor"] == "operator" and audit["detail"]["changed"] == 3 and audit["detail"]["not_in_sab"] == 1
    assert viewer.get("/api/v1/usenet/sab/reclassify").json()["last"]["checked"] == 3
    assert SAB_KEY not in r.text


def test_reclassify_runs_once_after_upgrade(app: Any, db: Any, admin: Any, serve: Any, worker: Any) -> None:
    from mso import jobs
    from mso.features.usenet import history

    sab = _start(serve, admin)
    nzo = _add(sab, 1, "unpack")
    _old_label(db, nzo, "A")
    assert history._reclassify_due() is True
    jobs.enqueue("usenet.reclassify", {})
    worker.drain()
    job = db.fetchone("SELECT * FROM jobs WHERE kind='usenet.reclassify'")
    assert job["status"] == "done", job
    assert history._reclassify_due() is False  # the schedule stops after one successful run
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.reclassified'") == 1
    jobs.enqueue("usenet.reclassify", {}, idempotency_key="again")
    worker.drain()
    again = db.fetchone("SELECT * FROM jobs WHERE idempotency_key='again'")
    assert again["result"] == {"skipped": "al gedaan"}
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.reclassified'") == 1


# ------------------------------------------------------------------ "SABnzbd kan downloads niet uitpakken"
def test_repeated_environment_error_raises_one_advisory_that_resolves_itself(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    from mso.features.usenet import history, unpack

    sab = _start(serve, admin)
    _add(sab, 1, "permission", ago_s=300)
    _add(sab, 2, "permission", ago_s=200)
    _add(sab, 3, "encrypted", ago_s=150)  # release problem: never part of it
    _add(sab, 4, "encrypted", ago_s=140)
    _add(sab, 5, "encrypted", ago_s=130)
    history.ingest()
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'usenet.unpack:%%'") == 0  # 2 < 3
    _add(sab, 6, "permission", ago_s=100)
    history.ingest()  # new failures → the detector runs
    recs = db.fetchall("SELECT * FROM recommendations WHERE dedupe_key LIKE 'usenet.unpack:%%'")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["title"] == "SABnzbd kan downloads niet uitpakken" and rec["status"] == "open"
    assert rec["risk"] == "toestemming_nodig" and rec["change_id"] is None
    assert '"Unpacking failed, write error or disk is full? Permission denied"' in rec["explanation"]
    assert "3 downloads mislukten" in rec["explanation"] and "rechten" in rec["explanation"]
    assert "Show.Name.S01E06" in rec["explanation"]
    assert rec["evidence"]["count"] == 3 and rec["evidence"]["problem"] == "no_permission"
    assert rec["evidence"]["data_complete"] == 3
    # visible in "Wacht op jou"
    waiting = viewer.get("/api/v1/overview/autopilot").json()["waiting"]["items"]
    assert any(i["title"] == "SABnzbd kan downloads niet uitpakken" for i in waiting)
    # deduplicated
    assert unpack.check()["open"] == 1
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'usenet.unpack:%%'") == 1
    # 3 days without the error → resolved by itself
    db.execute("UPDATE download_outcomes SET finished_at = finished_at - interval '4 days' "
               "WHERE meta->'stage'->>'problem'='no_permission'")
    assert unpack.check()["resolved"] == 1
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "resolved"
    # it comes back → shown again
    db.execute("UPDATE download_outcomes SET finished_at = now() WHERE meta->'stage'->>'problem'='no_permission'")
    unpack.check()
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "open"
    # a dismissal is kept
    db.execute("UPDATE recommendations SET status='dismissed' WHERE id=%s", (rec["id"],))
    unpack.check()
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "dismissed"


def test_same_error_needs_the_same_line(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso.features.usenet import history

    sab = _start(serve, admin)
    _add(sab, 1, "permission", ago_s=30)
    _add(sab, 2, "disk", ago_s=20)
    _add(sab, 3, "unpack", ago_s=10)
    history.ingest()
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'usenet.unpack:%%'") == 0
    _add(sab, 4, "unpack", ago_s=9)
    _add(sab, 5, "unpack", ago_s=8)  # 7-Zip "see logfile" ×3 with complete data
    history.ingest()
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key LIKE 'usenet.unpack:%%'")
    assert rec["evidence"]["problem"] == "unpacker_error" and '"Unpacking failed, see logfile"' in rec["explanation"]
    assert "unrar" in rec["explanation"]


# ------------------------------------------------------------------ coverage
def test_coverage_baseline_new_server_and_mso_probe(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso.features.usenet import coverage

    sab = _start(serve, admin, server_errors="field")
    sab.servers[1]["enable"] = 0  # newshosting off at the first observation
    _observe()
    assert coverage.last_gain() is None  # the servers MSO found first are the baseline
    assert db.scalar("SELECT count(*) FROM usenet_server_coverage") == 5
    _new_server(sab, "news.idle.test", "Idle")
    sab.servers[-1]["enable"] = 0
    _observe()
    assert coverage.last_gain() is None  # a new but disabled server adds nothing
    db.execute("INSERT INTO usenet_server_autoheal(server,status) VALUES ('news.newshosting.test','probing')")
    sab.servers[1]["enable"] = 1  # MSO's own probe of a server it switched off: only recovered_at counts
    _observe()
    assert coverage.last_gain() is None
    sab.servers[-1]["enable"] = 1  # the owner switched a dormant account on
    _observe()
    g = coverage.last_gain()
    assert g["server"] == "news.idle.test" and g["kind"] == "new_server"
    assert coverage.describe(g) == "server Idle is nieuw ingeschakeld"
    db.execute("UPDATE usenet_server_autoheal SET status='recovered', recovered_at=now() + interval '1 second' "
               "WHERE server='news.newshosting.test'")
    g = coverage.last_gain()
    assert g["kind"] == "recovered" and coverage.describe(g) == "server Newshosting werkt weer"


# ------------------------------------------------------------------ retry: only when it can help
def _ready_servers(sab: Any) -> None:
    """Failing servers off (1.5.0 condition that stays), SABnzbd's status refreshed."""
    from mso.features.usenet import serverstatus

    for s in sab.servers:
        if s["name"] in FAILING:
            s["enable"] = 0
    serverstatus.refresh()


def test_retry_only_retryable_causes_failed_before_the_gain(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    from mso import state
    from mso.features.usenet import history, retry

    sab = _start(serve, admin, server_errors="field")
    unpack_ids = [_add(sab, i, "unpack", ago_s=600 - i) for i in (1, 2)]
    missing = _add(sab, 3, "repair", ago_s=500)
    history.ingest(force=True)
    _ready_servers(sab)
    state.set_state("mode", "autopilot")
    res = retry.propose()
    # no coverage gain since the failures: nothing is retried, and only the retryable job waits for coverage
    assert res["status"] == "waiting" and res["kind"] == "coverage" and res["count"] == 1
    assert "Wacht op betere dekking" in res["reason"] and not sab.retried
    _new_server(sab)
    res = retry.propose()
    assert res["status"] == "applied" and res["count"] == 1, res
    assert sab.retried == [missing]  # complete data + unpack failure: never retried
    assert not set(unpack_ids) & set(sab.retried)
    ch = res["change"]
    assert "server Newbackbone is nieuw ingeschakeld" in ch["explanation"]
    assert ch["impact"]["Dekking verbeterd"] == "server Newbackbone is nieuw ingeschakeld"
    # a job that failed after the gain is not retried (the new coverage was already there)
    time.sleep(1.1)  # SABnzbd's timestamps are whole seconds
    late = _add(sab, 4, "repair", ago_s=0)
    history.ingest()
    assert retry.propose()["status"] in ("waiting", "nothing")
    assert late not in sab.retried
    ov = viewer.get("/api/v1/usenet/autopilot").json()["retry"]
    assert ov["coverage"]["description"] == "server Newbackbone is nieuw ingeschakeld"
    assert ov["efficacy"]["window_days"] == 14 and ov["brake"]["paused"] is False


def test_status_for_recovery(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso.domain import outcomes
    from mso.features.usenet import retry

    _start(serve, admin)
    base = {"protocol": "usenet", "client": "sabnzbd", "title": "x", "status": "failed",
            "finished_at": datetime.now(UTC) - timedelta(hours=2)}
    complete = {"stage": {"has_download": True, "data_complete": True, "unpack": "mislukt"}, "classifier_version": 2}
    gap = {"stage": {"has_download": True, "data_complete": False, "missing_articles": 40}, "classifier_version": 2}
    rows = {
        "v2-complete": {**base, "client_ref": "a", "failure_reason": "unpack_failed", "meta": complete},
        "v2-missing": {**base, "client_ref": "b", "failure_reason": "incomplete", "meta": gap},
        "v1-incomplete": {**base, "client_ref": "c", "failure_reason": "incomplete", "meta": {}},
        "v1-unpack-cause": {**base, "client_ref": "d", "failure_reason": "unpack_failed",
                            "meta": {"classification": {"likely_cause": {"reason": "account_expired"}}}},
        "server": {**base, "client_ref": "e", "failure_reason": "account_expired", "meta": {}},
    }
    for r in rows.values():
        outcomes.upsert_outcome(r)
    assert {k: retry.basis(v) for k, v in rows.items()} == {
        "v2-complete": None, "v2-missing": "missing", "v1-incomplete": "missing", "v1-unpack-cause": None,
        "server": "server",
    }
    assert all(retry.status_for(v) is None for v in rows.values())  # no coverage gain yet
    db.execute("INSERT INTO usenet_server_autoheal(server,status,recovered_at) VALUES ('s','recovered',now())")
    got = {k: retry.status_for(v) for k, v in rows.items()}
    assert got == {"v2-complete": None, "v2-missing": "candidate", "v1-incomplete": "candidate",
                   "v1-unpack-cause": None, "server": "candidate"}


# ------------------------------------------------------------------ effectiveness brake
def _attempts(db: Any, ok: int, bad: int, *, days_ago: float = 1) -> None:
    n = db.scalar("SELECT count(*) FROM usenet_retries") or 0
    for i in range(ok + bad):
        db.execute(
            "INSERT INTO usenet_retries(nzo_id,title,status,retried_at) "
            "VALUES (%s,%s,%s, now() - make_interval(secs => %s))",
            (f"att{n + i}", f"Att {n + i}", "verified" if i < ok else "failed", days_ago * 86400 + i),
        )


def test_brake_pauses_after_futile_retries_and_resumes_by_button(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    from mso import state
    from mso.features.usenet import history, retry

    sab = _start(serve, admin, server_errors="field")
    _add(sab, 1, "repair", ago_s=600)
    history.ingest(force=True)
    _ready_servers(sab)
    state.set_state("mode", "manage")
    _observe()
    _new_server(sab)
    res = retry.propose()
    assert res["status"] == "preview"  # an open bundle proposal outside Autopilot
    bundle_id = res["change"]["id"]
    _attempts(db, ok=2, bad=8)  # 10 finished, 20 % success
    _attempts(db, ok=5, bad=5, days_ago=20)  # outside the 14-day window
    res = retry.propose()
    assert res["status"] == "paused" and "lukte maar 20 %" in res["reason"] and not sab.retried
    assert viewer.get(f"/api/v1/changes/{bundle_id}").json()["status"] == "discarded"
    audit = db.fetchone("SELECT * FROM audit_log WHERE action='usenet.retry_paused'")
    assert audit["detail"]["attempts"] == 10 and audit["detail"]["verified"] == 2
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key='usenet.retry_paused'")
    assert rec["status"] == "open" and rec["risk"] == "advies"
    ov = viewer.get("/api/v1/usenet/autopilot").json()["retry"]
    assert ov["brake"]["paused"] is True and ov["efficacy"]["attempts"] == 10 and ov["efficacy"]["ratio"] == 0.2
    assert retry.status_for(db.fetchone("SELECT * FROM download_outcomes WHERE client='sabnzbd'")) is None
    assert retry.propose()["status"] == "paused"  # stays paused
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.retry_paused'") == 1

    assert viewer.post("/api/v1/usenet/retries/resume").status_code == 403
    r = operator.post("/api/v1/usenet/retries/resume")
    assert r.status_code == 200, r.text
    assert r.json()["brake"]["paused"] is False and r.json()["brake"]["resume_trigger"] == "user"
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "resolved"
    assert db.fetchone("SELECT * FROM audit_log WHERE action='usenet.retry_resumed'")["actor"] == "operator"
    assert operator.post("/api/v1/usenet/retries/resume").status_code == 409
    # attempts before the resume no longer count: the bundle is proposed again
    res = retry.propose()
    assert res["status"] == "preview" and res["count"] == 1


def test_brake_resumes_on_new_coverage_and_spares_effective_retries(
    app: Any, db: Any, admin: Any, serve: Any
) -> None:
    from mso import state
    from mso.features.usenet import retry

    sab = _start(serve, admin, server_errors="field")
    _ready_servers(sab)
    state.set_state("mode", "autopilot")
    _observe()
    _attempts(db, ok=3, bad=6)  # 9 finished: too few to judge
    assert retry.propose()["status"] != "paused"
    _attempts(db, ok=1, bad=0)  # 10 finished, 40 %: it works often enough
    assert retry.propose()["status"] != "paused"
    _attempts(db, ok=0, bad=10)  # 20 finished, 20 %
    assert retry.propose()["status"] == "paused"
    time.sleep(1.1)
    _new_server(sab)
    res = retry.propose()
    assert res["status"] != "paused"
    st = retry.brake_state()
    assert st["paused"] is False and st["resume_trigger"] == "coverage"
    audit = db.fetchone("SELECT * FROM audit_log WHERE action='usenet.retry_resumed'")
    assert audit["actor"] == "autopilot:usenet" and audit["detail"]["gain_server"] == "news.newbackbone.test"
