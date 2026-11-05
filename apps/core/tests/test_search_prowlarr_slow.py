"""1.4.0 — slow Prowlarr: one automatic retry with a longer time-out when the time budget allows, an explicit
"opnieuw proberen (ruimere time-out)" (``slow=1``), identical in-flight searches coalesced, per-user concurrency."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from msomocks import prowlarr as mock_prowlarr
from slice_a_helpers import PROWLARR_KEY, configure_prowlarr, report, search, set_settings


@pytest.fixture()
def prowlarr(serve: Any) -> Any:
    mock = mock_prowlarr.create_app(api_key=PROWLARR_KEY)
    configure_prowlarr(serve(mock))
    return mock


def test_automatic_retry_with_longer_timeout_succeeds(viewer: Any, prowlarr: Any) -> None:
    set_settings({"search.timeout_s": 2, "search.prowlarr_retry_timeout_s": 6, "search.time_budget_s": 10})
    prowlarr.state.faults["timeout_once"] = "3"
    res = search(viewer, q="Expanse", category="tv")
    rep = report(res, "prowlarr")
    assert rep["status"] in ("ok", "empty"), rep
    assert "opnieuw proberen met ruimere time-out" in rep["message"]
    assert prowlarr.state.search_calls == 2


def test_no_retry_without_budget_reports_slow(viewer: Any, prowlarr: Any) -> None:
    set_settings({"search.timeout_s": 2, "search.prowlarr_retry_timeout_s": 6, "search.time_budget_s": 3})
    prowlarr.state.faults["timeout"] = "3"
    t0 = time.monotonic()
    res = search(viewer, q="Expanse", category="tv")
    rep = report(res, "prowlarr")
    assert rep["status"] == "timeout" and rep["message"].startswith("Prowlarr reageert traag")
    assert time.monotonic() - t0 < 6
    assert prowlarr.state.search_calls == 1


def test_retry_button_uses_longer_timeout(viewer: Any, prowlarr: Any) -> None:
    set_settings({"search.timeout_s": 2, "search.prowlarr_retry_timeout_s": 6, "search.time_budget_s": 3})
    prowlarr.state.faults["timeout"] = "3"
    res = search(viewer, q="Expanse", category="tv", slow=True)
    assert report(res, "prowlarr")["status"] in ("ok", "empty")
    assert prowlarr.state.search_calls == 1


def test_identical_inflight_searches_are_coalesced(app: Any, prowlarr: Any) -> None:
    from mso.features.search import service
    from mso.features.search.models import SearchQuery

    set_settings({"search.timeout_s": 5})
    prowlarr.state.faults["timeout"] = "1"
    results: list[Any] = []

    def run() -> None:
        results.append(service.search(SearchQuery(q="Expanse", media_category="tv", kind="tvsearch"), actor="a"))

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
        time.sleep(0.05)
    for t in threads:
        t.join(20)
    assert len(results) == 3
    assert prowlarr.state.search_calls == 1  # one fan-out, three answers
    assert len({r.trace_id for r in results}) == 1


def test_per_user_concurrency_limit(app: Any, viewer: Any, prowlarr: Any) -> None:
    from mso.features.search import service
    from mso.features.search.models import SearchQuery

    set_settings({"search.timeout_s": 5, "search.max_concurrent_per_user": 1})
    prowlarr.state.faults["timeout"] = "1.5"
    t = threading.Thread(
        target=lambda: service.search(
            SearchQuery(q="Expanse", media_category="tv", kind="tvsearch"), actor="kijker", per_actor_limit=1
        )
    )
    t.start()
    time.sleep(0.3)
    r = viewer.get("/api/v1/search", params={"q": "Dune", "category": "movies"})
    t.join(20)
    assert r.status_code == 429 and "zoekopdracht(en) lopen" in r.json()["detail"]
    assert viewer.get("/api/v1/search", params={"q": "Dune", "category": "movies"}).status_code == 200
