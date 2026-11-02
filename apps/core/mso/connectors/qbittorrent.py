"""qBittorrent WebAPI v2 connector.

Auth
- API key (``Authorization: Bearer <key>``) where the server supports it (qBittorrent ≥ 5.2). Support is
  *detected*: the key is tried first; on 401/403 we fall back to session auth if username/password exist.
- Session auth: ``POST /api/v2/auth/login`` with ``Referer``/``Origin`` set to the WebUI origin (qBittorrent's
  CSRF protection). The SID cookie lives only in this object's in-memory cookie jar — it is never logged,
  stored or returned. A 403 on any call triggers exactly one re-login + retry.

Capabilities are derived from ``app/webapiVersion`` (and probing ``app/buildInfo``), so old versions keep
working with reduced features (e.g. no ``inactiveSeedingTimeLimit``, no ``private`` field in torrents/info).

There is deliberately **no delete operation** in this client: Media Source Optimizer never removes torrents or
files (integrity rule ``integrity.never_delete_files``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

import httpx

from mso.connectors import base as cb

LOGGER = logging.getLogger(__name__)
CONNECTOR_ID = "qbittorrent"
INFINITE_ETA = 8640000


class QbitError(RuntimeError):
    """Generic qBittorrent API error (Dutch message, never contains secrets)."""


class QbitAuthError(QbitError):
    pass


class QbitUnsupported(QbitError):
    """Endpoint/feature not available on this qBittorrent version."""


def _vtuple(v: str | None) -> tuple[int, ...]:
    out: list[int] = []
    for part in (v or "").lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def capabilities_for(app_version: str | None, webapi: str | None, *, build_info: bool) -> dict[str, Any]:
    """Feature flags by WebAPI version (documented in the qBittorrent WebUI API changelog)."""
    w = _vtuple(webapi)
    a = _vtuple(app_version)
    return {
        "webapi": webapi,
        "sync_maindata": w >= (2, 0),
        "share_limits": w >= (2, 0, 1),
        "inactive_seeding_time_limit": w >= (2, 9, 2),
        "infohash_v2": w >= (2, 8, 3),
        "private_flag_in_info": w >= (2, 11, 1),
        "build_info": build_info,
        "network_interfaces": w >= (2, 3),
        "start_stop_naming": w >= (2, 11),  # pause/resume renamed to stop/start
        "free_space_on_disk": w >= (2, 1),
        "api_key": a >= (5, 2),
    }


class MaindataSync:
    """Incremental ``sync/maindata`` polling: keeps ``rid`` and the merged state in memory.

    qBittorrent's sync state is tied to the WebUI session; after a re-login the server answers with
    ``full_update`` and we simply replace our state."""

    def __init__(self) -> None:
        self.rid = 0
        self.torrents: dict[str, dict[str, Any]] = {}
        self.categories: dict[str, dict[str, Any]] = {}
        self.server_state: dict[str, Any] = {}
        self.removed: list[str] = []
        self.full_updates = 0

    def merge(self, data: dict[str, Any]) -> None:
        self.removed = []
        if data.get("full_update"):
            self.full_updates += 1
            self.torrents = {}
            self.categories = {}
            self.server_state = {}
        for h, partial in (data.get("torrents") or {}).items():
            cur = self.torrents.setdefault(h, {"hash": h})
            cur.update(partial)
        for h in data.get("torrents_removed") or []:
            if self.torrents.pop(h, None) is not None:
                self.removed.append(h)
        for name, partial in (data.get("categories") or {}).items():
            self.categories.setdefault(name, {"name": name}).update(partial)
        for name in data.get("categories_removed") or []:
            self.categories.pop(name, None)
        self.server_state.update(data.get("server_state") or {})
        self.rid = int(data.get("rid") or 0)


class QbitClient:
    def __init__(
        self,
        url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base = url.rstrip("/")
        self._username = username
        self._password = password
        self._api_key = api_key
        self._http = cb.http_client(
            self.base, timeout=timeout, headers={"Referer": self.base + "/", "Origin": self.base}
        )
        self._lock = threading.RLock()
        self._authed = False
        self.auth_method: str | None = None  # api_key | session
        self.api_key_status: str = "niet_ingesteld" if not api_key else "onbekend"
        self.version: str | None = None
        self.webapi: str | None = None
        self.build: dict[str, Any] | None = None
        self.capabilities: dict[str, Any] = {}
        self.sync = MaindataSync()
        self.login_count = 0

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_config(cls, cfg: cb.ConnectorConfig | None = None) -> QbitClient:
        cfg = cfg or cb.load_config(CONNECTOR_ID)
        return cls(
            cfg.url,
            username=cfg.secrets.get("username"),
            password=cfg.secrets.get("password"),
            api_key=cfg.secrets.get("api_key"),
            timeout=float((cfg.options or {}).get("timeout_s", 20)),
        )

    _cache: dict[str, QbitClient] = {}
    _cache_lock = threading.Lock()

    @classmethod
    def cached(cls) -> QbitClient:
        """Process-wide client per configuration (keeps the session and the maindata ``rid``)."""
        cfg = cb.load_config(CONNECTOR_ID)
        fp = hashlib.sha256(
            json.dumps(
                [cfg.url, sorted(cfg.secrets.items()), cfg.options], sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        with cls._cache_lock:
            client = cls._cache.get(fp)
            if client is None:
                for old in cls._cache.values():
                    old.close()
                cls._cache.clear()
                client = cls.from_config(cfg)
                cls._cache[fp] = client
            return client

    @classmethod
    def reset_cache(cls) -> None:
        with cls._cache_lock:
            for c in cls._cache.values():
                c.close()
            cls._cache.clear()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ auth
    def _headers(self) -> dict[str, str]:
        if self.auth_method == "api_key" and self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _login(self) -> None:
        if not self._username or self._password is None:
            raise QbitAuthError("Geen gebruikersnaam/wachtwoord ingesteld voor qBittorrent")
        self._http.cookies.clear()
        r = self._http.post(
            "/api/v2/auth/login", data={"username": self._username, "password": self._password}
        )
        self.login_count += 1
        if r.status_code == 403:
            raise QbitAuthError("qBittorrent weigert inloggen (IP tijdelijk geblokkeerd na te veel pogingen)")
        if r.status_code != 200 or r.text.strip() != "Ok.":
            raise QbitAuthError("Inloggen bij qBittorrent mislukt: gebruikersnaam of wachtwoord onjuist")
        self.auth_method = "session"

    def _try_api_key(self) -> bool:
        r = self._http.get("/api/v2/app/version", headers={"Authorization": f"Bearer {self._api_key}"})
        if r.status_code == 200:
            self.auth_method = "api_key"
            self.api_key_status = "werkt"
            return True
        self.api_key_status = "geweigerd"
        return False

    def authenticate(self) -> None:
        with self._lock:
            if self._authed:
                return
            if self._api_key and self._try_api_key():
                self._authed = True
                return
            if self._api_key and not self._username:
                raise QbitAuthError(
                    "API-sleutel geweigerd door qBittorrent (ongeldig of versie < 5.2) en geen "
                    "gebruikersnaam/wachtwoord ingesteld als terugval"
                )
            self._login()
            self._authed = True

    def _request(self, method: str, path: str, *, retry: bool = True, **kw: Any) -> httpx.Response:
        self.authenticate()
        extra = dict(kw.pop("headers", None) or {})
        headers = extra | self._headers()
        r = self._http.request(method, "/api/v2/" + path.lstrip("/"), headers=headers, **kw)
        if r.status_code in (401, 403) and retry:
            # session expired (or API key revoked): re-authenticate once
            with self._lock:
                self._authed = False
                if self.auth_method == "session":
                    self._login()
                    self._authed = True
                else:
                    self.authenticate()
            return self._request(method, path, retry=False, headers=extra, **kw)
        if r.status_code in (401, 403):
            raise QbitAuthError("qBittorrent weigert toegang (sessie of API-sleutel ongeldig)")
        if r.status_code == 404:
            raise QbitUnsupported(f"Endpoint {path} bestaat niet in deze qBittorrent-versie")
        if r.status_code == 409:
            raise QbitError(f"qBittorrent weigert {path}: {r.text[:200]}")
        if r.status_code >= 400:
            raise QbitError(f"qBittorrent {path} gaf HTTP {r.status_code}")
        return r

    def _get_json(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params={k: v for k, v in params.items() if v is not None}).json()

    def _post(self, path: str, data: dict[str, Any] | None = None, files: Any = None) -> httpx.Response:
        return self._request("POST", path, data=data or {}, files=files)

    # ------------------------------------------------------------------ discovery
    def detect(self) -> dict[str, Any]:
        """Version + capability detection (cheap, call once per client)."""
        self.version = self._request("GET", "app/version").text.strip()
        try:
            self.webapi = self._request("GET", "app/webapiVersion").text.strip()
        except QbitUnsupported:
            self.webapi = "2.0"
        try:
            self.build = self._get_json("app/buildInfo")
            has_build = True
        except QbitUnsupported:
            self.build, has_build = None, False
        self.capabilities = capabilities_for(self.version, self.webapi, build_info=has_build)
        self.capabilities["auth_method"] = self.auth_method
        self.capabilities["api_key_status"] = self.api_key_status
        if self.build:
            self.capabilities["libtorrent"] = self.build.get("libtorrent")
        return self.capabilities

    def ensure_detected(self) -> dict[str, Any]:
        if not self.capabilities:
            self.detect()
        return self.capabilities

    def cap(self, name: str) -> bool:
        return bool(self.ensure_detected().get(name))

    # ------------------------------------------------------------------ reads
    def torrents(
        self,
        hashes: list[str] | None = None,
        *,
        filter: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._get_json(
            "torrents/info", hashes="|".join(hashes) if hashes else None, filter=filter, category=category
        )

    def torrent(self, h: str) -> dict[str, Any] | None:
        items = self.torrents([h])
        return items[0] if items else None

    def properties(self, h: str) -> dict[str, Any]:
        return self._get_json("torrents/properties", hash=h)

    def trackers(self, h: str) -> list[dict[str, Any]]:
        return self._get_json("torrents/trackers", hash=h)

    def files(self, h: str) -> list[dict[str, Any]]:
        return self._get_json("torrents/files", hash=h)

    def transfer_info(self) -> dict[str, Any]:
        return self._get_json("transfer/info")

    def speed_limits_mode(self) -> int:
        return int(self._request("GET", "transfer/speedLimitsMode").text.strip() or 0)

    def preferences(self) -> dict[str, Any]:
        return self._get_json("app/preferences")

    def network_interfaces(self) -> list[dict[str, Any]]:
        if not self.cap("network_interfaces"):
            return []
        return self._get_json("app/networkInterfaceList")

    def categories(self) -> list[str]:
        return sorted(self.category_details())

    def category_details(self) -> dict[str, dict[str, Any]]:
        return self._get_json("torrents/categories") or {}

    def maindata(self) -> MaindataSync:
        data = self._get_json("sync/maindata", rid=self.sync.rid)
        self.sync.merge(data)
        return self.sync

    # ------------------------------------------------------------------ writes (only via change sets /
    # audited actions)
    def set_preferences(self, values: dict[str, Any]) -> None:
        self._post("app/setPreferences", {"json": json.dumps(values)})

    def set_upload_limit(self, hashes: list[str], limit: int) -> None:
        self._post("torrents/setUploadLimit", {"hashes": "|".join(hashes), "limit": int(limit)})

    def set_download_limit(self, hashes: list[str], limit: int) -> None:
        self._post("torrents/setDownloadLimit", {"hashes": "|".join(hashes), "limit": int(limit)})

    def set_share_limits(
        self,
        hashes: list[str],
        *,
        ratio_limit: float,
        seeding_time_limit: int,
        inactive_seeding_time_limit: int | None = None,
    ) -> None:
        if not self.cap("share_limits"):
            raise QbitUnsupported(
                "Deelgrenzen per torrent worden door deze qBittorrent-versie niet ondersteund"
            )
        data: dict[str, Any] = {
            "hashes": "|".join(hashes),
            "ratioLimit": ratio_limit,
            "seedingTimeLimit": int(seeding_time_limit),
        }
        if self.cap("inactive_seeding_time_limit"):
            data["inactiveSeedingTimeLimit"] = int(
                -2 if inactive_seeding_time_limit is None else inactive_seeding_time_limit
            )
        self._post("torrents/setShareLimits", data)

    def recheck(self, hashes: list[str]) -> None:
        self._post("torrents/recheck", {"hashes": "|".join(hashes)})

    def reannounce(self, hashes: list[str]) -> None:
        self._post("torrents/reannounce", {"hashes": "|".join(hashes)})

    def queue_top(self, hashes: list[str]) -> None:
        self._post("torrents/topPrio", {"hashes": "|".join(hashes)})

    def queue_bottom(self, hashes: list[str]) -> None:
        self._post("torrents/bottomPrio", {"hashes": "|".join(hashes)})

    def queue_up(self, hashes: list[str]) -> None:
        self._post("torrents/increasePrio", {"hashes": "|".join(hashes)})

    def queue_down(self, hashes: list[str]) -> None:
        self._post("torrents/decreasePrio", {"hashes": "|".join(hashes)})

    def toggle_speed_limits_mode(self) -> None:
        self._post("transfer/toggleSpeedLimitsMode")

    def add_torrent(
        self,
        *,
        url: str | None = None,
        data: bytes | None = None,
        category: str,
        paused: bool = False,
        tags: list[str] | None = None,
        save_path: str | None = None,
        skip_checking: bool = False,
        filename: str = "release.torrent",
    ) -> None:
        """Add a torrent (URL/magnet or .torrent bytes). Paused uses both the old (``paused``) and the new
        (``stopped``) parameter names so every version honours it. ``skip_checking`` defaults to False:
        existing data is always hash-verified before it is seeded."""
        if not url and not data:
            raise ValueError("url of data is verplicht")
        form: dict[str, Any] = {
            "category": category,
            "paused": "true" if paused else "false",
            "stopped": "true" if paused else "false",
            "skip_checking": "true" if skip_checking else "false",
        }
        if tags:
            form["tags"] = ",".join(tags)
        if save_path:
            form["savepath"] = save_path
            form["autoTMM"] = "false"
            form["contentLayout"] = "Original"
        if url:
            form["urls"] = url
        files = {"torrents": (filename, data, "application/x-bittorrent")} if data else None
        r = self._post("torrents/add", form, files=files)
        if r.text.strip() == "Fails.":
            raise QbitError("qBittorrent weigerde de torrent (ongeldig of al aanwezig)")


# ------------------------------------------------------------------ connector registration
def _test(cfg: cb.ConnectorConfig) -> cb.TestResult:
    if not cfg.secrets.get("api_key") and not cfg.secrets.get("username"):
        return cb.TestResult(
            False, "auth_failed", "Vul een API-sleutel (qBittorrent ≥ 5.2) of gebruikersnaam en wachtwoord in"
        )
    client = QbitClient.from_config(cfg)
    try:
        try:
            caps = client.detect()
        except QbitAuthError as exc:
            return cb.TestResult(False, "auth_failed", str(exc))
        info = client.transfer_info()
        msg = f"qBittorrent {client.version} (WebAPI {client.webapi}), ingelogd via " + (
            "API-sleutel" if client.auth_method == "api_key" else "sessie"
        )
        status = "healthy"
        if cfg.secrets.get("api_key") and client.api_key_status == "geweigerd":
            msg += (
                "; API-sleutel geweigerd"
                + (" (deze versie ondersteunt nog geen API-sleutels)" if not caps.get("api_key") else "")
                + ", teruggevallen op sessie"
            )
            status = "degraded"
        if info.get("connection_status") == "firewalled":
            msg += "; let op: poort niet bereikbaar (firewalled)"
            status = "degraded"
        elif info.get("connection_status") == "disconnected":
            msg += "; let op: geen netwerkverbinding"
            status = "degraded"
        caps = dict(caps, connection_status=info.get("connection_status"))
        return cb.TestResult(True, status, msg, version=client.version, capabilities=caps)
    finally:
        client.close()


cb.register(
    cb.ConnectorSpec(
        id=CONNECTOR_ID,
        kind="torrent_client",
        label="qBittorrent",
        secret_fields=[
            cb.SecretField("username", "Gebruikersnaam", required=False),
            cb.SecretField("password", "Wachtwoord", required=False),
            cb.SecretField("api_key", "API-sleutel (qBittorrent ≥ 5.2, optioneel)", required=False),
        ],
        test=_test,
        optional=True,
        default_url="",
        description="Primaire torrentclient (WebAPI v2). API-sleutel waar ondersteund, anders veilige "
        "sessie-login. MSO verwijdert nooit torrents of bestanden.",
    )
)
