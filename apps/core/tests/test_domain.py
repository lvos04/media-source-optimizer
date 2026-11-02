"""Release parsing, identity/dedupe, category mapping and quality gates."""

from mso.domain.categories import media_category_from, newznab_for, scope_allows
from mso.domain.release import (
    NormalizedRelease,
    QualityRequirements,
    check_quality,
    dedupe,
    infohashes_from_magnet,
    parse_title,
)


def test_parse_scene_tv() -> None:
    p = parse_title("The.Show.S02E05.2160p.WEB-DL.DDP5.1.Atmos.DV.HDR10.H.265-GRP")
    assert (p.season, p.episodes, p.resolution, p.source, p.video_codec) == (2, [5], 2160, "web", "h265")
    assert "dv" in p.hdr and "hdr10" in p.hdr and "atmos" in p.audio_codecs and p.release_group == "GRP"


def test_parse_multi_episode() -> None:
    assert parse_title("Show.S01E01-E03.1080p.BluRay.x264-X").episodes == [1, 2, 3]


def test_parse_anime_absolute() -> None:
    p = parse_title("[SubsPlease] Frieren - 27 (1080p) [ABCDEF12].mkv")
    assert p.anime_style and p.absolute_episodes == [27] and p.resolution == 1080 and p.release_group == "SubsPlease"
    d = parse_title("[Judas] One Piece - 1100 [1080p][HEVC x265 10bit][Dual-Audio][Multi-Subs]")
    assert d.absolute_episodes == [1100] and d.dual_audio and "multi" in d.subtitles and d.video_codec == "h265"


def test_parse_season_pack_and_movie() -> None:
    assert parse_title("Show.S03.1080p.WEB.h264-ABC").full_season
    m = parse_title("Some.Movie.2023.1080p.BluRay.REMUX.AVC.TrueHD.7.1-FGT")
    assert m.year == 2023 and m.source == "remux" and "truehd" in m.audio_codecs


def test_obfuscated_title() -> None:
    assert parse_title("a8f3k2l0q9z7x6c5v4b3n2m1").obfuscated


def test_infohash_v1_base32_and_v2() -> None:
    v1, v2 = infohashes_from_magnet("magnet:?xt=urn:btih:CYYQZIBWXD6Y6PLDPAGBSS5PV5RBSSCF&dn=x")
    assert v1 and len(v1) == 40
    v1b, v2b = infohashes_from_magnet("magnet:?xt=urn:btmh:1220" + "ab" * 32)
    assert v1b is None and v2b == "ab" * 32


def test_dedupe_by_identity_not_title() -> None:
    a = NormalizedRelease("torrent", "s1", "prowlarr", "Same Title", "g1", infohash_v1="a" * 40, seeders=5)
    b = NormalizedRelease("torrent", "s2", "direct", "Different Title", "g2", infohash_v1="A" * 40, seeders=9)
    c = NormalizedRelease("torrent", "s3", "direct", "Same Title", "g3", infohash_v1="b" * 40)
    out = dedupe([a, b, c])
    assert len(out) == 2
    merged = next(r for r in out if r.infohash_v1 == "a" * 40)
    assert merged.seeders == 9 and "s2" in merged.extra["also_from"]


def test_usenet_same_title_different_guid_not_merged() -> None:
    a = NormalizedRelease("usenet", "i1", "direct", "X.S01E01.1080p", "guid-1")
    b = NormalizedRelease("usenet", "i1", "direct", "X.S01E01.1080p", "guid-2")
    assert len(dedupe([a, b])) == 2


def test_four_categories() -> None:
    assert media_category_from([5040]) == "tv"
    assert media_category_from([5070]) == "tv-anime"
    assert media_category_from([2040]) == "movies"
    assert media_category_from([2040, 5070], is_movie_hint=True) == "movies-anime"
    assert 5070 in newznab_for("movies-anime") and 5070 not in newznab_for("tv")
    assert not scope_allows(["anime"], "tv") and scope_allows(["anime"], "tv-anime")


def test_quality_gate_anime_numbering_and_audio() -> None:
    r = NormalizedRelease("torrent", "s", "direct", "[Grp] Show - 12 (1080p) [Dual-Audio]", "g",
                          media_category="tv-anime", parsed=parse_title("[Grp] Show - 12 (1080p) [Dual-Audio]"))
    ok = QualityRequirements("tv-anime", absolute_episodes=[12], min_resolution=1080,
                             required_audio_languages=["ja", "en"])
    assert check_quality(r, ok) == []
    wrong = QualityRequirements("tv-anime", absolute_episodes=[13])
    assert check_quality(r, wrong)


def test_quality_gate_dv_required_and_category() -> None:
    title = "Movie.2022.2160p.WEB-DL.HDR10.H265-GRP"
    r = NormalizedRelease("usenet", "s", "direct", title, "g", media_category="movies", parsed=parse_title(title))
    assert "DV vereist" in check_quality(r, QualityRequirements("movies", require_hdr=["dv"]))
    assert check_quality(r, QualityRequirements("movies-anime"))
