"""Torrent client adapter interface.

The engine (ingest, diagnostics, tuning, cross-seed, storage) talks to a ``TorrentClientAdapter``. The
canonical
vocabulary for torrent rows is the qBittorrent ``torrents/info`` field set (hash, name, state, progress,
dlspeed,
upspeed, eta, num_seeds, num_complete, availability, amount_left, ratio, seeding_time, category, save_path,
content_path, …) because it is the richest; other adapters must map onto it. Tracker rows are normalised to
``TrackerRow``.

Implemented: qBittorrent (WebAPI v2). Transmission (RPC) and Deluge (JSON-RPC via deluge-web) are documented
here as *not implemented*: they appear in ``/api/v1/torrent/clients`` with ``implemented: false`` and no code
path uses them. Adding one means implementing this protocol and registering it in ``ADAPTERS``.

The only removing primitive is ``delete_with_files`` and it is used exclusively by the cleanup applier for an
approved malware-cleanup change (``features/torrent/cleanup.py``); nothing else in the engine calls it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from mso.connectors import base as cb
from mso.connectors import torrent_form
from mso.features.torrent import policy
from mso.security import redact

TRACKER_STATUS = {0: "disabled", 1: "not_contacted", 2: "working", 3: "updating", 4: "not_working"}
# qBittorrent ≥ 5.1 (WebAPI 2.11.x) also reports 'tracker error' / 'unreachable' as distinct statuses 5/6
TRACKER_STATUS.update({5: "tracker_error", 6: "unreachable"})
PRIVATE_MSG = "this torrent is private"
PREFERENCE_TYPES = {
    "bool": (bool,), "int": (int,), "rate": (int,), "float": (int, float), "string": (str,),
    "patterns": (str, list, tuple),
}
SHARE_LIMIT_TYPES = {"ratio_limit": (int, float), "seeding_time_limit": (int,), "inactive_seeding_time_limit": (int,)}


class MissingPreference(ValueError):
    pass


def _measured_value(value: Any, types: tuple[type, ...], field: str) -> Any:
    error = f"qBittorrent-veld '{field}' is onleesbaar"
    if type(value) not in types or (type(value) is float and not math.isfinite(value)):
        raise ValueError(error)
    if type(value) in (int, float) and value < 0:
        # Deelgrenzen kennen ook -2 (globale instelling). Aantallen en snelheden kennen -1 (onbeperkt).
        if field in ("async_io_threads", "hashing_threads", "disk_queue_size") or not (
            value == -1 or (value == -2 and field in SHARE_LIMIT_TYPES)
        ):
            raise ValueError(error)
    if field == "max_ratio_act" and value not in range(4):
        raise ValueError(error)
    if isinstance(value, (list, tuple)) and any(not isinstance(pattern, str) for pattern in value):
        raise ValueError(error)
    return value


@dataclass
class TrackerRow:
    host: str | None  # announce host only — never the URL (passkeys!)
    kind: str  # tracker|dht|pex|lsd
    status: str  # working|not_working|updating|disabled|not_contacted|tracker_error|unreachable
    msg: str = ""
    seeds: int | None = None
    peers: int | None = None
    leeches: int | None = None
    tier: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "kind": self.kind,
            "status": self.status,
            "msg": self.msg,
            "seeds": self.seeds,
            "peers": self.peers,
            "leeches": self.leeches,
            "tier": self.tier,
        }


@dataclass
class ClientSnapshot:
    client: str
    version: str | None
    webapi: str | None
    auth_method: str | None
    capabilities: dict[str, Any]
    torrents: dict[str, dict[str, Any]]
    removed: list[str]
    server_state: dict[str, Any]
    preferences: dict[str, Any]
    categories: dict[str, dict[str, Any]]
    interfaces: list[dict[str, Any]] = field(default_factory=list)


class TorrentClientAdapter(Protocol):
    client_id: str

    def snapshot(self) -> ClientSnapshot: ...
    def trackers(self, h: str) -> list[TrackerRow]: ...
    def files(self, h: str) -> list[dict[str, Any]]: ...  # [{name, size, progress}]
    def is_private(self, h: str, info: dict[str, Any], trackers: list[TrackerRow] | None) -> bool | None: ...
    def read_value(self, path: str) -> Any: ...  # change-set applier paths (see applier.py)
    def write_value(self, path: str, value: Any) -> None: ...
    def add_torrent(
        self, *, data: bytes, category: str, save_path: str | None, paused: bool, pause_field: str, tags: list[str]
    ) -> None: ...
    def recheck(self, h: str) -> None: ...
    def reannounce(self, h: str) -> None: ...
    def has_torrent(self, h: str) -> bool: ...
    def crossseed_command(self, row: dict[str, Any]) -> dict[str, Any]: ...
    def torrent_state(self, h: str, *, category: str, save_path: str) -> str | None: ...
    def live_torrents(self) -> list[dict[str, Any]]: ...  # torrents/info rows (fresh, not the sync cache)
    def delete_with_files(self, h: str) -> None: ...  # cleanup applier only


SECRET_PREF_HINTS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "token",
    "ssl_key",
    "ssl_cert",
    "_cert",
    "web_ui_https_key",
    "passkey",
)


def sanitize_preferences(prefs: dict[str, Any]) -> dict[str, Any]:
    """Drop every preference that could hold a credential (proxy/mail/WebUI passwords, keys, certificates)."""
    return {k: v for k, v in prefs.items() if not any(s in k.lower() for s in SECRET_PREF_HINTS)}


def tracker_host(url: str | None) -> str | None:
    if not url or url.startswith("** ["):
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def normalise_tracker(row: dict[str, Any]) -> TrackerRow:
    url = str(row.get("url") or "")
    kind = "tracker"
    if url.startswith("** ["):
        kind = url.strip("*[] ").lower()  # dht|pex|lsd
    status = TRACKER_STATUS.get(int(row.get("status", 1) or 0), "not_contacted")

    def num(v: Any) -> int | None:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n if n >= 0 else None

    return TrackerRow(
        host=tracker_host(url),
        kind=kind,
        status=status,
        msg=redact(str(row.get("msg") or ""))[:300],
        seeds=num(row.get("num_seeds")),
        peers=num(row.get("num_peers")),
        leeches=num(row.get("num_leeches")),
        tier=num(row.get("tier")),
    )


class QbitAdapter:
    """qBittorrent WebAPI v2 adapter (uses the process-wide cached client: session + incremental sync)."""

    client_id = "qbittorrent"

    def __init__(self, client: Any | None = None) -> None:
        from mso.connectors.qbittorrent import QbitClient

        self.qb = client or QbitClient.cached()

    def snapshot(self) -> ClientSnapshot:
        caps = self.qb.ensure_detected()
        sync = self.qb.maindata()
        state = dict(sync.server_state)
        if "connection_status" not in state:
            state.update(self.qb.transfer_info())
        try:
            interfaces = self.qb.network_interfaces()
        except Exception:
            interfaces = []
        return ClientSnapshot(
            client=self.client_id,
            version=self.qb.version,
            webapi=self.qb.webapi,
            auth_method=self.qb.auth_method,
            capabilities=dict(caps),
            torrents={h: dict(t) for h, t in sync.torrents.items()},
            removed=list(sync.removed),
            server_state=state,
            preferences=sanitize_preferences(self.qb.preferences()),
            categories=dict(sync.categories),
            interfaces=[{"name": i.get("name"), "value": i.get("value")} for i in interfaces],
        )

    def trackers(self, h: str) -> list[TrackerRow]:
        return [normalise_tracker(r) for r in self.qb.trackers(h)]

    def files(self, h: str) -> list[dict[str, Any]]:
        return [
            {"name": f.get("name"), "size": int(f.get("size") or 0), "progress": f.get("progress")}
            for f in self.qb.files(h)
        ]

    def is_private(self, h: str, info: dict[str, Any], trackers: list[TrackerRow] | None) -> bool | None:
        if isinstance(info.get("private"), bool):
            return info["private"]
        if isinstance(info.get("isPrivate"), bool):
            return info["isPrivate"]
        if trackers:
            for t in trackers:
                if t.kind in ("dht", "pex", "lsd") and PRIVATE_MSG in t.msg.lower():
                    return True
        try:
            props = self.qb.properties(h)
        except Exception:
            props = {}
        if isinstance(props.get("is_private"), bool):
            return props["is_private"]
        if trackers and any(t.kind == "dht" for t in trackers):
            return False  # DHT row without the "private" message → public
        return None

    # ---- applier primitives (paths documented in applier.py)
    def _share_limits(self, values: dict[str, Any]) -> dict[str, Any]:
        limits = {}
        for key, types in SHARE_LIMIT_TYPES.items():
            if key == "inactive_seeding_time_limit" and key not in values and self.qb.has_legacy_share_limits():
                continue
            limits[key] = _measured_value(values.get(key), types, key)
        return limits

    def read_value(self, path: str) -> Any:
        kind, _, rest = path.partition(":")
        if kind == "pref":
            meta = policy.ALLOWED_PREFS[rest]
            value = self.qb.preferences().get(rest)
            if value is None:
                raise MissingPreference(f"qBittorrent-instelling '{rest}' is onleesbaar")
            return _measured_value(value, PREFERENCE_TYPES[meta["kind"]], rest)
        if kind == "transfer" and rest == "alt_speed":
            return bool(self.qb.speed_limits_mode())
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            t = self.qb.torrent(h)
            if t is None:
                raise ValueError("torrent niet (meer) aanwezig in qBittorrent")
            if field_ in ("up_limit", "dl_limit"):
                return _measured_value(t.get(field_), (int,), "snelheidslimiet")
            if field_ == "share_limits":
                return self._share_limits(t)
        raise ValueError(f"onbekend pad {path}")

    def write_value(self, path: str, value: Any) -> None:
        kind, _, rest = path.partition(":")
        if kind == "pref":
            types = PREFERENCE_TYPES[policy.ALLOWED_PREFS[rest]["kind"]]
            self.qb.set_preferences({rest: _measured_value(value, types, rest)})
            return
        if kind == "transfer" and rest == "alt_speed":
            if bool(self.qb.speed_limits_mode()) != _measured_value(value, (bool,), rest):
                self.qb.toggle_speed_limits_mode()
            return
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            if field_ == "up_limit":
                self.qb.set_upload_limit([h], _measured_value(value, (int,), "snelheidslimiet"))
                return
            if field_ == "dl_limit":
                self.qb.set_download_limit([h], _measured_value(value, (int,), "snelheidslimiet"))
                return
            if field_ == "share_limits":
                from mso.changes import SourceWriteRefused
                from mso.connectors.qbittorrent import QbitUnsupported

                if not isinstance(value, dict) or value.keys() - SHARE_LIMIT_TYPES.keys():
                    raise SourceWriteRefused("De goedgekeurde veldset voor deelgrenzen is onleesbaar")
                try:
                    self.qb.set_share_limits([h], **self._share_limits(value))
                except QbitUnsupported as exc:
                    raise SourceWriteRefused(str(exc)) from exc
                return
        raise ValueError(f"onbekend pad {path}")

    def add_torrent(
        self, *, data: bytes, category: str, save_path: str | None, paused: bool, pause_field: str, tags: list[str]
    ) -> None:
        self.qb.add_torrent(
            data=data, category=category, paused=paused, pause_field=pause_field, tags=tags,
            save_path=save_path, skip_checking=False
        )

    def recheck(self, h: str) -> None:
        self.qb.recheck([h])

    def reannounce(self, h: str) -> None:
        self.qb.reannounce([h])

    def has_torrent(self, h: str) -> bool:
        return self.qb.torrent(h) is not None

    def crossseed_command(self, row: dict[str, Any]) -> dict[str, Any]:
        source = self.qb.torrent(row["source_hash"])
        category, save_path = row["category"] or "", row["save_path"]
        if not source or source.get("category") != category or not save_path or source.get("save_path") != save_path:
            raise ValueError("Bron, categorie of opslagpad van de cross-seed is gewijzigd")
        pause = self.qb.pause_field()
        fields = torrent_form.add_fields(category, pause, paused=True, tags=["mso-cross-seed"], save_path=save_path)
        return {"instance": self.qb.instance_id, "source": row["source_hash"], "download_id": row["candidate_hash"],
                "category": category, "save_path": save_path, "pause_field": pause, "mode": "torrents/add",
                "fields": {**fields, "torrents": [torrent_form.FILENAME, torrent_form.CONTENT_TYPE]}}

    def torrent_state(self, h: str, *, category: str, save_path: str) -> str | None:
        torrent = self.qb.torrent(h)
        if torrent is None:
            return None
        if torrent.get("category") != category or torrent.get("save_path") != save_path:
            raise ValueError("De ontvangen torrent heeft een andere categorie of een ander opslagpad")
        value = torrent.get("state")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("De torrentstatus op de bron is onleesbaar")
        return value

    def live_torrents(self) -> list[dict[str, Any]]:
        return list(self.qb.torrents() or [])

    def delete_with_files(self, h: str) -> None:
        self.qb.delete_with_files([h])


@dataclass
class AdapterInfo:
    id: str
    label: str
    implemented: bool
    note: str


ADAPTERS: dict[str, AdapterInfo] = {
    "qbittorrent": AdapterInfo(
        "qbittorrent",
        "qBittorrent",
        True,
        "WebAPI v2; API-sleutel (≥ 5.2) of sessie-login; versie-/capability-detectie.",
    ),
    "transmission": AdapterInfo(
        "transmission", "Transmission", False, "Nog niet geïmplementeerd (RPC-adapter gepland)."
    ),
    "deluge": AdapterInfo(
        "deluge", "Deluge", False, "Nog niet geïmplementeerd (JSON-RPC via deluge-web gepland)."
    ),
}


def get_adapter(client_id: str = "qbittorrent") -> TorrentClientAdapter:
    info = ADAPTERS.get(client_id)
    if info is None or not info.implemented:
        raise cb.ConnectorNotConfigured(client_id)
    return QbitAdapter()


def configured_client() -> str | None:
    return "qbittorrent" if cb.is_configured("qbittorrent") else None
