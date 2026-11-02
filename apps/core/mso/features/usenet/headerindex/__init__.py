"""Optional, opt-in and bounded own NNTP header index (see ``engine``).

Plugs into Slice A's search backend registry when that module exists (lazy, guarded)."""

from __future__ import annotations

import logging
from typing import Any

from mso.features.usenet.headerindex import engine

LOGGER = logging.getLogger(__name__)
BACKEND_ID = "headerindex"
_registered = False


def search_backend(query: Any = None, *args: Any, **kwargs: Any) -> list[Any]:
    """Search backend: accepts a SearchQuery-like object (``q``, ``limit``, ``protocols``) or keywords."""
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
    limit = int(getattr(query, "limit", None) or kwargs.get("limit") or 50)
    return engine.search(str(q or ""), limit=limit)


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
        register_backend(
            BACKEND_ID,
            search_backend,
            label="Eigen header-index",
            source_id=engine.SOURCE_ID,
            kind="headerindex",
            protocols={"usenet"},
        )
        _registered = True
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("header-index kon niet als zoekbackend worden geregistreerd: %s", exc)
    return _registered


register_search_backend()
