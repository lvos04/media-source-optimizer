"""Slice A — MSO's own Newznab/Torznab endpoints: caps, search/tvsearch/movie with correct categories
(4-category
matrix incl. anime under 5070), XML shape (rss 2.0 + newznab/torznab attrs, validated with defusedxml),
error codes,
pagination, auth + key lifecycle (generate on first use, reveal/rotate audited), quota, t=get passthrough
without
leaking upstream credentials, private magnets never exposed."""

from __future__ import annotations

from typing import Any

import pytest
from defusedxml import ElementTree as DET
from msomocks import newznab as mock_nz
from slice_a_helpers import NZ_KEY, TZ_KEY, add_direct, set_settings

NS = {
    "newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/",
    "torznab": "http://torznab.com/schemas/2015/feed",
    "atom": "http://www.w3.org/2005/Atom",
}


@pytest.fixture()
def setup(admin: Any, serve: Any) -> dict[str, Any]:
    nz = mock_nz.create_app(apikey=NZ_KEY)
    tz = mock_nz.create_app(apikey=TZ_KEY, protocol="torrent")
    add_direct(admin, serve(nz), label="Upstream NZB")
    add_direct(admin, serve(tz), kind="torznab", key=TZ_KEY, label="Upstream torrent")
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    info = admin.get("/api/v1/newznab/endpoint").json()
    key = admin.post(f"/api/v1/newznab/consumers/{info['default_consumer']['id']}/reveal").json()["apikey"]
    return {"key": key, "nz": nz, "tz": tz, "info": info}


def xml(r: Any) -> Any:
    assert r.headers["content-type"].startswith("application/xml"), r.headers["content-type"]
    return DET.fromstring(r.content)


def items(root: Any) -> list[Any]:
    return root.findall("./channel/item")


def attrs(item: Any, ns: str = "newznab") -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in item.findall(f"{ns}:attr", NS):
        out.setdefault(a.get("name"), []).append(a.get("value"))
    return out


def test_caps_document(client: Any, app: Any) -> None:
    root = xml(client.get("/newznab/api", params={"t": "caps"}))
    assert root.tag == "caps"
    s = root.find("searching")
    assert s.find("tv-search").get("available") == "yes"
    assert set(s.find("tv-search").get("supportedParams").split(",")) >= {
        "q",
        "season",
        "ep",
        "tvdbid",
        "imdbid",
        "rid",
    }
    assert set(s.find("movie-search").get("supportedParams").split(",")) == {"q", "imdbid", "tmdbid"}
    tv = next(c for c in root.find("categories") if c.get("id") == "5000")
    assert any(sc.get("id") == "5070" and sc.get("name") == "TV/Anime" for sc in tv)
    assert int(root.find("limits").get("max")) == 100
    assert xml(client.get("/torznab/api", params={"t": "caps"})).tag == "caps"


def test_error_codes(client: Any, setup: dict[str, Any]) -> None:
    k = setup["key"]
    cases = [
        ({}, 200),
        ({"t": "search", "apikey": "fout"}, 100),
        ({"t": "search"}, 100),
        ({"t": "music", "apikey": k}, 202),
        ({"t": "tvsearch", "season": "x", "apikey": k}, 201),
        ({"t": "movie", "imdbid": "tt12ab", "apikey": k}, 201),
        ({"t": "search", "cat": "50x0", "apikey": k}, 201),
        ({"t": "get", "apikey": k}, 200),
        ({"t": "get", "id": "bestaatniet", "apikey": k}, 300),
    ]
    for params, code in cases:
        r = client.get("/newznab/api", params=params)
        root = xml(r)
        assert root.tag == "error" and root.get("code") == str(code), (params, r.text)
        assert r.status_code == 200


def test_key_generated_on_first_use_reveal_rotate_audited(
    admin: Any, viewer: Any, client: Any, db: Any, app: Any
) -> None:
    assert db.scalar("SELECT count(*) FROM newznab_consumers") == 0
    info = viewer.get("/api/v1/newznab/endpoint").json()  # first use
    assert info["default_consumer"]["node"] == "prowlarr" and info["default_consumer"]["key_hint"].startswith(
        "…"
    )
    from mso.vault import get_vault

    key = get_vault().get("newznab.apikey")
    assert key and len(key) == 32 and key not in str(info)
    assert (
        viewer.post(f"/api/v1/newznab/consumers/{info['default_consumer']['id']}/reveal").status_code == 403
    )
    cid = info["default_consumer"]["id"]
    assert admin.post(f"/api/v1/newznab/consumers/{cid}/reveal").json()["apikey"] == key
    assert xml(client.get("/newznab/api", params={"t": "search", "q": "x", "apikey": key})).tag == "rss"
    new = admin.post(f"/api/v1/newznab/consumers/{cid}/rotate").json()["apikey"]
    assert new != key
    assert xml(client.get("/newznab/api", params={"t": "search", "apikey": key})).get("code") == "100"
    assert xml(client.get("/newznab/api", params={"t": "search", "q": "x", "apikey": new})).tag == "rss"
    actions = [r["action"] for r in db.fetchall("SELECT action FROM audit_log ORDER BY id")]
    assert "newznab.key_revealed" in actions and "newznab.key_rotated" in actions
    assert key not in str(db.fetchall("SELECT * FROM audit_log")) and new not in str(
        db.fetchall("SELECT * FROM audit_log")
    )
    assert admin.delete(f"/api/v1/newznab/consumers/{cid}").status_code == 409
    extra = admin.post("/api/v1/newznab/consumers", json={"label": "Tweede", "node": "external"}).json()
    assert admin.patch(f"/api/v1/newznab/consumers/{extra['id']}", json={"enabled": False}).status_code == 200
    assert (
        xml(client.get("/newznab/api", params={"t": "search", "apikey": extra["apikey"]})).get("code")
        == "100"
    )
    assert admin.delete(f"/api/v1/newznab/consumers/{extra['id']}").status_code == 200


CAT_CASES = [
    # (endpoint, params, expected title fragment, forbidden fragment, required category attr)
    ("newznab", {"t": "tvsearch", "cat": "5030,5040,5045", "q": "Expanse"}, "Expanse", "Frieren", "5000"),
    ("newznab", {"t": "tvsearch", "cat": "5070", "q": "Frieren"}, "Frieren", "Expanse", "5070"),
    ("newznab", {"t": "movie", "cat": "2000,2040,2045,2060", "q": "Dune"}, "Dune", "Suzume", "2000"),
    ("newznab", {"t": "movie", "cat": "2000,2040,2045,2060,5070", "q": "Suzume"}, "Suzume", "Dune", "2000"),
    ("torznab", {"t": "tvsearch", "cat": "5070", "q": "Frieren"}, "Frieren", "Expanse", "5070"),
    ("torznab", {"t": "movie", "cat": "2000,2040", "q": "Dune"}, "Dune", "Suzume", "2000"),
]


@pytest.mark.parametrize("endpoint,params,want,never,cat", CAT_CASES)
def test_category_matrix_over_newznab(
    client: Any, setup: dict[str, Any], endpoint: str, params: dict[str, str], want: str, never: str, cat: str
) -> None:
    root = xml(client.get(f"/{endpoint}/api", params={**params, "apikey": setup["key"]}))
    its = items(root)
    assert its, f"geen resultaten voor {params}"
    ns = "torznab" if endpoint == "torznab" else "newznab"
    for it in its:
        title = it.findtext("title")
        assert want in title and never not in title
        a = attrs(it, ns)
        assert cat in a["category"]
        if params["cat"].startswith("2000") and "5070" not in params["cat"]:
            assert "5070" not in a["category"]  # normal movies never advertised as anime
        if params["cat"] == "5070":
            assert "5070" in a["category"]


def test_rss_shape_links_and_no_upstream_secret(client: Any, setup: dict[str, Any]) -> None:
    r = client.get(
        "/newznab/api",
        params={
            "t": "tvsearch",
            "q": "Expanse",
            "season": "2",
            "ep": "5",
            "tvdbid": "280619",
            "apikey": setup["key"],
        },
    )
    assert NZ_KEY not in r.text and TZ_KEY not in r.text
    root = xml(r)
    assert root.tag == "rss" and root.get("version") == "2.0"
    resp = root.find("./channel/newznab:response", NS)
    assert resp is not None and resp.get("offset") == "0" and int(resp.get("total")) >= 1
    assert root.find("./channel/atom:link", NS) is not None
    it = items(root)[0]
    for tag in ("title", "guid", "link", "pubDate", "size", "description", "category"):
        assert it.find(tag) is not None, tag
    enc = it.find("enclosure")
    assert enc.get("type") == "application/x-nzb" and int(enc.get("length")) > 0
    link = it.findtext("link")
    assert link.startswith("http://mso.lan:8000/newznab/api?t=get&id=") and f"apikey={setup['key']}" in link
    a = attrs(it)
    assert a["tvdbid"] == ["280619"] and a["season"] == ["2"] and a["episode"] == ["5"]
    assert a["imdb"] == ["3230854"] and "usenetdate" in a and "grabs" in a and int(a["size"][0]) > 0
    assert it.find("guid").text.startswith("guid:")  # stable identity, not the upstream link
    # upstream asked with tvsearch params, MSO trace header present
    up = setup["nz"].state.calls[-1]
    assert up["params"]["t"] == "tvsearch" and up["params"]["season"] == "2" and up["trace"]


def test_pagination_offset_total(client: Any, setup: dict[str, Any]) -> None:
    k = setup["key"]
    full = xml(client.get("/newznab/api", params={"t": "search", "q": "Expanse", "apikey": k}))
    total = int(full.find("./channel/newznab:response", NS).get("total"))
    assert total >= 3
    p2 = xml(
        client.get(
            "/newznab/api", params={"t": "search", "q": "Expanse", "limit": "1", "offset": "1", "apikey": k}
        )
    )
    resp = p2.find("./channel/newznab:response", NS)
    assert resp.get("offset") == "1" and int(resp.get("total")) == total and len(items(p2)) == 1
    big = xml(
        client.get("/newznab/api", params={"t": "search", "q": "Expanse", "limit": "100000", "apikey": k})
    )
    assert len(items(big)) <= 100


def test_torznab_attrs_and_private_magnets(
    admin: Any, client: Any, serve: Any, setup: dict[str, Any]
) -> None:
    root = xml(client.get("/torznab/api", params={"t": "movie", "q": "Dune", "apikey": setup["key"]}))
    it = items(root)[0]
    a = attrs(it, "torznab")
    for name in ("seeders", "peers", "infohash", "magneturl", "downloadvolumefactor", "uploadvolumefactor"):
        assert name in a, name
    assert it.find("enclosure").get("type") == "application/x-bittorrent"
    assert int(a["peers"][0]) >= int(a["seeders"][0])
    # a private tracker: no magnet (passkey in tracker URL) in the feed
    admin.patch("/api/v1/indexers/direct/upstream-torrent", json={"enabled": False})
    priv = mock_nz.create_app(apikey=TZ_KEY, protocol="torrent", private="1")
    add_direct(admin, serve(priv), kind="torznab", key=TZ_KEY, label="Prive", private=True)
    r = client.get("/torznab/api", params={"t": "movie", "q": "Dune", "apikey": setup["key"]})
    assert b"SECRETPASSKEY" not in r.content and b"passkey" not in r.content
    for it in items(xml(r)):
        assert "magneturl" not in attrs(it, "torznab")


def test_get_passthrough_proxies_content(client: Any, setup: dict[str, Any], db: Any) -> None:
    k = setup["key"]
    it = items(xml(client.get("/newznab/api", params={"t": "search", "q": "Dune", "apikey": k})))[0]
    token = it.findtext("link").split("id=")[1].split("&")[0]
    r = client.get("/newznab/api", params={"t": "get", "id": token, "apikey": k})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-nzb")
    assert b"<nzb" in r.content and ".nzb" in r.headers["content-disposition"]
    assert db.scalar("SELECT grabs FROM indexer_usage WHERE source_id='direct:upstream-nzb'") == 1
    # a usenet token is not served through the torznab endpoint
    assert xml(client.get("/torznab/api", params={"t": "get", "id": token, "apikey": k})).get("code") == "300"
    tt = items(xml(client.get("/torznab/api", params={"t": "search", "q": "Dune", "apikey": k})))[0]
    ttoken = tt.findtext("link").split("id=")[1].split("&")[0]
    r = client.get("/torznab/api", params={"t": "get", "id": ttoken, "apikey": k})
    assert r.status_code == 200 and r.content.startswith(b"d8:announce")
    # upstream failure → error 900, no secret
    setup["nz"].state.faults["error500"] = "1"
    r = client.get("/newznab/api", params={"t": "get", "id": token, "apikey": k})
    assert xml(r).get("code") == "900" and NZ_KEY not in r.text


def test_rate_limit_and_daily_quota(admin: Any, client: Any, setup: dict[str, Any]) -> None:
    cid = setup["info"]["default_consumer"]["id"]
    admin.patch(f"/api/v1/newznab/consumers/{cid}", json={"rate_per_min": 2})
    k = setup["key"]
    codes = [client.get("/newznab/api", params={"t": "search", "q": "x", "apikey": k}) for _ in range(3)]
    assert [r.status_code for r in codes] == [200, 200, 429]
    assert xml(codes[-1]).get("code") == "500" and "Retry-After" in codes[-1].headers
    from mso.security import limiter

    limiter.reset()
    admin.patch(f"/api/v1/newznab/consumers/{cid}", json={"rate_per_min": 100, "daily_quota": 1})
    r = client.get("/newznab/api", params={"t": "search", "q": "x", "apikey": k})
    assert r.status_code == 429 and xml(r).get("code") == "500"


def test_backends_setting_and_scope(client: Any, admin: Any, setup: dict[str, Any]) -> None:
    set_settings({"newznab.backends": ["spotweb"]})
    root = xml(client.get("/newznab/api", params={"t": "search", "q": "Expanse", "apikey": setup["key"]}))
    assert items(root) == []
    set_settings({"newznab.backends": ["direct"]})
    admin.patch("/api/v1/indexers/direct/upstream-nzb", json={"scopes": ["normal"]})
    root = xml(
        client.get(
            "/newznab/api", params={"t": "tvsearch", "cat": "5070", "q": "Frieren", "apikey": setup["key"]}
        )
    )
    assert items(root) == []  # anime request never answered by a normal-only source


def test_malformed_upstream_does_not_break_feed(client: Any, setup: dict[str, Any]) -> None:
    setup["nz"].state.faults["malformed"] = "1"
    root = xml(client.get("/newznab/api", params={"t": "search", "q": "Expanse", "apikey": setup["key"]}))
    assert root.tag == "rss" and items(root) == []


def test_private_magnets_not_persisted_and_magnet_only_redirect(
    admin: Any, client: Any, serve: Any, setup: dict[str, Any], db: Any
) -> None:
    admin.patch("/api/v1/indexers/direct/upstream-torrent", json={"enabled": False})
    priv = mock_nz.create_app(apikey=TZ_KEY, protocol="torrent", private="1")
    add_direct(admin, serve(priv), kind="torznab", key=TZ_KEY, label="Prive", private=True)
    root = xml(client.get("/torznab/api", params={"t": "movie", "q": "Dune", "apikey": setup["key"]}))
    assert items(root)
    rows = db.fetchall("SELECT * FROM release_cache WHERE source_id='direct:prive'")
    assert rows and all(r["magnet"] is None for r in rows)
    assert TZ_KEY not in str(rows)  # upstream key replaced by placeholder in stored links
    # a magnet-only (public) result is handed out as a redirect by t=get
    token = rows[0]["token"]
    magnet = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=x"
    db.execute("UPDATE release_cache SET download_url=NULL, magnet=%s WHERE token=%s", (magnet, token))
    r = client.get("/torznab/api", params={"t": "get", "id": token, "apikey": setup["key"]}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == magnet


def test_endpoint_uses_forwarded_host_from_web_proxy(admin) -> None:  # type: ignore[no-untyped-def]
    r = admin.get("/api/v1/newznab/endpoint", headers={"X-Forwarded-Host": "nas.lan:30081",
                                                       "X-Forwarded-Proto": "http"})
    assert r.status_code == 200
    assert "nas.lan:30081" in r.text and "api:8200" not in r.text
