"""1.4.0 · 2a: the header index as an own (small) source behind MSO's Newznab endpoint — RSS (text-less) query
returns the latest indexed releases, registered with rss=True/active=enabled so the endpoint readiness counts it
only while it is switched on, and t=get returns a valid NZB built from the stored headers (real message-ids,
groups, sizes; poster omitted)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from defusedxml import ElementTree as DET

USER, PASSWORD = "u", "p-geheim-hi"
GROUP = "alt.binaries.mso.test"
REL = "Some.Show.S02E05.1080p.WEB.H264-GRP"


def _posts() -> list[tuple[str, str, str, int]]:
    now = datetime.now(UTC)

    def d(minutes: int) -> str:
        return format_datetime(now - timedelta(minutes=minutes))

    return [
        ('"Movie.Title.2019.2160p.UHD.BluRay.x265-GRP.part01.rar" yEnc (1/1)', d(90), "mov1@mock", 500_000),
        (f'[1/3] - "{REL}.part01.rar" yEnc (1/3)', d(30), "s1p1@mock", 700_000),
        (f'[1/3] - "{REL}.part01.rar" yEnc (2/3)', d(30), "s1p2@mock", 700_000),
        (f'[1/3] - "{REL}.part01.rar" yEnc (3/3)', d(30), "s1p3@mock", 300_000),
        (f'[2/3] - "{REL}.part02.rar" yEnc (1/2)', d(29), "s2p1@mock", 700_000),  # (2/2) never seen
        (f'[3/3] - "{REL}.par2" yEnc (1/1)', d(28), "par@mock", 40_000),
        ('[1/1] - "a9f3c2b1e8d7c6b5a4f3e2d1.part01.rar" yEnc (1/9)', d(5), "obf@mock", 700_000),
    ]


@pytest.fixture()
def hi(app: Any, admin: Any) -> Iterator[Any]:
    from msomocks import nntp

    srv = nntp.start(username=USER, password=PASSWORD, groups={GROUP: _posts()})
    r = admin.post(
        "/api/v1/usenet/providers",
        json={"slug": "eweka", "label": "Eweka", "host": "127.0.0.1", "port": srv.port, "ssl": False,
              "username": USER, "password": PASSWORD},
    )
    assert r.status_code == 200
    r = admin.put("/api/v1/usenet/headerindex/config",
                  json={"enabled": True, "acknowledge_limits": True, "groups": [GROUP], "provider": "eweka"})
    assert r.status_code == 200
    from mso.features.usenet.headerindex import engine

    assert engine.run()["headers_stored"] == 7
    yield srv
    srv.stop()


class Q:
    q: str | None = None
    limit = 50
    offset = 0
    protocols = {"usenet"}
    imdb_id = tvdb_id = tmdb_id = season = episode = absolute_episode = None


def test_rss_returns_latest_collections(hi: Any) -> None:
    from mso.features.usenet.headerindex import search_backend

    items = search_backend(Q())
    assert [i.title for i in items] == [REL, "Movie.Title.2019.2160p.UHD.BluRay.x265-GRP"]  # newest first
    show = items[0]
    assert show.message_id.endswith("@mock") and show.size_bytes == 2_440_000
    assert show.extra["articles"] == 5 and show.extra["hi_collection"] == REL.lower()
    assert 5000 in show.categories and 2000 in items[1].categories
    assert all("obf" not in (i.message_id or "") for i in items)


def test_registered_rss_and_readiness_follows_enabled(app: Any, admin: Any, hi: Any) -> None:
    from mso.features.search.backends import BACKENDS

    b = BACKENDS["headerindex"]
    assert b.rss is True and b.is_active() is True
    from mso.features.newznab_server import readiness

    ready = readiness.endpoint_sources("newznab")
    assert ready["ready"] is True
    assert any(s["source_id"] == "headerindex:own" and s["rss"] for s in ready["sources"])
    admin.put("/api/v1/usenet/headerindex/config", json={"enabled": False})
    assert b.is_active() is False
    assert not any(s["source_id"] == "headerindex:own" for s in readiness.endpoint_sources("newznab")["sources"])


def test_newznab_rss_and_get_return_usable_nzb(app: Any, admin: Any, client: Any, hi: Any) -> None:
    from mso.features.usenet import nzbs

    info = admin.get("/api/v1/newznab/endpoint").json()
    key = admin.post(f"/api/v1/newznab/consumers/{info['default_consumer']['id']}/reveal").json()["apikey"]
    r = client.get("/newznab/api", params={"t": "search", "extended": "1", "limit": "100", "apikey": key})
    assert r.status_code == 200, r.text
    items = DET.fromstring(r.content).findall("./channel/item")
    assert len(items) >= 1
    show = next(i for i in items if i.findtext("title") == REL)
    link = show.find("enclosure").get("url") if show.find("enclosure") is not None else show.findtext("link")
    params = {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}
    assert params["t"] == "get"
    r = client.get("/newznab/api", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-nzb")
    root = DET.fromstring(r.content)  # valid XML
    ns = {"n": "http://www.newzbin.com/DTD/2003/nzb"}
    files = root.findall("n:file", ns)
    assert len(files) == 3 and all(f.get("poster") == "" for f in files)
    mids = sorted(s.text for s in root.iter("{http://www.newzbin.com/DTD/2003/nzb}segment"))
    assert mids == ["par@mock", "s1p1@mock", "s1p2@mock", "s1p3@mock", "s2p1@mock"]
    assert {g.text for g in root.iter("{http://www.newzbin.com/DTD/2003/nzb}group")} == {GROUP}
    part01 = next(f for f in files if "part01" in f.get("subject"))
    assert [s.get("number") for s in part01.iter("{http://www.newzbin.com/DTD/2003/nzb}segment")] == ["1", "2", "3"]
    parsed = nzbs.parse_nzb(r.content)  # MSO's own (and SABnzbd-compatible) parser accepts it
    assert parsed.title == REL and parsed.segment_total == 5 and parsed.groups == [GROUP]
    assert PASSWORD.encode() not in r.content


def test_build_reports_completeness_and_missing_collection(hi: Any) -> None:
    from mso.features.usenet.headerindex import nzb

    _, meta = nzb.build(REL)
    assert meta["files"] == 3 and meta["segments"] == 5 and meta["expected_segments"] == 6
    assert meta["completeness"] == round(5 / 6, 4)
    with pytest.raises(nzb.CollectionNotFound):
        nzb.build("bestaat.niet")
    assert nzb.split_subject('[1/3] - "x.rar" yEnc (2/30)') == ('[1/3] - "x.rar" yEnc', 2, 30)
