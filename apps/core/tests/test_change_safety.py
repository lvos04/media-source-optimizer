"""1.6.0 WP-E1 — core safety of changes and Autopilot.

- C12: a rollback that switches something off is recognised for SABnzbd's int 0/1 too; a re-enabled SABnzbd server
  is judged on its own status, never on the global failure ratio (and never rolled back by the engine).
- C25: no "Terugdraaien aanbevolen" for changes that cannot be rolled back (one_way / irreversible).
- C26: cooldown after failed automatic applies (1 h → 4 h → parked after 3, changed preview resets), failed
  attempts count for the hourly cap, the noodrem (hold + Hervatten) and "Autopilot terugdraaien (24 u / 7 d)".
- C28: rollback with a drift check per op, read-back verification, no false "teruggedraaid"; atomic transitions.
- C36: crash-safe apply ('applying' journal) and the reconciler for interrupted applies.
- C35 (part): the owner's override blocks automatic re-application in why_not().
Unit tests on a fake applier plus the same flows over real HTTP against the SABnzbd mock.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi import HTTPException

from mso import changes, state
from mso import settings_registry as sr
from mso.changes import Applier

SAB_KEY = "SABGEHEIM-veiligheid-0123456789"


class Crash(BaseException):
    """Simulates the process dying right after a write (not caught by ``except Exception``)."""


class Fake:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {"speed": 100, "conns": 50, "srv/enable": 0, "retry": "mislukt"}
        self.fail: set[tuple[str, Any]] = set()  # (path, value) writes that raise
        self.ignore: set[str] = set()  # writes that are accepted but change nothing (verification fails)
        self.crash_after: str | None = None
        self.read_fail = False
        self.writes: list[tuple[str, Any]] = []

    def applier(self) -> Applier:
        def read(path: str) -> Any:
            if self.read_fail:
                raise RuntimeError("bron offline (apikey=geheim999)")
            return self.values.get(path)

        def write(path: str, value: Any) -> None:
            self.writes.append((path, value))
            if (path, value) in self.fail:
                raise RuntimeError("schrijven geweigerd")
            if path not in self.ignore:
                self.values[path] = value
            if self.crash_after == path:
                raise Crash()

        return Applier(read=read, write=write)


@pytest.fixture()
def fake(app: Any) -> Any:
    f = Fake()
    changes.register_applier("fake_v", f.applier)
    yield f
    changes.APPLIERS.pop("fake_v", None)


def _create(ops: list[dict[str, Any]], *, title: str = "Wijziging", key: str | None = None,
            risk: str = "automatisch_veilig", area: str = "torrent") -> dict[str, Any]:
    return changes.create("fake_v", title, ops, risk=risk, dedupe_key=key, impact={"area": area})


def _propose(ops: list[dict[str, Any]], key: str, title: str = "Snelheid", area: str = "usenet") -> dict[str, Any]:
    from mso.features.automation import autopilot

    return autopilot.propose_and_apply("fake_v", title, ops, explanation="test", impact=None, dedupe_key=key,
                                       area=area)


SPEED = [{"path": "speed", "label": "Snelheid", "before": 100, "after": 80}]


class _Holder:
    """Another process applying the change right now: holds its advisory lock on its own connection."""

    def __init__(self, database_url: str, change_id: int) -> None:
        self.change_id = change_id
        self.conn = psycopg.connect(database_url, autocommit=True)
        self.conn.execute("SELECT pg_advisory_lock(%s, %s)", (changes.LOCK_CLASS, change_id))

    def close(self) -> None:  # unlock first: a closed backend releases its locks only asynchronously
        self.conn.execute("SELECT pg_advisory_unlock(%s, %s)", (changes.LOCK_CLASS, self.change_id))
        self.conn.close()


def _hold_lock(database_url: str, change_id: int) -> _Holder:
    return _Holder(database_url, change_id)


# ------------------------------------------------------------------ C12
def test_rollback_disables_recognises_int_and_string_flags() -> None:
    from mso.features.automation.engine import _rollback_disables

    def ch(path: str, before: Any, after: Any) -> dict[str, Any]:
        return {"ops": [{"path": path, "before": before, "observed_before": before, "after": after,
                         "result": "toegepast"}]}

    assert _rollback_disables(ch("servers/x/enable", 0, 1))  # SABnzbd stores enable as an int
    assert _rollback_disables(ch("servers/x/enable", "0", "1"))
    assert _rollback_disables(ch("excluded_file_names_enabled", False, True))
    assert _rollback_disables(ch("anything", False, True))  # booleans are always on/off
    assert not _rollback_disables(ch("servers/x/priority", 0, 1))  # a priority is not a switch
    assert not _rollback_disables(ch("servers/x/enable", 1, 0))  # rolling back a switch-off switches on
    assert not _rollback_disables({"ops": [{"path": "servers/x/enable", "before": 0, "after": 1,
                                            "result": "al_ingesteld"}]})


def test_reenabled_server_detection() -> None:
    from mso.features.automation.evaluators import reenabled_servers

    on = {"target": "sabnzbd", "ops": [{"path": "servers/news.a/enable", "after": 1},
                                       {"path": "servers/b.c/enable", "after": True}]}
    assert reenabled_servers(on) == ["news.a", "b.c"]
    assert reenabled_servers({"target": "sabnzbd", "ops": [{"path": "servers/a/enable", "after": 0}]}) == []
    assert reenabled_servers({"target": "sabnzbd", "ops": [{"path": "servers/a/enable", "after": 1},
                                                           {"path": "servers/a/priority", "after": 2}]}) == []
    assert reenabled_servers({"target": "qbittorrent", "ops": [{"path": "servers/a/enable", "after": 1}]}) == []


# ------------------------------------------------------------------ C25
def test_worse_one_way_change_is_never_proposed_for_rollback(db: Any, fake: Fake, manage_mode: None) -> None:
    from mso.features.automation import EVALUATORS, engine

    sr.put({"automation.auto_rollback": True}, "test")
    ch = _create([{"path": "retry", "label": "Download opnieuw", "before": "mislukt", "after": "opnieuw",
                   "one_way": True}])
    changes.apply(ch["id"], "beheerder")
    db.execute("UPDATE change_sets SET evaluate_after = now() - interval '1 minute' WHERE id=%s", (ch["id"],))
    EVALUATORS["fake_v"] = lambda c: "worse"
    writes = len(fake.writes)
    try:
        assert engine.evaluate_due()["slechter"] == 1
    finally:
        EVALUATORS.pop("fake_v", None)
    c = changes.get(ch["id"])
    ev = c["impact"]["evaluation"]
    assert c["status"] == "applied" and ev["result"] == "slechter" and ev["action"] == "geen"
    assert "niet terug te draaien" in ev["message"]
    assert len(fake.writes) == writes  # no rollback attempt
    assert db.scalar("SELECT count(*) FROM recommendations") == 0  # no 'Terugdraaien aanbevolen'
    view = engine.evaluation_view(c)
    assert view["reversible"] is False


# ------------------------------------------------------------------ C28: rollback
def test_rollback_skips_values_changed_outside_mso(admin: Any, db: Any, fake: Fake, manage_mode: None) -> None:
    from mso.features.automation import overrides

    ch = _create(SPEED)
    changes.apply(ch["id"], "beheerder")
    fake.values["speed"] = 90  # the owner changed it in the source itself
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback")
    assert r.status_code == 409 and "buiten MSO gewijzigd" in r.json()["detail"]
    c = changes.get(ch["id"])
    assert c["status"] == "applied" and fake.values["speed"] == 90  # never overwritten
    op = c["ops"][0]
    assert op["result"] == "overgeslagen" and op["current"] == 90 and op["skip_reason"] == "buiten MSO gewijzigd"
    assert ("speed", 100) not in fake.writes
    ov = overrides.active("fake_v", "speed")
    assert ov is not None and ov["current_value"] == 90 and ov["mso_value"] == 80
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.rollback_skipped'") == 1
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.rolled_back'") == 0


def test_rollback_mixed_drift_and_already_original(fake: Fake, manage_mode: None) -> None:
    ch = _create([{"path": "speed", "label": "Snelheid", "before": 100, "after": 80},
                  {"path": "conns", "label": "Verbindingen", "before": 50, "after": 60}])
    changes.apply(ch["id"], "beheerder")
    fake.values["conns"] = 70  # changed outside MSO
    out = changes.rollback(ch["id"], "beheerder")
    assert out["status"] == "rolled_back" and fake.values == {**fake.values, "speed": 100, "conns": 70}
    res = {o["path"]: o["result"] for o in out["ops"]}
    assert res == {"speed": "teruggedraaid", "conns": "overgeslagen"}
    assert out["ops"][0]["verified_at"] and "Overgeslagen" in out["error"]

    ch2 = _create(SPEED, title="Nog eens")
    changes.apply(ch2["id"], "beheerder")
    fake.values["speed"] = 100  # put back by hand already
    out2 = changes.rollback(ch2["id"], "beheerder")
    assert out2["status"] == "rolled_back" and out2["ops"][0]["rollback_note"] == "was al de oude waarde"


def test_rollback_verification_failure_is_not_reported_as_rolled_back(
    admin: Any, db: Any, fake: Fake, manage_mode: None
) -> None:
    ch = _create(SPEED)
    changes.apply(ch["id"], "beheerder")
    fake.ignore.add("speed")  # the source accepts the write but keeps its value
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback")
    assert r.status_code == 502 and "controle na terugzetten" in r.json()["detail"]
    c = changes.get(ch["id"])
    assert c["status"] == "applied" and c["ops"][0]["result"] == "toegepast"
    assert "controle na terugzetten" in c["ops"][0]["rollback_error"] and c["error"]
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.rolled_back'") == 0
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.rollback_failed'") == 1
    fake.ignore.clear()  # source fixed: a second attempt works
    assert changes.rollback(ch["id"], "beheerder")["status"] == "rolled_back" and fake.values["speed"] == 100


def test_engine_auto_rollback_drift_and_failure(db: Any, fake: Fake) -> None:
    from mso.features.automation import EVALUATORS, engine
    from mso.features.overview.autopilot import waiting

    state.set_state("mode", "autopilot")
    drift = _propose(SPEED, "a")
    fail = _propose([{"path": "conns", "label": "Verbindingen", "before": 50, "after": 60}], "b")
    assert drift["auto_applied"] and fail["auto_applied"]
    fake.values["speed"] = 95  # owner changed it meanwhile
    fake.ignore.add("conns")  # rollback write is ignored → verification fails
    db.execute("UPDATE change_sets SET evaluate_after = now() - interval '1 minute'")
    EVALUATORS["fake_v"] = lambda c: "worse"
    try:
        counts = engine.evaluate_due()
    finally:
        EVALUATORS.pop("fake_v", None)
    assert counts["slechter"] == 2 and counts["rolled_back"] == 0
    ev = changes.get(drift["id"])["impact"]["evaluation"]
    assert ev["action"] == "overgeslagen" and "buiten MSO gewijzigd" in ev["message"]
    assert fake.values["speed"] == 95
    ev2 = changes.get(fail["id"])["impact"]["evaluation"]
    assert ev2["action"] == "terugdraaien_mislukt" and changes.get(fail["id"])["status"] == "applied"
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s",
                      (f"automation.rollback_failed:{fail['id']}",))
    assert rec["risk"] == "toestemming_nodig" and rec["change_id"] is None
    assert rec["evidence"]["change_id"] == fail["id"]
    assert db.scalar("SELECT count(*) FROM recommendations WHERE change_id=%s", (drift["id"],)) == 0
    assert any(i["id"] == f"rec:{rec['id']}" for i in waiting()["items"])  # "Wacht op jou"


# ------------------------------------------------------------------ C28: atomic transitions
def test_apply_and_rollback_refuse_while_another_holds_the_change(
    admin: Any, database_url: str, fake: Fake, manage_mode: None
) -> None:
    ch = _create(SPEED)
    conn = _hold_lock(database_url, ch["id"])
    try:
        with pytest.raises(changes.ChangeBusy):
            changes.apply(ch["id"], "beheerder")
        r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
        assert r.status_code == 409 and "al toegepast of teruggedraaid" in r.json()["detail"]
    finally:
        conn.close()
    assert fake.values["speed"] == 100 and changes.get(ch["id"])["status"] == "preview"
    changes.apply(ch["id"], "beheerder")
    conn = _hold_lock(database_url, ch["id"])
    try:
        with pytest.raises(changes.ChangeBusy):
            changes.rollback(ch["id"], "beheerder")
    finally:
        conn.close()
    assert changes.rollback(ch["id"], "beheerder")["status"] == "rolled_back"


def test_status_transitions_are_guarded(db: Any, fake: Fake, manage_mode: None) -> None:
    ch = _create(SPEED, key="dup")
    db.execute("UPDATE change_sets SET status='applying' WHERE id=%s", (ch["id"],))
    again = _create(SPEED, key="dup")
    assert again["id"] == ch["id"]  # 'applying' is part of the open dedupe set
    for fn in (changes.discard, changes.approve):
        with pytest.raises(HTTPException) as exc:
            fn(ch["id"], "beheerder")
        assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        changes.apply(ch["id"], "beheerder")
    assert changes.get(ch["id"])["status"] == "applying"


# ------------------------------------------------------------------ C36: crash-safe apply
TWO = [{"path": "speed", "label": "Snelheid", "before": 100, "after": 80},
       {"path": "conns", "label": "Verbindingen", "before": 50, "after": 60}]


def test_crash_mid_apply_is_journaled_and_reconciled(db: Any, database_url: str, fake: Fake,
                                                     manage_mode: None) -> None:
    from mso.features.overview.autopilot import waiting

    ch = _create(TWO)
    fake.crash_after = "speed"
    with pytest.raises(Crash):
        changes.apply(ch["id"], "beheerder")
    row = changes.get(ch["id"])
    assert row["status"] == "applying" and row["apply_started_at"] is not None
    assert [o["observed_before"] for o in row["ops"]] == [100, 50]  # journaled before the first write
    assert fake.values["speed"] == 80 and fake.values["conns"] == 50

    conn = _hold_lock(database_url, ch["id"])  # an apply that is still running is never touched
    try:
        out = changes.reconcile_interrupted()
        assert out["running"] == 1 and changes.get(ch["id"])["status"] == "applying"
    finally:
        conn.close()

    fake.crash_after = None
    out = changes.reconcile_interrupted()
    assert out["outcomes"] == {str(ch["id"]): "teruggezet"}
    c = changes.get(ch["id"])
    assert c["status"] == "failed" and fake.values["speed"] == 100 and fake.values["conns"] == 50
    assert {o["path"]: o["result"] for o in c["ops"]} == {"speed": "teruggedraaid", "conns": "niet_uitgevoerd"}
    assert "Onderbroken" in c["error"] and "teruggezet" in c["error"]
    line = db.fetchone("SELECT * FROM audit_log WHERE action='change.interrupted'")
    assert line["change_id"] == ch["id"] and line["detail"]["outcome"] == "teruggezet"
    # a person's apply did not go through: one "Wacht op jou" item
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s", (f"change.interrupted:{ch['id']}",))
    assert rec is not None and rec["change_id"] is None
    assert any(i["id"] == f"rec:{rec['id']}" for i in waiting()["items"])
    assert changes.reconcile_interrupted()["checked"] == 0  # done


def test_crash_after_last_write_is_finished(db: Any, fake: Fake, manage_mode: None) -> None:
    ch = _create(TWO)
    fake.crash_after = "conns"
    with pytest.raises(Crash):
        changes.apply(ch["id"], "beheerder")
    assert changes.get(ch["id"])["ops"][0]["result"] == "toegepast"  # stored right after its write
    out = changes.reconcile_interrupted()
    assert out["outcomes"] == {str(ch["id"]): "afgerond"}
    c = changes.get(ch["id"])
    assert c["status"] == "applied" and c["applied_at"] is not None and c["applied_by"] == "beheerder"
    assert all(o["result"] == "toegepast" for o in c["ops"])
    applied = db.fetchone("SELECT detail FROM audit_log WHERE action='change.applied' AND change_id=%s", (ch["id"],))
    assert applied["detail"]["interrupted"] is True
    assert db.scalar("SELECT count(*) FROM recommendations") == 0
    fake.crash_after = None
    assert changes.rollback(ch["id"], "beheerder")["status"] == "rolled_back"  # observed_before kept: reversible
    assert fake.values["speed"] == 100 and fake.values["conns"] == 50


def test_reconcile_waits_for_unreachable_source_then_reports(db: Any, fake: Fake, manage_mode: None) -> None:
    ch = _create(TWO)
    fake.crash_after = "speed"
    with pytest.raises(Crash):
        changes.apply(ch["id"], "beheerder")
    fake.read_fail = True  # e.g. SABnzbd restarts together with MSO
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "wacht"}
    assert changes.get(ch["id"])["status"] == "applying"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.interrupted'") == 0
    db.execute("UPDATE change_sets SET apply_started_at = now() - interval '2 hours' WHERE id=%s", (ch["id"],))
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "onduidelijk"}
    c = changes.get(ch["id"])
    assert c["status"] == "failed" and "niet te controleren" in c["error"] and "geheim999" not in c["error"]
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key=%s",
                     (f"change.interrupted:{ch['id']}",)) == 1


def test_reconcile_never_writes_in_read_only(db: Any, fake: Fake, manage_mode: None) -> None:
    ch = _create(TWO)
    fake.crash_after = "speed"
    with pytest.raises(Crash):
        changes.apply(ch["id"], "beheerder")
    fake.crash_after = None
    state.set_state("mode", "read_only")
    writes = len(fake.writes)
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "wacht_op_beheermodus"}
    assert len(fake.writes) == writes and changes.get(ch["id"])["status"] == "applying"
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s", (f"change.interrupted:{ch['id']}",))
    assert "beheermodus" in rec["title"]
    state.set_state("mode", "manage")
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "teruggezet"}
    assert fake.values["speed"] == 100
    rec = db.fetchone("SELECT * FROM recommendations WHERE id=%s", (rec["id"],))
    assert rec["title"].startswith("Wijziging onderbroken") and rec["status"] == "open"


def test_interrupted_automatic_change_restored_quietly(db: Any, fake: Fake) -> None:
    state.set_state("mode", "autopilot")
    ch = _create(TWO)
    fake.crash_after = "speed"
    with pytest.raises(Crash):
        changes.apply(ch["id"], "automatisering", automatic=True)
    fake.crash_after = None
    assert changes.get(ch["id"])["applied_auto"] is True
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "teruggezet"}
    assert db.scalar("SELECT count(*) FROM recommendations") == 0  # MSO proposes it again by itself


def test_reconcile_job_is_scheduled_and_runs(db: Any, worker: Any, fake: Fake, manage_mode: None) -> None:
    from mso import jobs
    from mso.features.automation.api import SCHEDULE_LABELS

    assert "changes.reconcile" in jobs.HANDLERS and "changes.reconcile" in SCHEDULE_LABELS
    assert any(s.kind == "changes.reconcile" and s.every_s == 120 for s in jobs.SCHEDULES)
    ch = _create(TWO)
    fake.crash_after = "conns"
    with pytest.raises(Crash):
        changes.apply(ch["id"], "beheerder")
    jobs.enqueue("changes.reconcile", {}, idempotency_key="test-reconcile")
    worker.drain()
    assert changes.get(ch["id"])["status"] == "applied"


# ------------------------------------------------------------------ C26: cooldown, cap, noodrem
def test_failed_automatic_apply_cools_down_then_parks(db: Any, fake: Fake) -> None:
    from mso.features.automation import autopilot, engine
    from mso.features.overview.autopilot import waiting

    state.set_state("mode", "autopilot")
    fake.fail.add(("speed", 80))
    first = _propose(SPEED, "spd")
    assert first["auto_applied"] is False and first["status"] == "failed"
    row = db.fetchone("SELECT * FROM automation_failures")
    assert row["failures"] == 1 and row["key"] == "spd" and not row["parked"]
    assert timedelta(minutes=55) < row["next_attempt_at"] - datetime.now(UTC) <= timedelta(hours=1)

    writes = len(fake.writes)
    second = _propose(SPEED, "spd")  # the feature proposes again 5 minutes later
    assert second["id"] != first["id"] and second["status"] == "preview"
    assert second["auto_reason"].startswith("afkoeling na 1 mislukte automatische poging")
    res = engine.apply_safe()
    assert res["cooling"] == [second["id"]] and res["applied"] == [] and len(fake.writes) == writes
    assert autopilot.why_not(second, "usenet") is None  # not a "Wacht op jou" item: MSO retries by itself

    db.execute("UPDATE automation_failures SET next_attempt_at = now() - interval '1 minute'")
    assert engine.apply_safe()["failed"] == [second["id"]]
    row = db.fetchone("SELECT * FROM automation_failures")
    assert row["failures"] == 2 and timedelta(hours=3) < row["next_attempt_at"] - datetime.now(UTC)

    third = _propose(SPEED, "spd")
    db.execute("UPDATE automation_failures SET next_attempt_at = now() - interval '1 minute'")
    assert engine.apply_safe()["failed"] == [third["id"]]
    row = db.fetchone("SELECT * FROM automation_failures")
    assert row["failures"] == 3 and row["parked"] is True
    assert timedelta(hours=23) < row["next_attempt_at"] - datetime.now(UTC) <= timedelta(hours=24)
    rec = db.fetchone("SELECT * FROM recommendations WHERE id=%s", (row["rec_id"],))
    assert "3× niet automatisch toepassen" in rec["title"] and rec["status"] == "open"
    assert rec["risk"] == "toestemming_nodig" and rec["change_id"] is None
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='automation.parked'") == 1
    ids = [i["id"] for i in waiting()["items"]]
    assert ids.count(f"rec:{rec['id']}") == 1  # exactly one "Wacht op jou" item
    fourth = _propose(SPEED, "spd")
    assert "geparkeerd" in fourth["auto_reason"] and fourth["id"] not in ids  # the preview itself is not listed

    # a changed preview (other value) starts over and succeeds; the parked item is resolved
    changes.discard(fourth["id"], "beheerder")
    ok = _propose([{"path": "speed", "label": "Snelheid", "before": 100, "after": 70}], "spd")
    assert ok["auto_applied"] is True and fake.values["speed"] == 70
    assert db.scalar("SELECT count(*) FROM automation_failures") == 0
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec["id"],)) == "resolved"


def test_manual_success_clears_a_parked_change(db: Any, fake: Fake) -> None:
    from mso.features.automation import autopilot, engine

    state.set_state("mode", "autopilot")
    fake.fail.add(("speed", 80))
    _propose(SPEED, "spd")
    for _ in range(2):
        db.execute("UPDATE automation_failures SET next_attempt_at = now() - interval '1 minute'")
        _propose(SPEED, "spd")
    assert db.scalar("SELECT parked FROM automation_failures") is True
    fake.fail.clear()
    pending = _propose(SPEED, "spd")
    changes.apply(pending["id"], "beheerder")  # the owner applies it by hand
    engine.apply_safe()  # tidies the ledger
    assert db.scalar("SELECT count(*) FROM automation_failures") == 0
    assert db.scalar("SELECT count(*) FROM recommendations WHERE status='open'") == 0
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='automation.unparked'") == 1
    assert autopilot.cooldowns() == []


def test_failed_attempts_count_for_the_hourly_cap(db: Any, fake: Fake) -> None:
    state.set_state("mode", "autopilot")
    sr.put({"automation.max_per_hour": 2}, "test")
    fake.fail.add(("speed", 80))
    assert _propose(SPEED, "a")["auto_applied"] is False  # failed attempt 1
    assert _propose([{"path": "conns", "before": 50, "after": 60}], "b")["auto_applied"] is True
    capped = _propose([{"path": "srv/enable", "before": 0, "after": 1}], "c")
    assert capped["auto_applied"] is False and "limiet van 2" in capped["auto_reason"]


def test_noodrem_after_failures_and_hervatten(admin: Any, operator: Any, viewer: Any, db: Any, fake: Fake) -> None:
    from mso.features.automation import autopilot, engine
    from mso.features.overview.autopilot import waiting

    state.set_state("mode", "autopilot")
    for i in range(5):
        fake.fail.add(("conns", 100 + i))
        res = _propose([{"path": "conns", "before": 50, "after": 100 + i}], f"k{i}")
        assert res["auto_applied"] is False
    hold = state.hold()
    assert hold is not None and hold["failures"] == 5 and "konden niet worden toegepast" in hold["reason"]
    assert state.automation_allowed() is False and state.autopilot() is False and state.mode() == "autopilot"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='autopilot.self_paused'") == 1
    assert engine.apply_safe()["skipped"] == "not_allowed"
    blocked = _propose([{"path": "speed", "before": 100, "after": 90}], "na")
    assert blocked["auto_applied"] is False and blocked["auto_reason"] == autopilot.HOLD_REASON
    assert fake.values["speed"] == 100
    rec_id = hold["rec_id"]
    assert any(i["id"] == f"rec:{rec_id}" for i in waiting()["items"])
    s = viewer.get("/api/v1/automation/safety").json()
    assert s["hold"]["active"] is True and s["brake"]["failures"] == 5 and s["brake"]["max_failures"] == 5
    assert viewer.get("/api/v1/automation/status").json()["hold"]["active"] is True
    # a person can still apply by hand during the hold
    assert admin.post(f"/api/v1/changes/{blocked['id']}/apply").status_code == 200

    assert viewer.post("/api/v1/automation/hold/resume").status_code == 403
    r = operator.post("/api/v1/automation/hold/resume")
    assert r.status_code == 200 and r.json()["hold"] is None and r.json()["brake"]["failures"] == 0
    assert state.automation_allowed() is True and state.autopilot() is True
    assert db.scalar("SELECT status FROM recommendations WHERE id=%s", (rec_id,)) == "resolved"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='autopilot.resumed'") == 1
    assert operator.post("/api/v1/automation/hold/resume").status_code == 409
    assert autopilot.check_brake() is None  # counters start again after Hervatten


def test_noodrem_after_three_automatic_rollbacks(db: Any, fake: Fake) -> None:
    from mso.features.automation import EVALUATORS, engine

    state.set_state("mode", "autopilot")
    ids = []
    for i, path in enumerate(("speed", "conns", "srv/enable", "retry")):
        before = fake.values[path]
        after = {"speed": 70, "conns": 70, "srv/enable": 2, "retry": "x"}[path]
        res = _propose([{"path": path, "before": before, "after": after}], f"r{i}")
        assert res["auto_applied"] is True
        ids.append(res["id"])
    db.execute("UPDATE change_sets SET evaluate_after = now() - interval '1 minute'")
    EVALUATORS["fake_v"] = lambda c: "worse"
    try:
        counts = engine.evaluate_due()
    finally:
        EVALUATORS.pop("fake_v", None)
    assert counts["rolled_back"] == 3 and state.hold() is not None
    assert "slechter" in state.hold()["reason"]
    last = changes.get(ids[3])
    assert last["status"] == "applied" and last["impact"]["evaluation"]["action"] == "voorgesteld"


def test_undo_autopilot_window(admin: Any, operator: Any, viewer: Any, db: Any, fake: Fake) -> None:
    from mso.features.automation import autopilot, overrides

    state.set_state("mode", "autopilot")
    a = _propose(SPEED, "a")
    b = _propose([{"path": "srv/enable", "label": "Server", "before": 0, "after": 1}], "b")
    one_way = _propose([{"path": "retry", "label": "Opnieuw", "before": "mislukt", "after": "opnieuw",
                         "one_way": True}], "c")
    manual = _create([{"path": "conns", "before": 50, "after": 55}])
    changes.apply(manual["id"], "beheerder")
    old = _propose([{"path": "conns", "label": "Verbindingen", "before": 55, "after": 58}], "d")
    db.execute("UPDATE change_sets SET applied_at = now() - interval '3 days' WHERE id=%s", (old["id"],))

    day = viewer.get("/api/v1/automation/undo?window=24h").json()
    assert [i["id"] for i in day["items"]] == [b["id"], a["id"]]  # newest first; manual ones not listed
    assert [i["id"] for i in day["not_undoable"]] == [one_way["id"]]
    assert day["items"][1]["ops"][0] == {"label": "Snelheid", "path": "speed", "now": 80, "back_to": 100,
                                         "switches_on": False}
    week = viewer.get("/api/v1/automation/undo?window=7d").json()
    assert {i["id"] for i in week["items"]} == {a["id"], b["id"], old["id"]}
    assert viewer.get("/api/v1/automation/undo?window=1y").status_code == 422
    assert viewer.post("/api/v1/automation/undo", json={"window": "24h"}).status_code == 403
    state.set_state("mode", "read_only")
    assert operator.post("/api/v1/automation/undo", json={"window": "24h"}).status_code == 409
    state.set_state("mode", "autopilot")

    fake.values["srv/enable"] = 0  # already put back outside MSO → counts as rolled back (was already old)
    r = operator.post("/api/v1/automation/undo", json={"window": "24h"})
    assert r.status_code == 200
    assert r.json()["counts"] == {"teruggedraaid": 2, "overgeslagen": 0, "mislukt": 0}
    assert fake.values["speed"] == 100 and changes.get(a["id"])["status"] == "rolled_back"
    assert changes.get(old["id"])["status"] == "applied" and fake.values["conns"] == 58  # outside the window
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.rolled_back'") == 2
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='automation.undo'") == 1
    # what was put back is the owner's choice: MSO does not re-apply it automatically
    assert overrides.active("fake_v", "speed") is not None
    again = _propose(SPEED, "a2")
    assert again["auto_applied"] is False and again["auto_reason"] == autopilot.OVERRIDE_REASON
    assert fake.values["speed"] == 100
    s = viewer.get("/api/v1/automation/safety").json()
    assert s["undo"] == {"24h": 0, "7d": 1}


def test_owner_override_blocks_automatic_reapply(db: Any, fake: Fake) -> None:
    from mso.features.automation import autopilot, overrides
    from mso.features.overview.autopilot import waiting

    state.set_state("mode", "autopilot")
    overrides.record("fake_v", "speed", mso_value=80, current_value=100, change_id=None, days=30)
    ch = _propose(SPEED, "spd")
    assert ch["auto_applied"] is False and ch["status"] == "preview"
    assert ch["auto_reason"] == "Je hebt dit zelf teruggezet; MSO past het niet opnieuw automatisch toe"
    assert fake.values["speed"] == 100
    item = next(i for i in waiting()["items"] if i["id"] == ch["id"])
    assert item["reason"].startswith("Je hebt dit zelf teruggezet")
    other = _propose([{"path": "conns", "before": 50, "after": 60}], "c")  # other paths are not affected
    assert other["auto_applied"] is True
    overrides.clear("fake_v", "speed")
    assert autopilot.why_not(changes.get(ch["id"]), "usenet") is None


# ------------------------------------------------------------------ over real HTTP: the SABnzbd mock
def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    assert admin.put("/api/v1/settings", json={"values": {"automation.max_per_hour": 60}}).status_code == 200
    return mock_app.state.sab


def _srv(sab: Any, name: str) -> dict[str, Any]:
    return next(s for s in sab.servers if s["name"] == name)


NH, BLOCK, EWEKA = "news.newshosting.test", "block.example.test", "news.eweka.test"


def test_sab_crash_mid_apply_reconciled_over_http(app: Any, db: Any, admin: Any, serve: Any) -> None:
    sab = _start(serve, admin)
    state.set_state("mode", "manage")
    ch = changes.create("sabnzbd", "Prioriteiten", [
        {"path": f"servers/{NH}/priority", "label": "Newshosting: prioriteit", "before": 1, "after": 3},
        {"path": f"servers/{BLOCK}/priority", "label": "Blok: prioriteit", "before": 0, "after": 2},
    ], risk="automatisch_veilig")
    real = changes.APPLIERS["sabnzbd"]

    def crashing() -> Applier:
        a = real()

        def write(path: str, value: Any) -> None:
            a.write(path, value)
            raise Crash()

        return Applier(read=a.read, write=write, validate=a.validate)

    changes.APPLIERS["sabnzbd"] = crashing
    try:
        with pytest.raises(Crash):
            changes.apply(ch["id"], "beheerder")
    finally:
        changes.APPLIERS["sabnzbd"] = real
    assert _srv(sab, NH)["priority"] == 3 and _srv(sab, BLOCK)["priority"] == 0  # half applied in SABnzbd
    assert changes.get(ch["id"])["status"] == "applying"
    out = changes.reconcile_interrupted()  # reads SABnzbd's config over HTTP and writes the old value back
    assert out["outcomes"] == {str(ch["id"]): "teruggezet"}
    assert _srv(sab, NH)["priority"] == 1 and _srv(sab, BLOCK)["priority"] == 0
    c = changes.get(ch["id"])
    assert c["status"] == "failed" and c["ops"][0]["result"] == "teruggedraaid" and c["ops"][0]["verified_at"]


def test_sab_rollback_drift_and_failed_verification_over_http(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from msomocks import sabnzbd as mock

    sab = _start(serve, admin)
    state.set_state("mode", "manage")
    ch = changes.create("sabnzbd", "Newshosting lager", [
        {"path": f"servers/{NH}/priority", "label": "Newshosting: prioriteit", "before": 1, "after": 3}],
        risk="automatisch_veilig")
    assert admin.post(f"/api/v1/changes/{ch['id']}/apply").json()["status"] == "applied"
    _srv(sab, NH)["priority"] = 5  # the owner changed it in SABnzbd itself
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback")
    assert r.status_code == 409 and "buiten MSO gewijzigd" in r.json()["detail"]
    assert _srv(sab, NH)["priority"] == 5 and changes.get(ch["id"])["ops"][0]["current"] == 5

    ch2 = changes.create("sabnzbd", "Blok hoger", [
        {"path": f"servers/{BLOCK}/priority", "label": "Blok: prioriteit", "before": 0, "after": 4}],
        risk="automatisch_veilig")
    assert admin.post(f"/api/v1/changes/{ch2['id']}/apply").json()["status"] == "applied"
    # same SABnzbd config behind a mock that accepts set_config without changing anything
    deaf = mock.create_app(api_key=SAB_KEY, ignore_set_config="1")
    deaf.state.sab.servers = [dict(s) for s in sab.servers]
    assert admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(deaf)}).status_code == 200
    r = admin.post(f"/api/v1/changes/{ch2['id']}/rollback")
    assert r.status_code == 502 and "controle na terugzetten" in r.json()["detail"]
    c = changes.get(ch2["id"])
    assert c["status"] == "applied" and c["ops"][0]["result"] == "toegepast"
    assert _srv(deaf.state.sab, BLOCK)["priority"] == 4


def _evaluate_now(db: Any, change_id: int) -> dict[str, Any]:
    from mso.features.automation import engine

    db.execute("UPDATE change_sets SET evaluate_after = now() - interval '1 minute' WHERE id=%s", (change_id,))
    engine.evaluate_due()
    return changes.get(change_id)


def test_reenabled_server_judged_on_own_status_over_http(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from mso.features.usenet import serverstatus

    sab = _start(serve, admin, server_errors="report")  # newshosting: 502 Authentication Failed
    _srv(sab, NH)["enable"] = 0
    _srv(sab, EWEKA)["enable"] = 0
    state.set_state("mode", "autopilot")  # automatic rollback is always on in Autopilot
    bad = changes.create("sabnzbd", "Newshosting opnieuw proberen", [
        {"path": f"servers/{NH}/enable", "label": "Newshosting: inschakelen", "before": 0, "after": 1}],
        risk="automatisch_veilig", evaluate_after_h=24)
    good = changes.create("sabnzbd", "Eweka opnieuw proberen", [
        {"path": f"servers/{EWEKA}/enable", "label": "Eweka: inschakelen", "before": 0, "after": 1}],
        risk="automatisch_veilig", evaluate_after_h=24)
    for ch in (bad, good):
        assert changes.apply(ch["id"], "automatisering", automatic=True)["status"] == "applied"
    db.execute("UPDATE change_sets SET applied_at = now() - interval '1 hour'")
    serverstatus.refresh()  # SABnzbd's own per-server status, over HTTP
    # lots of failed downloads overall: the global ratio is NOT what decides here
    for i in range(30):
        db.execute("INSERT INTO download_outcomes(protocol, client, client_ref, title, status, finished_at) "
                   "VALUES ('usenet','sabnzbd',%s,%s,'failed',now() - interval '10 minutes')", (f"f{i}", f"t{i}"))

    c = _evaluate_now(db, bad["id"])
    ev = c["impact"]["evaluation"]
    assert ev["result"] == "slechter" and ev["action"] == "geen" and "serverbewaking" in ev["message"]
    assert ev["measures"][0]["key"] == f"server_status:{NH}" and ev["measures"][0]["unit"] == "status"
    assert "Authentication Failed" in ev["measures"][0]["why"]
    assert c["status"] == "applied" and _srv(sab, NH)["enable"] == 1  # never switched off by the engine
    assert db.scalar("SELECT count(*) FROM recommendations WHERE change_id=%s", (bad["id"],)) == 0

    c = _evaluate_now(db, good["id"])
    ev = c["impact"]["evaluation"]
    assert ev["result"] == "beter" and ev["evaluator"] == "sabnzbd" and c["status"] == "applied"
    assert all(m["key"].startswith("server_status:") for m in ev["measures"])


def test_undo_autopilot_puts_servers_back_and_respects_it_over_http(
    app: Any, db: Any, admin: Any, operator: Any, viewer: Any, serve: Any
) -> None:
    from mso.features.automation import autopilot
    from mso.features.usenet import autoheal, serverstatus

    xs = "reader.xsnews.test"
    sab = _start(serve, admin, server_errors="field")
    state.set_state("mode", "autopilot")
    serverstatus.refresh()
    assert autoheal.run()["servers"]["switched_off"] == [xs]
    assert _srv(sab, xs)["enable"] == 0
    preview = viewer.get("/api/v1/automation/undo?window=24h").json()
    assert len(preview["items"]) == 1 and preview["items"][0]["ops"][0]["switches_on"] is True
    r = operator.post("/api/v1/automation/undo", json={"window": "24h"})
    assert r.status_code == 200 and r.json()["counts"]["teruggedraaid"] == 1
    assert _srv(sab, xs)["enable"] == 1  # back on in SABnzbd, verified over HTTP
    # Autopilot does not fight the owner's choice: the server watch sees it on again (probe) ...
    serverstatus.refresh()
    assert xs not in autoheal.run()["servers"]["switched_off"]
    # ... and even when SABnzbd reports the error again, MSO only proposes switching it off
    sab.set_server_error(xs, f"Server {xs} reported: 482 Account expired.", at=int(time.time()) + 60)
    serverstatus.refresh()
    out = autoheal.run()["servers"]
    assert xs in out["probe_failed"] and xs not in out["switched_off"] and _srv(sab, xs)["enable"] == 1
    off = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status='preview'", (f"usenet.auto_off:{xs}",))
    assert off is not None and autopilot.why_not(dict(off), "usenet") == autopilot.OVERRIDE_REASON
    assert any(p.get("reason") == autopilot.OVERRIDE_REASON for p in out["pending"])
