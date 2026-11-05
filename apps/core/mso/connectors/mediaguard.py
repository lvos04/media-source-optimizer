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
from mso.security import redact

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


class MediaGuardError(Exception):
    """``code``: offline | auth_failed | forbidden | not_found | route_missing | quota | upstream |
    refused."""

    def __init__(self, code: str, message: str, status: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail


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
        try:
            with cb.http_client(self.base, timeout=self.timeout) as c:
                r = c.post(f"{API}/auth/login", json={"username": user, "password": pw})
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise MediaGuardError("offline", "MediaGuard is niet bereikbaar") from exc
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
            try:
                with cb.http_client(self.base, timeout=self.timeout) as c:
                    r = c.request(method, path, params=params, json=json, headers=h)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                raise MediaGuardError("offline", "MediaGuard is niet bereikbaar") from exc
            except httpx.HTTPError as exc:
                raise MediaGuardError("offline", redact(f"HTTP-fout naar MediaGuard: {exc}")) from exc
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

    def get(self, path: str, params: dict[str, Any] | None = None, *, auth: bool = True) -> Any:
        r = self.request("GET", path, params=params, auth=auth)
        self.raise_for(r)
        return r.json()

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
    from mso.features.mediaguard import compat

    out: dict[str, Any] = {
        "reachable": False,
        "version": None,
        "auth_ok": False,
        "role": None,
        "auth_method": auth_method(cfg),
        "capabilities": {},
        "capability_source": None,
        "emergency_stop": None,
        "warnings": [],
        "error": None,
        "error_code": None,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    client = MediaGuardClient(cfg)
    start = time.monotonic()
    try:
        health = client.get(f"{API}/health", auth=False)
        out["reachable"] = True
        out["version"] = health.get("version")
    except MediaGuardError as exc:
        out["error"], out["error_code"] = str(exc), exc.code
        if exc.code in ("offline",):
            out["latency_ms"] = int((time.monotonic() - start) * 1000)
            return out
        out["reachable"] = exc.code not in ("offline",)
    out["latency_ms"] = int((time.monotonic() - start) * 1000)
    # authentication + role
    try:
        me = client.get(f"{API}/auth/me")
        out["auth_ok"] = True
        out["role"] = me.get("role") or client.role
    except MediaGuardError as exc:
        out["error"], out["error_code"] = str(exc), exc.code
        if exc.code == "offline":
            out["reachable"] = False
        return out
    try:
        st = client.get(f"{API}/status")
        out["emergency_stop"] = bool((st.get("emergency_stop") or {}).get("active"))
        out["version"] = out["version"] or st.get("version")
    except MediaGuardError:
        pass
    # capabilities: OpenAPI document first, then route probing
    caps: dict[str, bool | None] = {}
    try:
        spec = client.get(f"{API}/openapi.json", auth=False)
        paths = spec.get("paths") or {}
        for route in compat.ROUTES:
            ops = paths.get(route.path) or {}
            caps[route.id] = route.method.lower() in ops
        caps["openapi"] = True  # the document itself is not listed in its own paths
        out["capability_source"] = "openapi"
    except MediaGuardError:
        out["capability_source"] = "probe"
        caps = _probe_routes(client)
        caps["openapi"] = False
    out["capabilities"] = caps
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


def _probe_routes(client: MediaGuardClient) -> dict[str, bool | None]:
    """Without OpenAPI: GET each read route; FastAPI's own 404 body ``{"detail": "Not Found"}`` means the
    route does not exist. Write routes are never probed (unknown → treated as unavailable)."""
    from mso.features.mediaguard import compat

    caps: dict[str, bool | None] = {}
    sample_id: int | None = None
    try:
        page = client.get(f"{API}/wanted", {"page_size": 1})
        items = page.get("items") or []
        sample_id = int(items[0]["id"]) if items else None
    except MediaGuardError:
        pass
    for route in compat.ROUTES:
        if route.method != "GET":
            caps[route.id] = None
            continue
        path = route.path
        if "{wanted_id}" in path:
            if sample_id is None:
                caps[route.id] = None
                continue
            path = path.replace("{wanted_id}", str(sample_id))
        params = dict(route.probe_params or {})
        try:
            r = client.request("GET", path, params=params, auth=route.role != "none")
        except MediaGuardError:
            caps[route.id] = None
            continue
        caps[route.id] = not (
            r.status_code in (404, 405)
            and MediaGuardClient._detail(r) in (FASTAPI_NOT_FOUND, "Method Not Allowed")
        )
    return caps


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
    missing = [r.label for r in compat.ROUTES if r.required and caps.get(r.id) is not True]
    msg = f"Verbonden met MediaGuard {p.get('version') or '?'} als rol '{p.get('role')}'"
    if missing:
        msg += f"; ontbrekende routes: {', '.join(missing)}"
    if p["warnings"]:
        msg += ". " + " ".join(p["warnings"])
    return cb.TestResult(
        not missing,
        "healthy" if not missing else "degraded",
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
