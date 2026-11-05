"""Failed download → other candidate (1.4.0).

When a download fails (``download_outcomes.status='failed'``, usenet or torrent) the ``recovery.analyze`` job:

1. puts the failed release on the blacklist (once per outcome; the hard cap/TTL escalation of
   :mod:`blacklist` applies);
2. maps the outcome to a wanted item: an earlier MSO send, the MediaGuard wanted cache (ids, or series/film
   name + exact numbering), or — standalone only — the outcome's own media metadata (category + parsed
   title) as a manual wanted item. When no mapping is possible the reason is recorded and the outcome is
   skipped;
3. runs the existing "Zoek fallback-kandidaten" path (:func:`candidates.find_candidates`, MediaGuard's
   quality requirements, never relaxed);
4. turns the best accepted candidate into a change set *"Versturen naar SABnzbd/qBittorrent"* on the
   ``download_send`` applier. Default risk ``toestemming_nodig`` (op marked ``irreversible``); only with
   ``recovery.auto_send_fallback`` on it is ``automatisch_veilig`` so Automatisering may apply it.

Applying goes through :func:`actions.send` — the same server-side quality gate, ``herstel.manual_sends_per_hour``
quota, per-item backoff, idempotency key and audit as a manual send. The applier's read-back checks that the
client really has the download (SABnzbd queue/history nzo_id, qBittorrent infohash). Sending cannot be undone:
MSO never deletes downloads, the preview says so.
"""

from __future__ import annotations

import importlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import get_db, jsonb
from mso.domain import categories
from mso.domain.outcomes import FAILURE_REASONS, recommend
from mso.domain.release import is_episodic, parse_title, title_part, title_similarity
from mso.features.recovery import actions, blacklist, candidates
from mso.security import redact

LOGGER = logging.getLogger(__name__)
TARGET = "download_send"
ACTOR = "systeem:herstel"
BEFORE, AFTER = "niet verstuurd", "verstuurd"
MAX_AGE_H = 72  # older failures are history, not something to fix now
CLIENT_LABEL = {"sabnzbd": "SABnzbd", "qbittorrent": "qBittorrent"}
# Causes outside the release itself: another release would fail the same way.
NOT_RELEASE_CAUSES = {
    "auth_failure",
    "account_expired",
    "quota_exceeded",
    "disk_full",
    "client_failure",
    "import_failure",
    "no_indexer_hit",
}
_PATH_RE = re.compile(r"^send:(?P<ref>(?:mg|manual):\d+):(?P<key>(?:nzb|mid|btih|btmh|guid):.+)$")


def _now() -> datetime:
    return datetime.now(UTC)


def op_path(media_ref: str, release_key: str) -> str:
    """Same string as the recovery_sends idempotency key, so a read can find the send."""
    return actions.idempotency_key(media_ref, release_key)


def parse_path(path: str) -> tuple[str, str]:
    m = _PATH_RE.match(path or "")
    if not m:
        raise ValueError(f"ongeldig pad '{path}' (verwacht send:<item>:<release>)")
    return m.group("ref"), m.group("key")


# ------------------------------------------------------------------ applier
def _client_has(row: dict[str, Any]) -> bool:
    """Read-back at the source: is the download really in the client?"""
    ref = row.get("client_ref")
    if not ref:
        return False
    if row["client"] == "sabnzbd":
        mod = importlib.import_module("mso.connectors.sabnzbd")
        sab = mod.SabClient.from_config()
        try:
            queue = sab.queue(limit=500) or {}
            if any(str(s.get("nzo_id")) == ref for s in queue.get("slots") or []):
                return True
            hist = sab.history(limit=500) or {}
            return any(str(s.get("nzo_id")) == ref for s in hist.get("slots") or [])
        finally:
            close = getattr(sab, "close", None)
            if callable(close):
                close()
    mod = importlib.import_module("mso.connectors.qbittorrent")
    qb = mod.QbitClient.from_config()
    for attempt in range(4):  # qBittorrent adds asynchronously: give it a moment
        if qb.torrent(ref.lower()):
            return True
        if attempt < 3:
            time.sleep(0.75)
    return False


def _precheck_quota(media_ref: str) -> None:
    """Before anything is written: refuse (change stays open) when a limit is reached."""
    per_hour = int(sr.get("herstel.manual_sends_per_hour"))
    if actions.sends_used_last_hour() >= per_hour:
        raise HTTPException(429, detail=f"Limiet van {per_hour} verzendingen per uur bereikt; het voorstel blijft open")
    ok, until = blacklist.media_allowed(media_ref)
    if not ok and until is not None:
        raise HTTPException(
            429,
            detail=f"Dit item faalde eerder; wacht tot {until.isoformat(timespec='minutes')} "
            "(exponential backoff). Het voorstel blijft open.",
        )


def _read(path: str) -> str:
    media_ref, _key = parse_path(path)
    row = get_db().fetchone("SELECT * FROM recovery_sends WHERE idempotency_key=%s", (path,))
    if row is None:
        _precheck_quota(media_ref)
        return BEFORE
    if row["status"] == "pending":
        return "bezig met versturen"
    if row["status"] in ("download_ok", "download_failed"):
        return AFTER
    label = CLIENT_LABEL.get(row["client"], row["client"])
    try:
        found = _client_has(dict(row))
    except Exception as exc:  # client unreachable: cannot confirm
        return f"niet te controleren in {label}: {redact(str(exc))[:120]}"
    return AFTER if found else f"niet gevonden in {label}"


def _write(path: str, value: Any) -> None:
    media_ref, key = parse_path(path)
    if value != AFTER:
        raise RuntimeError(
            "versturen kan niet ongedaan worden gemaakt: MSO verwijdert nooit downloads. Verwijder de "
            "download zo nodig zelf in de downloadclient"
        )
    try:
        actions.send(media_ref, key, actor=ACTOR, via="change")
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from exc


def _validate(path: str, value: Any) -> None:
    parse_path(path)
    if value not in (AFTER, BEFORE):
        raise ValueError("waarde moet 'verstuurd' zijn")


def build_applier() -> changes.Applier:
    return changes.Applier(read=_read, write=_write, validate=_validate)


changes.register_applier(TARGET, build_applier)


def _evaluate(change: dict[str, Any]) -> str:
    """Post-apply evaluation for Automatisering: kept when the replacement downloaded fine. Never 'rollback':
    a sent download cannot be undone by MSO."""
    for op in change.get("ops") or []:
        row = get_db().fetchone("SELECT status FROM recovery_sends WHERE idempotency_key=%s", (op["path"],))
        if row and row["status"] == "download_ok":
            return "keep"
    return "unknown"


try:  # optional: Slice E's evaluator registry
    from mso.features.automation import register_evaluator

    register_evaluator(TARGET, _evaluate)
except Exception:  # pragma: no cover - automation module absent
    LOGGER.debug("automatisering niet beschikbaar; geen evaluator voor %s", TARGET)


# ------------------------------------------------------------------ mapping failed outcome → wanted item
def _meta_media(o: dict[str, Any]) -> dict[str, Any]:
    meta = o.get("meta") or {}
    media = meta.get("media") if isinstance(meta.get("media"), dict) else {}
    out = {}
    for k in ("imdb_id", "tvdb_id", "tmdb_id", "title", "season", "episode", "absolute_episode", "year"):
        v = media.get(k, meta.get(k))
        if v not in (None, ""):
            out[k] = v
    return out


def _guess_category(o: dict[str, Any], p: Any) -> str | None:
    if o.get("media_category") in categories.MEDIA_CATEGORIES:
        return str(o["media_category"])
    cat = str(o.get("client_category") or "").lower().removeprefix("mso-")
    if cat in categories.MEDIA_CATEGORIES:
        return cat
    return None


def _mg_by_ids(media: dict[str, Any], p: Any) -> list[dict[str, Any]]:
    conds, params = [], []
    for k in ("imdb_id", "tvdb_id", "tmdb_id"):
        if media.get(k):
            conds.append(f"raw->>'{k}' = %s")
            params.append(str(media[k]))
    if not conds:
        return []
    sql = "SELECT * FROM mg_wanted_cache WHERE (" + " OR ".join(conds) + ")"
    season = media.get("season", p.season)
    episode = media.get("episode", p.episodes[0] if len(p.episodes) == 1 else None)
    if season is not None and episode is not None:
        sql += " AND season_number=%s AND episode_number=%s"
        params += [int(season), int(episode)]
    elif p.absolute_episodes and len(p.absolute_episodes) == 1:
        sql += " AND absolute_episode_number=%s"
        params.append(p.absolute_episodes[0])
    else:
        sql += " AND kind='movie'"
    return get_db().fetchall(sql + " LIMIT 5", params)


def _mg_by_title(o: dict[str, Any], p: Any, cat: str | None) -> tuple[list[dict[str, Any]], float]:
    db = get_db()
    params: list[Any] = []
    if p.season is not None and len(p.episodes) == 1:
        where = "season_number=%s AND episode_number=%s"
        params = [p.season, p.episodes[0]]
    elif len(p.absolute_episodes) == 1:
        where = "absolute_episode_number=%s"
        params = [p.absolute_episodes[0]]
    elif not is_episodic(p):
        where = "kind='movie'"
    else:
        return [], 0.0
    if cat:
        where += " AND media_category=%s"
        params.append(cat)
    rows = db.fetchall(f"SELECT * FROM mg_wanted_cache WHERE {where} LIMIT 2000", params)
    best: list[dict[str, Any]] = []
    best_score = 0.0
    for r in rows:
        name = r["item_title"] or candidates.RE_YEAR_SUFFIX.sub("", r["title"] or "").strip()
        sim = title_similarity(name, o["title"]) or 0.0
        if r["kind"] == "movie" and p.year:
            m = candidates.RE_YEAR_SUFFIX.search(r["title"] or "")
            if m and int(m.group(1)) != p.year:
                continue
        if sim > best_score:
            best, best_score = [dict(r)], sim
        elif sim == best_score and sim > 0:
            best.append(dict(r))
    return best, best_score


def _manual_from_outcome(o: dict[str, Any], p: Any, cat: str, media: dict[str, Any]) -> int | None:
    name = str(media.get("title") or title_part(o["title"])).strip()
    if not name:
        return None
    season = media.get("season", p.season)
    episode = media.get("episode", p.episodes[0] if len(p.episodes) == 1 else None)
    absolute = media.get("absolute_episode", p.absolute_episodes[0] if len(p.absolute_episodes) == 1 else None)
    year = media.get("year", p.year)
    tv = cat.startswith("tv")
    if tv and episode is None and absolute is None:
        return None
    if tv and episode is not None and season is None:
        return None
    if not tv:
        season = episode = absolute = None
    db = get_db()
    existing = db.scalar(
        "SELECT id FROM recovery_manual_wanted WHERE status='open' AND lower(title)=lower(%s) AND "
        "media_category=%s AND season_number IS NOT DISTINCT FROM %s AND episode_number IS NOT DISTINCT FROM %s "
        "AND absolute_episode_number IS NOT DISTINCT FROM %s AND (year IS NOT DISTINCT FROM %s OR %s) LIMIT 1",
        (name, cat, season, episode, absolute, year, tv),
    )
    if existing:
        return int(existing)
    row = db.fetchone(
        "INSERT INTO recovery_manual_wanted(title,media_category,season_number,episode_number,"
        "absolute_episode_number,year,imdb_id,tvdb_id,tmdb_id,requirements,created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            name,
            cat,
            season,
            episode,
            absolute,
            None if tv else year,
            media.get("imdb_id"),
            int(media["tvdb_id"]) if str(media.get("tvdb_id") or "").isdigit() else None,
            int(media["tmdb_id"]) if str(media.get("tmdb_id") or "").isdigit() else None,
            jsonb({"forbid_hardsub": True}),
            ACTOR,
        ),
    )
    audit(
        ACTOR,
        "recovery.manual_added",
        f"Ontbrekend item aangemaakt na mislukte download: {name}",
        target=f"manual:{row['id']}",
        detail={"outcome_title": o["title"]},
    )
    return int(row["id"])


def map_outcome(o: dict[str, Any], *, connected: bool) -> tuple[str | None, str | None, str | None]:
    """Returns (media_ref, mapping, reason_if_unmapped)."""
    db = get_db()
    send = db.fetchone(
        "SELECT media_ref FROM recovery_sends WHERE client=%s AND (client_ref=%s OR release_key=%s) "
        "ORDER BY id DESC LIMIT 1",
        (o["client"], o["client_ref"], o["release_key"] or ""),
    )
    if send:
        return send["media_ref"], "send", None
    p = parse_title(o["title"])
    media = _meta_media(o)
    cat = _guess_category(o, p)
    ids = _mg_by_ids(media, p)
    if len({r["mg_id"] for r in ids}) == 1:
        return f"mg:{ids[0]['mg_id']}", "mediaguard_ids", None
    if p.obfuscated or not title_part(o["title"]):
        return None, None, "Titel is niet herleidbaar (geobfusceerd): geen ontbrekend item te bepalen"
    if p.full_season and not p.episodes:
        return None, None, "Volledig seizoen: geen afzonderlijke aflevering om te vervangen"
    if len(p.episodes) > 1 or len(p.absolute_episodes) > 1:
        return None, None, "Meerdere afleveringen in één release: geen eenduidig ontbrekend item"
    rows, sim = _mg_by_title(o, p, cat)
    if rows and sim >= 0.9:
        if len({r["mg_id"] for r in rows}) > 1:
            return None, None, "Meerdere MediaGuard-items passen bij deze titel en nummering; niet eenduidig"
        return f"mg:{rows[0]['mg_id']}", "mediaguard_title", None
    if connected:
        return (
            None,
            None,
            "Niet (meer) gezocht in MediaGuard: geen MediaGuard-item met deze titel en nummering. "
            "MediaGuard is eigenaar van ontbrekende items; MSO maakt geen eigen item aan.",
        )
    if cat is None:
        return None, None, "Categorie onbekend (downloadcategorie niet gekoppeld): geen ontbrekend item"
    manual_id = _manual_from_outcome(o, p, cat, media)
    if manual_id is None:
        return None, None, "Geen titel of afleveringsnummer te herleiden uit de mislukte download"
    return f"manual:{manual_id}", "outcome_meta", None


# ------------------------------------------------------------------ proposal
def _source_label(source_id: str | None) -> str:
    if not source_id:
        return "onbekend"
    label = get_db().scalar("SELECT label FROM sources WHERE id=%s", (source_id,))
    return f"{label} ({source_id})" if label else source_id


def _size(b: int | None) -> str:
    return f"{b / 1e9:.1f} GB" if b else "onbekend"


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def pick_candidate(res: dict[str, Any], failed_key: str | None, failed_title: str) -> dict[str, Any] | None:
    for c in res.get("accepted") or []:
        if not c.get("has_download"):
            continue
        if failed_key and c["release_key"] == failed_key:
            continue
        if _norm(c["title"]) == _norm(failed_title):
            continue
        return c
    return None


def propose(o: dict[str, Any], media_ref: str, cand: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    auto = bool(sr.get("recovery.auto_send_fallback"))
    t = res["target"]
    client = "sabnzbd" if cand["protocol"] == "usenet" else "qbittorrent"
    label = CLIENT_LABEL[client]
    category = actions.client_category(t["media_category"])
    reason_label = FAILURE_REASONS.get(o.get("failure_reason") or "unknown", "Onbekende oorzaak")
    evidence = [
        f"{'✓' if e['ok'] else '✗'} {e['label']}: vereist {e['required']} · release {e['actual']}"
        for e in cand.get("evidence") or []
    ]
    undo = (
        "Niet mogelijk: een verstuurde download kan MSO niet terugdraaien (MSO verwijdert nooit downloads). "
        f"Verwijder hem zo nodig zelf in {label}."
    )
    op: dict[str, Any] = {
        "path": op_path(media_ref, cand["release_key"]),
        "label": f"{cand['title']} → {label} (categorie {category})",
        "before": BEFORE,
        "after": AFTER,
    }
    if not auto:
        op["irreversible"] = True  # never automatic; rollback skips it
    explanation = (
        f"De download '{o['title']}' is mislukt ({reason_label}). "
        + (
            "Die release staat nu op de blacklist. "
            if o.get("release_key")
            else "Die release heeft geen release-id; MSO sluit hem uit op titel. "
        )
        + f"MSO zocht een vervanger voor '{t['title']}' die aan alle kwaliteitseisen voldoet "
        f"({', '.join(res['requirements'].get('sources') or [])}). Toepassen haalt de NZB/torrent op via MSO "
        f"en voegt hem toe aan {label} in categorie '{category}'; daarna controleert MSO of {label} de download "
        "echt heeft. Terugdraaien is niet mogelijk."
    )
    impact = {
        "Ontbrekend item": f"{t['title']} ({media_ref})",
        "Mislukte release": f"{o['title']} — {reason_label}"
        + (f" · {o['release_key']}" if o.get("release_key") else ""),
        "Vervangende release": cand["title"],
        "Release-id": cand["release_key"],
        "Bron": _source_label(cand.get("source_id")),
        "Protocol": "Usenet" if cand["protocol"] == "usenet" else "Torrent",
        "Grootte": _size(cand.get("size_bytes")),
        "Score": cand.get("score"),
        "Kwaliteitscontrole": evidence,
        "Doel": f"{label} · categorie '{category}'",
        "Limieten": (
            f"max. {int(sr.get('herstel.manual_sends_per_hour'))} verzendingen per uur, wachttijd per item na "
            "mislukking, één keer per item + release"
        ),
        "Geldig tot": (datetime.now(UTC) + timedelta(hours=float(sr.get("herstel.candidate_ttl_h")))).isoformat(
            timespec="seconds"
        ),
        "Terugdraaien": undo,
    }
    ch = changes.create(
        TARGET,
        f"Versturen naar {label}: {cand['title']}",
        [op],
        risk="automatisch_veilig" if auto else "toestemming_nodig",
        explanation=explanation,
        impact=impact,
        source="recovery",
        actor=ACTOR,
        dedupe_key=f"fallback:{media_ref}",
        evaluate_after_h=24,
    )
    recommend(
        "recovery",
        f"Mislukte download vervangen: {t['title']}",
        f"'{o['title']}' is mislukt ({reason_label}). Voorstel: '{cand['title']}' naar {label} sturen.",
        risk=ch["risk"],
        subject=media_ref,
        confidence=0.7,
        change_id=ch["id"],
        dedupe_key=f"recovery:fallback:{media_ref}",
        evidence={"failed": o["title"], "candidate": cand["title"], "client": client, "category": category},
    )
    return ch


# ------------------------------------------------------------------ job step
def _record(
    o: dict[str, Any],
    status: str,
    *,
    media_ref: str | None = None,
    mapping: str | None = None,
    reason: str | None = None,
    change_id: int | None = None,
    cand: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    next_at: datetime | None = None,
) -> None:
    get_db().execute(
        "INSERT INTO recovery_fallbacks(outcome_id,failed_release_key,failed_title,protocol,failure_reason,"
        "media_ref,mapping,status,reason,change_id,candidate_release_key,candidate_title,detail,next_attempt_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (outcome_id) DO UPDATE SET "
        "media_ref=EXCLUDED.media_ref, mapping=EXCLUDED.mapping, status=EXCLUDED.status, reason=EXCLUDED.reason, "
        "change_id=EXCLUDED.change_id, candidate_release_key=EXCLUDED.candidate_release_key, "
        "candidate_title=EXCLUDED.candidate_title, detail=EXCLUDED.detail, "
        "next_attempt_at=EXCLUDED.next_attempt_at, updated_at=now()",
        (
            o["id"],
            o.get("release_key"),
            o["title"],
            o.get("protocol"),
            o.get("failure_reason"),
            media_ref,
            mapping,
            status,
            reason,
            change_id,
            cand["release_key"] if cand else None,
            cand["title"] if cand else None,
            jsonb(detail or {}),
            next_at,
        ),
    )


def _blacklist_failed(o: dict[str, Any], media_ref: str | None) -> None:
    key = o.get("release_key")
    if not key:
        return
    if (o.get("failure_reason") or "") in blacklist.RELEASE_SPECIFIC_FAILURES:
        return  # already added by tasks.process_outcomes (counted once per outcome)
    if blacklist.is_blacklisted(key):
        return
    blacklist.add(
        key,
        f"download mislukt: {o.get('failure_reason') or 'onbekend'}",
        None,
        media_ref,
        o.get("protocol"),
        actor=ACTOR,
    )


def handle_outcome(o: dict[str, Any], *, connected: bool, allow_connected_send: bool) -> str:
    """Process one failed outcome. Returns the recorded status ('searched' marks an indexer search)."""
    reason_code = o.get("failure_reason") or ""
    if reason_code in NOT_RELEASE_CAUSES:
        _record(
            o,
            "skipped",
            reason=(
                f"Oorzaak: {FAILURE_REASONS.get(reason_code, reason_code)}. Een andere release lost dit niet op; los "
                "eerst de oorzaak bij de downloadclient/provider op."
            ),
        )
        return "skipped"
    media_ref, mapping, why = map_outcome(o, connected=connected)
    _blacklist_failed(o, media_ref)
    if media_ref is None:
        _record(o, "skipped", reason=why)
        return "skipped"
    if media_ref.startswith("mg:") and connected and not allow_connected_send:
        _record(
            o,
            "skipped",
            media_ref=media_ref,
            mapping=mapping,
            reason=(
                "MediaGuard is gekoppeld en eigenaar van dit item. Zet 'Handmatig versturen ook bij "
                "MediaGuard-koppeling' aan voor een voorstel, of gebruik 'Vraag MediaGuard opnieuw te zoeken'."
            ),
        )
        return "skipped"
    ok, until = blacklist.media_allowed(media_ref)
    if not ok:
        _record(
            o,
            "waiting",
            media_ref=media_ref,
            mapping=mapping,
            next_at=until,
            reason=f"Dit item faalde eerder; opnieuw na {until.isoformat(timespec='minutes')} (backoff)",
        )
        return "waiting"
    open_change = get_db().fetchone(
        "SELECT id FROM change_sets WHERE dedupe_key=%s AND status IN ('preview','approved')",
        (f"fallback:{media_ref}",),
    )
    if open_change:
        _record(
            o,
            "proposed",
            media_ref=media_ref,
            mapping=mapping,
            change_id=open_change["id"],
            reason="Er staat al een open voorstel voor dit item",
        )
        return "proposed"
    try:
        res = candidates.find_candidates(media_ref, actor=ACTOR)
    except candidates.SearchUnavailable:
        raise
    except HTTPException as exc:
        _record(o, "skipped", media_ref=media_ref, mapping=mapping, reason=str(exc.detail))
        return "searched"
    detail = {"counts": res["counts"], "trace_id": res["search"].get("trace_id")}
    if media_ref.startswith("mg:") and not res["requirements"].get("policy_verified"):
        _record(
            o,
            "skipped",
            media_ref=media_ref,
            mapping=mapping,
            detail=detail,
            reason=("MediaGuard-kwaliteitseisen zijn niet leesbaar; zonder die eisen stelt MSO geen vervanger voor."),
        )
        return "searched"
    cand = pick_candidate(res, o.get("release_key"), o["title"])
    if cand is None:
        n = res["counts"]
        _record(
            o,
            "no_candidate",
            media_ref=media_ref,
            mapping=mapping,
            detail=detail,
            reason=(
                f"Geen vervanger die aan alle eisen voldoet ({n['total']} resultaten, {n['rejected']} afgewezen). "
                "Zoek later opnieuw via Ontbrekend."
            ),
        )
        return "searched"
    try:
        ch = propose(o, media_ref, cand, res)
    except (HTTPException, ValueError) as exc:
        msg = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _record(
            o,
            "skipped",
            media_ref=media_ref,
            mapping=mapping,
            cand=cand,
            detail=detail,
            reason=f"Voorstel niet gemaakt: {msg}",
        )
        return "searched"
    _record(o, "proposed", media_ref=media_ref, mapping=mapping, change_id=ch["id"], cand=cand, detail=detail)
    audit(
        ACTOR,
        "recovery.fallback_proposed",
        f"Vervanger voorgesteld voor mislukte download: {o['title']}",
        target=media_ref,
        change_id=ch["id"],
        detail={"failed": o.get("release_key"), "candidate": cand["release_key"], "risk": ch["risk"]},
    )
    return "searched"


def process_failed() -> dict[str, Any]:
    if not sr.get("recovery.fallback_on_failure"):
        return {"skipped": "uit"}
    from mso.features.mediaguard import service as mg

    connected = mg.mode() == "connected"
    allow = bool(sr.get("herstel.allow_manual_send_connected"))
    budget = int(sr.get("recovery.fallback_searches_per_run"))
    rows = get_db().fetchall(
        "SELECT o.* FROM download_outcomes o LEFT JOIN recovery_fallbacks f ON f.outcome_id=o.id "
        "WHERE o.status='failed' AND o.protocol IN ('usenet','torrent') "
        "AND COALESCE(o.finished_at, o.observed_at) > now() - make_interval(hours => %s) "
        "AND (f.id IS NULL OR (f.status='waiting' AND f.next_attempt_at <= now())) "
        "ORDER BY o.id LIMIT 200",
        (MAX_AGE_H,),
    )
    counts: dict[str, int] = {}
    for o in rows:
        if budget <= 0:
            counts["deferred"] = counts.get("deferred", 0) + 1
            continue
        try:
            res = handle_outcome(dict(o), connected=connected, allow_connected_send=allow)
        except candidates.SearchUnavailable:
            counts["search_unavailable"] = 1
            break
        if res == "searched":
            budget -= 1
        counts[res] = counts.get(res, 0) + 1
    sync_statuses()
    return counts


def sync_statuses() -> None:
    """Mirror the change set lifecycle into the fallback rows."""
    get_db().execute(
        "UPDATE recovery_fallbacks f SET status = CASE c.status WHEN 'applied' THEN 'sent' "
        "WHEN 'failed' THEN 'failed' ELSE 'discarded' END, "
        "reason = CASE WHEN c.status='failed' THEN c.error ELSE f.reason END, updated_at=now() "
        "FROM change_sets c WHERE c.id=f.change_id AND f.status='proposed' "
        "AND c.status IN ('applied','failed','discarded','rolled_back')"
    )


def list_fallbacks(limit: int = 100) -> list[dict[str, Any]]:
    sync_statuses()
    rows = get_db().fetchall(
        "SELECT f.*, c.status AS change_status, c.risk AS change_risk FROM recovery_fallbacks f "
        "LEFT JOIN change_sets c ON c.id=f.change_id ORDER BY f.id DESC LIMIT %s",
        (limit,),
    )
    return [
        {**r, "failure_label": FAILURE_REASONS.get(r["failure_reason"] or "unknown", "Onbekende oorzaak")}
        for r in rows
    ]
