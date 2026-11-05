"""SABnzbd connector (``/api?mode=…&output=json&apikey=…``).

Read: ``version``, ``queue``, ``history`` (incl. ``fail_message``/``stage_log``), ``server_stats``,
``get_config&section=servers``, ``status``/``fullstatus`` (per-server ``servererror``), ``warnings``,
``get_cats``. Direct (audited, rate limited, idempotent) user actions:
``addurl``/``addfile``. Server settings (priority/enable/optional) are ONLY written through the change-set
applier ``sabnzbd`` (drift detection + rollback restores the original values).

The API key is sent as SABnzbd requires (query parameter) but never logged, never put in exception
messages and never returned by MSO's API.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from typing import Any

import httpx

from mso import changes
from mso.audit import audit
from mso.connectors import base as cb
from mso.security import limiter, redact

LOGGER = logging.getLogger(__name__)
CONNECTOR_ID = "sabnzbd"
SERVER_FIELDS = {"priority": (0, 99), "enable": (0, 1), "optional": (0, 1)}
SEND_RATE_PER_MIN = 60


class SabError(RuntimeError):
    """SABnzbd call failed. ``code``: auth|http|timeout|unreachable|api|invalid."""

    def __init__(self, message: str, code: str = "api") -> None:
        super().__init__(redact(message))
        self.code = code


class SabClient:
    def __init__(self, url: str, api_key: str, *, timeout: float = 15.0) -> None:
        if not api_key:
            raise SabError("SABnzbd API-sleutel ontbreekt", "auth")
        self._api_key = api_key
        self._http = cb.http_client(url, timeout=timeout)

    def __repr__(self) -> str:  # never show the key
        return "SabClient(<sabnzbd>)"

    @classmethod
    def from_config(cls) -> SabClient:
        cfg = cb.load_config(CONNECTOR_ID)
        return cls(
            cfg.url, cfg.secrets.get("api_key", ""), timeout=float(cfg.options.get("timeout_s", 15) or 15)
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SabClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ transport
    def _call(
        self,
        mode: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        q: dict[str, Any] = {"mode": mode, "output": "json", "apikey": self._api_key}
        for k, v in (params or {}).items():
            if v is not None:
                q[k] = v
        try:
            if method == "POST":
                resp = self._http.post("/api", params=q, data=data, files=files)
            else:
                resp = self._http.get("/api", params=q)
        except httpx.TimeoutException:
            raise SabError("Time-out: SABnzbd reageert niet binnen de limiet", "timeout") from None
        except httpx.ConnectError:
            raise SabError("Verbinding met SABnzbd geweigerd of host onbereikbaar", "unreachable") from None
        except httpx.HTTPError as exc:
            raise SabError(f"HTTP-fout bij SABnzbd ({type(exc).__name__})", "http") from None
        if resp.status_code in (401, 403):
            raise SabError("SABnzbd weigert de API-sleutel", "auth")
        if resp.status_code >= 400:
            raise SabError(f"SABnzbd antwoordde HTTP {resp.status_code} op '{mode}'", "http")
        try:
            body = resp.json()
        except ValueError:
            text = resp.text.strip()
            if "api key" in text.lower():
                raise SabError("SABnzbd weigert de API-sleutel", "auth") from None
            raise SabError(f"SABnzbd gaf geen JSON terug op '{mode}'", "invalid") from None
        if isinstance(body, dict) and body.get("status") is False and body.get("error"):
            err = str(body["error"])
            if "api key" in err.lower() or "apikey" in err.lower():
                raise SabError("SABnzbd weigert de API-sleutel", "auth")
            raise SabError(f"SABnzbd-fout op '{mode}': {err}", "api")
        return body

    # ------------------------------------------------------------------ reads
    def version(self) -> str:
        return str(self._call("version").get("version", ""))

    def queue(self, *, start: int = 0, limit: int = 100) -> dict[str, Any]:
        return dict(self._call("queue", {"start": start, "limit": limit}).get("queue") or {})

    def history(
        self,
        *,
        start: int = 0,
        limit: int = 100,
        failed_only: bool = False,
        last_history_update: int | None = None,
    ) -> dict[str, Any] | None:
        """Returns the ``history`` object, or ``None`` when SAB reports nothing changed since
        ``last_history_update`` (conditional polling)."""
        body = self._call(
            "history",
            {
                "start": start,
                "limit": limit,
                "failed_only": 1 if failed_only else None,
                "last_history_update": last_history_update,
            },
        )
        hist = body.get("history")
        if hist is False or hist is None:
            return None
        return dict(hist)

    def server_stats(self) -> dict[str, Any]:
        return dict(self._call("server_stats"))

    def servers(self) -> list[dict[str, Any]]:
        """Server list from ``get_config&section=servers`` without credentials."""
        body = self._call("get_config", {"section": "servers"})
        servers = (body.get("config") or {}).get("servers") or []
        if isinstance(servers, dict):  # very old versions: {name: {...}}
            servers = [dict(v, name=k) for k, v in servers.items()]
        out = []
        for s in servers:
            out.append(
                {
                    "name": str(s.get("name", "")),
                    "displayname": str(s.get("displayname") or s.get("name", "")),
                    "host": str(s.get("host", "")),
                    "port": _int(s.get("port"), 0),
                    "ssl": bool(_int(s.get("ssl"), 0)),
                    "connections": _int(s.get("connections"), 0),
                    "priority": _int(s.get("priority"), 0),
                    "enable": _int(s.get("enable"), 1),
                    "optional": _int(s.get("optional"), 0),
                    "retention": _int(s.get("retention"), 0),
                    "username_set": bool(s.get("username")),
                }
            )
        return out

    def server_status(self) -> list[dict[str, Any]] | None:
        """Live per-server state from ``mode=status`` (SABnzbd ≥ 4; ``fullstatus`` on older versions):
        ``servername`` (display name), ``serveractive``, ``servererror`` (SABnzbd's own last warning for that
        server, e.g. "482 Account expired"), ``serveroptional``, connections. ``None`` when the version
        doesn't offer it."""
        body: Any = None
        for mode in ("status", "fullstatus"):
            try:
                body = self._call(mode, {"skip_dashboard": 1, "calculate_performance": 0})
            except SabError as exc:
                if exc.code in ("auth", "timeout", "unreachable"):
                    raise
                continue
            st = body.get("status") if isinstance(body, dict) else None
            if isinstance(st, dict) and isinstance(st.get("servers"), list):
                break
        else:
            return None
        out = []
        for s in st["servers"]:
            if not isinstance(s, dict):
                continue
            out.append(
                {
                    "servername": str(s.get("servername") or ""),
                    "active": bool(s.get("serveractive", True)),
                    "error": redact(str(s.get("servererror") or "")).strip()[:500],
                    "optional": bool(s.get("serveroptional", False)),
                    "priority": _int(s.get("serverpriority"), 0),
                    "total_conn": _int(s.get("servertotalconn"), 0),
                    "active_conn": _int(s.get("serveractiveconn"), 0),
                }
            )
        return out

    def warnings(self) -> list[dict[str, Any]]:
        """SABnzbd's own warning list (``mode=warnings``): ``[{text, type, time}]``; ``time`` is a unix
        timestamp or ``None`` (older versions return plain strings)."""
        body = self._call("warnings")
        raw = body.get("warnings") if isinstance(body, dict) else body
        out: list[dict[str, Any]] = []
        for w in raw or []:
            if isinstance(w, dict):
                ts = w.get("time")
                out.append(
                    {
                        "text": redact(str(w.get("text") or "")).strip()[:500],
                        "type": str(w.get("type") or "WARNING"),
                        "time": _int(ts, 0) or None,
                    }
                )
            elif isinstance(w, str):
                # "2024-01-01 12:00:00,123\nWARNING\nServer x reported: …"
                parts = w.split("\n")
                ts = None
                if len(parts) >= 3:
                    try:
                        ts = int(datetime.strptime(parts[0][:19], "%Y-%m-%d %H:%M:%S").timestamp())
                    except ValueError:
                        ts = None
                    out.append({"text": redact("\n".join(parts[2:])).strip()[:500], "type": parts[1], "time": ts})
                else:
                    out.append({"text": redact(w).strip()[:500], "type": "WARNING", "time": None})
        return out

    def categories(self) -> list[str]:
        cats = self._call("get_cats").get("categories") or []
        return [str(c) for c in cats if str(c) not in ("*", "")]

    # ------------------------------------------------------------------ writes (applier only)
    def set_server(self, name: str, **fields: int) -> None:
        for k, v in fields.items():
            _validate_server_field(k, v)
        if not any(s["name"] == name for s in self.servers()):
            raise SabError(f"SABnzbd-server '{name}' bestaat niet", "invalid")
        self._call(
            "set_config", {"section": "servers", "keyword": name, **{k: int(v) for k, v in fields.items()}}
        )

    def retry(self, nzo_id: str) -> str:
        """Retry a failed job from the history (``mode=retry&value=<nzo_id>``). Returns the nzo_id of the job
        that is back in the queue (SABnzbd may assign a new one). Only used via the ``sabnzbd_retry`` applier."""
        if not nzo_id or len(nzo_id) > 200:
            raise SabError("ongeldig nzo_id", "invalid")
        body = self._call("retry", {"value": nzo_id})
        if not isinstance(body, dict) or body.get("status") is False:
            raise SabError("SABnzbd kon de download niet opnieuw proberen", "api")
        new = body.get("nzo_id") or (body.get("nzo_ids") or [None])[0]
        return str(new or nzo_id)

    # ------------------------------------------------------------------ direct user actions
    def add_url(
        self,
        url: str,
        *,
        category: str,
        name: str | None = None,
        priority: int | None = None,
        actor: str = "system",
        release_key: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """Send an NZB URL to SABnzbd. Idempotent per URL for 24 h (returns the earlier nzo_id)."""
        from mso.features.usenet import nzbs  # lazy: feature module owns the send history

        url_hash = hashlib.sha256(url.encode()).hexdigest()
        if existing := nzbs.recent_send(sha256=url_hash):
            return existing
        _rate_limit()
        body = self._call(
            "addurl",
            {"cat": category, "nzbname": name, "priority": priority},
            method="POST",
            data={"name": url},
        )
        nzo_id = _nzo_id(body)
        nzbs.record_send(
            nzo_id=nzo_id,
            origin="send_url",
            title=name or redact(url)[:200],
            category=category,
            sha256=url_hash,
            url_redacted=redact(url),
            release_key=release_key,
            source_id=source_id,
            actor=actor,
        )
        audit(
            actor,
            "sabnzbd.add_url",
            f"NZB-link naar SABnzbd gestuurd ({category})",
            target=CONNECTOR_ID,
            detail={"nzo_id": nzo_id, "category": category, "name": name},
        )
        return nzo_id

    def add_nzb(
        self,
        data: bytes,
        filename: str,
        *,
        category: str,
        actor: str = "system",
        release_key: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """Upload NZB content to SABnzbd. Idempotent per NZB content for 24 h. The message-ids are kept
        (bounded sample) so MSO can later test article availability for exactly this job."""
        from mso.features.usenet import nzbs

        digest = hashlib.sha256(data).hexdigest()
        if existing := nzbs.recent_send(sha256=digest):
            return existing
        parsed = nzbs.parse_nzb(data)  # validates before sending
        _rate_limit()
        safe_name = filename if filename.lower().endswith(".nzb") else f"{filename}.nzb"
        body = self._call(
            "addfile",
            {"cat": category, "nzbname": None},
            method="POST",
            files={"name": (safe_name, data, "application/x-nzb")},
        )
        nzo_id = _nzo_id(body)
        nzbs.record_send(
            nzo_id=nzo_id,
            origin="send_nzb",
            title=safe_name[:-4],
            category=category,
            sha256=digest,
            parsed=parsed,
            release_key=release_key,
            source_id=source_id,
            actor=actor,
        )
        audit(
            actor,
            "sabnzbd.add_nzb",
            f"NZB naar SABnzbd gestuurd ({category})",
            target=CONNECTOR_ID,
            detail={"nzo_id": nzo_id, "category": category, "file": safe_name},
        )
        return nzo_id


def _rate_limit() -> None:
    ok, wait = limiter.hit("sab-send", "global", SEND_RATE_PER_MIN, 60)
    if not ok:
        raise SabError(f"Te veel verzendingen naar SABnzbd; wacht {int(wait) + 1} s", "rate_limited")


def _nzo_id(body: dict[str, Any]) -> str:
    ids = body.get("nzo_ids") or []
    if not body.get("status") or not ids:
        raise SabError("SABnzbd heeft de NZB niet geaccepteerd", "api")
    return str(ids[0])


def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _validate_server_field(field: str, value: Any) -> None:
    if field not in SERVER_FIELDS:
        raise ValueError(f"veld '{field}' mag niet worden gewijzigd (alleen priority/enable/optional)")
    lo, hi = SERVER_FIELDS[field]
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{field} moet een geheel getal {lo}–{hi} zijn")


# ------------------------------------------------------------------ change-set applier
def _split_path(path: str) -> tuple[str, str]:
    """``servers/<name>/<field>``"""
    if not path.startswith("servers/") or path.count("/") < 2:
        raise ValueError(f"ongeldig pad '{path}'")
    name, field = path[len("servers/") :].rsplit("/", 1)
    if not name:
        raise ValueError("servernaam ontbreekt")
    return name, field


def _applier() -> changes.Applier:
    client = SabClient.from_config()

    def read(path: str) -> Any:
        name, field = _split_path(path)
        for s in client.servers():
            if s["name"] == name:
                return s[field]
        raise SabError(f"SABnzbd-server '{name}' niet gevonden", "invalid")

    def write(path: str, value: Any) -> None:
        name, field = _split_path(path)
        client.set_server(name, **{field: value})

    def validate(path: str, value: Any) -> None:
        _, field = _split_path(path)
        _validate_server_field(field, value)

    return changes.Applier(read=read, write=write, validate=validate)


changes.register_applier(CONNECTOR_ID, _applier)

# ------------------------------------------------------------------ retry applier (failed downloads)
RETRY_TARGET = "sabnzbd_retry"
RETRY_BEFORE, RETRY_AFTER = "mislukt", "opnieuw in wachtrij"


def _retry_applier() -> changes.Applier:
    """``retry/<nzo_id>``: ``mislukt`` → ``opnieuw in wachtrij``. Write = ``mode=retry``; read-back looks the job up
    in SABnzbd's queue (also under the new nzo_id SABnzbd reported). The op is ``one_way``: a retried download
    cannot be "un-retried" (MSO never deletes downloads)."""
    from mso.features.usenet import retry as rt  # lazy: the feature module keeps the retry history

    client = SabClient.from_config()

    def read(path: str) -> Any:
        return rt.read_state(client, rt.parse_path(path))

    def write(path: str, value: Any) -> None:
        if value != RETRY_AFTER:
            raise SabError("een opnieuw geprobeerde download kan niet worden teruggezet", "invalid")
        rt.do_retry(client, rt.parse_path(path))

    def validate(path: str, value: Any) -> None:
        rt.parse_path(path)
        if value not in (RETRY_BEFORE, RETRY_AFTER):
            raise ValueError("waarde moet 'opnieuw in wachtrij' zijn")

    return changes.Applier(read=read, write=write, validate=validate)


changes.register_applier(RETRY_TARGET, _retry_applier)


# ------------------------------------------------------------------ connector test
def _test(cfg: cb.ConnectorConfig) -> cb.TestResult:
    start = time.monotonic()
    try:
        client = SabClient(cfg.url, cfg.secrets.get("api_key", ""), timeout=10)
    except SabError as exc:
        return cb.TestResult(False, "auth_failed", str(exc))
    with client:
        try:
            version = client.version()
        except SabError as exc:
            status = {"auth": "auth_failed"}.get(exc.code, "offline")
            return cb.TestResult(False, status, str(exc))
        modes: dict[str, bool] = {"version": True}
        info: dict[str, Any] = {}
        for mode, fn in (
            ("queue", lambda: client.queue(limit=1)),
            ("history", lambda: client.history(limit=1)),
            ("server_stats", client.server_stats),
            ("get_config", client.servers),
            ("get_cats", client.categories),
        ):
            try:
                res = fn()
                modes[mode] = True
                if mode == "get_config":
                    info["servers"] = len(res)
                    info["servers_enabled"] = sum(1 for s in res if s["enable"])
                elif mode == "get_cats":
                    info["categories"] = len(res)
            except SabError as exc:
                if exc.code == "auth":
                    return cb.TestResult(False, "auth_failed", str(exc), version=version)
                modes[mode] = False
        # SABnzbd's own per-server errors (shown, not a connector failure). Same evaluation as the
        # Usenet-providers page: live ``servererror`` from mode=status AND SABnzbd's warning list. (1.3.1 only
        # counted ``servererror``; SABnzbd clears that after its retry pause while the warnings still report
        # "502 Authentication Failed"/"482 Account expired", so the test showed no failing servers.)
        failing = _failing_servers(client, modes)
        if failing is not None:
            info["servers_failing"] = len(failing)
            info["failing_servers"] = failing
        latency = int((time.monotonic() - start) * 1000)
    failed = [m for m, ok in modes.items() if not ok]
    caps = {"modes": modes, **info}
    failing_note = (
        f" · {info['servers_failing']} server(s) melden fouten (zie Usenet-providers)"
        if info.get("servers_failing")
        else ""
    )
    if failed:
        return cb.TestResult(
            True,
            "degraded",
            f"SABnzbd {version} bereikbaar; niet beschikbaar: {', '.join(failed)}" + failing_note,
            version=version,
            capabilities=caps,
            latency_ms=latency,
        )
    return cb.TestResult(
        True,
        "healthy",
        f"SABnzbd {version} · {info.get('servers', 0)} server(s), {info.get('categories', 0)} categorieën"
        + failing_note,
        version=version,
        capabilities=caps,
        latency_ms=latency,
    )


def _failing_servers(client: SabClient, modes: dict[str, bool]) -> list[str] | None:
    """Enabled servers SABnzbd currently reports as failing (status + warnings), or ``None`` when the feature
    module is disabled / SABnzbd offers neither source."""
    try:
        from mso.features.usenet.serverstatus import FAILING, evaluate
    except ImportError:  # feature module disabled
        return None
    try:
        servers = client.servers()
    except SabError:
        return None
    try:
        live = client.server_status()
        if live is not None:
            modes["status"] = True
    except SabError:
        live = None
        modes["status"] = False
    try:
        warns = client.warnings()
    except SabError:
        warns = []
    if live is None and not warns:
        return None
    states = evaluate(servers, live, warns)
    return sorted(st["displayname"] for st in states if st["enabled"] and st["status"] in FAILING)


cb.register(
    cb.ConnectorSpec(
        id=CONNECTOR_ID,
        kind="usenet_client",
        label="SABnzbd",
        secret_fields=[cb.SecretField("api_key", "API-sleutel")],
        test=_test,
        description=(
            "Usenet-downloadclient: wachtrij, geschiedenis, serverstatistieken en serverprioriteiten."
        ),
    )
)
