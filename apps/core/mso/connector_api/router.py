"""Leesroutes en bewaakte schrijfroutes voor het connectorcontract."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict

from mso import change_requests, changes
from mso.audit import _clean
from mso.auth import Principal, require
from mso.change_actions import ACTIONS
from mso.config import VERSION, get_settings
from mso.connector_api.errors import MAX_BYTES, PREFIX, ConnectorError, ErrorBody
from mso.connector_api.events import APP_ID, MAX_ID, EventsPage, Evidence, change_link, get_evidence, poll
from mso.connector_api.execution import (
    DiscoveredAction,
    ExecutionRequest,
    ExecutionResult,
    Mode,
    nonces,
    policy_pin,
    require_pin,
    verify_approval,
    verify_change,
    verify_signature,
)
from mso.connector_api.status import (
    BACKUP_MAX_AGE,
    CONNECTOR_MAX_AGE,
    JOB_STALL_AGE,
    WORKER_MAX_AGE,
    Health,
    as_utc,
    collect_health,
    connector_status,
)
from mso.connectors.base import SPECS
from mso.db import get_db
from mso.machine_keys import ADMIN_LOCK

router = APIRouter(prefix=PREFIX, tags=["connector"])
Viewer = Annotated[Principal, Depends(require("viewer"))]
Limit = Annotated[int, Query(ge=1, le=200)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=80)]
ItemType = Literal["connector", "change_set"]
ITEM_CURSOR = re.compile(r"(all|connector|change_set)[.](connector:[a-z0-9_-]{1,48}|change_set:[0-9]{1,19})", re.ASCII)
CHANGE_STATES = frozenset({"preview", "approved", "applying", "applied", "rolled_back", "failed", "discarded"})


def _error(description: str, *, retry: bool = False) -> dict:
    spec = {"model": ErrorBody, "description": description}
    if retry:
        spec["headers"] = {"Retry-After": {"schema": {"type": "integer", "minimum": 1}}}
    return spec


COMMON_ERRORS = {
    401: _error("unauthorized"),
    403: _error("forbidden"),
    413: _error("payload_too_large"),
    422: _error("validation_error"),
    429: _error("rate_limited", retry=True),
    503: _error("dependency_unavailable", retry=True),
}


class Limits(BaseModel):
    max_limit: int = 200
    max_body_bytes: int = MAX_BYTES
    max_age_s: dict[str, int]


class Discovery(BaseModel):
    app_id: str = APP_ID
    source_version: str
    schema_version: Literal["1.0"] = "1.0"
    capabilities: list[Literal["read", "apply", "rollback"]]
    permissions: list[Literal["read"]]
    actions: list[DiscoveredAction]
    limits: Limits
    timestamp_utc: AwareDatetime


class Item(BaseModel):
    id: str
    type: ItemType
    source: Literal["connectors", "change_sets"]
    observed_at: AwareDatetime | None
    deep_link: str
    status: str
    reason_code: str | None = None


class ItemsPage(BaseModel):
    items: list[Item]
    next_cursor: str | None


class NoQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageQuery(NoQuery):
    cursor: Cursor = None
    limit: Limit = 100


class ItemsQuery(PageQuery):
    type: ItemType | None = None


@router.get("/discover", responses=COMMON_ERRORS)
def discover(_: Viewer, query: Annotated[NoQuery, Query()]) -> Discovery:
    version = get_settings().build.version
    return Discovery(source_version=VERSION if version == "onbekend" else version,
                     capabilities=["read", "apply", "rollback"] if policy_pin() else ["read"],
                     permissions=["read"], actions=[DiscoveredAction(**action.public()) for action in ACTIONS],
                     timestamp_utc=datetime.now(UTC),
                     limits=Limits(max_age_s={
                         "connector": CONNECTOR_MAX_AGE, "worker": WORKER_MAX_AGE,
                         "backup": BACKUP_MAX_AGE, "jobs": JOB_STALL_AGE,
                     }))


@router.get("/health", responses=COMMON_ERRORS)
def health(_: Viewer, query: Annotated[NoQuery, Query()]) -> Health:
    return collect_health()


def _connector_items(now: datetime) -> list[Item]:
    out = []
    rows = get_db().fetchall("SELECT id,enabled,status,last_checked_at FROM connectors ORDER BY id")
    for row in rows:
        cid = row["id"]
        if cid not in SPECS:
            continue
        at = as_utc(row["last_checked_at"])
        status, reason = connector_status(row["status"], at, now)
        if not row["enabled"]:
            status, reason = "unknown", "koppeling_uitgeschakeld"
        out.append(Item(id=f"connector:{cid}", type="connector", source="connectors", observed_at=at,
                        deep_link="/systeem", status=status, reason_code=reason))
    return out


def _change_items(after: int, limit: int) -> list[Item]:
    return [Item.model_validate(_clean({
        "id": f"change_set:{row['id']}", "type": "change_set", "source": "change_sets",
        "observed_at": row["observed_at"].astimezone(UTC), "deep_link": change_link(row["id"]),
        "status": row["status"] if row["status"] in CHANGE_STATES else "unknown",
    })) for row in get_db().fetchall(
        "SELECT id,status,statement_timestamp() AS observed_at FROM change_sets WHERE id>%s ORDER BY id LIMIT %s",
        (after, limit))]


@router.get("/items", responses=COMMON_ERRORS)
def items(_: Viewer, query: Annotated[ItemsQuery, Query()]) -> ItemsPage:
    selection = query.type or "all"
    limit = query.limit
    after = 0
    connector_after = ""
    if query.cursor is not None:
        match = ITEM_CURSOR.fullmatch(query.cursor)
        invalid = ConnectorError(422, "validation_error", {"fouten": [{"veld": "cursor", "reden": "ongeldig"}]})
        if match is None or match[1] != selection:
            raise invalid
        last_type, last_id = match[2].split(":")
        if query.type is not None and last_type != query.type:
            raise invalid
        if last_type == "connector":
            connector_after = match[2]
        else:
            after = int(last_id)
            if not 1 <= after <= MAX_ID:
                raise invalid

    page = [] if query.type == "change_set" or after else [
        item for item in _connector_items(datetime.now(UTC)) if item.id > connector_after]
    if query.type != "connector" and len(page) <= limit:
        page += _change_items(after, limit + 1 - len(page))
    next_cursor = f"{selection}.{page[limit - 1].id}" if len(page) > limit else None
    return ItemsPage(items=page[:limit], next_cursor=next_cursor)


@router.get("/events", responses={**COMMON_ERRORS, 409: _error("cursor_expired: begin opnieuw zonder cursor")})
def events(_: Viewer, query: Annotated[PageQuery, Query()]) -> EventsPage:
    return poll(query.cursor, query.limit)


@router.get("/evidence/{id}", responses={**COMMON_ERRORS, 404: _error("not_found")})
def evidence(_: Viewer, id: Annotated[str, Path(max_length=80)], query: Annotated[NoQuery, Query()]) -> Evidence:
    return get_evidence(id)


@router.post("/propose", status_code=501, response_model=ErrorBody, responses={
    **COMMON_ERRORS, 501: _error("capability_unavailable")})
def unavailable(_: Viewer) -> ErrorBody:
    raise ConnectorError(501, "capability_unavailable")


WRITE_ERRORS = {
    **COMMON_ERRORS,
    400: _error("idempotency_key_required of idempotency_key_invalid"),
    404: _error("not_found"),
    409: _error("preview_changed, approval_required, approval_expired, nonce_reused, "
                "idempotency_in_progress of conflict"),
    412: _error("precondition_failed"),
    422: _error("validation_error of idempotency_key_reused"),
    501: _error("capability_unavailable"),
    502: _error("upstream_error"),
    200: {"headers": {"Idempotent-Replay": {"schema": {"type": "string", "enum": ["true"]}}}},
}
Pin = Annotated[tuple, Depends(require_pin)]
WriteQuery = Annotated[NoQuery, Query()]
RequestKey = Annotated[str | None, Header(alias="Idempotency-Key",
                                                       json_schema_extra={"format": "uuid"})]


def _execute(request: Request, body: ExecutionRequest, pin: tuple, mode: Mode):
    verify_signature(body, pin)
    approval = verify_approval(body, pin[1], mode)
    request_id = change_requests.parse_request_id(request.headers.getlist("Idempotency-Key"), required=True)
    actor = "hub:policy"

    def check(_conn, ch):
        live_approval = verify_approval(body, pin[1], mode)
        verify_change(body, live_approval, ch, mode)

    def check_deadline():
        verify_approval(body, pin[1], mode)

    def execute(db, ch):
        if mode == "rollback":
            return changes.rollback_locked(ch, actor, db, request_id=request_id, check_deadline=check_deadline)
        return changes.apply_locked(ch, actor, db, verifies_approval=check_deadline, request_id=request_id)

    return change_requests.run(request, body.proposal.target.change_id, actor, body.model_dump(),
                               request_id, check, execute, guard_lock=ADMIN_LOCK, **nonces(body, approval))


@router.post("/apply", response_model=ExecutionResult, responses=WRITE_ERRORS)
def apply(request: Request, pin: Pin, _: WriteQuery, body: ExecutionRequest, idempotency_key: RequestKey = None):
    return _execute(request, body, pin, "apply")


@router.post("/rollback", response_model=ExecutionResult, responses=WRITE_ERRORS)
def rollback(request: Request, pin: Pin, _: WriteQuery, body: ExecutionRequest, idempotency_key: RequestKey = None):
    return _execute(request, body, pin, "rollback")
