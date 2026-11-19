"""Change sets: preview → (approve) → apply → rollback for every write to an external system.

An *applier* is registered per target system (``sabnzbd``, ``qbittorrent``, ``prowlarr`` …) with:

- ``read(path) -> value``  — current value at an applier-specific path
- ``write(path, value)``   — set the value (must be idempotent)

Apply semantics:
1. Requires beheermodus (manage). ``toestemming_nodig`` requires prior approval by an admin.
2. For every op the current value is read first. If it differs from the previewed ``before`` the
   apply is refused (drift — somebody else, e.g. MediaGuard's own reconcile, changed it) unless forced.
3. MSO markeert elke schrijfpoging vóór verzending en bewaart het schrijfantwoord vóór het teruglezen.
   Automatisch herstel vereist een bevestigd schrijfantwoord en een overeenkomende actuele bronwaarde.
4. Voor afronding meet MSO alle eindwaarden, ook bij :func:`reconcile_interrupted` na een onderbreking.
   Een onleesbare eindwaarde blijft in het journaal voor de achtergrondcontrole.
5. Rollback (1.6.0) checks drift per op first: a value that is neither MSO's value nor the original any more was
   changed outside MSO and is skipped ("overgeslagen"), never overwritten. Every written op is read back; a failed
   read-back keeps the change ``applied`` with the error — there is no "teruggedraaid" without verification.
   A rollback by a person records an owner override for every op it put back, so Autopilot does not redo it on its
   next round ("Je hebt dit zelf teruggezet").
6. Status transitions are atomic: a per-change advisory lock (held for the whole apply/rollback, released by
   PostgreSQL when the process dies) plus ``UPDATE … WHERE status IN (…) RETURNING``.
7. Writing a value back that *removes* something at the source (``Applier.rollback_deletes``, e.g. deleting a
   SABnzbd category MSO created) is never done unattended: not by the restore after a failed apply, not by the
   reconciler, not by an automatic rollback. Only a person with the admin role can roll such an op back.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from httpx import TimeoutException
from psycopg import Connection

from mso import state
from mso.audit import audit
from mso.change_preview import JOURNAL_FIELDS, preview_hash
from mso.db import Database, get_db, jsonb
from mso.machine_keys import ADMIN_LOCK, RESTORE_PENDING, MachineKeyError, restore_pending
from mso.security import redact

LOGGER = logging.getLogger(__name__)
RISKS = ("advies", "automatisch_veilig", "toestemming_nodig")
OPEN_STATUSES = ("preview", "approved", "applying")  # dedupe: one open change per key
LOCK_CLASS = 727101  # pg_advisory_lock(LOCK_CLASS, change_id) — distinct from the migration lock (727001)
APPROVAL_TTL = timedelta(minutes=15)
RECONCILE_GRACE = timedelta(hours=1)  # source unreachable after a restart: keep trying this long, then report


class ChangeBusy(HTTPException):
    """Another request/worker is applying or rolling back this change right now (not a failure of the change)."""

    def __init__(self) -> None:
        super().__init__(409, detail="Deze wijziging wordt op dit moment al toegepast of teruggedraaid")


class ChangePrecondition(HTTPException):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(409, detail=detail)
        self.code = code


class ChangeUncertain(HTTPException):
    """De achtergrondcontrole bewaakt de bronactie tot bevestiging of overdracht aan de beheerder."""

    def __init__(self) -> None:
        super().__init__(502, detail="De bronactie is nog niet bevestigd. MSO controleert de uitkomst.")


class ChangeFailed(HTTPException):
    """Definitieve fout na het beginnen van een gejournaliseerde bronactie."""

    def __init__(self, detail: str) -> None:
        super().__init__(502, detail=redact(detail))


class SourceWriteRefused(RuntimeError):
    """De bronadapter heeft de actie geweigerd vóór verzending."""


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
    # (path, value) → writing ``value`` back removes something at the source (e.g. deleting a SABnzbd category):
    # never unattended (hard rule 1), and a person's rollback needs the admin role
    rollback_deletes: Callable[[str, Any], bool] | None = None


def deletes_on_write(applier: Applier | None, path: str, value: Any) -> bool:
    if applier is None or applier.rollback_deletes is None:
        return False
    try:
        return bool(applier.rollback_deletes(path, value))
    except Exception:
        return True  # unsure → treat as a removal (the safe side)


DELETE_NOTE = "niet automatisch teruggezet: terugzetten zou iets verwijderen; verwijder het zo nodig zelf"
NO_WRITE_ACK = "Het journaal bevat geen schrijfbevestiging van de bron."


def _acknowledged(op: dict[str, Any]) -> bool:
    return op.get("write_acknowledged") is True


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


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _short(value: Any, n: int = 120) -> str:
    return redact(repr(value))[:n]


def reversible(op: dict[str, Any]) -> bool:
    """An op MSO can write back (not ``irreversible`` deletion, not ``one_way`` like sending a download)."""
    return not (op.get("irreversible") or op.get("one_way"))


@contextmanager
def _lock(change_id: int, db: Database) -> Iterator[Connection]:
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
                guarded = conn.execute("SELECT pg_try_advisory_lock_shared(%s) AS ok", (ADMIN_LOCK,)).fetchone()
                if not guarded["ok"]:
                    raise HTTPException(503, detail="Sleutelbeheer of gegevensherstel is bezig",
                                        headers={"Retry-After": "1"})
                try:
                    if restore_pending(conn):
                        raise MachineKeyError(503, "dependency_unavailable", RESTORE_PENDING)
                    yield conn
                finally:
                    conn.execute("SELECT pg_advisory_unlock_shared(%s)", (ADMIN_LOCK,))
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
            return _fill_hash(existing, db)
    evaluate_after = datetime.now(UTC) + timedelta(hours=evaluate_after_h) if evaluate_after_h else None
    with db.tx() as conn:
        row = conn.execute(
            "INSERT INTO change_sets(target,title,explanation,risk,ops,impact,source,dedupe_key,created_by,"
            "evaluate_after) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (target, title, explanation, risk, jsonb(ops), jsonb(impact or {}), source, dedupe_key, actor,
             evaluate_after),
        ).fetchone()
        # PostgreSQL normaliseert JSONB-getallen. Hash de vorm die latere lezers werkelijk ontvangen.
        row["preview_hash"] = preview_hash(row["target"], row["risk"], row["ops"])
        conn.execute("UPDATE change_sets SET preview_hash=%s WHERE id=%s", (row["preview_hash"], row["id"]))
        audit(actor, "change.created", f"Wijzigingsvoorstel: {title}", target=target, change_id=row["id"],
              detail={"risk": risk, "ops": len(ops)}, conn=conn)
    return row


def _validator(target: str) -> Callable[[str, Any], None] | None:
    try:
        return APPLIERS[target]().validate
    except Exception:
        return None


def _fill_hash(row: dict[str, Any], db: Database) -> dict[str, Any]:
    # Historische rijen behouden hun status en goedkeuring. Vul de hash bij de eerste lezing.
    while row["preview_hash"] is None:
        value = preview_hash(row["target"], row["risk"], row["ops"])
        updated = db.fetchone(
            "UPDATE change_sets SET preview_hash=%s WHERE id=%s AND preview_hash IS NULL "
            "AND target=%s AND risk=%s AND ops=%s RETURNING *",
            (value, row["id"], row["target"], row["risk"], jsonb(row["ops"])),
        )
        if updated:
            return updated
        row = db.fetchone("SELECT * FROM change_sets WHERE id=%s", (row["id"],))
        if row is None:
            raise HTTPException(404, detail="Wijziging niet gevonden")
    return row


def get(change_id: int, db: Database | None = None, *, persist_hash: bool = True) -> dict[str, Any]:
    db = db or get_db()
    row = db.fetchone("SELECT * FROM change_sets WHERE id=%s", (change_id,))
    if row is None:
        raise HTTPException(404, detail="Wijziging niet gevonden")
    if not persist_hash and row["preview_hash"] is None:
        row["preview_hash"] = preview_hash(row["target"], row["risk"], row["ops"])
        return row
    return _fill_hash(row, db)


def check_preview(ch: dict[str, Any], expected: str) -> None:
    if expected != ch["preview_hash"] or expected != preview_hash(ch["target"], ch["risk"], ch["ops"]):
        raise ChangePrecondition("preview_changed", "De voorvertoning is gewijzigd. Vraag een nieuw voorstel aan.")


def check_approval(ch: dict[str, Any], *, actor: str | None = None) -> None:
    if actor is not None and (ch["approved_by"] != actor or ch["approval_preview_hash"] is None):
        raise ChangePrecondition("approval_required", "Een gebonden goedkeuring door deze sleutel is vereist.")
    if ch["approval_preview_hash"] is not None:
        check_preview(ch, ch["approval_preview_hash"])
        if ch["approval_expires_at"] is None:
            raise ChangePrecondition("approval_expired", "De goedkeuring heeft geen geldige vervaltermijn.")
    if ch["approval_expires_at"] is not None and ch["approval_expires_at"] <= _now():
        raise ChangePrecondition("approval_expired", "De gebonden goedkeuring is verlopen.")


def _update(db: Database, change_id: int, sql_set: str, params: tuple[Any, ...], allowed: tuple[str, ...],
            *, conn: Connection | None = None) -> dict[str, Any] | None:
    statement = f"UPDATE change_sets SET {sql_set} WHERE id=%s AND status = ANY(%s) RETURNING *"
    args = (*params, change_id, list(allowed))
    return conn.execute(statement, args).fetchone() if conn is not None else db.fetchone(statement, args)


def _transition(db: Database, change_id: int, sql_set: str, params: tuple[Any, ...], allowed: tuple[str, ...]) -> bool:
    return _update(db, change_id, sql_set, params, allowed) is not None


def _conflict(change_id: int, db: Database) -> HTTPException:
    status = get(change_id, db, persist_hash=False)["status"]
    return HTTPException(409, detail=f"Wijziging heeft status '{status}'")


def _finish(db: Database, ch: dict[str, Any], sql_set: str, params: tuple[Any, ...], allowed: tuple[str, ...],
            actor: str, action: str, summary: str, *, detail: dict[str, Any] | None = None,
            request_id: UUID | None = None, reserve: Callable[[Connection], None] | None = None) -> dict[str, Any]:
    from mso.change_requests import complete

    with db.tx() as conn:
        if reserve:
            reserve(conn)
        result = _update(db, ch["id"], sql_set + ", preview_hash=COALESCE(preview_hash,%s)",
                         (*params, ch["preview_hash"]), allowed, conn=conn)
        if result is None:
            raise HTTPException(409, detail="De toestand van de wijziging is inmiddels gewijzigd")
        audit(actor, action, summary, target=ch["target"], change_id=ch["id"], detail=detail, conn=conn)
        if request_id is not None:
            complete(conn, request_id, result)
    return result


def approve(change_id: int, actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    with _lock(change_id, db):
        return approve_locked(get(change_id, db, persist_hash=False), actor, db)


def approve_locked(ch: dict[str, Any], actor: str, db: Database, *, expected: str | None = None,
                   request_id: UUID | None = None,
                   reserve: Callable[[Connection], None] | None = None) -> dict[str, Any]:
    if expected is not None:
        check_preview(ch, expected)
    if ch["status"] != "preview":
        raise _conflict(ch["id"], db)
    return _finish(db, ch, "status='approved', approved_by=%s, approval_preview_hash=%s, approval_expires_at=%s",
                   (actor, expected, _now() + APPROVAL_TTL if expected is not None else None), ("preview",),
                   actor, "change.approved", f"Goedgekeurd: {ch['title']}", request_id=request_id, reserve=reserve)


def discard(change_id: int, actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    ch = get(change_id, db)
    if ch["status"] not in ("preview", "approved") or not _transition(db, change_id, "status='discarded'", (),
                                                                      ("preview", "approved")):
        raise _conflict(change_id, db)
    audit(actor, "change.discarded", f"Verworpen: {ch['title']}", target=ch["target"], change_id=change_id)
    return get(change_id, db)


def _check_applicable(ch: dict[str, Any], automatic: bool, verified_approval: bool = False) -> None:
    if ch["status"] not in ("preview", "approved"):
        raise HTTPException(409, detail=f"Wijziging heeft status '{ch['status']}'")
    if ch["risk"] == "toestemming_nodig" and ch["status"] != "approved" and not verified_approval:
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
        if deletes_on_write(applier, str(op["path"]), op.get("observed_before")):
            op["result"] = "toegepast"
            op["restore_note"] = DELETE_NOTE
            continue
        try:
            current = applier.read(op["path"])
            if not _same(applier, op["path"], current, op.get("observed_after", op["after"])):
                op.update(result="overgeslagen", skip_reason="buiten MSO gewijzigd", current=current,
                          checked_at=_now_iso())
                continue
            if not _acknowledged(op):
                op.update(result="onduidelijk", restore_note=NO_WRITE_ACK)
                continue
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


def _read_apply_value(applier: Applier, op: dict[str, Any]) -> Any:
    value = applier.read(op["path"])
    if value is None and type(op.get("before")) is int and type(op["after"]) is int:
        raise ValueError("De bronmeting bevat geen waarde voor de limiet.")
    return value


def _write_and_read(applier: Applier, op: dict[str, Any], persist: Callable[[], None]) -> Any:
    """Ook bij een verloren schrijfantwoord bepaalt de teruggelezen waarde het resultaat."""
    path = op["path"]
    try:
        applier.write(path, op["after"])
    except SourceWriteRefused:
        raise
    except Exception as exc:
        op["write_acknowledged"] = False
        try:
            observed = _read_apply_value(applier, op)
        except Exception:
            raise ChangeUncertain() from exc
        if _same(applier, path, observed, op["after"]) and reversible(op):
            return observed
        timed_out = isinstance(exc, (TimeoutError, TimeoutException)) or getattr(exc, "code", None) == "timeout"
        if not timed_out and reversible(op) and _same(applier, path, observed, op["observed_before"]):
            raise
        raise ChangeUncertain() from exc
    op["write_acknowledged"] = True
    persist()
    try:
        return _read_apply_value(applier, op)
    except Exception as exc:
        raise ChangeUncertain() from exc


def apply(change_id: int, actor: str, *, automatic: bool = False, force: bool = False,
          db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    state.require_manage_mode()
    with _lock(change_id, db):
        return apply_locked(get(change_id, db, persist_hash=False), actor, db, automatic=automatic, force=force)


def apply_locked(ch: dict[str, Any], actor: str, db: Database, *, automatic: bool = False, force: bool = False,
                 verifies_approval: Callable[[], None] | None = None,
                 request_id: UUID | None = None, reserve: Callable[[Connection], None] | None = None) -> dict[str, Any]:
    state.require_manage_mode()
    _check_applicable(ch, automatic, verifies_approval is not None)
    check_deadline = verifies_approval or (lambda: check_approval(ch))
    check_deadline()
    change_id = ch["id"]
    factory = APPLIERS.get(ch["target"])
    if factory is None:
        raise HTTPException(409, detail=f"Geen schrijfkoppeling voor '{ch['target']}'")
    try:
        applier = factory()
    except Exception as exc:
        raise HTTPException(409, detail=f"Koppeling '{ch['target']}' niet beschikbaar: {redact(str(exc))[:300]}") \
            from exc

    ops = [{key: value for key, value in op.items() if key not in JOURNAL_FIELDS} for op in ch["ops"]]
    # 1) drift detection on everything before writing anything
    for op in ops:
        try:
            current = _read_apply_value(applier, op)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, detail=(f"Huidige waarde van '{op['label']}' niet te lezen: "
                                             f"{redact(str(exc))[:300]}")) from exc
        op.update(observed_before=current, write_started=False)
        if current == op["after"]:
            op["result"] = "al_ingesteld"
        if applier.validate:
            try:
                applier.validate(op["path"], op["after"])
            except ValueError as exc:
                raise HTTPException(422, detail=f"Beleid weigert '{op['label']}': {exc}") from exc
        if not force and op.get("before") is not None and current != op["before"] and current != op["after"]:
            raise HTTPException(409, detail=f"Afwijking gedetecteerd bij '{redact(op['label'])}'. "
                                "De huidige bronwaarde wijkt af van de voorvertoning. Maak een nieuw voorstel.")
    # Reservering en journaal worden samen zichtbaar, pas na alle broncontroles.
    with db.tx() as conn:
        if reserve:
            reserve(conn)
        if not _update(db, change_id, "status='applying', ops=%s, apply_started_at=now(), applied_by=%s, "
                       "applied_auto=%s, apply_request_id=%s, preview_hash=COALESCE(preview_hash,%s), error=NULL",
                       (jsonb(ops), actor, automatic, request_id, ch["preview_hash"]),
                       ("preview", "approved"), conn=conn):
            raise _conflict(change_id, db)
        check_deadline()
    # 3) write + verify one op at a time; each result is stored right away
    def persist() -> None:
        _persist_ops(db, change_id, ops, "applying")

    try:
        for op in ops:
            if op.get("result") == "al_ingesteld":
                continue
            op["write_started"] = True
            persist()
            check = _write_and_read(applier, op, persist)
            if not _same(applier, op["path"], check, op["after"]):
                raise RuntimeError(f"verificatie mislukt voor '{op['label']}': {check!r}")
            op.update(result="toegepast", observed_after=check, verified_at=_now_iso())
            persist()
    except ChangeUncertain:
        op["result"] = "onbevestigd"
        persist()
        audit(actor, "change.unconfirmed", f"Uitkomst van de bronactie nog onbekend: {ch['title']}",
              target=ch["target"], change_id=change_id)
        raise
    except Exception as exc:
        written = [o for o in ops if _acknowledged(o) or o.get("result") == "toegepast"]
        rollback_errors = _restore(applier, written)
        kept = [op for op in written if op.get("restore_note")]
        restored = [o["label"] for o in written if o.get("result") == "teruggedraaid"]
        skipped = [o["label"] for o in written if o.get("result") == "overgeslagen"]
        err = str(exc)
        if rollback_errors:
            err += f" · herstel mislukt: {rollback_errors}"
        elif restored:
            err += " · automatisch hersteld" if not kept else " · terugdraaibare waarden hersteld"
        for op in kept:
            err += f" · {op['label']} blijft staan: {op['restore_note']}"
        if skipped:
            err += f" · buiten MSO gewijzigd en overgeslagen: {', '.join(skipped)}"
        err = redact(err)
        db.execute("UPDATE change_sets SET status='failed', error=%s, ops=%s, applied_by=NULL, applied_auto=false "
                   "WHERE id=%s AND status='applying'", (err, jsonb(ops), change_id))
        if any(o.get("result") == "onduidelijk" for o in written):
            _waiting_item(ch, f"Wijziging controleren: {ch['title']}", NO_WRITE_ACK, "onduidelijk", db)
        audit(actor, "change.failed", f"Toepassen mislukt: {ch['title']}", target=ch["target"],
              change_id=change_id, detail={"error": err, "automatic": automatic})
        if restored or rollback_errors:
            audit("systeem", "change.auto_restored" if not rollback_errors else "change.auto_restore_failed",
                  (f"Automatisch hersteld na mislukte wijziging: {ch['title']}" if not rollback_errors
                   else f"Automatisch herstel deels mislukt: {ch['title']}"),
                  target=ch["target"], change_id=change_id,
                  detail={"restored": restored, "errors": rollback_errors, "skipped": skipped,
                          "values": [{"label": o["label"], "back_to": o["observed_before"]} for o in written
                                     if o.get("result") == "teruggedraaid"]})
        raise ChangeFailed(f"Toepassen mislukt: {err}") from exc
    outcome = _settle_apply(get(change_id, db), db, applier)
    if outcome in ("wacht", "wacht_op_beheermodus"):
        raise ChangeUncertain()
    result = get(change_id, db)
    if outcome != "afgerond":
        raise ChangeFailed(result["error"])
    return result


# ------------------------------------------------------------------ rollback
def _record_override(ch: dict[str, Any], op: dict[str, Any], current: Any) -> None:
    """A value MSO set was changed outside MSO: never re-apply that path automatically for a while."""
    try:
        from mso.features.automation import autopilot, overrides

        overrides.record(ch["target"], str(op["path"]), mso_value=op.get("after"), current_value=current,
                         change_id=ch["id"], days=autopilot.override_days())
    except Exception:  # automation module disabled: rollback itself must not fail on this
        LOGGER.debug("override niet vastgelegd", exc_info=True)


def is_person(actor: str, db: Database | None = None) -> bool:
    """A logged-in person (a row in ``users``), not MSO itself (automatisering, autopilot:*, systeem …)."""
    try:
        return bool((db or get_db()).scalar("SELECT 1 FROM users WHERE username=%s", (actor,)))
    except Exception:
        return False


def rollback(change_id: int, actor: str, reason: str = "", db: Database | None = None, *,
             allow_delete: bool = False, automatic: bool = False) -> dict[str, Any]:
    """Write ``observed_before`` back for every applied, reversible op (reverse order).

    Per op: read the current value first — still MSO's value → write back + read back (verified); already the
    original → nothing to write (verified); anything else → changed outside MSO: skipped ("overgeslagen"), never
    overwritten (and recorded as an owner override). A failed write or read-back keeps the change ``applied`` with
    the error (502). Nothing written back because everything drifted → :class:`RollbackSkipped` (409).

    An op whose write-back removes something (``Applier.rollback_deletes``) needs ``allow_delete`` (a person with
    the admin role); otherwise 403 and nothing is written. Ops a person put back become an owner override."""
    db = db or get_db()
    state.require_manage_mode()
    with _lock(change_id, db):
        ch = get(change_id, db, persist_hash=False)
        if automatic and any(op.get("result") == "toegepast" and reversible(op) and not _acknowledged(op)
                             for op in ch["ops"]):
            raise HTTPException(409, detail=NO_WRITE_ACK + " Controleer de wijziging handmatig.")
        return rollback_locked(ch, actor, db, reason=reason, allow_delete=allow_delete)


def _checked_rollback_value(applier: Applier, op: dict[str, Any], change_id: int, resuming: bool) -> Any:
    from mso.connector_api.errors import ConnectorError

    path = op["path"]
    try:
        current = applier.read(path)
    except Exception as exc:
        raise HTTPException(502, "De bronwaarde voor terugdraaien kon niet worden gecontroleerd.") from exc
    expected = (op.get("observed_before") if resuming and op.get("result") == "teruggedraaid"
                else op.get("observed_after", op["after"]))
    if _same(applier, path, current, expected) or resuming and _same(applier, path, current, op.get("observed_before")):
        return current
    LOGGER.warning("change.rollback_refused wijziging=%s reden=bronafwijking", change_id)
    raise ConnectorError(412, "precondition_failed")


def rollback_locked(ch: dict[str, Any], actor: str, db: Database, *, reason: str = "", allow_delete: bool = False,
                    request_id: UUID | None = None, check_deadline: Callable[[], None] | None = None,
                    reserve: Callable[[Connection], None] | None = None) -> dict[str, Any]:
    state.require_manage_mode()
    change_id = ch["id"]
    if ch["status"] != "applied":
        raise HTTPException(409, detail="Alleen toegepaste wijzigingen kunnen worden teruggedraaid")
    previous = ch["rollback_request_id"]
    resuming = previous is not None and db.scalar(
        "SELECT response IS NULL FROM connector_idempotency WHERE request_id=%s", (previous,))
    if resuming and check_deadline is not None and previous != request_id:
        raise ChangePrecondition("idempotency_in_progress", "Een eerdere terugdraaiing is nog bezig.")
    if not any(reversible(op) and (op.get("result") == "toegepast" or
                                   resuming and op.get("result") == "teruggedraaid") for op in ch["ops"]):
        raise HTTPException(409, detail="Deze wijziging is niet terug te draaien (bijv. verwijderde bestanden)")
    factory = APPLIERS.get(ch["target"])
    if factory is None:
        raise HTTPException(409, detail=f"Geen schrijfkoppeling voor '{ch['target']}'")
    try:
        applier = factory()
    except Exception as exc:
        raise HTTPException(409, detail=f"Koppeling '{ch['target']}' niet beschikbaar: {redact(str(exc))[:300]}") \
            from exc
    deleting = [op["label"] for op in ch["ops"] if op.get("result") == "toegepast" and reversible(op)
                and deletes_on_write(applier, str(op["path"]), op.get("observed_before"))]
    if deleting and not allow_delete:
        raise HTTPException(403, detail=(
            f"Terugdraaien verwijdert hier iets ({', '.join(deleting)[:200]}); dat mag alleen een beheerder, "
            "en nooit automatisch"))
    person = is_person(actor, db)
    ops = [dict(op) for op in ch["ops"]]
    if check_deadline is not None:
        # De handtekening bindt de hele wijziging. Een afwijking vóór de uitvoering weigert het hele verzoek.
        for op in ops:
            current = _checked_rollback_value(applier, op, change_id, resuming)
            op.setdefault("observed_after", current)
    start_request = request_id is not None or check_deadline is not None
    errors: list[str] = []
    skipped = [op for op in ops if resuming and op.get("result") == "overgeslagen"]
    for op in reversed(ops):
        was_reverted = resuming and op.get("result") == "teruggedraaid"
        if op.get("result") != "toegepast" and not was_reverted:
            continue
        can_revert = reversible(op)
        noop = can_revert and applier.noop_rollback is not None and applier.noop_rollback(str(op["path"]))
        if start_request and check_deadline is not None:
            current = _checked_rollback_value(applier, op, change_id, resuming)
        elif can_revert and not noop:
            try:
                current = applier.read(op["path"])
            except Exception as exc:
                op["rollback_error"] = f"huidige waarde niet te lezen: {redact(str(exc))[:200]}"
                errors.append(f"{op['label']}: {op['rollback_error']}")
                _persist_ops(db, change_id, ops, "applied")
                continue
        if start_request:
            with db.tx() as conn:
                if reserve:
                    reserve(conn)
                if request_id is not None:
                    conn.execute("UPDATE change_sets SET rollback_request_id=%s WHERE id=%s", (request_id, change_id))
                if check_deadline is not None:
                    check_deadline()
            start_request = False
        if not can_revert:
            op["result"] = "niet_terug_te_draaien"
            _persist_ops(db, change_id, ops, "applied")
            continue
        if noop:
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
        if _same(applier, op["path"], current, op.get("observed_before")):
            op.update(result="teruggedraaid", verified_at=_now_iso(), rollback_note="was al de oude waarde")
        elif was_reverted or not _same(applier, op["path"], current, op["after"]):
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
    if person:  # the person's own decision: Autopilot must not redo it on its next round
        for op in reverted:
            _record_override(ch, op, op.get("observed_before"))
    skip_line = "; ".join(f"{o['label']}: nu {_short(o.get('current'), 60)}" for o in skipped)
    if errors:
        db.execute("UPDATE change_sets SET error=%s, ops=%s WHERE id=%s AND status='applied'",
                   ("; ".join(errors)[:1000], jsonb(ops), change_id))
        audit(actor, "change.rollback_failed", f"Terugdraaien niet gelukt: {ch['title']}", target=ch["target"],
              change_id=change_id, detail={"errors": errors, "reverted": [o["label"] for o in reverted],
                                           "skipped": [o["label"] for o in skipped]})
        raise ChangeFailed(f"Terugdraaien deels mislukt: {errors}")
    if not reverted:
        msg = f"Niets teruggedraaid: buiten MSO gewijzigd ({skip_line}); MSO overschrijft dat niet"
        db.execute("UPDATE change_sets SET error=%s, ops=%s WHERE id=%s AND status='applied'",
                   (msg[:1000], jsonb(ops), change_id))
        audit(actor, "change.rollback_skipped", f"Niet teruggedraaid (buiten MSO gewijzigd): {ch['title']}",
              target=ch["target"], change_id=change_id,
              detail={"skipped": [{"label": o["label"], "now": o.get("current")} for o in skipped]})
        raise RollbackSkipped(msg, skipped)
    return _finish(db, ch, "status='rolled_back', rolled_back_at=now(), ops=%s, error=%s",
                   (jsonb(ops), f"Overgeslagen (buiten MSO gewijzigd): {skip_line}"[:1000] if skipped else None),
                   ("applied",), actor, "change.rolled_back", f"Teruggedraaid: {ch['title']}"
                   + (f" ({reason})" if reason else ""),
                   detail={"verified": [o["label"] for o in reverted],
                           "skipped": [{"label": o["label"], "now": o.get("current")} for o in skipped]},
                   request_id=request_id)


# ------------------------------------------------------------------ crash recovery
def _waiting_item(ch: dict[str, Any], title: str, explanation: str, outcome: str, db: Database, *,
                  once: bool = False) -> None:
    """One "Wacht op jou" item per interrupted change (recommendation without change link, so the inbox lists it).

    ``once``: a waiting state the reconciler sees every round (alleen-lezen) — create the item the first time only
    (no refresh every 2 minutes, no re-opening after the owner dismissed it). Otherwise (the final outcome) the item
    is refreshed and opened again, so the outcome is shown even when the waiting item was dismissed."""
    from mso.domain.outcomes import recommend

    key = f"change.interrupted:{ch['id']}"
    if once and db.scalar("SELECT 1 FROM recommendations WHERE dedupe_key=%s", (key,)):
        return
    area = (ch.get("impact") or {}).get("area") if isinstance(ch.get("impact"), dict) else None
    rec_id = recommend(area or "automation", title, explanation, risk="toestemming_nodig", subject=ch["target"],
                       dedupe_key=key,
                       evidence={"change_id": ch["id"], "outcome": outcome, "kind": "change_interrupted"}, db=db)
    if not once:
        db.execute("UPDATE recommendations SET status='open', updated_at=now() WHERE id=%s AND status<>'open'",
                   (rec_id,))


def _resolve_item(change_id: int, db: Database) -> None:
    db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s AND status='open'",
               (f"change.interrupted:{change_id}",))


def _measure_apply(applier: Applier, ops: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Meet de eindwaarden in één ronde. Een meting bewijst de waarde, een schrijfantwoord de herkomst."""
    pending: list[str] = []
    missing_proof: list[str] = []
    for op in ops:
        path = op["path"]
        preset = op.get("result") == "al_ingesteld" or (
            "observed_before" in op and op["observed_before"] == op["after"]
        )
        if not preset:
            if op.get("write_started") is False:
                op["result"] = "niet_uitgevoerd"
                continue
            if "write_acknowledged" not in op:
                # Een oud schrijfresultaat kan na een verloren antwoord zijn afgeleid uit een bronlezing.
                if op.get("result") == "onbevestigd" or (
                    op.get("result") == "toegepast" and op.get("verified_at") and not op.get("checked_at")
                ):
                    op["write_acknowledged"] = False
                else:
                    op["result"] = "onduidelijk"
                    reason = f"{op['label']}: {NO_WRITE_ACK}"
                    if op.get("write_started") is True:
                        missing_proof.append(reason)
                    else:
                        pending.append(reason)
                    continue
        try:
            current = _read_apply_value(applier, op)
        except Exception as exc:
            op.pop("observed_after", None)
            op.pop("verified_at", None)
            op.pop("checked_at", None)
            pending.append(f"{op['label']}: {redact(str(exc))[:160]}")
            continue
        expected = op["observed_after"] if op.get("result") == "toegepast" and "observed_after" in op else op["after"]
        op.update(checked_at=_now_iso(), observed_after=current)
        if _same(applier, path, current, op["after"]) and _same(applier, path, current, expected):
            op["result"] = "al_ingesteld" if preset else "toegepast"
            op["verified_at"] = op["checked_at"]
        elif op.get("result") == "onbevestigd":
            pending.append(f"{op['label']}: bronactie nog niet bevestigd")
        elif not preset and _same(applier, path, current, op.get("observed_before")):
            op["result"] = "niet_toegepast"
        else:
            op.update(result="afwijkend", current=current)
    return pending, missing_proof


def has_apply_readback(ops: list[dict[str, Any]]) -> bool:
    return bool(ops) and all(
        op.get("checked_at") and "observed_after" in op and op.get("result") in ("toegepast", "al_ingesteld")
        for op in ops
    )


def _settle_apply(ch: dict[str, Any], db: Database, applier: Applier | None = None) -> str:
    """Rond de uitvoering onder het wijzigingsslot af, direct of na een onderbreking."""
    interrupted = applier is None
    ops = [dict(op) for op in ch["ops"]]
    title = ch["title"]
    try:
        if applier is None:
            applier = APPLIERS[ch["target"]]()
    except Exception as exc:
        pending = [f"koppeling niet beschikbaar: {redact(str(exc))[:160]}"]
        missing_proof = []
    else:
        pending, missing_proof = _measure_apply(applier, ops)
    if pending:
        started = ch.get("apply_started_at") or ch["created_at"]
        if _now() - started <= RECONCILE_GRACE:
            _persist_ops(db, ch["id"], ops, "applying")
            return "wacht"
    if pending or missing_proof:
        reason = "Uitkomst na één uur niet te controleren. " if pending else ""
        err = redact(reason + "; ".join(pending + missing_proof))
        _waiting_item(ch, f"Onderbroken wijziging controleren: {title}",
                      f"{err} Controleer de instellingen zelf en maak zo nodig een nieuw voorstel.", "onduidelijk", db)
        db.execute("UPDATE change_sets SET status='failed', error=%s, ops=%s WHERE id=%s AND status='applying'",
                   (err[:1000], jsonb(ops), ch["id"]))
        return "onduidelijk"
    if has_apply_readback(ops):
        detail = {"ops": [{"label": o["label"], "before": o.get("observed_before"), "after": o["observed_after"]}
                          for o in ops], "automatic": bool(ch.get("applied_auto"))}
        summary = f"Toegepast: {title}"
        if interrupted:
            summary = f"Toegepast (afgerond na onderbreking): {title}"
            detail["interrupted"] = True
        _finish(db, ch, "status='applied', applied_at=now(), ops=%s, error=NULL", (jsonb(ops),), ("applying",),
                ch.get("applied_by") or "systeem", "change.applied", summary, detail=detail,
                request_id=ch["apply_request_id"])
        if interrupted:
            _resolve_item(ch["id"], db)
        return "afgerond"
    unclear = [f"{o['label']}: nu {_short(o['current'], 60)}" for o in ops if o.get("result") == "afwijkend"]
    written = [op for op in ops if op.get("result") == "toegepast"]
    restorable = [op for op in written if _acknowledged(op) and reversible(op)
                  and not deletes_on_write(applier, str(op["path"]), op.get("observed_before"))]
    if restorable and state.mode() not in ("manage", "autopilot"):
        # something MSO can put back was written, but alleen-lezen never writes: wait (one item) for beheermodus.
        # Nothing reversible written (cut off before the first write, or only one_way ops) → handled below
        # without any write, so the change does not block its dedupe key in 'applying'.
        _persist_ops(db, ch["id"], ops, "applying")
        _waiting_item(ch, f"Onderbroken wijziging wacht op beheermodus: {title}",
                      f"MSO werd herstart terwijl '{title}' werd toegepast; een deel is al ingesteld. In alleen-lezen "
                      "schrijft MSO niets, ook niet om dat terug te zetten. Zet beheermodus aan: dan zet MSO de al "
                      "geschreven waarden terug naar hoe ze waren.", "wacht_op_beheermodus", db, once=True)
        return "wacht_op_beheermodus"
    errors = _restore(applier, written) if applier is not None else []
    unclear.extend(o["label"] for o in written if o.get("result") == "overgeslagen")
    unowned = [o["label"] for o in written if o.get("result") == "onduidelijk"]
    for op in ops:
        if op.get("result") == "niet_toegepast":
            op["result"] = "niet_uitgevoerd"
    # one_way: cannot be undone; a write-back that would delete: never unattended (a person decides)
    kept = [o["label"] for o in written if o.get("result") == "toegepast" and o.get("restore_note") != DELETE_NOTE]
    kept_delete = [o["label"] for o in written if o.get("result") == "toegepast"
                   and o.get("restore_note") == DELETE_NOTE]
    parts = ["Onderbroken tijdens toepassen (herstart)" if interrupted else "Eindcontrole van de wijziging mislukt"]
    if not written and not unclear:
        parts.append("onderbroken vóór de eerste schrijfactie; niets ingesteld")
    if any(o.get("result") == "teruggedraaid" for o in written):
        parts.append("al geschreven waarden teruggezet en gecontroleerd")
    if kept:
        parts.append(f"niet terug te draaien en blijft: {', '.join(kept)}")
    if kept_delete:
        parts.append("blijft staan, want terugzetten zou iets verwijderen (MSO verwijdert nooit zonder jou; doe "
                     f"dat zo nodig zelf): {', '.join(kept_delete)}")
        kept = [*kept, *kept_delete]
    if errors:
        parts.append(f"terugzetten mislukt: {'; '.join(errors)}")
    if unclear:
        parts.append(f"buiten MSO gewijzigd, niet aangeraakt: {'; '.join(unclear)}")
    if unowned:
        parts.append(f"{NO_WRITE_ACK} Controleer {', '.join(unowned)}.")
    err = " · ".join(parts)
    db.execute("UPDATE change_sets SET status='failed', error=%s, ops=%s WHERE id=%s AND status='applying'",
               (err[:1000], jsonb(ops), ch["id"]))
    outcome = "deels" if (errors or unclear or kept_delete) else ("niets_ingesteld" if not written else "teruggezet")
    if unowned:
        outcome = "onduidelijk"
    if errors or unclear or unowned or kept or not ch.get("applied_auto"):
        _waiting_item(ch, f"Wijziging onderbroken: {title}",
                      f"{err}. "
                      + ("Controleer de genoemde instellingen." if errors or unclear or unowned
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
                outcome = _settle_apply(ch, db)
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
    return [_fill_hash(row, db) for row in db.fetchall(sql, params)]
