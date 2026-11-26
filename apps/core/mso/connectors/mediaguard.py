"""MediaGuard Ultimate connector (optional).

MSO talks to MediaGuard **only** through its documented core API (``/api/v1``, normally reached through the
MediaGuard web container on port 30080 which proxies ``/api/v1`` to core). It never touches MediaGuard's
database, files, configuration or secrets, and never runs a second wanted/import agent.

Authentication (least privilege first):
- ``apikey`` (recommended, MediaGuard with API keys): header ``X-Api-Key: mgk_…``. The key carries its own
  role in MediaGuard (``viewer`` or ``operator``), needs no session or CSRF and can be revoked in MediaGuard.
  Contract: docs/MEDIAGUARD_API_KEYS.md. An older MediaGuard without API keys answers 401 → clear message.
- ``user``: a dedicated MediaGuard user. Role ``operator`` allows "request search"; ``viewer``
  is enough for read-only use. MSO logs in via ``POST /api/v1/auth/login`` and keeps the ``mg_session``
  cookie and CSRF token **in memory only**; on 401 it logs in again once.
- ``token``: MediaGuard's internal ``X-MediaGuard-Token``. That principal is an *admin* machine user in
  MediaGuard — broader than MSO needs; the UI warns about it.

Write safety: :class:`MediaGuardClient` refuses, before any network I/O, every non-GET request except
``POST /api/v1/auth/login``, ``POST /api/v1/wanted/{id}/search`` and (1.4.0, approved malware cleanup only)
``DELETE /api/v1/instances/{id}/queue/{qid}?blocklist=true&remove_from_client=false`` (see ``ALLOWED_WRITES``). Notably
``retry-relaxed`` (relaxes quality), ``actions``, ``ignore``/``unignore``, ``backfill-plan`` (refreshes
MediaGuard's own tables) and interactive diagnosis are never called.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from mso.connectors import base as cb
from mso.connectors import mediaguard_response as schema
from mso.security import UnsafeTarget, redact

LOGGER = logging.getLogger(__name__)
CID = "mediaguard"
API = "/api/v1"
ALLOWED_WRITES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/api/v1/auth/login$")),
    ("POST", re.compile(r"^/api/v1/wanted/\d+/search$")),
    # 1.4.0: block a suspicious release in Sonarr/Radarr (only with blocklist=true & remove_from_client=false,
    # enforced in request(); MSO deletes the torrent itself via its own approved cleanup change)
    ("DELETE", re.compile(r"^/api/v1/instances/[A-Za-z0-9_.-]+/queue/\d+$")),
)
QUEUE_BLOCK_RE = re.compile(r"^/api/v1/instances/[A-Za-z0-9_.-]+/queue/\d+$")
FASTAPI_NOT_FOUND = "Not Found"
ROLE_LEVELS = {"none": 0, "viewer": 1, "operator": 2, "admin": 3}
INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
CATEGORY_FIELDS = ("tvCategory", "movieCategory")  # Sonarr / Radarr download-client field (MediaGuard engine.py)
MG_OWNED_PREFIX = "MediaGuard · "  # MediaGuard provisioning templates.PREFIX
HEALTH_MAX = 50


def _valid_instance_id(v: Any) -> bool:
    return isinstance(v, str) and v not in (".", "..") and bool(INSTANCE_ID_RE.fullmatch(v))


def _instance_segment(instance_id: str) -> str:
    if not _valid_instance_id(instance_id):
        raise ValueError("ongeldige MediaGuard-instantie")
    return instance_id


def _opt_str(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s[:100] or None


def _health_items(raw: Any) -> list[dict[str, Any]]:
    out = []
    for h in raw if isinstance(raw, list) else []:
        if not isinstance(h, dict):
            continue
        out.append(
            {
                "source": str(h.get("source") or "")[:100],
                "type": str(h.get("type") or "")[:20].lower(),
                "message": redact(str(h.get("message") or "")).strip()[:500],
            }
        )
        if len(out) >= HEALTH_MAX:
            break
    return out


class MediaGuardError(Exception):
    """``code``: offline | auth_failed | forbidden | not_found | route_missing | quota | upstream |
    refused."""

    def __init__(self, code: str, message: str, status: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail


class UnreadableResponse(MediaGuardError):
    def __init__(self, route_id: str) -> None:
        super().__init__("unknown", f"MediaGuard-antwoord is onleesbaar ({route_id})")
        self.route_id = route_id


def valid_read_response(route_id: str, data: Any, params: dict[str, Any] | None = None) -> bool:
    match route_id:
        case "health":
            return schema.has_fields(data, version=str) and bool(data["version"])
        case "auth_me":
            return schema.has_fields(data, role=str) and data["role"] in ("viewer", "operator", "admin")
        case "status":
            return schema.has_fields(data, emergency_stop=dict) and schema.has_fields(
                data["emergency_stop"], active=bool
            ) and (data.get("version") is None or isinstance(data["version"], str))
        case "wanted_list":
            params = params or {}
            return schema.wanted_page(data, params.get("page", 1), params.get("page_size", 50))
        case "wanted_kpis":
            return schema.kpis(data)
        case "wanted_reasons":
            return schema.reasons(data)
        case "backfill_quota":
            return schema.quota(data)
        case "wanted_item":
            return schema.wanted_item(data)
        case "wanted_diagnosis":
            return schema.diagnosis(data)
        case "policy_effective":
            return schema.has_fields(data, profile_id=str, config=dict)
        case "instances_list":
            return isinstance(data, list) and all(
                isinstance(row, dict) and _valid_instance_id(row.get("id")) for row in data
            )
        case "instance_queue":
            return schema.queue(data)
        case "instance_download_clients":
            return isinstance(data, list) and all(schema.has_fields(row, id=int, name=str, fields=list) for row in data)
        case "instance_health":
            return isinstance(data, list) and all(schema.has_fields(row, source=str, type=str, message=str)
                                                 for row in data)
        case "provisioning_config":
            return schema.has_fields(data, download_clients=list, instances=dict, defaults=dict)
        case _:
            return False


def read_response(r: httpx.Response, route_id: str, params: dict[str, Any] | None = None) -> Any:
    try:
        data = r.json()
    except ValueError as exc:
        raise UnreadableResponse(route_id) from exc
    if not r.is_success or not valid_read_response(route_id, data, params):
        raise UnreadableResponse(route_id)
    return data


class WriteRefused(MediaGuardError):
    def __init__(self, method: str, path: str) -> None:
        super().__init__(
            "refused", f"MSO roept MediaGuard-route {method} {path} nooit aan (alleen lezen + 'zoek opnieuw')"
        )


_SESSIONS: dict[str, dict[str, str]] = {}
_LOCK = threading.Lock()


def _session_key(url: str, username: str, password: str) -> str:
    return hashlib.sha256(f"{url}|{username}|{password}".encode()).hexdigest()


def reset_sessions() -> None:
    with _LOCK:
        _SESSIONS.clear()


def auth_method(cfg: cb.ConnectorConfig) -> str:
    method = str((cfg.options or {}).get("auth_method") or "")
    if method in ("apikey", "user", "token"):
        return method
    if cfg.secrets.get("api_key"):
        return "apikey"
    return "user" if cfg.secrets.get("username") else "token"


class MediaGuardClient:
    def __init__(self, cfg: cb.ConnectorConfig, *, timeout: float = 10.0) -> None:
        self.cfg = cfg
        self.base = cfg.url.rstrip("/")
        self.method = auth_method(cfg)
        self.timeout = timeout
        self.role: str | None = None

    @classmethod
    def from_config(cls) -> MediaGuardClient:
        return cls(cb.load_config(CID))

    # ------------------------------------------------------------------ auth
    def _skey(self) -> str:
        return _session_key(
            self.base, self.cfg.secrets.get("username", ""), self.cfg.secrets.get("password", "")
        )

    def login(self) -> dict[str, str]:
        user, pw = self.cfg.secrets.get("username"), self.cfg.secrets.get("password")
        if not user or not pw:
            raise MediaGuardError("auth_failed", "Gebruikersnaam en wachtwoord voor MediaGuard ontbreken")
        r = self._send("POST", f"{API}/auth/login", json={"username": user, "password": pw})
        if r.status_code == 401:
            raise MediaGuardError("auth_failed", "MediaGuard weigert de inloggegevens", 401)
        if r.status_code == 404:
            raise MediaGuardError("route_missing", "MediaGuard-login-route ontbreekt", 404)
        if r.status_code >= 500:
            raise MediaGuardError("offline", f"MediaGuard antwoordt met HTTP {r.status_code}", r.status_code)
        if r.status_code != 200:
            raise MediaGuardError(
                "upstream", f"Inloggen bij MediaGuard mislukt (HTTP {r.status_code})", r.status_code
            )
        cookie = r.cookies.get("mg_session")
        data = r.json()
        if not cookie:
            raise MediaGuardError("upstream", "MediaGuard gaf geen sessiecookie terug")
        sess = {
            "cookie": cookie,
            "csrf": str(data.get("csrf_token") or ""),
            "role": str(data.get("role") or ""),
        }
        with _LOCK:
            _SESSIONS[self._skey()] = sess
        self.role = sess["role"]
        return sess

    def _auth_headers(self, *, mutating: bool, fresh: bool = False) -> dict[str, str]:
        if self.method == "apikey":
            key = self.cfg.secrets.get("api_key")
            if not key:
                raise MediaGuardError("auth_failed", "MediaGuard-API-sleutel ontbreekt")
            return {"X-Api-Key": key}  # stateless: no session cookie, no CSRF
        if self.method == "token":
            token = self.cfg.secrets.get("internal_token")
            if not token:
                raise MediaGuardError("auth_failed", "Interne MediaGuard-token ontbreekt")
            self.role = "admin"
            return {"X-MediaGuard-Token": token}
        with _LOCK:
            sess = None if fresh else _SESSIONS.get(self._skey())
        if sess is None:
            sess = self.login()
        self.role = sess.get("role") or self.role
        h = {"Cookie": f"mg_session={sess['cookie']}"}
        if mutating and sess.get("csrf"):
            h["X-CSRF-Token"] = sess["csrf"]
        return h

    # ------------------------------------------------------------------ transport
    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with cb.http_client(self.base, timeout=self.timeout) as client:
                return client.request(method, path, **kwargs)
        except (UnsafeTarget, httpx.HTTPError) as exc:
            if isinstance(exc, UnsafeTarget) and exc.code != "dns_failure":
                raise
            raise MediaGuardError("offline", "MediaGuard is niet bereikbaar") from exc

    def _check_allowed(self, method: str, path: str) -> None:
        if method.upper() in ("GET", "HEAD"):
            return
        if not any(m == method.upper() and p.match(path) for m, p in ALLOWED_WRITES):
            raise WriteRefused(method.upper(), path)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        auth: bool = True,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self._check_allowed(method, path)
        if method.upper() == "DELETE" and QUEUE_BLOCK_RE.match(path):
            p = {k: str(v).lower() for k, v in (params or {}).items()}
            if p.get("blocklist") != "true" or p.get("remove_from_client") != "false":
                raise WriteRefused(method.upper(), path)
        mutating = method.upper() not in ("GET", "HEAD")
        for attempt in (1, 2):
            h = dict(headers or {})
            if auth:
                h.update(self._auth_headers(mutating=mutating, fresh=attempt == 2))
            r = self._send(method, path, params=params, json=json, headers=h)
            if r.status_code == 401 and auth and self.method == "user" and attempt == 1:
                continue  # session expired → log in once more
            return r
        return r

    @staticmethod
    def _detail(r: httpx.Response) -> Any:
        try:
            return r.json().get("detail")
        except Exception:
            return None

    def raise_for(self, r: httpx.Response) -> None:
        if r.status_code < 400:
            return
        detail = self._detail(r)
        if r.status_code == 401:
            if self.method == "apikey":
                raise MediaGuardError(
                    "auth_failed",
                    "MediaGuard weigert de API-sleutel: ongeldig of ingetrokken, of deze MediaGuard-versie "
                    "ondersteunt nog geen API-sleutels (zie docs/MEDIAGUARD_API_KEYS.md)",
                    401,
                    detail,
                )
            raise MediaGuardError("auth_failed", "Niet (meer) ingelogd bij MediaGuard", 401, detail)
        if r.status_code == 403:
            raise MediaGuardError("forbidden", "MediaGuard-rol heeft onvoldoende rechten", 403, detail)
        if r.status_code == 404:
            if detail == FASTAPI_NOT_FOUND:
                raise MediaGuardError(
                    "route_missing", "Deze MediaGuard-versie heeft deze route niet", 404, detail
                )
            raise MediaGuardError("not_found", str(detail or "Niet gevonden in MediaGuard"), 404, detail)
        if r.status_code == 405:
            raise MediaGuardError(
                "route_missing", "Methode niet ondersteund door deze MediaGuard-versie", 405
            )
        if r.status_code == 429:
            msg = detail.get("message") if isinstance(detail, dict) else str(detail or "Quotum bereikt")
            raise MediaGuardError("quota", f"MediaGuard-quotum: {msg}", 429, detail)
        if r.status_code >= 500:
            raise MediaGuardError("offline", f"MediaGuard antwoordt met HTTP {r.status_code}", r.status_code)
        raise MediaGuardError(
            "upstream", redact(f"MediaGuard HTTP {r.status_code}: {detail}"), r.status_code, detail
        )

    def get(
        self, path: str, params: dict[str, Any] | None = None, *, auth: bool = True, route_id: str | None = None
    ) -> Any:
        r = self.request("GET", path, params=params, auth=auth)
        self.raise_for(r)
        return r.json() if route_id is None else read_response(r, route_id, params)

    # ------------------------------------------------------------------ reads for the SABnzbd-category diagnosis
    # (1.6.0). All GET, rol viewer. MediaGuard already masks provider secrets; on top of that MSO keeps only an
    # explicit whitelist of fields, so host names, user names, URLs and (masked) keys never reach MSO's database,
    # API or UI. Errors are the usual MediaGuardError (forbidden / route_missing / not_found / offline).
    def instances(self) -> list[dict[str, Any]]:
        """``GET /api/v1/instances`` → per Sonarr/Radarr instance: id, app, media_type, name, archived/suspended,
        MediaGuard's ``download_category`` and its cached health messages (redacted)."""
        out = []
        for inst in self.get(f"{API}/instances") or []:
            if not isinstance(inst, dict) or not _valid_instance_id(inst.get("id")):
                continue
            out.append(
                {
                    "id": str(inst["id"]),
                    "app": str(inst.get("app") or ""),
                    "media_type": str(inst.get("media_type") or ""),
                    "name": str(inst.get("name") or inst["id"])[:100],
                    "archived": bool(inst.get("archived")),
                    "suspended": bool(inst.get("suspended")),
                    "download_category": _opt_str(inst.get("download_category")),
                    "health": _health_items(inst.get("health")),
                }
            )
        return out

    def download_clients(self, instance_id: str) -> list[dict[str, Any]]:
        """``GET /api/v1/instances/{id}/download-clients`` (MediaGuard ``manage.py`` provider list, secrets
        masked) → name, implementation, protocol, enable, priority and the category Sonarr (``tvCategory``) or
        Radarr (``movieCategory``) sends; ``mediaguard_owned`` = MediaGuard provisioning created it
        (name prefix ``MediaGuard · ``)."""
        iid = _instance_segment(instance_id)
        out = []
        for dc in self.get(f"{API}/instances/{iid}/download-clients") or []:
            if not isinstance(dc, dict):
                continue
            fields = {
                str(f.get("name")): f.get("value") for f in dc.get("fields") or [] if isinstance(f, dict)
            }
            field_name = next((k for k in CATEGORY_FIELDS if k in fields), None)
            name = str(dc.get("name") or "")[:100]
            out.append(
                {
                    "id": dc.get("id") if isinstance(dc.get("id"), int) else None,
                    "name": name,
                    "implementation": str(dc.get("implementation") or "")[:40],
                    "protocol": str(dc.get("protocol") or "")[:20],
                    "enable": bool(dc.get("enable", True)),
                    "priority": dc.get("priority") if isinstance(dc.get("priority"), int) else None,
                    "category_field": field_name,
                    "category": (_opt_str(fields.get(field_name)) if field_name else None),
                    "mediaguard_owned": name.startswith(MG_OWNED_PREFIX),
                }
            )
        return out

    def instance_health(self, instance_id: str) -> list[dict[str, Any]]:
        """``GET /api/v1/instances/{id}/health`` → Sonarr/Radarr's own health messages (source, type, message;
        message redacted)."""
        iid = _instance_segment(instance_id)
        return _health_items(self.get(f"{API}/instances/{iid}/health"))

    def provisioning_categories(self) -> dict[str, Any]:
        """``GET /api/v1/provisioning/config`` reduced to what decides the download category: per provisioned
        download client its name, implementation, enabled, target instances and per-instance category; per
        instance the provisioning mode and whether the download-client section is switched off; MediaGuard's
        resolved default category per instance. Hosts, user names and secret flags are dropped here."""
        cfg = self.get(f"{API}/provisioning/config") or {}
        clients = []
        for dc in cfg.get("download_clients") or []:
            if not isinstance(dc, dict):
                continue
            cats = dc.get("categories") if isinstance(dc.get("categories"), dict) else {}
            insts = dc.get("instances")
            clients.append(
                {
                    "name": str(dc.get("name") or "")[:100],
                    "implementation": str(dc.get("implementation") or "")[:40],
                    "enabled": bool(dc.get("enabled", True)),
                    "instances": [str(i) for i in insts] if isinstance(insts, list) else None,
                    "categories": {str(k): str(v) for k, v in cats.items() if v},
                }
            )
        instances = {}
        for iid, ic in (cfg.get("instances") or {}).items():
            if not isinstance(ic, dict):
                continue
            instances[str(iid)] = {
                "mode": str(ic.get("mode") or "auto"),
                "category": _opt_str(ic.get("category")),
                "downloadclients_disabled": "downloadclients" in (ic.get("disabled_sections") or []),
                "downloadclients_full_manage": "downloadclients" in (ic.get("full_manage") or []),
            }
        defaults = {
            str(iid): _opt_str(d.get("category"))
            for iid, d in (cfg.get("defaults") or {}).items()
            if isinstance(d, dict)
        }
        return {"download_clients": clients, "instances": instances, "default_categories": defaults}

    # ------------------------------------------------------------------ the single write MSO performs
    def request_search(self, mg_id: int, *, wait_seconds: float = 0) -> dict[str, Any]:
        """``POST /api/v1/wanted/{id}/search`` — MediaGuard's own 'Zoek nu'. Counts against (and never
        bypasses) MediaGuard's per-instance backfill quota."""
        r = self.request("POST", f"{API}/wanted/{int(mg_id)}/search", json={"wait_seconds": wait_seconds})
        self.raise_for(r)
        return r.json()


# ------------------------------------------------------------------ probe / capability detection
def probe(cfg: cb.ConnectorConfig) -> dict[str, Any]:
    """Reachability, version, auth, role and route capabilities. Never writes anything to MediaGuard
    (login only creates MSO's own session)."""
    caps: dict[str, bool | None] = {"health": None, "auth_me": None, "status": None, "openapi": None}
    out: dict[str, Any] = {
        "reachable": False,
        "version": None,
        "auth_ok": False,
        "role": None,
        "auth_method": auth_method(cfg),
        "capabilities": caps,
        "capability_source": "probe",
        "emergency_stop": None,
        "warnings": [],
        "error": None,
        "error_code": None,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    client = MediaGuardClient(cfg)
    start = time.monotonic()
    try:
        health = client.get(f"{API}/health", auth=False, route_id="health")
        out["reachable"] = True
        out["version"] = health["version"]
        caps["health"] = True
    except MediaGuardError as exc:
        caps["health"] = False if exc.code == "route_missing" else None
        out["error"], out["error_code"] = str(exc), exc.code
        if exc.code in ("offline",):
            out["latency_ms"] = int((time.monotonic() - start) * 1000)
            return out
        out["reachable"] = exc.code not in ("offline",)
    out["latency_ms"] = int((time.monotonic() - start) * 1000)
    # authentication + role
    try:
        me = client.get(f"{API}/auth/me", route_id="auth_me")
        out["auth_ok"] = True
        out["role"] = me["role"]
        caps["auth_me"] = True
        _probe_routes(client, out)
    except MediaGuardError as exc:
        if exc.code == "route_missing":
            caps["auth_me"] = False
        out["error"], out["error_code"] = str(exc), exc.code
        if exc.code == "offline":
            out["reachable"] = False
            out["auth_ok"] = False
        return out
    if out["auth_method"] == "apikey" and out["role"] == "admin":
        out["warnings"].append(
            "Deze MediaGuard-API-sleutel heeft rol 'admin'; maak een sleutel met rol 'operator' "
            "(of 'viewer' voor alleen-lezen) — least privilege."
        )
    elif out["auth_method"] == "apikey" and out["role"] == "viewer":
        out["warnings"].append(
            "API-sleutel met rol 'viewer': alleen-lezen. 'Vraag MediaGuard opnieuw te zoeken' vereist 'operator'."
        )
    if out["auth_method"] == "token":
        out["warnings"].append(
            "De interne MediaGuard-token geeft beheerdersrechten (admin) — ruimer dan MSO "
            "nodig heeft. Gebruik bij voorkeur een eigen MediaGuard-gebruiker met rol 'operator' "
            "(of 'viewer' voor alleen-lezen)."
        )
    elif out["role"] == "admin":
        out["warnings"].append(
            "Deze MediaGuard-gebruiker is beheerder; rol 'operator' volstaat (least privilege)."
        )
    elif out["role"] == "viewer":
        out["warnings"].append(
            "Rol 'viewer': alleen-lezen. 'Vraag MediaGuard opnieuw te zoeken' vereist 'operator'."
        )
    if out["emergency_stop"]:
        out["warnings"].append("MediaGuard-noodstop is actief: MSO vraagt geen zoekacties aan.")
    return out


def _probe_routes(client: MediaGuardClient, out: dict[str, Any]) -> None:
    """Bevestig alleen leesroutes waarvoor de gekoppelde rol bevoegd is."""
    from mso.features.mediaguard import compat

    ids: dict[str, str] = {}
    caps = out["capabilities"]
    level = ROLE_LEVELS.get(out["role"] or "none", 0)
    for route in sorted(compat.ROUTES, key=lambda route: "{" in route.path):
        if route.id in ("health", "auth_me", "openapi"):
            continue
        caps[route.id] = None
        if route.method != "GET" or level < ROLE_LEVELS[route.role]:
            continue
        path = route.path
        for name, value in ids.items():
            path = path.replace("{" + name + "}", value)
        if "{" in path:
            continue
        try:
            r = client.request("GET", path, params=route.probe_params, auth=route.role != "none")
        except MediaGuardError as exc:
            if exc.code == "offline":
                raise
            continue
        if r.is_success:
            try:
                body = read_response(r, route.id, route.probe_params)
            except UnreadableResponse:
                continue
            caps[route.id] = True
            if route.id == "status":
                out["emergency_stop"] = body["emergency_stop"]["active"]
                out["version"] = out["version"] or body.get("version")
            elif route.id == "wanted_list" and body["items"]:
                ids["wanted_id"] = str(body["items"][0]["id"])
            elif route.id == "instances_list" and body:
                ids["instance_id"] = body[0]["id"]
        elif r.status_code == 405 or (r.status_code == 404 and MediaGuardClient._detail(r) == FASTAPI_NOT_FOUND):
            caps[route.id] = False
        elif (
            route.id == "wanted_diagnosis" and r.status_code == 404
            and MediaGuardClient._detail(r) == "Nog geen diagnose opgeslagen"
        ):
            caps[route.id] = True  # De eigen handler bevestigt dat alleen de cache-inhoud ontbreekt.


def result_from_probe(p: dict[str, Any]) -> cb.TestResult:
    from mso.features.mediaguard import compat

    caps = p["capabilities"]
    if not p["reachable"]:
        return cb.TestResult(
            False,
            "offline",
            p.get("error") or "MediaGuard onbereikbaar",
            p.get("version"),
            {"routes": caps},
            p.get("latency_ms"),
        )
    if not p["auth_ok"]:
        status = "auth_failed" if p.get("error_code") in ("auth_failed", "forbidden") else "degraded"
        return cb.TestResult(
            False,
            status,
            p.get("error") or "Authenticatie mislukt",
            p.get("version"),
            {"routes": caps},
            p.get("latency_ms"),
        )
    unconfirmed = [r.label for r in compat.ROUTES if r.required and caps.get(r.id) is not True]
    msg = f"Verbonden met MediaGuard {p.get('version') or '?'} als rol '{p.get('role')}'"
    if unconfirmed:
        msg += f" (routes niet bevestigd: {', '.join(unconfirmed)})"
    if p["warnings"]:
        msg += ". " + " ".join(p["warnings"])
    return cb.TestResult(
        not unconfirmed,
        "healthy" if not unconfirmed else "degraded",
        msg,
        p.get("version"),
        {
            "routes": caps,
            "role": p.get("role"),
            "auth_method": p.get("auth_method"),
            "capability_source": p.get("capability_source"),
        },
        p.get("latency_ms"),
    )


def _test(cfg: cb.ConnectorConfig) -> cb.TestResult:
    return result_from_probe(probe(cfg))


cb.register(
    cb.ConnectorSpec(
        id=CID,
        kind="mediaguard",
        label="MediaGuard Ultimate",
        secret_fields=[
            cb.SecretField("api_key", "MediaGuard-API-sleutel (aanbevolen; sleutel met rol operator)", False),
            cb.SecretField("username", "MediaGuard-gebruikersnaam (alternatief; rol operator)", False),
            cb.SecretField("password", "MediaGuard-wachtwoord", False),
            cb.SecretField("internal_token", "Interne token (X-MediaGuard-Token; admin-rechten)", False),
        ],
        test=_test,
        optional=True,
        default_url="",
        description="Optioneel. Alleen-lezen koppeling via de gedocumenteerde MediaGuard-API plus "
        "'zoek opnieuw' "
        "(POST /api/v1/wanted/{id}/search) met quotum en idempotentie. Zonder MediaGuard werkt MSO "
        "zelfstandig.",
    )
)
