"""Slice E — AI advisor: opt-in default off, budget cap, deterministic fallback, mocked HTTP only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from mso.domain import outcomes

URL = "https://api.anthropic.com/v1/messages"
KEY = "sk-ant-test-0123456789abcdef"


def _reply(
    text: str = "Waarschijnlijk retentie. Controleer je backbone.",
    inp: int = 1000,
    out: int = 200,
    stop: str = "end_turn",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "usage": {"input_tokens": inp, "output_tokens": out},
        },
    )


def _fail(ref: str, reason: str = "no_retention") -> int:
    return outcomes.upsert_outcome(
        {
            "protocol": "usenet",
            "client": "sabnzbd",
            "client_ref": ref,
            "title": "Geheime.Persoonlijke.Titel.S01E01",
            "status": "failed",
            "failure_reason": reason,
            "failure_detail": "/home/jan/downloads apikey=abc",
            "provider_bytes": {"provider:eweka": 10},
            "finished_at": datetime.now(UTC),
        }
    )


def _enable(admin: Any, budget: float = 1.0) -> None:
    assert admin.put("/api/v1/ai/key", json={"api_key": KEY}).status_code == 200
    r = admin.put("/api/v1/ai/config", json={"enabled": True, "monthly_budget_eur": budget})
    assert r.status_code == 200, r.text


@pytest.fixture()
def no_network() -> Any:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL)
        yield route


def test_opt_in_default_off_and_deterministic(operator: Any, viewer: Any, db: Any, no_network: Any) -> None:
    st = viewer.get("/api/v1/ai/status").json()
    assert st["enabled"] is False and st["active"] is False and st["monthly_budget_eur"] == 0
    assert st["key"] == "leeg" and st["model"] == "claude-sonnet-5-5"
    _fail("1")
    res = operator.post("/api/v1/ai/advise", json={"subject": "overview"}).json()
    assert res["mode"] == "deterministic" and "opt-in" in res["fallback_reason"]
    assert "retentie" in res["text"].lower()
    assert no_network.call_count == 0
    rec = db.fetchone("SELECT * FROM recommendations WHERE id=%s", (res["recommendation_id"],))
    assert rec["risk"] == "advies" and rec["change_id"] is None and rec["area"] == "usenet"
    assert db.scalar("SELECT count(*) FROM ai_usage") == 0


def test_enabled_but_zero_budget_stays_off(admin: Any, operator: Any, no_network: Any) -> None:
    _enable(admin, budget=0)
    st = admin.get("/api/v1/ai/status").json()
    assert st["active"] is False and "€0" in st["reason"]
    _fail("1")
    assert operator.post("/api/v1/ai/advise", json={}).json()["mode"] == "deterministic"
    assert no_network.call_count == 0


def test_ai_call_headers_redaction_and_accounting(
    admin: Any, operator: Any, db: Any, no_network: Any
) -> None:
    no_network.mock(return_value=_reply())
    _enable(admin)
    oid = _fail("1")
    res = operator.post("/api/v1/ai/advise", json={"subject": "download", "ref": str(oid)}).json()
    assert res["mode"] == "ai" and res["text"].startswith("Waarschijnlijk retentie")
    req = no_network.calls.last.request
    assert req.headers["x-api-key"] == KEY and req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["model"] == "claude-sonnet-5-5" and body["output_config"] == {"effort": "low"}
    prompt = body["messages"][0]["content"] + body["system"]
    for secret in ("Geheime", "/home/jan", "apikey=abc", KEY):
        assert secret not in prompt
    use = db.fetchone("SELECT * FROM ai_usage")
    # 1000 in × 200 ct/Mtok + 200 out × 1000 ct/Mtok = 400000 microcents = 0.4 ct
    assert use["status"] == "ok" and use["cost_microcents"] == 400_000
    st = admin.get("/api/v1/ai/status").json()
    assert st["usage_month"]["spent_cents"] == 0.4 and st["usage_month"]["calls"] == 1
    rec = db.fetchone("SELECT * FROM recommendations WHERE id=%s", (res["recommendation_id"],))
    assert rec["risk"] == "advies" and rec["change_id"] is None and rec["evidence"]["generated_by"] == "ai"
    assert db.scalar("SELECT count(*) FROM change_sets") == 0
    assert KEY not in json.dumps(admin.get("/api/v1/ai/status").json())
    assert KEY not in json.dumps(db.fetchall("SELECT summary, detail FROM audit_log"), default=str)


def test_budget_cap_refuses_before_calling(admin: Any, operator: Any, db: Any, no_network: Any) -> None:
    no_network.mock(return_value=_reply())
    _enable(admin, budget=0.01)  # 1 cent; worst case estimate: 2000 out × 1000 ct/Mtok = 2 ct
    _fail("1")
    res = operator.post("/api/v1/ai/advise", json={}).json()
    assert res["mode"] == "deterministic" and "budget" in res["fallback_reason"].lower()
    assert no_network.call_count == 0
    assert db.scalar("SELECT status FROM ai_usage") == "refused_budget"
    # lower max output so the estimate fits; then spending pushes it over the cap
    admin.put("/api/v1/ai/config", json={"max_output_tokens": 200, "monthly_budget_eur": 0.01})
    assert operator.post("/api/v1/ai/advise", json={}).json()["mode"] == "ai"  # 0.4 ct spent
    no_network.mock(return_value=_reply(inp=1000, out=200))
    operator.post("/api/v1/ai/advise", json={})  # 0.8 ct spent
    res3 = operator.post("/api/v1/ai/advise", json={}).json()
    assert res3["mode"] == "deterministic" and no_network.call_count == 2
    spent = db.scalar("SELECT sum(cost_microcents) FROM ai_usage WHERE ts >= date_trunc('month', now())")
    assert spent <= 1_000_000  # never above 1 cent


def test_api_error_and_refusal_fall_back(admin: Any, operator: Any, db: Any, no_network: Any) -> None:
    _enable(admin)
    _fail("1")
    no_network.mock(
        return_value=httpx.Response(529, json={"error": {"message": "overloaded x-api-key=" + KEY}})
    )
    res = operator.post("/api/v1/ai/advise", json={}).json()
    assert (
        res["mode"] == "deterministic"
        and "529" in res["fallback_reason"]
        and KEY not in res["fallback_reason"]
    )
    no_network.mock(side_effect=httpx.ConnectTimeout("timeout"))
    assert operator.post("/api/v1/ai/advise", json={}).json()["mode"] == "deterministic"
    no_network.mock(return_value=_reply(text="", stop="refusal"))
    assert operator.post("/api/v1/ai/advise", json={}).json()["mode"] == "deterministic"
    statuses = [r["status"] for r in db.fetchall("SELECT status FROM ai_usage ORDER BY id")]
    assert statuses == ["error", "error", "error"]
    assert KEY not in json.dumps(db.fetchall("SELECT error FROM ai_usage"))


def test_unknown_model_without_price_is_refused(admin: Any, operator: Any, no_network: Any) -> None:
    _enable(admin)
    admin.put("/api/v1/ai/config", json={"model": "claude-nieuw-9"})
    st = admin.get("/api/v1/ai/status").json()
    assert st["active"] is False and "prijs" in st["reason"]
    assert (
        admin.put(
            "/api/v1/ai/prices/claude-nieuw-9",
            json={"input_cents_per_mtok": 100, "output_cents_per_mtok": 500},
        ).status_code
        == 200
    )
    assert admin.get("/api/v1/ai/status").json()["active"] is True


def test_test_button_roles_and_usage(admin: Any, operator: Any, viewer: Any, no_network: Any) -> None:
    assert operator.post("/api/v1/ai/test").status_code == 403
    assert viewer.put("/api/v1/ai/key", json={"api_key": KEY}).status_code == 403
    r = admin.post("/api/v1/ai/test").json()
    assert r["ok"] is False and no_network.call_count == 0
    _enable(admin)
    no_network.mock(return_value=_reply(text="Verbinding werkt.", inp=30, out=5))
    r = admin.post("/api/v1/ai/test").json()
    assert r["ok"] is True and r["message"] == "Verbinding werkt."
    u = viewer.get("/api/v1/ai/usage").json()
    assert u["items"][0]["purpose"] == "test" and u["month"]["calls"] == 1
    assert admin.delete("/api/v1/ai/key").json()["key"] == "leeg"


def test_deterministic_explainer_unit(app: Any) -> None:
    from mso.features.ai import explain

    ev = {
        "subject": "overview",
        "key": "overview",
        "period_days": 7,
        "per_protocol": {"torrent": {"success": 3, "failed": 2}},
        "failures_by_reason": {"stalled": 2},
        "connectors_unhealthy": ["qBittorrent"],
    }
    d = explain.deterministic(ev)
    assert d["area"] == "torrent" and "vastgelopen" in d["title"].lower()
    assert "qBittorrent" in d["text"] and "klein" in d["text"]
    for r in outcomes.FAILURE_REASONS:
        assert r in explain.ADVICE
