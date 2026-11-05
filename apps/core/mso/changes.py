"""Change sets: preview → (approve) → apply → rollback for every write to an external system.

An *applier* is registered per target system (``sabnzbd``, ``qbittorrent``, ``prowlarr`` …) with:

- ``read(path) -> value``  — current value at an applier-specific path
- ``write(path, value)``   — set the value (must be idempotent)

Apply semantics:
1. Requires beheermodus (manage). ``toestemming_nodig`` requires prior approval by an admin.
2. For every op the current value is read first. If it differs from the previewed ``before`` the
   apply is refused (drift — somebody else, e.g. MediaGuard's own reconcile, changed it) unless forced.
3. Writes happen one op at a time and are verified by reading back. Any failure rolls back the ops
   already written (best effort, recorded) and marks the change ``failed``.
4. ``observed_before`` is stored per op; rollback writes it back in reverse order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import state
from mso.audit import audit
from mso.db import Database, get_db, jsonb

LOGGER = logging.getLogger(__name__)
RISKS = ("advies", "automatisch_veilig", "toestemming_nodig")


@dataclass
class Applier:
    read: Callable[[str], Any]
    write: Callable[[str, Any], None]
    validate: Callable[[str, Any], None] | None = None  # raise ValueError on policy violation


APPLIERS: dict[str, Callable[[], Applier]] = {}


def register_applier(target: str, factory: Callable[[], Applier]) -> None:
    """``factory`` builds an Applier on demand (so it can pick up current connector credentials)."""
    APPLIERS[target] = factory


def create(target: str, title: str, ops: list[dict[str, Any]], *, risk: str, explanation: str = "",
           impact: dict[str, Any] | None = None, source: str = "user", actor: str = "system",
           dedupe_key: str | None = None, evaluate_after_h: float | None = None,
           db: Database | None = None) -> dict[str, Any]:
    """Create a change set in ``preview``. ``ops``: [{path, label, before, after, destructive?, irreversible?}].
    Idempotent on ``dedupe_key`` while an open (preview/approved) change with that key exists.

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
        existing = db.fetchone("SELECT * FROM change_sets WHERE dedupe_key=%s AND status IN ('preview','approved')",
                               (dedupe_key,))
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


def approve(change_id: int, actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    ch = get(change_id, db)
    if ch["status"] != "preview":
        raise HTTPException(409, detail=f"Wijziging heeft status '{ch['status']}'")
    db.execute("UPDATE change_sets SET status='approved', approved_by=%s WHERE id=%s", (actor, change_id))
    audit(actor, "change.approved", f"Goedgekeurd: {ch['title']}", target=ch["target"], change_id=change_id)
    return get(change_id, db)


def discard(change_id: int, actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    ch = get(change_id, db)
    if ch["status"] not in ("preview", "approved"):
        raise HTTPException(409, detail=f"Wijziging heeft status '{ch['status']}'")
    db.execute("UPDATE change_sets SET status='discarded' WHERE id=%s", (change_id,))
    audit(actor, "change.discarded", f"Verworpen: {ch['title']}", target=ch["target"], change_id=change_id)
    return get(change_id, db)


def apply(change_id: int, actor: str, *, automatic: bool = False, force: bool = False,
          db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    state.require_manage_mode()
    ch = get(change_id, db)
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
            raise HTTPException(409, detail="Automatisering staat op pauze of alleen-lezen")
    factory = APPLIERS.get(ch["target"])
    if factory is None:
        raise HTTPException(409, detail=f"Geen schrijfkoppeling voor '{ch['target']}'")
    try:
        applier = factory()
    except Exception as exc:
        raise HTTPException(409, detail=f"Koppeling '{ch['target']}' niet beschikbaar: {exc}") from exc

    ops = [dict(op) for op in ch["ops"]]
    # 1) drift detection on everything before writing anything
    for op in ops:
        current = applier.read(op["path"])
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
    # 2) write + verify, auto-rollback on failure
    written: list[dict[str, Any]] = []
    try:
        for op in ops:
            if op["observed_before"] == op["after"]:
                op["result"] = "al_ingesteld"
                continue
            applier.write(op["path"], op["after"])
            written.append(op)
            check = applier.read(op["path"])
            if check != op["after"]:
                raise RuntimeError(f"verificatie mislukt voor '{op['label']}': {check!r}")
            op["result"] = "toegepast"
    except Exception as exc:
        rollback_errors = []
        for op in reversed(written):
            try:
                applier.write(op["path"], op["observed_before"])
                op["result"] = "teruggedraaid"
            except Exception as rexc:
                rollback_errors.append(f"{op['label']}: {rexc}")
        err = f"{exc}" + (f" · herstel mislukt: {rollback_errors}" if rollback_errors else " · automatisch hersteld")
        db.execute("UPDATE change_sets SET status='failed', error=%s, ops=%s WHERE id=%s",
                   (err, jsonb(ops), change_id))
        audit(actor, "change.failed", f"Toepassen mislukt: {ch['title']}", target=ch["target"],
              change_id=change_id, detail={"error": err})
        if written:
            restored = [o["label"] for o in written if o.get("result") == "teruggedraaid"]
            audit("systeem", "change.auto_restored" if not rollback_errors else "change.auto_restore_failed",
                  (f"Automatisch hersteld na mislukte wijziging: {ch['title']}" if not rollback_errors
                   else f"Automatisch herstel deels mislukt: {ch['title']}"),
                  target=ch["target"], change_id=change_id,
                  detail={"restored": restored, "errors": rollback_errors,
                          "values": [{"label": o["label"], "back_to": o["observed_before"]} for o in written]})
        raise HTTPException(502, detail=f"Toepassen mislukt: {err}") from exc
    db.execute("UPDATE change_sets SET status='applied', applied_by=%s, applied_at=now(), ops=%s, error=NULL "
               "WHERE id=%s", (actor, jsonb(ops), change_id))
    audit(actor, "change.applied", f"Toegepast: {ch['title']}", target=ch["target"], change_id=change_id,
          detail={"ops": [{"label": o["label"], "before": o["observed_before"], "after": o["after"]} for o in ops],
                  "automatic": automatic})
    return get(change_id, db)


def rollback(change_id: int, actor: str, reason: str = "", db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    state.require_manage_mode()
    ch = get(change_id, db)
    if ch["status"] != "applied":
        raise HTTPException(409, detail="Alleen toegepaste wijzigingen kunnen worden teruggedraaid")
    applier = APPLIERS[ch["target"]]()
    ops = [dict(op) for op in ch["ops"]]
    errors = []
    # ``one_way``: cannot be undone but is not destructive (e.g. sending a release to a download client); allowed in
    # any risk class, skipped on rollback exactly like ``irreversible``.
    reversible = [op for op in ops if op.get("result") == "toegepast"
                  and not (op.get("irreversible") or op.get("one_way"))]
    if not reversible:
        raise HTTPException(409, detail="Deze wijziging is niet terug te draaien (bijv. verwijderde bestanden)")
    for op in reversed(ops):
        if op.get("result") != "toegepast":
            continue
        if op.get("irreversible") or op.get("one_way"):
            op["result"] = "niet_terug_te_draaien"
            continue
        try:
            applier.write(op["path"], op["observed_before"])
            op["result"] = "teruggedraaid"
        except Exception as exc:
            errors.append(f"{op['label']}: {exc}")
    if errors:
        db.execute("UPDATE change_sets SET error=%s, ops=%s WHERE id=%s", ("; ".join(errors), jsonb(ops), change_id))
        raise HTTPException(502, detail=f"Terugdraaien deels mislukt: {errors}")
    db.execute("UPDATE change_sets SET status='rolled_back', rolled_back_at=now(), ops=%s WHERE id=%s",
               (jsonb(ops), change_id))
    audit(actor, "change.rolled_back", f"Teruggedraaid: {ch['title']}" + (f" ({reason})" if reason else ""),
          target=ch["target"], change_id=change_id)
    return get(change_id, db)


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
