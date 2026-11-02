"""Cross-cutting security: secret redaction in logs, SSRF guard for outbound URLs, rate limiting."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from mso.config import get_settings

# ------------------------------------------------------------------ redaction
_REDACT_PATTERNS = [
    re.compile(r"(?i)\b(apikey|api_key|x-api-key|token|password|passwd|pass|secret|authorization|cookie|sid|"
                r"nzbkey|passkey|session)(\"?\s*[=:]\s*\"?)([^\s&\"',;]+)"),
    re.compile(r"(?i)(https?://)([^/\s:@]+):([^/\s@]+)@"),  # credentials in URLs
    re.compile(r"(?i)\b(bearer\s+)([a-z0-9._\-]+)"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = _REDACT_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    out = _REDACT_PATTERNS[1].sub(lambda m: f"{m.group(1)}***:***@", out)
    out = _REDACT_PATTERNS[2].sub(lambda m: f"{m.group(1)}***", out)
    return out


class RedactingFilter(logging.Filter):
    """Applied to every handler: no API key, password or passkey ever reaches a log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.handlers = [handler]
    root.setLevel(level)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ------------------------------------------------------------------ SSRF guard
class UnsafeTarget(ValueError):
    """Outbound URL refused (reason in message, Dutch)."""

    def __init__(self, message: str, code: str = "ssrf_blocked") -> None:
        super().__init__(message)
        self.code = code


# Never reachable: cloud metadata/link-local, multicast, unspecified, broadcast.
_ALWAYS_BLOCKED = [ipaddress.ip_network(n) for n in (
    "169.254.0.0/16", "fe80::/10", "0.0.0.0/8", "224.0.0.0/4", "ff00::/8", "255.255.255.255/32",
    "100.100.100.200/32", "fd00:ec2::254/128",
)]
_LOOPBACK = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]


def check_url(url: str, *, purpose: str = "connector", resolve: bool = True) -> str:
    """Validate an outbound URL. LAN (RFC1918) targets are allowed — the app talks to LAN services —
    but metadata/link-local, non-HTTP(S) schemes, embedded credentials and (by default) loopback are not.
    Returns the normalised URL (no trailing slash)."""
    settings = get_settings()
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise UnsafeTarget(f"Ongeldige URL ({purpose})", "invalid_url") from exc
    if parts.scheme not in {"http", "https"}:
        raise UnsafeTarget("Alleen http:// en https:// zijn toegestaan", "invalid_scheme")
    if not parts.hostname:
        raise UnsafeTarget("URL mist een hostnaam", "invalid_url")
    if parts.username or parts.password:
        raise UnsafeTarget("Inloggegevens horen niet in de URL; gebruik de geheimvelden", "credentials_in_url")
    host = parts.hostname
    if host.lower() in {h.lower() for h in settings.ssrf_extra_blocked}:
        raise UnsafeTarget(f"Host {host} is geblokkeerd door MSO_SSRF_BLOCK")
    addresses: list[str] = []
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if resolve:
            try:
                infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                           proto=socket.IPPROTO_TCP)
                addresses = sorted({str(info[4][0]) for info in infos})
            except socket.gaierror as exc:
                raise UnsafeTarget(f"Host {host} is niet te resolven", "dns_failure") from exc
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if any(ip in net for net in _ALWAYS_BLOCKED):
            raise UnsafeTarget(f"Adres {ip} (link-local/metadata/multicast) is nooit toegestaan")
        if any(ip in net for net in _LOOPBACK) and not settings.allow_loopback_targets:
            raise UnsafeTarget(
                "Loopback-adressen zijn uitgeschakeld; gebruik de LAN- of containernaam van de dienst",
                "loopback_blocked")
    return url.strip().rstrip("/")


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    """Sliding-window limiter per (bucket, key). In-process; one API process per stack."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window_s: float) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return False, max(0.0, q[0] + window_s - now)
            q.append(now)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
