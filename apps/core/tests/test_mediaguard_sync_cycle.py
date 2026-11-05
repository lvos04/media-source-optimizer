"""Field report 2: "MediaGuard-cache van 11 uur geleden" while the overview said "5 min". A wanted list larger
than one run's page limit is read over several runs (resume cursor); every run records its time; items no
longer reported by MediaGuard are dropped once a full cycle has passed; both screens label precisely what the
time means (wanted-list sync vs. status check)."""

from __future__ import annotations

from typing import Any

import pytest
from mg_support import configure, start_mock


@pytest.fixture()
def mg(serve: Any, admin: Any) -> Any:
    app, url = start_mock(serve)
    configure(admin, url)
    return app


def test_partial_sync_resumes_and_completes(mg: Any, monkeypatch: Any, viewer: Any, db: Any) -> None:
    from mso import settings_registry as sr
    from mso.features.mediaguard import service

    n = len(mg.state.wanted)
    assert n >= 6
    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    sr.put({"mediaguard.sync_max_pages": 2}, "test")  # 4 items per run
    r1 = service.sync()
    assert r1["ok"] and r1["complete"] is False and r1["items"] == 4 and r1["next_page"] == 3
    st = service.snap_data("sync_state")
    assert st["last_success_at"] == r1["finished_at"] and service.sync_in_progress(st)
    # the recovery list and the overview show the wanted-list sync time, plus the partial note
    lst = viewer.get("/api/v1/recovery/wanted").json()
    assert lst["mediaguard_synced_at"] == r1["finished_at"] and lst["mediaguard_sync"]["partial"] is True
    ov = viewer.get("/api/v1/overview").json()["mediaguard"]
    assert ov["wanted_synced_at"] == r1["finished_at"] and ov["wanted_sync_partial"] is True
    assert ov["wanted_sync_s"] is not None and ov["wanted_sync_s"] < 60
    runs = 1
    while service.sync_in_progress():
        res = service.sync()
        assert res["ok"]
        runs += 1
        assert runs < 10
    assert runs == -(-n // 4)
    assert db.scalar("SELECT count(*) FROM mg_wanted_cache") == n
    st = service.snap_data("sync_state")
    assert st["cycle_items"] == n and st["last_complete_at"] and st["next_page"] == 1
    # an item that disappears in MediaGuard is dropped after it went unseen for two multi-run cycles
    gone = mg.state.wanted[-1]["id"]
    mg.state.wanted = mg.state.wanted[:-1]
    for _ in range(2):
        service.sync()
        while service.sync_in_progress():
            service.sync()
    assert db.scalar("SELECT count(*) FROM mg_wanted_cache WHERE mg_id=%s", (gone,)) == 0
    assert db.scalar("SELECT count(*) FROM mg_wanted_cache") == n - 1


def test_partial_cycle_continues_at_next_tick(mg: Any, monkeypatch: Any, worker: Any) -> None:
    from mso import jobs
    from mso import settings_registry as sr
    from mso.features.mediaguard import service

    monkeypatch.setattr(service, "PAGE_SIZE", 2)
    sr.put({"mediaguard.sync_max_pages": 1}, "test")
    service.sync()
    assert service.sync_in_progress()
    # interval is 15 min, but an unfinished cycle continues at the next 5-minute tick
    st = service.snap_data("sync_state")
    from datetime import UTC, datetime, timedelta

    st["finished_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    service.snap_put("sync_state", st)
    jobs.enqueue("mediaguard.sync", {})
    worker.drain()
    assert service.snap_data("sync_state")["next_page"] == 3
