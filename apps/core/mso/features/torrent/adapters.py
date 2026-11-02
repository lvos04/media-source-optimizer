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

There is intentionally no ``delete``/``remove`` method in the protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from mso.connectors import base as cb
from mso.security import redact

TRACKER_STATUS = {0: "disabled", 1: "not_contacted", 2: "working", 3: "updating", 4: "not_working"}
# qBittorrent ≥ 5.1 (WebAPI 2.11.x) also reports 'tracker error' / 'unreachable' as distinct statuses 5/6
TRACKER_STATUS.update({5: "tracker_error", 6: "unreachable"})
PRIVATE_MSG = "this torrent is private"


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
        self, *, data: bytes, category: str, save_path: str | None, paused: bool, tags: list[str]
    ) -> None: ...
    def recheck(self, h: str) -> None: ...
    def reannounce(self, h: str) -> None: ...
    def has_torrent(self, h: str) -> bool: ...


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
        except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            props = {}
        if isinstance(props.get("is_private"), bool):
            return props["is_private"]
        if trackers and any(t.kind == "dht" for t in trackers):
            return False  # DHT row without the "private" message → public
        return None

    # ---- applier primitives (paths documented in applier.py)
    def read_value(self, path: str) -> Any:
        kind, _, rest = path.partition(":")
        if kind == "pref":
            return self.qb.preferences().get(rest)
        if kind == "transfer" and rest == "alt_speed":
            return bool(self.qb.speed_limits_mode())
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            t = self.qb.torrent(h)
            if t is None:
                raise ValueError("torrent niet (meer) aanwezig in qBittorrent")
            if field_ in ("up_limit", "dl_limit"):
                return int(t.get(field_) or 0)
            if field_ == "share_limits":
                return {
                    "ratio_limit": t.get("ratio_limit", -2),
                    "seeding_time_limit": t.get("seeding_time_limit", -2),
                    "inactive_seeding_time_limit": t.get("inactive_seeding_time_limit", -2),
                }
        raise ValueError(f"onbekend pad {path}")

    def write_value(self, path: str, value: Any) -> None:
        kind, _, rest = path.partition(":")
        if kind == "pref":
            self.qb.set_preferences({rest: value})
            return
        if kind == "transfer" and rest == "alt_speed":
            if bool(self.qb.speed_limits_mode()) != bool(value):
                self.qb.toggle_speed_limits_mode()
            return
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            if field_ == "up_limit":
                self.qb.set_upload_limit([h], int(value))
                return
            if field_ == "dl_limit":
                self.qb.set_download_limit([h], int(value))
                return
            if field_ == "share_limits":
                self.qb.set_share_limits(
                    [h],
                    ratio_limit=float(value["ratio_limit"]),
                    seeding_time_limit=int(value["seeding_time_limit"]),
                    inactive_seeding_time_limit=value.get("inactive_seeding_time_limit"),
                )
                return
        raise ValueError(f"onbekend pad {path}")

    def add_torrent(
        self, *, data: bytes, category: str, save_path: str | None, paused: bool, tags: list[str]
    ) -> None:
        self.qb.add_torrent(
            data=data, category=category, paused=paused, tags=tags, save_path=save_path, skip_checking=False
        )

    def recheck(self, h: str) -> None:
        self.qb.recheck([h])

    def reannounce(self, h: str) -> None:
        self.qb.reannounce([h])

    def has_torrent(self, h: str) -> bool:
        return self.qb.torrent(h) is not None


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
