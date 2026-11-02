"""qBittorrent connector: auth (API key / session / re-login), capability detection, no secret leakage."""

from __future__ import annotations

from typing import Any

import pytest

from mso.connectors import base as cb
from mso.connectors.qbittorrent import QbitAuthError, QbitClient, capabilities_for


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    QbitClient.reset_cache()
    yield
    QbitClient.reset_cache()


def mock(**faults: Any) -> Any:
    from msomocks.qbittorrent import create_app

    return create_app(**faults)


def configure(admin: Any, url: str, **secrets: str) -> dict[str, Any]:
    r = admin.put("/api/v1/connectors/qbittorrent", json={"url": url, "secrets": secrets})
    assert r.status_code == 200, r.text
    return r.json()


def test_session_login_and_capabilities(admin: Any, serve: Any) -> None:
    app = mock()
    url = serve(app)
    body = configure(admin, url, username="admin", password="adminadmin")
    assert "adminadmin" not in str(body)
    r = admin.post("/api/v1/connectors/qbittorrent/test")
    res = r.json()
    assert res["ok"] and res["status"] == "healthy", res
    assert res["version"] == "v5.1.2"
    caps = res["capabilities"]
    assert caps["auth_method"] == "session" and caps["webapi"] == "2.11.4"
    assert caps["build_info"] is True and caps["inactive_seeding_time_limit"] is True
    assert caps["api_key"] is False
    # the mock enforced Referer/Origin on login (CSRF) and handed out a SID that is never exposed
    assert app.state.qb.logins == 1
    assert "SID" not in r.text


def test_wrong_password_reports_auth_failed(admin: Any, serve: Any) -> None:
    url = serve(mock())
    configure(admin, url, username="admin", password="fout")
    res = admin.post("/api/v1/connectors/qbittorrent/test").json()
    assert res["ok"] is False and res["status"] == "auth_failed"
    assert "fout" not in res["message"]


def test_auth_fail_fault(serve: Any) -> None:
    url = serve(mock(auth_fail="1"))
    c = QbitClient(url, username="admin", password="adminadmin")
    with pytest.raises(QbitAuthError):
        c.detect()


def test_api_key_used_on_new_version(admin: Any, serve: Any) -> None:
    app = mock(version="5.2.0", api_key="SLEUTEL-123")
    url = serve(app)
    configure(admin, url, api_key="SLEUTEL-123")
    r = admin.post("/api/v1/connectors/qbittorrent/test")
    res = r.json()
    assert res["ok"] and res["capabilities"]["auth_method"] == "api_key"
    assert res["capabilities"]["api_key"] is True
    assert app.state.qb.logins == 0  # never needed a session
    assert "SLEUTEL-123" not in r.text


def test_api_key_falls_back_to_session_on_old_version(admin: Any, serve: Any) -> None:
    app = mock(version="5.0.4", api_key="SLEUTEL-123")
    url = serve(app)
    configure(admin, url, api_key="SLEUTEL-123", username="admin", password="adminadmin")
    res = admin.post("/api/v1/connectors/qbittorrent/test").json()
    assert res["ok"] and res["status"] == "degraded"
    assert res["capabilities"]["auth_method"] == "session"
    assert "API-sleutel geweigerd" in res["message"]


def test_api_key_only_on_old_version_fails_clearly(serve: Any) -> None:
    url = serve(mock(version="5.0.4", api_key="SLEUTEL-123"))
    c = QbitClient(url, api_key="SLEUTEL-123")
    with pytest.raises(QbitAuthError, match="API-sleutel geweigerd"):
        c.detect()


def test_session_expiry_triggers_single_relogin(serve: Any) -> None:
    app = mock(session_ttl="3")
    url = serve(app)
    c = QbitClient(url, username="admin", password="adminadmin")
    c.detect()  # version, webapi, buildInfo = 3 requests
    assert app.state.qb.logins == 1
    torrents = c.torrents()  # 4th request on the same SID → 403 → re-login → retry
    assert len(torrents) == 8
    assert app.state.qb.logins == 2
    assert c.login_count == 2


def test_old_version_capability_detection(serve: Any) -> None:
    app = mock(old_version="1")
    url = serve(app)
    c = QbitClient(url, username="admin", password="adminadmin")
    caps = c.detect()
    assert c.version == "v4.1.9" and c.webapi == "2.2.0"
    assert caps["build_info"] is False and caps["infohash_v2"] is False
    assert caps["inactive_seeding_time_limit"] is False and caps["network_interfaces"] is False
    assert c.network_interfaces() == []
    # share limits still work, without the parameter old versions reject
    h = c.torrents()[0]["hash"]
    c.set_share_limits([h], ratio_limit=2.0, seeding_time_limit=1440)
    assert app.state.qb.torrents[h]["ratio_limit"] == 2.0


def test_capabilities_table() -> None:
    caps = capabilities_for("v5.2.0", "2.12.0", build_info=True)
    assert caps["api_key"] and caps["start_stop_naming"] and caps["private_flag_in_info"]
    old = capabilities_for("v4.3.9", "2.8.2", build_info=True)
    assert not old["api_key"] and not old["infohash_v2"] and not old["start_stop_naming"]


def test_incremental_maindata(serve: Any) -> None:
    app = mock()
    url = serve(app)
    c = QbitClient(url, username="admin", password="adminadmin")
    s = c.maindata()
    assert s.full_updates == 1 and len(s.torrents) == 8 and s.rid > 0
    h = next(iter(app.state.qb.torrents))
    app.state.qb.torrents[h]["dlspeed"] = 12345
    s = c.maindata()
    assert s.full_updates == 1  # incremental diff, not a full update
    assert s.torrents[h]["dlspeed"] == 12345 and len(s.torrents) == 8
    assert "free_space_on_disk" in s.server_state


def test_add_torrent_and_categories(serve: Any) -> None:
    app = mock(scenario="empty")
    url = serve(app)
    c = QbitClient.from_config(
        cb.ConnectorConfig("qbittorrent", url, {"username": "admin", "password": "adminadmin"}, {})
    )
    assert c.categories() == ["movies", "movies-anime", "tv", "tv-anime"]
    c.add_torrent(url="magnet:?xt=urn:btih:" + "a" * 40, category="tv", paused=True, tags=["mso"])
    added = list(app.state.qb.torrents.values())
    assert len(added) == 1 and added[0]["state"] == "stoppedDL" and added[0]["category"] == "tv"
    assert added[0]["skip_checking"] is False


def test_client_has_no_delete_operation() -> None:
    names = [n.lower() for n in dir(QbitClient)]
    assert not any("delete" in n or "remove" in n for n in names)
