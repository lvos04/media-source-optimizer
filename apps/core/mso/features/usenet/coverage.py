"""Usenet coverage gains: the moments a retry of a failed download can help (1.6.0).

Retrying the same SABnzbd job asks the same servers for the same articles again. That only helps when SABnzbd can
reach more articles than when the job failed:

- a server that was switched off because it failed is confirmed working again — ``usenet_server_autoheal.recovered_at``
  (set by the server Autopilot once SABnzbd really used the server again without new errors);
- a server appears in SABnzbd enabled for the first time (a new provider/backbone, or a dormant account the owner
  switched on) — ``usenet_server_coverage.gain_at``, kept by :func:`observe`.

Switching a broken server off is not a gain (a server whose login fails delivers nothing). The servers present at
MSO's first observation are the baseline, and a server MSO itself switched off and on again only counts through
``recovered_at``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from mso.db import Database, get_db

LOGGER = logging.getLogger(__name__)


def observe(servers: list[dict[str, Any]], db: Database | None = None) -> list[str]:
    """Record SABnzbd's server list (``SabClient.servers()``). Returns the servers that are a new coverage gain."""
    db = db or get_db()
    known = {r["server"]: dict(r) for r in db.fetchall("SELECT * FROM usenet_server_coverage")}
    baseline = not known
    autoheal = {
        r["server"]: r["status"]
        for r in db.fetchall("SELECT server, status FROM usenet_server_autoheal")
    }
    gains: list[str] = []
    for s in servers:
        name = str(s.get("name") or "")
        if not name:
            continue
        enabled = bool(s.get("enable"))
        display = str(s.get("displayname") or name)[:200]
        row = known.get(name)
        if row is None:
            gain = enabled and not baseline
            db.execute(
                "INSERT INTO usenet_server_coverage(server, displayname, first_enabled_at, gain_at, enabled) "
                "VALUES (%s, %s, CASE WHEN %s THEN now() END, CASE WHEN %s THEN now() END, %s) "
                "ON CONFLICT (server) DO NOTHING",
                (name, display, enabled, gain, enabled),
            )
            if gain:
                gains.append(name)
            continue
        if enabled and row["first_enabled_at"] is None:
            # first time MSO sees this server switched on; MSO's own probe of a server it switched off is not a gain
            gain = autoheal.get(name) not in ("disabled", "probing", "given_up")
            db.execute(
                "UPDATE usenet_server_coverage SET first_enabled_at=now(), gain_at=CASE WHEN %s THEN now() END, "
                "enabled=true, displayname=%s, updated_at=now() WHERE server=%s",
                (gain, display, name),
            )
            if gain:
                gains.append(name)
        elif bool(row["enabled"]) != enabled or row.get("displayname") != display:
            db.execute(
                "UPDATE usenet_server_coverage SET enabled=%s, displayname=%s, updated_at=now() WHERE server=%s",
                (enabled, display, name),
            )
    if gains:
        LOGGER.info("nieuwe Usenet-dekking: %s", ", ".join(gains))
        try:
            from mso.features.recovery import fallback
        except ImportError:  # recovery module disabled
            return gains
        try:
            fallback.reevaluate()  # failed downloads that waited for more coverage are looked at again
        except Exception:  # pragma: no cover - never let this break the caller
            LOGGER.info("herbeoordeling mislukte downloads overgeslagen", exc_info=True)
    return gains


def last_gain(db: Database | None = None) -> dict[str, Any] | None:
    """The most recent coverage gain: ``{at, since, server, kind}`` (kind ``recovered`` or ``new_server``), or None.

    ``at`` is when MSO knew (the trigger: ``recovered_at`` is set only after a watch of at least 2 hours, once
    SABnzbd really used the server). ``since`` is when the coverage became available: for a recovered server the
    switch-on that led to the recovery (``probe_started_at``), so a job that failed while SABnzbd already had that
    server is not "before the gain"."""
    db = db or get_db()
    row = db.fetchone(
        "SELECT * FROM ("
        "  SELECT recovered_at AS at, LEAST(COALESCE(probe_started_at, recovered_at), recovered_at) AS since, server, "
        "  'recovered' AS kind FROM usenet_server_autoheal WHERE recovered_at IS NOT NULL "
        "  UNION ALL "
        "  SELECT gain_at AS at, gain_at AS since, server, 'new_server' AS kind FROM usenet_server_coverage "
        "  WHERE gain_at IS NOT NULL"
        ") g ORDER BY at DESC LIMIT 1"
    )
    if row is None:
        return None
    name = db.scalar(
        "SELECT COALESCE((SELECT displayname FROM usenet_server_coverage WHERE server=%s), "
        "(SELECT displayname FROM usenet_sab_servers WHERE server=%s))",
        (row["server"], row["server"]),
    )
    return {"at": row["at"], "since": row["since"] or row["at"], "server": row["server"], "name": name or row["server"],
            "kind": row["kind"]}


def last_gain_at(db: Database | None = None) -> datetime | None:
    """max(``usenet_server_autoheal.recovered_at``, ``usenet_server_coverage.gain_at``)."""
    g = last_gain(db)
    return g["at"] if g else None


def describe(gain: dict[str, Any] | None) -> str | None:
    """Dutch: what improved the coverage."""
    if not gain:
        return None
    from mso.features.usenet import serverstatus  # lazy: serverstatus pulls in more of the module

    name = serverstatus.short_name(gain.get("name") or gain["server"])
    if gain["kind"] == "recovered":
        return f"server {name} werkt weer"
    return f"server {name} is nieuw ingeschakeld"


__all__ = ["describe", "last_gain", "last_gain_at", "observe"]
