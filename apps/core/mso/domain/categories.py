"""The four MediaGuard library categories and their Newznab/Torznab mapping.

MediaGuard runs four Sonarr/Radarr forks: normal TV, anime TV, normal movies, anime movies. Indexers are
tagged ``normal`` or ``anime`` in Prowlarr (MediaGuard's own reconcile owns those tags). The mapping below
mirrors MediaGuard ``provisioning/templates.py`` (SONARR_CATEGORIES, SONARR_ANIME_CATEGORIES,
RADARR_CATEGORIES, RADARR_ANIME_EXTRA) so both apps agree on what is anime.
"""

from __future__ import annotations

from dataclasses import dataclass

MEDIA_CATEGORIES = ("tv", "tv-anime", "movies", "movies-anime")
SCOPE_TAGS = ("normal", "anime")

# Newznab standard categories
TV, TV_SD, TV_HD, TV_UHD, TV_ANIME = 5000, 5030, 5040, 5045, 5070
TV_FOREIGN, TV_OTHER, TV_SPORT, TV_DOC = 5020, 5050, 5060, 5080
MOVIES, MOVIES_SD, MOVIES_HD, MOVIES_UHD, MOVIES_BLURAY, MOVIES_3D = 2000, 2030, 2040, 2045, 2050, 2060
MOVIES_FOREIGN, MOVIES_OTHER, MOVIES_WEBDL = 2010, 2020, 2070


@dataclass(frozen=True)
class CategoryDef:
    key: str
    label: str
    kind: str  # tv|movie
    anime: bool
    newznab: tuple[int, ...]
    scope_tag: str


CATEGORIES: dict[str, CategoryDef] = {
    "tv": CategoryDef("tv", "Series", "tv", False, (TV, TV_SD, TV_HD, TV_UHD, TV_FOREIGN, TV_OTHER, TV_DOC),
                      "normal"),
    "tv-anime": CategoryDef("tv-anime", "Anime-series", "tv", True, (TV_ANIME,), "anime"),
    "movies": CategoryDef("movies", "Films", "movie", False,
                          (MOVIES, MOVIES_SD, MOVIES_HD, MOVIES_UHD, MOVIES_BLURAY, MOVIES_3D, MOVIES_FOREIGN,
                           MOVIES_OTHER, MOVIES_WEBDL), "normal"),
    # anime movies are found under Movies AND under TV/Anime (5070) — same as MediaGuard RADARR_ANIME_EXTRA
    "movies-anime": CategoryDef("movies-anime", "Animefilms", "movie", True,
                                (MOVIES, MOVIES_HD, MOVIES_UHD, MOVIES_BLURAY, TV_ANIME), "anime"),
}


def newznab_for(media_category: str) -> list[int]:
    return list(CATEGORIES[media_category].newznab)


def scope_for(media_category: str) -> str:
    return CATEGORIES[media_category].scope_tag


def media_category_from(newznab_cats: list[int] | tuple[int, ...], *, anime_hint: bool | None = None,
                        is_movie_hint: bool | None = None) -> str | None:
    """Best-effort classification of a result. 5070 is anime; 2xxx is a movie; 5xxx TV."""
    cats = set(int(c) for c in newznab_cats)
    is_anime = TV_ANIME in cats if anime_hint is None else anime_hint
    has_movie = any(2000 <= c < 3000 for c in cats)
    has_tv = any(5000 <= c < 6000 and c != TV_ANIME for c in cats)
    if is_movie_hint is True or (has_movie and not has_tv):
        return "movies-anime" if is_anime else "movies"
    if has_tv or TV_ANIME in cats or is_movie_hint is False:
        return "tv-anime" if is_anime else "tv"
    return None


def scope_allows(source_scopes: list[str] | tuple[str, ...], media_category: str) -> bool:
    """A source tagged only 'anime' never serves normal categories and vice versa."""
    return scope_for(media_category) in set(source_scopes)
