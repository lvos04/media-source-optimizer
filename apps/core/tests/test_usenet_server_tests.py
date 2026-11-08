"""1.6.0 WP-B · SABnzbd's own server test instead of blind production probes (C10), and account expiry / block quota
from SABnzbd (C11). Everything over real HTTP against the SABnzbd mock, which follows SABnzbd 5.1's
``api.test_nntp_server_dict`` (masked password + ``server=<name>`` → SABnzbd's stored password)."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

SAB_KEY = "SABGEHEIM-servertest-0123456789"
XS = "reader.xsnews.test"
NH = "news.newshosting.test"
EW = "news.eweka.test"
BLOCK = "block.example.test"


def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    assert admin.put("/api/v1/settings", json={"values": {"automation.max_per_hour": 60}}).status_code == 200
    return mock_app.state.sab


def _client() -> Any:
    from mso.connectors.sabnzbd import SabClient

    return SabClient.from_config()


def _enabled(sab: Any) -> dict[str, int]:
    return {s["name"]: s["enable"] for s in sab.servers}


def _srv(sab: Any, name: str) -> dict[str, Any]:
    return next(s for s in sab.servers if s["name"] == name)


def _pass() -> dict[str, Any]:
    from mso.features.usenet import autoheal, serverstatus

    serverstatus.refresh()
    return autoheal.run()


def _row(db: Any, server: str) -> dict[str, Any] | None:
    return db.fetchone("SELECT * FROM usenet_server_autoheal WHERE server=%s", (server,))


def _backdate(db: Any, hours: float = 7) -> None:
    db.execute("UPDATE usenet_sab_servers SET since = now() - make_interval(hours => %s)", (hours,))
    db.execute("UPDATE usenet_sab_server_events SET first_at = now() - make_interval(hours => %s)", (hours,))


def _test_due(db: Any, server: str) -> None:
    db.execute("UPDATE usenet_server_autoheal SET next_test_at = now() - interval '1 minute' WHERE server=%s",
               (server,))


def _rec(db: Any, key: str) -> dict[str, Any] | None:
    return db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s", (key,))


# ------------------------------------------------------------------ connector + classification
def test_connector_test_server_uses_sabnzbds_stored_password(app: Any, admin: Any, serve: Any) -> None:
    from mso.connectors.sabnzbd import SabError

    sab = _start(serve, admin, server_errors="report")
    with _client() as c:
        ok = c.test_server(EW)
        bad = c.test_server(NH)
        with pytest.raises(SabError) as exc:
            c.test_server("bestaat.niet")
    assert ok == {"supported": True, "ok": True, "message": "Connection Successful!"}
    assert bad["supported"] is True and bad["ok"] is False and bad["message"] == "502 Authentication Failed"
    assert exc.value.code == "invalid"
    sent = sab.test_calls[0]
    # exactly what SABnzbd's own 'Test server' button sends: the masked password + the server name
    assert sent["server"] == EW and sent["host"] == EW and set(sent["password"]) == {"*"}
    assert sent["username"] == "user1" and sent["connections"] == "20" and sent["ssl"] == "1"
    # the username never comes back: SABnzbd's answer is scrubbed
    sab.test_results[EW] = (False, "481 Authentication failed for user1")
    with _client() as c:
        res = c.test_server(EW)
    assert "user1" not in res["message"] and "<gebruiker>" in res["message"]
    # a masked password without a known server name cannot be resolved by SABnzbd (as in 5.1)
    assert sab.test_server({"host": EW, "password": "*****", "username": "user1"}) == (
        False, "Password masked in ******, please re-enter")


@pytest.mark.parametrize("fault,message", [("unsupported", "biedt de servertest niet aan"),
                                           ("locked", "vergrendeld")])
def test_connector_test_server_unsupported(app: Any, admin: Any, serve: Any, fault: str, message: str) -> None:
    _start(serve, admin, test_server=fault)
    with _client() as c:
        res = c.test_server(EW)
    assert res["supported"] is False and res["ok"] is None and message in res["message"]


def test_connector_servers_reads_account_fields_and_fingerprint(app: Any, admin: Any, serve: Any) -> None:
    from mso.connectors.sabnzbd import SabClient

    sab = _start(serve, admin)
    _srv(sab, BLOCK).update(expire_date="2026-12-01", quota="500G", usage_at_start=1234)
    with _client() as c:
        items = {s["name"]: s for s in c.servers(fingerprint_key=b"k" * 32)}
        plain = {s["name"]: s for s in c.servers()}
    b = items[BLOCK]
    assert b["expire_date"] == "2026-12-01" and b["quota_bytes"] == 500 * 1024**3 and b["usage_at_start"] == 1234
    assert items[EW]["expire_date"] is None and items[EW]["quota_bytes"] is None and items[EW]["usage_at_start"] == 0
    assert "username" not in b and "password" not in b and "fingerprint" not in plain[BLOCK]
    fp = b["fingerprint"]
    _srv(sab, BLOCK)["username"] = "iemand-anders"
    with _client() as c:
        fp2 = {s["name"]: s for s in c.servers(fingerprint_key=b"k" * 32)}[BLOCK]["fingerprint"]
    assert fp2 != fp and "iemand-anders" not in fp2
    for raw, want in (("1.5T", int(1.5 * 1024**4)), ("500", 500), ("100 GB", 100 * 1024**3), ("", None),
                      ("abc", None), ("0", None), (None, None)):
        assert SabClient._sab_units(raw) == want, raw
    assert SabClient._sab_date("2026-02-30") is None and SabClient._sab_date("") is None


@pytest.mark.parametrize("raw,want", [
    ({"supported": True, "ok": True, "message": "Verbinding geslaagd!"}, "ok"),
    ({"supported": True, "ok": False, "message": "502 Authentication Failed"}, "auth_failed"),
    ({"supported": True, "ok": False, "message": "Authentication Failed"}, "auth_failed"),
    ({"supported": True, "ok": False, "message": "482 Account expired"}, "account_expired"),
    ({"supported": True, "ok": False, "message": "502 Access Denied"}, "access_denied"),
    ({"supported": True, "ok": False, "message": "481 Too many connections"}, "rate_limited"),
    ({"supported": True, "ok": False, "message": "Server requires username and password."}, "auth_failed"),
    ({"supported": True, "ok": False, "message": "Timed out"}, "unreachable"),
    ({"supported": True, "ok": False, "message": 'Server address "news.x.test:563" is not valid.'}, "unreachable"),
    ({"supported": True, "ok": False, "message": "Unknown SSL protocol: Try disabling SSL"}, "tls_error"),
    ({"supported": True, "ok": False, "message": "Could not determine connection result ([Errno 111] Connection "
                                                  "refused)"}, "unreachable"),
    ({"supported": True, "ok": False, "message": "Password masked in ******, please re-enter"}, "unknown"),
    ({"supported": True, "ok": None, "message": ""}, "unknown"),
    ({"supported": False, "ok": None, "message": ""}, "unsupported"),
])
def test_classify_test_real_sabnzbd_messages(raw: dict[str, Any], want: str) -> None:
    from mso.features.usenet.serverstatus import classify_test

    assert classify_test(raw) == want


def test_usage_and_interval_helpers() -> None:
    from mso.features.usenet import autoheal

    now = datetime.now(UTC)
    base = autoheal.usage_marker({"total": 1000, "articles_tried": {now.date().isoformat(): 5}}, now)
    assert autoheal.used_since(base, {"total": 1000, "articles_tried": {now.date().isoformat(): 5}}) is False
    assert autoheal.used_since(base, {"total": 1000, "articles_tried": {now.date().isoformat(): 9}}) is True
    assert autoheal.used_since(base, {"total": 1500, "articles_tried": {}}) is True
    assert autoheal.used_since(base, {"total": 10, "articles_tried": {}}) is None  # SABnzbd statistics reset
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    assert autoheal.used_since(base, {"total": 1000, "articles_tried": {tomorrow: 1}}) is True


def test_test_interval_6h_24h_weekly(app: Any) -> None:
    from mso.features.usenet import autoheal

    now = datetime.now(UTC)
    assert autoheal.test_interval("auth_failed", now - timedelta(days=1), now) == timedelta(hours=6)
    assert autoheal.test_interval("auth_failed", now - timedelta(days=8), now) == timedelta(hours=24)
    assert autoheal.test_interval("account_expired", now, now) == timedelta(days=7)


# ------------------------------------------------------------------ API
def test_server_test_endpoint_any_mode_roles_and_no_secrets(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    _start(serve, admin, server_errors="report")  # read-only mode (default): a test changes nothing
    r = operator.post(f"/api/v1/usenet/sab/servers/{NH}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["test"]["result"] == "auth_failed" and body["test"]["label"] == "Inloggen geweigerd"
    assert body["change"] is None and SAB_KEY not in r.text and "user2" not in r.text
    assert viewer.post(f"/api/v1/usenet/sab/servers/{NH}/test").status_code == 403
    assert operator.post("/api/v1/usenet/sab/servers/nope/test").status_code == 404
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.server_test' AND actor='operator'") == 1
    assert db.scalar("SELECT count(*) FROM change_sets") == 0
    ov = viewer.get("/api/v1/usenet/sab/server-tests").json()
    assert ov["items"][NH]["latest"]["result"] == "auth_failed" and ov["interval_h"] == 6
    assert "user2" not in str(ov)


# ------------------------------------------------------------------ Autopilot: confirm before off
def test_confirming_test_keeps_a_working_server_on(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    sab.fix_account(NH)  # the owner fixed the password; SABnzbd's warning list still shows the old error
    sab.set_server_error(NH, f"Server {NH} reported: 502 Authentication Failed")
    sab.account_errors.pop(NH)
    sab.test_results["nl.thundernews.test"] = (False, "481 Too many connections")  # alive, at its connection limit
    state.set_state("mode", "autopilot")
    _pass()
    _backdate(db)
    out = _pass()
    assert NH in out["servers"]["kept_on"] and "nl.thundernews.test" in out["servers"]["kept_on"]
    assert _enabled(sab)[NH] == 1 and _enabled(sab)["nl.thundernews.test"] == 1
    assert _enabled(sab)["reader.usenetbucket.test"] == 0  # its test confirms the refused login
    # the confirming test is reused for an hour: no new SABnzbd test in the next round
    n = len(sab.test_calls)
    _pass()
    assert len(sab.test_calls) == n


def test_failing_again_soon_after_recovery_goes_off_without_waiting(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin)
    db.execute("INSERT INTO usenet_server_autoheal(server,status,recovered_at) VALUES (%s,'recovered',now() - "
               "interval '1 day')", (NH,))
    sab.set_server_error(NH, f"Server {NH} reported: 502 Authentication Failed")
    state.set_state("mode", "autopilot")
    out = _pass()
    assert out["servers"]["switched_off"] == [NH] and _enabled(sab)[NH] == 0
    row = _row(db, NH)
    assert row["status"] == "disabled" and row["probes"] == 1 and row["recovered_at"] is not None
    ch = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s", (f"usenet.auto_off:{NH}",))
    assert "binnen een week" in ch["explanation"] and "servertest bevestigt" in ch["explanation"]


# ------------------------------------------------------------------ Autopilot: testing switched-off servers
def test_expired_account_calm_notice_weekly_test_and_renewal_noticed(
    app: Any, db: Any, admin: Any, serve: Any
) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    key = f"usenet.server_expired:{XS}"
    rec = _rec(db, key)
    assert rec["status"] == "open" and rec["risk"] == "toestemming_nodig" and rec["change_id"] is None
    assert "verlopen" in rec["title"] and "verwijderen" in rec["title"]
    first_update = rec["updated_at"]
    _pass()
    assert _rec(db, key)["updated_at"] == first_update  # calm: not refreshed every round
    ch = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s", (f"usenet.auto_off:{XS}",))
    assert "elke week" in ch["explanation"] and "niet blind" in ch["explanation"]
    # nothing happens before the weekly test …
    calls = len(sab.test_calls)
    _pass()
    assert len(sab.test_calls) == calls and _enabled(sab)[XS] == 0
    # … unless the owner changes the server in SABnzbd (here: the new expiry date after renewing) → test right away
    sab.fix_account(XS)
    _srv(sab, XS)["expire_date"] = (date.today() + timedelta(days=365)).isoformat()
    out = _pass()
    assert out["servers"]["switched_on"] == [XS] and _enabled(sab)[XS] == 1
    assert db.scalar("SELECT trigger FROM usenet_server_tests WHERE server=%s ORDER BY id DESC LIMIT 1",
                     (XS,)) == "config_changed"
    assert _rec(db, key)["status"] == "resolved"
    assert "na je wijziging in SABnzbd" in _row(db, XS)["last_result"]


def test_too_many_connections_is_not_a_failed_test(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    _backdate(db)
    _pass()
    assert _enabled(sab)[NH] == 0
    before = _row(db, NH)["tests_failed"]
    sab.test_results[NH] = (False, "481 Too many connections, slow down")
    _test_due(db, NH)
    _pass()
    row = _row(db, NH)
    assert row["tests_failed"] == before and row["status"] == "disabled" and _enabled(sab)[NH] == 0
    assert timedelta(minutes=55) < row["next_test_at"] - datetime.now(UTC) <= timedelta(hours=1)
    assert "te veel verbindingen" in row["last_result"]


def test_passing_test_outside_autopilot_is_a_proposal_only(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    assert _enabled(sab)[XS] == 0
    for mode in ("read_only", "manage"):
        state.set_state("mode", mode)
        sab.fix_account(XS)
        _test_due(db, XS)
        out = _pass()
        assert _enabled(sab)[XS] == 0, mode  # nothing applied
        ch = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status='preview'",
                         (f"usenet.auto_on:{XS}",))
        assert ch is not None and out["servers"]["pending"], mode
        assert _row(db, XS)["status"] == "disabled" and "wacht" in _row(db, XS)["last_result"]
    # a later failing test makes the proposal wrong → discarded
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.")
    _test_due(db, XS)
    _pass()
    assert db.scalar("SELECT count(*) FROM change_sets WHERE dedupe_key=%s AND status='preview'",
                     (f"usenet.auto_on:{XS}",)) == 0


def test_testen_switches_a_fixed_server_back_on_in_autopilot(
    app: Any, db: Any, admin: Any, operator: Any, serve: Any
) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    r = operator.post(f"/api/v1/usenet/sab/servers/{XS}/test")
    assert r.json()["test"]["result"] == "account_expired" and r.json()["change"] is None
    sab.fix_account(XS)
    r = operator.post(f"/api/v1/usenet/sab/servers/{XS}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["test"]["ok"] is True and body["change"]["auto_applied"] is True
    assert body["state"]["status"] == "probing" and body["state"]["probe_trigger"] == "server_test"
    assert _enabled(sab)[XS] == 1


def test_legacy_given_up_server_is_tested_again(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    _srv(sab, XS)["enable"] = 0
    db.execute("INSERT INTO usenet_server_autoheal(server,status,reason,probes,disabled_at) VALUES "
               "(%s,'given_up','account_expired',5,now() - interval '30 days')", (XS,))  # 1.5.0: never probed again
    state.set_state("mode", "autopilot")
    out = _pass()
    assert XS in out["servers"]["tested"] and _row(db, XS)["status"] == "disabled"  # tested; still expired
    sab.fix_account(XS)
    _test_due(db, XS)
    out = _pass()
    assert out["servers"]["switched_on"] == [XS] and _enabled(sab)[XS] == 1


def test_flapping_results_give_one_advice(app: Any, db: Any, admin: Any, operator: Any, serve: Any) -> None:
    from mso.features.usenet import servertest

    sab = _start(serve, admin)
    srv = "news.newshosting.test"
    assert operator.post(f"/api/v1/usenet/sab/servers/{srv}/test").json()["test"]["ok"] is True
    assert not servertest.flapping(srv)
    sab.test_results[srv] = (False, "502 Authentication Failed")
    operator.post(f"/api/v1/usenet/sab/servers/{srv}/test")
    assert servertest.flapping(srv)
    rec = _rec(db, f"usenet.server_flapping:{srv}")
    assert rec["status"] == "open" and rec["risk"] == "advies" and "verbindingen" in rec["title"]
    # broken → repaired is not flapping
    db.execute("DELETE FROM usenet_server_tests")
    sab.test_results[srv] = (False, "502 Authentication Failed")
    operator.post(f"/api/v1/usenet/sab/servers/{srv}/test")
    sab.test_results.pop(srv)
    operator.post(f"/api/v1/usenet/sab/servers/{srv}/test")
    assert not servertest.flapping(srv) and _rec(db, f"usenet.server_flapping:{srv}")["status"] == "resolved"


def test_provider_testen_without_credentials_uses_sabnzbd(
    app: Any, db: Any, admin: Any, operator: Any, serve: Any
) -> None:
    _start(serve, admin, server_errors="report")
    for slug, server in (("nh", NH), ("ew", EW)):
        r = admin.post("/api/v1/usenet/providers", json={"slug": slug, "label": slug.upper(), "host": "127.0.0.1",
                                                          "port": 1119, "ssl": False, "sab_server": server})
        assert r.status_code == 200, r.text
    res = operator.post("/api/v1/usenet/providers/nh/test").json()
    assert res["tested"] is True and res["source"] == "sabnzbd" and res["status"] == "auth_failed"
    assert res["provider"]["status"] == "missing_credentials"  # MSO's own test status is untouched
    assert res["provider"]["sab_test"]["result"] == "auth_failed"
    ok = operator.post("/api/v1/usenet/providers/ew/test").json()
    assert ok["status"] == "ok" and "SABnzbd kan inloggen" in ok["message"]
    assert "user1" not in str(ok) and SAB_KEY not in str(ok)


# ------------------------------------------------------------------ C11: expiry, block, Kosten
def _refresh() -> None:
    from mso.features.usenet import serverstatus

    serverstatus.refresh()


def test_expiry_reminders_14_7_1_days_deduplicated(app: Any, db: Any, admin: Any, serve: Any) -> None:
    sab = _start(serve, admin)
    eweka = _srv(sab, EW)
    today = date.today()

    def keys() -> dict[str, str]:
        return {r["dedupe_key"]: r["status"] for r in db.fetchall(
            "SELECT dedupe_key, status FROM recommendations WHERE dedupe_key LIKE 'usenet.server\\_expiry:%%'")}

    eweka["expire_date"] = (today + timedelta(days=20)).isoformat()
    _refresh()
    assert keys() == {}
    exp = today + timedelta(days=10)
    eweka["expire_date"] = exp.isoformat()
    _refresh()
    k14 = f"usenet.server_expiry:{EW}:{exp.isoformat()}:14"
    assert keys() == {k14: "open"}
    rec = _rec(db, k14)
    assert "over 10 dagen" in rec["title"] and rec["risk"] == "toestemming_nodig" and "Eweka" in rec["title"]
    _refresh()
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'usenet.server\\_expiry:%%'") == 1
    exp = today + timedelta(days=5)
    eweka["expire_date"] = exp.isoformat()
    _refresh()
    k7 = f"usenet.server_expiry:{EW}:{exp.isoformat()}:7"
    assert keys()[k7] == "open" and keys()[k14] == "resolved"
    db.execute("UPDATE recommendations SET status='dismissed' WHERE dedupe_key=%s", (k7,))
    _refresh()
    assert keys()[k7] == "dismissed"  # the owner dismissed this step: it stays dismissed
    exp = today + timedelta(days=1)
    eweka["expire_date"] = exp.isoformat()
    _refresh()
    k1 = f"usenet.server_expiry:{EW}:{exp.isoformat()}:1"
    assert keys()[k1] == "open" and "morgen" in _rec(db, k1)["title"]
    # SABnzbd clears the date on the day itself: MSO remembers it, the reminders resolve
    eweka["expire_date"] = ""
    _refresh()
    assert keys()[k1] == "resolved"
    assert db.scalar("SELECT expire_date FROM usenet_server_accounts WHERE server=%s", (EW,)) == exp
    # a switched-off server gets no reminder
    eweka["expire_date"] = (today + timedelta(days=3)).isoformat()
    eweka["enable"] = 0
    _refresh()
    assert "open" not in keys().values()


def test_renewal_date_prefilled_under_kosten(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso.features.costs import api as costs_api

    sab = _start(serve, admin)
    r = admin.post("/api/v1/usenet/providers", json={"slug": "eweka", "label": "Eweka", "host": "127.0.0.1",
                                                      "port": 1119, "ssl": False, "sab_server": EW})
    assert r.status_code == 200, r.text
    own = admin.post("/api/v1/usenet/providers", json={"slug": "nh", "label": "NH", "host": "127.0.0.1",
                                                        "port": 1119, "ssl": False, "sab_server": NH})
    assert own.status_code == 200
    for slug, renewal in (("eweka", None), ("nh", date.today() + timedelta(days=40))):
        db.execute("INSERT INTO services(source_id,kind,label,billing,amount_cents,renewal_date) VALUES "
                   "(%s,'usenet_provider',%s,'monthly',1299,%s)", (f"provider:{slug}", slug, renewal))
    exp = date.today() + timedelta(days=9)
    _srv(sab, EW)["expire_date"] = exp.isoformat()
    _srv(sab, NH)["expire_date"] = (date.today() + timedelta(days=12)).isoformat()
    _refresh()
    svc = {r["label"]: r["renewal_date"] for r in db.fetchall("SELECT label, renewal_date FROM services")}
    assert svc["eweka"] == exp  # empty → filled from SABnzbd
    assert svc["nh"] == date.today() + timedelta(days=40)  # the owner's own date is never overwritten
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='costs.renewal_prefilled'") == 1
    # Kosten's own 'Verlenging … over 9 dag(en)' for the same service and date is merged into the Usenet reminder
    costs_api.create_renewal_reminders()
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'costs.renewal:%%' AND "
                     "status='open'") == 0
    assert _rec(db, f"usenet.server_expiry:{EW}:{exp.isoformat()}:14")["status"] == "open"
    # renewed: SABnzbd's new date moves the date MSO filled along
    new = date.today() + timedelta(days=375)
    _srv(sab, EW)["expire_date"] = new.isoformat()
    _refresh()
    assert db.scalar("SELECT renewal_date FROM services WHERE label='eweka'") == new


def test_block_quota_remaining_forecast_and_reminder(app: Any, db: Any, admin: Any, viewer: Any, serve: Any) -> None:
    from mso.domain import outcomes

    sab = _start(serve, admin)
    gb = 1024**3
    _srv(sab, BLOCK).update(quota="100G", usage_at_start=200 * gb)
    today = date.today()
    for d in range(1, 15):
        db.execute("INSERT INTO usenet_server_stats(server,day,bytes) VALUES (%s,%s,%s)",
                   (BLOCK, today - timedelta(days=d), 2 * gb))
    outcomes.observe("sabnzbd", "sab.server_total_bytes", float(290 * gb), {"server": BLOCK})
    _refresh()
    info = viewer.get("/api/v1/usenet/sab/accounts").json()["items"][BLOCK]["block"]
    assert info["source"] == "sabnzbd" and info["size_bytes"] == 100 * gb and info["used_bytes"] == 90 * gb
    assert info["remaining_bytes"] == 10 * gb and info["avg_daily_bytes"] == 2 * gb and info["days_left"] == 5.0
    assert info["run_out_date"] == (today + timedelta(days=5)).isoformat() and info["low"] is True
    key = f"usenet.block_low:{BLOCK}:{100 * gb}:{200 * gb}"
    rec = _rec(db, key)
    assert rec["status"] == "open" and "nog 10,0 GB" in rec["title"] and "~5 dag" in rec["title"]
    # a new block in SABnzbd (new quota, SABnzbd resets usage_at_start) → no longer low → resolved
    _srv(sab, BLOCK).update(quota="500G", usage_at_start=290 * gb)
    _refresh()
    assert _rec(db, key)["status"] == "resolved"


def test_import_quota_server_as_block_and_baseline_on_block_size(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    from mso.domain import outcomes

    sab = _start(serve, admin)
    gb = 1024**3
    _srv(sab, BLOCK).update(quota="250G", usage_at_start=7 * gb)
    for name in (BLOCK, EW, NH):
        _srv(sab, name)["host"] = "127.0.0.1"  # the mock host names don't resolve → import would skip them
    r = admin.post("/api/v1/usenet/providers/import-sab")
    assert r.status_code == 200, r.text
    rows = {p["sab_server"]: p for p in db.fetchall("SELECT * FROM usenet_providers")}
    assert rows[BLOCK]["kind"] == "block" and rows[BLOCK]["block_size_gb"] == 250
    assert rows[BLOCK]["block_baseline_bytes"] == 7 * gb
    assert rows[EW]["kind"] == "unlimited"
    # block size entered by hand (no SABnzbd quota): usage counts from SABnzbd's total at that moment
    outcomes.observe("sabnzbd", "sab.server_total_bytes", float(40 * gb), {"server": NH})
    slug = rows[NH]["slug"]
    r = admin.put(f"/api/v1/usenet/providers/{slug}", json={"kind": "block", "block_size_gb": 100})
    assert r.status_code == 200, r.text
    assert db.scalar("SELECT block_baseline_bytes FROM usenet_providers WHERE slug=%s", (slug,)) == 40 * gb
    outcomes.observe("sabnzbd", "sab.server_total_bytes", float(55 * gb), {"server": NH})
    items = {p["slug"]: p for p in viewer.get("/api/v1/usenet/providers").json()["items"]}
    p = items[slug]
    assert p["block_used_bytes"] == 15 * gb and p["block_size_bytes"] == 100 * gb and p["block"]["source"] == "mso"
    assert p["block"]["remaining_bytes"] == 85 * gb


def test_mock_test_server_contract_matches_sabnzbd(app: Any) -> None:
    """The mock answers like SABnzbd 5.1 (shape ``{"value": {"result", "message"}}``; missing host/connections)."""
    from msomocks import sabnzbd as mock

    st = mock.SabState()
    assert st.test_server({"host": "", "connections": "1"}) == (False, "The hostname is not set.")
    assert st.test_server({"host": EW, "connections": "0"})[0] is False
    assert st.test_server({"host": EW, "connections": "1", "username": "user1", "password": "*****",
                           "server": EW}) == (True, "Connection Successful!")
    assert st.test_server({"host": EW, "connections": "1", "username": "fout", "password": "*****",
                           "server": EW}) == (False, "481 Authentication failed")
    assert time.time() > 0
