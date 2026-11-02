"""Credential-safe handling of upstream download links.

Upstream links (Prowlarr proxy links, indexer ``t=get`` links, DOGnzb-style ``/fetch/<guid>/<key>``) embed
API keys. MSO never returns them to a browser or Newznab consumer: results are referenced by an opaque
token; the stored link has every known secret replaced by a placeholder that is re-filled from the vault
only at download time (server side), through the SSRF guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from mso.connectors.base import USER_AGENT
from mso.security import UnsafeTarget, check_url

PLACEHOLDER = "{{mso-secret}}"
SECRET_PARAM_NAMES = {
    "apikey",
    "api_key",
    "passkey",
    "torrent_pass",
    "authkey",
    "rsskey",
    "token",
    "r",
    "i",
    "key",
    "uid",
    "user",
    "pass",
    "password",
    "secret",
    "sid",
}
MAX_NZB_BYTES = 25 * 1024 * 1024
MAX_TORRENT_BYTES = 10 * 1024 * 1024


def strip_secret(url: str | None, secret: str | None) -> str | None:
    """Replace a known secret anywhere in the URL (query *or* path) by the placeholder."""
    if not url:
        return url
    if secret and len(secret) >= 4:
        url = url.replace(secret, PLACEHOLDER)
    return url


def fill_secret(url: str, secret: str | None) -> str:
    if PLACEHOLDER in url:
        if not secret:
            raise UnsafeTarget("API-sleutel van de bron ontbreekt; download niet mogelijk", "missing_secret")
        url = url.replace(PLACEHOLDER, secret)
    return url


def public_link(url: str | None) -> str | None:
    """A link that can be shown to a user: only when it contains no secret-looking parameters/placeholder."""
    if not url or PLACEHOLDER in url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.username or parts.password:
        return None
    if any(k.lower() in SECRET_PARAM_NAMES for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return None
    return url


def redact_magnet(magnet: str | None, private: bool | None) -> str | None:
    """Magnets of private torrents carry passkeys in tracker URLs: never expose those; public magnets lose
    any secret-looking tracker parameter."""
    if not magnet:
        return None
    if private:
        return None
    parts = urlsplit(magnet)
    keep = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k == "tr":
            tparts = urlsplit(v)
            if (
                any(p.lower() in SECRET_PARAM_NAMES for p, _ in parse_qsl(tparts.query))
                or "passkey" in v.lower()
            ):
                continue
        keep.append((k, v))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep, safe=":/"), ""))


@dataclass
class Fetched:
    content: bytes
    content_type: str
    filename: str | None
    final_url: str | None = None  # magnet target if the upstream redirected to a magnet


def safe_fetch(
    url: str,
    *,
    timeout: float = 30.0,
    max_bytes: int = MAX_NZB_BYTES,
    headers: dict[str, str] | None = None,
    max_redirects: int = 5,
) -> Fetched:
    """GET with the SSRF guard applied to the URL *and every redirect hop*, and a hard size cap."""
    current = url
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    for _ in range(max_redirects + 1):
        if current.startswith("magnet:"):
            return Fetched(b"", "application/x-bittorrent-magnet", None, final_url=current)
        target = check_url(current, purpose="download")
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=5.0), follow_redirects=False, headers=h
        ) as c, c.stream("GET", target) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    raise httpx.HTTPStatusError(
                        "redirect zonder Location", request=resp.request, response=resp
                    )
                current = str(resp.url.join(loc)) if not loc.startswith("magnet:") else loc
                continue
            resp.raise_for_status()
            total = 0
            chunks: list[bytes] = []
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise UnsafeTarget(
                        f"Download groter dan {max_bytes // (1024 * 1024)} MB geweigerd", "too_large"
                    )
                chunks.append(chunk)
            ctype = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            filename = None
            disp = resp.headers.get("content-disposition", "")
            if "filename=" in disp:
                filename = disp.split("filename=", 1)[1].strip().strip('"').split(";")[0]
            return Fetched(b"".join(chunks), ctype, filename)
    raise UnsafeTarget("Te veel doorverwijzingen", "too_many_redirects")
