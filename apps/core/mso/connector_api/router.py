"""Leesroutes en tijdelijke weigeringen voor het connectorcontract."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime, BaseModel

from mso.audit import _clean
from mso.auth import Principal, require
from mso.config import VERSION, get_settings
from mso.connector_api.errors import MAX_BYTES, PREFIX, ConnectorError, ErrorBody
from mso.connector_api.events import APP_ID, MAX_ID, EventsPage, Evidence, change_link, get_evidence, poll
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
    capabilities: list[Literal["read"]]
    permissions: list[Literal["read"]]
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


@router.get("/discover", responses=COMMON_ERRORS)
def discover(_: Viewer) -> Discovery:
    version = get_settings().build.version
    return Discovery(source_version=VERSION if version == "onbekend" else version,
                     capabilities=["read"], permissions=["read"], timestamp_utc=datetime.now(UTC),
                     limits=Limits(max_age_s={
                         "connector": CONNECTOR_MAX_AGE, "worker": WORKER_MAX_AGE,
                         "backup": BACKUP_MAX_AGE, "jobs": JOB_STALL_AGE,
                     }))


@router.get("/health", responses=COMMON_ERRORS)
def health(_: Viewer) -> Health:
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
def items(_: Viewer, type: ItemType | None = None, cursor: Cursor = None, limit: Limit = 100) -> ItemsPage:
    selection = type or "all"
    after = 0
    connector_after = ""
    if cursor is not None:
        match = ITEM_CURSOR.fullmatch(cursor)
        invalid = ConnectorError(422, "validation_error", {"fouten": [{"veld": "cursor", "reden": "ongeldig"}]})
        if match is None or match[1] != selection:
            raise invalid
        last_type, last_id = match[2].split(":")
        if type is not None and last_type != type:
            raise invalid
        if last_type == "connector":
            connector_after = match[2]
        else:
            after = int(last_id)
            if not 1 <= after <= MAX_ID:
                raise invalid

    page = [] if type == "change_set" or after else [
        item for item in _connector_items(datetime.now(UTC)) if item.id > connector_after]
    if type != "connector" and len(page) <= limit:
        page += _change_items(after, limit + 1 - len(page))
    next_cursor = f"{selection}.{page[limit - 1].id}" if len(page) > limit else None
    return ItemsPage(items=page[:limit], next_cursor=next_cursor)


@router.get("/events", responses={**COMMON_ERRORS, 409: _error("cursor_expired: begin opnieuw zonder cursor")})
def events(_: Viewer, cursor: Cursor = None, limit: Limit = 100) -> EventsPage:
    return poll(cursor, limit)


@router.get("/evidence/{id}", responses={**COMMON_ERRORS, 404: _error("not_found")})
def evidence(_: Viewer, id: Annotated[str, Path(max_length=80)]) -> Evidence:
    return get_evidence(id)


@router.post("/propose", status_code=501, response_model=ErrorBody, responses={
    **COMMON_ERRORS, 501: _error("capability_unavailable")})
@router.post("/apply", status_code=501, response_model=ErrorBody, responses={
    **COMMON_ERRORS, 501: _error("capability_unavailable")})
@router.post("/rollback", status_code=501, response_model=ErrorBody, responses={
    **COMMON_ERRORS, 501: _error("capability_unavailable")})
def unavailable(_: Viewer) -> ErrorBody:
    raise ConnectorError(501, "capability_unavailable")
