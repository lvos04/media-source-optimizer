"""Autopilot helpers for other slices: "propose and (if allowed) apply in one go".

Usage from any feature module::

    from mso.features.automation import autopilot

    if autopilot.enabled("usenet"):
        ...  # e.g. retry a failed download yourself (direct action, audited)

    ch = autopilot.propose_and_apply(
        "sabnzbd", "Server 'xsnews' uitschakelen (abonnement verlopen)",
        [{"path": "servers/xsnews/enable", "label": "xsnews actief", "before": True, "after": False,
          "destructive": True}],
        explanation="…", impact={"Reden": "482 expired"}, dedupe_key="usenet.server_off:xsnews",
        area="usenet")
    ch["auto_applied"]  # True when MSO applied it right away

Rules (enforced again by :func:`mso.changes.apply`):
- Only in beheermodus/Autopilot without pause; in alleen-lezen nothing is ever written.
- An area is "on" when Autopilot is active, or when its own toggle ``automation.<area>.auto_apply_safe`` is on.
- ``destructive`` ops (switching off, reversible) are only applied automatically in Autopilot.
- ``irreversible`` ops (deleting) are NEVER applied automatically: such a change is created as
  ``toestemming_nodig`` and waits for one approval.
- The owner's own override (:mod:`overrides`: a value MSO set that the owner changed back outside MSO) is never
  re-applied automatically; the change stays a proposal.
- The global cap ``automation.max_per_hour`` applies to every automatic attempt, failed ones included; over the cap
  the change stays a proposal and the periodic ``automation.apply_safe`` job picks it up later.
- Cooldown (1.6.0): an automatic apply that fails waits 1 h, then 4 h; after 3 failures the change is *parked*:
  one "Wacht op jou" item and at most one quiet attempt per 24 h. A changed preview (other before/after) starts
  over; a successful apply (also by a person) clears it.
- Noodrem (1.6.0): ``automation.brake_rollbacks`` automatic rollbacks or ``automation.brake_failures`` failed
  automatic applies within 24 h put every automatic write on hold (``state.hold()``) until "Hervatten".
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import Database, get_db
from mso.features.automation import AREAS, overrides

ACTOR = "automatisering"
WAITS = (timedelta(hours=1), timedelta(hours=4), timedelta(hours=24))
PARK_AFTER = 3
OVERRIDE_DAYS_DEFAULT = 30
OVERRIDE_REASON = "Je hebt dit zelf teruggezet; MSO past het niet opnieuw automatisch toe"
HOLD_REASON = ("Autopilot staat stil na te veel mislukte of teruggedraaide wijzigingen (noodrem); hervat via "
               "Automatisering")
# sets a person starts (Voetafdruk → Alles terugzetten: ``footprint.RESTORE_SOURCE``): never for the automatic round,
# also not in the moment between creating and approving them, nor when a request dies in between
PERSON_ONLY_SOURCES = ("footprint:restore",)
PERSON_ONLY_REASON = "terugzetten door een persoon; nooit automatisch"


def area_toggle(area: str | None) -> bool:
    if area not in AREAS:
        return False
    return bool(sr.get(f"automation.{area}.auto_apply_safe"))


def area_on(area: str | None) -> bool:
    """Area switched on for automatic application (Autopilot, or the area's own toggle). Ignores mode/pause."""
    return state.autopilot() or area_toggle(area)


def slice_on(ch: dict[str, Any]) -> bool:
    """A slice's own switch for unattended apply of its own changes, besides the area toggles: qBittorrent tuning
    steps follow ``torrent.autotune_enabled`` (beheermodus). Ignores mode/pause, like :func:`area_on`."""
    if ch.get("source") == "tuning" and ch.get("target") == "qbittorrent" and "torrent.autotune_enabled" in sr.REGISTRY:
        try:
            return bool(sr.get("torrent.autotune_enabled"))
        except Exception:
            return False
    return False


def enabled(area: str) -> bool:
    """May MSO act on its own in ``area`` right now? True when not read-only, not paused, not held by the noodrem
    and either Autopilot is active or the area's 'automatisch veilig toepassen' toggle is on."""
    return state.automation_allowed() and area_on(area)


def _count(db: Database, results: tuple[str, ...], since: datetime | str = "1 hour") -> int:
    if isinstance(since, str):
        sql = "ts > now() - %s::interval"
    else:
        sql = "ts > %s"
    return int(db.scalar(f"SELECT count(*) FROM automation_log WHERE kind='apply' AND result = ANY(%s) AND {sql}",
                         (list(results), since)) or 0)


def applied_last_hour(db: Database | None = None) -> int:
    """Successful automatic applies in the last hour (shown in the UI)."""
    return _count(db or get_db(), ("applied",))


def attempts_last_hour(db: Database | None = None) -> int:
    """Automatic attempts in the last hour, failed ones included: what ``automation.max_per_hour`` limits."""
    return _count(db or get_db(), ("applied", "failed"))


def budget_left(db: Database | None = None) -> int:
    return int(sr.get("automation.max_per_hour")) - attempts_last_hour(db)


def override_days() -> int:
    """How long an owner override blocks automatic re-application (the footprint setting when it exists)."""
    key = "automation.override_days"
    if key in sr.REGISTRY:
        try:
            return max(1, int(sr.get(key)))
        except Exception:
            return OVERRIDE_DAYS_DEFAULT
    return OVERRIDE_DAYS_DEFAULT


def why_not(ch: dict[str, Any], area: str | None) -> str | None:
    """Reason this change may not be applied automatically now, or None when it may. (Timing — the hourly cap and
    the cooldown after failures — is checked separately: see :func:`cooldown` and :func:`budget_left`.)"""
    ops = ch.get("ops") or []
    if ch.get("status") not in ("preview",):
        return f"status '{ch.get('status')}'"
    if ch.get("source") in PERSON_ONLY_SOURCES:
        return PERSON_ONLY_REASON
    if any(op.get("irreversible") for op in ops):
        return "verwijderen gebeurt nooit automatisch; één goedkeuring van jou nodig"
    if ch.get("risk") != "automatisch_veilig":
        return "vereist goedkeuring"
    if not state.automation_allowed():
        if state.mode() == "read_only":
            return "alleen-lezen modus"
        if state.paused():
            return "automatisering gepauzeerd"
        if state.hold():
            return HOLD_REASON
        return "automatisering gepauzeerd"
    if any(op.get("destructive") for op in ops) and not state.autopilot():
        return "uitschakelen gebeurt alleen automatisch in Autopilot"
    if not area_on(area) and not slice_on(ch):
        return "automatisch toepassen staat uit voor dit onderdeel"
    if ch.get("target") and overrides.active_for_ops(str(ch["target"]), ops):
        return OVERRIDE_REASON
    return None


def log(kind: str, result: str, message: str, change_id: int | None = None, area: str | None = None,
        db: Database | None = None) -> None:
    from mso.security import redact

    (db or get_db()).execute(
        "INSERT INTO automation_log(kind,change_id,area,result,message) VALUES (%s,%s,%s,%s,%s)",
        (kind, change_id, area, result, redact(message)[:1000]),
    )


# ------------------------------------------------------------------ cooldown after failed automatic applies
def fingerprint(ch: dict[str, Any]) -> str:
    """Target + every op's path/before/after: the same preview gives the same fingerprint."""
    ops = [[op.get("path"), op.get("before"), op.get("after")] for op in ch.get("ops") or []]
    raw = json.dumps([ch.get("target"), ops], default=str, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def failure_key(ch: dict[str, Any]) -> str:
    return str(ch.get("dedupe_key") or f"fp:{fingerprint(ch)}")


def _wait_text(until: datetime) -> str:
    minutes = max(1, int((until - datetime.now(UTC)).total_seconds() // 60) + 1)
    if minutes < 60:
        return f"over {minutes} min"
    hours = round(minutes / 60)
    return f"over {hours} uur"


def _cooldown_view(row: dict[str, Any]) -> dict[str, Any]:
    n = int(row["failures"])
    if row["parked"]:
        reason = (f"geparkeerd na {n} mislukte automatische pogingen; MSO probeert het hooguit één keer per dag "
                  f"(volgende {_wait_text(row['next_attempt_at'])}). Pas het zelf toe of verwerp het")
    else:
        reason = (f"afkoeling na {n} mislukte automatische poging{'en' if n != 1 else ''}; MSO probeert het "
                  f"{_wait_text(row['next_attempt_at'])} opnieuw")
    return {"key": row["key"], "title": row["title"], "target": row["target"], "area": row.get("area"),
            "change_id": row.get("change_id"), "failures": n, "parked": bool(row["parked"]),
            "last_error": row.get("last_error"), "last_failed_at": row.get("last_failed_at"),
            "next_attempt_at": row["next_attempt_at"], "reason": reason}


def _resolve_rec(rec_id: int | None, db: Database) -> None:
    if rec_id:
        db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE id=%s AND status='open'",
                   (rec_id,))


def _clear(row: dict[str, Any], db: Database, *, why: str, actor: str = ACTOR) -> None:
    db.execute("DELETE FROM automation_failures WHERE key=%s", (row["key"],))
    _resolve_rec(row.get("rec_id"), db)
    if row.get("parked"):
        audit(actor, "automation.unparked", f"Weer automatisch toe te passen ({why}): {row['title']}",
              target=row["target"], change_id=row.get("change_id"), detail={"key": row["key"], "why": why})


def cooldown(ch: dict[str, Any], db: Database | None = None) -> dict[str, Any] | None:
    """The active wait after failed automatic attempts of this change ``{reason, next_attempt_at, failures,
    parked, …}``, or None when an automatic attempt is allowed now. A changed preview resets the counter."""
    db = db or get_db()
    row = db.fetchone("SELECT * FROM automation_failures WHERE key=%s", (failure_key(ch),))
    if row is None:
        return None
    if row["fingerprint"] != fingerprint(ch):
        _clear(dict(row), db, why="gewijzigd voorstel")
        return None
    if row["next_attempt_at"] <= datetime.now(UTC):
        return None
    return _cooldown_view(dict(row))


def note_failure(ch: dict[str, Any], error: str, area: str | None, db: Database | None = None) -> dict[str, Any]:
    """Record a failed automatic apply: wait 1 h, 4 h; from the 3rd failure on parked (24 h, one inbox item)."""
    from mso.domain.outcomes import recommend
    from mso.security import redact

    db = db or get_db()
    key, fp = failure_key(ch), fingerprint(ch)
    row = db.fetchone("SELECT * FROM automation_failures WHERE key=%s", (key,))
    same = row is not None and row["fingerprint"] == fp
    failures = int(row["failures"]) + 1 if same else 1
    parked = failures >= PARK_AFTER
    nxt = datetime.now(UTC) + WAITS[min(failures, len(WAITS)) - 1]
    err = redact(str(error))[:500]
    if row is not None and not same:
        _resolve_rec(row.get("rec_id"), db)
    rec_id = row.get("rec_id") if same and row is not None else None
    if parked and not (same and row is not None and row["parked"]):
        rec_id = recommend(
            area or "automation",
            f"MSO kon “{ch['title']}” {failures}× niet automatisch toepassen",
            f"Laatste fout: {err}. MSO heeft het automatisch toepassen geparkeerd en probeert het hooguit één keer "
            "per dag opnieuw. Bekijk de wijziging: pas haar zelf toe (lukt dat, dan gaat MSO gewoon verder) of "
            "verwerp haar. Een gewijzigd voorstel begint vanzelf opnieuw.",
            risk="toestemming_nodig",
            subject=ch.get("target"),
            dedupe_key=f"automation.parked:{key}:{fp}",
            evidence={"kind": "automation_parked", "change_id": ch.get("id"), "key": key, "failures": failures,
                      "last_error": err},
            db=db,
        )
        # A new parking episode of the same change (after "Opnieuw automatisch proberen", a successful apply or a
        # changed preview resolved the old item) must show up again: recommend() keeps the old status on upsert.
        db.execute("UPDATE recommendations SET status='open', updated_at=now() WHERE id=%s", (rec_id,))
        audit(ACTOR, "automation.parked", f"Automatisch toepassen geparkeerd na {failures} pogingen: {ch['title']}",
              target=ch.get("target"), change_id=ch.get("id"), detail={"key": key, "error": err})
    db.execute(
        "INSERT INTO automation_failures(key,fingerprint,target,title,area,change_id,failures,last_error,"
        "last_failed_at,next_attempt_at,parked,rec_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s) "
        "ON CONFLICT (key) DO UPDATE SET fingerprint=EXCLUDED.fingerprint, target=EXCLUDED.target, "
        "title=EXCLUDED.title, area=EXCLUDED.area, change_id=EXCLUDED.change_id, failures=EXCLUDED.failures, "
        "last_error=EXCLUDED.last_error, last_failed_at=now(), next_attempt_at=EXCLUDED.next_attempt_at, "
        "parked=EXCLUDED.parked, rec_id=EXCLUDED.rec_id",
        (key, fp, ch.get("target") or "", ch.get("title") or "", area, ch.get("id"), failures, err, nxt, parked,
         rec_id),
    )
    return _cooldown_view(dict(db.fetchone("SELECT * FROM automation_failures WHERE key=%s", (key,)) or {}))


def note_success(ch: dict[str, Any], db: Database | None = None) -> None:
    db = db or get_db()
    row = db.fetchone("SELECT * FROM automation_failures WHERE key=%s", (failure_key(ch),))
    if row is not None:
        _clear(dict(row), db, why="toegepast")


def tidy_failures(db: Database | None = None) -> int:
    """Clear entries whose change was applied meanwhile (e.g. by a person) and stale non-parked entries."""
    db = db or get_db()
    cleared = 0
    for row in db.fetchall("SELECT * FROM automation_failures"):
        row = dict(row)
        sql = ("SELECT * FROM change_sets WHERE target=%s AND applied_at > %s "
               "AND status IN ('applied','rolled_back')")
        params: list[Any] = [row["target"], row["last_failed_at"]]
        if not row["key"].startswith("fp:"):
            sql += " AND dedupe_key=%s"
            params.append(row["key"])
        later = db.fetchall(sql + " ORDER BY id DESC LIMIT 20", params)
        if any(failure_key(dict(c)) == row["key"] and fingerprint(dict(c)) == row["fingerprint"] for c in later):
            _clear(row, db, why="toegepast")
            cleared += 1
        elif not row["parked"] and row["next_attempt_at"] < datetime.now(UTC) - timedelta(days=7):
            db.execute("DELETE FROM automation_failures WHERE key=%s", (row["key"],))
            cleared += 1
    return cleared


def reset_failure(key: str, actor: str, db: Database | None = None) -> dict[str, Any]:
    """'Opnieuw automatisch proberen' (operator+): forget the failures of ``key`` so the next round tries again."""
    db = db or get_db()
    row = db.fetchone("SELECT * FROM automation_failures WHERE key=%s", (key,))
    if row is None:
        raise HTTPException(404, detail="Geen afkoeling of geparkeerde wijziging met deze sleutel")
    db.execute("DELETE FROM automation_failures WHERE key=%s", (key,))
    _resolve_rec(row.get("rec_id"), db)
    audit(actor, "automation.unparked", f"Opnieuw automatisch proberen: {row['title']}", target=row["target"],
          change_id=row.get("change_id"), detail={"key": key, "failures": row["failures"]})
    return {"key": key, "cleared": True}


def cooldowns(db: Database | None = None) -> list[dict[str, Any]]:
    db = db or get_db()
    return [_cooldown_view(dict(r)) for r in db.fetchall(
        "SELECT * FROM automation_failures ORDER BY parked DESC, next_attempt_at")]


# ------------------------------------------------------------------ noodrem (self-imposed hold)
def _hold_value() -> dict[str, Any]:
    v = state.get_state(state.HOLD_KEY, None)
    return v if isinstance(v, dict) else {}


def brake_counts(db: Database | None = None) -> dict[str, Any]:
    """Automatic rollbacks and failed automatic applies in the last 24 h (since the last 'Hervatten')."""
    db = db or get_db()
    since = datetime.now(UTC) - timedelta(hours=24)
    resumed = _hold_value().get("resumed_at")
    if resumed:
        try:
            since = max(since, datetime.fromisoformat(str(resumed)))
        except ValueError:
            pass
    rollbacks = int(db.scalar("SELECT count(*) FROM automation_log WHERE kind='evaluate' AND result='rolled_back' "
                              "AND ts > %s", (since,)) or 0)
    failures = int(db.scalar("SELECT count(*) FROM automation_log WHERE kind='apply' AND result='failed' "
                             "AND ts > %s", (since,)) or 0)
    return {"since": since, "rollbacks": rollbacks, "failures": failures,
            "max_rollbacks": int(sr.get("automation.brake_rollbacks")),
            "max_failures": int(sr.get("automation.brake_failures"))}


def check_brake(db: Database | None = None) -> dict[str, Any] | None:
    """Engage the hold when a threshold is reached (called after every failed automatic apply and every automatic
    rollback). Returns the new hold, or None."""
    db = db or get_db()
    if state.hold() is not None or state.mode() not in ("manage", "autopilot"):
        return None
    c = brake_counts(db)
    parts = []
    if c["rollbacks"] >= c["max_rollbacks"]:
        parts.append(f"{c['rollbacks']} wijzigingen bleken slechter en zijn automatisch teruggedraaid")
    if c["failures"] >= c["max_failures"]:
        parts.append(f"{c['failures']} automatische wijzigingen konden niet worden toegepast")
    if not parts:
        return None
    return engage_hold("; ".join(parts), c, db)


def engage_hold(reason: str, counts: dict[str, Any], db: Database | None = None) -> dict[str, Any]:
    from mso.domain.outcomes import recommend

    db = db or get_db()
    now = datetime.now(UTC)
    who = "Autopilot" if state.mode() == "autopilot" else "Automatisering"
    rec_id = recommend(
        "automation",
        f"{who} heeft zichzelf gepauzeerd",
        f"{reason} (binnen 24 uur). MSO past daarom niets meer automatisch toe tot jij op 'Hervatten' klikt. "
        "Synchronisaties, controles en voorstellen klaarzetten gaan door, en je kunt zelf wijzigingen toepassen. "
        "Bekijk eerst wat er misging (Automatisering → Controle en Wijzigingen).",
        risk="toestemming_nodig",
        subject="automation",
        dedupe_key=f"autopilot.hold:{now.isoformat()}",
        evidence={"kind": "autopilot_hold", "rollbacks": counts.get("rollbacks"), "failures": counts.get("failures")},
        db=db,
    )
    value = {"active": True, "since": now.isoformat(), "reason": reason, "mode": state.mode(),
             "rollbacks": counts.get("rollbacks"), "failures": counts.get("failures"), "rec_id": rec_id}
    state.set_state(state.HOLD_KEY, value, db)
    audit(ACTOR, "autopilot.self_paused", f"{who} heeft zichzelf gepauzeerd: {reason}"[:500], target="automation",
          detail={k: value[k] for k in ("reason", "rollbacks", "failures", "mode")})
    log("run", "self_paused", f"Noodrem: {reason}", db=db)
    return value


def resume_hold(actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    current = state.hold()
    if current is None:  # idempotent: resumed meanwhile (another tab, another operator) — nothing to do
        return _hold_value()
    value = {**current, "active": False, "resumed_at": datetime.now(UTC).isoformat(), "resumed_by": actor}
    state.set_state(state.HOLD_KEY, value, db)
    _resolve_rec(current.get("rec_id"), db)
    audit(actor, "autopilot.resumed", "Noodrem opgeheven: automatische wijzigingen hervat", target="automation",
          detail={"since": current.get("since"), "reason": current.get("reason")})
    log("run", "resumed", f"Noodrem opgeheven door {actor}", db=db)
    return value


# ------------------------------------------------------------------ apply
def apply_one(ch: dict[str, Any], area: str | None, db: Database | None = None, *,
              actor: str = ACTOR) -> tuple[bool, str | None]:
    """Apply one change automatically (no checks beyond :func:`changes.apply`'s, plus the cooldown). Logs + audits;
    a failure enters the cooldown and may engage the noodrem. Returns (applied, error)."""
    db = db or get_db()
    cd = cooldown(ch, db)
    if cd is not None:
        return False, cd["reason"]
    try:
        changes.apply(ch["id"], actor, automatic=True, db=db)
    except changes.ChangeBusy as exc:  # someone else is applying it right now: not a failure of the change
        return False, str(exc.detail)
    except HTTPException as exc:
        log("apply", "failed", f"'{ch['title']}': {exc.detail}", ch["id"], area, db)
        audit(actor, "automation.apply_failed", f"Automatisch toepassen mislukt: {ch['title']}",
              target=ch["target"], change_id=ch["id"], detail={"error": str(exc.detail), "area": area})
        note_failure(ch, str(exc.detail), area, db)
        check_brake(db)
        return False, str(exc.detail)
    note_success(ch, db)
    db.execute("UPDATE recommendations SET status='accepted', updated_at=now() WHERE change_id=%s "
               "AND status='open'", (ch["id"],))
    log("apply", "applied", f"Automatisch toegepast: {ch['title']}", ch["id"], area, db)
    audit(actor, "automation.applied", f"Automatisch toegepast: {ch['title']}", target=ch["target"],
          change_id=ch["id"], detail={"area": area, "autopilot": state.autopilot()})
    return True, None


def apply_unattended(change_id: int, *, area: str | None, actor: str = ACTOR,
                     db: Database | None = None) -> tuple[bool, str | None]:
    """For a slice that applies its own change right after creating it (e.g. qBittorrent tuning in beheermodus with
    ``torrent.autotune_enabled``): exactly the checks of every other automatic apply — :func:`why_not` (mode,
    pause, noodrem, owner override, switched on), the cooldown after failures and the hourly cap (failed attempts
    included) — and the same bookkeeping (automation log, failure ledger, noodrem count). A change held back by
    the cooldown or the cap stays a proposal; the periodic ``automation.apply_safe`` picks it up later. Returns
    (applied, reason)."""
    db = db or get_db()
    ch = changes.get(change_id, db)
    if ch["status"] == "applied":
        return True, None
    reason = why_not(ch, area)
    if reason is None and (cd := cooldown(ch, db)) is not None:
        reason = cd["reason"]
    if reason is None and budget_left(db) <= 0:
        cap = int(sr.get("automation.max_per_hour"))
        reason = f"limiet van {cap} automatische wijzigingen per uur bereikt; volgt later"
        log("apply", "rate_limited", f"Limiet van {cap} per uur bereikt; '{ch['title']}' wacht", ch["id"], area, db)
    if reason is not None:
        return False, reason
    return apply_one(ch, area, db, actor=actor)


def propose_and_apply(target: str, title: str, ops: list[dict[str, Any]], *, explanation: str,
                      impact: dict[str, Any] | None, dedupe_key: str | None, evaluate_after_h: float | None = None,
                      area: str) -> dict[str, Any]:
    """Create the change as ``automatisch_veilig`` (``toestemming_nodig`` when an op is ``irreversible``) and apply
    it right away when allowed (see module docstring). Otherwise it stays a preview proposal.

    Returns the change row plus ``auto_applied: bool`` and ``auto_reason: str | None`` (why it was not applied,
    or the apply error). Idempotent on ``dedupe_key`` like :func:`changes.create`. Never raises on a failed
    automatic apply (the change is then ``failed`` and audited); raises ValueError/HTTPException only for an
    invalid change (empty ops, policy validation)."""
    db = get_db()
    irreversible = any(op.get("irreversible") for op in ops)
    imp = {"area": area, **(impact or {})}
    if state.autopilot():
        imp.setdefault("autopilot", True)
    ch = changes.create(target, title, [dict(op) for op in ops],
                        risk="toestemming_nodig" if irreversible else "automatisch_veilig",
                        explanation=explanation, impact=imp, source=f"automation:{area}", actor=ACTOR,
                        dedupe_key=dedupe_key, evaluate_after_h=evaluate_after_h, db=db)
    reason = why_not(ch, area)
    if reason is None and (cd := cooldown(ch, db)) is not None:
        reason = cd["reason"]
    if reason is None and budget_left(db) <= 0:
        cap = int(sr.get("automation.max_per_hour"))
        reason = f"limiet van {cap} automatische wijzigingen per uur bereikt; volgt later"
        log("apply", "rate_limited", f"Limiet van {cap} per uur bereikt; '{title}' wacht", ch["id"], area, db)
    if reason is not None:
        return dict(ch) | {"auto_applied": False, "auto_reason": reason}
    ok, err = apply_one(ch, area, db)
    return changes.get(ch["id"], db) | {"auto_applied": ok, "auto_reason": err}


__all__ = ["apply_one", "apply_unattended", "area_on", "budget_left", "check_brake", "cooldown", "enabled",
           "propose_and_apply", "resume_hold", "slice_on", "why_not"]
