"""Slice E — costs: CRUD, normalisation, bounded analysis, disable simulation, scenarios, renewals."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mso.domain import outcomes

FORBIDDEN = ("gegarandeerd", "bespaart zeker", "garantie dat je bespaart")


def _no_promises(payload: Any) -> None:
    text = json.dumps(payload, default=str).lower()
    for w in FORBIDDEN:
        assert w not in text, w


def _src(sid: str, kind: str, label: str) -> None:
    outcomes.upsert_source(sid, kind, label, "direct")


def _out(
    ref: str,
    *,
    status: str = "success",
    protocol: str = "usenet",
    source: str | None = None,
    pb: dict[str, int] | None = None,
    rk: str | None = None,
    meta: dict[str, Any] | None = None,
    reason: str | None = None,
    days_ago: float = 1,
    size: int = 1024**3,
) -> None:
    outcomes.upsert_outcome(
        {
            "protocol": protocol,
            "client": "sabnzbd" if protocol == "usenet" else "qbittorrent",
            "client_ref": ref,
            "title": f"Show.S01E{ref}",
            "status": status,
            "source_id": source,
            "provider_bytes": pb or {},
            "release_key": rk,
            "meta": meta or {},
            "failure_reason": reason,
            "size_bytes": size,
            "finished_at": datetime.now(UTC) - timedelta(days=days_ago),
        }
    )


def _service(admin: Any, **kw: Any) -> dict[str, Any]:
    body = {"label": "Eweka", "kind": "usenet_provider", "billing": "monthly", "amount_cents": 1099} | kw
    r = admin.post("/api/v1/costs/services", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_crud_roles_and_audit(admin: Any, viewer: Any, db: Any) -> None:
    _src("provider:eweka", "usenet_provider", "Eweka")
    assert viewer.post("/api/v1/costs/services", json={"label": "x", "kind": "other"}).status_code == 403
    s = _service(admin, source_id="provider:eweka", backbone="Eweka/Omicron", renewal_date="2026-12-01")
    assert s["backbone"] == "Eweka/Omicron" and s["renewal_date"] == "2026-12-01"
    items = viewer.get("/api/v1/costs/services").json()["items"]
    assert items[0]["monthly"]["monthly_cents"] == 1099
    upd = admin.put(
        f"/api/v1/costs/services/{s['id']}",
        json={"label": "Eweka", "kind": "usenet_provider", "billing": "yearly", "amount_cents": 12000},
    )
    assert upd.status_code == 200 and upd.json()["billing"] == "yearly"
    assert viewer.get(f"/api/v1/costs/services/{s['id']}").json()["monthly"]["monthly_cents"] == 1000
    assert (
        admin.post(
            "/api/v1/costs/services", json={"label": "b", "kind": "block_account", "billing": "block"}
        ).status_code
        == 422
    )
    assert (
        admin.post(
            "/api/v1/costs/services", json={"label": "b", "kind": "other", "source_id": "bestaat:niet"}
        ).status_code
        == 422
    )
    assert admin.delete(f"/api/v1/costs/services/{s['id']}").status_code == 200
    actions = [r["action"] for r in db.fetchall("SELECT action FROM audit_log ORDER BY id")]
    assert "costs.service_created" in actions and "costs.service_deleted" in actions


def test_block_account_normalised_by_observed_usage(admin: Any, viewer: Any) -> None:
    _src("block:blocknews", "block_account", "Blocknews")
    s = _service(
        admin,
        label="Blocknews",
        kind="block_account",
        billing="block",
        amount_cents=2000,
        block_size_gb=500,
        block_used_gb=100,
        source_id="block:blocknews",
    )
    a = viewer.get(f"/api/v1/costs/services/{s['id']}/analysis?period_days=30").json()
    assert a["cost"]["monthly_cents"] is None and "reason" in a["cost"]  # no usage observed -> honest unknown
    for i in range(3):
        _out(f"b{i}", pb={"provider:eweka": 9 * 1024**3, "block:blocknews": 10 * 1024**3})
    a = viewer.get(f"/api/v1/costs/services/{s['id']}/analysis?period_days=30").json()
    # 30 GB in 30 days at 4 ct/GB = 120 ct per month
    assert a["cost"]["monthly_cents"] == 120
    assert a["usage"]["gb"] == 30.0
    assert a["block"]["remaining_gb"] == 400 and a["block"]["est_months_left"] == 13.3
    _no_promises(a)


def test_usenet_simulation_ranges_and_coverage(admin: Any, viewer: Any) -> None:
    _src("provider:eweka", "usenet_provider", "Eweka")
    _src("provider:newshosting", "usenet_provider", "Newshosting")
    s = _service(admin, label="Newshosting", source_id="provider:newshosting", amount_cents=999)
    gb = 1024**3
    _out("1", pb={"provider:eweka": gb})  # not involved
    _out("2", pb={"provider:eweka": gb})  # not involved
    _out("3", pb={"provider:newshosting": gb})  # only this
    _out("4", pb={"provider:eweka": gb, "newshosting": gb // 10})  # shared (bare slug key)
    _out("5")  # no evidence
    _out("6", status="failed", reason="no_retention", pb={"provider:newshosting": 10})
    _out("7", pb={"provider:newshosting": gb}, days_ago=60)  # outside period
    sim = viewer.get(f"/api/v1/costs/services/{s['id']}/simulate?period_days=30").json()
    assert sim["available"] and sim["method"] == "usenet_providerbytes"
    assert sim["observed_successes"] == 5 and sim["involved"] == 2
    buckets = {b["key"]: b["count"] for b in sim["buckets"]}
    assert buckets == {"niet_geraakt": 2, "alleen_deze": 1, "gedeeld": 1, "geen_bewijs": 1}
    # without article-level evidence the lower bound is 0 and the upper bound every involved download
    assert sim["affected_range"] == {"low": 0, "high": 2, "low_pct": 0.0, "high_pct": 0.4}
    assert sim["coverage"] == {"with_evidence": 4, "total": 5, "ratio": 0.8}
    assert sim["confidence"] == "laag"  # capped: provider bytes cannot prove uniqueness; also n < 10
    assert sim["never_auto_cancel"] is True and sim["entered_cost"]["monthly_cents"] == 999
    assert any("garantie" in d for d in sim["disclaimers"])
    _no_promises(sim)
    a = viewer.get(f"/api/v1/costs/services/{s['id']}/analysis?period_days=30").json()
    assert a["unique"]["value"] is None and a["unique"]["confidence"] == "onbekend"
    assert a["failures_by_reason"] == [
        {"reason": "no_retention", "label": "Buiten retentie / artikelen verlopen", "count": 1}
    ]
    assert a["usage"]["successful_downloads"] == 2


def test_usenet_simulation_uses_slice_b_evidence(admin: Any, viewer: Any, monkeypatch: Any) -> None:
    import sys
    import types

    mod = types.ModuleType("mso.features.usenet.metrics")
    mod.provider_contribution = lambda days: {
        "items": [  # type: ignore[attr-defined]
            {
                "provider_id": "provider:newshosting",
                "unique_downloads": 1,
                "coverage": 0.9,
                "confidence": "middel",
                "unique_available": 7,
                "compared_articles": 40,
            }
        ]
    }
    pkg = sys.modules.get("mso.features.usenet") or types.ModuleType("mso.features.usenet")
    monkeypatch.setitem(sys.modules, "mso.features.usenet", pkg)
    monkeypatch.setitem(sys.modules, "mso.features.usenet.metrics", mod)
    _src("provider:newshosting", "usenet_provider", "Newshosting")
    s = _service(admin, label="Newshosting", source_id="provider:newshosting")
    _out("1", pb={"provider:newshosting": 5})
    _out("2", pb={"provider:newshosting": 5, "provider:eweka": 5})
    sim = viewer.get(f"/api/v1/costs/services/{s['id']}/simulate?period_days=30").json()
    assert sim["method"] == "usenet_artikelbewijs"
    assert sim["affected_range"]["low"] == 1 and sim["affected_range"]["high"] == 2
    assert sim["slice_b_evidence"]["coverage"] == 0.9
    a = viewer.get(f"/api/v1/costs/services/{s['id']}/analysis?period_days=30").json()
    assert a["unique"]["articles"]["unique_available"] == 7 and a["unique"]["value"] == 1


def test_real_slice_b_shape_without_download_count(admin: Any, viewer: Any, monkeypatch: Any) -> None:
    """Slice B's provider_contribution() returns a list keyed by source_id with article counts only:
    the download-level lower bound must stay 0 (no invented uniqueness)."""
    import sys
    import types

    mod = types.ModuleType("mso.features.usenet.metrics")
    mod.provider_contribution = lambda days: [  # type: ignore[attr-defined]
        {"source_id": "provider:eweka", "unique_available": 3, "coverage": 0.5, "confidence": "laag"}
    ]
    monkeypatch.setitem(sys.modules, "mso.features.usenet.metrics", mod)
    _src("provider:eweka", "usenet_provider", "Eweka")
    s = _service(admin, source_id="provider:eweka")
    _out("1", pb={"provider:eweka": 5})
    sim = viewer.get(f"/api/v1/costs/services/{s['id']}/simulate?period_days=30").json()
    assert sim["method"] == "usenet_providerbytes" and sim["affected_range"]["low"] == 0
    assert sim["slice_b_evidence"]["unique_available"] == 3


def test_indexer_simulation_release_overlap(admin: Any, viewer: Any) -> None:
    _src("direct:geek", "nzb_indexer", "NZBgeek")
    _src("direct:finder", "nzb_indexer", "NZBFinder")
    s = _service(
        admin,
        label="NZBgeek",
        kind="nzb_indexer",
        billing="yearly",
        amount_cents=1200,
        source_id="direct:geek",
    )
    _out("1", source="direct:geek", rk="g1", meta={"also_from": ["direct:finder"]})  # alternative
    _out("2", source="direct:geek", rk="g2", meta={"also_from": []})  # only this
    _out("3", source="direct:geek", rk="g3")  # unknown
    _out("4", source="direct:geek", rk="g4")  # alt via other outcome
    _out("5", source="direct:finder", rk="g4", days_ago=100)
    _out("6", source="direct:finder", rk="x")  # other source
    sim = viewer.get(f"/api/v1/costs/services/{s['id']}/simulate?period_days=30").json()
    b = {x["key"]: x["count"] for x in sim["buckets"]}
    assert b == {"alternatief": 2, "geen_alternatief": 1, "onbekend": 1}
    assert sim["affected_range"]["low"] == 1 and sim["affected_range"]["high"] == 2
    assert sim["coverage"]["ratio"] == 0.75
    a = viewer.get(f"/api/v1/costs/services/{s['id']}/analysis?period_days=30").json()
    assert a["unique"]["value"] == 1 and a["cost"]["monthly_cents"] == 100
    assert a["cost"]["per_success_cents"] == 25.0
    _no_promises(a)


def test_simulation_unlinked_service_is_unavailable(admin: Any, viewer: Any) -> None:
    s = _service(admin, label="VPN", kind="vpn")
    sim = viewer.get(f"/api/v1/costs/services/{s['id']}/simulate").json()
    assert sim["available"] is False and sim["reason"]


def test_latency_and_recovered(admin: Any, viewer: Any, db: Any) -> None:
    _src("direct:geek", "nzb_indexer", "NZBgeek")
    s = _service(admin, label="NZBgeek", kind="nzb_indexer", source_id="direct:geek")
    for v in (100, 200, 300, 400):
        outcomes.observe("direct:geek", "search.latency_ms", v)
    outcomes.recommend("recovery", "Hersteld", "x", subject="direct:geek", dedupe_key="r1")
    db.execute("UPDATE recommendations SET status='resolved'")
    a = viewer.get(f"/api/v1/costs/services/{s['id']}/analysis").json()
    assert a["latency"]["n"] == 4 and a["latency"]["p50_ms"] == 250.0
    assert a["recovered"]["count"] == 1


def test_scenarios_no_invented_backbone(admin: Any, viewer: Any) -> None:
    _src("provider:eweka", "usenet_provider", "Eweka")
    _src("provider:newshosting", "usenet_provider", "Newshosting")
    e = _service(admin, label="Eweka", source_id="provider:eweka", amount_cents=1000)
    n = _service(admin, label="Newshosting", source_id="provider:newshosting", amount_cents=900)
    c = _service(
        admin,
        label="Blockkandidaat",
        kind="block_account",
        billing="block",
        amount_cents=1500,
        block_size_gb=1000,
        active=False,
        backbone="Omicron",
    )
    _out("1", pb={"provider:eweka": 10})
    _out("2", pb={"provider:eweka": 10, "provider:newshosting": 3})
    res = viewer.get("/api/v1/costs/scenarios?period_days=30").json()
    labels = [x["label"] for x in res["items"]]
    assert "Huidige combinatie" in labels and "Zonder Newshosting" in labels
    assert "Blockkandidaat in plaats van Eweka" in labels
    cur = res["items"][0]
    assert cur["monthly_cents"] == 1900 and cur["unknown_backbones"] == 2
    assert any("verzint geen backbone" in n_ for n_ in cur["notes"])
    without_n = next(x for x in res["items"] if x["label"] == "Zonder Newshosting")
    assert without_n["observed"]["needed_excluded_provider"] == 1
    assert without_n["observed"]["fully_within_set"] == 1
    cand = next(x for x in res["items"] if x["label"] == "Blockkandidaat in plaats van Eweka")
    assert cand["observed"]["unobserved_candidates"] == 1 and cand["monthly_complete"] is False
    custom = viewer.post(
        "/api/v1/costs/scenarios",
        json={"scenarios": [{"label": "Alleen Eweka", "service_ids": [e["id"]]}], "period_days": 30},
    ).json()
    assert custom["items"][0]["monthly_cents"] == 1000
    _no_promises(res)
    assert n and c


def test_renewal_reminders_job_creates_advice(admin: Any, viewer: Any, db: Any, worker: Any) -> None:
    from mso import jobs

    soon = (date.today() + timedelta(days=5)).isoformat()
    later = (date.today() + timedelta(days=90)).isoformat()
    _service(admin, label="Eweka", renewal_date=soon)
    _service(admin, label="Later", renewal_date=later)
    jobs.enqueue("costs.renewal_reminders", {})
    worker.drain()
    recs = db.fetchall("SELECT * FROM recommendations WHERE area='costs'")
    assert len(recs) == 1 and recs[0]["risk"] == "advies"
    assert "nooit zelf iets op" in recs[0]["explanation"]
    jobs.enqueue("costs.renewal_reminders", {}, idempotency_key="again")
    worker.drain()
    assert db.scalar("SELECT count(*) FROM recommendations WHERE area='costs'") == 1  # idempotent
    summ = viewer.get("/api/v1/costs/summary").json()
    assert summ["total"]["monthly_cents"] == 2198 and len(summ["renewals"]) == 1
