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

Speed limits (``pref:up_limit|dl_limit|alt_up_limit|alt_dl_limit``, ``torrent:<hash>:up_limit|dl_limit``) are
kept by qBittorrent in whole KiB/s: a written 200000000 reads back as 199999488. ``read`` therefore reports
the value that was just written (by this applier instance) when qBittorrent's value is that same limit rounded
to KiB (difference < 1024 bytes/s), so verify-after-write does not fail and auto-roll back a correct write.
MSO itself proposes limits as multiples of 1024 already (``settings.kib_floor``).
"""

from __future__ import annotations

import base64
from typing import Any

from mso import changes
from mso.changes import Applier
from mso.db import get_db, jsonb
from mso.features.torrent import cleanup, policy
from mso.vault import get_vault

ACTION_WINDOW_MIN = 10


def crossseed_secret(cid: int) -> str:
    return f"torrent.crossseed.{cid}"


def _action_done(h: str, action: str) -> bool:
    return bool(
        get_db().scalar(
            "SELECT count(*) FROM torrent_actions WHERE hash=%s AND action=%s AND ts > now() - "
            "make_interval(mins => %s)",
            (h, action, ACTION_WINDOW_MIN),
        )
    )


def _record_action(h: str, action: str) -> None:
    get_db().execute("INSERT INTO torrent_actions(hash, action) VALUES (%s,%s)", (h, action))


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
    written: dict[str, Any] = {}  # rate path → value this applier wrote (for KiB-tolerant read-back)

    def adapter() -> Any:
        if "a" not in state:
            from mso.features.torrent.adapters import get_adapter

            state["a"] = get_adapter("qbittorrent")
        return state["a"]

    cl = cleanup.CleanupOps(adapter)

    def read(path: str) -> Any:
        if cleanup.is_cleanup_path(path):
            return cl.read(path)
        kind, _, rest = path.partition(":")
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            if field_ in ("recheck", "reannounce"):
                return "uitgevoerd" if _action_done(h, field_) else "niet_uitgevoerd"
        if kind == "crossseed":
            row = get_db().fetchone(
                "SELECT candidate_hash FROM torrent_crossseed_candidates WHERE id=%s", (int(rest),)
            )
            if row is None:
                raise ValueError("cross-seed-kandidaat bestaat niet")
            return "toegevoegd_gepauzeerd" if adapter().has_torrent(row["candidate_hash"]) else "afwezig"
        value = adapter().read_value(path)
        if path == "pref:excluded_file_names" and value is not None:
            # qBittorrent keeps a list (one glob per line); compare the normalised text
            return policy.join_patterns(policy.split_patterns(value))
        if path in written and policy.rate_matches(written[path], value):
            return written[path]  # same limit, rounded by qBittorrent to whole KiB/s
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
                _record_action(h, field_)
                return
        if kind == "crossseed":
            if value != "toegevoegd_gepauzeerd":
                raise ValueError(
                    "MSO verwijdert nooit torrents; verwijder deze cross-seed zo nodig handmatig in "
                    "qBittorrent (zonder bestanden)"
                )
            cid = int(rest)
            row = get_db().fetchone("SELECT * FROM torrent_crossseed_candidates WHERE id=%s", (cid,))
            blob = get_vault().get(crossseed_secret(cid))
            if row is None or not blob:
                raise ValueError("torrentbestand van de kandidaat ontbreekt")
            adapter().add_torrent(
                data=base64.b64decode(blob),
                category=row["category"] or "",
                save_path=row["save_path"],
                paused=True,
                tags=["mso-cross-seed"],
            )
            _record_action(row["candidate_hash"], "crossseed_add")
            get_db().execute(
                "UPDATE torrent_crossseed_candidates SET status='added', updated_at=now() WHERE id=%s", (cid,)
            )
            return
        adapter().write_value(path, value)
        if policy.is_rate_path(path):
            written[path] = value
        if kind == "pref":
            _refresh_snapshot_pref(rest, adapter().read_value(path))

    def validate(path: str, value: Any) -> None:
        if cleanup.is_cleanup_path(path):
            cl.validate(path, value)
        else:
            policy.validate(path, value)  # refuses every other delete/remove path

    def same(path: str, a: Any, b: Any) -> bool:
        # qBittorrent keeps speed limits in whole KiB/s: the same limit, rounded (rollback drift check / read-back)
        return policy.is_rate_path(path) and policy.rate_matches(a, b)

    def noop_rollback(path: str) -> bool:
        # a recheck/reannounce changes no data: its rollback is a harmless no-op (see ``write``)
        return path.startswith("torrent:") and path.endswith((":recheck", ":reannounce"))

    return Applier(read=read, write=write, validate=validate, same=same, noop_rollback=noop_rollback)


changes.register_applier("qbittorrent", build)
