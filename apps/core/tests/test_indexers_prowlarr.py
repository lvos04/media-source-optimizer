"""Slice A — Prowlarr connector: test/capabilities, error classification, source sync (scopes from
MediaGuard tags),
reconcile awareness (conflicts → recommendations, never writes), and the one allowed write (registering MSO's
own endpoint) through a change set with approval, idempotency and rollback."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from msomocks import newznab as mock_nz
from msomocks import prowlarr as mock_prowlarr
from slice_a_helpers import NZ_KEY, PROWLARR_KEY, TZ_KEY, add_direct, configure_prowlarr, set_settings


@pytest.fixture()
def prowlarr(app: Any, serve: Any) -> tuple[Any, str]:
    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    url = serve(mock)
    configure_prowlarr(url)
    return mock, url


def test_connector_test_reports_version_and_capabilities(admin: Any, prowlarr: tuple[Any, str]) -> None:
    r = admin.post("/api/v1/connectors/prowlarr/test")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["ok"] is True and res["version"] == mock_prowlarr.VERSION
    caps = res["capabilities"]
    assert caps["indexer_count"] == 4
    assert all(caps["endpoints"].values())
    assert caps["scope_tags"] == ["anime", "normal"]
    assert res["status"] == "degraded"  # mock reports one health warning
    assert PROWLARR_KEY not in r.text


def test_connector_auth_failure_is_distinct(admin: Any, prowlarr: tuple[Any, str]) -> None:
    mock, _ = prowlarr
    mock.state.faults["auth"] = "fail"
    res = admin.post("/api/v1/connectors/prowlarr/test").json()
    assert res["ok"] is False and res["status"] == "auth_failed"


def test_connector_unreachable(admin: Any, app: Any) -> None:
    configure_prowlarr("http://127.0.0.1:9")
    res = admin.post("/api/v1/connectors/prowlarr/test").json()
    assert res["ok"] is False and res["status"] == "offline"


def test_sync_sources_scopes_privacy_and_untagged(operator: Any, prowlarr: tuple[Any, str], db: Any) -> None:
    r = operator.post("/api/v1/indexers/prowlarr/sync")
    assert r.status_code == 200, r.text
    rows = {s["id"]: s for s in db.fetchall("SELECT * FROM sources WHERE origin='prowlarr'")}
    assert rows["prowlarr:1"]["kind"] == "nzb_indexer" and rows["prowlarr:1"]["scopes"] == ["normal"]
    assert rows["prowlarr:1"]["private"] is True
    assert rows["prowlarr:2"]["kind"] == "torrent_indexer" and rows["prowlarr:2"]["scopes"] == ["anime"]
    assert rows["prowlarr:2"]["private"] is False
    assert (
        rows["prowlarr:4"]["scopes"] == ["normal", "anime"] and rows["prowlarr:4"]["meta"]["untagged"] is True
    )
    assert rows["prowlarr:4"]["meta"]["disabled_till"]
    assert db.scalar("SELECT count(*) FROM observations WHERE metric='prowlarr.avg_response_ms'") == 4
    # indexer removed in Prowlarr → disabled, history kept
    mock, _ = prowlarr
    mock.state.data["indexers"] = [i for i in mock.state.data["indexers"] if i["id"] != 3]
    operator.post("/api/v1/indexers/prowlarr/sync")
    row = db.fetchone("SELECT * FROM sources WHERE id='prowlarr:3'")
    assert row["enabled"] is False and row["meta"]["removed"] is True


def test_view_detects_conflicts_and_makes_recommendations_only(
    viewer: Any, operator: Any, prowlarr: tuple[Any, str], db: Any
) -> None:
    mock, _ = prowlarr
    before = [dict(i) for i in mock.state.data["indexers"]]
    view = viewer.get("/api/v1/indexers/prowlarr/view").json()
    codes = {(c["code"], c["subject"]) for c in view["conflicts"]}
    assert ("anime_tagged_normal", "prowlarr:3") in codes
    assert ("indexer_untagged", "prowlarr:4") in codes
    assert ("mso_not_registered", "mso:registration") in codes
    assert view["mediaguard_managed"] is True
    assert {a["name"] for a in view["applications"] if a["mediaguard"]} == {
        "MediaGuard · Sonarr",
        "MediaGuard · Sonarr Anime",
        "MediaGuard · Radarr",
        "MediaGuard · Radarr Anime",
    }
    operator.post("/api/v1/indexers/prowlarr/sync")
    operator.post("/api/v1/indexers/prowlarr/sync")  # idempotent
    recs = viewer.get("/api/v1/indexers/recommendations").json()["items"]
    keys = sorted(r["dedupe_key"] for r in recs)
    # no own sources behind MSO's endpoint → no recommendation to register MSO (Prowlarr would refuse it)
    assert keys == sorted(set(keys)) and len(keys) == 2
    assert not [k for k in keys if "mso_not_registered" in k]
    anime = next(r for r in recs if "anime_tagged_normal" in r["dedupe_key"])
    assert anime["risk"] == "advies" and anime["evidence"]["preview"]["suggested_tags"] == ["anime"]
    # never wrote anything to Prowlarr
    assert mock.state.data["indexers"] == before
    assert not [c for c in mock.state.calls if c["method"] != "GET"]
    # once MediaGuard fixes the tag, the recommendation resolves
    mock.state.data["indexers"][2]["tags"] = [1]
    operator.post("/api/v1/indexers/prowlarr/sync")
    row = db.fetchone("SELECT status FROM recommendations WHERE dedupe_key LIKE '%%anime_tagged_normal%%'")
    assert row["status"] == "resolved"


def test_registration_requires_public_url(admin: Any, prowlarr: tuple[Any, str]) -> None:
    r = admin.post("/api/v1/indexers/prowlarr/register")
    assert r.status_code == 422 and "publieke URL" in r.json()["detail"]


@pytest.fixture()
def own_sources(admin: Any, serve: Any) -> None:
    """One direct NZB + one direct Torznab indexer behind MSO's endpoint (registration precondition)."""
    add_direct(admin, serve(mock_nz.create_app(apikey=NZ_KEY)), label="Eigen NZB")
    add_direct(
        admin, serve(mock_nz.create_app(apikey=TZ_KEY, protocol="torrent")), kind="torznab", key=TZ_KEY,
        label="Eigen torrent",
    )


def test_registration_change_set_flow(
    admin: Any, prowlarr: tuple[Any, str], db: Any, manage_mode: None, caplog: Any, own_sources: None
) -> None:
    caplog.set_level(logging.DEBUG)
    mock, _ = prowlarr
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    tags_before = [dict(t) for t in mock.state.data["tags"]]
    apps_before = [dict(a) for a in mock.state.data["applications"]]
    r = admin.post("/api/v1/indexers/prowlarr/register")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "preview"
    ch = res["change"]
    assert ch["risk"] == "toestemming_nodig" and ch["status"] == "preview" and ch["target"] == "prowlarr"
    assert [op["path"] for op in ch["ops"]] == ["indexer:newznab", "indexer:torznab"]
    assert "URL: http://mso.lan:8000/newznab" in ch["ops"][0]["after"]
    assert ch["ops"][0]["after"].startswith("Naam: Media Source Optimizer (Newznab)\nType: Newznab")
    # compact Dutch impact; the raw Prowlarr payload only in the response (technische details)
    imp = ch["impact"]["Te registreren"]["Newznab"]
    assert imp["URL"] == "http://mso.lan:8000/newznab/api" and imp["Tags"] == "anime, normal"
    assert imp["Prioriteit"] == 25 and "Eigen NZB" in imp["Bronnen achter het endpoint"]
    assert "payloads" not in ch["impact"] and "sortName" not in str(ch)
    payload = res["payloads"]["newznab"]
    api_field = next(f for f in payload["fields"] if f["name"] == "apiKey")
    assert api_field["value"].startswith("***")
    # built from the *generic* schema entry, never a preset's identity (abNZB / AnimeTosho)
    for kind in ("newznab", "torznab"):
        p = res["payloads"][kind]
        assert not {"sortName", "added", "description", "infoLink", "presets", "id"} & set(p)
        assert p["name"].startswith("Media Source Optimizer") and p["indexerUrls"] == []
        assert p["configContract"] == ("NewznabSettings" if kind == "newznab" else "TorznabSettings")
        assert p["appProfileId"] == 1 and p["priority"] == 25 and sorted(p["tags"]) == [1, 2]
    from mso.vault import get_vault

    key = get_vault().get("newznab.apikey")
    assert key and key not in str(db.fetchall("SELECT * FROM change_sets")) and key not in r.text
    # dedupe: proposing again returns the same open change
    assert admin.post("/api/v1/indexers/prowlarr/register").json()["change"]["id"] == ch["id"]
    # toestemming_nodig: apply without approval refused
    assert admin.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 409
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    regs = [i for i in mock.state.data["indexers"] if i["name"].startswith("Media Source Optimizer")]
    assert len(regs) == 2
    posts = [c for c in mock.state.calls if c["method"] in ("POST", "PUT")]
    assert posts and all("forceSave" not in c["query"] for c in posts)  # never bypass Prowlarr's validation
    assert not [i for i in regs if "animetosho" in str(i).lower() or "abnzb" in str(i).lower()]
    nz = next(i for i in regs if i["implementation"] == "Newznab")
    fields = {f["name"]: f["value"] for f in nz["fields"]}
    assert fields["baseUrl"] == "http://mso.lan:8000/newznab" and fields["apiKey"] == key
    assert sorted(nz["tags"]) == [1, 2]  # existing scope tags used, never created
    assert mock.state.data["tags"] == tags_before and mock.state.data["applications"] == apps_before
    # idempotent: nothing more to do
    assert admin.post("/api/v1/indexers/prowlarr/register").json()["status"] == "already_registered"
    # sync recognises MSO's own registration and never searches it
    admin.post("/api/v1/indexers/prowlarr/sync")
    own = db.fetchall("SELECT * FROM sources WHERE origin='prowlarr' AND (meta->>'is_mso')::boolean")
    assert len(own) == 2 and all(not s["enabled"] for s in own)
    # rollback removes exactly what MSO created
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "test"})
    assert r.status_code == 200, r.text
    assert not [i for i in mock.state.data["indexers"] if i["name"].startswith("Media Source Optimizer")]
    assert len(mock.state.data["indexers"]) == 4
    assert key not in caplog.text and PROWLARR_KEY not in caplog.text


def test_duplicate_registration_detected(
    admin: Any, viewer: Any, prowlarr: tuple[Any, str], own_sources: None
) -> None:
    mock, _ = prowlarr
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    for i in (50, 51):
        mock.state.data["indexers"].append(
            {
                "id": i,
                "name": f"MSO kopie {i}",
                "implementation": "Newznab",
                "protocol": "usenet",
                "enable": True,
                "tags": [],
                "fields": mock_prowlarr._fields("http://mso.lan:8000/newznab", "/api", "x"),
            }
        )
    view = viewer.get("/api/v1/indexers/prowlarr/view").json()
    assert any(c["code"] == "mso_registered_twice" and c["severity"] == "error" for c in view["conflicts"])
    r = admin.post("/api/v1/indexers/prowlarr/register", json={"kinds": ["newznab"]})
    assert r.status_code == 409


def test_applier_refuses_foreign_paths(app: Any, prowlarr: tuple[Any, str]) -> None:
    from mso import changes

    applier = changes.APPLIERS["prowlarr"]()
    with pytest.raises(ValueError):
        applier.validate("tag:anime", {"label": "x"})
    with pytest.raises(ValueError):
        applier.validate("application:1", None)


def test_prowlarr_endpoints_require_roles(client: Any, viewer: Any, prowlarr: tuple[Any, str]) -> None:
    assert client.get("/api/v1/indexers/prowlarr/view").status_code == 401
    assert viewer.post("/api/v1/indexers/prowlarr/sync").status_code == 403
    assert viewer.post("/api/v1/indexers/prowlarr/register").status_code == 403
    h = viewer.get("/api/v1/indexers/prowlarr/health").json()
    assert h["health"] and h["indexer_status"][0]["indexerId"] == 4 and h["history"]
