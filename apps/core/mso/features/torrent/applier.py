"""Change-set applier ``qbittorrent``.

Paths
- ``pref:<key>``                    qBittorrent preference (only keys in ``policy.ALLOWED_PREFS``);
                                    ``pref:excluded_file_names`` reads back normalised (trimmed lines, no blanks)
- ``transfer:alt_speed``            alternative speed limits on/off
- ``torrent:<hash>:up_limit``       per-torrent upload limit (bytes/s, 0 = none)
- ``torrent:<hash>:dl_limit``       per-torrent download limit
- ``torrent:<hash>:share_limits``   {ratio_limit, seeding_time_limit, inactive_seeding_time_limit}
- ``torrent:<hash>:recheck``        one-shot action: 'niet_uitgevoerd' → 'uitgevoerd' (rollback = no-op: a
hash
                                    check only reads data)
- ``torrent:<hash>:reannounce``     one-shot action, same semantics
- ``crossseed:<candidate_id>``      'afwezig' → 'toegevoegd_gepauzeerd' (paused, hash-checked, same save
path).
                                    Rollback is refused: MSO never removes torrents.
- ``cleanup:<token>``, ``arrblock:…``, ``blacklist:…``, ``torrent:<hash>:delete_with_files:<token>``
                                    malware cleanup (``cleanup.py``): the ONLY delete path; validated against
                                    a fresh scan and the stored preview, irreversible.

Validation (``policy.validate``) does not need a reachable client, so guardrails also hold at preview time.

qBittorrent bewaart snelheidslimieten in hele KiB/s. ``read`` levert de gemeten waarde voor het journaal en
de eindhash. ``same`` houdt bij de verificatie rekening met de afronding. MSO stelt zelf veelvouden van
1024 voor (``settings.kib_floor``).
"""

from __future__ import annotations

import base64
from typing import Any

from mso import changes
from mso.change_preview import digest
from mso.changes import Applier
from mso.connectors import base as cb
from mso.db import get_db, jsonb
from mso.features.torrent import cleanup, policy
from mso.vault import get_vault

ACTION_WINDOW_MIN = 10


def crossseed_secret(cid: int) -> str:
    return f"torrent.crossseed.{cid}"


def crossseed_payload_hash(row: dict[str, Any], blob: str | None) -> str:
    return digest([row["candidate_hash"], row["category"], row["save_path"], blob])


def _action_done(path: str) -> bool:
    if cb.recorded_target(path) == "ontvangen":
        return True
    instance = cb.recorded_target("qbittorrent")
    if instance is None:
        return False
    return bool(get_db().scalar(
        "SELECT EXISTS (SELECT 1 FROM change_sets WHERE target='qbittorrent' AND "
        "apply_started_at > now() - make_interval(mins => %s) AND ops->0->'source_bindings' @> %s)",
        (ACTION_WINDOW_MIN, jsonb({"qbittorrent": instance, path: "ontvangen"})),
    ))


def _record_action(h: str, action: str) -> None:
    get_db().execute("INSERT INTO torrent_actions(hash,action,client_instance) VALUES (%s,%s,%s)",
                     (h, action, cb.recorded_target("qbittorrent")))


def _refresh_snapshot_pref(key: str, value: Any) -> None:
    """Keep the stored client snapshot in step with a write (apply AND rollback), so the read-only blocks
    ("Veilige snelheidslimieten", "Instellingen relevant voor tuning") show the new value at once instead of after
    the next ingest. Only keys MSO manages (never a secret preference)."""
    if key not in policy.ALLOWED_PREFS:
        return
    get_db().execute(
        "UPDATE torrent_client_snapshot SET preferences = COALESCE(preferences, '{}'::jsonb) || %s WHERE id=1",
        (jsonb({key: value}),),
    )


def build() -> Applier:
    state: dict[str, Any] = {}
    payload_hashes: dict[str, str] = {}
    commands: dict[str, dict[str, Any]] = {}

    def adapter() -> Any:
        if "a" not in state:
            from mso.features.torrent.adapters import get_adapter

            state["a"] = get_adapter("qbittorrent")
        return state["a"]

    cl = cleanup.CleanupOps(adapter)

    def crossseed_candidate(path: str, cid: int) -> tuple[dict[str, Any], str]:
        row = get_db().fetchone("SELECT * FROM torrent_crossseed_candidates WHERE id=%s", (cid,))
        blob = get_vault().get(crossseed_secret(cid))
        if row is None or not blob:
            raise ValueError("Torrentbestand van de kandidaat ontbreekt")
        command = commands.get(path)
        if adapter().crossseed_command(row) != command or crossseed_payload_hash(row, blob) != payload_hashes.get(path):
            raise changes.SourceWriteRefused("De cross-seedopdracht wijkt af van de goedkeuring")
        cb.bind_write_target(path, command["download_id"])
        return command, blob

    def read(path: str) -> Any:
        if cleanup.is_cleanup_path(path):
            return cl.read(path)
        kind, _, rest = path.partition(":")
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            if field_ in ("recheck", "reannounce"):
                if not adapter().has_torrent(h):
                    raise ValueError("De torrent voor de aangevraagde actie ontbreekt op de bron")
                return "uitgevoerd" if _action_done(path) else "niet_uitgevoerd"
        if kind == "crossseed":
            command, _ = crossseed_candidate(path, int(rest))
            status = adapter().torrent_state(command["download_id"], category=command["category"],
                                            save_path=command["save_path"])
            if status is None:
                return "afwezig"
            return "toegevoegd_gepauzeerd" if status in ("pausedDL", "pausedUP", "stoppedDL", "stoppedUP") else "actief"
        value = adapter().read_value(path)
        if kind == "pref" and state.get("refresh_pref") == rest:
            _refresh_snapshot_pref(rest, value)
            del state["refresh_pref"]
        if path == "pref:excluded_file_names" and value is not None:
            # qBittorrent keeps a list (one glob per line); compare the normalised text
            return policy.join_patterns(policy.split_patterns(value))
        return value

    def write(path: str, value: Any) -> None:
        # no validation here: changes.apply validates every 'after' value; rollback must be able to restore
        # the observed previous value even if it would not pass today's guardrails
        if cleanup.is_cleanup_path(path):
            cl.write(path, value)  # refuses every "restore" of a deleted torrent / removed block
            return
        kind, _, rest = path.partition(":")
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            if field_ in ("recheck", "reannounce"):
                if value == "niet_uitgevoerd":
                    return  # nothing to undo: a recheck/reannounce does not change data
                getattr(adapter(), field_)(h)
                cb.bind_write_target(path, "ontvangen")
                _record_action(h, field_)
                return
        if kind == "crossseed":
            if value != "toegevoegd_gepauzeerd":
                raise ValueError(
                    "MSO verwijdert nooit torrents; verwijder deze cross-seed zo nodig handmatig in "
                    "qBittorrent (zonder bestanden)"
                )
            cid = int(rest)
            command, blob = crossseed_candidate(path, cid)
            adapter().add_torrent(
                data=base64.b64decode(blob),
                category=command["category"],
                save_path=command["save_path"],
                paused=True,
                pause_field=command["pause_field"],
                tags=["mso-cross-seed"],
            )
            _record_action(command["download_id"], "crossseed_add")
            get_db().execute(
                "UPDATE torrent_crossseed_candidates SET status='added', updated_at=now() WHERE id=%s", (cid,)
            )
            return
        adapter().write_value(path, value)
        if kind == "pref":
            # De eerstvolgende bronlezing werkt de momentopname bij. Het schrijfantwoord is al ontvangen.
            state["refresh_pref"] = rest

    def validate(path: str, value: Any) -> None:
        if cleanup.is_cleanup_path(path):
            cl.validate(path, value)
        else:
            policy.validate(path, value)  # refuses every other delete/remove path

    def same(path: str, current: Any, expected: Any) -> bool:
        # qBittorrent keeps speed limits in whole KiB/s: the same limit, rounded (rollback drift check / read-back)
        return policy.is_rate_path(path) and policy.rate_matches(expected, current)

    def noop_rollback(path: str) -> bool:
        # a recheck/reannounce changes no data: its rollback is a harmless no-op (see ``write``)
        return path.startswith("torrent:") and path.endswith((":recheck", ":reannounce"))

    return Applier(read=read, write=write, validate=validate, same=same,
                   noop_rollback=noop_rollback, payload_hashes=payload_hashes, commands=commands)


changes.register_applier("qbittorrent", build)
