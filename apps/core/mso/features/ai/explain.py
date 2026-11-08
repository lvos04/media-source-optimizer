"""Evidence aggregation (non-personal) and the deterministic, rule-based Dutch explainer.

The deterministic explainer is always computed; the AI (when allowed) only rephrases/extends it."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from mso.db import Database, get_db
from mso.domain.outcomes import FAILURE_REASONS, outcome_at

ADVICE: dict[str, str] = {
    "unpack_failed": "SABnzbd kon de download niet uitpakken; de melding noemt zelf geen oorzaak. Vaak ontbraken "
    "artikelen (bijv. omdat servers het inloggen weigerden): controleer de serverstatus bij Usenet-providers en de "
    "SABnzbd-log.",
    "no_indexer_hit": "Geen enkele bron vond een geschikte release. Controleer of de juiste categorieën en "
    "indexertags (normaal/anime) gekoppeld zijn en of er een bron met dit soort inhoud actief is.",
    "false_positive": "De gevonden release bleek verkeerde inhoud te bevatten. Overweeg de betrouwbaarheid "
    "van de "
    "bron te verlagen en releases van deze bron strenger te controleren.",
    "rejected_quality": "De release voldeed niet aan de MediaGuard-kwaliteitseisen. Dat is bedoeld gedrag; "
    "zoek "
    "een release die wel voldoet in plaats van de eisen te versoepelen.",
    "no_retention": "Artikelen waren buiten retentie of verwijderd bij je provider(s). Een provider of "
    "blockaccount op een andere backbone kán helpen, maar alleen als je backbones echt "
    "verschillen; controleer dat eerst in Kosten & besparing.",
    "incomplete": "Er ontbraken artikelen en reparatie lukte niet. Kijk of een back-upprovider op een andere "
    "backbone is ingesteld en of de release bij een andere bron in betere staat is.",
    "client_failure": "De downloadclient gaf een fout. Controleer de koppeling (test op de "
    "koppelingspagina) en "
    "de logboeken van de client.",
    "import_failure": "De download is gelukt maar het importeren mislukte. Dat valt onder MediaGuard; "
    "controleer "
    "daar de importmeldingen.",
    "auth_failure": "Inloggen bij de provider/tracker mislukte. Controleer of de inloggegevens nog kloppen "
    "en "
    "of het account actief is.",
    "account_expired": "Het account is verlopen. Controleer het abonnement; de app verlengt of betaalt nooit "
    "zelf.",
    "quota_exceeded": "Een limiet of quotum is bereikt (bijv. blockaccount op of dagelijkse limiet). "
    "Controleer "
    "het verbruik bij Kosten & besparing.",
    "stalled": "De torrent liep vast (geen peers of voortgang). Controleer seeders, poortbereikbaarheid en "
    "de "
    "tracker-status; probeer zo nodig een andere release.",
    "tracker_error": "De tracker gaf een fout. Controleer de trackerstatus en je account; voeg nooit "
    "publieke "
    "trackers toe aan private torrents.",
    "disk_full": "Er was onvoldoende schijfruimte. Maak ruimte vrij of verplaats de werkmap; de app "
    "verwijdert "
    "zelf nooit bestanden.",
    "encrypted": "Het archief was versleuteld of had een wachtwoord. Sla zulke releases over en blokkeer ze "
    "voor dit item.",
    "unknown": "De oorzaak kon niet met zekerheid worden vastgesteld. Bekijk de details in de "
    "downloadclient.",
}
AREA_BY_REASON = {
    "no_indexer_hit": "indexers",
    "false_positive": "indexers",
    "rejected_quality": "indexers",
    "stalled": "torrent",
    "tracker_error": "torrent",
    "import_failure": "mediaguard",
}


def _area(reason: str, protocol: str | None) -> str:
    return AREA_BY_REASON.get(reason) or ("torrent" if protocol == "torrent" else "usenet")


def evidence(subject: str, ref: str | None = None, db: Database | None = None) -> dict[str, Any]:
    """Aggregated, non-personal evidence. No titles, file paths, usernames or failure_detail text."""
    db = db or get_db()
    if subject == "download":
        try:
            oid = int(ref or "")
        except ValueError as exc:
            raise HTTPException(422, detail="Geef een download-id op") from exc
        row = db.fetchone(
            "SELECT d.*, s.kind AS source_kind, s.label AS source_label FROM download_outcomes d "
            "LEFT JOIN sources s ON s.id=d.source_id WHERE d.id=%s",
            (oid,),
        )
        if row is None:
            raise HTTPException(404, detail="Download niet gevonden")
        pb = row["provider_bytes"] or {}
        total = sum(int(v or 0) for v in pb.values())
        return {
            "subject": "download",
            "key": f"download:{oid}",
            "protocol": row["protocol"],
            "client": row["client"],
            "status": row["status"],
            "failure_reason": row["failure_reason"],
            "media_category": row["media_category"],
            "source_kind": row["source_kind"],
            "source": row["source_label"],
            "private": row["private"],
            "size_gb": round((row["size_bytes"] or 0) / 1024**3, 2),
            "provider_share": {k: round(int(v or 0) / total, 3) for k, v in pb.items()} if total else {},
            "same_release_attempts": int(
                db.scalar(
                    "SELECT count(*) FROM download_outcomes WHERE release_key=%s", (row["release_key"],)
                )
                or 0
            )
            if row["release_key"]
            else None,
        }
    where, params, key = "", [], "overview"
    if subject == "source":
        if not ref:
            raise HTTPException(422, detail="Geef een bron-id op")
        if not db.scalar("SELECT count(*) FROM sources WHERE id=%s", (ref,)):
            raise HTTPException(404, detail="Bron niet gevonden")
        where, params, key = " AND (source_id=%s OR provider_bytes ? %s)", [ref, ref], f"source:{ref}"
    elif subject != "overview":
        raise HTTPException(422, detail="Onbekend onderwerp")
    rows = db.fetchall(
        "SELECT protocol, status, failure_reason, count(*) AS n FROM download_outcomes WHERE "
        f"{outcome_at()} > now() - interval '7 days' AND status IN ('success','failed')"
        + where
        + " GROUP BY protocol, status, failure_reason",
        params,
    )
    reasons: dict[str, int] = {}
    per_protocol: dict[str, dict[str, int]] = {}
    for r in rows:
        p = per_protocol.setdefault(r["protocol"], {"success": 0, "failed": 0})
        p[r["status"]] += int(r["n"])
        if r["status"] == "failed":
            k = r["failure_reason"] or "unknown"
            reasons[k] = reasons.get(k, 0) + int(r["n"])
    ev: dict[str, Any] = {
        "subject": subject,
        "key": key,
        "period_days": 7,
        "per_protocol": per_protocol,
        "failures_by_reason": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }
    if subject == "source":
        src = db.fetchone("SELECT kind, label, private, reliability FROM sources WHERE id=%s", (ref,))
        ev["source"] = {
            "kind": src["kind"],
            "label": src["label"],
            "private": src["private"],
            "reliability": src["reliability"],
        }
    ev["connectors_unhealthy"] = [
        r["label"]
        for r in db.fetchall(
            "SELECT label FROM connectors WHERE enabled AND url IS NOT NULL AND status IN "
            "('offline','auth_failed','degraded')"
        )
    ]
    return ev


def deterministic(ev: dict[str, Any]) -> dict[str, Any]:
    lines: list[str] = []
    if ev["subject"] == "download":
        reason = ev.get("failure_reason")
        if ev["status"] != "failed" or not reason:
            text = (
                f"Deze download heeft status '{ev['status']}'. Er is geen faaloorzaak vastgelegd, dus er is "
                "geen advies nodig."
            )
            return {
                "title": "Geen fout om te verklaren",
                "text": text,
                "lines": [text],
                "area": _area("", ev["protocol"]),
                "confidence": None,
            }
        label = FAILURE_REASONS.get(reason, reason)
        lines.append(f"Oorzaak: {label}.")
        lines.append(ADVICE.get(reason, ADVICE["unknown"]))
        if ev.get("same_release_attempts") and ev["same_release_attempts"] > 1:
            lines.append(
                f"Deze release is {ev['same_release_attempts']} keer geprobeerd; herhaal dezelfde release "
                "niet opnieuw maar kies een alternatief."
            )
        if ev.get("provider_share"):
            share = ", ".join(f"{k} {round(v * 100)}%" for k, v in ev["provider_share"].items())
            lines.append(f"Waargenomen bytes per provider: {share}.")
        return {
            "title": f"Uitleg mislukte download: {label}",
            "text": " ".join(lines),
            "lines": lines,
            "area": _area(reason, ev["protocol"]),
            "confidence": 0.9 if reason != "unknown" else 0.3,
        }
    reasons: dict[str, int] = ev.get("failures_by_reason") or {}
    total_failed = sum(reasons.values())
    total_ok = sum(p["success"] for p in ev["per_protocol"].values())
    scope = f"bron {ev['source']['label']}" if ev["subject"] == "source" else "alle downloads"
    if not total_failed and not total_ok:
        text = (
            f"Geen afgeronde downloads waargenomen in de afgelopen 7 dagen ({scope}); er valt nog niets "
            "te verklaren."
        )
        return {
            "title": "Onvoldoende gegevens",
            "text": text,
            "lines": [text],
            "area": "usenet",
            "confidence": None,
        }
    lines.append(f"Afgelopen 7 dagen ({scope}): {total_ok} geslaagd, {total_failed} mislukt.")
    for reason, n in list(reasons.items())[:3]:
        lines.append(f"{FAILURE_REASONS.get(reason, reason)} ({n}×): {ADVICE.get(reason, ADVICE['unknown'])}")
    if ev.get("connectors_unhealthy"):
        lines.append(
            "Let op: deze koppelingen zijn niet gezond: " + ", ".join(ev["connectors_unhealthy"]) + "."
        )
    if total_failed + total_ok < 10:
        lines.append("Het aantal waarnemingen is klein; trek nog geen harde conclusies.")
    top = next(iter(reasons), None)
    protocols = list(ev["per_protocol"])
    return {
        "title": f"Foutanalyse {scope}"
        + (f": vooral {FAILURE_REASONS.get(top, top).lower()}" if top else ""),
        "text": " ".join(lines),
        "lines": lines,
        "area": _area(top or "", protocols[0] if len(protocols) == 1 else None),
        "confidence": round(min(0.9, 0.3 + (total_failed + total_ok) / 100), 2),
    }
