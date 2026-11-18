"""Polling van de twee bestaande logboeken (contract §7)."""

import re
from datetime import UTC, datetime
from hashlib import blake2s
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.errors import LockNotAvailable, QueryCanceled
from pydantic import AwareDatetime, BaseModel

from mso import backups, machine_keys
from mso.audit import _clean
from mso.connector_api.errors import ConnectorError
from mso.db import PRE_UPGRADE_LOCK, get_db
from mso.retention import AUTOMATION_RETENTION_KEY

APP_ID = "media-source-optimizer"
MAX_ID = 2**63 - 1
CURSOR = re.compile(r"v1[.](\d{1,19})[.](\d{1,19})[.](\d{1,20})[.]([0-9a-f]{16})", re.ASCII)
LOG_ID = re.compile(r"(audit|automation):([1-9][0-9]{0,18})(?::([0-9a-f]{32}))?", re.ASCII)
BACKUP_EVENTS = {
    "backup.pre_upgrade_failed": "mso.backup.failed",
    "backup.pre_upgrade_skipped": "mso.backup.blocked",
    "backup.skipped": "mso.backup.blocked",
    "backup.pre_upgrade": "mso.backup.succeeded",
    "backup.created": "mso.backup.succeeded",
    "backup.restored": "mso.backup.restored",
}


class Entity(BaseModel):
    type: str
    id: str


class Actor(BaseModel):
    type: Literal["system", "user", "ai", "flow"]
    id: str


class Evidence(BaseModel):
    id: str
    source: Literal["audit_log", "automation_log"]
    observed_at: AwareDatetime
    deep_link: str
    change_id: int | None
    event_type: str
    entity: Entity


class Event(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    source_app: str = APP_ID
    source_event_id: str
    event_type: str
    entity: Entity
    timestamp_utc: AwareDatetime
    trace_id: UUID
    correlation_id: UUID | None = None
    actor: Actor
    classification: Literal["internal"] = "internal"
    payload: Evidence
    payload_ref: str | None = None


class EventsPage(BaseModel):
    events: list[Event]
    next_cursor: str


def change_link(change_id: int) -> str:
    return f"/automatisering?tab=wijzigingen&change={change_id}"


def _event_id(source: str, row: dict[str, Any]) -> UUID:
    at = row["ts"].astimezone(UTC)
    return row["event_uuid"] or uuid5(NAMESPACE_URL, f"urn:{APP_ID}:{source}:{row['id']}:{at.isoformat()}")


def _evidence(source: str, row: dict[str, Any], stable_id: UUID) -> Evidence:
    change_id = row["change_id"]
    event_type = BACKUP_EVENTS.get(row["action"])
    if event_type:
        entity = Entity(type="backup", id=APP_ID)
    else:
        event_type = f"mso.{source}.recorded"
        entity = Entity(type="change_set", id=str(change_id)) if change_id else Entity(type="log", id=source)
    return Evidence.model_validate(_clean({
        "id": f"{source}:{row['id']}:{stable_id.hex}",
        "source": "audit_log" if source == "audit" else "automation_log",
        "observed_at": row["ts"].astimezone(UTC),
        "deep_link": change_link(change_id) if change_id else "/logs" if source == "audit" else "/automatisering",
        "change_id": change_id,
        "event_type": event_type,
        "entity": entity,
    }))


def _event(source: str, row: dict[str, Any]) -> Event:
    stable_id = _event_id(source, row)
    evidence = _evidence(source, row, stable_id)
    return Event(event_id=stable_id, source_event_id=evidence.id,
                 event_type=evidence.event_type, entity=evidence.entity,
                 actor=Actor(type="system", id="mso"), timestamp_utc=evidence.observed_at,
                 trace_id=stable_id, payload=evidence)


def _history(conn) -> str:
    locked = conn.execute("SELECT pg_try_advisory_xact_lock_shared(%s) AS ok", (PRE_UPGRADE_LOCK,)).fetchone()
    if not locked["ok"] or machine_keys.restore_pending(conn):
        raise ConnectorError(503, "dependency_unavailable")
    row = conn.execute("SELECT value FROM system_state WHERE key=%s", (backups.EVENT_HISTORY_KEY,)).fetchone()
    return row["value"] if row else ""


def get_evidence(log_id: str) -> Evidence:
    match = LOG_ID.fullmatch(log_id)
    if match is None or int(match[2]) > MAX_ID:
        raise ConnectorError(404, "not_found")
    source, row_id, identity = match.groups()
    query = ("SELECT id,ts,change_id,action,NULL::uuid AS event_uuid FROM audit_log WHERE id=%s" if source == "audit"
             else "SELECT id,ts,change_id,NULL::text AS action,event_uuid FROM automation_log WHERE id=%s")
    with get_db().tx() as conn:
        history = _history(conn)
        if not identity and history:
            raise ConnectorError(404, "not_found")
        row = conn.execute(query, (int(row_id),)).fetchone()
    if row is None:
        raise ConnectorError(404, "not_found")
    evidence = _evidence(source, row, _event_id(source, row))
    if identity and evidence.id != log_id:
        raise ConnectorError(404, "not_found")
    return evidence if identity else evidence.model_copy(update={"id": log_id})


def _positions(cursor: str | None) -> tuple[int, int, int, str]:
    if cursor is None:
        return 0, 0, 0, ""
    match = CURSOR.fullmatch(cursor)
    if match:
        audit, automation, revision = map(int, match.groups()[:3])
        if max(audit, automation) <= MAX_ID:
            return audit, automation, revision, match[4]
    raise ConnectorError(422, "validation_error", {"fouten": [{"veld": "cursor", "reden": "ongeldig"}]})


def _anchor_digest(anchors: dict[str, datetime | None], history: str) -> str:
    keys = [at.astimezone(UTC).isoformat() if at is not None else "" for at in anchors.values()]
    if history:
        keys.append(history)
    return blake2s("|".join(keys).encode(), digest_size=8).hexdigest()


def poll(cursor: str | None, limit: int) -> EventsPage:
    audit, automation, revision, digest = _positions(cursor)
    anchors = {"audit": None, "automation": None}
    try:
        with get_db().tx() as conn:
            conn.execute("SET LOCAL statement_timeout = '2s'")
            history = _history(conn)
            # Een nog lopende insert kan een lager ID hebben dan een reeds zichtbare rij.
            # Houd beide bronnen en de retentiestand stabiel gedurende de korte paginaselectie.
            conn.execute("LOCK TABLE audit_log, automation_log IN SHARE MODE NOWAIT")
            marker = conn.execute("SELECT value FROM system_state WHERE key=%s",
                                  (AUTOMATION_RETENTION_KEY,)).fetchone()
            retention = marker["value"] if marker else {"revision": 0, "max_id": 0}
            current = retention["revision"]
            if cursor is not None:
                anchors = conn.execute(
                    "SELECT (SELECT ts FROM audit_log WHERE id=%s) AS audit, "
                    "(SELECT ts FROM automation_log WHERE id=%s) AS automation", (audit, automation)).fetchone()
                if (_anchor_digest(anchors, history) != digest or current < revision
                        or (current != revision and retention["max_id"] > automation)):
                    raise ConnectorError(409, "cursor_expired", {"restart": True})

            rows = conn.execute(
                """SELECT * FROM (
                    (SELECT 'audit' AS source,id,ts,change_id,action,NULL::uuid AS event_uuid FROM audit_log
                     WHERE id>%s ORDER BY id LIMIT %s)
                    UNION ALL
                    (SELECT 'automation' AS source,id,ts,change_id,NULL,event_uuid FROM automation_log
                     WHERE id>%s ORDER BY id LIMIT %s)
                ) AS logs ORDER BY id,source LIMIT %s""", (audit, limit, automation, limit, limit)).fetchall()
    except (LockNotAvailable, QueryCanceled):
        raise ConnectorError(503, "dependency_unavailable") from None
    positions = {"audit": audit, "automation": automation}
    for row in rows:
        positions[row["source"]] = row["id"]
        anchors[row["source"]] = row["ts"]
    return EventsPage(events=[_event(row["source"], row) for row in rows],
                      next_cursor=f"v1.{positions['audit']}.{positions['automation']}.{current}."
                                  f"{_anchor_digest(anchors, history)}")
