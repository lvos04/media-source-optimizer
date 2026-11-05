"""Optional, opt-in and bounded own NNTP header index (see ``engine``) — a small own source behind MSO's Newznab
endpoint, not a replacement for indexers.

Plugs into Slice A's search backend registry (``register_backend(..., rss=True, active=engine.enabled)``):
a text-less query returns the latest indexed releases, so Prowlarr's add-indexer validation gets real results.

Downloads (``t=get`` on the Newznab endpoint, "send to SABnzbd" in the UI): header-index results have no upstream
URL; the NZB is built from the stored headers (``nzb.build``). Slice A's ``service.fetch_download(token)`` only
knows URLs and magnets, so this package hands it a fetcher: through ``register_backend(fetch=...)`` when Slice A
supports that, otherwise by wrapping ``fetch_download`` for header-index tokens only (every other token goes to
the original function unchanged).
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from mso.features.usenet.headerindex import engine, nzb

LOGGER = logging.getLogger(__name__)
BACKEND_ID = "headerindex"
_registered = False
_wrapped = False


def search_backend(query: Any = None, *args: Any, **kwargs: Any) -> list[Any]:
    """Search backend: accepts a SearchQuery-like object (``q``, ``limit``, ``protocols``) or keywords.
    ``q`` empty/None (and no id/season filter) → latest indexed releases (RSS)."""
    q = getattr(query, "q", None) if query is not None and not isinstance(query, str) else query
    q = q if q is not None else kwargs.get("q")
    protocols = getattr(query, "protocols", None) or kwargs.get("protocols")
    if protocols and "usenet" not in protocols:
        return []
    if not engine.enabled():
        try:
            from mso.features.search.models import SourceError
        except ImportError:
            return []
        # not an empty result: the source is simply not switched on (opt-in)
        raise SourceError("skipped_scope", "Eigen header-index staat uit (opt-in)")
    if not (q or "").strip() and any(
        getattr(query, k, None) for k in ("imdb_id", "tvdb_id", "tmdb_id", "season", "episode", "absolute_episode")
    ):
        return []  # headers carry no ids: an id-only query can't be answered honestly
    limit = int(getattr(query, "limit", None) or kwargs.get("limit") or 50)
    offset = int(getattr(query, "offset", None) or kwargs.get("offset") or 0)
    return engine.search(str(q or ""), limit=limit + offset, offset=0)


def fetch(row: dict[str, Any]) -> Any:
    """NZB for a cached header-index result (``release_cache`` row) → ``Fetched``."""
    from mso.features.search.urls import Fetched

    data = row.get("data") or {}
    key = (data.get("extra") or {}).get("hi_collection") or row.get("title") or ""
    try:
        content, info = nzb.build(str(key))
    except nzb.CollectionNotFound as exc:
        raise LookupError("Deze release staat niet (meer) in de header-index") from exc
    return Fetched(content, "application/x-nzb", nzb.filename_for(info["title"]))


def _install_fetch_wrapper() -> bool:
    global _wrapped
    if _wrapped:
        return True
    try:
        from mso.features.search import service
    except ImportError:
        return False
    original = service.fetch_download
    if getattr(original, "_mso_headerindex", False):
        _wrapped = True
        return True

    def fetch_download(token: str, *args: Any, **kwargs: Any) -> Any:
        row = service.get_cached(token)
        if row is not None and row.get("source_id") == engine.SOURCE_ID and not row.get("download_url"):
            return fetch(dict(row))
        return original(token, *args, **kwargs)

    fetch_download._mso_headerindex = True  # type: ignore[attr-defined]
    fetch_download.__doc__ = original.__doc__
    service.fetch_download = fetch_download
    _wrapped = True
    return True


def register_search_backend() -> bool:
    """Register with Slice A's ``mso.features.search.backends.register_backend`` if present (guarded)."""
    global _registered
    if _registered:
        return True
    try:
        from mso.features.search.backends import register_backend
    except ImportError:
        return False
    try:
        kw: dict[str, Any] = {
            "label": "Eigen header-index",
            "source_id": engine.SOURCE_ID,
            "kind": "headerindex",
            "protocols": {"usenet"},
            "rss": True,
            "active": engine.enabled,
        }
        params = inspect.signature(register_backend).parameters
        if "fetch" in params:
            kw["fetch"] = fetch
        register_backend(BACKEND_ID, search_backend, **{k: v for k, v in kw.items() if k in params})
        if "fetch" not in params:
            _install_fetch_wrapper()
        _registered = True
    except Exception as exc:
        LOGGER.warning("header-index kon niet als zoekbackend worden geregistreerd: %s", exc)
    return _registered


register_search_backend()
