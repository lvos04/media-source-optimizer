"""1.6.0 WP-G1 (C35) — Voetafdruk: what MSO changed in your systems right now, the owner's own choice is respected
(override, no tug-of-war) and "Alles terugzetten" restores the originals through the normal change flow.

Over real HTTP: SABnzbd and qBittorrent mocks (apps/mocks/msomocks)."""

from __future__ import annotations

from typing import Any

import pytest

from mso import changes, jobs, state

SAB_KEY = "SABGEHEIM-0123456789"
NH = "news.newshosting.test"


def _sab(serve: Any, admin: Any) -> Any:
    from msomocks import sabnzbd as mock

    app = mock.create_app(api_key=SAB_KEY)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200
    return app.state.sab


def _qb(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks.qbittorrent import create_app

    app = create_app(**faults)
    r = admin.put("/api/v1/connectors/qbittorrent",
                  json={"url": serve(app), "secrets": {"username": "admin", "password": "adminadmin"}})
    assert r.status_code == 200
    return app.state.qb


def _srv(sab: Any, name: str) -> dict[str, Any]:
    return next(s for s in sab.servers if s["name"] == name)


def _apply(target: str, title: str, ops: list[dict[str, Any]], actor: str = "automatisering") -> dict[str, Any]:
    """An MSO change applied by MSO itself (as Autopilot does): create + apply."""
    ch = changes.create(target, title, ops, risk="automatisch_veilig", explanation="Test.", actor=actor)
    return changes.apply(ch["id"], actor)


def _audits(db: Any, action: str) -> list[dict[str, Any]]:
    return db.fetchall("SELECT * FROM audit_log WHERE action=%s ORDER BY id", (action,))


def test_footprint_job_registered_read_only_every_mode(viewer: Any) -> None:
    from mso.features.automation import footprint

    sched = next(s for s in jobs.SCHEDULES if s.kind == footprint.JOB)
    assert sched.every_s == 3600 and sched.enabled() is True  # read-only towards the systems: also in Alleen-lezen
    items = {i["kind"]: i for i in viewer.get("/api/v1/automation/schedules").json()["items"]}
    assert items[footprint.JOB]["label"].startswith("Voetafdruk controleren")
    assert items[footprint.JOB]["enabled"] is True
    body = viewer.get("/api/v1/automation/footprint").json()
    assert body["systems"] == [] and body["override_days"] == 30 and body["restore_allowed"] is False


def test_sab_override_recorded_once_calm_info_item_then_cleared(
    app: Any, db: Any, admin: Any, viewer: Any, operator: Any, serve: Any
) -> None:
    from mso.features.automation import footprint, overrides

    sab = _sab(serve, admin)
    state.set_state("mode", "autopilot")
    ch = _apply("sabnzbd", "Server Newshosting automatisch uitgeschakeld (inloggen geweigerd)",
                [{"path": f"servers/{NH}/enable", "label": "Newshosting: server uitschakelen", "before": 1,
                  "after": 0, "destructive": True}])
    assert ch["status"] == "applied" and _srv(sab, NH)["enable"] == 0
    # read-only mode: the job still reads (nothing is written to SABnzbd)
    state.set_state("mode", "read_only")
    out = footprint.refresh()
    assert out["checked"] == 1 and out["actief"] == 1
    body = viewer.get("/api/v1/automation/footprint").json()
    [sys_] = body["systems"]
    [e] = sys_["entries"]
    assert sys_["label"] == "SABnzbd" and e["label"] == "Newshosting · aan/uit"
    assert (e["original_text"], e["mso_text"], e["current_text"]) == ("aan", "uit", "uit")
    assert e["status"] == "actief" and e["status_label"] == "Nog actief" and e["change_id"] == ch["id"]
    assert e["automatic"] is True and e["override"] is None

    # the owner switches the server on again in SABnzbd itself → MSO respects it (override, audit, info item)
    _srv(sab, NH)["enable"] = 1
    out = footprint.refresh()
    assert out["gewijzigd"] == 1 and out["overridden"] == ["Newshosting · aan/uit"]
    ov = overrides.active("sabnzbd", f"servers/{NH}/enable")
    assert ov is not None and ov["change_id"] == ch["id"] and ov["mso_value"] == 0 and ov["current_value"] == 1
    assert (ov["until"] - ov["detected_at"]).days in (29, 30)
    [a] = _audits(db, "footprint.overridden")
    assert "Newshosting · aan/uit" in a["summary"] and "nu aan" in a["summary"] and "30 dagen" in a["summary"]
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s",
                      (f"footprint.override:sabnzbd:servers/{NH}/enable",))
    assert rec["risk"] == "advies" and rec["status"] == "open" and rec["area"] == "usenet"
    assert "respecteert" in rec["explanation"]
    e = viewer.get("/api/v1/automation/footprint").json()["systems"][0]["entries"][0]
    assert e["status"] == "gewijzigd" and e["current_text"] == "aan" and e["override"]["until"]
    # a calm info item, never a decision
    w = viewer.get("/api/v1/overview/waiting").json()
    assert w["decisions"] == 0 and w["count"] == 0 and w["info_count"] == 1
    assert w["info"][0]["link"] == "/automatisering/voetafdruk"
    # an observation, not an action by MSO: not counted in "Wat MSO deed"
    cats = {c["key"] for c in viewer.get("/api/v1/overview/autopilot").json()["categories"]}
    assert "other" not in cats
    # the next hourly run: nothing new → no second audit/override refresh
    footprint.refresh()
    assert len(_audits(db, "footprint.overridden")) == 1

    # the owner switches it off again (MSO's value) → override cleared, info item resolved
    _srv(sab, NH)["enable"] = 0
    out = footprint.refresh()
    assert out["actief"] == 1 and overrides.active("sabnzbd", f"servers/{NH}/enable") is None
    assert len(_audits(db, "footprint.override_cleared")) == 1
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "resolved"

    # a person rolls MSO's change back in MSO → no footprint any more
    state.set_state("mode", "manage")
    assert operator.post(f"/api/v1/changes/{ch['id']}/rollback").status_code == 200
    assert footprint.refresh()["checked"] == 0
    assert viewer.get("/api/v1/automation/footprint").json()["systems"] == []
    assert db.scalar("SELECT count(*) FROM change_footprint") == 0


def test_sab_unreachable_keeps_last_value_and_removed_server_is_gone(
    app: Any, db: Any, admin: Any, viewer: Any, serve: Any
) -> None:
    from mso.features.automation import footprint

    sab = _sab(serve, admin)
    state.set_state("mode", "manage")
    _apply("sabnzbd", "Prioriteit", [{"path": "servers/news.eweka.test/priority", "label": "Eweka: prioriteit",
                                      "before": 0, "after": 2}])
    _apply("sabnzbd", "Uit", [{"path": f"servers/{NH}/enable", "label": "Newshosting: uit", "before": 1,
                               "after": 0, "destructive": True}])
    assert footprint.refresh()["actief"] == 2
    sab.servers = [s for s in sab.servers if s["name"] != NH]  # the owner deleted the server in SABnzbd
    out = footprint.refresh()
    assert out["verdwenen"] == 1 and out["actief"] == 1 and out["gewijzigd"] == 0
    # SABnzbd down: last known value kept, no override
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": "http://127.0.0.1:9"})
    assert r.status_code == 200
    out = footprint.refresh()
    assert out["onbereikbaar"] == 2 and out["overridden"] == []
    rows = {r["path"]: r for r in viewer.get("/api/v1/automation/footprint").json()["systems"][0]["entries"]}
    eweka = rows["servers/news.eweka.test/priority"]
    assert eweka["status"] == "onbereikbaar" and eweka["current_text"] == "2" and eweka["detail"]
    assert SAB_KEY not in str(rows)
    assert db.scalar("SELECT count(*) FROM change_overrides") == 0


def test_qbittorrent_normalisation_rates_and_exclude_list(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso.features.automation import footprint

    qb = _qb(serve, admin, version="5.3.0", webapi="2.16.2", excluded_file_names="*.txt")
    state.set_state("mode", "manage")
    _apply("qbittorrent", "Uploadlimiet", [{"path": "pref:up_limit", "label": "Upload", "before": 0,
                                            "after": 200_000_000}])
    assert qb.prefs["up_limit"] == 199_999_488  # qBittorrent keeps whole KiB/s
    _apply("qbittorrent", "Preventie", [
        {"path": "pref:excluded_file_names", "label": "Uitgesloten: + *.exe, *.scr", "before": "*.txt",
         "after": "*.txt\n*.exe\n*.scr"},
        {"path": "pref:excluded_file_names_enabled", "label": "Aanzetten", "before": False, "after": True},
    ])
    out = footprint.refresh()
    assert out["actief"] == 3 and out["gewijzigd"] == 0  # 199999488 is MSO's 200000000, rounded by qBittorrent
    # the owner adds an own pattern: MSO's patterns are still there → still active
    qb.prefs["excluded_file_names"] = "*.txt\n*.exe\n*.scr\n*.lnk"
    assert footprint.refresh()["gewijzigd"] == 0
    # the owner removes one of MSO's patterns → changed outside MSO
    qb.prefs["excluded_file_names"] = "*.txt\n*.scr\n*.lnk"
    out = footprint.refresh()
    assert out["gewijzigd"] == 1 and out["overridden"] == ["Uitgesloten bestandsnamen"]
    view = footprint.view()
    texts = {e["path"]: e for e in view["systems"][0]["entries"]}
    assert texts["pref:up_limit"]["mso_text"] == "190,7 MiB/s"
    assert texts["pref:up_limit"]["original_text"] == "geen limiet"
    assert texts["pref:excluded_file_names"]["current_text"].startswith("3 patronen")


def test_restore_all_preview_roles_policy_and_flow(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    from mso.features.automation import engine, footprint, overrides

    sab = _sab(serve, admin)
    qb = _qb(serve, admin, version="5.3.0", webapi="2.16.2", excluded_file_names="*.txt")
    qb.prefs["max_uploads"] = -1  # an extreme value MSO brought within its limits
    state.set_state("mode", "autopilot")
    off = _apply("sabnzbd", "Server Newshosting uit", [{"path": f"servers/{NH}/enable", "label": "Newshosting: uit",
                                                        "before": 1, "after": 0, "destructive": True}])
    prio = _apply("sabnzbd", "Prioriteit", [{"path": "servers/news.eweka.test/priority", "label": "Eweka: prioriteit",
                                             "before": 0, "after": 1}])
    prio2 = _apply("sabnzbd", "Prioriteit 2", [{"path": "servers/news.eweka.test/priority",
                                                "label": "Eweka: prioriteit", "before": 1, "after": 3}])
    guard = _apply("qbittorrent", "Uploadslots begrenzen", [{"path": "pref:max_uploads", "label": "Uploadslots",
                                                             "before": -1, "after": 100}])
    prev = _apply("qbittorrent", "Preventie", [
        {"path": "pref:excluded_file_names", "label": "+ *.exe", "before": "*.txt", "after": "*.txt\n*.exe"}])
    qb.prefs["dht"] = True
    dht = _apply("qbittorrent", "DHT uit", [{"path": "pref:dht", "label": "DHT", "before": True, "after": False}])
    qb.prefs["dht"] = True  # ... which the owner switched on again in qBittorrent
    qb.prefs["excluded_file_names"] = "*.txt\n*.exe\n*.lnk"  # the owner added an own pattern since

    plan = viewer.get("/api/v1/automation/footprint/restore").json()
    restore = {(r["target"], r["path"]): r for r in plan["restore"]}
    skipped = {(s["target"], s["path"]): s for s in plan["skipped"]}
    assert set(restore) == {("sabnzbd", f"servers/{NH}/enable"), ("sabnzbd", "servers/news.eweka.test/priority"),
                            ("qbittorrent", "pref:excluded_file_names")}
    # a chain of two MSO changes goes back to the value before the first one
    assert restore[("sabnzbd", "servers/news.eweka.test/priority")]["after"] == 0
    assert restore[("sabnzbd", "servers/news.eweka.test/priority")]["change_ids"] == [prio["id"], prio2["id"]]
    # only MSO's own pattern is taken out; the owner's later addition stays
    assert restore[("qbittorrent", "pref:excluded_file_names")]["after"] == "*.txt\n*.lnk"
    assert skipped[("qbittorrent", "pref:dht")]["reason_code"] == "gewijzigd"
    assert "MSO laat jouw waarde staan" in skipped[("qbittorrent", "pref:dht")]["reason"]
    assert skipped[("qbittorrent", "pref:max_uploads")]["reason_code"] == "beleid"
    assert plan["allowed"] is True and len(plan["fingerprint"]) == 16

    # roles, mode and the exact list
    fp = plan["fingerprint"]
    assert viewer.post("/api/v1/automation/footprint/restore", json={"fingerprint": fp}).status_code == 403
    state.set_state("mode", "read_only")
    r = operator.post("/api/v1/automation/footprint/restore", json={"fingerprint": fp})
    assert r.status_code == 409 and "alleen-lezen" in r.json()["detail"]
    state.set_state("mode", "autopilot")
    r = operator.post("/api/v1/automation/footprint/restore", json={"fingerprint": "0" * 16})
    assert r.status_code == 409 and "veranderd" in r.json()["detail"]
    assert _srv(sab, NH)["enable"] == 0  # nothing written

    body = operator.post("/api/v1/automation/footprint/restore", json={"fingerprint": fp}).json()
    res = {x["target"]: x for x in body["results"]}
    assert res["sabnzbd"]["status"] == "teruggezet" and res["sabnzbd"]["count"] == 2
    assert res["qbittorrent"]["status"] == "teruggezet" and res["qbittorrent"]["count"] == 1
    # verified at the source
    assert _srv(sab, NH)["enable"] == 1 and _srv(sab, "news.eweka.test")["priority"] == 0
    assert qb.prefs["excluded_file_names"] == "*.txt\n*.lnk" and qb.prefs["dht"] is True
    assert qb.prefs["max_uploads"] == 100  # outside policy: left alone, with the reason shown
    # MSO's changes count as rolled back; the overridden and the policy one stay applied
    status = {r["id"]: r["status"] for r in db.fetchall("SELECT id, status FROM change_sets")}
    assert status[off["id"]] == status[prio["id"]] == status[prio2["id"]] == status[prev["id"]] == "rolled_back"
    assert status[guard["id"]] == "applied" and status[dht["id"]] == "applied"
    # the restore change itself: the normal flow, applied by the person, never measured nor auto-rolled back
    rest = changes.get(res["sabnzbd"]["change_id"])
    assert rest["status"] == "applied" and rest["applied_by"] == "operator" and rest["source"] == "footprint:restore"
    assert rest["impact"]["evaluation"]["result"] == "niet_van_toepassing"
    assert all(o["result"] == "toegepast" for o in rest["ops"])
    assert engine.evaluate_due()["rolled_back"] == 0
    # Autopilot will not put them back: overrides for the restored paths
    for target, path in restore:
        assert overrides.active(target, path) is not None
    assert len(_audits(db, "footprint.restored")) == 2
    own = {o["path"]: o for o in body["footprint"]["overrides"]}
    assert own[f"servers/{NH}/enable"]["label"] == f"{NH} · aan/uit"
    assert own[f"servers/{NH}/enable"]["mso_text"] == "uit" and own[f"servers/{NH}/enable"]["current_text"] == "aan"
    # what remains of the footprint: the owner's own DHT choice and the policy limit
    left = {(e["target"], e["path"]) for s in body["footprint"]["systems"] for e in s["entries"]}
    assert left == {("qbittorrent", "pref:dht"), ("qbittorrent", "pref:max_uploads")}
    footprint.refresh()
    assert {(r["target"], r["path"]) for r in db.fetchall("SELECT target, path FROM change_footprint")} == left
    r = operator.post("/api/v1/automation/footprint/restore",
                      json={"fingerprint": viewer.get("/api/v1/automation/footprint/restore").json()["fingerprint"]})
    assert r.status_code == 409 and "niets" in r.json()["detail"]


@pytest.fixture()
def fake_reg(app: Any) -> dict[str, Any]:
    store: dict[str, Any] = {"indexer:newznab": None}
    changes.register_applier("fake_reg", lambda: changes.Applier(read=store.get, write=store.__setitem__))
    yield store
    changes.APPLIERS.pop("fake_reg", None)


def test_restore_that_removes_needs_admin_approval(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, fake_reg: dict[str, Any]
) -> None:
    from mso.features.automation import footprint

    state.set_state("mode", "manage")
    reg = _apply("fake_reg", "MSO-indexer registreren", [{"path": "indexer:newznab", "label": "Registratie",
                                                         "before": None, "after": "Naam: MSO"}], actor="beheerder")
    footprint.refresh()
    plan = viewer.get("/api/v1/automation/footprint/restore").json()
    [item] = plan["restore"]
    assert item["removes"] is True and item["needs_approval"] is True and item["after"] is None
    body = operator.post("/api/v1/automation/footprint/restore", json={"fingerprint": plan["fingerprint"]}).json()
    [res] = body["results"]
    assert res["status"] == "wacht_op_goedkeuring" and fake_reg["indexer:newznab"] == "Naam: MSO"
    waiting = changes.get(res["change_id"])
    assert waiting["status"] == "preview" and waiting["risk"] == "toestemming_nodig"
    assert waiting["ops"][0]["destructive"] is True
    w = viewer.get("/api/v1/overview/waiting").json()
    assert [i["id"] for i in w["items"]] == [waiting["id"]] and w["decisions"] == 1
    # an admin confirming the same list approves and applies it (one explicit approval)
    body = admin.post("/api/v1/automation/footprint/restore", json={"fingerprint": plan["fingerprint"]}).json()
    [res2] = body["results"]
    assert res2["status"] == "teruggezet" and res2["change_id"] == waiting["id"]
    assert fake_reg["indexer:newznab"] is None
    assert changes.get(reg["id"])["status"] == "rolled_back"
    assert changes.get(waiting["id"])["approved_by"] == "beheerder"


def test_irreversible_summary_and_one_way_ops_are_not_footprint(app: Any, db: Any, viewer: Any) -> None:
    from mso.db import jsonb
    from mso.features.automation import footprint

    h = "a" * 40
    tok = "0123456789abcdef"
    for target, ops in (
        ("qbittorrent", [{"path": f"torrent:{h}:delete_with_files:{tok}", "after": "verwijderd",
                          "irreversible": True, "result": "toegepast"},
                         {"path": f"arrblock:{tok}:{h}", "after": "geblokkeerd", "irreversible": True,
                          "result": "toegepast"},
                         {"path": f"torrent:{h}:recheck", "after": "uitgevoerd", "one_way": True,
                          "result": "toegepast"}]),
        ("sabnzbd_retry", [{"path": "retry/SABnzbd_nzo_1", "after": "opnieuw in wachtrij", "one_way": True,
                            "result": "toegepast"}]),
    ):
        db.execute("INSERT INTO change_sets(target,title,risk,ops,status,applied_at) VALUES "
                   "(%s,'x','toestemming_nodig',%s,'applied',now())", (target, jsonb(ops)))
    assert footprint.collect() == {}
    items = {i["key"]: i["count"] for i in viewer.get("/api/v1/automation/footprint").json()["irreversible"]}
    assert items == {"torrents_deleted": 1, "arr_blocked": 1, "retried": 1}


# ------------------------------------------------------------------ review fixes (1.6.0 G1)
class _Box:
    """An in-memory system with a switch to make it unreachable (a raw ConnectionError, as a socket gives)."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.down = False

    def read(self, path: str) -> Any:
        if self.down:
            raise ConnectionError("verbinding geweigerd")
        return self.values.get(path)


@pytest.fixture()
def box(app: Any) -> _Box:
    b = _Box()
    changes.register_applier("fake_fp", lambda: changes.Applier(read=b.read, write=b.values.__setitem__))
    yield b
    changes.APPLIERS.pop("fake_fp", None)


def test_restore_raw_read_error_discards_the_set_never_applied_automatically(
    app: Any, db: Any, operator: Any, viewer: Any, box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mso.features.automation import engine, footprint

    box.values["conns"] = 10
    state.set_state("mode", "autopilot")
    ch = _apply("fake_fp", "Verbindingen 20", [{"path": "conns", "label": "Verbindingen", "before": 10, "after": 20}])
    assert box.values["conns"] == 20
    fp = viewer.get("/api/v1/automation/footprint/restore").json()["fingerprint"]
    orig = footprint.restore_plan

    def plan_then_down(db: Any = None) -> dict[str, Any]:
        out = orig(db)
        box.down = True  # the system goes away between the list and the write (a raw error from applier.read)
        return out

    monkeypatch.setattr(footprint, "restore_plan", plan_then_down)
    r = operator.post("/api/v1/automation/footprint/restore", json={"fingerprint": fp})
    assert r.status_code == 200
    [res] = r.json()["results"]
    assert res["status"] == "mislukt" and "verbinding geweigerd" in res["message"]
    assert changes.get(res["change_id"])["status"] == "discarded"  # not left open for the automatic round
    monkeypatch.setattr(footprint, "restore_plan", orig)
    box.down = False
    out = engine.apply_safe()
    assert out["applied"] == [] and box.values["conns"] == 20
    assert db.scalar("SELECT count(*) FROM change_sets WHERE source='footprint:restore' AND status='applied'") == 0
    # MSO's change is untouched and still MSO's: no false 'changed outside MSO'
    assert changes.get(ch["id"])["status"] == "applied"
    assert footprint.refresh()["actief"] == 1 and _audits(db, "footprint.overridden") == []

    # a successful restore is approved by the person right away: never an open 'automatisch veilig' preview
    fp = viewer.get("/api/v1/automation/footprint/restore").json()["fingerprint"]
    [res] = operator.post("/api/v1/automation/footprint/restore", json={"fingerprint": fp}).json()["results"]
    rest = changes.get(res["change_id"])
    assert res["status"] == "teruggezet" and box.values["conns"] == 10
    assert rest["approved_by"] == "operator" and rest["applied_by"] == "operator"


def test_unreachable_run_between_is_no_new_override_event(app: Any, db: Any, box: _Box) -> None:
    from mso.features.automation import footprint, overrides

    box.values["conns"] = 10
    state.set_state("mode", "manage")
    _apply("fake_fp", "Verbindingen 20", [{"path": "conns", "label": "Verbindingen", "before": 10, "after": 20}])
    box.values["conns"] = 12  # the owner's own value
    assert footprint.refresh()["overridden"] == ["Verbindingen"]
    until = overrides.active("fake_fp", "conns")["until"]
    key = "footprint.override:fake_fp:conns"
    db.execute("UPDATE recommendations SET status='dismissed' WHERE dedupe_key=%s", (key,))  # the owner read it
    box.down = True
    out = footprint.refresh()
    assert out["onbereikbaar"] == 1
    box.down = False
    out = footprint.refresh()
    assert out["gewijzigd"] == 1 and out["overridden"] == []
    assert len(_audits(db, "footprint.overridden")) == 1
    assert overrides.active("fake_fp", "conns")["until"] == until  # the 30 days are not extended
    assert db.scalar("SELECT status FROM recommendations WHERE dedupe_key=%s", (key,)) == "dismissed"
    # another value of the owner is a new event
    box.values["conns"] = 14
    assert footprint.refresh()["overridden"] == ["Verbindingen"]
    assert len(_audits(db, "footprint.overridden")) == 2


def test_automatic_reapply_keeps_the_owner_choice(
    app: Any, db: Any, viewer: Any, operator: Any, box: _Box
) -> None:
    from mso.features.automation import footprint, overrides

    box.values["conns"] = 10
    state.set_state("mode", "manage")
    _apply("fake_fp", "Verbindingen 20", [{"path": "conns", "label": "Verbindingen", "before": 10, "after": 20}])
    box.values["conns"] = 12
    footprint.refresh()
    # MSO itself puts its value back (a path that does not honour overrides, e.g. tuning in manage mode)
    again = changes.create("fake_fp", "Verbindingen 20", [{"path": "conns", "label": "Verbindingen", "before": 12,
                                                           "after": 20}], risk="automatisch_veilig", actor="autotune")
    changes.apply(again["id"], "autotune", automatic=True)
    assert footprint.refresh()["actief"] == 1
    assert overrides.active("fake_fp", "conns") is not None  # the owner's choice is not erased by MSO
    assert _audits(db, "footprint.override_cleared") == []
    assert db.scalar("SELECT status FROM recommendations WHERE dedupe_key='footprint.override:fake_fp:conns'") == "open"
    e = viewer.get("/api/v1/automation/footprint").json()["systems"][0]["entries"][0]
    assert e["applied_by"] == "autotune" and e["automatic"] is True  # MSO's own actor, not a person
    # a person applying MSO's value deliberately does clear it
    box.values["conns"] = 12
    footprint.refresh()
    mine = changes.create("fake_fp", "Verbindingen 20", [{"path": "conns", "label": "Verbindingen", "before": 12,
                                                          "after": 20}], risk="automatisch_veilig", actor="operator")
    changes.apply(mine["id"], "operator")
    footprint.refresh()
    assert overrides.active("fake_fp", "conns") is None and len(_audits(db, "footprint.override_cleared")) == 1
    e = viewer.get("/api/v1/automation/footprint").json()["systems"][0]["entries"][0]
    assert e["applied_by"] == "operator" and e["automatic"] is False


def test_restore_marks_only_the_restored_chain(app: Any, db: Any, operator: Any, viewer: Any, box: _Box) -> None:
    box.values["conns"] = 1
    state.set_state("mode", "manage")
    a = _apply("fake_fp", "A", [{"path": "conns", "label": "Verbindingen", "before": 1, "after": 2}])
    box.values["conns"] = 3  # the owner in between
    b = _apply("fake_fp", "B", [{"path": "conns", "label": "Verbindingen", "before": 3, "after": 4}])
    plan = viewer.get("/api/v1/automation/footprint/restore").json()
    [item] = plan["restore"]
    assert item["after"] == 3 and item["change_ids"] == [b["id"]]
    body = operator.post("/api/v1/automation/footprint/restore", json={"fingerprint": plan["fingerprint"]}).json()
    assert body["results"][0]["status"] == "teruggezet" and box.values["conns"] == 3
    assert changes.get(b["id"])["status"] == "rolled_back"
    assert changes.get(a["id"])["status"] == "applied"  # outside the restored chain: not reported as rolled back
    assert changes.get(a["id"])["ops"][0]["result"] == "toegepast"
    [rb] = _audits(db, "change.rolled_back")
    assert rb["change_id"] == b["id"]
    assert changes.get(body["results"][0]["change_id"])["impact"]["Wijzigingen van MSO"] == f"#{b['id']}"


def test_restore_approved_later_on_changes_is_booked_and_its_rollback_is_footprint_again(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, fake_reg: dict[str, Any]
) -> None:
    from mso.features.automation import footprint, overrides

    state.set_state("mode", "manage")
    reg = _apply("fake_reg", "MSO-indexer registreren", [{"path": "indexer:newznab", "label": "Registratie",
                                                         "before": None, "after": "Naam: MSO"}], actor="beheerder")
    footprint.refresh()
    plan = viewer.get("/api/v1/automation/footprint/restore").json()
    [res] = operator.post("/api/v1/automation/footprint/restore",
                          json={"fingerprint": plan["fingerprint"]}).json()["results"]
    assert res["status"] == "wacht_op_goedkeuring"
    rid = res["change_id"]
    # an admin approves and applies it later on Wijzigingen (not through 'Alles terugzetten')
    assert admin.post(f"/api/v1/changes/{rid}/approve").status_code == 200
    assert operator.post(f"/api/v1/changes/{rid}/apply").status_code == 200
    assert fake_reg["indexer:newznab"] is None
    out = footprint.refresh()
    assert out["checked"] == 0 and out["gewijzigd"] == 0  # no false 'changed outside MSO'
    assert _audits(db, "footprint.overridden") == []
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'footprint.override:%%'") == 0
    assert changes.get(reg["id"])["status"] == "rolled_back"  # booked exactly like 'Alles terugzetten'
    assert overrides.active("fake_reg", "indexer:newznab") is not None
    [booked] = _audits(db, "footprint.restored")
    assert booked["change_id"] == rid and booked["actor"] == "operator"
    footprint.refresh()
    assert len(_audits(db, "footprint.restored")) == 1  # booked once

    # the restore itself is rolled back on Wijzigingen: MSO's registration is back → footprint again
    assert admin.post(f"/api/v1/changes/{rid}/rollback").status_code == 200
    assert fake_reg["indexer:newznab"] == "Naam: MSO"
    out = footprint.refresh()
    assert out["actief"] == 1 and out["gewijzigd"] == 0
    [e] = viewer.get("/api/v1/automation/footprint").json()["systems"][0]["entries"]
    assert e["change_id"] == rid and e["mso_text"] == "Naam: MSO" and e["original_text"] == "niet aanwezig"
    assert e["applied_by"] == "beheerder" and e["automatic"] is False
    assert overrides.active("fake_reg", "indexer:newznab") is None  # a person put MSO's value back


def test_gone_only_for_an_explicitly_removed_object() -> None:
    import json

    from mso.connectors.sabnzbd import SabError
    from mso.features.automation.footprint import _gone

    assert _gone(SabError("SABnzbd-server 'x' niet gevonden", "invalid")) is True
    assert _gone(SabError("SABnzbd-server 'x' bestaat niet", "invalid")) is True
    assert _gone(SabError("SABnzbd gaf geen JSON terug op 'get_config'", "invalid")) is False  # proxy/HTML page
    assert _gone(SabError("Time-out: SABnzbd reageert niet binnen de limiet", "timeout")) is False
    assert _gone(ValueError("torrent niet (meer) aanwezig in qBittorrent")) is True
    assert _gone(json.JSONDecodeError("x", "doc", 0)) is False
    assert _gone(ConnectionError("verbinding geweigerd")) is False


# ------------------------------------------------------------------ integration review (1.6.0)
def test_mso_apply_during_a_refresh_is_not_booked_as_the_owners_choice(app: Any, db: Any,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from mso.features.automation import autopilot, footprint
    from tests.test_change_safety import Fake

    fake = Fake()
    changes.register_applier("fake_v", fake.applier)
    try:
        state.set_state("mode", "autopilot")

        def step(before: int, after: int, key: str) -> dict[str, Any]:
            return autopilot.propose_and_apply("fake_v", "Snelheid", [{"path": "speed", "label": "Snelheid",
                                                                       "before": before, "after": after}],
                                               explanation="test", impact=None, dedupe_key=key, area="usenet")

        assert step(100, 80, "s1")["auto_applied"]
        footprint.refresh()
        real = footprint.read_current

        def read_while_next_step_applies(entries: Any) -> Any:
            assert step(80, 60, "s2")["auto_applied"]  # MSO's next step lands between collect and read
            return real(entries)

        monkeypatch.setattr(footprint, "read_current", read_while_next_step_applies)
        out = footprint.refresh()
        monkeypatch.setattr(footprint, "read_current", real)
        assert out["overridden"] == [] and out["unsettled"] == 1
        assert db.fetchall("SELECT * FROM change_overrides") == []
        footprint.refresh()  # the next run sees MSO's newest value active
        assert db.scalar("SELECT status FROM change_footprint WHERE path='speed'") == "actief"
        assert step(60, 40, "s3")["auto_applied"]  # Autopilot is not blocked
    finally:
        changes.APPLIERS.pop("fake_v", None)


def test_live_footprint_routes_are_rate_limited(app: Any, viewer: Any, operator: Any) -> None:
    codes = {viewer.get("/api/v1/automation/footprint/restore").status_code for _ in range(13)}
    assert codes == {200, 429}
    codes = {operator.post("/api/v1/automation/footprint/refresh").status_code for _ in range(7)}
    assert codes == {200, 429}
    r = operator.post("/api/v1/automation/footprint/refresh")
    assert r.status_code == 429 and "Te veel verzoeken" in r.json()["detail"]
    codes = {operator.post("/api/v1/automation/undo", json={"window": "24h"}).status_code for _ in range(4)}
    assert 429 in codes
