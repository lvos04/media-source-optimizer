"""Slice E — automation: safe-only unattended apply, toggles, rate cap, max age, evaluators, inbox."""

from __future__ import annotations

from typing import Any

import pytest

from mso import changes, state
from mso.changes import Applier
from mso.domain import outcomes


@pytest.fixture()
def fake_target() -> dict[str, Any]:
    store: dict[str, Any] = {"queue.max": 3, "speed": 10}
    original = changes.APPLIERS.get("qbittorrent")  # Slice C's real applier: restore it afterwards
    changes.register_applier("qbittorrent", lambda: Applier(read=store.get, write=store.__setitem__))
    yield store
    if original is not None:
        changes.APPLIERS["qbittorrent"] = original
    else:
        changes.APPLIERS.pop("qbittorrent", None)


def _ch(
    risk: str = "automatisch_veilig", path: str = "queue.max", after: Any = 5, before: Any = 3, **kw: Any
) -> dict[str, Any]:
    return changes.create(
        "qbittorrent",
        f"Zet {path} op {after}",
        [{"path": path, "before": before, "after": after}],
        risk=risk,
        **kw,
    )


def _toggle(area: str, on: bool) -> None:
    from mso import settings_registry as sr

    sr.put({f"automation.{area}.auto_apply_safe": on}, "test")


def test_defaults_off_and_nothing_applied(app: Any, fake_target: dict[str, Any], manage_mode: None) -> None:
    from mso.features.automation import engine

    _ch()
    res = engine.apply_safe()
    assert res["applied"] == [] and fake_target["queue.max"] == 3  # toggle off by default


def test_only_safe_in_manage_mode_with_toggle(app: Any, db: Any, fake_target: dict[str, Any]) -> None:
    from mso.features.automation import engine

    safe = _ch()
    advice = _ch(risk="advies", path="speed", after=20, before=10)
    needs = _ch(risk="toestemming_nodig", path="speed", after=30, before=10, dedupe_key="x")
    _toggle("torrent", True)
    assert engine.apply_safe()["skipped"] == "not_allowed"  # read-only mode
    state.set_state("mode", "manage")
    state.set_state("paused", True)
    assert engine.apply_safe()["skipped"] == "not_allowed"
    state.set_state("paused", False)
    res = engine.apply_safe()
    assert res["applied"] == [safe["id"]]
    assert fake_target == {"queue.max": 5, "speed": 10}
    assert (
        changes.get(advice["id"])["status"] == "preview" and changes.get(needs["id"])["status"] == "preview"
    )
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='automation.applied'") == 1
    assert changes.get(safe["id"])["applied_by"] == "automatisering"


def test_rate_cap_and_max_age(app: Any, db: Any, fake_target: dict[str, Any], manage_mode: None) -> None:
    from mso import settings_registry as sr
    from mso.features.automation import engine

    _toggle("torrent", True)
    sr.put({"automation.max_per_hour": 2}, "test")
    for i in range(4):
        changes.create("qbittorrent", f"c{i}", [{"path": f"k{i}", "after": i}], risk="automatisch_veilig")
    old = changes.create("qbittorrent", "oud", [{"path": "old", "after": 1}], risk="automatisch_veilig")
    db.execute("UPDATE change_sets SET created_at = now() - interval '48 hours' WHERE id=%s", (old["id"],))
    res = engine.apply_safe()
    assert len(res["applied"]) == 2
    assert engine.apply_safe()["applied"] == []  # cap reached within the hour
    assert db.scalar("SELECT count(*) FROM automation_log WHERE result='rate_limited'") >= 2
    assert changes.get(old["id"])["status"] == "preview"  # too old, never auto-applied


def test_linked_recommendation_area_wins(
    app: Any, db: Any, fake_target: dict[str, Any], manage_mode: None
) -> None:
    from mso.features.automation import engine

    ch = _ch()
    outcomes.recommend(
        "recovery", "Herstel", "x", risk="automatisch_veilig", change_id=ch["id"], dedupe_key="r"
    )
    _toggle("torrent", True)
    assert engine.apply_safe()["applied"] == []  # area is 'recovery', not toggled
    _toggle("recovery", True)
    assert engine.apply_safe()["applied"] == [ch["id"]]
    assert db.scalar("SELECT status FROM recommendations WHERE dedupe_key='r'") == "accepted"


def test_evaluation_without_own_evaluator_uses_generic_and_never_blind_rollback(
    app: Any, db: Any, fake_target: dict[str, Any], manage_mode: None
) -> None:
    from mso.features.automation import EVALUATORS, engine

    original_eval = EVALUATORS.pop("qbittorrent", None)
    try:
        ch = _ch(evaluate_after_h=1)
        changes.apply(ch["id"], "beheerder")
        db.execute(
            "UPDATE change_sets SET evaluate_after = now() - interval '1 minute' WHERE id=%s", (ch["id"],)
        )
        counts = engine.evaluate_due()
        assert counts["onvoldoende"] == 1  # generic fallback, no measurements -> insufficient data
        c = changes.get(ch["id"])
        ev = c["impact"]["evaluation"]
        assert c["status"] == "applied" and ev["result"] == "onvoldoende" and ev["evaluator"] == "generiek"
        assert {m["key"] for m in ev["measures"]} >= {"failure_ratio", "time_to_completion"}
        assert engine.evaluate_due()["onvoldoende"] == 0  # evaluated once
    finally:
        if original_eval is not None:
            EVALUATORS["qbittorrent"] = original_eval


def test_evaluator_hook_keep_and_rollback(app: Any, db: Any, manage_mode: None) -> None:
    """Legacy string evaluators keep working (keep → gelijk, rollback → slechter, unknown → onvoldoende)."""
    from mso import settings_registry as sr
    from mso.features.automation import engine, register_evaluator
    from mso.features.automation.evaluators import EVALUATORS

    store: dict[str, Any] = {"queue.max": 3, "speed": 10}
    changes.register_applier("testdoel", lambda: Applier(read=store.get, write=store.__setitem__))
    verdict = {"v": "keep"}
    register_evaluator("testdoel", lambda change: verdict["v"])

    def mk(path: str, after: Any, before: Any) -> dict[str, Any]:
        return changes.create(
            "testdoel", f"Zet {path} op {after}", [{"path": path, "before": before, "after": after}],
            risk="automatisch_veilig", impact={"area": "torrent"}, evaluate_after_h=1,
        )

    def due(cid: int) -> None:
        db.execute("UPDATE change_sets SET evaluate_after = now() - interval '1 minute' WHERE id=%s", (cid,))

    try:
        _toggle("torrent", True)
        keep = mk("queue.max", 5, 3)
        engine.apply_safe()
        due(keep["id"])
        assert engine.evaluate_due()["gelijk"] == 1
        assert changes.get(keep["id"])["impact"]["evaluation"]["result"] == "gelijk"

        # negative verdict, auto rollback off -> proposal (toestemming_nodig), nothing rolled back
        verdict["v"] = "rollback"
        bad = mk("speed", 50, 10)
        engine.apply_safe()
        due(bad["id"])
        engine.evaluate_due()
        assert changes.get(bad["id"])["status"] == "applied" and store["speed"] == 50
        assert changes.get(bad["id"])["impact"]["evaluation"]["action"] == "voorgesteld"
        rec = db.fetchone(
            "SELECT * FROM recommendations WHERE change_id=%s AND risk='toestemming_nodig'", (bad["id"],)
        )
        assert rec is not None

        # auto rollback on -> rolled back with a dedicated audit line
        sr.put({"automation.auto_rollback": True}, "test")
        bad2 = mk("speed", 70, 50)
        engine.apply_safe()
        due(bad2["id"])
        assert engine.evaluate_due()["rolled_back"] == 1
        assert changes.get(bad2["id"])["status"] == "rolled_back" and store["speed"] == 50
        assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.auto_rolled_back'") == 1

        # evaluator crash -> insufficient, never rollback, secret redacted
        def boom(change: dict[str, Any]) -> str:
            raise RuntimeError("apikey=geheim123 kapot")

        register_evaluator("testdoel", boom)
        c3 = mk("queue.max", 9, 5)
        engine.apply_safe()
        due(c3["id"])
        assert engine.evaluate_due()["onvoldoende"] == 1
        ev = changes.get(c3["id"])["impact"]["evaluation"]
        assert ev["result"] == "onvoldoende" and "geheim123" not in ev["message"]
        assert changes.get(c3["id"])["status"] == "applied"
    finally:
        EVALUATORS.pop("testdoel", None)
        changes.APPLIERS.pop("testdoel", None)


def test_api_inbox_accept_dismiss_toggles(
    admin: Any, operator: Any, viewer: Any, db: Any, fake_target: dict[str, Any]
) -> None:
    proposed = {
        "target": "qbittorrent",
        "title": "Wachtrij naar 4",
        "risk": "automatisch_veilig",
        "ops": [{"path": "queue.max", "before": 3, "after": 4, "label": "Max actieve torrents"}],
    }
    r1 = outcomes.recommend(
        "torrent",
        "Verhoog wachtrij",
        "uitleg",
        risk="automatisch_veilig",
        dedupe_key="a",
        evidence={"proposed_change": proposed},
    )
    r2 = outcomes.recommend("costs", "Verlenging", "uitleg", dedupe_key="b")
    lst = viewer.get("/api/v1/automation/recommendations").json()
    assert {i["id"] for i in lst["items"]} == {r1, r2}
    assert next(i for i in lst["items"] if i["id"] == r1)["can_create_change"] is True
    assert viewer.get("/api/v1/automation/recommendations?area=costs").json()["items"][0]["id"] == r2
    assert viewer.post(f"/api/v1/automation/recommendations/{r1}/accept").status_code == 403
    acc = operator.post(f"/api/v1/automation/recommendations/{r1}/accept").json()
    assert acc["status"] == "accepted" and acc["change"]["status"] == "preview"
    assert fake_target["queue.max"] == 3  # accepting never applies
    assert operator.post(f"/api/v1/automation/recommendations/{r1}/accept").status_code == 409
    d = operator.post(f"/api/v1/automation/recommendations/{r2}/dismiss", json={"reason": "al geregeld"})
    assert d.json()["status"] == "dismissed"
    assert operator.put("/api/v1/automation/toggles", json={"areas": {"torrent": True}}).status_code == 403
    st = admin.put("/api/v1/automation/toggles", json={"areas": {"torrent": True}}).json()
    assert next(a for a in st["areas"] if a["area"] == "torrent")["auto_apply_safe"] is True
    assert st["automation_allowed"] is False and "advies" in st["risk_explanation"]
    assert admin.put("/api/v1/automation/toggles", json={"areas": {"bestaatniet": True}}).status_code == 422
    assert operator.post("/api/v1/automation/run").status_code == 409  # read-only
    state.set_state("mode", "manage")
    assert operator.post("/api/v1/automation/run").status_code == 200
    actions = {r["action"] for r in db.fetchall("SELECT action FROM audit_log")}
    assert {"recommendation.accepted", "recommendation.dismissed", "automation.toggle"} <= actions
