"""Herkomst van door MSO verzonden items. Het adres alleen bewijst geen herkomst."""

import time
from typing import Any

from psycopg import Connection

from mso.change_preview import digest
from mso.connectors.qbittorrent import QbitUnsupported
from mso.db import get_db, jsonb


class ReceiptConflict(ValueError):
    # De bron heeft mogelijk al geschreven. Een retry mag geen tweede verzending starten.
    code = "ambiguous_id"

    def __init__(self):
        super().__init__("Het download-ID heeft tegenstrijdige ontvangstbewijzen; de herkomst is onbekend")


def register(conn: Connection, client: str, instance: str, ref: str, owner: str | None,
             proof: dict[str, Any] | None) -> bool:
    """Alleen bij een nieuw schrijfantwoord. Bewaar de betwisting ook als de aanroeper daarna weigert."""
    row = conn.execute(
        "INSERT INTO download_receipt_targets(client,instance,download_id,owner,proof) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (client,instance,download_id) DO UPDATE SET disputed=true RETURNING disputed",
        (client, instance, ref, owner, jsonb(proof) if proof else None),
    ).fetchone()
    return not row["disputed"]


def target(client: str, instance: str, ref: str) -> dict[str, Any] | None:
    row = get_db().fetchone("SELECT owner,proof,disputed FROM download_receipt_targets "
                            "WHERE client=%s AND instance=%s AND download_id=%s", (client, instance, ref))
    if row and row["disputed"]:
        raise ReceiptConflict()
    return row


def bind_owner(client: str, instance: str, ref: str, owner: str) -> None:
    row = get_db().fetchone(
        "UPDATE download_receipt_targets SET owner=%s WHERE client=%s AND instance=%s AND download_id=%s "
        "AND NOT disputed AND proof IS NOT NULL AND (owner IS NULL OR owner=%s) RETURNING download_id",
        (owner, client, instance, ref, owner),
    )
    if not row:
        raise ReceiptConflict()


def send_torrent(client: Any, fetched: Any, ref: str, name: str | None, category: str, *,
                 owner: str, tags: list[str], form_hash: str, **options: Any) -> None:
    if not name:
        raise ValueError("De exacte naam van de te verzenden torrent is onbekend")
    # De hash is vóór verzending bekend. Reserveer het doel vóór de POST, ook bij een latere time-out.
    reserved = get_db().fetchone(
        "INSERT INTO download_receipt_targets(client,instance,download_id,owner) VALUES ('qbittorrent',%s,%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING download_id", (client.instance_id, ref, owner),
    )
    if not reserved:
        raise ReceiptConflict()
    started = int(time.time())
    payload = {"url": fetched.final_url} if fetched.final_url else {"data": fetched.content}
    try:
        client.add_torrent(**payload, category=category, tags=tags, **options)
    except QbitUnsupported:
        # De client weigerde de veldset vóór de POST.
        get_db().execute("DELETE FROM download_receipt_targets WHERE client='qbittorrent' AND instance=%s "
                         "AND download_id=%s AND owner=%s AND proof IS NULL", (client.instance_id, ref, owner))
        raise
    except Exception as exc:
        # De aanvraag kan al zijn ontvangen. Houd de reservering bij een onbekende uitkomst.
        raise UnconfirmedDownload("De ontvangst van de torrent is onbekend") from exc
    finished = int(time.time())
    proof = {"name": name, "category": category, "form_hash": form_hash, "hash": ref, "sent_at": started, "tags": tags}
    item = _read_torrent(client, ref)
    added = item.get("added_on") if item else None
    if type(added) is int and started <= added <= finished:
        proof["added"] = added
    if not item or not _matches_torrent(item, proof, category):
        raise UnconfirmedDownload("De herkomst van de toegevoegde torrent is onbekend")
    if not get_db().execute("UPDATE download_receipt_targets SET proof=%s WHERE client='qbittorrent' AND instance=%s "
                            "AND download_id=%s AND owner=%s AND NOT disputed",
                            (jsonb(proof), client.instance_id, ref, owner)):
        raise ReceiptConflict()


class UnconfirmedDownload(ValueError):
    code = "ambiguous_id"


def _read_torrent(client: Any, ref: str) -> dict[str, Any] | None:
    item = client.torrent(ref)
    for _ in range(3):
        if item:
            break
        time.sleep(0.75)
        item = client.torrent(ref)
    return item


def confirmed_torrent(client: Any, ref: str, category: str | None, owner: str | None,
                      form_hash: str | None = None) -> bool:
    item = _read_torrent(client, ref)
    if not item or not owner or type(item.get("added_on")) is not int:
        return False
    row = target("qbittorrent", client.instance_id, ref)
    if not row or row["owner"] != owner or not (proof := row["proof"]):
        return False
    if form_hash and proof.get("form_hash") != form_hash:
        return False
    return _matches_torrent(item, proof, category)


def _matches_torrent(item: dict[str, Any], proof: dict[str, Any], category: str | None) -> bool:
    tags = item.get("tags")
    return (item["hash"].lower() == proof["hash"] and item.get("name") == proof["name"]
            and item.get("category") == category == proof["category"]
            and type(item.get("added_on")) is int and item["added_on"] == proof.get("added")
            and isinstance(tags, str) and set(proof["tags"]) <= {tag.strip() for tag in tags.split(",")})


def torrent_tag(receipt_id: Any, payload_digest: str) -> str:
    return "mso-" + digest([str(receipt_id), payload_digest])[:32]


def nzb_filename(name: str, payload_digest: str, receipt_id: str) -> str:
    """SAB bewaart de multipartnaam in de historie naast de afzonderlijke leesbare opdrachtnaam."""
    return "mso-" + digest([receipt_id, name, payload_digest]) + ".nzb"
