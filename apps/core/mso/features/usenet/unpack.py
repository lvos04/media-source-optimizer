""""SABnzbd kan downloads niet uitpakken" — one in-app advisory when the same unpack/environment error repeats (1.6.0).

Input: failed SABnzbd downloads with stage-log facts (:mod:`mso.features.usenet.stagelog`, ``meta.stage``). When the
same error — same problem and the same SABnzbd line apart from set names, paths and numbers (``meta.stage.signature``)
— occurred at least :data:`MIN_COUNT` times in :data:`WINDOW`, one recommendation is opened with SABnzbd's exact
(redacted) line, the count and examples. It appears in "Wacht op jou" (risk ``toestemming_nodig``: only the owner can
fix SABnzbd or the storage; MSO changes nothing), is deduplicated per error (``usenet.unpack:<signature>``), keeps a
dismissal, and is resolved by itself when the error has not occurred for :data:`QUIET`.

Only problems of SABnzbd/the storage count (:data:`mso.features.usenet.stagelog.ENVIRONMENT`: disk full, no write
permission, file too large for the file system, moving to the final folder failed, the unpack program itself failed —
e.g. 7-Zip's "Unpacking failed, see logfile"). A password, a damaged archive or an executable belong to one release,
and missing articles are a coverage question; those never raise this advisory.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from mso import jobs
from mso.connectors import base as cb
from mso.db import get_db
from mso.domain import outcomes
from mso.features.usenet import stagelog

LOGGER = logging.getLogger(__name__)
KEY_PREFIX = "usenet.unpack:"
TITLE = "SABnzbd kan downloads niet uitpakken"
MIN_COUNT = 3
WINDOW = timedelta(days=7)
QUIET = timedelta(days=3)
MAX_EXAMPLES = 3

_HINTS = {
    "disk_full": "de schijf of dataset waar SABnzbd naartoe uitpakt is vol (of een quotum is bereikt). Maak ruimte "
    "vrij of kies in SABnzbd een andere map voor voltooide downloads.",
    "no_permission": "SABnzbd mag niet schrijven in de map voor (voltooide) downloads. Controleer de rechten van die "
    "map/dataset voor de gebruiker waaronder SABnzbd draait.",
    "file_too_large": "het bestandssysteem van de doelmap ondersteunt geen grote bestanden (bijv. FAT32). Kies een "
    "map op een ander bestandssysteem.",
    "move_failed": "SABnzbd kon de uitgepakte bestanden niet naar de eindmap verplaatsen (pad bestaat niet, rechten "
    "of een volle schijf). Controleer de map voor voltooide downloads in SABnzbd.",
    "unpacker_error": "het uitpakprogramma van SABnzbd (unrar/7-Zip) geeft een fout. Bekijk het SABnzbd-logbestand "
    "rond deze downloads en controleer of unrar en 7-Zip goed geïnstalleerd zijn en de doelmap beschrijfbaar is.",
}


def _rows(since: datetime) -> list[dict[str, Any]]:
    return get_db().fetchall(
        "SELECT client_ref, title, meta, COALESCE(finished_at, observed_at) AS at FROM download_outcomes "
        "WHERE client='sabnzbd' AND status='failed' AND COALESCE(finished_at, observed_at) > %s "
        "AND meta->'stage'->>'signature' IS NOT NULL AND meta->'stage'->>'problem' = ANY(%s) "
        "ORDER BY COALESCE(finished_at, observed_at) DESC",
        (since, sorted(stagelog.ENVIRONMENT)),
    )


def groups(now: datetime | None = None) -> list[dict[str, Any]]:
    """Repeated environment errors in the window: ``[{signature, problem, line, count, complete, last_at,
    examples, nzo_ids}]``, most frequent first."""
    now = now or datetime.now(UTC)
    out: dict[str, dict[str, Any]] = {}
    for r in _rows(now - WINDOW):
        st = (r["meta"] or {}).get("stage") or {}
        sig = st["signature"]
        g = out.setdefault(
            sig,
            {
                "signature": sig,
                "problem": st.get("problem"),
                "line": st.get("unpack_line") or st.get("repair_line") or "",
                "count": 0,
                "complete": 0,
                "last_at": r["at"],
                "examples": [],
                "nzo_ids": [],
            },
        )
        g["count"] += 1
        if st.get("data_complete") is True:
            g["complete"] += 1
        if len(g["examples"]) < MAX_EXAMPLES and r["title"] not in g["examples"]:
            g["examples"].append(r["title"])
        if len(g["nzo_ids"]) < 20:
            g["nzo_ids"].append(r["client_ref"])
    return sorted(out.values(), key=lambda g: (-g["count"], g["signature"]))


def explanation(g: dict[str, Any]) -> str:
    n, complete = g["count"], g["complete"]
    data = (
        " Bij al deze downloads was de data compleet: de servers en de releases zijn niet de oorzaak."
        if complete == n
        else f" Bij {complete} van de {n} was de data compleet." if complete else ""
    )
    examples = "; ".join(g["examples"])
    return (
        f"{n} downloads mislukten de afgelopen 7 dagen met dezelfde melding van SABnzbd: \"{g['line']}\".{data} "
        f"Waarschijnlijke oorzaak: {_HINTS.get(g['problem'], _HINTS['unpacker_error'])} Voorbeelden: {examples}. "
        "MSO verandert hier zelf niets aan en probeert deze downloads niet opnieuw; nieuwe downloads mislukken op "
        "dezelfde manier tot dit is opgelost. Deze melding verdwijnt vanzelf als de fout 3 dagen niet meer voorkomt."
    )


def check(now: datetime | None = None) -> dict[str, Any]:
    """Open/refresh one advisory per repeated error; resolve the ones that have been quiet for :data:`QUIET`."""
    now = now or datetime.now(UTC)
    db = get_db()
    active: list[str] = []
    for g in groups(now):
        if g["count"] < MIN_COUNT or g["last_at"] <= now - QUIET:
            continue
        key = f"{KEY_PREFIX}{g['signature']}"
        active.append(key)
        label = stagelog.PROBLEM_LABELS.get(g["problem"] or "", g["problem"] or "")
        outcomes.recommend(
            "usenet",
            TITLE,
            explanation(g),
            risk="toestemming_nodig",
            subject="sabnzbd",
            impact=f"{g['count']} mislukte downloads in 7 dagen ({label}); los dit op in SABnzbd of op de opslag",
            confidence=0.9 if g["complete"] == g["count"] else 0.7,
            dedupe_key=key,
            evidence={
                "signature": g["signature"],
                "problem": g["problem"],
                "problem_label": label,
                "line": g["line"],
                "count": g["count"],
                "data_complete": g["complete"],
                "examples": g["examples"],
                "nzo_ids": g["nzo_ids"],
                "last_at": g["last_at"].isoformat() if isinstance(g["last_at"], datetime) else g["last_at"],
                "source": "sabnzbd",
            },
        )
        # the same error again after it had resolved itself: show it again (a dismissal is kept)
        db.execute(
            "UPDATE recommendations SET status='open', updated_at=now() WHERE dedupe_key=%s AND status='resolved'",
            (key,),
        )
    resolved = db.execute(
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE status='open' "
        "AND dedupe_key LIKE 'usenet.unpack:%%' AND NOT (dedupe_key = ANY(%s))",
        (active,),
    )
    return {"open": len(active), "resolved": resolved}


def open_items() -> list[dict[str, Any]]:
    return get_db().fetchall(
        "SELECT id, title, explanation, evidence, updated_at FROM recommendations WHERE status='open' "
        "AND dedupe_key LIKE 'usenet.unpack:%%' ORDER BY updated_at DESC"
    )


@jobs.handler("usenet.unpack_check")
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    return check()


def _sab_configured() -> bool:
    return cb.is_configured("sabnzbd")


jobs.every("usenet.unpack_check", 3600, enabled=_sab_configured)
