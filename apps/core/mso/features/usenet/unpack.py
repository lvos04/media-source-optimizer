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

An unpack-program error names no cause by itself, so it only counts once the same line came back for
:data:`MIN_COUNT` *different* releases. From then on the downloads of an active error are relabelled from
``unpack_failed`` (a release problem: recovery would blacklist the release and search again) to ``client_failure``
(``disk_full`` for a full disk) with ``meta.classification.repeated`` — so recovery skips them, Downloads → Usenet
says "SABnzbd of de opslag", and the advisory's "another release fails the same way" holds everywhere in MSO.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from mso import jobs
from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.features.usenet import failures, stagelog

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
        "SELECT id, client_ref, title, failure_reason, meta, COALESCE(finished_at, observed_at) AS at "
        "FROM download_outcomes "
        "WHERE client='sabnzbd' AND status='failed' AND COALESCE(finished_at, observed_at) > %s "
        "AND meta->'stage'->>'signature' IS NOT NULL AND meta->'stage'->>'problem' = ANY(%s) "
        "ORDER BY COALESCE(finished_at, observed_at) DESC",
        (since, sorted(stagelog.ENVIRONMENT)),
    )


def groups(now: datetime | None = None) -> list[dict[str, Any]]:
    """Repeated environment errors in the window: ``[{signature, problem, line, count, releases, complete, last_at,
    examples, nzo_ids, ids}]``, most frequent first (``releases`` = different titles)."""
    now = now or datetime.now(UTC)
    out: dict[str, dict[str, Any]] = {}
    titles: dict[str, set[str]] = {}
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
                "releases": 0,
                "complete": 0,
                "last_at": r["at"],
                "examples": [],
                "nzo_ids": [],
                "ids": [],
            },
        )
        g["count"] += 1
        titles.setdefault(sig, set()).add(str(r["title"] or r["client_ref"]).strip().lower())
        g["releases"] = len(titles[sig])
        if st.get("data_complete") is True:
            g["complete"] += 1
        if len(g["examples"]) < MAX_EXAMPLES and r["title"] not in g["examples"]:
            g["examples"].append(r["title"])
        if len(g["nzo_ids"]) < 20:
            g["nzo_ids"].append(r["client_ref"])
        g["ids"].append(r["id"])
    return sorted(out.values(), key=lambda g: (-g["count"], g["signature"]))


def repeated(g: dict[str, Any]) -> bool:
    """Enough repetitions to open the advisory: :data:`MIN_COUNT` downloads; for an unpack-program error (the line
    names no cause) :data:`MIN_COUNT` different releases — only that makes it SABnzbd/the storage, not the release."""
    if g["problem"] in stagelog.CERTAIN_ENVIRONMENT:
        return g["count"] >= MIN_COUNT
    return g["releases"] >= MIN_COUNT


def explanation(g: dict[str, Any]) -> str:
    n, complete, releases = g["count"], g["complete"], g["releases"]
    data = (
        " Bij al deze downloads was de data compleet: de servers en de releases zijn niet de oorzaak."
        if complete == n
        else f" Bij {complete} van de {n} was de data compleet." if complete else ""
    )
    spread = f" ({releases} verschillende releases)" if releases != n else ""
    examples = "; ".join(g["examples"])
    return (
        f"{n} downloads{spread} mislukten de afgelopen 7 dagen met dezelfde melding van SABnzbd: \"{g['line']}\"."
        f"{data} Waarschijnlijke oorzaak: {_HINTS.get(g['problem'], _HINTS['unpacker_error'])} Voorbeelden: "
        f"{examples}. MSO verandert hier zelf niets aan, probeert deze downloads niet opnieuw en zoekt er ook geen "
        "andere release voor: die zou op dezelfde manier mislukken. Nieuwe downloads mislukken op dezelfde manier "
        "tot dit is opgelost. Deze melding verdwijnt vanzelf als de fout 3 dagen niet meer voorkomt."
    )


def relabel_evidence(stage: dict[str, Any], g: dict[str, Any]) -> str:
    """Why a download that SABnzbd reported as "uitpakken mislukt" is not a release problem."""
    line = stage.get("unpack_line") or g["line"] or "—"
    label = stagelog.PROBLEM_LABELS.get(g["problem"] or "", "")
    head = "Data was compleet; uitpakken mislukte" if stage.get("data_complete") is True else "Uitpakken mislukte"
    if g["problem"] in stagelog.CERTAIN_ENVIRONMENT:
        why = "SABnzbd noemt zelf een oorzaak bij SABnzbd of de opslag"
    elif g["releases"] >= MIN_COUNT:
        why = f"Dezelfde melding kwam de afgelopen 7 dagen bij {g['releases']} verschillende releases voor"
    else:  # the advisory is still open and the error keeps coming back
        why = f"Dezelfde melding kwam eerder bij meerdere releases voor (zie '{TITLE}')"
    return (
        f"{head}: {line}" + (f" ({label})" if label else "") + f". {why}, dus ligt het aan SABnzbd of de opslag, niet "
        "aan de release. De servers zijn hier niet de oorzaak."
    )


def relabeled(stage: dict[str, Any], g: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(failure_reason, meta.classification)`` for a download of the active repeated error ``g``."""
    return failures.ENVIRONMENT_REASON.get(g["problem"] or "", "client_failure"), {
        "certainty": "zeker" if g["problem"] in stagelog.CERTAIN_ENVIRONMENT else "waarschijnlijk",
        "evidence": relabel_evidence(stage, g),
        "repeated": {"signature": g["signature"], "count": g["count"], "releases": g["releases"]},
    }


def _relabel(g: dict[str, Any]) -> int:
    """The downloads of an active repeated error: ``unpack_failed`` → the reason that is not a release problem."""
    db = get_db()
    n = 0
    for r in db.fetchall(
        "SELECT id, meta FROM download_outcomes WHERE id = ANY(%s) AND failure_reason='unpack_failed'", (g["ids"],)
    ):
        meta = dict(r["meta"] or {})
        reason, meta["classification"] = relabeled(meta.get("stage") or {}, g)
        db.execute("UPDATE download_outcomes SET failure_reason=%s, meta=%s WHERE id=%s",
                   (reason, jsonb(meta), r["id"]))
        n += 1
    return n


def active(now: datetime | None = None) -> list[dict[str, Any]]:
    """The repeated errors that are active now: seen within :data:`QUIET`, and :func:`repeated` (opens the advisory)
    or already shown (open/accepted advisory: it stays while the error keeps coming back, whatever the 7-day
    count)."""
    now = now or datetime.now(UTC)
    shown = {
        r["dedupe_key"]
        for r in get_db().fetchall(
            "SELECT dedupe_key FROM recommendations WHERE dedupe_key LIKE 'usenet.unpack:%%' "
            "AND status IN ('open','accepted')"
        )
    }
    return [
        g for g in groups(now)
        if g["last_at"] > now - QUIET and (repeated(g) or f"{KEY_PREFIX}{g['signature']}" in shown)
    ]


def active_map(now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """``{signature: group}`` of :func:`active` — for labelling new downloads of an active error right away
    (``history.map_slot``)."""
    return {g["signature"]: g for g in active(now)}


def check(now: datetime | None = None) -> dict[str, Any]:
    """Open/refresh one advisory per active repeated error (and relabel its downloads); resolve the ones that have
    been quiet for :data:`QUIET`."""
    db = get_db()
    keys: list[str] = []
    relabeled_n = 0
    for g in active(now):
        key = f"{KEY_PREFIX}{g['signature']}"
        keys.append(key)
        relabeled_n += _relabel(g)
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
                "releases": g["releases"],
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
    # quiet for QUIET: resolved — also when the owner had accepted it, so it shows again if the error returns
    resolved = db.execute(
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE status IN ('open','accepted') "
        "AND dedupe_key LIKE 'usenet.unpack:%%' AND NOT (dedupe_key = ANY(%s))",
        (keys,),
    )
    return {"open": len(keys), "resolved": resolved, "relabeled": relabeled_n}


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
