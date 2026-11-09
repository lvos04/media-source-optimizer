"""Slice E — overview: availability flags on an empty DB, aggregation, failure locations, staleness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mso import changes
from mso.domain import outcomes


def test_empty_db_reports_unavailable_not_zero(viewer: Any, client: Any) -> None:
    assert client.get("/api/v1/overview").status_code == 401
    o = viewer.get("/api/v1/overview").json()
    assert o["downloads"]["available"] is False and o["downloads"]["reason"]
    assert o["failures"]["available"] is False
    assert o["costs"]["available"] is False and o["costs"]["monthly_cents"] is None
    assert o["backup"]["available"] is False and o["backup"]["age_s"] is None
    assert o["mediaguard"]["available"] is False and o["mediaguard"]["mode"] == "standalone"
    assert o["connectors"]["available"] is False
    assert o["health"]["status"] == "unknown"
    assert o["recommendations"]["open"] == 0 and o["changes"]["open"] == 0
    assert o["jobs"]["worker"]["seen"] is False


def _o(ref: str, **kw: Any) -> None:
    row = {
        "protocol": "usenet",
        "client": "sabnzbd",
        "client_ref": ref,
        "title": ref,
        "status": "failed",
        "finished_at": datetime.now(UTC) - timedelta(hours=kw.pop("hours", 1)),
    } | kw
    outcomes.upsert_outcome(row)


def test_aggregation_and_locations(admin: Any, viewer: Any, db: Any) -> None:
    outcomes.upsert_source("provider:eweka", "usenet_provider", "Eweka", "direct")
    outcomes.upsert_source("direct:geek", "nzb_indexer", "NZBgeek", "direct")
    _o("a", failure_reason="no_retention", provider_bytes={"provider:eweka": 5})
    _o("b", failure_reason="incomplete", provider_bytes={"eweka": 5}, hours=30)
    _o("c", failure_reason="false_positive", source_id="direct:geek")
    _o("d", failure_reason="disk_full")
    _o("e", failure_reason="stalled", protocol="torrent", client="qbittorrent", hours=24 * 10)  # outside 7d
    _o("f", status="active", protocol="torrent", client="qbittorrent")
    _o("g", status="success")
    db.execute(
        "INSERT INTO connectors(id,kind,label,url,enabled,status,last_checked_at) VALUES "
        "('sabx','usenet_client','SABnzbd','http://sab',true,'offline', now() - interval '5 minutes') "
        "ON CONFLICT (id) DO UPDATE SET url=EXCLUDED.url, enabled=true, status='offline', "
        "last_checked_at=EXCLUDED.last_checked_at"
    )
    admin.post(
        "/api/v1/costs/services", json={"label": "Eweka", "kind": "usenet_provider", "amount_cents": 1099}
    )
    outcomes.recommend("torrent", "x", "y", risk="toestemming_nodig", dedupe_key="1")
    changes.create("prowlarr", "t", [{"path": "p", "after": 1}], risk="advies")
    o = viewer.get("/api/v1/overview").json()
    assert o["downloads"]["available"] and o["downloads"]["active"] == {"usenet": 0, "torrent": 1}
    assert o["downloads"]["stale_s"] is not None and o["downloads"]["stale_s"] < 60
    f = o["failures"]
    assert f["last_7d"] == 4 and f["last_24h"] == 3
    locs = {(x["kind"], x["id"]): x["count"] for x in f["by_location"]}
    assert locs[("provider", "provider:eweka")] == 2
    assert locs[("source", "direct:geek")] == 1 and locs[("client", "sabnzbd")] == 1
    assert next(x for x in f["by_location"] if x["id"] == "provider:eweka")["label"] == "Eweka"
    assert o["costs"]["available"] and o["costs"]["monthly_cents"] == 1099
    # 1.6.0 integration (deliberate): a decision is counted once — the toestemming_nodig recommendation is in
    # "Wacht op jou", so the Aanbevelingen card only shows the other ones and links to the inbox
    assert o["recommendations"]["by_risk"]["toestemming_nodig"] == 0 and o["recommendations"]["waiting"] == 1
    assert o["changes"]["open"] == 1  # the advies change is no decision in the inbox
    sab = next(c for c in o["connectors"]["items"] if c["id"] == "sabx")
    assert sab["status"] == "offline" and 250 < sab["stale_s"] < 400
    assert o["health"]["status"] == "offline" and any("SABnzbd" in i for i in o["health"]["issues"])
    assert o["recent_audit"][0]["action"] in ("change.created", "costs.service_created")


def test_backup_and_mediaguard_connector(viewer: Any, db: Any) -> None:
    db.execute(
        "INSERT INTO backups(file,kind,created_at) VALUES ('x.dump','auto', now() - interval '3 days')"
    )
    db.execute(
        "INSERT INTO connectors(id,kind,label,url,enabled,status) VALUES "
        "('mediaguard','mediaguard','MediaGuard','http://mg',true,'healthy') ON CONFLICT (id) DO UPDATE "
        "SET url='http://mg', enabled=true, status='healthy'"
    )
    o = viewer.get("/api/v1/overview").json()
    assert o["backup"]["available"] and o["backup"]["stale"] is True and o["backup"]["age_s"] > 2 * 86400
    mg = o["mediaguard"]
    # configured: with Slice D loaded and no probe yet it is honestly 'standalone' (not reachable yet);
    # without Slice D the connectors table says 'connected'
    assert mg["available"] is True and mg["mode"] in ("connected", "standalone") and mg["mode_label"]
