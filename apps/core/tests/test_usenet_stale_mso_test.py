"""Field report 2 (P2): an MSO provider test recorded while credentials were missing (pre-1.3.0 stored it as
"Inloggen geweigerd") or an MSO test that is old / older than SABnzbd's latest status never overrides
SABnzbd's current status — not on the provider card and not under "Knelpunten per server"."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mso.features.usenet import providers

NOW = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)


def _row(status: str, checked: datetime | None, detail: str | None = None) -> dict[str, Any]:
    return {"slug": "eweka", "status": status, "status_detail": detail, "last_check_at": checked}


def _sab(status: str, since: datetime) -> dict[str, Any]:
    return {"status": status, "message": None, "explanation": "SABnzbd meldt geen fouten.", "since": since}


def test_missing_credentials_ignores_old_refused_login() -> None:
    row = _row("auth_failed", NOW - timedelta(hours=13), "Gebruikersnaam of wachtwoord ontbreekt")
    t = providers.mso_test(row, _sab("ok", NOW - timedelta(days=3)), False, now=NOW)
    assert t["state"] == "missing_credentials" and t["status"] == "missing_credentials"
    h = providers.health(row, _sab("ok", NOW - timedelta(days=3)), False, now=NOW)
    assert h["status"] == "ok" and h["source"] == "sabnzbd"
    assert providers.own_status(row, _sab("ok", NOW), False) is None


def test_newer_failed_mso_test_with_credentials_still_counts() -> None:
    row = _row("auth_failed", NOW - timedelta(hours=2), "481 Authentication failed")
    sab = _sab("ok", NOW - timedelta(days=3))
    assert providers.mso_test(row, sab, True, now=NOW)["state"] == "current"
    h = providers.health(row, sab, True, now=NOW)
    assert h == {"status": "auth_failed", "detail": "481 Authentication failed", "source": "mso", "stale": False}


def test_sab_status_changed_after_mso_test_wins() -> None:
    row = _row("auth_failed", NOW - timedelta(hours=2), "481 Authentication failed")
    sab = _sab("ok", NOW - timedelta(hours=1))
    assert providers.mso_test(row, sab, True, now=NOW)["state"] == "superseded"
    assert providers.health(row, sab, True, now=NOW)["source"] == "sabnzbd"


def test_mso_test_expires_after_24h() -> None:
    row = _row("auth_failed", NOW - timedelta(hours=25), "481 Authentication failed")
    t = providers.mso_test(row, None, True, now=NOW)
    assert t["state"] == "stale" and t["stale_after"] == NOW - timedelta(hours=1)
    # with SABnzbd's status: SABnzbd is shown; without: the old result, flagged as stale
    assert providers.health(row, _sab("ok", NOW - timedelta(days=9)), True, now=NOW)["status"] == "ok"
    h = providers.health(row, None, True, now=NOW)
    assert h["status"] == "auth_failed" and h["stale"] is True


def test_report_scenario_no_false_refused_login(app: Any, serve: Any, admin: Any, viewer: Any, db: Any) -> None:
    from msomocks import sabnzbd as mock

    from mso.db import load_migrations

    mock_app = mock.create_app(api_key="k")
    mock_app.state.sab.servers[0]["host"] = "127.0.0.1"
    url = serve(mock_app)
    admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "secrets": {"api_key": "k"}})
    assert admin.post("/api/v1/usenet/providers/import-sab").json()["items"][0]["slug"] == "eweka"
    # what a pre-1.3.0 "test" without credentials left behind (4 Nov)
    db.execute(
        "UPDATE usenet_providers SET status='auth_failed', status_detail='Gebruikersnaam of wachtwoord ontbreekt', "
        "last_check_at=now() - interval '13 hours' WHERE slug='eweka'"
    )
    p = viewer.get("/api/v1/usenet/providers").json()["items"][0]
    assert p["health"]["status"] == "ok" and p["health"]["source"] == "sabnzbd"
    assert p["mso_test"]["state"] == "missing_credentials"
    sug = viewer.get("/api/v1/usenet/recommendations").json()
    assert "news.eweka.test" not in {i["server"] for i in sug["issues"]}
    assert not any("eweka" in o["path"] and o["path"].endswith("/enable") for o in sug["ops"])
    servers = {s["name"]: s for s in viewer.get("/api/v1/usenet/sab/servers").json()["items"]}
    assert servers["news.eweka.test"]["provider"]["status"] == "ok"
    ov = viewer.get("/api/v1/usenet/overview").json()
    assert ov["providers"]["problems"] == []
    # data migration 0202 converts the stored row
    mig = next(m for m in load_migrations() if m.version == 202)
    db.execute(mig.up)
    row = db.fetchone("SELECT status, status_detail FROM usenet_providers WHERE slug='eweka'")
    assert row["status"] == "missing_credentials" and row["status_detail"] == providers.MISSING_CREDENTIALS_TEXT
    db.execute(mig.down)
    assert db.scalar("SELECT status FROM usenet_providers WHERE slug='eweka'") == "auth_failed"
    # the periodic job normalises as well
    assert providers.normalize_missing_credentials() == ["eweka"]
    assert db.scalar("SELECT status FROM usenet_providers WHERE slug='eweka'") == "missing_credentials"
