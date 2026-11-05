"""1.5.0 — "Wat MSO deed" / "Wacht op jou" (GET /api/v1/overview/autopilot) and Systeem → Automatische taken."""

from __future__ import annotations

from typing import Any

import pytest

from mso import changes, jobs, state
from mso.audit import audit
from mso.changes import Applier


@pytest.fixture()
def fake(app: Any) -> dict[str, Any]:
    store: dict[str, Any] = {"srv": True, "conns": 10, "file": 1}
    changes.register_applier("fake_ov", lambda: Applier(read=store.get, write=store.__setitem__))
    yield store
    changes.APPLIERS.pop("fake_ov", None)


def test_summary_counts_automatic_actions_only(admin: Any, viewer: Any, db: Any, fake: dict[str, Any]) -> None:
    from mso.features.automation import autopilot

    state.set_state("mode", "autopilot")
    autopilot.propose_and_apply("fake_ov", "Verbindingen 20", [{"path": "conns", "before": 10, "after": 20}],
                                explanation="", impact=None, dedupe_key="a", area="usenet")
    autopilot.propose_and_apply("fake_ov", "Server uit", [{"path": "srv", "before": True, "after": False,
                                                           "destructive": True}],
                                explanation="", impact=None, dedupe_key="b", area="usenet")
    audit("systeem:herstel", "usenet.download_retried", "Download opnieuw geprobeerd: X")
    audit("system", "mediaguard.search_requested", "MediaGuard gevraagd opnieuw te zoeken: Y (automatisch)")
    audit("beheerder", "mediaguard.search_requested", "Handmatig: Z")  # a person → not counted
    audit("system", "mediaguard.sync", "MediaGuard-synchronisatie: 3 items")  # counted via jobs, not audit
    jobs.enqueue("mediaguard.sync", {}, idempotency_key="sched:x", created_by="scheduler")
    db.execute("UPDATE jobs SET status='done' WHERE idempotency_key='sched:x'")
    # a cleanup that needs approval (deleting) — prepared + waiting
    gone = autopilot.propose_and_apply("fake_ov", "Malware opruimen", [{"path": "file", "after": None,
                                                                         "destructive": True,
                                                                         "irreversible": True}],
                                       explanation="", impact=None, dedupe_key="c", area="torrent")

    body = viewer.get("/api/v1/overview/autopilot?days=1").json()
    cats = {c["key"]: c["count"] for c in body["categories"]}
    assert cats["applied"] == 1 and cats["switched_off"] == 1 and cats["retries"] == 1
    assert cats["searches"] == 1 and cats["prepared"] == 1 and cats["syncs"] == 1
    assert "other" not in cats
    assert body["autopilot"] is True and body["days"] == 1 and len(body["by_day"]) == 1
    assert body["by_day"][0]["total"] == body["total"]
    assert all(i["actor"] != "beheerder" for i in body["items"])
    applied = next(i for i in body["items"] if i["category"] == "applied")
    assert applied["link"].startswith("/automatisering?tab=controle&change=")
    w = body["waiting"]
    assert w["count"] == 1 and w["items"][0]["id"] == gone["id"] and w["items"][0]["deletes"] is True
    assert "één klik" in w["items"][0]["reason"]

    week = viewer.get("/api/v1/overview/autopilot?days=7").json()
    assert len(week["by_day"]) == 7 and week["total"] == body["total"]
    assert viewer.get("/api/v1/overview/autopilot?days=0").status_code == 422


def test_waiting_lists_safe_proposals_blocked_by_mode(viewer: Any, fake: dict[str, Any]) -> None:
    from mso.features.automation import autopilot

    state.set_state("mode", "manage")
    off = autopilot.propose_and_apply("fake_ov", "Server uit", [{"path": "srv", "before": True, "after": False,
                                                                 "destructive": True}],
                                      explanation="", impact=None, dedupe_key="b", area="usenet")
    assert off["auto_applied"] is False
    w = viewer.get("/api/v1/overview/autopilot").json()["waiting"]
    assert [i["id"] for i in w["items"]] == [off["id"]] and "Autopilot" in w["items"][0]["reason"]
    state.set_state("mode", "autopilot")  # MSO will apply it itself in the next round → no longer waiting
    assert viewer.get("/api/v1/overview/autopilot").json()["waiting"]["count"] == 0


def test_schedules_listed_with_status(viewer: Any, db: Any) -> None:
    body = viewer.get("/api/v1/automation/schedules").json()
    kinds = {i["kind"]: i for i in body["items"]}
    for k in ("costs.import_services", "automation.evaluate", "backup.create"):
        assert k in kinds
    assert kinds["costs.import_services"]["enabled"] is True and kinds["costs.import_services"]["next_run_at"]
    assert kinds["backup.create"]["enabled"] is True  # daily backup on by default
    assert kinds["automation.apply_safe"]["enabled"] is False  # read-only by default
    assert kinds["automation.apply_safe"]["reason"]
    jobs.enqueue("costs.import_services", {}, idempotency_key="k1", created_by="scheduler")
    db.execute("UPDATE jobs SET status='failed', error='boem' WHERE idempotency_key='k1'")
    item = {i["kind"]: i for i in viewer.get("/api/v1/automation/schedules").json()["items"]}["costs.import_services"]
    assert item["status"] == "mislukt" and item["failed_24h"] == 1 and item["last_error"] == "boem"


def test_usenet_and_torrent_autopilot_actions(viewer: Any, db: Any, fake: dict[str, Any]) -> None:
    from mso.features.automation import TARGET_AREA, autopilot

    assert TARGET_AREA["sabnzbd_retry"] == "usenet" and TARGET_AREA["download_send"] == "recovery"
    state.set_state("mode", "autopilot")
    for a in ("usenet.server_probe_failed", "usenet.server_recovered", "usenet.server_kept_last",
              "usenet.server_retry"):
        audit("automatisering", a, f"{a} xsnews", target="sabnzbd")
    audit("automatisering", "sabnzbd.retry", "Mislukte download opnieuw geprobeerd in SABnzbd", target="sabnzbd")
    audit("automatisering", "sabnzbd.retry", "Mislukte download opnieuw geprobeerd in SABnzbd", target="sabnzbd")
    audit("automatisering", "change.applied", "Toegepast: retry-bundel", target="sabnzbd_retry",
          detail={"automatic": True})  # bundle itself: not counted twice
    rem = autopilot.propose_and_apply("fake_ov", "Recheck", [{"path": "conns", "before": 10, "after": 11}],
                                      explanation="", impact=None, dedupe_key="r", area="torrent")
    audit("autopilot", "torrent.autopilot_remedy", "Autopilot recheck: x", target="qbittorrent", change_id=rem["id"])
    clean = autopilot.propose_and_apply("fake_ov", "Opruimen", [{"path": "file", "after": None, "destructive": True,
                                                                  "irreversible": True}],
                                        explanation="", impact=None, dedupe_key="c", area="torrent")
    audit("autopilot", "torrent.cleanup_proposed", "Opruimvoorstel klaargezet", target="qbittorrent",
          change_id=clean["id"])
    audit("autopilot", "torrent.cleanup_refreshed", "Opruimvoorstel ververst", target="qbittorrent",
          change_id=clean["id"])
    audit("beheerder", "mediaguard.search_requested", "MediaGuard (automatisch)", detail={"trigger": "auto"})
    db.execute("INSERT INTO users(username,password_hash,role) VALUES ('beheerder','x','admin') "
               "ON CONFLICT DO NOTHING")
    cats = {c["key"]: c["count"] for c in viewer.get("/api/v1/overview/autopilot").json()["categories"]}
    assert cats["servers"] == 4 and cats["retries"] == 2 and cats["remedies"] == 1
    assert cats["prepared"] == 1 and cats["searches"] == 1 and "applied" not in cats and "failed" not in cats
    sched = viewer.get("/api/v1/automation/schedules").json()
    assert "usenet" in sched["autopilot_runs"] and "torrent" in sched["autopilot_runs"]
