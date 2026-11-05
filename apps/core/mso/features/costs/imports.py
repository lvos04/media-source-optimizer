"""Automatic import of paid-service candidates into Kosten: one service per provider/indexer source.

Sources come from the engines' registry (``sources``): Usenet providers/blockaccounts (MSO's provider list, which
mirrors SABnzbd servers) and Prowlarr indexers. Each new source becomes a service with ``origin='import'``, the
right ``kind``, linked ``source_id``, amount 0 and ``price_missing=true`` — the user only fills in price, period
and renewal date. Idempotent: a source that already has a service (imported or hand-made) is skipped, and a source
whose imported service the user removed is remembered in ``cost_import_skips``. Nothing is written to an
external system; public torrent indexers are imported as 'gratis' (editable).
"""

from __future__ import annotations

import logging
from typing import Any

from mso.audit import audit
from mso.db import Database, get_db

LOGGER = logging.getLogger(__name__)
IMPORT_KINDS = ("usenet_provider", "block_account", "nzb_indexer", "torrent_indexer")


def candidates(db: Database | None = None) -> list[dict[str, Any]]:
    return (db or get_db()).fetchall(
        "SELECT src.* FROM sources src WHERE src.kind = ANY(%s) "
        "AND COALESCE(src.meta->>'removed', '') <> 'true' AND COALESCE(src.meta->>'is_mso', '') <> 'true' "
        "AND NOT EXISTS (SELECT 1 FROM services s WHERE s.source_id = src.id) "
        "AND NOT EXISTS (SELECT 1 FROM cost_import_skips k WHERE k.source_id = src.id) "
        "ORDER BY src.kind, src.label",
        (list(IMPORT_KINDS),),
    )


def _row_for(src: dict[str, Any]) -> dict[str, Any]:
    meta = src.get("meta") or {}
    kind = src["kind"]
    public_torrent = kind == "torrent_indexer" and not src.get("private")
    origin = {"prowlarr": "Prowlarr", "sabnzbd": "SABnzbd", "mso": "Usenet-providers"}.get(
        src.get("origin"), src.get("origin") or "MSO"
    )
    if public_torrent:
        billing, missing = "free", False
        notes = f"Automatisch toegevoegd uit {origin}: publieke indexer, daarom als gratis gemarkeerd. Pas aan als je "
        notes += "ervoor betaalt."
    elif kind == "block_account":
        billing, missing = "block", True
        notes = f"Automatisch toegevoegd uit {origin}. Vul de blokprijs en blokgrootte in."
    else:
        billing, missing = "monthly", True
        notes = f"Automatisch toegevoegd uit {origin}. Vul prijs, periode en verlengdatum in."
    block_size = meta.get("block_size_gb") if kind == "block_account" else None
    try:
        block_size = float(block_size) if block_size else None
    except (TypeError, ValueError):
        block_size = None
    return {
        "source_id": src["id"],
        "kind": kind,
        "label": str(src["label"])[:120],
        "billing": billing,
        "amount_cents": 0,
        "block_size_gb": block_size,
        "notes": notes,
        "active": bool(src.get("enabled", True)),
        "price_missing": missing,
    }


def _sab_servers_to_providers(actor: str) -> int:
    """Best effort: let the Usenet engine create provider rows for SABnzbd servers it has not linked yet (MSO's own
    database only; SABnzbd itself is not changed). Absent module/connector → nothing happens."""
    try:
        from mso.connectors import base as cb

        if not cb.is_configured("sabnzbd"):
            return 0
        from mso.features.usenet import providers  # type: ignore[import-not-found]

        return len(providers.import_from_sab(actor))
    except Exception as exc:  # optional; costs import works on the sources that already exist
        LOGGER.info("SABnzbd-servers niet geïmporteerd: %s", type(exc).__name__)
        return 0


def import_services(actor: str = "systeem", *, include_sab: bool = False,
                    db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    sab_created = _sab_servers_to_providers(actor) if include_sab else 0
    created = []
    for src in candidates(db):
        row = _row_for(src)
        cols = list(row) + ["origin"]
        new = db.fetchone(
            f"INSERT INTO services({', '.join(cols)}) SELECT {', '.join(['%s'] * len(cols))} "
            "WHERE NOT EXISTS (SELECT 1 FROM services WHERE source_id=%s) RETURNING id, label, kind",
            [*row.values(), "import", row["source_id"]],
        )
        if new:
            created.append(dict(new))
    if created:
        audit(
            actor,
            "costs.services_imported",
            f"{len(created)} dienst(en) automatisch toegevoegd aan Kosten (prijs nog in te vullen)",
            target="costs",
            detail={"services": [f"{c['label']} ({c['kind']})" for c in created]},
        )
    return {"created": created, "sab_providers_created": sab_created}


def remember_skip(service: dict[str, Any], actor: str, db: Database | None = None) -> None:
    if service.get("origin") == "import" and service.get("source_id"):
        (db or get_db()).execute(
            "INSERT INTO cost_import_skips(source_id, label, skipped_by) VALUES (%s,%s,%s) "
            "ON CONFLICT (source_id) DO NOTHING",
            (service["source_id"], service.get("label") or "", actor),
        )
