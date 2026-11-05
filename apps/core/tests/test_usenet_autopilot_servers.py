"""1.5.0 · Autopilot for SABnzbd servers: failing servers off automatically (expired immediately, others after
``usenet.auto_disable_after_h``), never the last enabled server, back on as a probe with backoff, on MSO's own
successful test, or via "Opnieuw proberen". Reversible change sets, verified by read-back, audited."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

SAB_KEY = "SABGEHEIM-autopilot-0123456789"
FAILING = ["news.newshosting.test", "nl.thundernews.test", "reader.usenetbucket.test", "reader.xsnews.test"]
XS = "reader.xsnews.test"


def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    # plenty of room under the global automation cap for these tests
    assert admin.put("/api/v1/settings", json={"values": {"automation.max_per_hour": 60}}).status_code == 200
    return mock_app.state.sab


def _enabled(sab: Any) -> dict[str, int]:
    return {s["name"]: s["enable"] for s in sab.servers}


def _pass() -> dict[str, Any]:
    from mso.features.usenet import autoheal, serverstatus

    serverstatus.refresh()
    return autoheal.run()


def _backdate(db: Any, hours: float = 7) -> None:
    db.execute("UPDATE usenet_sab_servers SET since = now() - make_interval(hours => %s)", (hours,))
    db.execute("UPDATE usenet_sab_server_events SET first_at = now() - make_interval(hours => %s)", (hours,))


def _row(db: Any, server: str) -> dict[str, Any] | None:
    return db.fetchone("SELECT * FROM usenet_server_autoheal WHERE server=%s", (server,))


def test_autopilot_switches_failing_servers_off_expired_immediately(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    # beheermodus without Autopilot: nothing is switched off
    state.set_state("mode", "manage")
    out = _pass()
    assert out["servers"]["switched_off"] == [] and all(v == 1 for v in _enabled(sab).values())

    state.set_state("mode", "autopilot")
    out = _pass()
    # "account verlopen" at once; the others only after usenet.auto_disable_after_h (6 h)
    assert out["servers"]["switched_off"] == [XS]
    assert _enabled(sab)[XS] == 0 and sum(_enabled(sab).values()) == 4
    ch = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s", (f"usenet.auto_off:{XS}",))
    assert ch["status"] == "applied" and ch["risk"] == "automatisch_veilig" and ch["target"] == "sabnzbd"
    op = ch["ops"][0]
    assert op["destructive"] is True and op["before"] == 1 and op["after"] == 0 and op["result"] == "toegepast"
    assert "XSNews" in ch["title"] and "account verlopen" in ch["title"]
    assert db.scalar(
        "SELECT count(*) FROM audit_log WHERE action='change.applied' AND change_id=%s AND "
        "(detail->>'automatic')::boolean", (ch["id"],)
    ) == 1
    row = _row(db, XS)
    assert row["status"] == "disabled" and row["reason"] == "account_expired" and row["probes"] == 0
    wait = row["next_probe_at"] - datetime.now(UTC)
    assert timedelta(hours=23) < wait <= timedelta(hours=24)

    # failing for longer than 6 h → the other three go off too; eweka (works) stays on
    _backdate(db)
    out = _pass()
    assert sorted(out["servers"]["switched_off"]) == sorted(set(FAILING) - {XS})
    assert _enabled(sab) == {"news.eweka.test": 1, **{n: 0 for n in FAILING}}
    # reversible: rollback switches the server back on
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "test"})
    assert r.status_code == 200, r.text
    assert _enabled(sab)[XS] == 1
    # the API shows the automatic state per server
    items = {i["name"]: i for i in viewer.get("/api/v1/usenet/sab/servers").json()["items"]}
    assert items["news.newshosting.test"]["autopilot"]["status"] == "disabled"
    ov = viewer.get("/api/v1/usenet/autopilot").json()
    assert ov["autopilot"] is True and ov["servers"]["nl.thundernews.test"]["reason_label"] == "Toegang geweigerd"


def test_never_switches_off_the_last_enabled_server(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    sab.set_server_error("news.eweka.test", "Server news.eweka.test reported: 502 Authentication Failed")
    sab.server_errors["news.eweka.test"] = ""
    state.set_state("mode", "autopilot")
    _pass()
    _backdate(db)
    out = _pass()
    assert "news.eweka.test" in out["servers"]["kept_last"]
    assert _enabled(sab) == {"news.eweka.test": 1, **{n: 0 for n in FAILING}}
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.server_kept_last'") >= 1
    # only one server, failing: stays on
    sab.servers = [s for s in sab.servers if s["name"] == "news.eweka.test"]
    out = _pass()
    assert out["servers"]["switched_off"] == [] and _enabled(sab) == {"news.eweka.test": 1}


def test_probe_reenable_with_backoff_then_recovered(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    assert _enabled(sab)[XS] == 0
    # probe due → switched on again and watched
    db.execute("UPDATE usenet_server_autoheal SET next_probe_at = now() - interval '1 minute' WHERE server=%s", (XS,))
    out = _pass()
    assert out["servers"]["switched_on"] == [XS] and _enabled(sab)[XS] == 1
    row = _row(db, XS)
    assert row["status"] == "probing" and row["probe_trigger"] == "autopilot"
    on = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s", (f"usenet.auto_on:{XS}",))
    assert on["status"] == "applied" and not on["ops"][0].get("destructive")

    # the error returns → off again, backoff doubles (48 h)
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.", at=int(time.time()) + 2)
    out = _pass()
    assert out["servers"]["probe_failed"] == [XS] and _enabled(sab)[XS] == 0
    row = _row(db, XS)
    assert row["status"] == "disabled" and row["probes"] == 1
    wait = row["next_probe_at"] - datetime.now(UTC)
    assert timedelta(hours=47) < wait <= timedelta(hours=48)
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.server_probe_failed'") == 1

    # next probe: no new error for usenet.probe_watch_h → recovered
    db.execute("UPDATE usenet_sab_server_events SET last_at = now() - interval '1 day' WHERE server=%s", (XS,))
    db.execute("UPDATE usenet_server_autoheal SET next_probe_at = now() - interval '1 minute' WHERE server=%s", (XS,))
    sab.warnings.clear()
    sab.server_errors[XS] = ""
    _pass()
    assert _enabled(sab)[XS] == 1 and _row(db, XS)["status"] == "probing"
    db.execute("UPDATE usenet_server_autoheal SET probe_started_at = now() - interval '3 hours' WHERE server=%s",
               (XS,))
    out = _pass()
    assert out["servers"]["recovered"] == [XS] and _row(db, XS)["status"] == "recovered"


def test_bounded_probes_give_up(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    assert admin.put("/api/v1/settings", json={"values": {"usenet.auto_probe_max": 1}}).status_code == 200
    state.set_state("mode", "autopilot")
    _pass()
    db.execute("UPDATE usenet_server_autoheal SET next_probe_at = now() - interval '1 minute' WHERE server=%s", (XS,))
    _pass()
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.", at=int(time.time()) + 2)
    _pass()
    row = _row(db, XS)
    assert row["status"] == "given_up" and row["next_probe_at"] is None and _enabled(sab)[XS] == 0
    _pass()  # no more automatic probes
    assert _enabled(sab)[XS] == 0


def test_own_successful_test_switches_server_back_on(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    r = admin.post(
        "/api/v1/usenet/providers",
        json={"slug": "xsnews", "label": "XSNews", "host": "127.0.0.1", "port": 1119, "ssl": False,
              "sab_server": XS, "username": "u", "password": "p"},
    )
    assert r.status_code == 200, r.text
    state.set_state("mode", "autopilot")
    _pass()
    assert _enabled(sab)[XS] == 0
    # MSO's own NNTP test with credentials succeeds (after the switch-off) → back on, watched
    db.execute("UPDATE usenet_providers SET status='ok', last_check_at=now() + interval '1 second' WHERE slug='xsnews'")
    sab.warnings.clear()
    out = _pass()
    assert out["servers"]["switched_on"] == [XS] and _enabled(sab)[XS] == 1
    assert _row(db, XS)["probe_trigger"] == "own_test"


def test_opnieuw_proberen_reenables_and_watches(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    state.set_state("mode", "read_only")
    r = operator.post(f"/api/v1/usenet/sab/servers/{XS}/retry")
    assert r.status_code == 409 and _enabled(sab)[XS] == 0  # alleen-lezen
    assert viewer.post(f"/api/v1/usenet/sab/servers/{XS}/retry").status_code == 403
    state.set_state("mode", "manage")
    r = operator.post(f"/api/v1/usenet/sab/servers/{XS}/retry")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["change"]["status"] == "applied" and body["state"]["status"] == "probing"
    assert body["state"]["probe_trigger"] == "user" and _enabled(sab)[XS] == 1
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.server_retry'") == 1
    assert operator.post("/api/v1/usenet/sab/servers/nope/retry").status_code == 404
    assert SAB_KEY not in r.text
