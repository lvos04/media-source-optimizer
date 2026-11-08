"""Change sets: preview → (approve) → apply → rollback for every write to an external system.

An *applier* is registered per target system (``sabnzbd``, ``qbittorrent``, ``prowlarr`` …) with:

- ``read(path) -> value``  — current value at an applier-specific path
- ``write(path, value)``   — set the value (must be idempotent)

Apply semantics:
1. Requires beheermodus (manage). ``toestemming_nodig`` requires prior approval by an admin.
2. For every op the current value is read first. If it differs from the previewed ``before`` the
   apply is refused (drift — somebody else, e.g. MediaGuard's own reconcile, changed it) unless forced.
3. Crash-safe journal (1.6.0): before the first external write the change moves to ``applying`` with
   ``observed_before`` stored per op; every op's result is stored right after its write + read-back. An apply that
   is cut off (restart, crash) is finished or undone by :func:`reconcile_interrupted` (worker start + periodically).
4. Writes happen one op at a time and are verified by reading back. Any failure restores the ops already written
   (verified, recorded) and marks the change ``failed``.
5. Rollback (1.6.0) checks drift per op first: a value that is neither MSO's value nor the original any more was
   changed outside MSO and is skipped ("overgeslagen"), never overwritten. Every written op is read back; a failed
   read-back keeps the change ``applied`` with the error — there is no "teruggedraaid" without verification.
6. Status transitions are atomic: a per-change advisory lock (held for the whole apply/rollback, released by
   PostgreSQL when the process dies) plus ``UPDATE … WHERE status IN (…) RETURNING``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import state
from mso.audit import audit
from mso.db import Database, get_db, jsonb
from mso.security import redact

LOGGER = logging.getLogger(__name__)
RISKS = ("advies", "automatisch_veilig", "toestemming_nodig")
OPEN_STATUSES = ("preview", "approved", "applying")  # dedupe: one open change per key
LOCK_CLASS = 727101  # pg_advisory_lock(LOCK_CLASS, change_id) — distinct from the migration lock (727001)
RECONCILE_GRACE = timedelta(hours=1)  # source unreachable after a restart: keep trying this long, then report


class ChangeBusy(HTTPException):
    """Another request/worker is applying or rolling back this change right now (not a failure of the change)."""

    def __init__(self) -> None:
        super().__init__(409, detail="Deze wijziging wordt op dit moment al toegepast of teruggedraaid")


class RollbackSkipped(HTTPException):
    """Nothing was rolled back: every value was changed outside MSO since (drift), and MSO never overwrites that."""

    def __init__(self, detail: str, skipped: list[dict[str, Any]]) -> None:
        super().__init__(409, detail=detail)
        self.skipped = skipped


@dataclass
class Applier:
    read: Callable[[str], Any]
    write: Callable[[str, Any], None]
    validate: Callable[[str, Any], None] | None = None  # raise ValueError on policy violation
    # (path, a, b) → the source holds the same value (e.g. qBittorrent keeps limits in whole KiB/s); default ==
    same: Callable[[str, Any, Any], bool] | None = None
    # path → rolling back is a no-op by design (a one-shot action that changes no data, e.g. a hash check)
    noop_rollback: Callable[[str], bool] | None = None


def _same(applier: Applier, path: str, a: Any, b: Any) -> bool:
    if a == b:
        return True
    if applier.same is not None:
        try:
            return bool(applier.same(path, a, b))
        except Exception:
            return False
    return False


APPLIERS: dict[str, Callable[[], Applier]] = {}


def register_applier(target: str, factory: Callable[[], Applier]) -> None:
    """``factory`` builds an Applier on demand (so it can pick up current connector credentials)."""
    APPLIERS[target] = factory


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _short(value: Any, n: int = 120) -> str:
    return redact(repr(value))[:n]


def reversible(op: dict[str, Any]) -> bool:
    """An op MSO can write back (not ``irreversible`` deletion, not ``one_way`` like sending a download)."""
    return not (op.get("irreversible") or op.get("one_way"))


@contextmanager
def _lock(change_id: int, db: Database) -> Iterator[None]:
    """Session-level advisory lock on a dedicated pool connection for the whole apply/rollback. PostgreSQL releases
    it when that connection closes, so a crashed process never leaves it behind — which is how the reconciler tells
    an interrupted apply from one that is still running. Raises :class:`ChangeBusy` when someone else holds it."""
    key = int(change_id) % 2147483647
    with db.pool.connection() as conn:
        conn.autocommit = True
        try:
            got = conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok", (LOCK_CLASS, key)).fetchone()
            if not got or not got["ok"]:
                raise ChangeBusy()
            try:
                yield
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_CLASS, key))
        finally:
            conn.autocommit = False


def _persist_ops(db: Database, change_id: int, ops: list[dict[str, Any]], status: str) -> None:
    db.execute("UPDATE change_sets SET ops=%s WHERE id=%s AND status=%s", (jsonb(ops), change_id, status))


def create(target: str, title: str, ops: list[dict[str, Any]], *, risk: str, explanation: str = "",
           impact: dict[str, Any] | None = None, source: str = "user", actor: str = "system",
           dedupe_key: str | None = None, evaluate_after_h: float | None = None,
           db: Database | None = None) -> dict[str, Any]:
    """Create a change set in ``preview``. ``ops``: [{path, label, before, after, destructive?, irreversible?}].
    Idempotent on ``dedupe_key`` while an open (preview/approved/applying) change with that key exists.

    Hard rule: an op that removes or disables something (``destructive``) or cannot be undone (``irreversible``)
    is only allowed in a ``toestemming_nodig`` change: preview → explicit approval → apply. It can never be applied
    automatically, whatever mode or automation setting is active."""
    if risk not in RISKS:
        raise ValueError(f"onbekend risico {risk}")
    # deleting / not undoable → always explicit approval; switching off (destructive but reversible) may be
    # 'automatisch_veilig' only for autopilot use (apply() checks the mode)
    if any(op.get("irreversible") for op in ops) and risk != "toestemming_nodig":
        raise ValueError("verwijderen (niet terug te draaien) vereist risico 'toestemming_nodig'")
    if any(op.get("destructive") for op in ops) and risk == "advies":
        raise ValueError("uitschakelen kan niet als advies worden toegepast")
    if not ops:
        raise ValueError("lege wijziging")
    for op in ops:
        if "path" not in op or "after" not in op:
            raise ValueError("elke operatie heeft path en after nodig")
        op.setdefault("label", op["path"])
        op.setdefault("before", None)
    db = db or get_db()
    if target in APPLIERS and (validate := _validator(target)):
        for op in ops:
            try:
                validate(op["path"], op["after"])
            except ValueError as exc:
                raise HTTPException(422, detail=f"Beleid weigert '{op['label']}': {exc}") from exc
    if dedupe_key:
        existing = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status = ANY(%s)",
                               (dedupe_key, list(OPEN_STATUSES)))
        if existing:
            return dict(existing)
    evaluate_after = datetime.now(UTC) + timedelta(hours=evaluate_after_h) if evaluate_after_h else None
    row = db.fetchone(
        "INSERT INTO change_sets(target,title,explanation,risk,ops,impact,source,dedupe_key,created_by,evaluate_after) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (target, title, explanation, risk, jsonb(ops), jsonb(impact or {}), source, dedupe_key, actor,
         evaluate_after))
    audit(actor, "change.created", f"Wijzigingsvoorstel: {title}", target=target, change_id=row["id"],
          detail={"risk": risk, "ops": len(ops)})
    return dict(row)


def _validator(target: str) -> Callable[[str, Any], None] | None:
    try:
        return APPLIERS[target]().validate
    except Exception:
        return None


def get(change_id: int, db: Database | None = None) -> dict[str, Any]:
    row = (db or get_db()).fetchone("SELECT * FROM change_sets WHERE id=%s", (change_id,))
    if row is None:
        raise HTTPException(404, detail="Wijziging niet gevonden")
    return dict(row)


def _transition(db: Database, change_id: int, sql_set: str, params: tuple[Any, ...], allowed: tuple[str, ...]) -> bool:
    """``UPDATE change_sets SET <sql_set> WHERE id AND status IN allowed RETURNING id`` — False when another request
    changed the status first (the caller answers 409)."""
    row = db.fetchone(f"UPDATE change_sets SET {sql_set} WHERE id=%s AND status = ANY(%s) RETURNING id",
                      (*params, change_id, list(allowed)))
    return row is not None


def _conflict(change_id: int, db: Database) -> HTTPException:
    return HTTPException(409, detail=f"Wijziging heeft status '{get(change_id, db)['status']}'")


def approve(change_id: int, actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    ch = get(change_id, db)
    if ch["status"] != "preview" or not _transition(db, change_id, "status='approved', approved_by=%s", (actor,),
                                                    ("preview",)):
        raise _conflict(change_id, db)
    audit(actor, "change.approved", f"Goedgekeurd: {ch['title']}", target=ch["target"], change_id=change_id)
    return get(change_id, db)


def discard(change_id: int, actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    ch = get(change_id, db)
    if ch["status"] not in ("preview", "approved") or not _transition(db, change_id, "status='discarded'", (),
                                                                      ("preview", "approved")):
        raise _conflict(change_id, db)
    audit(actor, "change.discarded", f"Verworpen: {ch['title']}", target=ch["target"], change_id=change_id)
    return get(change_id, db)


def _check_applicable(ch: dict[str, Any], automatic: bool) -> None:
    if ch["status"] not in ("preview", "approved"):
        raise HTTPException(409, detail=f"Wijziging heeft status '{ch['status']}'")
    if ch["risk"] == "toestemming_nodig" and ch["status"] != "approved":
        raise HTTPException(409, detail="Deze wijziging vereist eerst goedkeuring door een beheerder")
    if automatic:
        if any(op.get("irreversible") for op in ch["ops"]):
            raise HTTPException(409, detail="Verwijderen wordt nooit automatisch toegepast")
        if any(op.get("destructive") for op in ch["ops"]) and not state.autopilot():
            raise HTTPException(409, detail="Uitschakelen gebeurt alleen automatisch in Autopilot")
        if ch["risk"] != "automatisch_veilig":
            raise HTTPException(409, detail="Alleen 'automatisch veilig' mag zonder gebruiker worden toegepast")
        if not state.automation_allowed():
            raise HTTPException(409, detail="Automatisering staat op pauze, staat stil (noodrem) of alleen-lezen")


def _restore(applier: Applier, written: list[dict[str, Any]]) -> list[str]:
    """Write ``observed_before`` back for written reversible ops (reverse order), verified by read-back. Returns the
    errors; ops that cannot be written back (one_way) keep their result."""
    errors: list[str] = []
    for op in reversed(written):
        if not reversible(op):
            op["result"] = "toegepast"
            op["restore_note"] = "niet terug te draaien"
            continue
        try:
            applier.write(op["path"], op["observed_before"])
            check = applier.read(op["path"])
            if not _same(applier, op["path"], check, op["observed_before"]):
                raise RuntimeError(f"controle na terugzetten gaf {_short(check)}")
            op["result"] = "teruggedraaid"
            op["verified_at"] = _now_iso()
        except Exception as exc:
            op["restore_error"] = redact(str(exc))[:300]
            errors.append(f"{op['label']}: {redact(str(exc))[:200]}")
    return errors


def apply(change_id: int, actor: str, *, automatic: bool = False, force: bool = False,
          db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    state.require_manage_mode()
    ch = get(change_id, db)
    _check_applicable(ch, automatic)
    factory = APPLIERS.get(ch["target"])
    if factory is None:
        raise HTTPException(409, detail=f"Geen schrijfkoppeling voor '{ch['target']}'")
    with _lock(change_id, db):
        ch = get(change_id, db)  # under the lock: another request may have applied/discarded it meanwhile
        _check_applicable(ch, automatic)
        try:
            applier = factory()
        except Exception as exc:
            raise HTTPException(409, detail=f"Koppeling '{ch['target']}' niet beschikbaar: {redact(str(exc))[:300]}") \
                from exc

        ops = [dict(op) for op in ch["ops"]]
        # 1) drift detection on everything before writing anything
        for op in ops:
            try:
                current = applier.read(op["path"])
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, detail=(f"Huidige waarde van '{op['label']}' niet te lezen: "
                                                 f"{redact(str(exc))[:300]}")) from exc
            op["observed_before"] = current
            if applier.validate:
                try:
                    applier.validate(op["path"], op["after"])
                except ValueError as exc:
                    raise HTTPException(422, detail=f"Beleid weigert '{op['label']}': {exc}") from exc
            if not force and op.get("before") is not None and current != op["before"] and current != op["after"]:
                raise HTTPException(409, detail=(
                    f"Afwijking gedetecteerd bij '{op['label']}': verwacht {op['before']!r}, nu {current!r}. "
                    "Een ander systeem (bijv. MediaGuard) beheert deze waarde mogelijk; maak een nieuw voorstel."))
        # 2) journal: 'applying' with every observed_before, BEFORE the first external write
        if not _transition(db, change_id, "status='applying', ops=%s, apply_started_at=now(), applied_by=%s, "
                           "applied_auto=%s, error=NULL", (jsonb(ops), actor, automatic), ("preview", "approved")):
            raise _conflict(change_id, db)
        # 3) write + verify one op at a time; each result is stored right away
        written: list[dict[str, Any]] = []
        try:
            for op in ops:
                if op["observed_before"] == op["after"]:
                    op["result"] = "al_ingesteld"
                else:
                    applier.write(op["path"], op["after"])
                    written.append(op)
                    check = applier.read(op["path"])
                    if not _same(applier, op["path"], check, op["after"]):
                        raise RuntimeError(f"verificatie mislukt voor '{op['label']}': {check!r}")
                    op["result"] = "toegepast"
                    op["verified_at"] = _now_iso()
                _persist_ops(db, change_id, ops, "applying")
        except Exception as exc:
            rollback_errors = _restore(applier, written)
            err = redact(f"{exc}") + (f" · herstel mislukt: {rollback_errors}" if rollback_errors
                                      else " · automatisch hersteld")
            db.execute("UPDATE change_sets SET status='failed', error=%s, ops=%s, applied_by=NULL, applied_auto=false "
                       "WHERE id=%s AND status='applying'", (err, jsonb(ops), change_id))
            audit(actor, "change.failed", f"Toepassen mislukt: {ch['title']}", target=ch["target"],
                  change_id=change_id, detail={"error": err, "automatic": automatic})
            if written:
                restored = [o["label"] for o in written if o.get("result") == "teruggedraaid"]
                audit("systeem", "change.auto_restored" if not rollback_errors else "change.auto_restore_failed",
                      (f"Automatisch hersteld na mislukte wijziging: {ch['title']}" if not rollback_errors
                       else f"Automatisch herstel deels mislukt: {ch['title']}"),
                      target=ch["target"], change_id=change_id,
                      detail={"restored": restored, "errors": rollback_errors,
                              "values": [{"label": o["label"], "back_to": o["observed_before"]} for o in written]})
            raise HTTPException(502, detail=f"Toepassen mislukt: {err}") from exc
        if not _transition(db, change_id, "status='applied', applied_by=%s, applied_at=now(), ops=%s, error=NULL",
                           (actor, jsonb(ops)), ("applying",)):
            raise _conflict(change_id, db)
    audit(actor, "change.applied", f"Toegepast: {ch['title']}", target=ch["target"], change_id=change_id,
          detail={"ops": [{"label": o["label"], "before": o["observed_before"], "after": o["after"]} for o in ops],
                  "automatic": automatic})
    return get(change_id, db)


# ------------------------------------------------------------------ rollback
def _record_override(ch: dict[str, Any], op: dict[str, Any], current: Any) -> None:
    """A value MSO set was changed outside MSO: never re-apply that path automatically for a while."""
    try:
        from mso.features.automation import autopilot, overrides

        overrides.record(ch["target"], str(op["path"]), mso_value=op.get("after"), current_value=current,
                         change_id=ch["id"], days=autopilot.override_days())
    except Exception:  # automation module disabled: rollback itself must not fail on this
        LOGGER.debug("override niet vastgelegd", exc_info=True)


def rollback(change_id: int, actor: str, reason: str = "", db: Database | None = None) -> dict[str, Any]:
    """Write ``observed_before`` back for every applied, reversible op (reverse order).

    Per op: read the current value first — still MSO's value → write back + read back (verified); already the
    original → nothing to write (verified); anything else → changed outside MSO: skipped ("overgeslagen"), never
    overwritten (and recorded as an owner override). A failed write or read-back keeps the change ``applied`` with
    the error (502). Nothing written back because everything drifted → :class:`RollbackSkipped` (409)."""
    db = db or get_db()
    state.require_manage_mode()
    ch = get(change_id, db)
    if ch["status"] != "applied":
        raise HTTPException(409, detail="Alleen toegepaste wijzigingen kunnen worden teruggedraaid")
    # ``one_way``: cannot be undone but is not destructive (e.g. sending a release to a download client); allowed in
    # any risk class, skipped on rollback exactly like ``irreversible``.
    if not any(op.get("result") == "toegepast" and reversible(op) for op in ch["ops"]):
        raise HTTPException(409, detail="Deze wijziging is niet terug te draaien (bijv. verwijderde bestanden)")
    factory = APPLIERS.get(ch["target"])
    if factory is None:
        raise HTTPException(409, detail=f"Geen schrijfkoppeling voor '{ch['target']}'")
    with _lock(change_id, db):
        ch = get(change_id, db)
        if ch["status"] != "applied":
            raise HTTPException(409, detail="Alleen toegepaste wijzigingen kunnen worden teruggedraaid")
        try:
            applier = factory()
        except Exception as exc:
            raise HTTPException(409, detail=f"Koppeling '{ch['target']}' niet beschikbaar: {redact(str(exc))[:300]}") \
                from exc
        ops = [dict(op) for op in ch["ops"]]
        errors: list[str] = []
        skipped: list[dict[str, Any]] = []
        for op in reversed(ops):
            if op.get("result") != "toegepast":
                continue
            if not reversible(op):
                op["result"] = "niet_terug_te_draaien"
                _persist_ops(db, change_id, ops, "applied")
                continue
            if applier.noop_rollback is not None and applier.noop_rollback(str(op["path"])):
                try:
                    applier.write(op["path"], op.get("observed_before"))
                except Exception as exc:
                    op["rollback_error"] = redact(str(exc))[:300]
                    errors.append(f"{op['label']}: {op['rollback_error']}")
                    _persist_ops(db, change_id, ops, "applied")
                    continue
                op.update(result="teruggedraaid", rollback_note="eenmalige actie zonder blijvend effect; niets "
                          "terug te zetten")
                _persist_ops(db, change_id, ops, "applied")
                continue
            try:
                current = applier.read(op["path"])
            except Exception as exc:
                op["rollback_error"] = f"huidige waarde niet te lezen: {redact(str(exc))[:200]}"
                errors.append(f"{op['label']}: {op['rollback_error']}")
                _persist_ops(db, change_id, ops, "applied")
                continue
            if _same(applier, op["path"], current, op.get("observed_before")):
                op.update(result="teruggedraaid", verified_at=_now_iso(), rollback_note="was al de oude waarde")
            elif not _same(applier, op["path"], current, op["after"]):
                op.update(result="overgeslagen", skip_reason="buiten MSO gewijzigd", current=current,
                          checked_at=_now_iso())
                op.pop("rollback_error", None)
                skipped.append(op)
                _record_override(ch, op, current)
            else:
                try:
                    applier.write(op["path"], op.get("observed_before"))
                    check = applier.read(op["path"])
                    if not _same(applier, op["path"], check, op.get("observed_before")):
                        raise RuntimeError(f"controle na terugzetten gaf {_short(check)}, verwacht "
                                           f"{_short(op.get('observed_before'))}")
                except Exception as exc:
                    op["rollback_error"] = redact(str(exc))[:300]
                    errors.append(f"{op['label']}: {op['rollback_error']}")
                    _persist_ops(db, change_id, ops, "applied")
                    continue
                op.update(result="teruggedraaid", verified_at=_now_iso())
                op.pop("rollback_error", None)
            _persist_ops(db, change_id, ops, "applied")
        reverted = [op for op in ops if op.get("result") == "teruggedraaid"]
        skip_line = "; ".join(f"{o['label']}: nu {_short(o.get('current'), 60)}" for o in skipped)
        if errors:
            db.execute("UPDATE change_sets SET error=%s, ops=%s WHERE id=%s AND status='applied'",
                       ("; ".join(errors)[:1000], jsonb(ops), change_id))
            audit(actor, "change.rollback_failed", f"Terugdraaien niet gelukt: {ch['title']}", target=ch["target"],
                  change_id=change_id, detail={"errors": errors, "reverted": [o["label"] for o in reverted],
                                               "skipped": [o["label"] for o in skipped]})
            raise HTTPException(502, detail=f"Terugdraaien deels mislukt: {errors}")
        if not reverted:
            msg = f"Niets teruggedraaid: buiten MSO gewijzigd ({skip_line}); MSO overschrijft dat niet"
            db.execute("UPDATE change_sets SET error=%s, ops=%s WHERE id=%s AND status='applied'",
                       (msg[:1000], jsonb(ops), change_id))
            audit(actor, "change.rollback_skipped", f"Niet teruggedraaid (buiten MSO gewijzigd): {ch['title']}",
                  target=ch["target"], change_id=change_id,
                  detail={"skipped": [{"label": o["label"], "now": o.get("current")} for o in skipped]})
            raise RollbackSkipped(msg, skipped)
        if not _transition(db, change_id, "status='rolled_back', rolled_back_at=now(), ops=%s, error=%s",
                           (jsonb(ops), (f"Overgeslagen (buiten MSO gewijzigd): {skip_line}"[:1000] if skipped
                                         else None)), ("applied",)):
            raise _conflict(change_id, db)
    audit(actor, "change.rolled_back", f"Teruggedraaid: {ch['title']}" + (f" ({reason})" if reason else ""),
          target=ch["target"], change_id=change_id,
          detail={"verified": [o["label"] for o in reverted],
                  "skipped": [{"label": o["label"], "now": o.get("current")} for o in skipped]})
    return get(change_id, db)


# ------------------------------------------------------------------ crash recovery
def _waiting_item(ch: dict[str, Any], title: str, explanation: str, outcome: str, db: Database) -> None:
    """One "Wacht op jou" item per interrupted change (recommendation without change link, so the inbox lists it)."""
    from mso.domain.outcomes import recommend

    area = (ch.get("impact") or {}).get("area") if isinstance(ch.get("impact"), dict) else None
    recommend(area or "automation", title, explanation, risk="toestemming_nodig", subject=ch["target"],
              dedupe_key=f"change.interrupted:{ch['id']}",
              evidence={"change_id": ch["id"], "outcome": outcome, "kind": "change_interrupted"}, db=db)


def _resolve_item(change_id: int, db: Database) -> None:
    db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s AND status='open'",
               (f"change.interrupted:{change_id}",))


def _reconcile_one(ch: dict[str, Any], db: Database) -> str:
    """Finish or undo one interrupted apply (lock held by the caller). Returns the outcome."""
    ops = [dict(op) for op in ch["ops"]]
    started = ch.get("apply_started_at") or ch.get("created_at") or datetime.now(UTC)
    grace_over = datetime.now(UTC) - started > RECONCILE_GRACE
    try:
        applier = APPLIERS[ch["target"]]()
    except Exception as exc:
        applier = None
        unreadable = [f"koppeling niet beschikbaar: {redact(str(exc))[:160]}"]
    else:
        unreadable = []
    unclear: list[str] = []
    if applier is not None:
        for op in ops:
            if op.get("result") == "al_ingesteld":
                continue
            try:
                cur = applier.read(op["path"])
            except Exception as exc:
                unreadable.append(f"{op['label']}: {redact(str(exc))[:160]}")
                continue
            op["checked_at"] = _now_iso()
            if op.get("observed_before") == op["after"]:
                op["result"] = "al_ingesteld"
            elif _same(applier, op["path"], cur, op["after"]):
                op["result"] = "toegepast"
                op.setdefault("verified_at", _now_iso())
            elif _same(applier, op["path"], cur, op.get("observed_before")):
                op["result"] = "niet_toegepast"
            else:
                op["result"] = "afwijkend"
                op["current"] = cur
                unclear.append(f"{op['label']}: nu {_short(cur, 60)}")
    if unreadable and not grace_over:
        return "wacht"  # e.g. SABnzbd restarts together with MSO: try again in the next round
    title = ch["title"]
    if unreadable:
        _persist_ops(db, ch["id"], ops, "applying")
        err = "Onderbroken tijdens toepassen; huidige waarde bij de bron niet te controleren: " + "; ".join(unreadable)
        db.execute("UPDATE change_sets SET status='failed', error=%s, ops=%s WHERE id=%s AND status='applying'",
                   (err[:1000], jsonb(ops), ch["id"]))
        _waiting_item(ch, f"Onderbroken wijziging controleren: {title}",
                      f"MSO werd herstart terwijl '{title}' werd toegepast, en kon daarna niet controleren wat er bij "
                      f"{ch['target']} is ingesteld ({'; '.join(unreadable)[:300]}). Controleer de instellingen zelf; "
                      "maak zo nodig een nieuw voorstel.", "onduidelijk", db)
        return "onduidelijk"
    if all(op.get("result") in ("toegepast", "al_ingesteld") for op in ops):
        db.execute("UPDATE change_sets SET status='applied', applied_at=now(), ops=%s, error=NULL "
                   "WHERE id=%s AND status='applying'", (jsonb(ops), ch["id"]))
        audit(ch.get("applied_by") or "systeem", "change.applied", f"Toegepast (afgerond na onderbreking): {title}",
              target=ch["target"], change_id=ch["id"],
              detail={"ops": [{"label": o["label"], "before": o.get("observed_before"), "after": o["after"]}
                              for o in ops], "automatic": bool(ch.get("applied_auto")), "interrupted": True})
        _resolve_item(ch["id"], db)
        return "afgerond"
    if state.mode() not in ("manage", "autopilot"):
        _persist_ops(db, ch["id"], ops, "applying")
        _waiting_item(ch, f"Onderbroken wijziging wacht op beheermodus: {title}",
                      f"MSO werd herstart terwijl '{title}' werd toegepast; een deel is al ingesteld. In alleen-lezen "
                      "schrijft MSO niets, ook niet om dat terug te zetten. Zet beheermodus aan: dan zet MSO de al "
                      "geschreven waarden terug naar hoe ze waren.", "wacht_op_beheermodus", db)
        return "wacht_op_beheermodus"
    written = [op for op in ops if op.get("result") == "toegepast"]
    errors = _restore(applier, written) if applier is not None else []
    for op in ops:
        if op.get("result") == "niet_toegepast":
            op["result"] = "niet_uitgevoerd"
    kept = [o["label"] for o in written if o.get("result") == "toegepast"]  # one_way: cannot be undone
    parts = ["Onderbroken tijdens toepassen (herstart)"]
    if any(o.get("result") == "teruggedraaid" for o in written):
        parts.append("al geschreven waarden teruggezet en gecontroleerd")
    if kept:
        parts.append(f"niet terug te draaien en blijft: {', '.join(kept)}")
    if errors:
        parts.append(f"terugzetten mislukt: {'; '.join(errors)}")
    if unclear:
        parts.append(f"buiten MSO gewijzigd, niet aangeraakt: {'; '.join(unclear)}")
    err = " · ".join(parts)
    db.execute("UPDATE change_sets SET status='failed', error=%s, ops=%s WHERE id=%s AND status='applying'",
               (err[:1000], jsonb(ops), ch["id"]))
    outcome = "deels" if (errors or unclear) else "teruggezet"
    if errors or unclear or kept or not ch.get("applied_auto"):
        _waiting_item(ch, f"Wijziging onderbroken: {title}",
                      f"MSO werd herstart terwijl '{title}' werd toegepast. {err}. "
                      + ("Controleer de genoemde instellingen." if errors or unclear
                         else "Pas de wijziging opnieuw toe als je haar nog wilt."), outcome, db)
    else:  # an automatic change MSO proposes again by itself: no item needed
        _resolve_item(ch["id"], db)
    return outcome


def reconcile_interrupted(db: Database | None = None) -> dict[str, Any]:
    """Every change still ``applying`` whose apply is no longer running (its advisory lock is free): read each op at
    the source, then finish (everything written → ``applied``) or put the reversible ops that were written back
    (→ ``failed``), audit ``change.interrupted`` and, when something needs a person, a "Wacht op jou" item.
    In alleen-lezen nothing is written: a half-applied change waits (with an item) until beheermodus."""
    db = db or get_db()
    out: dict[str, Any] = {"checked": 0, "running": 0, "outcomes": {}}
    for row in db.fetchall("SELECT id FROM change_sets WHERE status='applying' ORDER BY id"):
        out["checked"] += 1
        try:
            with _lock(row["id"], db):
                ch = get(row["id"], db)
                if ch["status"] != "applying":
                    continue  # finished between our query and the lock
                outcome = _reconcile_one(ch, db)
        except ChangeBusy:
            out["running"] += 1
            continue
        except Exception as exc:  # one broken change must not stop the others
            LOGGER.warning("onderbroken wijziging %s niet afgehandeld: %s", row["id"], redact(str(exc)))
            out["outcomes"][str(row["id"])] = "fout"
            continue
        out["outcomes"][str(row["id"])] = outcome
        if outcome in ("wacht", "wacht_op_beheermodus"):
            continue  # still 'applying'; tried again in the next round (the item is already there)
        audit("systeem", "change.interrupted",
              f"Onderbroken wijziging {'afgerond' if outcome == 'afgerond' else 'afgehandeld'}: {ch['title']} "
              f"({outcome.replace('_', ' ')})", target=ch["target"], change_id=ch["id"],
              detail={"outcome": outcome, "automatic": bool(ch.get("applied_auto")),
                      "ops": [{"label": o["label"], "result": o.get("result")} for o in get(ch["id"], db)["ops"]]})
    return out


def list_changes(status: str | None = None, target: str | None = None, limit: int = 100,
                 db: Database | None = None) -> list[dict[str, Any]]:
    db = db or get_db()
    where, params = [], []
    if status:
        where.append("status = ANY(%s)")
        params.append(status.split(","))
    if target:
        where.append("target=%s")
        params.append(target)
    sql = "SELECT * FROM change_sets" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    return db.fetchall(sql, params)
