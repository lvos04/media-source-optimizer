"""PLAN §7 test 7 — MediaGuard online/offline: capability detection, safe fallback (standalone), no duplicate
download/wanted agents (MSO only ever calls the read routes + POST /api/v1/wanted/{id}/search),
idempotency and
quota. Exercised over real HTTP against the MediaGuard mock (shapes from the reference source @ e92c2ac)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mg_support import (
    VIEWER,
    assert_only_allowed_writes,
    configure,
    start_mock,
    writes,
)
from msomocks import mediaguard as mgmock

from mso.connectors import base as cb
from mso.connectors import mediaguard as mgc


def _search_calls(mock: Any) -> list[Any]:
    return [c for c in mock.state.calls if c[0] == "POST" and c[1].endswith("/search")]


@pytest.fixture()
def mg(serve: Any, admin: Any) -> tuple[Any, str]:
    app, url = start_mock(serve)
    configure(admin, url)
    return app, url


def _sync(operator: Any, worker: Any) -> None:
    r = operator.post("/api/v1/mediaguard/sync")
    assert r.status_code == 200, r.text
    worker.drain()


# ------------------------------------------------------------------ modes / capability detection
def test_standalone_without_mediaguard(viewer: Any, operator: Any) -> None:
    st = viewer.get("/api/v1/mediaguard/status").json()
    assert st["mode"] == "standalone" and st["configured"] is False
    assert st["search"]["available"] is False
    assert all(r["live"] == "onbekend" for r in st["compat"]["routes"])
    # all recovery features keep working
    assert viewer.get("/api/v1/recovery/baseline").status_code == 200
    assert viewer.get("/api/v1/recovery/wanted").json()["items"] == []
    r = operator.post(
        "/api/v1/recovery/wanted",
        json={"title": "Show", "media_category": "tv", "season_number": 1, "episode_number": 2},
    )
    assert r.status_code == 200, r.text
    assert operator.post("/api/v1/mediaguard/sync").status_code == 409


def test_connected_handshake_and_compat_matrix(mg: Any, viewer: Any, operator: Any) -> None:
    mock, _ = mg
    t = operator.post("/api/v1/connectors/mediaguard/test").json()
    assert t["ok"] is True and t["status"] == "healthy", t
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "connected"
    assert st["version"] == mgmock.VERSION and st["role"] == "operator" and st["auth_method"] == "user"
    assert st["search"]["available"] is True
    compat = st["compat"]
    assert compat["reference"]["commit"] == "e92c2ac" and compat["live_source"] == "openapi"
    rows = {r["id"]: r for r in compat["routes"]}
    assert rows["wanted_search"]["method"] == "POST" and rows["wanted_search"]["role"] == "operator"
    assert all(r["verified"] for r in compat["routes"])
    assert all(r["live"] == "aanwezig" for r in compat["routes"])
    assert any("retry-relaxed" in n["path"] for n in compat["never_used"])
    assert viewer.get("/api/v1/mediaguard/compat").json()["routes"]
    assert_only_allowed_writes(mock)


def test_offline_falls_back_to_standalone(serve: Any, admin: Any, viewer: Any, operator: Any) -> None:
    mock, url = start_mock(serve, offline=1)
    configure(admin, url)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "standalone" and st["configured"] is True and st["reachable"] is False
    assert "niet bereikbaar" in st["reason"]
    t = operator.post("/api/v1/connectors/mediaguard/test").json()
    assert t["ok"] is False and t["status"] == "offline"
    # closed port behaves the same
    configure(admin, "http://127.0.0.1:9")
    assert viewer.get("/api/v1/mediaguard/status?refresh=true").json()["mode"] == "standalone"
    r = operator.post("/api/v1/recovery/items/mg:1/request-search")
    assert r.status_code in (404, 409)


def test_auth_failure_is_degraded(serve: Any, admin: Any, viewer: Any, operator: Any) -> None:
    mock, url = start_mock(serve, auth_fail=1)
    configure(admin, url)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "degraded" and "authenticatie" in st["reason"]
    t = operator.post("/api/v1/connectors/mediaguard/test").json()
    assert t["status"] == "auth_failed"
    assert_only_allowed_writes(mock)


def test_old_version_capability_detection(
    serve: Any, admin: Any, viewer: Any, operator: Any, worker: Any
) -> None:
    mock, url = start_mock(serve, old_version=1)
    configure(admin, url)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    rows = {r["id"]: r for r in st["compat"]["routes"]}
    assert rows["wanted_search"]["live"] == "ontbreekt"
    assert rows["backfill_quota"]["live"] == "ontbreekt" and rows["policy_effective"]["live"] == "ontbreekt"
    assert rows["wanted_list"]["live"] == "aanwezig"
    assert st["mode"] == "connected" and st["search"]["available"] is False
    _sync(operator, worker)
    r = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert r.status_code == 409 and "biedt" in r.json()["detail"]
    assert not _search_calls(mock)


def test_capability_probe_without_openapi(serve: Any, admin: Any, viewer: Any) -> None:
    mock, url = start_mock(serve, no_openapi=1)
    configure(admin, url)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["compat"]["live_source"] == "probe"
    rows = {r["id"]: r for r in st["compat"]["routes"]}
    assert rows["wanted_list"]["live"] == "aanwezig" and rows["wanted_item"]["live"] == "aanwezig"
    assert rows["wanted_search"]["live"] == "onbekend"  # write routes are never probed
    assert st["search"]["available"] is False
    assert not writes(mock) or all(c[1] == "/api/v1/auth/login" for c in writes(mock))


def test_emergency_stop_blocks_search_requests(
    serve: Any, admin: Any, viewer: Any, operator: Any, worker: Any
) -> None:
    mock, url = start_mock(serve, emergency_stop=1)
    configure(admin, url)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "degraded" and st["emergency_stop"] is True
    _sync(operator, worker)
    assert operator.post("/api/v1/mediaguard/wanted/1/search").status_code == 409
    assert not _search_calls(mock)


# ------------------------------------------------------------------ sync (read-only cache)
def test_sync_caches_wanted_kpis_reasons_quota(
    mg: Any, viewer: Any, operator: Any, worker: Any, db: Any
) -> None:
    mock, _ = mg
    _sync(operator, worker)
    page = viewer.get("/api/v1/mediaguard/wanted?page_size=100").json()
    assert page["total"] == len(mock.state.wanted)
    cats = {i["media_ref"]: i["media_category"] for i in page["items"]}
    assert cats["mg:1"] == "tv" and cats["mg:4"] == "tv-anime" and cats["mg:6"] == "movies"
    assert cats["mg:8"] == "movies-anime"
    assert viewer.get("/api/v1/mediaguard/kpis").json()["data"]["missing"] >= 1
    reasons = viewer.get("/api/v1/mediaguard/reasons").json()["data"]
    assert reasons["no-indexer-hit"]["label_nl"] == "Geen release gevonden op indexers"
    q = viewer.get("/api/v1/mediaguard/quota").json()
    assert q["mediaguard"]["sonarr-series"]["remaining"] == 20
    st = viewer.get("/api/v1/mediaguard/status").json()
    assert st["last_sync"]["ok"] is True and st["last_sync"]["complete"] is True
    # item resolved in MediaGuard → dropped from the cache on the next full sync (cache is never leading)
    mock.state.wanted = [w for w in mock.state.wanted if w["id"] != 1]
    _sync(operator, worker)
    assert db.scalar("SELECT count(*) FROM mg_wanted_cache WHERE mg_id=1") == 0
    detail = viewer.get("/api/v1/mediaguard/wanted/2").json()
    assert detail["diagnosis"]["primary_code"] == "rejected-quality"
    # only cached diagnosis is ever requested
    diag_calls = [c for c in mock.state.calls if c[1].endswith("/diagnosis")]
    assert diag_calls and all("cached=true" in c[2] for c in diag_calls)
    assert_only_allowed_writes(mock)


def test_session_relogin_after_expiry(mg: Any, viewer: Any, operator: Any, worker: Any) -> None:
    mock, _ = mg
    _sync(operator, worker)
    mock.state.sessions.clear()  # MediaGuard restarted / session expired
    _sync(operator, worker)
    assert viewer.get("/api/v1/mediaguard/status").json()["last_sync"]["ok"] is True
    logins = [c for c in mock.state.calls if c[1] == "/api/v1/auth/login"]
    assert len(logins) >= 2


# ---------------------------------------------------- request search: idempotency, quota, backoff
def test_request_search_idempotent_and_audited(
    mg: Any, operator: Any, viewer: Any, admin: Any, worker: Any
) -> None:
    mock, _ = mg
    _sync(operator, worker)
    assert viewer.post("/api/v1/mediaguard/wanted/1/search").status_code == 403
    r = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicate"] is False and body["request"]["status"] == "sent"
    r2 = operator.post("/api/v1/recovery/items/mg:1/request-search")
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert len(_search_calls(mock)) == 1 and len(mock.state.searches) == 1
    audit = admin.get("/api/v1/audit?action=mediaguard.search_requested").json()
    items = audit["items"] if isinstance(audit, dict) else audit
    assert any(a["action"] == "mediaguard.search_requested" for a in items)
    assert_only_allowed_writes(mock)


def test_mso_quota_respected(mg: Any, operator: Any, admin: Any, worker: Any) -> None:
    mock, _ = mg
    _sync(operator, worker)
    assert (
        admin.put("/api/v1/settings", json={"values": {"mediaguard.search_quota_per_hour": 2}}).status_code
        == 200
    )
    assert operator.post("/api/v1/mediaguard/wanted/1/search").status_code == 200
    assert operator.post("/api/v1/mediaguard/wanted/2/search").status_code == 200
    r = operator.post("/api/v1/mediaguard/wanted/3/search")
    assert r.status_code == 429 and "MSO-zoekquotum" in r.json()["detail"]
    assert len(_search_calls(mock)) == 2


def test_mediaguard_quota_never_exceeded(serve: Any, admin: Any, operator: Any, worker: Any) -> None:
    mock, url = start_mock(serve, quota_exhausted=1)
    configure(admin, url)
    _sync(operator, worker)
    r = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert r.status_code == 429 and "MediaGuard-quotum" in r.json()["detail"]
    assert not _search_calls(mock)  # refused before calling MediaGuard


def test_quota_reserve_left_for_mediaguard_backfill(mg: Any, operator: Any, worker: Any) -> None:
    mock, _ = mg
    _sync(operator, worker)
    mock.state.quota["sonarr-series"]["hour_used"] = 18  # remaining 2 == default reserve
    r = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert r.status_code == 429 and "reserve" in r.json()["detail"]
    mock.state.quota["sonarr-series"]["hour_used"] = 17
    assert operator.post("/api/v1/mediaguard/wanted/1/search").status_code == 200


def test_failed_requests_back_off_exponentially(mg: Any, operator: Any, worker: Any, db: Any) -> None:
    mock, _ = mg
    _sync(operator, worker)
    mock.state.wanted = [w for w in mock.state.wanted if w["id"] != 2]  # gone in MediaGuard, still in cache
    r = operator.post("/api/v1/mediaguard/wanted/2/search")
    assert r.status_code == 404
    bo = db.fetchone("SELECT * FROM mg_search_backoff WHERE mg_id=2")
    assert bo["failures"] == 1 and bo["next_allowed_at"] is not None
    calls_before = len(_search_calls(mock))
    r = operator.post("/api/v1/mediaguard/wanted/2/search")
    assert r.status_code == 429 and "Wacht tot" in r.json()["detail"]
    assert len(_search_calls(mock)) == calls_before  # backoff: MediaGuard not called again
    db.execute("UPDATE mg_search_backoff SET next_allowed_at=now() - interval '1 minute' WHERE mg_id=2")
    operator.post("/api/v1/mediaguard/wanted/2/search")
    first = db.fetchone(
        "SELECT failures, next_allowed_at - last_failure_at AS wait FROM mg_search_backoff WHERE mg_id=2"
    )
    assert first["failures"] == 2 and first["wait"].total_seconds() >= 2 * 30 * 60 - 5


def test_viewer_role_cannot_request(serve: Any, admin: Any, viewer: Any, operator: Any, worker: Any) -> None:
    mock, url = start_mock(serve)
    configure(admin, url, creds=VIEWER)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["role"] == "viewer" and st["search"]["available"] is False
    assert any("viewer" in w for w in st["warnings"])
    _sync(operator, worker)
    assert operator.post("/api/v1/mediaguard/wanted/1/search").status_code == 409


def test_internal_token_warns_admin_scope(serve: Any, admin: Any, viewer: Any) -> None:
    mock, url = start_mock(serve)
    configure(admin, url, creds=None, token=mgmock.INTERNAL_TOKEN)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    assert st["mode"] == "connected" and st["auth_method"] == "token" and st["role"] == "admin"
    assert any("beheerdersrechten" in w for w in st["warnings"])


def test_auto_search_only_when_enabled_and_automation_allowed(
    mg: Any, operator: Any, admin: Any, worker: Any, db: Any
) -> None:
    from mso.features.recovery import tasks

    mock, _ = mg
    _sync(operator, worker)
    db.execute(
        "INSERT INTO recovery_candidates(media_ref,release_key,protocol,title,passed,release,expires_at) "
        "VALUES ('mg:1','nzb:x','usenet','X',true,'{}',now() + interval '1 hour')"
    )
    assert tasks.auto_search_requests()["skipped"]  # default off
    admin.put("/api/v1/settings", json={"values": {"mediaguard.auto_search_requests": True}})
    assert tasks.auto_search_requests()["skipped"]  # read-only mode → not allowed
    assert not _search_calls(mock)
    admin.put("/api/v1/system/mode", json={"mode": "manage"})
    assert tasks.auto_search_requests() == {"requested": 1}
    admin.post("/api/v1/system/pause")
    assert tasks.auto_search_requests()["skipped"]
    assert len(_search_calls(mock)) == 1
    assert_only_allowed_writes(mock)


def test_client_refuses_forbidden_write_routes_before_io() -> None:
    cfg = cb.ConnectorConfig(id="mediaguard", url="http://127.0.0.1:9", secrets={}, options={})
    c = mgc.MediaGuardClient(cfg)
    for path in (
        "/api/v1/wanted/1/retry-relaxed",
        "/api/v1/wanted/actions",
        "/api/v1/wanted/1/ignore",
        "/api/v1/wanted/backfill-plan",
        "/api/v1/hooks/pre-grab",
        "/api/v1/instances/x/sync",
        "/api/v1/recovery/anything",
        "/api/v1/wanted/1/search/../retry-relaxed",
    ):
        with pytest.raises(mgc.WriteRefused):
            c.request("POST", path)
    for method in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(mgc.WriteRefused):
            c.request(method, "/api/v1/wanted/1/search")


def test_secrets_never_exposed(mg: Any, admin: Any, viewer: Any, operator: Any, worker: Any) -> None:
    _sync(operator, worker)
    blobs = [
        admin.get("/api/v1/connectors").text,
        viewer.get("/api/v1/mediaguard/status").text,
        admin.get("/api/v1/audit").text,
        admin.get("/api/v1/jobs").text,
    ]
    for blob in blobs:
        assert "Operator-Wachtwoord1" not in blob
        assert "mg_session=" not in blob
        assert mgmock.INTERNAL_TOKEN not in blob
    conn = next(c for c in admin.get("/api/v1/connectors").json() if c["id"] == "mediaguard")
    assert {f["name"]: f["status"] for f in conn["secret_fields"]}["password"] == "ingesteld"
    assert "password" not in json.dumps(conn.get("options"))
