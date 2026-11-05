"""Slice E — costs: automatic import of providers/indexers as services, price-missing state, inline price edit,
cost per successful GB/download and the measured unique share."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from mso.domain import outcomes

GB = 1024**3


def _sources() -> None:
    outcomes.upsert_source("provider:eweka", "usenet_provider", "Eweka", "mso")
    outcomes.upsert_source("provider:xsnews", "usenet_provider", "XSNews", "mso", enabled=False)
    outcomes.upsert_source("block:blocknews", "block_account", "Blocknews", "mso", meta={"block_size_gb": 500})
    outcomes.upsert_source("prowlarr:1", "nzb_indexer", "NZBgeek", "prowlarr", private=True)
    outcomes.upsert_source("prowlarr:2", "torrent_indexer", "1337x", "prowlarr", private=False)
    outcomes.upsert_source("prowlarr:3", "nzb_indexer", "Weg", "prowlarr", meta={"removed": True})
    outcomes.upsert_source("prowlarr:9", "nzb_indexer", "MSO", "prowlarr", meta={"is_mso": True})
    outcomes.upsert_source("tracker:example.org", "tracker", "example.org", "qbittorrent")


def test_import_idempotent_kinds_and_price_missing(admin: Any, viewer: Any, db: Any) -> None:
    _sources()
    items = viewer.get("/api/v1/costs/services").json()["items"]
    by_src = {i["source_id"]: i for i in items}
    assert set(by_src) == {"provider:eweka", "provider:xsnews", "block:blocknews", "prowlarr:1", "prowlarr:2"}
    eweka = by_src["provider:eweka"]
    assert eweka["kind"] == "usenet_provider" and eweka["origin"] == "import" and eweka["price_missing"] is True
    assert eweka["amount_cents"] == 0 and eweka["monthly"]["monthly_cents"] is None
    assert "prijs" in eweka["monthly"]["reason"].lower()
    assert by_src["provider:xsnews"]["active"] is False
    assert by_src["block:blocknews"]["billing"] == "block" and by_src["block:blocknews"]["block_size_gb"] == 500
    assert by_src["prowlarr:1"]["kind"] == "nzb_indexer" and by_src["prowlarr:1"]["price_missing"] is True
    assert by_src["prowlarr:2"]["billing"] == "free" and by_src["prowlarr:2"]["price_missing"] is False
    # idempotent: a second list or explicit import adds nothing
    assert len(viewer.get("/api/v1/costs/services").json()["items"]) == 5
    assert admin.post("/api/v1/costs/services/import").json()["created"] == 0
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='costs.services_imported'") == 1
    # summary: price-missing services are unknown, never counted as 0
    summ = viewer.get("/api/v1/costs/summary").json()["total"]
    assert summ["price_missing"] == 3 and summ["monthly_cents"] == 0  # xsnews inactive, 1337x gratis
    assert {u["label"] for u in summ["unknown"]} == {"Eweka", "Blocknews", "NZBgeek"}

    # removing an imported service is remembered; it does not come back
    assert admin.delete(f"/api/v1/costs/services/{by_src['prowlarr:1']['id']}").status_code == 200
    assert "prowlarr:1" not in {i["source_id"] for i in viewer.get("/api/v1/costs/services").json()["items"]}
    # an existing hand-made service for a source blocks the import
    outcomes.upsert_source("prowlarr:4", "nzb_indexer", "DrunkenSlug", "prowlarr")
    r = admin.post("/api/v1/costs/services", json={"label": "Slug", "kind": "nzb_indexer", "billing": "yearly",
                                                   "amount_cents": 1200, "source_id": "prowlarr:4"})
    assert r.status_code == 200
    assert admin.post("/api/v1/costs/services/import").json()["created"] == 0


def test_inline_price_roles_and_audit(admin: Any, operator: Any, viewer: Any, db: Any) -> None:
    _sources()
    items = viewer.get("/api/v1/costs/services").json()["items"]
    eweka = next(i for i in items if i["source_id"] == "provider:eweka")
    url = f"/api/v1/costs/services/{eweka['id']}/price"
    body = {"billing": "monthly", "amount_cents": 1099, "renewal_date": "2027-01-01"}
    assert operator.patch(url, json=body).status_code == 403
    r = admin.patch(url, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["price_missing"] is False and out["monthly"]["monthly_cents"] == 1099
    assert out["renewal_date"] == "2027-01-01"
    blk = next(i for i in items if i["source_id"] == "block:blocknews")
    assert admin.patch(f"/api/v1/costs/services/{blk['id']}/price",
                       json={"billing": "block", "amount_cents": 2500}).status_code == 200  # size from import
    assert admin.patch(url, json={"billing": "block", "amount_cents": 1}).status_code == 422  # no block size
    line = db.fetchone("SELECT * FROM audit_log WHERE action='costs.service_price_set' ORDER BY id LIMIT 1")
    assert "Eweka" in line["summary"] and "per maand" in line["summary"]


def test_cost_per_successful_gb_and_download_with_unique_share(
    admin: Any, viewer: Any, monkeypatch: Any
) -> None:
    mod = types.ModuleType("mso.features.usenet.metrics")
    mod.provider_contribution = lambda days: [  # type: ignore[attr-defined]
        {"source_id": "provider:eweka", "unique_available": 8, "compared_articles": 40, "articles_checked": 50,
         "confidence": "middel", "method": "explicit_test"}
    ]
    monkeypatch.setitem(sys.modules, "mso.features.usenet.metrics", mod)
    _sources()
    now = datetime.now(UTC)
    for i, (status, pb) in enumerate([("success", 2 * GB), ("success", 1 * GB), ("failed", 3 * GB)]):
        outcomes.upsert_outcome({
            "protocol": "usenet", "client": "sabnzbd", "client_ref": f"n{i}", "title": f"t{i}", "status": status,
            "provider_bytes": {"provider:eweka": pb}, "finished_at": now - timedelta(days=1),
        })
    outcomes.upsert_outcome({
        "protocol": "usenet", "client": "sabnzbd", "client_ref": "x", "title": "x", "status": "success",
        "source_id": "prowlarr:1", "size_bytes": 4 * GB, "finished_at": now - timedelta(days=1),
    })
    items = viewer.get("/api/v1/costs/services?period_days=30").json()["items"]
    eweka = next(i for i in items if i["source_id"] == "provider:eweka")
    m = eweka["metrics"]
    assert m["successful_downloads"] == 2 and m["successful_gb"] == 3.0
    assert m["per_gb_cents"] is None and "prijs" in m["cost_reason"].lower()  # price missing → no ratio
    assert m["unique"]["available"] is True and m["unique"]["pct"] == 0.2
    admin.patch(f"/api/v1/costs/services/{eweka['id']}/price", json={"billing": "monthly", "amount_cents": 900})
    items = viewer.get("/api/v1/costs/services?period_days=30").json()["items"]
    m = next(i for i in items if i["source_id"] == "provider:eweka")["metrics"]
    assert m["period_cost_cents"] == 900 and m["per_success_cents"] == 450.0 and m["per_gb_cents"] == 300.0
    geek = next(i for i in items if i["source_id"] == "prowlarr:1")["metrics"]
    assert geek["successful_downloads"] == 1 and geek["successful_gb"] == 4.0 and "unique" not in geek
