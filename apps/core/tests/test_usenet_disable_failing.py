"""1.4.0 · 1b: failing SABnzbd servers are switched off with ONE approval (destructive op, toestemming_nodig,
verified by reading SABnzbd's config back, reversible, audited, no secrets); reminders for expired accounts or
long-running errors; the cause of a failed download names the real state per server; the connector test reports
failing servers again when SABnzbd only keeps them in its warning list (1.3.1 regression)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

SAB_KEY = "SABGEHEIM-0123456789"
PROVIDER_PASSWORD = "PROVIDER-WACHTWOORD-geheim"


def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200
    return mock_app.state.sab


def _enabled(sab: Any) -> dict[str, int]:
    return {s["name"]: s["enable"] for s in sab.servers}


FAILING = ["news.newshosting.test", "nl.thundernews.test", "reader.usenetbucket.test", "reader.xsnews.test"]


def test_disable_failing_one_approval_verified_reversible_audited(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    # a provider with a password in MSO's vault: must never show up anywhere below
    r = admin.post(
        "/api/v1/usenet/providers",
        json={"slug": "xsnews", "label": "XSNews", "host": "127.0.0.1", "port": 1119, "ssl": False,
              "sab_server": "reader.xsnews.test", "username": "u", "password": PROVIDER_PASSWORD},
    )
    assert r.status_code == 200
    # suggestion: the UI shows "Voorstel: 4 kapotte servers uitschakelen"
    sug = viewer.get("/api/v1/usenet/recommendations").json()
    assert sug["disable"]["count"] == 4 and sorted(sug["disable"]["servers"]) == FAILING
    # preview only: nothing written yet
    assert operator.post("/api/v1/usenet/recommendations/disable-failing").status_code == 403
    ch = admin.post("/api/v1/usenet/recommendations/disable-failing").json()
    assert ch["status"] == "preview" and ch["risk"] == "toestemming_nodig" and ch["target"] == "sabnzbd"
    assert ch["title"] == "4 kapotte servers uitschakelen in SABnzbd"
    ops = {o["path"]: o for o in ch["ops"]}
    assert sorted(ops) == [f"servers/{n}/enable" for n in FAILING]
    assert all(o["before"] == 1 and o["after"] == 0 and o["destructive"] is True for o in ops.values())
    assert "XSNews: account verlopen" in ch["explanation"] and "terug" in ch["explanation"].lower()
    assert "Thundernews: toegang geweigerd" in ch["explanation"]
    assert "Newshosting: inloggen geweigerd" in ch["explanation"]
    assert admin.post("/api/v1/usenet/recommendations/disable-failing").json()["id"] == ch["id"]  # idempotent
    assert all(v == 1 for v in _enabled(sab).values())

    # approval needed, read-only blocks apply
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 409
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 409 and "alleen-lezen" in r.json()["detail"]
    assert all(v == 1 for v in _enabled(sab).values())

    # never automatic, even in beheermodus
    from fastapi import HTTPException

    from mso import changes

    state.set_state("mode", "manage")
    try:
        changes.apply(ch["id"], "systeem", automatic=True)
        raise AssertionError("destructive change applied automatically")
    except HTTPException as exc:
        assert exc.status_code == 409 and "nooit automatisch" in exc.detail

    # apply → verified by reading SABnzbd's config back
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    applied = r.json()
    assert applied["status"] == "applied" and all(o["result"] == "toegepast" for o in applied["ops"])
    assert _enabled(sab) == {"news.eweka.test": 1, **{n: 0 for n in FAILING}}

    # rollback switches them back on
    r = operator.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "test"})
    assert r.status_code == 200 and r.json()["status"] == "rolled_back"
    assert all(v == 1 for v in _enabled(sab).values())

    # audit trail without secrets
    actions = [r["action"] for r in db.fetchall("SELECT action FROM audit_log ORDER BY id")]
    for a in ("change.created", "change.approved", "change.applied", "change.rolled_back"):
        assert a in actions
    dump = json.dumps(db.fetchall("SELECT * FROM audit_log"), default=str)
    dump += json.dumps(db.fetchall("SELECT * FROM change_sets"), default=str)
    dump += json.dumps(db.fetchall("SELECT * FROM recommendations"), default=str)
    dump += admin.get(f"/api/v1/changes/{ch['id']}").text + viewer.get("/api/v1/usenet/recommendations").text
    assert SAB_KEY not in dump and PROVIDER_PASSWORD not in dump


def test_verification_failure_restores_and_nothing_to_disable(
    app: Any, admin: Any, serve: Any, manage_mode: None
) -> None:
    sab = _start(serve, admin, server_errors="report", ignore_set_config="1")
    ch = admin.post("/api/v1/usenet/recommendations/disable-failing").json()
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 502 and "verificatie" in r.json()["detail"]
    assert admin.get(f"/api/v1/changes/{ch['id']}").json()["status"] == "failed"
    assert all(s["enable"] == 1 for s in sab.servers)

    _start(serve, admin)  # no server errors
    r = admin.post("/api/v1/usenet/recommendations/disable-failing")
    assert r.status_code == 409 and "Geen kapotte" in r.json()["detail"]


def test_priority_proposal_marks_disable_ops_destructive(app: Any, admin: Any, serve: Any) -> None:
    _start(serve, admin)
    ch = admin.post(
        "/api/v1/usenet/recommendations/priority",
        json={"ops": [{"path": "servers/news.eweka.test/enable", "after": 0},
                      {"path": "servers/news.newshosting.test/priority", "after": 4}]},
    ).json()
    ops = {o["path"]: o for o in ch["ops"]}
    assert ops["servers/news.eweka.test/enable"]["destructive"] is True
    assert "destructive" not in ops["servers/news.newshosting.test/priority"]


def test_reminders_for_expired_account_and_long_errors(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import settings_registry as sr
    from mso.features.usenet import serverstatus

    _start(serve, admin, server_errors="field")
    serverstatus.refresh()
    open_keys = {r["dedupe_key"] for r in db.fetchall("SELECT dedupe_key FROM recommendations WHERE status='open'")}
    # expired → reminder right away; the others only after usenet.server_error_reminder_days (default 3)
    assert "usenet.sab_server_reminder:reader.xsnews.test" in open_keys
    assert "usenet.sab_server_reminder:news.newshosting.test" not in open_keys
    rem = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key='usenet.sab_server_reminder:reader.xsnews.test'")
    assert rem["title"].startswith("Herinnering: account bij XSNews is verlopen") and rem["risk"] == "advies"
    assert sr.get("usenet.server_error_reminder_days") == 3

    db.execute("UPDATE usenet_sab_servers SET since=%s", (datetime.now(UTC) - timedelta(days=4),))
    serverstatus.refresh()
    rem = db.fetchone(
        "SELECT * FROM recommendations WHERE dedupe_key='usenet.sab_server_reminder:nl.thundernews.test'"
    )
    assert rem["status"] == "open" and "al 4 dag(en) fouten (toegang geweigerd)" in rem["title"]

    # problem gone → reminder resolved
    _start(serve, admin)
    serverstatus.refresh()
    rem = db.fetchone(
        "SELECT status FROM recommendations WHERE dedupe_key='usenet.sab_server_reminder:nl.thundernews.test'"
    )
    assert rem["status"] == "resolved"


def test_connector_test_reports_failing_servers_from_warnings(app: Any, admin: Any, serve: Any) -> None:
    sab = _start(serve, admin, server_errors="field")
    assert all(not v for v in sab.server_errors.values())  # only in the warnings, as on the real system
    r = admin.post("/api/v1/connectors/sabnzbd/test").json()
    assert r["ok"] is True and "4 server(s) melden fouten" in r["message"], r["message"]
    assert sorted(r["capabilities"]["failing_servers"]) == ["Newshosting", "Thundernews", "UsenetBucket", "XSNews"]
    assert SAB_KEY not in json.dumps(r)


def test_failed_download_names_state_per_server(app: Any, db: Any, admin: Any, viewer: Any, serve: Any) -> None:
    import time

    from msomocks.sabnzbd import _slot

    from mso.features.usenet import serverstatus

    sab = _start(serve, admin, server_errors="field", scenario="empty")
    serverstatus.refresh()  # the 5-minute job: SABnzbd's server errors become events
    slot = _slot(1, "Show.Name.S01E01.1080p.WEB.H264-GRP", "tv", "unpack", int(time.time()))
    sab.add_history(slot)
    h = viewer.get("/api/v1/usenet/sab/history").json()["items"][0]
    cause = h["likely_cause"]
    assert cause["label"] == "Inlog- of accountproblemen bij servers"
    labels = {s["name"]: s["label"] for s in cause["servers"]}
    assert labels == {
        "Newshosting": "Inloggen geweigerd",
        "UsenetBucket": "Inloggen geweigerd",
        "Thundernews": "Toegang geweigerd",
        "XSNews": "Account verlopen",
    }
