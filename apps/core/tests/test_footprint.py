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
