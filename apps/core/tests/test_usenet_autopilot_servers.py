"""1.5.0 · Autopilot for SABnzbd servers: failing servers off automatically (expired immediately, others after
``usenet.auto_disable_after_h``), never the last enabled server, back on via "Opnieuw proberen". Reversible change
sets, verified by read-back, audited.

1.6.0 (WP-B, deliberately changed): a server is switched off only after SABnzbd's own server test confirms the
problem, and back on only after that test passes (no blind probe, never given up while SABnzbd can test; the 1.5.0
probe with backoff is only the fallback when SABnzbd does not offer the test); "werkt weer" only once SABnzbd used the
server. More in test_usenet_server_tests.py."""

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
    # 1.6.0: SABnzbd's own test confirmed it; an expired account is never probed, only tested weekly
    assert row["next_probe_at"] is None and row["last_test_result"] == "account_expired"
    wait = row["next_test_at"] - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < wait <= timedelta(days=7)

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


def _test_due(db: Any, server: str) -> None:
    db.execute("UPDATE usenet_server_autoheal SET next_test_at = now() - interval '1 minute' WHERE server=%s",
               (server,))


def _use(sab: Any, server: str, nbytes: int = 50_000_000) -> None:
    """SABnzbd downloads something from ``server`` (its server_stats counters rise)."""
    from datetime import date

    day = date.today().isoformat()
    st = sab.stats.setdefault(server, {"daily": {}, "articles_tried": {}, "articles_success": {}})
    st["daily"][day] = st["daily"].get(day, 0) + nbytes
    st["articles_tried"][day] = st["articles_tried"].get(day, 0) + 70


def test_reenable_only_after_passing_test_then_recovered_on_use(app: Any, db: Any, admin: Any, serve: Any) -> None:
    """1.6.0 replaces test_probe_reenable_with_backoff_then_recovered: no blind probe; a test that still fails keeps
    the server off; after the owner fixed the account the passing test switches it on; it goes off again only when
    SABnzbd's test confirms the returning error; "werkt weer" only after SABnzbd really used it."""
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    assert _enabled(sab)[XS] == 0
    # test due, account still expired → stays off, next test in a week (expired)
    _test_due(db, XS)
    out = _pass()
    assert out["servers"]["tested"] == [XS] and out["servers"]["switched_on"] == [] and _enabled(sab)[XS] == 0
    row = _row(db, XS)
    assert row["tests_failed"] == 2 and row["next_probe_at"] is None  # the confirming test + this one
    assert timedelta(days=6) < row["next_test_at"] - datetime.now(UTC) <= timedelta(days=7)

    # the owner renewed the account → the next test passes → switched on and watched
    sab.fix_account(XS)
    _test_due(db, XS)
    out = _pass()
    assert out["servers"]["switched_on"] == [XS] and _enabled(sab)[XS] == 1
    row = _row(db, XS)
    assert row["status"] == "probing" and row["probe_trigger"] == "server_test" and row["probe_usage"] is not None
    on = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s", (f"usenet.auto_on:{XS}",))
    assert on["status"] == "applied" and not on["ops"][0].get("destructive")

    # the error returns and SABnzbd's test confirms it → off again
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.", at=int(time.time()) + 2)
    out = _pass()
    assert out["servers"]["probe_failed"] == [XS] and _enabled(sab)[XS] == 0
    row = _row(db, XS)
    assert row["status"] == "disabled" and row["probes"] == 1 and row["next_probe_at"] is None
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.server_probe_failed'") == 1

    # fixed again → on; no new error, but SABnzbd has not used it yet → not "recovered"
    db.execute("UPDATE usenet_sab_server_events SET last_at = now() - interval '1 day' WHERE server=%s", (XS,))
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()
    assert _enabled(sab)[XS] == 1 and _row(db, XS)["status"] == "probing"
    db.execute("UPDATE usenet_server_autoheal SET probe_started_at = now() - interval '3 hours' WHERE server=%s",
               (XS,))
    out = _pass()
    assert out["servers"]["recovered"] == [] and _row(db, XS)["status"] == "probing"
    assert "nog niet gebruikt" in _row(db, XS)["last_result"]
    # SABnzbd downloads from it without errors → recovered (recovered_at set)
    _use(sab, XS)
    out = _pass()
    row = _row(db, XS)
    assert out["servers"]["recovered"] == [XS] and row["status"] == "recovered" and row["recovered_at"] is not None


def test_bounded_probes_give_up_only_without_server_test(app: Any, db: Any, admin: Any, serve: Any) -> None:
    """1.6.0: with SABnzbd's test MSO never gives up (tests keep running); the 1.5.0 bounded probe remains only as the
    fallback when SABnzbd does not offer the test (and never for an expired account)."""
    from mso import state

    _start(serve, admin, server_errors="field")
    assert admin.put("/api/v1/settings", json={"values": {"usenet.auto_probe_max": 1}}).status_code == 200
    state.set_state("mode", "autopilot")
    _pass()
    for _ in range(3):
        _test_due(db, XS)
        _pass()
    row = _row(db, XS)
    assert row["status"] == "disabled" and row["tests_failed"] == 4 and row["next_test_at"] is not None

    # fallback: SABnzbd without the test → blind probe with backoff for a login problem, given up after the max
    sab2 = _start(serve, admin, server_errors="field", test_server="unsupported")
    db.execute("DELETE FROM usenet_server_autoheal")
    db.execute("DELETE FROM usenet_server_tests")
    _backdate(db)
    _pass()
    nh = "news.newshosting.test"
    row = _row(db, nh)
    assert _enabled(sab2)[nh] == 0 and row["last_test_result"] == "unsupported"
    assert timedelta(hours=23) < row["next_probe_at"] - datetime.now(UTC) <= timedelta(hours=24)
    assert _row(db, XS)["next_probe_at"] is None  # expired: never a probe
    db.execute("UPDATE usenet_server_autoheal SET next_probe_at = now() - interval '1 minute' WHERE server=%s", (nh,))
    out = _pass()
    assert nh in out["servers"]["switched_on"] and _row(db, nh)["probe_trigger"] == "autopilot"
    sab2.set_server_error(nh, f"Server {nh} reported: 502 Authentication Failed", at=int(time.time()) + 2)
    _pass()
    row = _row(db, nh)
    assert row["status"] == "given_up" and row["next_probe_at"] is None and _enabled(sab2)[nh] == 0
    _pass()  # no more automatic probes
    assert _enabled(sab2)[nh] == 0


def test_own_successful_test_switches_server_back_on(app: Any, db: Any, admin: Any, serve: Any) -> None:
    """1.6.0 (deliberately changed): MSO's own NNTP test (its credentials may differ from SABnzbd's) now triggers
    SABnzbd's test right away; only when that passes too does the server go back on."""
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
    # MSO's own NNTP test with credentials succeeds, but SABnzbd still can't log in → SABnzbd's test runs, stays off
    db.execute("UPDATE usenet_providers SET status='ok', last_check_at=now() + interval '1 second' WHERE slug='xsnews'")
    sab.warnings.clear()
    out = _pass()
    assert out["servers"]["tested"] == [XS] and out["servers"]["switched_on"] == [] and _enabled(sab)[XS] == 0
    assert db.scalar("SELECT trigger FROM usenet_server_tests WHERE server=%s ORDER BY id DESC LIMIT 1",
                     (XS,)) == "own_test"
    # the account works in SABnzbd too; a newer own test → SABnzbd's test passes → back on, watched
    sab.fix_account(XS)
    db.execute("UPDATE usenet_providers SET last_check_at=now() + interval '5 seconds' WHERE slug='xsnews'")
    out = _pass()
    assert out["servers"]["switched_on"] == [XS] and _enabled(sab)[XS] == 1
    assert _row(db, XS)["probe_trigger"] == "server_test"


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
