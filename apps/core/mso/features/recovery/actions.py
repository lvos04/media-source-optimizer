"""Recovery actions.

- Connected mode: the action for a MediaGuard item is "Vraag MediaGuard opnieuw te zoeken" (official route,
  see ``mso.features.mediaguard.service.request_search``).
- Standalone (or explicitly allowed): the user may send one evaluated candidate to SABnzbd/qBittorrent. This
  is a direct, user-initiated action: audited, rate limited, idempotent per item+release, and it re-runs the
  full quality gate on the server — a candidate that fails MediaGuard's requirements is always refused. MSO
  never changes a media library; by default the download lands in a separate ``mso-<category>`` category.
- Release gate (every send: manual, approved change set, Autopilot): after fetching, the actual payload is checked
  before the client gets it (:func:`mso.domain.releasegate.check_download`: .torrent file list, magnet name, NZB
  file names). A fake (program, double extension like ``.mkv.exe``, password-protected archive) is refused with
  "Beleid weigert deze release: bevat uitvoerbaar bestand …", audited as ``recovery.send_refused`` and — when the
  file list itself proves it — put on the blacklist so MSO never picks it again.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from mso import download_receipts
from mso import settings_registry as sr
from mso.audit import audit
from mso.change_preview import digest
from mso.connectors import base as cb
from mso.connectors import torrent_form
from mso.db import get_db, jsonb
from mso.domain import releasegate
from mso.domain.release import NormalizedRelease
from mso.features.recovery import blacklist, candidates
from mso.features.usenet import nzbs
from mso.security import redact

FAKE_BLACKLIST_TTL_H = 24 * 365 * 10  # a proven fake never becomes good (same as the malware cleanup)


def client_category(media_category: str) -> str:
    return media_category if sr.get("herstel.send_category_mode") == "mediaguard" else f"mso-{media_category}"


def download_client(protocol: str) -> Any:
    if protocol == "usenet":
        return importlib.import_module("mso.connectors.sabnzbd").SabClient.from_config()
    return importlib.import_module("mso.connectors.qbittorrent").QbitClient.from_config()


def confirmed_download(row: dict[str, Any], command: dict[str, Any] | None) -> bool:
    """Lees het eerder ontvangen ID en de categorie op de geadresseerde client terug."""
    command = command or row.get("command")
    ref = row.get("client_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("Download-ID van de herstelverzending is onleesbaar")
    if row["client"] not in {"sabnzbd", "qbittorrent"}:
        raise ValueError("Downloadclient van de herstelverzending is onleesbaar")
    protocol = "usenet" if row["client"] == "sabnzbd" else "torrent"
    with closing(download_client(protocol)) as client:
        if command and client.instance_id != command["instance"]:
            raise ValueError("De eerdere download is op een andere instantie verzonden")
        owner = row.get("idempotency_key")
        category = command["category"] if command else row.get("client_category")
        if protocol == "usenet":
            expected = command.get("fields", {}).get("name") if command else None
            found = client.has_download(ref, category, owner=owner, expected_name=expected)
            return bool(owner and expected and found)
        if command and (ref != command["download_id"] or client.pause_field() != command["pause_field"]):
            raise ValueError("Het download-ID of pauzeveld wijkt af van de goedgekeurde torrentopdracht")
        form_hash = digest(command["fields"]) if command and command.get("fields") else None
        found = download_receipts.confirmed_torrent(client, ref.lower(), category, owner, form_hash)
        return bool(form_hash and found)


@contextmanager
def prepared_download(
    r: NormalizedRelease, category: str, receipt_id: Any,
) -> Iterator[tuple[dict[str, Any], Any, Any]]:
    """Leg ook geheime formulierwaarden vast met een hash. Alleen het opgehaalde exemplaar kan worden verzonden."""
    search = importlib.import_module("mso.features.search.service")
    fetched = search.fetch_download(r.extra["token"])
    payload = hashlib.sha256(fetched.content or b"").hexdigest()
    with closing(download_client(r.protocol)) as client:
        if r.protocol == "usenet":
            name = fetched.filename or f"{r.title}.nzb"
            filename = name if name.lower().endswith(".nzb") else f"{name}.nzb"
            upload_name = nzbs.upload_name(filename, payload)
            fields = {"cat": category, "nzbname": digest(filename[:-4]), "name": digest([upload_name, payload])}
            command = {"client": "sabnzbd", "mode": "addfile", "fields": fields}
        else:
            pause = client.pause_field()
            content_hash = hashlib.sha256(fetched.final_url.encode()).hexdigest() if fetched.final_url else payload
            tags = ["mso-herstel", download_receipts.torrent_tag(receipt_id, content_hash)]
            fields = torrent_form.add_fields(category, pause, paused=False, tags=tags)
            if fetched.final_url:
                fields["urls"] = digest(fetched.final_url)
            else:
                fields["torrents"] = digest([torrent_form.FILENAME, torrent_form.CONTENT_TYPE, payload])
            download_id = r.infohash_v1 or r.infohash_v2 or releasegate.torrent_download_id(fetched, None)
            command = {"client": "qbittorrent", "mode": "torrents/add", "fields": fields, "pause_field": pause,
                       "download_id": download_id.lower()}
        command.update(instance=client.instance_id, category=category, release_key=r.release_key)
        yield command, client, fetched


def preview_command(media_ref: str, release_key: str, category: str) -> dict[str, Any]:
    row = get_db().fetchone("SELECT release,receipt_id FROM recovery_candidates WHERE media_ref=%s AND release_key=%s",
                            (media_ref, release_key))
    if not row:
        raise ValueError("De beoordeelde kandidaat ontbreekt")
    try:
        release = candidates.release_from_json(row["release"])
        with prepared_download(release, category, row["receipt_id"]) as (command, _, _):
            return command
    except Exception as exc:
        raise ValueError("De verzendopdracht kon niet worden vastgelegd. Zoek de kandidaat opnieuw.") from exc


def _dispatch(r: NormalizedRelease, media_category: str, approved: dict[str, Any] | None,
              owner: str, receipt_id: Any) -> tuple[dict[str, Any], str]:
    category = client_category(media_category)
    with prepared_download(r, category, receipt_id) as (current, client, fetched):
        if approved is not None and current != approved:
            raise HTTPException(409, detail="De verzendopdracht wijkt af van de goedgekeurde voorvertoning")
        command = current if approved is None else approved
        checked = releasegate.check_download(r.protocol, fetched, title=r.title, media=True)
        if command["category"] not in client.categories() or client_category(media_category) != command["category"]:
            raise HTTPException(409, detail="De verzendcategorie ontbreekt op de ontvangende instantie")
        if r.protocol == "usenet":
            name = fetched.filename or f"{r.title}.nzb"
            ref = client.add_nzb(fetched.content, name, category=command["category"], exact=True, owner=owner,
                                 expected_field=command["fields"]["name"])
        else:
            try:
                ref = releasegate.torrent_download_id(fetched, command["download_id"], checked.keys)
            except ValueError as exc:
                raise HTTPException(409, detail=str(exc)) from exc
            fields = command["fields"]
            download_receipts.send_torrent(client, fetched, ref, checked.name, category, owner=owner,
                                           tags=fields["tags"].split(","), form_hash=digest(fields),
                                           paused=False, pause_field=command["pause_field"])
        return command, ref


def idempotency_key(media_ref: str, release_key: str) -> str:
    return f"send:{media_ref}:{release_key}"


def sends_used_last_hour() -> int:
    return int(
        get_db().scalar(
            "SELECT count(*) FROM recovery_sends WHERE status IN ('pending','sent') AND "
            "created_at > now() - interval '1 hour'"
        )
        or 0
    )


def send(media_ref: str, release_key: str, *, actor: str, via: str = "manual",
         approved: dict[str, Any] | None = None) -> dict[str, Any]:
    """``via='change'``: sent by applying an approved change set (``download_send`` applier) — same quality
    gate, quota, backoff and idempotency as a manual send."""
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
    used = sends_used_last_hour()
    if used >= per_hour:
        raise HTTPException(429, detail=f"Limiet van {per_hour} handmatige verzendingen per uur bereikt")
    token = r.extra.get("token")
    if not token:
        raise HTTPException(409, detail="Deze release heeft geen (geldige) downloadverwijzing; zoek opnieuw")
    key = idempotency_key(media_ref, release_key)
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
        command = approved or preview_command(media_ref, release_key, category)
        if (not existing or existing["status"] not in {"sent", "download_ok", "download_failed"}
                or existing["command"] != command):
            raise HTTPException(409, detail="De eerdere verzending heeft geen overeenkomende ontvanger en opdracht")
        try:
            found = confirmed_download(existing, command)
        except Exception as exc:
            raise HTTPException(409, detail="Het eerdere doel kan op deze instantie niet worden bevestigd") from exc
        if not found:
            raise HTTPException(409, detail="De herkomst van de eerdere download is onbekend")
        return {
            "duplicate": True,
            "send": existing,
            "message": "Deze release is al naar de downloadclient gestuurd voor dit item; niet opnieuw.",
        }
    try:
        command, ref = _dispatch(r, t.media_category, approved, key, cand["receipt_id"])
        client = command["client"]
        category = command["category"]
    except releasegate.Refused as exc:
        db.execute(
            "UPDATE recovery_sends SET status='failed', error=%s, idempotency_key=%s, updated_at=now() "
            "WHERE id=%s",
            (exc.message[:500], f"{key}:failed:{row['id']}", row["id"]),
        )
        _refuse_fake(exc, r, media_ref=media_ref, release_key=release_key, actor=actor, via=via)
        raise HTTPException(422, detail=exc.message) from exc
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
    except Exception as exc:
        msg = redact(f"{type(exc).__name__}: {exc}")
        failed_key = key if getattr(exc, "code", "") in {"ambiguous_id", "timeout"} else f"{key}:failed:{row['id']}"
        db.execute(
            "UPDATE recovery_sends SET status='failed', error=%s, idempotency_key=%s, updated_at=now() "
            "WHERE id=%s",
            (msg, failed_key, row["id"]),
        )
        raise HTTPException(502, detail=f"Downloadclient weigerde: {msg}") from exc
    done = db.fetchone(
        "UPDATE recovery_sends SET status='sent', client=%s, client_ref=%s, client_category=%s, command=%s, "
        "updated_at=now() "
        "WHERE id=%s RETURNING *",
        (client, ref, category, jsonb(command), row["id"]),
    )
    audit(
        actor,
        "recovery.sent",
        (f"Verstuurd via wijzigingsvoorstel naar {client}: {r.title}" if via == "change"
         else f"Handmatig verstuurd naar {client}: {r.title}"),
        target=media_ref,
        detail={"release_key": release_key, "category": category, "protocol": r.protocol, "via": via},
    )
    return {
        "duplicate": False,
        "send": done,
        "message": f"Verstuurd naar {client} in categorie '{category}'. MSO wijzigt de bibliotheek niet.",
    }


def _refuse_fake(
    exc: releasegate.Refused, r: Any, *, media_ref: str, release_key: str, actor: str, via: str
) -> None:
    """Audit a release-gate refusal; a fake proven by the fetched file list goes on the blacklist (all its
    identities: the indexer's release key and the infohash read from the .torrent) and stops being a stored
    candidate."""
    keys = list(dict.fromkeys([release_key, *exc.keys])) if exc.content else []
    for k in keys:
        try:
            blacklist.add(
                k,
                f"nep-release: {exc.reasons[0]}"[:300],
                FAKE_BLACKLIST_TTL_H,
                media_ref=media_ref,
                protocol=r.protocol,
                actor=actor,
            )
        except ValueError:  # pragma: no cover - only for an empty key
            continue
    candidates.mark_fake(keys, exc.reasons)  # no longer offered as "Voldoet" nor recommended
    audit(
        actor,
        "recovery.send_refused",
        (f"Versturen geweigerd, nep-release: {r.title}" if exc.content or exc.evidence.get("matches")
         else f"Versturen geweigerd, bestanden niet te controleren: {r.title}"),
        target=media_ref,
        detail={
            "release_key": release_key,
            "reasons": exc.reasons,
            "checked": exc.evidence,
            "blacklisted": keys,
            "via": via,
        },
    )


def sends(media_ref: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if media_ref:
        return get_db().fetchall(
            "SELECT * FROM recovery_sends WHERE media_ref=%s ORDER BY id DESC LIMIT %s", (media_ref, limit)
        )
    return get_db().fetchall("SELECT * FROM recovery_sends ORDER BY id DESC LIMIT %s", (limit,))
