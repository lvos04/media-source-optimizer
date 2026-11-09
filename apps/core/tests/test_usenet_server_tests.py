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
    assert exc.value.code == "not_found"
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
    sab.server_errors[NH] = ""  # only in the warning list, as SABnzbd does after its retry pause
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
    # the passing test is newer than SABnzbd's warning: the warning is outdated, the server counts as working
    st = db.fetchone("SELECT status, source FROM usenet_sab_servers WHERE server=%s", (NH,))
    assert st["status"] == "ok" and st["source"] == "test"
    assert _rec(db, f"usenet.sab_server:{NH}")["status"] == "resolved"
    # a NEW warning after the test (e.g. a connection limit at peak time) counts again
    sab.warnings.append({"text": f"Server {NH} reported: 502 Authentication Failed", "type": "WARNING",
                         "time": int(time.time()) + 5})
    _refresh()
    assert db.scalar("SELECT status FROM usenet_sab_servers WHERE server=%s", (NH,)) == "auth_failed"


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
    eweka["expire_date"] = today.isoformat()
    _refresh()
    k0 = f"usenet.server_expiry:{EW}:{today.isoformat()}:1"
    assert keys()[k0] == "open" and "vandaag" in _rec(db, k0)["title"]
    # SABnzbd clears the date on the day itself (check_server_expiration): MSO remembers it, the reminders resolve
    eweka["expire_date"] = ""
    _refresh()
    assert keys()[k0] == "resolved" and keys()[k1] == "resolved"
    assert db.scalar("SELECT expire_date FROM usenet_server_accounts WHERE server=%s", (EW,)) == today
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


# ------------------------------------------------------------------ review fixes (1.6.0 WP-B)
def test_latency_hint_and_expiry_notice_are_not_server_errors(app: Any, db: Any, admin: Any, serve: Any) -> None:
    """SABnzbd ≥ 5.1 adds "Server h took N ms to respond … 'Articles per request'" after a passing test with fewer
    than 5 articles per request; MSO sends at least 5, and such hints (and "is expiring in N day(s)") never count."""
    from mso.features.usenet import serverstatus

    sab = _start(serve, admin)
    _srv(sab, EW)["pipelining_requests"] = 2
    with _client() as c:
        assert c.test_server(EW)["ok"] is True
    assert sab.test_calls[-1]["pipelining_requests"] == "5" and sab.warnings == []
    _srv(sab, EW)["pipelining_requests"] = 8
    with _client() as c:
        c.test_server(EW)
    assert sab.test_calls[-1]["pipelining_requests"] == "8"
    # the hint SABnzbd would add for a slower client (481 ms used to read as NNTP code 481 → "inloggen geweigerd")
    sab.test_server({"host": EW, "connections": "1", "username": "user1", "password": "*****", "server": EW})
    sab.warnings.append({"text": "Server Eweka is expiring in 3 day(s)", "type": "WARNING", "time": int(time.time())})
    assert len(sab.warnings) == 2
    _refresh()
    assert db.scalar("SELECT status FROM usenet_sab_servers WHERE server=%s", (EW,)) == "ok"
    assert db.scalar("SELECT count(*) FROM usenet_sab_server_events WHERE server=%s", (EW,)) == 0
    for text in ("Server news.x.test took 502 ms to respond. On high-latency connections, increasing 'Articles "
                 "per request' to a value between 5 and 10 can improve download speed.",
                 "Server X is expiring in 0 day(s)"):
        assert serverstatus.classify_message(text) is None, text
    # a provider's own wording with "is not valid" is a refused login, not "onbereikbaar"
    assert serverstatus.classify_test({"supported": True, "ok": False, "message": "481 Username is not valid"}) == (
        "auth_failed")


def test_unknown_server_is_404_and_broken_sabnzbd_answer_is_502(
    app: Any, admin: Any, operator: Any, serve: Any, monkeypatch: Any
) -> None:
    from mso.connectors.sabnzbd import SabClient, SabError

    _start(serve, admin)
    assert operator.post("/api/v1/usenet/sab/servers/bestaat.niet/test").status_code == 404

    def broken(self: Any, name: str) -> dict[str, Any]:
        raise SabError("SABnzbd gaf geen geldig antwoord", "invalid")  # e.g. a reverse-proxy login page

    monkeypatch.setattr(SabClient, "test_server", broken)
    r = operator.post(f"/api/v1/usenet/sab/servers/{EW}/test")
    assert r.status_code == 502 and "niet gevonden" not in r.text


def test_legacy_block_without_baseline_gives_no_false_reminder(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    from mso.domain import outcomes

    _start(serve, admin)
    gb = 1024**3
    r = admin.post("/api/v1/usenet/providers", json={"slug": "nh", "label": "NH", "host": "127.0.0.1", "port": 1119,
                                                      "ssl": False, "kind": "block", "block_size_gb": 100,
                                                      "sab_server": NH})
    assert r.status_code == 200, r.text
    # a block size from before 1.6.0: no baseline; SABnzbd's counter since its last reset is far above the block
    db.execute("UPDATE usenet_providers SET block_baseline_bytes=NULL, block_baseline_at=NULL WHERE slug='nh'")
    outcomes.observe("sabnzbd", "sab.server_total_bytes", float(950 * gb), {"server": NH})
    today = date.today()
    for d in range(1, 15):
        db.execute("INSERT INTO usenet_server_stats(server,day,bytes) VALUES (%s,%s,%s)",
                   (NH, today - timedelta(days=d), 5 * gb))
    _refresh()
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'usenet.block\\_low:%%'") == 0
    info = viewer.get("/api/v1/usenet/sab/accounts").json()["items"][NH]["block"]
    assert info["legacy"] is True and info["low"] is False and info["days_left"] is None
    assert info["used_bytes"] == 950 * gb  # still shown, with the "sinds de laatste reset" note
    # saving the block size (here: what is left of it) sets the baseline: from now on it counts
    r = admin.put("/api/v1/usenet/providers/nh", json={"block_size_gb": 100})
    assert r.status_code == 200, r.text
    assert db.scalar("SELECT block_baseline_bytes FROM usenet_providers WHERE slug='nh'") == 950 * gb
    p = {x["slug"]: x for x in viewer.get("/api/v1/usenet/providers").json()["items"]}["nh"]
    assert p["block"]["legacy"] is False and p["block"]["used_bytes"] == 0 and p["block"]["days_left"] == 20.0
    # a block provider without a linked SABnzbd server has no counter at all (old meter text)
    r = admin.post("/api/v1/usenet/providers", json={"slug": "los", "label": "Los", "host": "127.0.0.1", "port": 1119,
                                                      "ssl": False, "kind": "block", "block_size_gb": 50})
    assert r.status_code == 200, r.text
    p = {x["slug"]: x for x in viewer.get("/api/v1/usenet/providers").json()["items"]}["los"]
    assert p["block"] is None and p["block_used_bytes"] is None


def test_future_expiry_removed_by_owner_is_forgotten(app: Any, db: Any, admin: Any, serve: Any) -> None:
    sab = _start(serve, admin)
    future = date.today() + timedelta(days=200)
    _srv(sab, EW)["expire_date"] = future.isoformat()
    _refresh()
    assert db.scalar("SELECT expire_date FROM usenet_server_accounts WHERE server=%s", (EW,)) == future
    _srv(sab, EW)["expire_date"] = ""  # the owner moved to a plan without end date
    _refresh()
    assert db.scalar("SELECT expire_date FROM usenet_server_accounts WHERE server=%s", (EW,)) is None


def test_import_tiny_quota_imports_unlimited(app: Any, db: Any, admin: Any, serve: Any) -> None:
    sab = _start(serve, admin)
    _srv(sab, BLOCK).update(quota="500", usage_at_start=0)  # SABnzbd reads "500" as 500 bytes
    _srv(sab, BLOCK)["host"] = "127.0.0.1"
    r = admin.post("/api/v1/usenet/providers/import-sab")
    assert r.status_code == 200, r.text
    row = db.fetchone("SELECT kind, block_size_gb FROM usenet_providers WHERE sab_server=%s", (BLOCK,))
    assert row["kind"] == "unlimited" and row["block_size_gb"] is None


def test_config_change_still_tests_right_away_after_a_skipped_round(
    app: Any, db: Any, admin: Any, serve: Any, monkeypatch: Any
) -> None:
    from mso import state
    from mso.features.usenet import autoheal

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    assert _enabled(sab)[XS] == 0
    fp = _row(db, XS)["config_fp"]
    sab.fix_account(XS)
    _srv(sab, XS)["expire_date"] = (date.today() + timedelta(days=365)).isoformat()  # renewed in SABnzbd
    monkeypatch.setattr(autoheal, "TESTS_PER_TICK", 0)  # no test possible this round (budget/transient error)
    out = _pass()
    assert out["servers"]["tested"] == [] and _row(db, XS)["config_fp"] == fp  # trigger kept for the next round
    monkeypatch.setattr(autoheal, "TESTS_PER_TICK", 6)
    out = _pass()
    assert out["servers"]["switched_on"] == [XS] and _enabled(sab)[XS] == 1
    assert db.scalar("SELECT trigger FROM usenet_server_tests WHERE server=%s ORDER BY id DESC LIMIT 1",
                     (XS,)) == "config_changed"


def test_discarded_proposal_not_repeated_and_expired_notice_resolves(
    app: Any, db: Any, admin: Any, operator: Any, serve: Any
) -> None:
    from mso import changes, state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    notice = f"usenet.server_expired:{XS}"
    assert _enabled(sab)[XS] == 0 and _rec(db, notice)["status"] == "open"
    state.set_state("mode", "manage")
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()

    def previews() -> list[dict[str, Any]]:
        return db.fetchall("SELECT * FROM change_sets WHERE dedupe_key=%s AND status='preview'",
                           (f"usenet.auto_on:{XS}",))

    assert len(previews()) == 1
    # the renewal is recognised: 'account verlopen' resolves while the server waits for the proposal
    assert _rec(db, notice)["status"] == "resolved"
    _pass()
    assert _rec(db, notice)["status"] == "resolved"
    changes.discard(previews()[0]["id"], "admin")  # the owner does not want it back on
    _test_due(db, XS)
    out = _pass()
    assert previews() == [] and not out["servers"]["pending"] and XS in out["servers"]["tested"]
    assert "verwierp" in _row(db, XS)["last_result"]
    # a failing test breaks the series: the next passing test proposes again
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.")
    _test_due(db, XS)
    _pass()
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()
    assert len(previews()) == 1
    # 'Testen' is an explicit request: it proposes even right after a discard
    changes.discard(previews()[0]["id"], "admin")
    body = operator.post(f"/api/v1/usenet/sab/servers/{XS}/test").json()
    assert body["test"]["ok"] is True and body["change"] is not None and len(previews()) == 1


def test_probing_server_inconclusive_test_waits_before_switching_off(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin)
    db.execute("INSERT INTO usenet_server_autoheal(server,status,reason,probe_started_at) VALUES "
               "(%s,'probing','auth_failed',now() - interval '3 hours')", (NH,))
    sab.set_server_error(NH, f"Server {NH} reported: 502 Authentication Failed")
    sab.test_results[NH] = (False, "Timed out")  # inconclusive: says nothing about the account
    state.set_state("mode", "autopilot")
    out = _pass()
    assert NH in out["servers"]["kept_on"] and _enabled(sab)[NH] == 1
    row = _row(db, NH)
    assert row["status"] == "probing" and "over een uur opnieuw" in row["last_result"]
    assert timedelta(minutes=55) < row["next_test_at"] - datetime.now(UTC) <= timedelta(hours=1)
    # still inconclusive after the usual 6 h of errors → off, as for any other failing server
    db.execute("UPDATE usenet_server_autoheal SET probe_started_at = now() - interval '8 hours', "
               "next_test_at = now() - interval '1 minute' WHERE server=%s", (NH,))
    db.execute("UPDATE usenet_sab_server_events SET first_at = now() - interval '7 hours' WHERE server=%s", (NH,))
    out = _pass()
    assert out["servers"]["switched_off"] == [NH] and _enabled(sab)[NH] == 0


def test_flapping_advice_resolves_without_a_new_test(app: Any, db: Any, admin: Any, operator: Any, serve: Any) -> None:
    sab = _start(serve, admin)
    operator.post(f"/api/v1/usenet/sab/servers/{NH}/test")
    sab.test_results[NH] = (False, "502 Authentication Failed")
    operator.post(f"/api/v1/usenet/sab/servers/{NH}/test")
    key = f"usenet.server_flapping:{NH}"
    assert _rec(db, key)["status"] == "open"
    _pass()
    assert _rec(db, key)["status"] == "open"  # the pattern is still in the 7-day window
    db.execute("UPDATE usenet_server_tests SET at = at - interval '8 days' WHERE server=%s", (NH,))
    sab.test_results.pop(NH)
    _pass()  # NH is enabled and healthy: no new test, the advice resolves anyway
    assert _rec(db, key)["status"] == "resolved"


# ------------------------------------------------------------------ integration review (1.6.0): the owner's choice
def _use(sab: Any, server: str, nbytes: int = 50_000_000) -> None:
    day = date.today().isoformat()
    st = sab.stats.setdefault(server, {"daily": {}, "articles_tried": {}, "articles_failed": {}})
    st["daily"][day] = st["daily"].get(day, 0) + nbytes
    st["articles_tried"][day] = st["articles_tried"].get(day, 0) + 70


def test_person_rollback_of_a_switch_off_is_respected_and_waits_quietly(
    app: Any, db: Any, admin: Any, operator: Any, serve: Any
) -> None:
    from mso import state
    from mso.features.automation import autopilot

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    off = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status='applied'", (f"usenet.auto_off:{XS}",))
    r = operator.post(f"/api/v1/changes/{off['id']}/rollback", json={"reason": "ik wil hem aan"})
    assert r.status_code == 200, r.text
    assert _enabled(sab)[XS] == 1
    ov = db.fetchone("SELECT * FROM change_overrides WHERE target='sabnzbd' AND path=%s", (f"servers/{XS}/enable",))
    assert ov is not None and ov["change_id"] == off["id"]
    _pass()  # watch starts
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.", at=int(time.time()) + 60)
    calls = len(sab.test_calls)
    for _ in range(6):  # six 5-minute rounds
        out = _pass()
    assert _enabled(sab)[XS] == 1  # the person's decision stands
    pending = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status='preview'",
                          (f"usenet.auto_off:{XS}",))
    assert pending is not None and autopilot.why_not(dict(pending), "usenet") == autopilot.OVERRIDE_REASON
    # one audit line and at most one confirming test, not one per round
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='usenet.server_probe_failed'") == 1
    assert len(sab.test_calls) - calls <= 1
    row = _row(db, XS)
    assert row["status"] == "probing" and row["last_result"].startswith("uitschakelen wacht")
    assert row["next_test_at"] > datetime.now(UTC) + timedelta(days=20)  # until the override ends
    assert out["servers"]["probe_failed"] == []


def test_owner_switch_off_during_the_watch_is_respected(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state
    from mso.features.overview import goals

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()
    assert _enabled(sab)[XS] == 1 and _row(db, XS)["status"] == "probing"
    _srv(sab, XS)["enable"] = 0  # the owner does not want this server: off in SABnzbd
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.", warn=False)
    _pass()
    assert _row(db, XS)["status"] == "disabled"
    assert db.fetchone("SELECT * FROM change_overrides WHERE path=%s", (f"servers/{XS}/enable",))
    before = [d["server"] for d in goals.servers_now(db)["disabled_by_mso"]]
    assert XS not in before
    _test_due(db, XS)
    _pass()  # a failing test rewrites last_result: still the owner's choice on the goals card
    assert XS not in [d["server"] for d in goals.servers_now(db)["disabled_by_mso"]]
    sab.fix_account(XS)
    _test_due(db, XS)
    out = _pass()
    assert _enabled(sab)[XS] == 0 and out["servers"]["switched_on"] == []  # a passing test: only a proposal
    assert db.scalar("SELECT count(*) FROM change_sets WHERE dedupe_key=%s AND status='preview'",
                     (f"usenet.auto_on:{XS}",)) == 1


def test_person_rollback_of_a_switch_on_is_respected(app: Any, db: Any, admin: Any, operator: Any,
                                                     serve: Any) -> None:
    from mso import state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()
    on = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status='applied'", (f"usenet.auto_on:{XS}",))
    assert operator.post(f"/api/v1/changes/{on['id']}/rollback", json={}).status_code == 200
    assert _enabled(sab)[XS] == 0
    _pass()
    _test_due(db, XS)
    _pass()
    assert _enabled(sab)[XS] == 0


def test_mso_proposal_applied_by_a_person_is_not_the_owners_override(
    app: Any, db: Any, admin: Any, serve: Any
) -> None:
    from mso import changes, state

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    state.set_state("mode", "manage")
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()
    ch = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status='preview'", (f"usenet.auto_on:{XS}",))
    changes.apply(ch["id"], "beheerder")  # the person applies MSO's own 'weer inschakelen' proposal
    _pass()
    row = _row(db, XS)
    assert row["status"] == "probing" and row["probe_change_id"] == ch["id"]
    assert not db.fetchone("SELECT * FROM change_overrides WHERE path=%s", (f"servers/{XS}/enable",))


def test_noodrem_keeps_the_watch_and_recovered_at(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso import state
    from mso.features.automation import autopilot
    from mso.features.usenet import coverage

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()
    db.execute("UPDATE usenet_sab_server_events SET last_at = now() - interval '1 day' WHERE server=%s", (XS,))
    db.execute("UPDATE usenet_server_autoheal SET probe_started_at = now() - interval '3 hours' WHERE server=%s", (XS,))
    _use(sab, XS)
    _pass()
    assert _row(db, XS)["status"] == "recovered"
    gain = coverage.last_gain_at()
    db.execute("UPDATE usenet_server_autoheal SET status='probing', probe_started_at=now() WHERE server=%s", (XS,))
    db.execute("UPDATE usenet_server_tests SET at = at - interval '2 hours'")
    autopilot.engage_hold("test", {"rollbacks": 0, "failures": 5})  # noodrem for an unrelated reason
    sab.set_server_error(XS, f"Server {XS} reported: 482 Account expired.", at=int(time.time()) + 5)
    _pass()
    row = _row(db, XS)
    assert row is not None and row["status"] == "probing" and coverage.last_gain_at() == gain
    assert _enabled(sab)[XS] == 1


def test_job_that_failed_while_the_reenabled_server_was_on_is_not_retried(
    app: Any, db: Any, admin: Any, serve: Any
) -> None:
    from msomocks import sabnzbd as mock

    from mso import state
    from mso.features.usenet import coverage, history

    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    _pass()
    _backdate(db)
    _pass()
    sab.fix_account(XS)
    _test_due(db, XS)
    _pass()
    assert _enabled(sab)[XS] == 1 and _row(db, XS)["status"] == "probing"
    time.sleep(1.1)  # SABnzbd's timestamps are whole seconds
    slot = mock._slot(7, "Show.Name.S01E07.1080p.WEB.H264-GRP7", "tv", "repair", int(time.time()))
    sab.add_history(slot)  # fails while XS is on (SABnzbd already tried XS too)
    _use(sab, XS)
    history.ingest(force=True)
    db.execute("UPDATE usenet_sab_server_events SET last_at = now() - interval '1 day' WHERE server=%s", (XS,))
    db.execute("UPDATE usenet_server_autoheal SET probe_started_at = probe_started_at - interval '3 hours' "
               "WHERE server=%s", (XS,))
    out = _pass()
    assert out["servers"]["recovered"] == [XS]
    gain = coverage.last_gain()
    assert gain["kind"] == "recovered" and gain["since"] < gain["at"]
    assert slot["nzo_id"] not in sab.retried


def test_config_locked_sabnzbd_causes_no_failed_automatic_applies(app: Any, db: Any, admin: Any, serve: Any) -> None:
    """SABnzbd's config_lock refuses the server test AND set_config: MSO tries no switch-off (which would only fail
    and trip the global noodrem), raises one calm item and looks again daily."""
    from mso import state

    sab = _start(serve, admin, server_errors="field", config_locked=1)
    state.set_state("mode", "autopilot")
    _pass()
    _backdate(db)
    for _ in range(3):
        out = _pass()
    db.execute("UPDATE automation_failures SET next_attempt_at = now() - interval '1 minute'")
    out = _pass()
    assert out["servers"]["switched_off"] == [] and set(out["servers"]["locked"]) >= {XS}
    assert db.scalar("SELECT count(*) FROM automation_log WHERE kind='apply' AND result='failed'") == 0
    assert state.hold() is None
    assert all(s["enable"] == 1 for s in sab.servers)
    assert {r["result"] for r in db.fetchall("SELECT result FROM usenet_server_tests")} == {"locked"}
    rec = _rec(db, "usenet.sab_locked")
    assert rec["status"] == "open" and rec["risk"] == "toestemming_nodig"
    n = db.scalar("SELECT count(*) FROM usenet_server_tests")
    _pass()
    assert db.scalar("SELECT count(*) FROM usenet_server_tests") == n  # no new test before the daily re-check
