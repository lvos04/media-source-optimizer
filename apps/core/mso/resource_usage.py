"""Dagmetingen en herkomst voor Systeem → Opslag en groei."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from mso import backups
from mso.api.foundation import _disk
from mso.config import get_settings
from mso.db import Database, jsonb

GROWTH_DAYS = 30
Quality = Literal["gemeten", "geschat", "onbekend"]
# Alleen deze vaste velden komen in de meetreeks terecht.
SOURCES: dict[str, tuple[str, Quality]] = {
    "database_bytes": ("PostgreSQL", "gemeten"),
    "database_connections": ("PostgreSQL", "gemeten"),
    "table_bytes": ("PostgreSQL", "gemeten"),
    "backups_bytes": ("MSO-back-upadministratie", "gemeten"),
    "backups_count": ("MSO-back-upadministratie", "gemeten"),
    "backup_free_bytes": ("Ruimtecontrole back-ups", "gemeten"),
    "appdata_free_bytes": ("Systeemgezondheid", "gemeten"),
    "header_index_bytes": ("Header-index", "gemeten"),
    "header_index_estimated_bytes": ("Header-index", "geschat"),
    "torrent_free_bytes": ("qBittorrent-opslagrapport", "gemeten"),
    "container_log_bytes": ("Containerlog", "onbekend"),
}
METRICS = tuple(key for key in SOURCES if key != "table_bytes")


class ResourceDay(BaseModel):
    day: date
    observed_at: datetime | None
    values: dict[str, int | None] | None


class ResourceTrend(BaseModel):
    """Gemiddeld verschil per UTC-kalenderdag tussen de eerste en laatste bruikbare waarde."""

    value: float | None = None
    from_day: date | None = None
    to_day: date | None = None
    quality: Quality = "onbekend"


class TableGrowth(BaseModel):
    name: str
    bytes: int
    per_day: ResourceTrend


class ResourceGrowth(BaseModel):
    latest: ResourceDay | None
    days: list[ResourceDay]
    per_day: dict[str, ResourceTrend]
    tables: list[TableGrowth]


class ResourceSource(BaseModel):
    source: str
    quality: Quality
    observed_at: datetime | None
    reason: str | None = None


class MaintenanceStatus(BaseModel):
    retention: dict[str, Any]
    database: dict[str, Any]
    backups: dict[str, Any]
    growth: ResourceGrowth
    provenance: dict[str, ResourceSource]


def database_usage(db: Database, *, limit: int | None = 6) -> dict[str, Any]:
    tables = db.fetchall(
        "SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes, c.reltuples::bigint AS rows "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 2 DESC, 1 LIMIT %s", (limit,))
    return {"size_bytes": int(db.scalar("SELECT pg_database_size(current_database())") or 0),
            "tables": [{"name": t["name"], "bytes": int(t["bytes"] or 0), "rows": max(int(t["rows"] or 0), 0)}
                       for t in tables]}


def backup_kinds(db: Database) -> dict[str, dict[str, int]]:
    return {r["kind"]: {"count": int(r["n"]), "bytes": int(r["bytes"] or 0)}
            for r in db.fetchall("SELECT kind, count(*) AS n, sum(size_bytes) AS bytes FROM backups GROUP BY kind")}


def _database_clock(db: Database) -> datetime:
    return db.scalar("SELECT clock_timestamp()").astimezone(UTC)


def _measure(db: Database, now: datetime) -> dict[str, Any]:
    from mso.features.torrent import storage
    from mso.features.usenet.headerindex import engine

    database = database_usage(db, limit=None)
    backups_by_kind = backup_kinds(db)
    appdata = _disk(get_settings().data_dir)
    header_index = engine.usage()
    torrent = storage.report()
    torrent_at = torrent["client"]["updated_at"]
    torrent_disk = torrent["disk"]
    torrent_recent = (torrent["client"]["available"] and torrent_at is not None
                      and timedelta(0) <= now - torrent_at <= timedelta(seconds=storage.SNAPSHOT_MAX_AGE_S))
    return {
        "values": {
            "database_bytes": database["size_bytes"],
            "database_connections": db.scalar("SELECT numbackends FROM pg_stat_database "
                                              "WHERE datname = current_database()"),
            "backups_bytes": sum(row["bytes"] for row in backups_by_kind.values()),
            "backups_count": sum(row["count"] for row in backups_by_kind.values()),
            "backup_free_bytes": backups.space_status(db)["free_bytes"],
            "appdata_free_bytes": appdata["free"] if appdata else None,
            "header_index_bytes": header_index["physical_bytes"],
            "header_index_estimated_bytes": header_index["estimated_bytes"],
            "torrent_free_bytes": torrent_disk["free"] if torrent_recent and torrent_disk else None,
            "container_log_bytes": None,
        },
        "tables": {row["name"]: row["bytes"] for row in database["tables"]},
        "torrent_observed_at": torrent_at.astimezone(UTC) if torrent_recent and torrent_disk else None,
    }


def sample(db: Database) -> bool:
    now = _database_clock(db)
    day = now.astimezone(UTC).date()
    if db.scalar("SELECT max(day) >= %s FROM resource_samples", (day,)):
        return False
    data = _measure(db, now)
    # Serialiseer alleen het schrijven. Een later voltooide taak mag na een klokcorrectie of een
    # gelijktijdige taak geen oudere dag invoegen. Lezers houden toegang tijdens de korte lock.
    with db.tx() as conn:
        conn.execute("LOCK TABLE resource_samples IN SHARE ROW EXCLUSIVE MODE")
        result = conn.execute(
            "INSERT INTO resource_samples(day, observed_at, data) "
            "SELECT %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM resource_samples WHERE day >= %s)",
            (day, now, jsonb(data), day),
        )
        return result.rowcount == 1


def _trend(points: list[tuple[date, int | None]], quality: Quality) -> ResourceTrend:
    known = [(day, value) for day, value in points if value is not None]
    if len(known) < 2:
        return ResourceTrend()
    last_day, last_value = known[0]
    first_day, first_value = known[-1]
    return ResourceTrend(value=round((last_value - first_value) / (last_day - first_day).days, 2),
                         from_day=first_day, to_day=last_day, quality=quality)


def _provenance(latest: dict[str, Any] | None) -> dict[str, ResourceSource]:
    values = latest["data"]["values"] if latest else {}
    result = {}
    for key, (source, quality) in SOURCES.items():
        available = bool(latest["data"]["tables"]) if latest and key == "table_bytes" else values.get(key) is not None
        observed_at = latest["observed_at"].astimezone(UTC) if latest and available else None
        if latest and available and key == "torrent_free_bytes":
            observed_at = latest["data"]["torrent_observed_at"]
        reason = None
        if not available:
            reason = {
                "container_log_bytes": "Het containerlog valt buiten MSO.",
                "torrent_free_bytes": "Er is geen bruikbare, recente momentopname van de torrentclient.",
            }.get(key, "De bron leverde geen waarde voor deze meting." if latest else "Er zijn nog geen metingen.")
        result[key] = ResourceSource(source=source, quality=quality if available else "onbekend",
                                     observed_at=observed_at, reason=reason)
    return result


def history(db: Database) -> tuple[ResourceGrowth, dict[str, ResourceSource]]:
    today = _database_clock(db).astimezone(UTC).date()
    start = today - timedelta(days=GROWTH_DAYS - 1)
    rows = db.fetchall(
        "SELECT day, observed_at, data FROM resource_samples "
        "WHERE day BETWEEN %s AND %s OR day = (SELECT max(day) FROM resource_samples WHERE day <= %s) "
        "ORDER BY day DESC", (start, today, today))
    latest = rows[0] if rows else None
    recent = [row for row in rows if row["day"] >= start]
    days = {row["day"]: ResourceDay(day=row["day"], observed_at=row["observed_at"].astimezone(UTC),
                                   values=row["data"]["values"]) for row in rows}
    calendar = [today - timedelta(days=i) for i in range(GROWTH_DAYS)] if latest else []
    latest_tables = latest["data"]["tables"] if latest else {}
    largest = sorted(latest_tables, key=lambda name: (-latest_tables[name], name))[:6]
    return ResourceGrowth(
        latest=days[latest["day"]] if latest else None,
        days=[days.get(day) or ResourceDay(day=day, observed_at=None, values=None) for day in calendar],
        per_day={key: _trend([(row["day"], row["data"]["values"].get(key)) for row in recent], SOURCES[key][1])
                 for key in METRICS},
        tables=[TableGrowth(name=name, bytes=latest_tables[name],
                            per_day=_trend([(row["day"], row["data"]["tables"].get(name)) for row in recent],
                                           SOURCES["table_bytes"][1]))
                for name in largest],
    ), _provenance(latest)
