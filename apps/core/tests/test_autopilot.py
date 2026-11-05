"""Autopilot (1.5.0): mode switching, engine behaviour and the propose_and_apply/enabled helpers."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from mso import changes, state
from mso import settings_registry as sr
from mso.changes import Applier


@pytest.fixture()
def fake(app: Any) -> dict[str, Any]:
    store: dict[str, Any] = {"server.a": True, "server.b": True, "conns": 10, "speed": 5}
    changes.register_applier("fake_ap", lambda: Applier(read=store.get, write=store.__setitem__))
    yield store
    changes.APPLIERS.pop("fake_ap", None)


def _off(name: str = "a", **kw: Any) -> dict[str, Any]:
    from mso.features.automation import autopilot

    return autopilot.propose_and_apply(
        "fake_ap", f"Server {name} uit", [{"path": f"server.{name}", "label": f"{name} actief", "before": True,
                                           "after": False, "destructive": True}],
        explanation="kapot", impact={"Reden": "502"}, dedupe_key=f"off:{name}", area="usenet", **kw)


def _tune(after: int = 20, key: str = "tune") -> dict[str, Any]:
    from mso.features.automation import autopilot

    return autopilot.propose_and_apply(
        "fake_ap", f"Verbindingen naar {after}", [{"path": "conns", "before": 10, "after": after}],
        explanation="sneller", impact=None, dedupe_key=key, area="usenet", evaluate_after_h=2)


def test_read_only_blocks_everything(admin: Any, fake: dict[str, Any]) -> None:
    from mso.features.automation import autopilot, engine

    sr.put({"automation.usenet.auto_apply_safe": True}, "test")
    assert state.mode() == "read_only" and not autopilot.enabled("usenet")
    ch = _tune()
    assert ch["auto_applied"] is False and ch["status"] == "preview" and "alleen-lezen" in ch["auto_reason"]
    assert ch["risk"] == "automatisch_veilig" and ch["impact"]["area"] == "usenet"
    assert engine.apply_safe()["skipped"] == "not_allowed"
    with pytest.raises(HTTPException):
        changes.apply(ch["id"], "auto", automatic=True)
    assert admin.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 409
    assert fake["conns"] == 10


def test_manage_mode_follows_area_toggle_and_never_switches_off(fake: dict[str, Any], manage_mode: None) -> None:
    from mso.features.automation import autopilot

    assert not autopilot.enabled("usenet")
    first = _tune(key="t1")
    assert first["auto_applied"] is False and "staat uit" in first["auto_reason"]
    sr.put({"automation.usenet.auto_apply_safe": True}, "test")
    assert autopilot.enabled("usenet") and not autopilot.enabled("torrent")
    second = _tune(30, key="t2")
    assert second["auto_applied"] is True and second["status"] == "applied" and fake["conns"] == 30
    assert second["applied_by"] == "automatisering" and second["evaluate_after"] is not None
    off = _off()
    assert off["auto_applied"] is False and "Autopilot" in off["auto_reason"] and fake["server.a"] is True


def test_autopilot_applies_switch_off_but_never_deletes(admin: Any, db: Any, fake: dict[str, Any]) -> None:
    from mso.features.automation import autopilot

    assert admin.put("/api/v1/system/mode", json={"mode": "autopilot"}).status_code == 200
    assert state.autopilot() and autopilot.enabled("usenet") and autopilot.enabled("torrent")
    assert autopilot.enabled("iets_anders")  # every area in Autopilot
    off = _off()
    assert off["auto_applied"] is True and fake["server.a"] is False
    applied = db.fetchone("SELECT detail FROM audit_log WHERE action='change.applied' ORDER BY id DESC LIMIT 1")
    assert applied["detail"]["automatic"] is True
    # dedupe: the same key does not create/apply a second change while the first is open; applied → new one ok
    again = _off()
    assert again["id"] != off["id"] and again["auto_applied"] is True  # already False → 'al_ingesteld'
    gone = autopilot.propose_and_apply(
        "fake_ap", "Bestand weg", [{"path": "speed", "after": 0, "destructive": True, "irreversible": True}],
        explanation="malware", impact={}, dedupe_key="del", area="recovery")
    assert gone["risk"] == "toestemming_nodig" and gone["auto_applied"] is False and gone["status"] == "preview"
    assert "nooit automatisch" in gone["auto_reason"] and fake["speed"] == 5


def test_switch_to_autopilot_unpauses(admin: Any, viewer: Any) -> None:
    state.set_state("paused", True)
    body = admin.put("/api/v1/system/mode", json={"mode": "autopilot"}).json()
    assert body["mode"] == "autopilot" and body["paused"] is False and body["autopilot"] is True
    mode = viewer.get("/api/v1/system/mode").json()
    assert mode["automation_allowed"] is True
    admin.post("/api/v1/system/pause")
    assert state.autopilot() is False  # paused autopilot does nothing by itself


def test_paused_autopilot_and_rate_cap(fake: dict[str, Any], db: Any) -> None:
    from mso.features.automation import engine

    state.set_state("mode", "autopilot")
    state.set_state("paused", True)
    ch = _off()
    assert ch["auto_applied"] is False and "gepauzeerd" in ch["auto_reason"]
    state.set_state("paused", False)
    sr.put({"automation.max_per_hour": 1}, "test")
    assert engine.apply_safe()["applied"] == [ch["id"]]  # the engine picks up the pending switch-off
    assert fake["server.a"] is False
    capped = _off("b")
    assert capped["auto_applied"] is False and "limiet" in capped["auto_reason"] and fake["server.b"] is True
    assert db.scalar("SELECT count(*) FROM automation_log WHERE result='rate_limited'") == 1


def test_engine_apply_safe_autopilot_ignores_toggles(fake: dict[str, Any]) -> None:
    from mso.features.automation import engine

    ch = changes.create("fake_ap", "Uit", [{"path": "server.a", "before": True, "after": False,
                                            "destructive": True}], risk="automatisch_veilig",
                        impact={"area": "usenet"})
    state.set_state("mode", "manage")
    assert engine.apply_safe()["applied"] == []
    sr.put({"automation.usenet.auto_apply_safe": True}, "test")
    assert engine.apply_safe()["applied"] == []  # switch-off: not in manage mode, even with toggle
    state.set_state("mode", "autopilot")
    sr.put({"automation.usenet.auto_apply_safe": False}, "test")
    assert engine.apply_safe()["applied"] == [ch["id"]]


def test_auto_rollback_on_in_autopilot(fake: dict[str, Any]) -> None:
    from mso.features.automation import engine

    ch = {"target": "fake_ap"}
    state.set_state("mode", "manage")
    assert engine.auto_rollback_setting(ch) == (False, "automation.auto_rollback")
    state.set_state("mode", "autopilot")
    assert engine.auto_rollback_setting(ch) == (True, "autopilot")


def test_autopilot_rolls_back_worse_change(fake: dict[str, Any], db: Any) -> None:
    from mso.features.automation import EVALUATORS, engine

    state.set_state("mode", "autopilot")
    ch = _tune(40)
    assert ch["auto_applied"] is True and fake["conns"] == 40
    db.execute("UPDATE change_sets SET evaluate_after=now() - interval '1 minute' WHERE id=%s", (ch["id"],))
    EVALUATORS["fake_ap"] = lambda c: "worse"
    try:
        assert engine.evaluate_due()["rolled_back"] == 1
    finally:
        EVALUATORS.pop("fake_ap", None)
    assert fake["conns"] == 10 and changes.get(ch["id"])["status"] == "rolled_back"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.auto_rolled_back'") == 1


def test_status_shows_areas_on_via_autopilot(admin: Any) -> None:
    state.set_state("mode", "autopilot")
    s = admin.get("/api/v1/automation/status").json()
    assert s["autopilot"] is True and s["auto_rollback"] is True
    assert all(a["effective"] and a["via_autopilot"] for a in s["areas"])
    assert s["max_per_hour"] == 20


def test_rolled_back_without_measurement_is_not_applicable(admin: Any, fake: dict[str, Any]) -> None:
    state.set_state("mode", "manage")
    ch = changes.create("fake_ap", "Tune", [{"path": "conns", "before": 10, "after": 12}], risk="automatisch_veilig")
    changes.apply(ch["id"], "beheerder")
    changes.rollback(ch["id"], "beheerder")
    item = admin.get(f"/api/v1/automation/evaluations/{ch['id']}").json()
    assert item["evaluation"]["label"] == "n.v.t. (teruggedraaid)" and item["interim"] is None
