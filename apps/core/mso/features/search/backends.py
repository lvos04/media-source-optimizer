"""Pluggable search backends (e.g. Slice B's own NNTP header index).

Usage from another slice, without touching Slice A code::

    from mso.features.search.backends import register_backend
    register_backend("headerindex", my_search, label="Eigen header-index", protocols={"usenet"})

``fn(query: SearchQuery) -> list[NormalizedRelease]``. Raise ``SourceError(status, message)`` for a
timeout/error/category error (so it is not reported as an empty result). Backends registered here are
leaf nodes in the origin graph (they never call Prowlarr or MSO back) unless ``edges_to`` says so.

``rss=True`` declares that ``fn`` answers a query *without* text (``query.q is None``) with the latest
releases (an RSS-style listing). Prowlarr validates a newly added Newznab/Torznab indexer with exactly such a
query, so MSO only offers to register itself in Prowlarr when at least one backend can answer it.
``active`` (optional) says whether the backend is switched on right now (e.g. an opt-in feature).
``fetch`` (optional) ``fn(release_cache_row: dict) -> Fetched`` builds the NZB/.torrent for a cached result
of this backend that has no download URL (e.g. an NZB assembled from the header index); used by
``service.fetch_download`` (sending from MSO and grabs through the Newznab endpoint).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mso.domain.release import NormalizedRelease
from mso.features.search.models import SearchQuery, SourceError  # noqa: F401  (re-export)

BackendFn = Callable[[SearchQuery], list[NormalizedRelease]]


@dataclass
class Backend:
    name: str
    fn: BackendFn
    source_id: str
    label: str
    kind: str  # used by the Newznab server setting 'newznab.backends'
    protocols: set[str] = field(default_factory=lambda: {"usenet"})
    scopes: tuple[str, ...] = ("normal", "anime")
    edges_to: tuple[str, ...] = ()  # origin-graph nodes this backend itself may query
    rss: bool = False  # answers a text-less query with the latest releases
    active: Callable[[], bool] | None = None  # None = always on
    fetch: Callable[[dict[str, Any]], Any] | None = None  # cached row without download URL -> Fetched

    def is_active(self) -> bool:
        if self.active is None:
            return True
        try:
            return bool(self.active())
        except Exception:
            return False


BACKENDS: dict[str, Backend] = {}


def register_backend(
    name: str,
    fn: BackendFn,
    *,
    label: str | None = None,
    source_id: str | None = None,
    kind: str | None = None,
    protocols: set[str] | None = None,
    scopes: tuple[str, ...] | list[str] = ("normal", "anime"),
    edges_to: tuple[str, ...] | list[str] = (),
    rss: bool = False,
    active: Callable[[], bool] | None = None,
    fetch: Callable[[dict[str, Any]], Any] | None = None,
) -> Backend:
    sid = source_id or (name if ":" in name else f"{name}:default")
    b = Backend(
        name=name,
        fn=fn,
        source_id=sid,
        label=label or name,
        kind=kind or name.split(":")[0],
        protocols=set(protocols or {"usenet"}),
        scopes=tuple(scopes),
        edges_to=tuple(edges_to),
        rss=rss,
        active=active,
        fetch=fetch,
    )
    BACKENDS[name] = b
    return b


def unregister_backend(name: str) -> None:
    BACKENDS.pop(name, None)
