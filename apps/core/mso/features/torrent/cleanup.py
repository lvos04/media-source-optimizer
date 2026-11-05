"""Malware cleanup: the ONLY path in MSO that deletes torrents (together with their files).

A media download that contains a program/script/shortcut (``diagnostics.payload_scan``, the existing
``suspicious_payload`` finding) is a fake release. MSO proposes to remove it with ONE approval:

``cleanup:<token>``                         guard: the previewed list is still exactly what a fresh scan finds
``arrblock:<token>:<hash>``                 (optional) Sonarr/Radarr blocklist via MediaGuard's queue route
``blacklist:<token>:<release_key>``         MSO's own release blacklist (``recovery.blacklist``, ~permanent)
``torrent:<hash>:delete_with_files:<token>`` ``POST torrents/delete hashes=<h>&deleteFiles=true``

Ops are ordered guard → blocks → deletes, so a failing block never leaves a half-deleted bundle. Every block
and delete op is ``irreversible`` (deletes also ``destructive``); the change is always ``toestemming_nodig``
and ``mso.changes`` refuses to apply it automatically. Rolling back is refused ("niet terug te draaien").

Safety (``validate``, run at preview AND again at apply, before any write):
- the token must belong to a stored preview (``torrent_cleanup_previews``) and match its scope + hashes;
- a fresh scan (live ``torrents/info`` + ``torrents/files`` of the previewed torrents) must still flag every
  previewed torrent as suspicious and present; for the "alle verdachte torrents" bundle the set of suspicious
  torrents must be exactly the previewed set (one gone, a new one, or no longer suspicious → the whole bundle is
  refused: "lijst gewijzigd, maak een nieuw voorstel");
- a delete/block op is only valid next to its guard op in the same change and only for a hash in the preview.
So a non-suspicious torrent can never be deleted, whatever ops are injected.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from mso import changes
from mso.audit import audit
from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.features.torrent import diagnostics
from mso.features.torrent import settings as ts
from mso.features.torrent.policy import PolicyError
from mso.security import redact

LOGGER = logging.getLogger(__name__)
_TOKID = r"[0-9a-f]{16}"
_HASH = r"[0-9a-f]{40}|[0-9a-f]{64}"
GUARD_RE = re.compile(rf"^cleanup:({_TOKID})$")
DELETE_RE = re.compile(rf"^torrent:({_HASH}):delete_with_files:({_TOKID})$")
BLACKLIST_RE = re.compile(rf"^blacklist:({_TOKID}):((?:btih|btmh):[0-9a-f]{{40,64}})$")
ARRBLOCK_RE = re.compile(rf"^arrblock:({_TOKID}):({_HASH})$")
BLACKLIST_TTL_H = 24 * 365 * 10  # effectively permanent: a fake release never becomes good
IRREVERSIBLE_LINE = "Niet terug te draaien: torrent en bestanden worden verwijderd"
DRIFT_MSG = "lijst gewijzigd, maak een nieuw voorstel"
DELETE_WAIT_S = 5.0
SOURCE = "torrent-cleanup"
MG_QUEUE_PAGES = 10


def is_cleanup_path(path: str) -> bool:
    return bool(GUARD_RE.match(path) or DELETE_RE.match(path) or BLACKLIST_RE.match(path) or ARRBLOCK_RE.match(path))


def token_for(scope: str, hashes: list[str]) -> str:
    return hashlib.sha256(f"{scope}|{'|'.join(sorted(hashes))}".encode()).hexdigest()[:16]


def release_key_for(h: str, info: dict[str, Any] | None = None) -> str:
    """Same identity as ``ingest`` uses (``btih:<v1>`` or ``btmh:<v2>``)."""
    info = info or {}
    v1 = info.get("infohash_v1") or (h if len(h) == 40 else None)
    v2 = info.get("infohash_v2") or None
    return f"btih:{str(v1).lower()}" if v1 else f"btmh:{str(v2 or h).lower()}"


def guard_value(n: int) -> str:
    return f"ongewijzigd ({n} verdachte torrent{'s' if n != 1 else ''})"


# ------------------------------------------------------------------ previews (stored token → hashes)
def store_preview(scope: str, hashes: list[str], actor: str = "system") -> str:
    hashes = sorted({h.lower() for h in hashes})
    token = token_for(scope, hashes)
    get_db().execute(
        "INSERT INTO torrent_cleanup_previews(token, scope, hashes, created_by) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (token) DO UPDATE SET updated_at=now()",
        (token, scope, jsonb(hashes), actor),
    )
    return token


def load_preview(token: str) -> dict[str, Any] | None:
    row = get_db().fetchone("SELECT * FROM torrent_cleanup_previews WHERE token=%s", (token,))
    if row is None:
        return None
    hashes = sorted(str(h).lower() for h in row["hashes"] or [])
    if token_for(row["scope"], hashes) != token:  # tampered row
        return None
    return {"token": token, "scope": row["scope"], "hashes": hashes}


# ------------------------------------------------------------------ fresh scan
def fresh_scan(adapter: Any, focus: set[str]) -> dict[str, Any]:
    """Live scan: name check of every torrent, file list of the ``focus`` torrents (the preview), of torrents the
    last ingest flagged and of torrents ingest has not seen yet. Returns ``{live, suspicious}``."""
    live = {str(t.get("hash") or "").lower(): t for t in adapter.live_torrents() if t.get("hash")}
    rows = get_db().fetchall(
        "SELECT hash, EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(diagnosis->'findings', '[]'::jsonb)) f "
        "WHERE f->>'code' = 'suspicious_payload') AS sus FROM torrent_state WHERE NOT removed"
    )
    known = {r["hash"] for r in rows}
    flagged = {r["hash"] for r in rows if r["sus"]}
    suspicious: dict[str, dict[str, Any]] = {}
    for h, t in live.items():
        name = str(t.get("name") or "")
        if not diagnostics.looks_like_media(name, ts.media_category(t.get("category"))):
            continue
        files: list[dict[str, Any]] = []
        if h in focus or h in flagged or h not in known:
            try:
                files = adapter.files(h)
            except Exception as exc:
                if h in focus:
                    raise PolicyError(
                        f"bestandenlijst van '{name[:80]}' is niet te controleren: {redact(str(exc))[:120]}"
                    ) from exc
        scan = diagnostics.payload_scan(name, files, media=True)
        if scan["matches"]:
            suspicious[h] = {"name": name, "matches": scan["matches"], "info": t}
    return {"live": live, "suspicious": suspicious}


# ------------------------------------------------------------------ MediaGuard (Sonarr/Radarr blocklist)
MG_BLOCK_ROUTE = "/api/v1/instances/{instance_id}/queue/{queue_id}"


def mediaguard_block_status() -> tuple[bool, str]:
    """Whether MSO can block a release in Sonarr/Radarr through MediaGuard. MediaGuard (current main) has
    ``DELETE /api/v1/instances/{id}/queue/{queue_id}?remove_from_client=false&blocklist=true`` (operator), but
    MSO's MediaGuard client only allows the writes in ``ALLOWED_WRITES``; until that route is whitelisted there,
    MSO uses only its own blacklist and says so in the preview."""
    if not cb.is_configured("mediaguard"):
        return False, "MediaGuard is niet gekoppeld"
    from mso.connectors import mediaguard as mg

    probe = MG_BLOCK_ROUTE.format(instance_id="sonarr", queue_id=1)
    if not any(m == "DELETE" and p.match(probe) for m, p in mg.ALLOWED_WRITES):
        return False, "de MediaGuard-koppeling van MSO staat de blokkeerroute (wachtrij + blocklist) nog niet toe"
    return True, "via MediaGuard (Sonarr/Radarr-blocklist)"


def _mg_client() -> Any:
    from mso.connectors.mediaguard import MediaGuardClient

    return MediaGuardClient.from_config()


def mediaguard_find(client: Any, h: str) -> dict[str, Any] | None:
    """The Sonarr/Radarr queue record (via MediaGuard) whose ``downloadId`` is this infohash."""
    instances = client.get("/api/v1/instances") or []
    for inst in instances:
        iid = str(inst.get("id") or "")
        if not iid or inst.get("archived"):
            continue
        for page in range(1, MG_QUEUE_PAGES + 1):
            data = client.get(f"/api/v1/instances/{iid}/queue", {"page": page, "page_size": 200}) or {}
            records = data.get("records") if isinstance(data, dict) else data
            for rec in records or []:
                if str(rec.get("downloadId") or "").lower() == h.lower():
                    return {"instance_id": iid, "instance": inst.get("name") or iid, "queue_id": int(rec["id"]),
                            "title": rec.get("title")}
            total = int(data.get("totalRecords") or 0) if isinstance(data, dict) else 0
            if not records or page * 200 >= total:
                break
    return None


def mediaguard_block(client: Any, rec: dict[str, Any]) -> None:
    path = MG_BLOCK_ROUTE.format(instance_id=rec["instance_id"], queue_id=int(rec["queue_id"]))
    r = client.request("DELETE", path, params={"remove_from_client": "false", "blocklist": "true"})
    client.raise_for(r)


# ------------------------------------------------------------------ applier hooks
class CleanupOps:
    """Per-applier-instance state + read/write/validate for cleanup paths (see module docstring)."""

    def __init__(self, adapter: Callable[[], Any]) -> None:
        self.adapter = adapter
        self.scan: dict[str, Any] | None = None
        self.scan_focus: set[str] = set()
        self.tokens: dict[str, list[str]] = {}
        self.blocked: set[str] = set()
        self._mg: Any = None

    def _scan(self, focus: set[str]) -> dict[str, Any]:
        if self.scan is None or not focus <= self.scan_focus:
            self.scan_focus |= focus
            self.scan = fresh_scan(self.adapter(), self.scan_focus)
        return self.scan

    def _guard_ok(self, pv: dict[str, Any]) -> bool:
        sc = self._scan(set(pv["hashes"]))
        sus = set(sc["suspicious"])
        if pv["scope"] == "all":
            return sus == set(pv["hashes"])
        return set(pv["hashes"]) <= sus

    def mg(self) -> Any:
        if self._mg is None:
            self._mg = _mg_client()
        return self._mg

    # ---- read
    def read(self, path: str) -> Any:
        if m := GUARD_RE.match(path):
            pv = load_preview(m[1])
            if pv is None:
                return "onbekend voorstel"
            return guard_value(len(pv["hashes"])) if self._guard_ok(pv) else "gewijzigd"
        if m := DELETE_RE.match(path):
            return "aanwezig" if self.adapter().has_torrent(m[1]) else "verwijderd"
        if m := BLACKLIST_RE.match(path):
            from mso.features.recovery import blacklist

            return "geblokkeerd" if blacklist.is_blacklisted(m[2]) else "niet_geblokkeerd"
        if m := ARRBLOCK_RE.match(path):
            h = m[2]
            if mediaguard_find(self.mg(), h) is not None:
                return "in_wachtrij"
            return "geblokkeerd" if h in self.blocked else "niet_in_wachtrij"
        raise ValueError(f"onbekend pad {path}")

    # ---- validate
    def validate(self, path: str, value: Any) -> None:
        if m := GUARD_RE.match(path):
            token = m[1]
            pv = load_preview(token)
            if pv is None:
                raise PolicyError("onbekend of ongeldig opruimvoorstel; maak een nieuw voorstel")
            if value != guard_value(len(pv["hashes"])):
                raise PolicyError("controlewaarde van het opruimvoorstel klopt niet")
            if not self._guard_ok(pv):
                raise PolicyError(DRIFT_MSG)
            self.tokens[token] = pv["hashes"]
            return
        if m := DELETE_RE.match(path):
            h, token = m[1], m[2]
            if value != "verwijderd":
                raise PolicyError("een verwijderde torrent kan niet worden teruggezet")
            self._check_member(token, h)
            sc = self._scan({h})
            if h not in sc["live"]:
                raise PolicyError(f"torrent is niet meer aanwezig in qBittorrent; {DRIFT_MSG}")
            if h not in sc["suspicious"]:
                raise PolicyError(
                    "een nieuwe controle vindt geen verdacht bestand meer in deze torrent; MSO verwijdert "
                    "alleen verdachte torrents"
                )
            return
        if m := BLACKLIST_RE.match(path):
            token, key = m[1], m[2]
            if value != "geblokkeerd":
                raise PolicyError("een blacklist-item verwijdert alleen een beheerder (Ontbrekend & herstel)")
            hashes = self._check_member(token, None)
            sc = self._scan(set(hashes))
            keys = {release_key_for(h, (sc["live"].get(h) or {})) for h in hashes}
            if key not in keys:
                raise PolicyError("release staat niet in het voorbeeld van dit opruimvoorstel")
            return
        if m := ARRBLOCK_RE.match(path):
            if value != "geblokkeerd":
                raise PolicyError("een blokkade in Sonarr/Radarr wordt niet door MSO opgeheven")
            self._check_member(m[1], m[2])
            ok, why = mediaguard_block_status()
            if not ok:
                raise PolicyError(f"blokkeren via MediaGuard niet mogelijk: {why}")
            return
        raise PolicyError(f"pad '{path}' is niet toegestaan")

    def _check_member(self, token: str, h: str | None) -> list[str]:
        hashes = self.tokens.get(token)
        if hashes is None:
            raise PolicyError(
                "verwijderen of blokkeren kan alleen binnen een geldig opruimvoorstel (controle-operatie ontbreekt)"
            )
        if h is not None and h not in hashes:
            raise PolicyError("torrent staat niet in het voorbeeld van dit opruimvoorstel")
        return hashes

    # ---- write
    def write(self, path: str, value: Any) -> None:
        if GUARD_RE.match(path):
            raise ValueError(DRIFT_MSG)
        if m := DELETE_RE.match(path):
            h = m[1]
            if value != "verwijderd":
                raise ValueError("een verwijderde torrent en zijn bestanden kunnen niet worden teruggezet")
            info = dict((self.scan or {}).get("live", {}).get(h) or {})
            sus = ((self.scan or {}).get("suspicious") or {}).get(h) or {}
            ad = self.adapter()
            ad.delete_with_files(h)
            deadline = time.monotonic() + DELETE_WAIT_S
            while ad.has_torrent(h) and time.monotonic() < deadline:
                time.sleep(0.25)
            _audit_removed(path, h, info, sus)
            return
        if m := BLACKLIST_RE.match(path):
            if value != "geblokkeerd":
                raise ValueError("blacklist-items worden niet door een terugdraaiactie verwijderd")
            from mso.features.recovery import blacklist

            blacklist.add(
                m[2],
                "Nep-release met malware (verdacht uitvoerbaar bestand); opgeruimd via MSO",
                BLACKLIST_TTL_H,
                protocol="torrent",
                actor="torrent-opruimen",
            )
            return
        if m := ARRBLOCK_RE.match(path):
            if value != "geblokkeerd":
                raise ValueError("een blokkade in Sonarr/Radarr wordt niet door MSO opgeheven")
            h = m[2]
            rec = mediaguard_find(self.mg(), h)
            if rec is None:
                raise ValueError("release staat niet (meer) in de wachtrij van Sonarr/Radarr")
            mediaguard_block(self.mg(), rec)
            self.blocked.add(h)
            return
        raise ValueError(f"onbekend pad {path}")


def _audit_removed(path: str, h: str, info: dict[str, Any], sus: dict[str, Any]) -> None:
    db = get_db()
    ch = db.fetchone(
        "SELECT id, approved_by FROM change_sets WHERE target='qbittorrent' AND status IN ('approved','preview') "
        "AND ops @> %s ORDER BY id DESC LIMIT 1",
        (jsonb([{"path": path}]),),
    )
    row = db.fetchone("SELECT name, tracker_host, category FROM torrent_state WHERE hash=%s", (h,)) or {}
    name = str(info.get("name") or row.get("name") or h)
    tracker = row.get("tracker_host")
    audit(
        (ch or {}).get("approved_by") or "systeem",
        "torrent.suspicious_removed",
        f"Verdachte torrent verwijderd (met bestanden): {name[:150]}",
        target="qbittorrent",
        change_id=(ch or {}).get("id"),
        detail={
            "hash": h,
            "name": name[:200],
            "tracker": tracker,
            "category": info.get("category") or row.get("category"),
            "files": [m.get("name") for m in (sus.get("matches") or [])][:10],
        },
    )
    db.execute("UPDATE torrent_state SET removed=true, updated_at=now() WHERE hash=%s", (h,))


# ------------------------------------------------------------------ proposals
def _size(info: dict[str, Any]) -> str:
    return diagnostics.human_bytes(float(info.get("total_size") or info.get("size") or 0))


def build_ops(
    scope: str,
    items: list[dict[str, Any]],
    *,
    token: str,
    mg_blocks: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """``items``: [{hash, name, info}] in preview order."""
    mg_blocks = mg_blocks or {}
    ops: list[dict[str, Any]] = [
        {
            "path": f"cleanup:{token}",
            "label": "Controle: lijst verdachte torrents ongewijzigd (nieuwe scan vlak voor verwijderen)",
            "before": guard_value(len(items)),
            "after": guard_value(len(items)),
        }
    ]
    for it in items:
        if it["hash"] in mg_blocks:
            rec = mg_blocks[it["hash"]]
            ops.append(
                {
                    "path": f"arrblock:{token}:{it['hash']}",
                    "label": f"Blokkeren in {rec.get('instance') or 'Sonarr/Radarr'} (blocklist, via MediaGuard): "
                    f"{it['name'][:120]}",
                    "before": "in_wachtrij",
                    "after": "geblokkeerd",
                    "irreversible": True,
                }
            )
    for it in items:
        ops.append(
            {
                "path": f"blacklist:{token}:{release_key_for(it['hash'], it.get('info'))}",
                "label": f"Op MSO-blacklist (permanent): {it['name'][:120]}",
                "before": "niet_geblokkeerd",
                "after": "geblokkeerd",
                "irreversible": True,
            }
        )
    for it in items:
        ops.append(
            {
                "path": f"torrent:{it['hash']}:delete_with_files:{token}",
                "label": f"Verwijderen mét bestanden: {it['name'][:120]}",
                "before": "aanwezig",
                "after": "verwijderd",
                "destructive": True,
                "irreversible": True,
            }
        )
    return ops


def unique_match_names(matches: list[dict[str, Any]]) -> list[str]:
    """Each suspicious file name once (a single-file torrent is flagged both by its name and by its file)."""
    out: list[str] = []
    for m in matches or []:
        n = str(m.get("name") or "")[-120:]
        if n and n not in out:
            out.append(n)
    return out


REGRAB_NOTE = (
    "Sonarr kan deze releases opnieuw grijpen; de preventie-instelling 'Uitvoerbare bestanden nooit downloaden' "
    "voorkomt dat de bestanden opnieuw binnenkomen"
)
MG_COMPLETED_MISSING = (
    "MediaGuard heeft (nog) geen route om een afgeronde release in Sonarr/Radarr als mislukt te markeren "
    "(blocklist); zie docs/dev/MEDIAGUARD_BLOCKLIST_REQUEST.md"
)


def _regrab_note() -> str:
    from mso.features.torrent.autopilot import prevention_note

    return REGRAB_NOTE + prevention_note() + "."


def impact_rows(items: list[dict[str, Any]], *, block_note: str) -> dict[str, Any]:
    return {
        "Soort": "malware opruimen (torrent + bestanden verwijderen)",
        "Gevolg": IRREVERSIBLE_LINE,
        "Aantal torrents": len(items),
        "Blokkeren": block_note,
        "Controle": "Vlak voor het verwijderen scant MSO opnieuw (naam + bestandenlijst). Is de lijst veranderd "
        f"(een torrent weg, een nieuwe erbij of niet meer verdacht), dan wordt niets verwijderd: '{DRIFT_MSG}'.",
        "Torrents": [
            {
                "Naam": it["name"][:200],
                "Verdacht bestand": ", ".join(unique_match_names(it["matches"])[:5])
                + (f" (+{len(unique_match_names(it['matches'])) - 5})"
                   if len(unique_match_names(it["matches"])) > 5 else ""),
                "Tracker": it.get("tracker") or "onbekend",
                "Categorie": it.get("category") or "geen",
                "Grootte": _size(it.get("info") or {}),
                "Gevolg": IRREVERSIBLE_LINE,
            }
            for it in items
        ],
    }


def _tracker_hosts(hashes: list[str]) -> dict[str, str | None]:
    if not hashes:
        return {}
    rows = get_db().fetchall("SELECT hash, tracker_host FROM torrent_state WHERE hash = ANY(%s)", (hashes,))
    return {r["hash"]: r["tracker_host"] for r in rows}


EXPLANATION = (
    "Deze torrents zijn media-downloads met een programma, script of snelkoppeling (bijv. .exe, .scr) in plaats "
    "van video: vrijwel zeker nep-releases met malware. Na jouw goedkeuring verwijdert MSO ze uit qBittorrent "
    "MÉT hun bestanden en zet de release op de MSO-blacklist (en op de Sonarr/Radarr-blocklist via MediaGuard "
    "als hij nog in een wachtrij staat; zie 'Blokkeren'). MSO houdt dit voorstel automatisch actueel, maar voert "
    "het nooit zelf uit. "
    + IRREVERSIBLE_LINE
    + ". Gezonde torrents en je mediabibliotheek worden nooit aangeraakt."
)


def preview(hashes: list[str] | None, actor: str) -> dict[str, Any]:
    """Create the cleanup change set (preview) from a FRESH scan. ``hashes`` None = all suspicious torrents."""
    from mso.features.torrent.adapters import get_adapter

    try:
        adapter = get_adapter("qbittorrent")
    except cb.ConnectorNotConfigured as exc:
        raise HTTPException(409, detail="qBittorrent is nog niet gekoppeld") from exc
    wanted = sorted({h.lower() for h in hashes}) if hashes else []
    try:
        sc = fresh_scan(adapter, set(wanted))
    except PolicyError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, detail=f"qBittorrent niet bereikbaar: {redact(str(exc))[:200]}") from exc
    sus = sc["suspicious"]
    if hashes:
        scope = "selection"
        not_sus = [h for h in wanted if h not in sus]
        if not_sus:
            raise HTTPException(
                422,
                detail="MSO verwijdert alleen torrents die bij een nieuwe controle verdacht zijn; niet verdacht of "
                f"niet aanwezig: {', '.join(h[:8] for h in not_sus)}",
            )
        selected = wanted
    else:
        scope = "all"
        selected = sorted(sus)
    if not selected:
        raise HTTPException(409, detail="Geen verdachte torrents gevonden; er valt niets op te ruimen")
    token = store_preview(scope, selected, actor)
    hosts = _tracker_hosts(selected)
    items = [
        {
            "hash": h,
            "name": sus[h]["name"],
            "matches": sus[h]["matches"],
            "info": sus[h]["info"],
            "tracker": hosts.get(h),
            "category": sus[h]["info"].get("category"),
        }
        for h in selected
    ]
    mg_ok, mg_why = mediaguard_block_status()
    mg_blocks: dict[str, dict[str, Any]] = {}
    if mg_ok:
        try:
            client = _mg_client()
            for it in items:
                rec = mediaguard_find(client, it["hash"])
                if rec is not None:
                    mg_blocks[it["hash"]] = rec
        except Exception as exc:  # MediaGuard down: MSO blacklist still applies
            mg_ok, mg_why = False, f"MediaGuard niet bereikbaar ({redact(str(exc))[:120]})"
    if mg_ok:
        rest = len(items) - len(mg_blocks)
        block_note = (
            f"MSO-blacklist (permanent) voor alle {len(items)}; Sonarr/Radarr-blocklist via MediaGuard voor "
            f"{len(mg_blocks)} die in de wachtrij staan"
            + ("." if not rest else
               f". De overige {rest} zijn al afgerond en staan niet meer in een wachtrij: {MG_COMPLETED_MISSING}. "
               + _regrab_note())
        )
    else:
        block_note = (
            f"MSO-blacklist (permanent): MSO zoekt/verstuurt deze releases nooit meer. Sonarr/Radarr-blocklist "
            f"niet mogelijk: {mg_why}. " + _regrab_note()
        )
    n = len(items)
    title = (
        f"Alle {n} verdachte torrents opruimen" if scope == "all" and n > 1
        else (f"Verdachte torrent opruimen: {items[0]['name'][:120]}" if n == 1
              else f"{n} verdachte torrents opruimen")
    )
    return changes.create(
        "qbittorrent",
        title,
        build_ops(scope, items, token=token, mg_blocks=mg_blocks),
        risk="toestemming_nodig",
        explanation=EXPLANATION,
        impact=impact_rows(items, block_note=block_note),
        source=SOURCE,
        actor=actor,
        dedupe_key=f"torrent-cleanup:{token}",
    )


def proposed_change_for(x: dict[str, Any], actor: str = "system") -> dict[str, Any]:
    """``evidence.proposed_change`` for the per-torrent recommendation (accepting it creates this preview).
    Built from the ingest scan (no extra calls); the applier re-checks everything live before deleting."""
    h = x["hash"]
    token = store_preview("selection", [h], actor)
    f = x["finding"]
    item = {
        "hash": h,
        "name": x["name"],
        "matches": f["evidence"].get("matches") or [],
        "info": x.get("info") or {},
        "tracker": x.get("tracker"),
        "category": x.get("category"),
    }
    return {
        "target": "qbittorrent",
        "title": f"Verdachte torrent opruimen: {x['name'][:120]}",
        "risk": "toestemming_nodig",
        "ops": build_ops("selection", [item], token=token),
        "explanation": EXPLANATION,
        "impact": impact_rows(
            [item],
            block_note="MSO-blacklist (permanent). Voor ook een Sonarr/Radarr-blocklist via MediaGuard (alleen "
            "voor releases die nog in de wachtrij staan): gebruik het opruimvoorstel op Torrentclient. "
            + REGRAB_NOTE + ".",
        ),
        "dedupe_key": f"torrent-cleanup:{token}",
    }
