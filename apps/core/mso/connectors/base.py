"""Connector framework: registry of external systems (Prowlarr, SABnzbd, qBittorrent, MediaGuard, …).

Each connector module calls ``register(ConnectorSpec(...))``. Config (URL, enabled, options) lives in
the ``connectors`` table; credentials live only in the vault under ``connector.<id>.<field>``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from mso.connectors import monitor
from mso.db import get_db, jsonb
from mso.security import UnsafeTarget, check_url, redact
from mso.vault import get_vault

LOGGER = logging.getLogger(__name__)
USER_AGENT = "MediaSourceOptimizer/1.0 (+self-hosted)"


@dataclass
class SecretField:
    name: str
    label: str
    required: bool = True


@dataclass
class TestResult:
    ok: bool
    status: str  # healthy|degraded|offline|auth_failed|unknown
    message: str
    version: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "status": self.status, "message": self.message, "version": self.version,
                "capabilities": self.capabilities, "latency_ms": self.latency_ms}


@dataclass
class ConnectorConfig:
    id: str
    url: str
    secrets: dict[str, str]
    options: dict[str, Any]
    enabled: bool = True


@dataclass
class ConnectorSpec:
    id: str
    kind: str  # indexer_manager|usenet_client|torrent_client|mediaguard
    label: str
    secret_fields: list[SecretField]
    test: Callable[[ConnectorConfig], TestResult]
    optional: bool = False
    description: str = ""
    default_url: str = ""


SPECS: dict[str, ConnectorSpec] = {}


def register(spec: ConnectorSpec) -> None:
    SPECS[spec.id] = spec


class ConnectorNotConfigured(RuntimeError):
    def __init__(self, cid: str) -> None:
        super().__init__(f"Koppeling '{cid}' is niet ingesteld of uitgeschakeld")
        self.cid = cid


def secret_name(cid: str, fieldname: str) -> str:
    return f"connector.{cid}.{fieldname}"


def ensure_rows() -> None:
    db = get_db()
    for spec in SPECS.values():
        db.execute("INSERT INTO connectors(id,kind,label,url) VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                   (spec.id, spec.kind, spec.label, spec.default_url or None))


def load_config(cid: str, *, require_enabled: bool = True) -> ConnectorConfig:
    monitor.note_lookup(cid)
    row = get_db().fetchone("SELECT * FROM connectors WHERE id=%s", (cid,))
    spec = SPECS.get(cid)
    if row is None or spec is None or not row["url"] or (require_enabled and not row["enabled"]):
        raise ConnectorNotConfigured(cid)
    _remember_url(cid, row["url"])
    vault = get_vault()
    secrets = {f.name: v for f in spec.secret_fields if (v := vault.get(secret_name(cid, f.name)))}
    return ConnectorConfig(id=cid, url=row["url"], secrets=secrets, options=row["options"] or {},
                           enabled=row["enabled"])


def is_configured(cid: str) -> bool:
    try:
        load_config(cid)
        return True
    except ConnectorNotConfigured:
        return False


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    spec = SPECS.get(row["id"])
    vault = get_vault()
    fields = []
    if spec:
        for f in spec.secret_fields:
            fields.append({"name": f.name, "label": f.label, "required": f.required,
                           "status": vault.status(secret_name(row["id"], f.name))})
    return {
        "id": row["id"], "kind": row["kind"], "label": row["label"], "url": row["url"],
        "enabled": row["enabled"], "options": row["options"], "status": row["status"], "version": row["version"],
        "capabilities": row["capabilities"], "last_checked_at": row["last_checked_at"],
        "last_error": row["last_error"], "secret_fields": fields,
        "status_since": row.get("status_since"), "last_ok_at": row.get("last_ok_at"),
        "failure_kind": row.get("failure_kind"),
        "optional": spec.optional if spec else True, "description": spec.description if spec else "",
    }


def record_test(cid: str, result: TestResult) -> None:
    kind = None if result.ok else ("auth" if result.status == "auth_failed" else
                                   "timeout" if "time-out" in (result.message or "").lower() else
                                   "offline" if result.status == "offline" else None)
    prev = get_db().scalar("SELECT status FROM connectors WHERE id=%s", (cid,))
    get_db().execute(
        "UPDATE connectors SET status=%s, version=COALESCE(%s, version), capabilities=%s, last_checked_at=now(), "
        "last_error=%s, failure_kind=%s, "
        "last_ok_at=CASE WHEN %s THEN now() ELSE last_ok_at END, "
        "status_since=CASE WHEN status IS DISTINCT FROM %s OR status_since IS NULL THEN now() ELSE status_since END "
        "WHERE id=%s",
        (result.status, result.version, jsonb(result.capabilities), None if result.ok else redact(result.message),
         kind, result.ok, result.status, cid))
    if prev in monitor.DOWN or result.status in monitor.DOWN:
        try:
            monitor.sync_alert(cid)
        except Exception as exc:  # never let the alert bookkeeping break a test
            LOGGER.warning("melding voor %s niet bijgewerkt: %s", cid, redact(str(exc)))


def run_test(spec: ConnectorSpec, cfg: ConnectorConfig) -> TestResult:
    try:
        cfg.url = check_url(cfg.url, purpose=spec.label)
    except ValueError as exc:
        return TestResult(False, "offline", str(exc))
    start = time.monotonic()
    try:
        res = spec.test(cfg)
    except httpx.TimeoutException:
        res = TestResult(False, "offline", "Time-out: dienst reageert niet binnen de limiet")
    except httpx.ConnectError:
        res = TestResult(False, "offline", "Verbinding geweigerd of host onbereikbaar")
    except httpx.HTTPError as exc:
        res = TestResult(False, "offline", redact(f"HTTP-fout: {exc}"))
    except Exception as exc:
        res = TestResult(False, "unknown", redact(f"{type(exc).__name__}: {exc}"))
    if res.latency_ms is None:
        res.latency_ms = int((time.monotonic() - start) * 1000)
    res.message = redact(res.message)
    return res


# ------------------------------------------------------------------ background monitoring hooks (see monitor.py)
_URLS: dict[str, str] = {}  # normalised base URL -> connector id (filled by load_config)
_URLS_LOCK = threading.Lock()


def _norm_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip().rstrip("/").lower()
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}"


def _remember_url(cid: str, url: str) -> None:
    key = _norm_url(url)
    if _URLS.get(key) != cid:
        with _URLS_LOCK:
            for k in [k for k, v in _URLS.items() if v == cid]:
                del _URLS[k]  # the connector moved to another URL
            _URLS[key] = cid


def connector_for_url(url: str, *, prefix: bool = False) -> str | None:
    """Connector id whose configured base URL is ``url`` (or, with ``prefix``, a prefix of it)."""
    key = _norm_url(url)
    cid = _URLS.get(key)
    if cid is not None or not prefix:
        return cid
    for base, c in list(_URLS.items()):
        if key == base or key.startswith(base + "/"):
            return c
    return None


class MonitoredClient(httpx.Client):
    """``httpx.Client`` that reports every response and transport error of a monitored connector to the
    background-monitoring scope of the running job (no effect outside a job)."""

    def __init__(self, cid: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.mso_connector = cid

    def send(self, request: httpx.Request, **kw: Any) -> httpx.Response:  # type: ignore[override]
        try:
            response = super().send(request, **kw)
        except httpx.TransportError as exc:
            monitor.note_transport_error(self.mso_connector, exc)
            raise
        monitor.note_response(self.mso_connector, request, response)
        return response


def http_client(base_url: str, *, timeout: float = 15.0, headers: dict[str, str] | None = None,
                follow_redirects: bool = False, **kw: Any) -> httpx.Client:
    """Outbound HTTP client for LAN services: SSRF-checked base URL, sane timeouts, no redirects by default.

    For SABnzbd / qBittorrent / Prowlarr (looked up by base URL) the client also feeds background monitoring."""
    cid = connector_for_url(base_url)
    monitored = cid if cid in monitor.MONITORED else None
    try:
        base = check_url(base_url)
    except UnsafeTarget as exc:
        if monitored and exc.code == "dns_failure":
            monitor.note_error(monitored, "offline", redact(str(exc)))
        raise
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    opts: dict[str, Any] = {"base_url": base, "timeout": httpx.Timeout(timeout, connect=5.0), "headers": h,
                            "follow_redirects": follow_redirects, **kw}
    if monitored:
        return MonitoredClient(monitored, **opts)
    return httpx.Client(**opts)
