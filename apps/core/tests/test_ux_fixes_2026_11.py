"""Regression tests for the real-hardware test report of 2026-11-04 (UI/UX round):
- H3: MediaGuard quota in MSO is refreshed after a successful "request search" (live re-read, local fallback);
- P6: the recovery wanted list pages consistently over manual + MediaGuard items (default 50, max 200);
- P1: the operating mode can be switched back and forth after setup (admin only, audited in Dutch);
- P11: health component messages are Dutch, never raw `healthy`/`degraded`."""

from __future__ import annotations

from typing import Any

import pytest
from mg_support import assert_only_allowed_writes, configure, start_mock


@pytest.fixture()
def mg(serve: Any, admin: Any) -> tuple[Any, str]:
    app, url = start_mock(serve)
    configure(admin, url)
    return app, url


def _sync(operator: Any, worker: Any) -> None:
    r = operator.post("/api/v1/mediaguard/sync")
    assert r.status_code == 200, r.text
    worker.drain()


# ------------------------------------------------------------------ H3 quota refresh
def test_quota_snapshot_refreshed_after_search_request(mg: Any, viewer: Any, operator: Any, worker: Any) -> None:
    mock, _ = mg
    _sync(operator, worker)
    assert viewer.get("/api/v1/mediaguard/quota").json()["mediaguard"]["sonarr-series"]["remaining"] == 20
    r = operator.post("/api/v1/recovery/items/mg:1/request-search")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["request"]["status"] == "sent"
    # the response and the quota endpoint both show MediaGuard's quota after the search
    assert body["quota"]["mediaguard"]["sonarr-series"]["remaining"] == 19
    q = viewer.get("/api/v1/mediaguard/quota").json()
    assert q["mediaguard"]["sonarr-series"]["remaining"] == 19
    assert q["mso"]["hour_used"] == 1
    assert_only_allowed_writes(mock)


def test_quota_counted_locally_when_reread_fails(
    mg: Any, viewer: Any, operator: Any, worker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mso.connectors import mediaguard as mgc
    from mso.features.mediaguard import service

    _sync(operator, worker)
    real = service._live_mg_quota
    calls = {"n": 0}

    def flaky(c: Any, instance_id: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:  # the pre-request check works …
            return real(c, instance_id)
        raise mgc.MediaGuardError("offline", "MediaGuard tijdelijk onbereikbaar")  # … the re-read afterwards fails

    monkeypatch.setattr(service, "_live_mg_quota", flaky)
    r = operator.post("/api/v1/mediaguard/wanted/1/search")
    assert r.status_code == 200, r.text
    assert calls["n"] == 2
    q = viewer.get("/api/v1/mediaguard/quota").json()["mediaguard"]["sonarr-series"]
    assert q["remaining"] == 19 and q["hour_used"] == 1


# ------------------------------------------------------------------ P6 pagination
def test_wanted_list_pages_do_not_overlap(mg: Any, viewer: Any, operator: Any, worker: Any) -> None:
    mock, _ = mg
    _sync(operator, worker)
    for n in (1, 2, 3):
        r = operator.post(
            "/api/v1/recovery/wanted",
            json={"title": f"Handmatig {n}", "media_category": "tv", "season_number": 1, "episode_number": n},
        )
        assert r.status_code == 200, r.text
    mg_total = len(mock.state.wanted)
    first = viewer.get("/api/v1/recovery/wanted?page=1&page_size=2").json()
    assert first["total"] == mg_total + 3 and first["page_size"] == 2 and len(first["items"]) == 2
    seen: list[str] = []
    page = 1
    while True:
        d = viewer.get(f"/api/v1/recovery/wanted?page={page}&page_size=2").json()
        if not d["items"]:
            break
        assert len(d["items"]) <= 2
        seen += [i["media_ref"] for i in d["items"]]
        page += 1
    assert len(seen) == len(set(seen)) == mg_total + 3
    # manual items first (newest first), then MediaGuard
    assert [s.split(":")[0] for s in seen[:3]] == ["manual"] * 3
    assert all(s.startswith("mg:") for s in seen[3:])
    assert viewer.get("/api/v1/recovery/wanted?page_size=200").status_code == 200
    assert viewer.get("/api/v1/recovery/wanted?page_size=201").status_code == 422
    assert viewer.get("/api/v1/recovery/wanted").json()["page_size"] == 50


# ------------------------------------------------------------------ P1 mode switch
def test_mode_switch_both_ways_audited(admin: Any, operator: Any, viewer: Any) -> None:
    assert admin.put("/api/v1/system/mode", json={"mode": "manage"}).json()["mode"] == "manage"
    assert operator.put("/api/v1/system/mode", json={"mode": "read_only"}).status_code == 403
    r = admin.put("/api/v1/system/mode", json={"mode": "read_only"})
    assert r.status_code == 200 and r.json()["mode"] == "read_only"
    assert viewer.get("/api/v1/system/mode").json()["mode"] == "read_only"
    items = admin.get("/api/v1/audit?action=system.mode").json()["items"]
    assert items[0]["summary"] == "Bedrijfsmodus: Beheermodus → Alleen-lezen"


# ------------------------------------------------------------------ P11 Dutch health messages
def test_health_messages_are_dutch(admin: Any, viewer: Any, db: Any) -> None:
    db.execute(
        "UPDATE connectors SET url='http://192.168.1.10:9696', enabled=true, status='degraded', "
        "last_error=NULL WHERE id='prowlarr'"
    )
    comps = viewer.get("/api/v1/system/health").json()["components"]
    prowlarr = next(c for c in comps if c["name"] == "connector:prowlarr")
    assert prowlarr["message"] == "Verstoord"
    assert all(c.get("message") not in ("healthy", "degraded", "offline") for c in comps)
