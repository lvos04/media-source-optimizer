"""1.5.0 · P4: the MediaGuard quota card showed "0 over · 0 bruikbaar" (the copy stored by the last sync, taken when
MediaGuard's own backfill had used the hour) while the detail panel showed "19 over · 17 bruikbaar". The status and
quota endpoints now re-read MediaGuard's quota (read-only) when the cached copy is older than 2 minutes."""

from __future__ import annotations

from typing import Any

from mg_support import assert_only_allowed_writes, configure, start_mock


def test_quota_card_is_refreshed_from_mediaguard(
    app: Any, db: Any, serve: Any, admin: Any, operator: Any, viewer: Any, worker: Any
) -> None:
    mock, url = start_mock(serve)
    configure(admin, url)
    mock.state.quota["sonarr-anime"]["hour_used"] = 20  # MediaGuard's backfill just used the hour
    assert operator.post("/api/v1/mediaguard/sync").status_code == 200
    worker.drain()
    st = viewer.get("/api/v1/mediaguard/status").json()
    assert st["quota"]["mediaguard"]["sonarr-anime"]["remaining"] == 0
    # the hour rolls over in MediaGuard
    mock.state.quota["sonarr-anime"]["hour_used"] = 1
    # within 2 minutes the cached copy is used (no extra calls)
    assert viewer.get("/api/v1/mediaguard/status").json()["quota"]["mediaguard"]["sonarr-anime"]["remaining"] == 0
    db.execute("UPDATE mg_snapshots SET fetched_at = now() - interval '10 minutes' WHERE key='quota'")
    st = viewer.get("/api/v1/mediaguard/status").json()
    q = st["quota"]["mediaguard"]["sonarr-anime"]
    assert q["remaining"] == 19 and st["quota"]["mediaguard_stale"] is False
    # card and detail (GET /quota) agree
    db.execute("UPDATE mg_snapshots SET fetched_at = now() - interval '10 minutes' WHERE key='quota'")
    mock.state.quota["sonarr-anime"]["hour_used"] = 2
    assert viewer.get("/api/v1/mediaguard/quota").json()["mediaguard"]["sonarr-anime"]["remaining"] == 18
    assert_only_allowed_writes(mock)


def test_quota_unreachable_keeps_cached_copy_marked_stale(
    app: Any, db: Any, serve: Any, admin: Any, operator: Any, viewer: Any, worker: Any
) -> None:
    mock, url = start_mock(serve)
    configure(admin, url)
    operator.post("/api/v1/mediaguard/sync")
    worker.drain()
    db.execute("UPDATE mg_snapshots SET fetched_at = now() - interval '10 minutes' WHERE key='quota'")
    db.execute("UPDATE connectors SET url='http://127.0.0.1:9' WHERE id='mediaguard'")
    q = viewer.get("/api/v1/mediaguard/quota").json()
    assert q["mediaguard"]["sonarr-series"]["remaining"] == 20 and q["mediaguard_stale"] is True
