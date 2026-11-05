"""Slice E — controle na een wijziging: built-in evaluators on synthetic measurement series.

better, worse (→ automatic rollback + audit line), insufficient data, irreversible excluded, qBittorrent's
own rollback setting, a rollback that would disable something, compare() helper and the API view."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from mso import changes, state
from mso import settings_registry as sr
from mso.changes import Applier


@pytest.fixture()
def sab(app: Any) -> dict[str, Any]:
    store: dict[str, Any] = {"servers/eweka/priority": 1, "servers/xsnews/priority": 0,
                             "servers/xsnews/enable": False}
    original = changes.APPLIERS.get("sabnzbd")
    changes.register_applier("sabnzbd", lambda: Applier(read=store.get, write=store.__setitem__))
    yield store
    if original is not None:
        changes.APPLIERS["sabnzbd"] = original
    else:
        changes.APPLIERS.pop("sabnzbd", None)


def _outcomes(db: Any, *, start: datetime, end: datetime, ok: int, failed: int, client: str = "sabnzbd",
              duration_s: float = 600) -> None:
    n = ok + failed
    step = (end - start) / (n + 1)
    for i in range(n):
        fin = start + step * (i + 1)
        db.execute(
            "INSERT INTO download_outcomes(protocol, client, client_ref, title, status, started_at, finished_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            ("usenet" if client == "sabnzbd" else "torrent", client, uuid.uuid4().hex, f"t{i}",
             "failed" if i < failed else "success", fin - timedelta(seconds=duration_s), fin),
        )


def _applied(db: Any, target: str, ops: list[dict[str, Any]], *, hours_ago: float = 24,
             risk: str = "toestemming_nodig") -> dict[str, Any]:
    ch = changes.create(target, "Testwijziging", ops, risk=risk, evaluate_after_h=1)
    if risk == "toestemming_nodig":
        changes.approve(ch["id"], "admin")
    changes.apply(ch["id"], "admin")
    db.execute(
        "UPDATE change_sets SET applied_at = now() - make_interval(hours => %s), "
        "evaluate_after = now() - interval '1 minute' WHERE id=%s",
        (hours_ago, ch["id"]),
    )
    return changes.get(ch["id"])


PRIO = [{"path": "servers/eweka/priority", "label": "eweka: prioriteit", "before": 1, "after": 0}]


def _windows() -> tuple[datetime, datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(hours=48), now - timedelta(hours=24), now


def test_compare_helper() -> None:
    from mso.features.automation.evaluators import compare

    v, d = compare([10] * 10, [13] * 10, True, 10)
    assert v == "better" and d["delta_pct"] == 30.0
    assert compare([10] * 10, [10.5] * 10, True, 10)[0] == "same"
    assert compare([10] * 10, [5] * 10, True, 10)[0] == "worse"
    assert compare([0.3] * 10, [0.1] * 10, False, 10, stat="mean")[0] == "better"
    v, d = compare([1, 2], [1, 2, 3], True, 10, min_samples=5)
    assert v == "insufficient" and "te weinig" in d["why"]


def test_sabnzbd_better(db: Any, sab: dict[str, Any], manage_mode: None) -> None:
    from mso.features.automation import engine

    b0, b1, now = _windows()
    _outcomes(db, start=b0, end=b1, ok=14, failed=6)  # 30 % failures, 10 min
    _outcomes(db, start=b1, end=now, ok=19, failed=1, duration_s=300)  # 5 %, 5 min
    ch = _applied(db, "sabnzbd", PRIO)
    counts = engine.evaluate_due()
    assert counts["beter"] == 1
    ev = changes.get(ch["id"])["impact"]["evaluation"]
    assert ev["result"] == "beter" and ev["label"] == "Beter" and ev["action"] == "behouden"
    fr = next(m for m in ev["measures"] if m["key"] == "failure_ratio")
    assert fr["baseline"]["value"] == pytest.approx(0.3) and fr["current"]["value"] == pytest.approx(0.05)
    assert fr["baseline"]["n"] == 20 and fr["verdict"] == "better"
    ttc = next(m for m in ev["measures"] if m["key"] == "time_to_completion")
    assert ttc["baseline"]["value"] == pytest.approx(600) and ttc["current"]["value"] == pytest.approx(300)


def test_sabnzbd_worse_auto_rollback_with_audit(db: Any, sab: dict[str, Any], manage_mode: None) -> None:
    from mso.features.automation import engine

    b0, b1, now = _windows()
    _outcomes(db, start=b0, end=b1, ok=19, failed=1)
    _outcomes(db, start=b1, end=now, ok=12, failed=8)
    sr.put({"automation.auto_rollback": True}, "test")
    ch = _applied(db, "sabnzbd", PRIO)
    assert sab["servers/eweka/priority"] == 0
    counts = engine.evaluate_due()
    assert counts["slechter"] == 1 and counts["rolled_back"] == 1
    c = changes.get(ch["id"])
    assert c["status"] == "rolled_back" and sab["servers/eweka/priority"] == 1
    ev = c["impact"]["evaluation"]
    assert ev["result"] == "slechter" and ev["action"] == "teruggedraaid" and "Faalratio" in ev["reason"]
    line = db.fetchone("SELECT * FROM audit_log WHERE action='change.auto_rolled_back'")
    assert line is not None and line["change_id"] == ch["id"] and "verslechtering" in line["summary"]
    assert line["detail"]["reason"] and line["detail"]["setting"] == "automation.auto_rollback"


def test_worse_without_setting_proposes_and_read_only_never_rolls_back(
    db: Any, sab: dict[str, Any], manage_mode: None
) -> None:
    from mso.features.automation import engine

    b0, b1, now = _windows()
    _outcomes(db, start=b0, end=b1, ok=19, failed=1)
    _outcomes(db, start=b1, end=now, ok=12, failed=8)
    ch = _applied(db, "sabnzbd", PRIO)
    engine.evaluate_due()  # automation.auto_rollback default off
    c = changes.get(ch["id"])
    assert c["status"] == "applied" and c["impact"]["evaluation"]["action"] == "voorgesteld"
    assert db.scalar("SELECT count(*) FROM recommendations WHERE change_id=%s", (ch["id"],)) == 1

    # setting on, but alleen-lezen: proposal only
    sr.put({"automation.auto_rollback": True}, "test")
    ch2 = _applied(db, "sabnzbd", [{"path": "servers/xsnews/priority", "before": 0, "after": 2}])
    state.set_state("mode", "read_only")
    engine.evaluate_due()
    assert changes.get(ch2["id"])["status"] == "applied"
    assert changes.get(ch2["id"])["impact"]["evaluation"]["action"] == "voorgesteld"


def test_insufficient_data(db: Any, sab: dict[str, Any], manage_mode: None) -> None:
    from mso.features.automation import engine

    b0, b1, now = _windows()
    _outcomes(db, start=b0, end=b1, ok=2, failed=1)
    _outcomes(db, start=b1, end=now, ok=0, failed=3)
    sr.put({"automation.auto_rollback": True}, "test")
    ch = _applied(db, "sabnzbd", PRIO)
    assert engine.evaluate_due()["onvoldoende"] == 1
    c = changes.get(ch["id"])
    assert c["status"] == "applied" and c["impact"]["evaluation"]["result"] == "onvoldoende"
    assert c["impact"]["evaluation"]["label"] == "Onvoldoende data"


def test_irreversible_and_destructive_excluded(db: Any, sab: dict[str, Any], manage_mode: None) -> None:
    from mso.features.automation import engine

    b0, b1, now = _windows()
    _outcomes(db, start=b0, end=b1, ok=19, failed=1)
    _outcomes(db, start=b1, end=now, ok=5, failed=15)
    sr.put({"automation.auto_rollback": True}, "test")
    sab["servers/eweka/enable"] = True
    ch = changes.create(
        "sabnzbd", "Server uitschakelen",
        [{"path": "servers/eweka/enable", "before": True, "after": False, "destructive": True}],
        risk="toestemming_nodig",
    )  # no evaluate_after: still marked right after applying
    changes.approve(ch["id"], "admin")
    changes.apply(ch["id"], "admin")
    assert engine.evaluate_due()["niet_van_toepassing"] == 1
    c = changes.get(ch["id"])
    assert c["status"] == "applied" and sab["servers/eweka/enable"] is False
    assert c["impact"]["evaluation"]["result"] == "niet_van_toepassing"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.auto_rolled_back'") == 0


def test_rollback_that_disables_is_only_proposed(db: Any, sab: dict[str, Any], manage_mode: None) -> None:
    from mso.features.automation import engine

    b0, b1, now = _windows()
    _outcomes(db, start=b0, end=b1, ok=19, failed=1)
    _outcomes(db, start=b1, end=now, ok=10, failed=10)
    sr.put({"automation.auto_rollback": True}, "test")
    ch = _applied(db, "sabnzbd", [{"path": "servers/xsnews/enable", "before": False, "after": True}])
    engine.evaluate_due()
    c = changes.get(ch["id"])
    assert c["status"] == "applied" and sab["servers/xsnews/enable"] is True
    assert "uitschakelen" in c["impact"]["evaluation"]["message"]


def test_qbittorrent_follows_torrent_setting(db: Any, manage_mode: None) -> None:
    from mso.features.automation import engine
    from mso.features.automation.evaluators import EVALUATORS, evaluation, measure

    store: dict[str, Any] = {"max_connec": 50000}
    original_applier = changes.APPLIERS.get("qbittorrent")
    original_eval = EVALUATORS.get("qbittorrent")
    changes.register_applier("qbittorrent", lambda: Applier(read=store.get, write=store.__setitem__))
    EVALUATORS["qbittorrent"] = lambda ch: evaluation([measure(
        "download_speed", label="Downloadsnelheid", unit="B/s", baseline=[100.0] * 10, current=[50.0] * 10,
        higher_is_better=True, hysteresis_pct=10, min_samples=5)])
    try:
        if "torrent.autotune_auto_rollback" not in sr.REGISTRY:
            pytest.skip("torrent module niet geladen")
        sr.put({"automation.auto_rollback": False, "torrent.autotune_auto_rollback": True}, "test")
        ch = _applied(db, "qbittorrent", [{"path": "max_connec", "before": 50000, "after": 500}])
        engine.evaluate_due()
        assert changes.get(ch["id"])["status"] == "rolled_back" and store["max_connec"] == 50000
        line = db.fetchone("SELECT detail FROM audit_log WHERE action='change.auto_rolled_back'")
        assert line["detail"]["setting"] == "torrent.autotune_auto_rollback"
    finally:
        if original_applier is not None:
            changes.APPLIERS["qbittorrent"] = original_applier
        else:
            changes.APPLIERS.pop("qbittorrent", None)
        if original_eval is not None:
            EVALUATORS["qbittorrent"] = original_eval
        else:
            EVALUATORS.pop("qbittorrent", None)


def test_evaluations_api_interim_and_status(
    db: Any, viewer: Any, sab: dict[str, Any], manage_mode: None
) -> None:
    b0, b1, now = _windows()
    _outcomes(db, start=b0, end=b1, ok=14, failed=6)
    _outcomes(db, start=b1, end=now, ok=19, failed=1)
    ch = _applied(db, "sabnzbd", PRIO)
    db.execute("UPDATE change_sets SET evaluate_after = now() + interval '1 hour' WHERE id=%s", (ch["id"],))
    items = viewer.get("/api/v1/automation/evaluations").json()["items"]
    row = next(i for i in items if i["id"] == ch["id"])
    assert row["evaluation"] is None and row["interim"]["result"] == "beter" and row["applicable"] is True
    one = viewer.get(f"/api/v1/automation/evaluations/{ch['id']}").json()
    assert one["interim"]["measures"][0]["baseline"]["n"] == 20
    st = viewer.get("/api/v1/automation/status").json()
    assert "sabnzbd" in st["evaluators"] and st["evaluator_fallback"] is True


def test_accept_generic_proposal_with_destructive_op(operator: Any, db: Any, sab: dict[str, Any]) -> None:
    from mso.domain import outcomes

    proposed = {
        "target": "sabnzbd",
        "title": "Kapotte server xsnews uitschakelen",
        "risk": "automatisch_veilig",  # must be lifted: disabling is always toestemming_nodig
        "ops": [{"path": "servers/xsnews/enable", "label": "xsnews: ingeschakeld", "before": True,
                 "after": False, "destructive": True}],
        "impact": {"Server": "xsnews", "_technical": {"error": "482 Account expired"}},
    }
    rid = outcomes.recommend("usenet", "Server uitschakelen", "x", risk="toestemming_nodig",
                             dedupe_key="srv", evidence={"proposed_change": proposed})
    acc = operator.post(f"/api/v1/automation/recommendations/{rid}/accept").json()
    ch = changes.get(acc["change"]["id"])
    assert ch["risk"] == "toestemming_nodig" and ch["ops"][0]["destructive"] is True
    assert ch["impact"]["_technical"]["error"] == "482 Account expired" and ch["impact"]["area"] == "usenet"
    bad = outcomes.recommend("usenet", "Kapot", "x", dedupe_key="kapot",
                             evidence={"proposed_change": {"target": "sabnzbd", "ops": []}})
    assert operator.post(f"/api/v1/automation/recommendations/{bad}/accept").status_code == 422
