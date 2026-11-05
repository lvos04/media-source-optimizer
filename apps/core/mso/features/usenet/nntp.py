"""Small, safe NNTP client (RFC 3977/4643) over ``socket``/``ssl`` — ``nntplib`` was removed in Python 3.13.

Supported: greeting, CAPABILITIES, AUTHINFO USER/PASS, GROUP, STAT, HEAD, OVER/XOVER, QUIT.
Safety: connect/read timeouts on every operation, bounded line length and multi-line response size,
TLS with certificate verification by default, and error texts that never contain host names or credentials
(callers log provider slugs only).
"""

from __future__ import annotations

import re
import socket
import ssl
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

MAX_LINE = 16 * 1024
DEFAULT_MAX_LONG_BYTES = 64 * 1024 * 1024

# Provider status values (shared with the providers module and UI)
STATUSES = (
    "ok",
    "auth_failed",
    "account_expired",
    "rate_limited",
    "block_exhausted",
    "unreachable",
    "tls_error",
    "timeout",
    "error",
    "unknown",
    "missing_credentials",  # no username/password in MSO: never tested, never "auth_failed"
)

_EXPIRED = re.compile(r"expir|verlopen|inactive account|account (is )?(suspended|disabled)", re.I)
_BLOCK = re.compile(
    r"\bblock\b.{0,30}(exhaust|used|empty|depleted|0 ?(b|bytes|gb))|quota|exhaust|"
    r"(download|transfer|data) limit|limit (reached|exceeded)|exceeded|"
    r"no (more )?(data|credits?|bytes)( left| remaining)?|out of (data|credit)",
    re.I,
)
_TOO_MANY = re.compile(
    r"too many|connection limit|max(imum)? (number of )?connections|"
    r"connections? (limit|exceeded)|slow down|rate limit",
    re.I,
)


class NNTPError(Exception):
    """``status`` is one of STATUSES; ``code`` the NNTP response code if any. Message is safe to show."""

    def __init__(self, status: str, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def classify_response(code: int, text: str, phase: str) -> str:
    """Map a negative NNTP response to a provider status. ``phase``: greeting|auth|command."""
    if _EXPIRED.search(text):
        return "account_expired"
    if _TOO_MANY.search(text):
        return "rate_limited"
    if _BLOCK.search(text):
        return "block_exhausted"
    if code in (481, 482):
        return "auth_failed"
    if code == 502:
        return "auth_failed" if phase in ("greeting", "auth") else "error"
    if code == 480:
        return "auth_failed"
    if code == 400:
        return "rate_limited" if phase == "greeting" and "unavailable" not in text.lower() else "error"
    return "error"


_STATUS_TEXT = {
    "account_expired": "Account verlopen volgens provider",
    "block_exhausted": "Blokaccount op of datalimiet bereikt volgens provider",
    "rate_limited": "Te veel verbindingen of tijdelijk beperkt door provider",
    "auth_failed": "Inloggen geweigerd (gebruikersnaam/wachtwoord of toegang)",
    "error": "Onverwacht antwoord van provider",
}


def _safe_text(text: str) -> str:
    # provider texts can echo usernames or hosts; keep only a short, redacted gist
    from mso.security import redact

    text = re.sub(r"[\w.-]+\.(com|net|nl|org|eu|de|info|io|biz)\b", "<host>", text, flags=re.I)
    return redact(text)[:160]


@dataclass
class OverEntry:
    number: int
    subject: str
    date: str
    message_id: str
    bytes: int
    lines: int


class NNTPClient:
    def __init__(
        self, host: str, port: int, *, use_ssl: bool = True, timeout: float = 15.0, verify_tls: bool = True
    ) -> None:
        self._host = host
        self._port = port
        self._ssl = use_ssl
        self._timeout = timeout
        self._verify = verify_tls
        self._sock: socket.socket | None = None
        self._file: Any = None
        self.bytes_received = 0
        self.bytes_sent = 0
        self.commands_sent = 0
        self.greeting: tuple[int, str] | None = None
        self.posting_allowed = False

    def __repr__(self) -> str:
        return "NNTPClient(<provider>)"

    # ------------------------------------------------------------------ connection
    def connect(self) -> tuple[int, str]:
        try:
            raw = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except TimeoutError:
            raise NNTPError("timeout", "Time-out bij verbinden met provider") from None
        except socket.gaierror:
            raise NNTPError("unreachable", "Hostnaam van provider niet te resolven") from None
        except OSError:
            raise NNTPError(
                "unreachable", "Provider onbereikbaar (verbinding geweigerd of netwerkfout)"
            ) from None
        try:
            if self._ssl:
                ctx = ssl.create_default_context()
                if not self._verify:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                raw = ctx.wrap_socket(raw, server_hostname=self._host)
        except ssl.SSLCertVerificationError:
            raw.close()
            raise NNTPError("tls_error", "TLS-certificaat van provider niet geldig voor deze host") from None
        except (ssl.SSLError, OSError):
            raw.close()
            raise NNTPError(
                "tls_error", "TLS-handshake met provider mislukt (verkeerde poort of SSL-instelling?)"
            ) from None
        raw.settimeout(self._timeout)
        self._sock = raw
        self._file = raw.makefile("rb")
        code, text = self._getresp(phase="greeting", check=False)
        self.greeting = (code, text)
        if code not in (200, 201):
            self.close()
            status = classify_response(code, text, "greeting")
            raise NNTPError(
                status,
                f"{_STATUS_TEXT.get(status, 'Provider weigert verbinding')} ({code} {_safe_text(text)})",
                code,
            )
        self.posting_allowed = code == 200
        return code, text

    def close(self) -> None:
        for obj in (self._file, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._file = None
        self._sock = None

    def __enter__(self) -> NNTPClient:
        if self._sock is None:
            self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.quit()

    # ------------------------------------------------------------------ io
    def _readline(self) -> bytes:
        if self._file is None:
            raise NNTPError("error", "Geen verbinding")
        try:
            line = self._file.readline(MAX_LINE + 1)
        except TimeoutError:
            self.close()
            raise NNTPError("timeout", "Time-out: provider antwoordt niet binnen de limiet") from None
        except (OSError, ssl.SSLError):
            self.close()
            raise NNTPError("unreachable", "Verbinding met provider verbroken") from None
        if not line:
            self.close()
            raise NNTPError("unreachable", "Provider heeft de verbinding gesloten")
        if len(line) > MAX_LINE:
            self.close()
            raise NNTPError("error", "Antwoordregel van provider te lang")
        self.bytes_received += len(line)
        return line.rstrip(b"\r\n")

    def _send(self, line: str) -> None:
        if self._sock is None:
            raise NNTPError("error", "Geen verbinding")
        if "\r" in line or "\n" in line:
            raise NNTPError("error", "Ongeldig NNTP-commando")
        data = line.encode("utf-8") + b"\r\n"
        try:
            self._sock.sendall(data)
            self.bytes_sent += len(data)
            self.commands_sent += 1
        except TimeoutError:
            self.close()
            raise NNTPError("timeout", "Time-out bij verzenden naar provider") from None
        except OSError:
            self.close()
            raise NNTPError("unreachable", "Verbinding met provider verbroken") from None

    def _getresp(self, *, phase: str = "command", check: bool = True) -> tuple[int, str]:
        raw = self._readline()
        text = _decode(raw)
        try:
            code = int(text[:3])
        except ValueError:
            self.close()
            raise NNTPError("error", "Ongeldig antwoord van provider (geen NNTP?)") from None
        rest = text[4:] if len(text) > 3 else ""
        if check and code >= 400:
            status = classify_response(code, rest, phase)
            raise NNTPError(status, f"{_STATUS_TEXT.get(status, 'Fout')} ({code} {_safe_text(rest)})", code)
        return code, rest

    def _getlong(self, max_lines: int = 1_000_000, max_bytes: int = DEFAULT_MAX_LONG_BYTES) -> Iterator[str]:
        start = self.bytes_received
        n = 0
        while True:
            line = self._readline()
            if line == b".":
                return
            if line.startswith(b".."):
                line = line[1:]
            n += 1
            if n > max_lines or self.bytes_received - start > max_bytes:
                self.close()  # can't resynchronise mid-response: drop the connection
                raise NNTPError("error", "Antwoord van provider overschrijdt de ingestelde limiet")
            yield _decode(line)

    def _cmd(self, line: str, *, phase: str = "command") -> tuple[int, str]:
        self._send(line)
        return self._getresp(phase=phase)

    # ------------------------------------------------------------------ commands
    def capabilities(self) -> list[str]:
        self._send("CAPABILITIES")
        code, _ = self._getresp(check=False)
        if code != 101:
            return []
        return [c for c in self._getlong(max_lines=500, max_bytes=64 * 1024)]

    def login(self, username: str, password: str) -> None:
        if not username:
            raise NNTPError("auth_failed", "Gebruikersnaam ontbreekt")
        self._send(f"AUTHINFO USER {username}")
        code, rest = self._getresp(phase="auth")
        if code == 281:
            return
        if code != 381:
            raise NNTPError("auth_failed", f"Onverwacht antwoord op AUTHINFO USER ({code})", code)
        self._send(f"AUTHINFO PASS {password}")
        code, rest = self._getresp(phase="auth")
        if code != 281:
            raise NNTPError("auth_failed", f"Inloggen niet bevestigd ({code})", code)

    def group(self, name: str) -> tuple[int, int, int, str]:
        if not re.fullmatch(r"[A-Za-z0-9.+_-]{1,200}", name):
            raise NNTPError("error", "Ongeldige groepsnaam")
        _, rest = self._cmd(f"GROUP {name}")
        parts = rest.split()
        try:
            return int(parts[0]), int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else name
        except (IndexError, ValueError):
            raise NNTPError("error", "Ongeldig GROUP-antwoord") from None

    @staticmethod
    def _mid(message_id: str) -> str:
        mid = message_id.strip().strip("<>")
        if not mid or "@" not in mid or len(mid) > 250 or any(c.isspace() or c in "<>" for c in mid):
            raise NNTPError("error", "Ongeldig message-id")
        return f"<{mid}>"

    def stat(self, message_id: str) -> bool:
        """True = article exists (223), False = no such article (430). Other answers raise."""
        self._send(f"STAT {self._mid(message_id)}")
        code, rest = self._getresp(check=False)
        if code == 223:
            return True
        if code in (430, 423):
            return False
        status = classify_response(code, rest, "command")
        raise NNTPError(status, f"{_STATUS_TEXT.get(status, 'Fout')} ({code} {_safe_text(rest)})", code)

    def head(self, message_id: str) -> list[str] | None:
        self._send(f"HEAD {self._mid(message_id)}")
        code, rest = self._getresp(check=False)
        if code in (430, 423):
            return None
        if code != 221:
            status = classify_response(code, rest, "command")
            raise NNTPError(status, f"{_STATUS_TEXT.get(status, 'Fout')} ({code} {_safe_text(rest)})", code)
        return list(self._getlong(max_lines=2000, max_bytes=256 * 1024))

    def over(self, first: int, last: int, *, max_bytes: int = DEFAULT_MAX_LONG_BYTES) -> list[OverEntry]:
        """OVER (RFC 3977), falling back to XOVER for older servers. Range is inclusive."""
        if first > last or first < 0:
            return []
        self._send(f"OVER {first}-{last}")
        code, rest = self._getresp(check=False)
        if code in (500, 501):
            self._send(f"XOVER {first}-{last}")
            code, rest = self._getresp(check=False)
        if code == 423:  # no articles in that range
            return []
        if code != 224:
            status = classify_response(code, rest, "command")
            raise NNTPError(status, f"{_STATUS_TEXT.get(status, 'Fout')} ({code} {_safe_text(rest)})", code)
        out: list[OverEntry] = []
        for line in self._getlong(max_lines=last - first + 1 + 10, max_bytes=max_bytes):
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            try:
                out.append(
                    OverEntry(
                        number=int(parts[0]),
                        subject=parts[1][:500],
                        date=parts[3],
                        message_id=parts[4].strip().strip("<>"),
                        bytes=_toint(parts[6]),
                        lines=_toint(parts[7]),
                    )
                )
            except ValueError:
                continue
        return out

    def quit(self) -> None:
        if self._sock is None:
            return
        try:
            self._send("QUIT")
            self._getresp(check=False)
        except NNTPError:
            pass
        finally:
            self.close()


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _toint(v: str) -> int:
    try:
        return int(v.strip() or 0)
    except ValueError:
        return 0


def check_login(
    host: str, port: int, *, use_ssl: bool, username: str, password: str, timeout: float = 15.0
) -> dict[str, Any]:
    """Connect, read capabilities, authenticate, quit. Returns {status, message, latency_ms, capabilities}."""
    start = time.monotonic()
    client = NNTPClient(host, port, use_ssl=use_ssl, timeout=timeout)
    try:
        client.connect()
        caps = client.capabilities()
        client.login(username, password)
        # some servers only reveal limits after auth; a second CAPABILITIES is cheap
        caps_after = client.capabilities() or caps
        client.quit()
        return {
            "status": "ok",
            "message": "Inloggen gelukt",
            "capabilities": caps_after[:40],
            "latency_ms": int((time.monotonic() - start) * 1000),
            "code": 281,
        }
    except NNTPError as exc:
        client.close()
        return {
            "status": exc.status,
            "message": str(exc),
            "capabilities": [],
            "latency_ms": int((time.monotonic() - start) * 1000),
            "code": exc.code,
        }
