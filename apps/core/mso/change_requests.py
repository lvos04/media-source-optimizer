"""Idempotentie voor de bestaande change sets, onder hetzelfde wijzigingsslot."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg import Connection

from mso import changes
from mso.change_preview import digest, public_change
from mso.connector_api.errors import is_connector
from mso.db import Database, get_db, jsonb
from mso.machine_keys import RESTORE_PENDING, restore_pending


class ChangeRequestError(HTTPException):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(status, detail)
        self.code = code


def parse_request_id(values: list[str], *, required: bool) -> UUID | None:
    if not values:
        if required:
            raise ChangeRequestError(400, "idempotency_key_required", "Idempotency-Key ontbreekt.")
        return None
    try:
        if len(values) == 1:
            key = UUID(values[0])
            if str(key) == values[0].lower():
                return key
    except ValueError:
        pass
    raise ChangeRequestError(400, "idempotency_key_invalid", "Idempotency-Key moet één UUID zijn.")


@dataclass(frozen=True)
class ChangeRequest:
    request_id: UUID
    change_id: int
    route: str
    actor: str
    body_hash: str
    nonce: UUID | None = None
    approval_nonce: UUID | None = None

    def saved(self, db: Database) -> dict[str, Any] | None:
        row = db.fetchone(
            "SELECT change_id,route,actor,body_hash,response FROM connector_idempotency WHERE request_id=%s",
            (self.request_id,),
        )
        if row is not None and (row["route"], row["change_id"], row["actor"], row["body_hash"]) != (
            self.route, self.change_id, self.actor, self.body_hash
        ):
            raise ChangeRequestError(422, "idempotency_key_reused", "Deze Idempotency-Key hoort bij een ander verzoek.")
        return row

    def reserve(self, db: Database) -> None:
        inserted = db.execute(
            "INSERT INTO connector_idempotency(request_id,change_id,route,actor,body_hash,nonce,approval_nonce) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (self.request_id, self.change_id, self.route, self.actor, self.body_hash, self.nonce, self.approval_nonce),
        )
        if not inserted and self.saved(db) is None:
            raise ChangeRequestError(409, "nonce_reused", "De nonce van dit uitvoeringsverzoek is al verbruikt.")


def resource_etag(row: dict[str, Any]) -> str:
    applied_at = row.get("applied_at")
    if applied_at is None:
        return row["preview_hash"]
    return digest({
        "change_id": row["id"], "applied_at": applied_at.astimezone(UTC).isoformat(),
        "preview_hash": row["preview_hash"],
        "observed_before": [op.get("observed_before") for op in row["ops"]],
    })


def connector_result(row: dict[str, Any]) -> dict[str, Any]:
    rollback = row["status"] == "rolled_back"
    before = [op.get("observed_before") for op in row["ops"]]
    after = [op.get("observed_after", op["after"]) for op in row["ops"]]
    if rollback:
        before, after = after, [
            op.get("current") if op.get("result") == "overgeslagen"
            else op.get("observed_before") if op.get("result") == "teruggedraaid" else op["after"]
            for op in row["ops"]
        ]
    return {
        "change_id": row["id"], "status": row["status"], "preview_hash": row["preview_hash"],
        "resource_etag": resource_etag(row),
        "before_hash": digest(before), "after_hash": digest(after),
        "readback": {"verified": all(op.get("result") != "overgeslagen" for op in row["ops"]),
                     "results": [op.get("result") for op in row["ops"]]},
    }


def change_view(row: dict[str, Any]) -> dict[str, Any]:
    return {**public_change(row), "resource_etag": resource_etag(row)}


def complete(conn: Connection, request_id: UUID, row: dict[str, Any]) -> None:
    request = conn.execute("SELECT route FROM connector_idempotency WHERE request_id=%s", (request_id,)).fetchone()
    if request is None:
        return
    result = connector_result(row) if is_connector(request["route"]) else change_view(row)
    conn.execute("UPDATE connector_idempotency SET response=%s WHERE request_id=%s",
                 (jsonb({"status": 200, "body": jsonable_encoder(result)}), request_id))


def replay(saved: dict[str, Any]) -> JSONResponse:
    return JSONResponse(saved["body"], status_code=saved["status"], headers={"Idempotent-Replay": "true"})


def run(
    request: Request, change_id: int, actor: str, body: Any, request_id: UUID | None,
    check: Callable[[Connection, dict[str, Any]], None],
    execute: Callable[[Database, dict[str, Any]], dict[str, Any]],
    *, nonce: UUID | None = None, approval_nonce: UUID | None = None, guard_lock: int | None = None,
) -> dict[str, Any] | JSONResponse:
    db = get_db()
    operation = (ChangeRequest(request_id, change_id, request.url.path if is_connector(request.url.path)
                               else request.url.path.rsplit("/", 1)[-1], actor, digest(body), nonce, approval_nonce)
                 if request_id is not None else None)
    try:
        with changes._lock(change_id, db, guard_lock=guard_lock) as conn:
            if guard_lock is not None and restore_pending(conn):
                raise ChangeRequestError(503, "dependency_unavailable", RESTORE_PENDING)
            saved = operation.saved(db) if operation else None
            ch = changes.get(change_id, db)
            check(conn, ch)
            if saved and saved["response"] is not None:
                if saved["response"].get("invalidated"):
                    raise ChangeRequestError(409, "conflict", "Maak na het gegevensherstel een nieuw verzoek aan.")
                return replay(saved["response"])
            if operation is not None and not saved:
                operation.reserve(db)
            if ch["status"] == "applying" and ch["apply_request_id"] == request_id and request_id is not None:
                raise ChangeRequestError(409, "idempotency_in_progress", "Wacht op het herstel van de wijziging.")
            try:
                result = execute(db, ch)
            except HTTPException as exc:
                if operation is None or isinstance(exc, changes.ChangeBusy) or exc.status_code >= 503:
                    raise
                # Bewaar ook een definitieve weigering na het reserveren, met hetzelfde openbare foutformaat.
                import json

                from mso.app import _error

                default = {403: "forbidden", 404: "not_found", 409: "conflict", 412: "precondition_failed",
                           422: "validation_error", 502: "upstream_error"}
                response = _error(exc.status_code, exc.detail, getattr(exc, "code", default.get(exc.status_code, "")),
                                  request, safe_details=getattr(exc, "safe_details", None), headers=exc.headers)
                db.execute("UPDATE connector_idempotency SET response=%s WHERE request_id=%s",
                           (jsonb({"status": exc.status_code, "body": json.loads(response.body)}), request_id))
                return response
            view = connector_result(result) if is_connector(request.url.path) else change_view(result)
            return JSONResponse(jsonable_encoder(view)) if operation else view
    except changes.ChangeBusy:
        if operation is not None:
            if operation.saved(db):
                raise ChangeRequestError(409, "idempotency_in_progress", "Dit verzoek is nog bezig.") from None
        raise
