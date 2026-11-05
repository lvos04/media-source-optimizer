"""PLAN §7 test 4 (part 2): SABnzbd server priorities read-only → dry-run (preview) → apply → revert, with
drift detection, verification failure + auto-restore and recommendation logic (block account last, expired
disabled)."""

from __future__ import annotations

from typing import Any

import pytest

SAB_KEY = "SABGEHEIM-0123456789"


def _start(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    url = serve(mock_app)
    assert (
        admin.put(
            "/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": SAB_KEY}}
        ).status_code
        == 200
    )
    return mock_app.state.sab


@pytest.fixture()
def sab(app: Any, serve: Any, admin: Any) -> Any:
    return _start(serve, admin)


def _providers(admin: Any) -> None:
    for slug, label, kind, server in (
        ("eweka", "Eweka", "unlimited", "news.eweka.test"),
        ("newshosting", "Newshosting", "unlimited", "news.newshosting.test"),
        ("blok", "Blok", "block", "block.example.test"),
    ):
        r = admin.post(
            "/api/v1/usenet/providers",
            json={
                "slug": slug,
                "label": label,
                "kind": kind,
                "host": "127.0.0.1",
                "port": 1119,
                "ssl": False,
                "connections": 10,
                "sab_server": server,
                "username": "u",
                "password": "p",
            },
        )
        assert r.status_code == 200, r.text


def _prio(sab: Any) -> dict[str, int]:
    return {s["name"]: s["priority"] for s in sab.servers}


def test_priority_readonly_preview_apply_revert(admin: Any, operator: Any, viewer: Any, sab: Any) -> None:
    _providers(admin)
    original = _prio(sab)
    # 1) read-only: suggestions are computed without writing anything
    sug = viewer.get("/api/v1/usenet/recommendations").json()
    assert sug["risk"] == "toestemming_nodig"
    ops = {o["path"]: o for o in sug["ops"]}
    assert ops["servers/block.example.test/priority"]["after"] == 2  # block account as last resort
    assert "servers/news.eweka.test/priority" not in ops  # no coverage → current order kept
    assert any("Onvoldoende dekking" in n for n in sug["notes"])
    assert _prio(sab) == original
    # 2) dry-run: an admin turns the suggestion into a change set in 'preview'
    assert operator.post("/api/v1/usenet/recommendations/priority").status_code == 403
    ch = admin.post("/api/v1/usenet/recommendations/priority").json()
    assert ch["status"] == "preview" and ch["target"] == "sabnzbd" and ch["risk"] == "toestemming_nodig"
    assert [(o["path"], o["before"], o["after"]) for o in ch["ops"]] == [
        ("servers/block.example.test/priority", 0, 2)
    ]
    # idempotent while open
    assert admin.post("/api/v1/usenet/recommendations/priority").json()["id"] == ch["id"]
    assert _prio(sab) == original
    # read-only mode refuses apply
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 409 and "alleen-lezen" in r.json()["detail"]
    assert _prio(sab) == original
    # 3) apply in beheermodus
    from mso import state

    state.set_state("mode", "manage")
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    assert _prio(sab)["block.example.test"] == 2
    # 4) revert restores the original server settings
    r = operator.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "test"})
    assert r.status_code == 200 and r.json()["status"] == "rolled_back"
    assert _prio(sab) == original


def test_toestemming_nodig_requires_approval(admin: Any, operator: Any, sab: Any, manage_mode: None) -> None:
    _providers(admin)
    ch = admin.post("/api/v1/usenet/recommendations/priority").json()
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 403 and "Goedkeuring" in r.json()["detail"]
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")  # even an admin must approve first
    assert r.status_code == 409 and "goedkeuring" in r.json()["detail"]


def test_drift_detected_when_sab_changed_meanwhile(admin: Any, sab: Any, manage_mode: None) -> None:
    ch = admin.post(
        "/api/v1/usenet/recommendations/priority",
        json={"ops": [{"path": "servers/news.newshosting.test/priority", "after": 5}]},
    ).json()
    assert ch["ops"][0]["before"] == 1
    sab.servers[1]["priority"] = 3  # somebody else changes SAB after the preview
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 409 and "Afwijking" in r.json()["detail"]
    assert sab.servers[1]["priority"] == 3


def test_verification_failure_restores(app: Any, serve: Any, admin: Any, manage_mode: None) -> None:
    sab = _start(serve, admin, ignore_set_config="1")
    ch = admin.post(
        "/api/v1/usenet/recommendations/priority",
        json={"ops": [{"path": "servers/news.eweka.test/enable", "after": 0}]},
    ).json()
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 502
    assert admin.get(f"/api/v1/changes/{ch['id']}").json()["status"] == "failed"
    assert sab.servers[0]["enable"] == 1


def test_only_whitelisted_fields_and_known_servers(admin: Any, sab: Any) -> None:
    r = admin.post(
        "/api/v1/usenet/recommendations/priority",
        json={"ops": [{"path": "servers/news.eweka.test/connections", "after": 50}]},
    )
    assert r.status_code == 422
    r = admin.post(
        "/api/v1/usenet/recommendations/priority",
        json={"ops": [{"path": "servers/bestaat.niet/priority", "after": 1}]},
    )
    assert r.status_code == 422
    r = admin.post(
        "/api/v1/usenet/recommendations/priority",
        json={"ops": [{"path": "servers/news.eweka.test/priority", "after": 0}]},
    )
    assert r.status_code == 409  # already so


def test_policy_rejects_out_of_range_value(app: Any, sab: Any) -> None:
    from fastapi import HTTPException

    from mso import changes

    with pytest.raises(HTTPException) as exc:
        changes.create(
            "sabnzbd",
            "x",
            [{"path": "servers/news.eweka.test/priority", "after": 500}],
            risk="toestemming_nodig",
        )
    assert exc.value.status_code == 422


def test_expired_provider_gets_disable_suggestion(admin: Any, db: Any, sab: Any) -> None:
    _providers(admin)
    admin.get("/api/v1/usenet/sab/servers")  # SABnzbd's status is observed first …
    # … then MSO's own (newer) test with credentials fails: that counts, even though SABnzbd says "ok"
    db.execute(
        "UPDATE usenet_providers SET status='account_expired', last_check_at=now() WHERE slug='newshosting'"
    )
    sug = admin.get("/api/v1/usenet/recommendations").json()
    ops = {o["path"]: o for o in sug["ops"]}
    assert ops["servers/news.newshosting.test/enable"]["after"] == 0
    assert ops["servers/news.newshosting.test/enable"]["reason"] == "account verlopen"
    # block goes right after the remaining unlimited provider
    assert ops["servers/block.example.test/priority"]["after"] == 1


def test_availability_based_order_needs_coverage(admin: Any, db: Any, sab: Any) -> None:
    """With enough compared articles where Newshosting has better availability, it is proposed first."""
    from mso import settings_registry as sr

    _providers(admin)
    sr.put({"usenet.min_articles_for_recommendation": 20}, "test")
    nid = db.scalar("INSERT INTO usenet_nzbs(origin,title) VALUES ('upload','x') RETURNING id")
    tid = db.scalar(
        "INSERT INTO usenet_article_tests(nzb_id,trigger,providers,sample_size,status) "
        "VALUES (%s,'manual',ARRAY['eweka','newshosting'],30,'done') RETURNING id",
        (nid,),
    )
    for i in range(30):
        db.execute(
            "INSERT INTO usenet_article_results(test_id,provider,message_id,available) VALUES "
            "(%s,'eweka',%s,%s),(%s,'newshosting',%s,true)",
            (tid, f"a{i}@x", i % 3 != 0, tid, f"a{i}@x"),
        )
    sug = admin.get("/api/v1/usenet/recommendations").json()
    assert sug["method"] == "explicit_test"
    ops = {o["path"]: o["after"] for o in sug["ops"]}
    assert ops["servers/news.newshosting.test/priority"] == 0 and ops["servers/news.eweka.test/priority"] == 1
    assert any(i["kind"] == "unique" and i["slug"] == "newshosting" for i in sug["insights"])
