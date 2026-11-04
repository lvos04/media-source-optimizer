"""Search contract types (shared with other slices; see docs/dev/AGENT_BRIEF.md)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from mso.domain.release import NormalizedRelease

SearchKind = Literal["search", "tvsearch", "movie"]
SOURCE_STATUSES = (
    "ok",
    "empty",
    "timeout",
    "error",
    "category_error",
    "skipped_cycle",
    "skipped_scope",
    "rate_limited",
    "backoff",
    "auth_error",
    "unavailable",  # the indexer manager (Prowlarr) itself reports this indexer as not working
)


@dataclass
class SearchQuery:
    q: str | None = None
    media_category: str | None = None  # tv|tv-anime|movies|movies-anime
    kind: SearchKind = "search"
    season: int | None = None
    episode: int | None = None
    absolute_episode: int | None = None
    imdb_id: str | None = None  # 'tt1234567'
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    protocols: set[str] | None = None  # {'usenet','torrent'}; None = both
    limit: int = 50
    offset: int = 0
    languages: list[str] | None = None  # extra required audio languages for this query
    newznab_cats: list[int] | None = None  # explicit Newznab categories (Newznab server passthrough)

    def fingerprint(self) -> str:
        """Identity of the *question* (not paging), used for in-flight loop detection."""
        parts = [
            (self.q or "").strip().lower(),
            str(self.season or ""),
            str(self.episode or ""),
            str(self.absolute_episode or ""),
            (self.imdb_id or "").lower(),
            str(self.tvdb_id or ""),
            str(self.tmdb_id or ""),
        ]
        return "|".join(parts)


@dataclass
class SourceReport:
    source_id: str
    label: str
    status: str
    count: int = 0
    latency_ms: int | None = None
    message: str = ""
    parent: str | None = None  # e.g. 'prowlarr' for a per-indexer row under the Prowlarr aggregate

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResult:
    items: list[NormalizedRelease]
    sources: list[SourceReport]
    trace_id: str
    total: int = 0
    offset: int = 0
    limit: int = 50
    trace: list[str] = field(default_factory=list)


class SourceError(Exception):
    """A backend failure with a status that is *not* 'empty' (timeouts/auth/category errors are distinct)."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
