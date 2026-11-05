"""1.5.0 P4 — public torrent indexers are free and never 'prijs ontbreekt'; paid kinds ask for a price;
older imports are reclassified once (user edits untouched)."""

from __future__ import annotations

from typing import Any

from mso.domain import outcomes


def test_free_and_paid_classification(viewer: Any, db: Any) -> None:
    from mso.features.costs import imports

    outcomes.upsert_source("prowlarr:1", "nzb_indexer", "NZBgeek", "prowlarr")
    outcomes.upsert_source("prowlarr:2", "torrent_indexer", "0Magnet", "prowlarr", private=False)
    outcomes.upsert_source("prowlarr:3", "torrent_indexer", "Union Fansub", "prowlarr", private=True)
    outcomes.upsert_source("provider:eweka", "usenet_provider", "Eweka", "mso")
    outcomes.upsert_source("block:bn", "block_account", "Blocknews", "mso")
    body = viewer.get("/api/v1/costs/services").json()
    by = {i["source_id"]: i for i in body["items"]}
    assert by["prowlarr:2"]["free"] is True and by["prowlarr:2"]["price_missing"] is False
    assert all(by[s]["price_missing"] for s in ("prowlarr:1", "prowlarr:3", "provider:eweka", "block:bn"))
    assert body["free"] == 1 and body["paid"] == 4 and body["price_missing"] == 4
    assert body["items"][-1]["source_id"] == "prowlarr:2"  # free ones last
    assert imports.is_free_source("torrent_indexer", False) and not imports.is_free_source("torrent_indexer", True)
    assert not imports.is_free_source("nzb_indexer", False)


def test_old_imports_reclassified_but_user_prices_kept(viewer: Any, db: Any) -> None:
    outcomes.upsert_source("prowlarr:2", "torrent_indexer", "BlueRoms", "prowlarr", private=False)
    outcomes.upsert_source("prowlarr:5", "torrent_indexer", "Betaald", "prowlarr", private=False)
    db.execute("INSERT INTO services(source_id, kind, label, billing, amount_cents, origin, price_missing) VALUES "
               "('prowlarr:2','torrent_indexer','BlueRoms','monthly',0,'import',true),"
               "('prowlarr:5','torrent_indexer','Betaald','monthly',300,'import',false)")
    items = {i["source_id"]: i for i in viewer.get("/api/v1/costs/services").json()["items"]}
    assert items["prowlarr:2"]["billing"] == "free" and items["prowlarr:2"]["price_missing"] is False
    assert items["prowlarr:5"]["billing"] == "monthly" and items["prowlarr:5"]["amount_cents"] == 300
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='costs.services_free'") == 1
    summ = viewer.get("/api/v1/costs/summary").json()["total"]
    assert summ["price_missing"] == 0
