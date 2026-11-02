"""Connector framework: registry of external systems (Prowlarr, SABnzbd, qBittorrent, MediaGuard, …).

Each connector module calls ``register(ConnectorSpec(...))``. Config (URL, enabled, options) lives in
the ``connectors`` table; credentials live only in the vault under ``connector.<id>.<field>``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from mso.db import get_db, jsonb
from mso.security import check_url, redact
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
    row = get_db().fetchone("SELECT * FROM connectors WHERE id=%s", (cid,))
    spec = SPECS.get(cid)
    if row is None or spec is None or not row["url"] or (require_enabled and not row["enabled"]):
        raise ConnectorNotConfigured(cid)
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
        "optional": spec.optional if spec else True, "description": spec.description if spec else "",
    }


def record_test(cid: str, result: TestResult) -> None:
    get_db().execute(
        "UPDATE connectors SET status=%s, version=COALESCE(%s, version), capabilities=%s, last_checked_at=now(), "
        "last_error=%s WHERE id=%s",
        (result.status, result.version, jsonb(result.capabilities), None if result.ok else redact(result.message),
         cid))


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
    except Exception as exc:  # noqa: BLE001
        res = TestResult(False, "unknown", redact(f"{type(exc).__name__}: {exc}"))
    if res.latency_ms is None:
        res.latency_ms = int((time.monotonic() - start) * 1000)
    res.message = redact(res.message)
    return res


def http_client(base_url: str, *, timeout: float = 15.0, headers: dict[str, str] | None = None,
                follow_redirects: bool = False, **kw: Any) -> httpx.Client:
    """Outbound HTTP client for LAN services: SSRF-checked base URL, sane timeouts, no redirects by default."""
    base = check_url(base_url)
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    return httpx.Client(base_url=base, timeout=httpx.Timeout(timeout, connect=5.0), headers=h,
                        follow_redirects=follow_redirects, **kw)
