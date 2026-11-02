"""Recovery actions.

- Connected mode: the action for a MediaGuard item is "Vraag MediaGuard opnieuw te zoeken" (official route,
  see ``mso.features.mediaguard.service.request_search``).
- Standalone (or explicitly allowed): the user may send one evaluated candidate to SABnzbd/qBittorrent. This
  is a direct, user-initiated action: audited, rate limited, idempotent per item+release, and it re-runs the
  full quality gate on the server — a candidate that fails MediaGuard's requirements is always refused. MSO
  never changes a media library; by default the download lands in a separate ``mso-<category>`` category.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from mso import settings_registry as sr
from mso.audit import audit
from mso.connectors import base as cb
from mso.db import get_db
from mso.features.recovery import blacklist, candidates
from mso.security import redact


def client_category(media_category: str) -> str:
    return media_category if sr.get("herstel.send_category_mode") == "mediaguard" else f"mso-{media_category}"


def _dispatch(protocol: str, token: str, *, category: str, title: str) -> tuple[str, str | None]:
    """Fetch the NZB/.torrent through Slice A (``fetch_download(token)``: SSRF guard, size cap, indexer secret
    re-injected server-side) and hand it to the client via the Slice B/C contracts.
    Returns (client, client_ref)."""
    try:
        search = importlib.import_module("mso.features.search.service")
    except ModuleNotFoundError as exc:
        raise HTTPException(409, detail="Zoekmachine niet beschikbaar; kan de release niet ophalen") from exc
    if protocol == "usenet":
        try:
            mod = importlib.import_module("mso.connectors.sabnzbd")
        except ModuleNotFoundError as exc:
            raise HTTPException(409, detail="SABnzbd-koppeling niet beschikbaar in deze installatie") from exc
        sab = mod.SabClient.from_config()
        fetched = search.fetch_download(token)
        name = getattr(fetched, "filename", None) or f"{title}.nzb"
        return "sabnzbd", sab.add_nzb(fetched.content, name, category=category)
    try:
        mod = importlib.import_module("mso.connectors.qbittorrent")
    except ModuleNotFoundError as exc:
        raise HTTPException(409, detail="qBittorrent-koppeling niet beschikbaar in deze installatie") from exc
    qb = mod.QbitClient.from_config()
    fetched = search.fetch_download(token)
    if getattr(fetched, "final_url", None):
        qb.add_torrent(url=fetched.final_url, category=category, paused=False, tags=["mso-herstel"])
    else:
        qb.add_torrent(data=fetched.content, category=category, paused=False, tags=["mso-herstel"])
    return "qbittorrent", None


def send(media_ref: str, release_key: str, *, actor: str) -> dict[str, Any]:
    t = candidates.resolve_target(media_ref)
    if t.origin == "mediaguard":
        from mso.features.mediaguard import service as mg

        if mg.mode() == "connected" and not sr.get("herstel.allow_manual_send_connected"):
            raise HTTPException(
                409,
                detail="MediaGuard is gekoppeld en eigenaar van dit item: gebruik 'Vraag "
                "MediaGuard opnieuw te zoeken'. Handmatig versturen staat uit.",
            )
    db = get_db()
    cand = db.fetchone(
        "SELECT * FROM recovery_candidates WHERE media_ref=%s AND release_key=%s", (media_ref, release_key)
    )
    if cand is None:
        raise HTTPException(
            404, detail="Deze release is niet door MSO beoordeeld voor dit item; zoek eerst kandidaten"
        )
    if cand["expires_at"] < datetime.now(UTC):
        raise HTTPException(409, detail="Kandidaat is verlopen; zoek opnieuw")
    # full server-side re-evaluation with *current* requirements (never trust earlier verdicts)
    b = candidates.build_requirements(t)
    stored_req = cand["requirements"] or {}
    extra = {k: stored_req.get(k) for k in candidates.TIGHTEN_FIELDS if stored_req.get(k)}
    if extra:
        b = candidates.build_requirements(t, extra)  # earlier user tightening stays in force
    if t.origin == "mediaguard" and not b["policy_verified"]:
        raise HTTPException(409, detail="MediaGuard-kwaliteitseisen zijn niet leesbaar; versturen geweigerd")
    r = candidates.release_from_json(cand["release"])
    verdict = candidates.evaluate(r, b, blacklisted=blacklist.is_blacklisted(release_key))
    if not verdict["passed"]:
        audit(
            actor,
            "recovery.send_refused",
            f"Versturen geweigerd door kwaliteitsbeleid: {r.title}",
            target=media_ref,
            detail={"release_key": release_key, "reasons": verdict["reasons"]},
        )
        raise HTTPException(422, detail="Beleid weigert deze release: " + "; ".join(verdict["reasons"]))
    ok, until = blacklist.media_allowed(media_ref)
    if not ok:
        raise HTTPException(
            429,
            detail=f"Dit item faalde eerder; wacht tot {until.isoformat(timespec='minutes')} "
            "(exponential backoff)",
        )
    per_hour = int(sr.get("herstel.manual_sends_per_hour"))
    used = int(
        db.scalar(
            "SELECT count(*) FROM recovery_sends WHERE status IN ('pending','sent') AND "
            "created_at > now() - interval '1 hour'"
        )
        or 0
    )
    if used >= per_hour:
        raise HTTPException(429, detail=f"Limiet van {per_hour} handmatige verzendingen per uur bereikt")
    token = r.extra.get("token")
    if not token:
        raise HTTPException(409, detail="Deze release heeft geen (geldige) downloadverwijzing; zoek opnieuw")
    key = f"send:{media_ref}:{release_key}"
    category = client_category(t.media_category)
    row = db.fetchone(
        "INSERT INTO recovery_sends(idempotency_key,media_ref,release_key,protocol,client,"
        "client_category,title,"
        "status,actor) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s) ON CONFLICT (idempotency_key) DO NOTHING "
        "RETURNING *",
        (
            key,
            media_ref,
            release_key,
            r.protocol,
            "sabnzbd" if r.protocol == "usenet" else "qbittorrent",
            category,
            r.title,
            actor,
        ),
    )
    if row is None:
        existing = db.fetchone("SELECT * FROM recovery_sends WHERE idempotency_key=%s", (key,))
        return {
            "duplicate": True,
            "send": existing,
            "message": "Deze release is al naar de downloadclient gestuurd voor dit item; niet opnieuw.",
        }
    try:
        client, ref = _dispatch(r.protocol, token, category=category, title=r.title)
    except LookupError as exc:  # Slice A's result cache expired
        db.execute(
            "UPDATE recovery_sends SET status='failed', error=%s, idempotency_key=%s, updated_at=now() "
            "WHERE id=%s",
            (str(exc), f"{key}:failed:{row['id']}", row["id"]),
        )
        raise HTTPException(409, detail=f"{exc}") from exc
    except HTTPException as exc:
        db.execute(
            "UPDATE recovery_sends SET status='failed', error=%s, idempotency_key=%s, updated_at=now() "
            "WHERE id=%s",
            (str(exc.detail), f"{key}:failed:{row['id']}", row["id"]),
        )
        raise
    except cb.ConnectorNotConfigured as exc:
        db.execute(
            "UPDATE recovery_sends SET status='failed', error=%s, idempotency_key=%s, updated_at=now() "
            "WHERE id=%s",
            (str(exc), f"{key}:failed:{row['id']}", row["id"]),
        )
        raise HTTPException(409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        msg = redact(f"{type(exc).__name__}: {exc}")
        db.execute(
            "UPDATE recovery_sends SET status='failed', error=%s, idempotency_key=%s, updated_at=now() "
            "WHERE id=%s",
            (msg, f"{key}:failed:{row['id']}", row["id"]),
        )
        raise HTTPException(502, detail=f"Downloadclient weigerde: {msg}") from exc
    if r.protocol == "torrent":
        ref = r.infohash_v1 or r.infohash_v2 or ref
    done = db.fetchone(
        "UPDATE recovery_sends SET status='sent', client=%s, client_ref=%s, updated_at=now() "
        "WHERE id=%s RETURNING *",
        (client, ref, row["id"]),
    )
    audit(
        actor,
        "recovery.sent",
        f"Handmatig verstuurd naar {client}: {r.title}",
        target=media_ref,
        detail={"release_key": release_key, "category": category, "protocol": r.protocol},
    )
    return {
        "duplicate": False,
        "send": done,
        "message": f"Verstuurd naar {client} in categorie '{category}'. MSO wijzigt de bibliotheek niet.",
    }


def sends(media_ref: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if media_ref:
        return get_db().fetchall(
            "SELECT * FROM recovery_sends WHERE media_ref=%s ORDER BY id DESC LIMIT %s", (media_ref, limit)
        )
    return get_db().fetchall("SELECT * FROM recovery_sends ORDER BY id DESC LIMIT %s", (limit,))
