"""Torrent Autopilot ("set it and forget it"): everything reversible automatically, deleting never.

Runs as job ``torrent.autopilot`` (every 5 minutes while qBittorrent is configured):

1. **Prevention** — qBittorrent ≥ 4.6 preferences ``excluded_file_names_enabled`` + ``excluded_file_names``
   (newline-separated globs; matching files get priority 0 and are never downloaded). MSO merges
   ``PREVENTION_PATTERNS`` into the user's own list (never removes a user pattern), one ``automatisch_veilig``
   change (policy-bounded: ``policy._validate_patterns`` refuses any pattern that would also exclude video files
   or release/season folders). Applied automatically in Autopilot, otherwise left as a proposal; verified by
   read-back (``changes.apply``); rolling back restores the original string and the original on/off state.
   Old qBittorrent versions without the preference: nothing is proposed, ``prevention_status`` explains why.
   A proposal that was discarded or rolled back is not proposed again for ``SUPPRESS_DAYS`` (manual "Voorstel
   maken" still works).
2. **Malware cleanup prepared** — while suspicious torrents exist MSO keeps exactly ONE open "alle verdachte
   torrents" cleanup proposal (``cleanup.preview``, ``toestemming_nodig``), refreshed when the set changes (the
   outdated one is discarded). It is never applied automatically (``changes.apply`` refuses irreversible ops).
3. **Safe remedies (Autopilot only)** — reannounce a torrent whose trackers all fail; recheck ONLY a torrent
   that qBittorrent reports as ``missingFiles`` (evidence, and such a torrent is stopped so a recheck never
   starts a re-download), not when several torrents miss files at once (storage probably not mounted). Bounded
   per hour (``torrent.autopilot_remedies_per_hour``) and per torrent (back-off), audited, never deletes.

Automatic application goes through ``mso.features.automation.autopilot.propose_and_apply`` when that module
exists, else ``changes.create`` + ``changes.apply(..., automatic=True)`` when ``state.autopilot()``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from mso import changes, jobs, state
from mso.audit import audit
from mso.connectors import base as cb
from mso.db import get_db
from mso.features.torrent import policy
from mso.features.torrent import settings as ts
from mso.security import redact

LOGGER = logging.getLogger(__name__)
AREA = "torrent"
ACTOR = "autopilot"

PREVENTION_PATTERNS = (
    "*.exe", "*.scr", "*.bat", "*.cmd", "*.msi", "*.lnk", "*.js", "*.vbs", "*.ps1", "*.jar", "*.zipx",
    "*.pif", "*.hta",
    # '*.com' would also match folder names such as 'Show.S01-[site.com]' (qBittorrent checks every path
    # component), so only the disguised double extensions are excluded
    "*.mkv.com", "*.mp4.com", "*.avi.com",
)
PREVENTION_KEY = "torrent-prevention:excluded-files"
PREVENTION_TITLE = "Uitvoerbare bestanden nooit downloaden"
PREF_ENABLED = "excluded_file_names_enabled"
PREF_PATTERNS = "excluded_file_names"
SUPPRESS_DAYS = 30
REMEDY_PREFIX = "torrent-auto"  # dedupe keys torrent-autoreannounce:<hash> / torrent-autorecheck:<hash>
REANNOUNCE_BACKOFF_H = 6
RECHECK_BACKOFF_H = 24
MISSING_FILES_STORAGE_LIMIT = 3  # this many missingFiles torrents at once → storage problem, no rechecks


# ------------------------------------------------------------------ autopilot plumbing
def _ap() -> Any:
    try:
        from mso.features.automation import autopilot as ap
    except ImportError:
        return None
    return ap


def autopilot_on() -> bool:
    """May MSO apply reversible torrent changes on its own right now (Autopilot, or the torrent area's own
    'automatisch veilig toepassen' toggle; never in alleen-lezen or while paused)."""
    ap = _ap()
    if ap is not None and hasattr(ap, "enabled"):
        try:
            return bool(ap.enabled(AREA))
        except Exception:  # fall back to the mode flag
            LOGGER.debug("autopilot.enabled faalde", exc_info=True)
    return state.autopilot()


def apply_auto(change_id: int, actor: str = ACTOR) -> tuple[str | None, str | None]:
    """Apply an ``automatisch_veilig`` change unattended when allowed. Returns (``'toegepast'`` | None, reason)."""
    ch = changes.get(change_id)
    if ch["status"] == "applied":
        return "toegepast", None
    ap = _ap()
    if ap is not None and hasattr(ap, "why_not") and hasattr(ap, "apply_one"):
        reason = ap.why_not(ch, AREA)
        if reason is None and hasattr(ap, "budget_left") and ap.budget_left() <= 0:
            reason = "limiet van automatische wijzigingen per uur bereikt; volgt later"
        if reason is not None:
            return None, reason
        ok, err = ap.apply_one(ch, AREA)
        return ("toegepast", None) if ok else (None, err)
    if not autopilot_on():
        return None, "Autopilot staat uit"
    try:
        changes.apply(change_id, actor, automatic=True)
        return "toegepast", None
    except HTTPException as exc:
        return None, str(exc.detail)


def _change_from(res: Any) -> dict[str, Any] | None:
    if not isinstance(res, dict):
        return None
    if "id" in res and "status" in res and "ops" in res:
        return res
    for k in ("change", "change_set"):
        if isinstance(res.get(k), dict) and "id" in res[k]:
            return res[k]
    if isinstance(res.get("change_id"), int):
        return changes.get(res["change_id"])
    return None


def submit(
    title: str,
    ops: list[dict[str, Any]],
    *,
    explanation: str,
    impact: dict[str, Any],
    dedupe_key: str,
    actor: str = ACTOR,
    evaluate_after_h: float | None = None,
) -> dict[str, Any]:
    """Create a reversible ``automatisch_veilig`` change and apply it when allowed.
    Returns ``{change, auto, auto_reason}`` (``auto`` None = left as proposal, ``auto_reason`` says why)."""
    ap = _ap()
    if ap is not None and hasattr(ap, "propose_and_apply"):
        res: Any = None
        try:
            res = ap.propose_and_apply(
                "qbittorrent", title, ops, explanation=explanation, impact=impact, dedupe_key=dedupe_key,
                evaluate_after_h=evaluate_after_h, area=AREA,
            )
        except HTTPException:
            raise
        except TypeError:  # contract mismatch → the documented fallback below
            LOGGER.warning("autopilot.propose_and_apply: ander contract; terugval op changes.create", exc_info=True)
        ch = _change_from(res)
        if ch is not None:
            cur = changes.get(ch["id"])
            applied = cur["status"] == "applied"
            return {"change": cur, "auto": "toegepast" if applied else None,
                    "auto_reason": None if applied else res.get("auto_reason")}
    ch = changes.create(
        "qbittorrent", title, ops, risk="automatisch_veilig", explanation=explanation, impact=impact,
        source=f"automation:{AREA}", actor=actor, dedupe_key=dedupe_key, evaluate_after_h=evaluate_after_h,
    )
    auto, reason = apply_auto(ch["id"], actor)
    return {"change": changes.get(ch["id"]), "auto": auto, "auto_reason": reason}


# ------------------------------------------------------------------ 1. prevention
def _adapter() -> Any:
    from mso.features.torrent.adapters import get_adapter

    return get_adapter("qbittorrent")


def merged_patterns(current: Any) -> tuple[list[str], list[str]]:
    """(merged list, patterns MSO adds). User patterns stay first and are never removed."""
    cur = policy.split_patterns(current)
    have = {p.lower() for p in cur}
    add = [p for p in PREVENTION_PATTERNS if p.lower() not in have]
    return cur + add, add


def _open_prevention() -> dict[str, Any] | None:
    return get_db().fetchone(
        "SELECT * FROM change_sets WHERE dedupe_key=%s AND status IN ('preview','approved') ORDER BY id DESC "
        "LIMIT 1",
        (PREVENTION_KEY,),
    )


def _last_prevention() -> dict[str, Any] | None:
    return get_db().fetchone(
        "SELECT * FROM change_sets WHERE dedupe_key=%s ORDER BY id DESC LIMIT 1", (PREVENTION_KEY,)
    )


def _suppressed() -> str | None:
    row = get_db().fetchone(
        "SELECT status FROM change_sets WHERE dedupe_key=%s AND status IN ('discarded','rolled_back') "
        "AND COALESCE(rolled_back_at, created_at) > now() - make_interval(days => %s) ORDER BY id DESC LIMIT 1",
        (PREVENTION_KEY, SUPPRESS_DAYS),
    )
    if row is None:
        return None
    what = "teruggedraaid" if row["status"] == "rolled_back" else "verworpen"
    return (
        f"het vorige voorstel is {what}; MSO stelt dit {SUPPRESS_DAYS} dagen niet zelf opnieuw voor "
        "(maak het handmatig via Torrentclient → Preventie)"
    )


def prevention_state(prefs: dict[str, Any] | None) -> dict[str, Any]:
    """Pure: status of the prevention setting in a preferences dict (snapshot or live)."""
    prefs = prefs or {}
    supported = PREF_ENABLED in prefs and PREF_PATTERNS in prefs
    current = policy.split_patterns(prefs.get(PREF_PATTERNS)) if supported else []
    _, missing = merged_patterns(current)
    enabled = bool(prefs.get(PREF_ENABLED)) if supported else False
    active = supported and enabled and not missing
    return {
        "supported": supported,
        "enabled": enabled,
        "active": active,
        "patterns": current,
        "missing": missing,
        "user_patterns": [p for p in current if p.lower() not in {x.lower() for x in PREVENTION_PATTERNS}],
    }


def prevention_status() -> dict[str, Any]:
    """Read-only view for the UI (from the last snapshot; no qBittorrent call)."""
    snap = get_db().fetchone("SELECT version, preferences, updated_at FROM torrent_client_snapshot WHERE id=1")
    prefs = (snap or {}).get("preferences") or {}
    st = prevention_state(prefs)
    version = (snap or {}).get("version")
    if snap is None:
        why = "Nog geen gegevens van qBittorrent."
    elif not st["supported"]:
        why = (
            f"Deze qBittorrent-versie ({version or 'onbekend'}) heeft de instelling 'bestandsnamen uitsluiten' "
            "niet (pas vanaf 4.6). Werk qBittorrent bij; tot die tijd vangt de malware-opruiming nep-releases af."
        )
    elif st["active"]:
        why = "Actief: bestanden met deze extensies worden door qBittorrent nooit gedownload."
    elif autopilot_on():
        why = "Autopilot zet deze instelling automatisch aan."
    else:
        why = "Staat nog niet (volledig) aan. MSO maakt een voorstel; in Autopilot gebeurt dit automatisch."
    open_ch = _open_prevention()
    last = _last_prevention()
    return {
        **st,
        "version": version,
        "updated_at": (snap or {}).get("updated_at"),
        "recommended": list(PREVENTION_PATTERNS),
        "message": why,
        "autopilot": autopilot_on(),
        "open_change": dict(open_ch) if open_ch else None,
        "last_change": {k: last[k] for k in ("id", "status", "title", "applied_at", "applied_by")} if last else None,
        "suppressed": _suppressed(),
    }


def prevention_note() -> str:
    """One sentence for the cleanup preview about whether prevention is active."""
    try:
        st = prevention_status()
    except Exception:  # only a hint
        return ""
    if st["active"]:
        return " (staat aan)"
    if not st["supported"]:
        return " (niet beschikbaar in deze qBittorrent-versie; werk bij naar ≥ 4.6)"
    return " (staat nog uit: zie Torrentclient → Preventie)"


def ensure_prevention(actor: str = ACTOR, *, manual: bool = False) -> dict[str, Any]:
    """Create (and in Autopilot apply) the prevention change. Returns ``{change, auto}`` or ``{reason}``."""
    if not cb.is_configured("qbittorrent"):
        return {"reason": "qBittorrent is niet gekoppeld"}
    open_ch = _open_prevention()
    if open_ch is not None:
        auto, reason = apply_auto(open_ch["id"], actor) if open_ch["risk"] == "automatisch_veilig" else (None, None)
        return {"change": changes.get(open_ch["id"]), "auto": auto, "auto_reason": reason}
    if not manual and (why := _suppressed()):
        return {"reason": why}
    try:
        ad = _adapter()
        enabled_raw = ad.read_value(f"pref:{PREF_ENABLED}")
        patterns_raw = ad.read_value(f"pref:{PREF_PATTERNS}")
    except Exception as exc:
        return {"reason": f"qBittorrent niet bereikbaar: {redact(str(exc))[:160]}"}
    if enabled_raw is None or patterns_raw is None:
        return {
            "reason": "Deze qBittorrent-versie heeft de instelling 'bestandsnamen uitsluiten' niet (pas vanaf "
            "4.6); werk qBittorrent bij",
            "supported": False,
        }
    before = policy.join_patterns(policy.split_patterns(patterns_raw))
    merged, added = merged_patterns(patterns_raw)
    after = policy.join_patterns(merged)
    if bool(enabled_raw) and not added:
        return {"reason": "Al actief: uitvoerbare bestanden worden nooit gedownload", "active": True}
    try:
        policy.validate(f"pref:{PREF_PATTERNS}", after)
    except policy.PolicyError as exc:
        return {"reason": f"Je huidige uitsluitlijst wordt door het beleid geweigerd: {exc}"}
    ops: list[dict[str, Any]] = []
    if added:
        ops.append({
            "path": f"pref:{PREF_PATTERNS}",
            "label": f"Uitgesloten bestandsnamen: + {', '.join(added)}",
            "before": before,
            "after": after,
        })
    if not bool(enabled_raw):
        ops.append({
            "path": f"pref:{PREF_ENABLED}",
            "label": "Bestandsnamen uitsluiten aanzetten",
            "before": bool(enabled_raw),
            "after": True,
        })
    user = [p for p in policy.split_patterns(patterns_raw) if p.lower() not in {x.lower() for x in added}]
    explanation = (
        "qBittorrent kan bestanden met bepaalde namen overslaan: ze worden nooit gedownload. MSO zet dat aan voor "
        "programma's, scripts en snelkoppelingen (" + ", ".join(PREVENTION_PATTERNS) + "). Zo komt de malware "
        "uit nep-releases nooit op je schijf, ook als Sonarr/Radarr zo'n release opnieuw grijpt. Video, "
        "ondertitels en mappen worden nooit uitgesloten (beleid). "
        + (f"Je eigen {len(user)} patroon/patronen blijven staan" + (" en worden ook actief" if not enabled_raw
                                                                     else "") + ". " if user else "")
        + "Terugdraaien zet de oorspronkelijke lijst en de oorspronkelijke aan/uit-stand terug."
    )
    impact = {
        "Soort": "beleidsgrens (preventie), geen meting",
        "Toegevoegd": ", ".join(added) if added else "geen (lijst was al compleet)",
        "Eigen patronen": ", ".join(user) if user else "geen",
        "Geldt voor": "nieuwe torrents (bestaande downloads houden hun bestandskeuze)",
        "Controle": "MSO leest de instelling na het schrijven terug uit qBittorrent",
        "Terugdraaien": "oorspronkelijke lijst en aan/uit-stand terug",
    }
    res = submit(
        PREVENTION_TITLE, ops, explanation=explanation, impact=impact, dedupe_key=PREVENTION_KEY,
        actor=actor,
    )
    audit(
        actor,
        "torrent.prevention_proposed" if res["auto"] != "toegepast" else "torrent.prevention_applied",
        f"Preventie '{PREVENTION_TITLE}': " + ("automatisch toegepast (Autopilot)" if res["auto"] == "toegepast"
                                                 else "voorstel klaargezet"),
        target="qbittorrent",
        change_id=res["change"]["id"],
        detail={"added": added, "auto": res["auto"]},
    )
    return res


# ------------------------------------------------------------------ 2. malware cleanup kept ready
def _open_cleanups() -> list[dict[str, Any]]:
    from mso.features.torrent import cleanup

    out = []
    for ch in get_db().fetchall(
        "SELECT * FROM change_sets WHERE target='qbittorrent' AND source=%s AND status IN ('preview','approved') "
        "ORDER BY id DESC",
        (cleanup.SOURCE,),
    ):
        guard = next((o for o in ch["ops"] if cleanup.GUARD_RE.match(str(o.get("path") or ""))), None)
        pv = cleanup.load_preview(cleanup.GUARD_RE.match(guard["path"])[1]) if guard else None
        if pv is not None and pv["scope"] == "all":
            out.append({**dict(ch), "_hashes": pv["hashes"]})
    return out


def _db_suspicious() -> list[str]:
    from mso.features.torrent.api import SUS_SQL

    return sorted(r["hash"] for r in get_db().fetchall(
        f"SELECT hash FROM torrent_state WHERE NOT removed AND {SUS_SQL}"
    ))


def maintain_cleanup_proposal(actor: str = ACTOR) -> dict[str, Any]:
    """Keep ONE open 'alle verdachte torrents' cleanup proposal in step with the scan. Never applies it."""
    from mso.features.torrent import cleanup

    if not cb.is_configured("qbittorrent"):
        return {"reason": "qBittorrent is niet gekoppeld"}
    sus = _db_suspicious()
    opens = _open_cleanups()
    if not sus:
        for ch in opens:
            changes.discard(ch["id"], actor)
        return {"discarded": [c["id"] for c in opens], "change": None}
    if len(opens) == 1 and opens[0]["_hashes"] == sus:
        return {"change": changes.get(opens[0]["id"]), "refreshed": False}
    try:
        ch = cleanup.preview(None, actor)
    except HTTPException as exc:
        if exc.status_code == 409 and "Geen verdachte" in str(exc.detail):
            for o in opens:
                changes.discard(o["id"], actor)
            return {"discarded": [c["id"] for c in opens], "change": None}
        return {"reason": str(exc.detail), "change": None}
    stale = [o for o in opens if o["id"] != ch["id"]]
    for o in stale:
        changes.discard(o["id"], actor)
    created = not any(o["id"] == ch["id"] for o in opens)
    if created:
        n = sum(1 for o in ch["ops"] if str(o["path"]).startswith("torrent:"))
        audit(
            actor,
            "torrent.cleanup_proposed" if not stale else "torrent.cleanup_refreshed",
            (f"Opruimvoorstel klaargezet ({n} verdachte torrents); wacht op goedkeuring" if not stale
             else f"Opruimvoorstel ververst: lijst gewijzigd, nu {n} verdachte torrents; wacht op goedkeuring"),
            target="qbittorrent",
            change_id=ch["id"],
            detail={"torrents": n, "replaced": [o["id"] for o in stale]},
        )
    return {"change": ch, "refreshed": bool(stale), "created": created, "discarded": [o["id"] for o in stale]}


# ------------------------------------------------------------------ 3. safe remedies (Autopilot only)
def _remedies_last_hour() -> int:
    return int(get_db().scalar(
        "SELECT count(*) FROM change_sets WHERE dedupe_key LIKE %s AND status IN ('applied','failed','rolled_back') "
        "AND created_at > now() - interval '1 hour'",
        (REMEDY_PREFIX + "%",),
    ) or 0)


def _recent(h: str, action: str, hours: float) -> bool:
    return bool(get_db().scalar(
        "SELECT count(*) FROM change_sets WHERE dedupe_key=%s AND created_at > now() - make_interval(hours => %s)",
        (f"{REMEDY_PREFIX}{action}:{h}", hours),
    )) or bool(get_db().scalar(
        "SELECT count(*) FROM torrent_actions WHERE hash=%s AND action=%s AND ts > now() - make_interval(hours => %s)",
        (h, action, hours),
    ))


def remedy_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Pure-ish selection from ``torrent_state`` rows → ([{hash, name, action, why}], notes)."""
    notes: list[str] = []
    out: list[dict[str, Any]] = []
    missing = [r for r in rows if r.get("state") == "missingFiles"]
    storage_down = len(missing) >= MISSING_FILES_STORAGE_LIMIT
    if storage_down:
        notes.append(
            f"{len(missing)} torrents missen tegelijk bestanden: de opslag lijkt niet gekoppeld. MSO doet dan "
            "geen recheck (controleer de schijf/mount)."
        )
    for r in rows:
        codes = {f.get("code") for f in ((r.get("diagnosis") or {}).get("findings") or [])}
        if "suspicious_payload" in codes:
            continue  # malware: only the cleanup proposal applies
        if "tracker_error" in codes and not _recent(r["hash"], "reannounce", REANNOUNCE_BACKOFF_H):
            out.append({"hash": r["hash"], "name": r["name"], "action": "reannounce",
                        "why": "alle trackers geven een fout"})
        elif (r.get("state") == "missingFiles" and "missing_files" in codes and not storage_down
              and not _recent(r["hash"], "recheck", RECHECK_BACKOFF_H)):
            out.append({"hash": r["hash"], "name": r["name"], "action": "recheck",
                        "why": "qBittorrent meldt ontbrekende bestanden (torrent staat stil)"})
    return out, notes


def auto_remedies(actor: str = ACTOR) -> dict[str, Any]:
    if not (state.autopilot() and autopilot_on()):
        return {"reason": "alleen in Autopilot", "applied": []}
    limit = int(ts.get("autopilot_remedies_per_hour"))
    budget = limit - _remedies_last_hour()
    if budget <= 0:
        return {"reason": f"maximum van {limit} herstelacties per uur bereikt", "applied": []}
    rows = get_db().fetchall(
        "SELECT hash, name, state, diagnosis FROM torrent_state WHERE NOT removed ORDER BY updated_at DESC"
    )
    cands, notes = remedy_candidates(rows)
    if not cands:
        return {"applied": [], "notes": notes}
    try:
        live = {str(t.get("hash") or "").lower(): t for t in _adapter().live_torrents()}
    except Exception as exc:
        return {"reason": f"qBittorrent niet bereikbaar: {redact(str(exc))[:160]}", "applied": []}
    applied: list[dict[str, Any]] = []
    for c in cands:
        if budget <= 0:
            notes.append(f"maximum van {limit} herstelacties per uur bereikt; de rest volgt later")
            break
        t = live.get(c["hash"])
        if t is None:
            continue
        if c["action"] == "recheck" and t.get("state") != "missingFiles":
            continue  # live state changed: no evidence any more
        title = ("Opnieuw aankondigen" if c["action"] == "reannounce" else "Recheck") + f": {c['name'][:120]}"
        expl = (
            "Autopilot: alle trackers van deze torrent geven een fout. Opnieuw aankondigen vraagt de tracker(s) "
            "opnieuw om peers; onschadelijk, voegt geen trackers toe."
            if c["action"] == "reannounce" else
            "Autopilot: qBittorrent meldt ontbrekende bestanden en de torrent staat stil. Een recheck controleert "
            "wat er op schijf staat; er wordt niets verwijderd, verplaatst of opnieuw gedownload (de torrent blijft "
            "gestopt)."
        )
        try:
            res = submit(
                title,
                [{"path": f"torrent:{c['hash']}:{c['action']}", "label": title, "before": "niet_uitgevoerd",
                  "after": "uitgevoerd", "one_way": True}],
                explanation=expl,
                impact={"Soort": "herstelactie (eenmalig), geen meting", "Torrent": c["name"][:200],
                        "Reden": c["why"], "Status in qBittorrent": str(t.get("state") or "onbekend")},
                dedupe_key=f"{REMEDY_PREFIX}{c['action']}:{c['hash']}",
                actor=actor,
            )
        except HTTPException as exc:
            notes.append(f"{c['name'][:60]}: {exc.detail}")
            continue
        budget -= 1
        applied.append({"hash": c["hash"], "action": c["action"], "result": res["auto"],
                        "change_id": res["change"]["id"]})
        audit(
            actor,
            "torrent.autopilot_remedy",
            f"Autopilot {('opnieuw aankondigen' if c['action'] == 'reannounce' else 'recheck')}: "
            f"{c['name'][:120]} ({res['auto'] or 'voorstel'})",
            target="qbittorrent",
            change_id=res["change"]["id"],
            detail={"hash": c["hash"], "action": c["action"], "why": c["why"], "result": res["auto"]},
        )
    return {"applied": applied, "notes": notes}


# ------------------------------------------------------------------ job
def run(actor: str = ACTOR) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # prevention first, so a cleanup preview made in the same run states correctly whether it is active
    for name, fn in (("prevention", ensure_prevention), ("cleanup", maintain_cleanup_proposal),
                     ("remedies", auto_remedies)):
        try:
            res = fn(actor)
        except HTTPException as exc:
            res = {"reason": str(exc.detail)}
        except Exception as exc:  # one part failing must not stop the others
            LOGGER.warning("torrent-autopilot %s faalde: %s", name, redact(str(exc)))
            res = {"reason": redact(str(exc))[:200]}
        out[name] = {k: (v["id"] if isinstance(v, dict) and "id" in v else v) for k, v in res.items()}
    return out


@jobs.handler("torrent.autopilot")
def _autopilot_job(ctx: jobs.JobContext) -> dict[str, Any]:
    return run()


jobs.every("torrent.autopilot", 300, enabled=lambda: cb.is_configured("qbittorrent"))
