"""1.5.0 · Autopilot for failed downloads of MediaGuard-owned items: MSO asks MediaGuard to search again via its
official route — only in Autopilot, within MSO's + MediaGuard's quota, idempotent, MediaGuard stays owner (never a
direct send). Items MediaGuard no longer wants are skipped silently (counted). Skipped items are re-evaluated when
a server changes state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mg_support import assert_only_allowed_writes, configure, start_mock

FAILED = "Lost.Signal.S02E05.1080p.WEB-DL.H.264-BAD"


@pytest.fixture()
def mg(serve: Any, admin: Any, operator: Any, worker: Any) -> Any:
    app, url = start_mock(serve)
    configure(admin, url)
    r = operator.post("/api/v1/mediaguard/sync")
    assert r.status_code == 200, r.text
    worker.drain()
    return app


def _failed(title: str, ref: str, reason: str = "incomplete") -> int:
    from mso.domain import outcomes

    return outcomes.upsert_outcome(
        {"protocol": "usenet", "client": "sabnzbd", "client_ref": ref, "release_key": f"nzb:{ref}", "title": title,
         "client_category": "tv", "media_category": "tv", "status": "failed", "failure_reason": reason,
         "finished_at": datetime.now(UTC), "meta": {}}
    )


def _searches(mock: Any) -> list[Any]:
    return [c for c in mock.state.calls if c[0] == "POST" and c[1].endswith("/search")]


def _run() -> dict[str, Any]:
    from mso.features.recovery import fallback

    return fallback.process_failed()


def test_mediaguard_search_only_in_autopilot(app: Any, db: Any, mg: Any, viewer: Any, admin: Any) -> None:
    from mso import state
    from mso.features.recovery import fallback

    _failed(FAILED, "nzo_a")
    state.set_state("mode", "manage")
    out = _run()
    assert out.get("skipped") == 1 and not _searches(mg)
    fb = viewer.get("/api/v1/recovery/fallbacks").json()["items"][0]
    assert fb["status"] == "skipped" and fb["media_ref"] == "mg:1" and "MediaGuard is gekoppeld" in fb["reason"]

    # Autopilot: re-evaluated → MediaGuard is asked to search via its own route (no direct send)
    state.set_state("mode", "autopilot")
    assert fallback.reevaluate() == 1
    out = _run()
    assert out.get("mg_requested") == 1, out
    assert len(_searches(mg)) == 1 and _searches(mg)[0][1] == "/api/v1/wanted/1/search"
    fb = viewer.get("/api/v1/recovery/fallbacks").json()["items"][0]
    assert fb["status"] == "mg_requested" and fb["change_id"] is None
    req = db.fetchone("SELECT * FROM mg_search_requests ORDER BY id DESC LIMIT 1")
    assert req["trigger"] == "auto" and req["status"] == "sent" and req["actor"] == "autopilot:herstel"
    assert db.scalar("SELECT count(*) FROM change_sets WHERE target='download_send'") == 0
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='mediaguard.search_requested'") == 1
    # idempotent: another failure of the same item inside the window is not sent twice
    _failed("Lost.Signal.S02E05.720p.WEB-DL.H.264-OTHER", "nzo_b")
    _run()
    assert len(_searches(mg)) == 1
    assert_only_allowed_writes(mg)


def test_autopilot_search_respects_quotas(app: Any, db: Any, mg: Any, admin: Any, operator: Any) -> None:
    from mso import state

    assert admin.put("/api/v1/settings", json={"values": {"mediaguard.search_quota_per_hour": 1}}).status_code == 200
    assert operator.post("/api/v1/mediaguard/wanted/2/search").status_code == 200  # uses MSO's hourly quota
    state.set_state("mode", "autopilot")
    _failed(FAILED, "nzo_q")
    out = _run()
    assert out.get("waiting") == 1 and len(_searches(mg)) == 1
    row = db.fetchone("SELECT * FROM recovery_fallbacks")
    assert row["status"] == "waiting" and "MSO-zoekquotum" in row["reason"] and row["next_attempt_at"]


def test_mediaguard_quota_reserve_respected_in_autopilot(
    app: Any, db: Any, serve: Any, admin: Any, operator: Any, worker: Any
) -> None:
    from mso import state

    mock, url = start_mock(serve, quota_exhausted=1)
    configure(admin, url)
    operator.post("/api/v1/mediaguard/sync")
    worker.drain()
    state.set_state("mode", "autopilot")
    _failed(FAILED, "nzo_x")
    out = _run()
    assert out.get("waiting") == 1 and not _searches(mock)


def test_not_wanted_items_skipped_silently(app: Any, db: Any, mg: Any, viewer: Any) -> None:
    from mso import state

    state.set_state("mode", "autopilot")
    _failed("Some.Unknown.Show.S07E09.1080p.WEB-DL.H.264-GRP", "nzo_nw")
    out = _run()
    assert out.get("not_wanted") == 1 and not _searches(mg)
    body = viewer.get("/api/v1/recovery/fallbacks").json()
    assert body["items"] == [] and body["summary"]["not_wanted"] == 1
    assert body["settings"]["autopilot_fallback"] is True
    shown = viewer.get("/api/v1/recovery/fallbacks?include_silent=true").json()["items"]
    assert len(shown) == 1 and shown[0]["reason"].startswith("Niet (meer) gezocht in MediaGuard")


def test_auto_search_refused_outside_autopilot_without_setting(app: Any, mg: Any) -> None:
    from fastapi import HTTPException

    from mso import state
    from mso.features.mediaguard import service

    state.set_state("mode", "manage")
    with pytest.raises(HTTPException) as exc:
        service.request_search(1, actor="x", trigger="auto")
    assert exc.value.status_code == 409 and not _searches(mg)
