"""AI advisor orchestration: opt-in/budget gate → (optional) AI call → stored as an 'advies' recommendation.
Every path falls back to the deterministic explainer."""

from __future__ import annotations

import json
import logging
from typing import Any

from mso import settings_registry as sr
from mso.audit import audit
from mso.db import Database, get_db
from mso.domain.outcomes import recommend
from mso.features.ai import budget, client, explain
from mso.security import redact
from mso.vault import get_vault

LOGGER = logging.getLogger(__name__)
KEY_NAME = "ai.api_key"
SYSTEM_PROMPT = (
    "Je bent de adviseur van Media Source Optimizer, een zelf-gehoste app die Usenet- en torrentdownloads "
    "analyseert. Je krijgt geaggregeerde, geanonimiseerde waarnemingen en een regelgebaseerde diagnose. "
    "Geef in het Nederlands een korte uitleg (maximaal 180 woorden) van de waarschijnlijke oorzaak en 2-4 "
    "concrete, controleerbare suggesties. Regels: je kunt zelf niets wijzigen, verwijderen, opzeggen of "
    "kopen; "
    "verzin geen gegevens en noem onzekerheid expliciet; beloof geen besparingen of resultaten; stel nooit "
    "voor "
    "om kwaliteitseisen, private-trackerregels, betaalmuren of seedverplichtingen te omzeilen; stel niet "
    "voor dat "
    "een eigen indexer een provider vervangt."
)


def status(db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    enabled = bool(sr.get("ai.enabled"))
    budget_eur = float(sr.get("ai.monthly_budget_eur"))
    model = str(sr.get("ai.model"))
    key = get_vault().status(KEY_NAME)
    p = budget.price(model, db)
    usage = budget.month_usage(db)
    reason = None
    if not enabled:
        reason = "AI-adviseur staat uit (opt-in vereist); de ingebouwde uitleg wordt gebruikt."
    elif budget_eur <= 0:
        reason = "Maandbudget is €0; de AI-adviseur blijft uit."
    elif key != "ingesteld":
        reason = "Geen API-sleutel ingesteld."
    elif p is None:
        reason = (
            f"Geen prijs ingesteld voor model '{model}'; zonder prijs kan het budget niet bewaakt worden."
        )
    elif usage["spent_cents"] >= budget_eur * 100:
        reason = "Maandbudget is op; de ingebouwde uitleg wordt gebruikt tot volgende maand."
    return {
        "enabled": enabled,
        "active": reason is None,
        "reason": reason,
        "provider": sr.get("ai.provider"),
        "model": model,
        "effort": sr.get("ai.effort"),
        "max_output_tokens": int(sr.get("ai.max_output_tokens")),
        "monthly_budget_eur": budget_eur,
        "key": key,
        "price": {"input_cents_per_mtok": p[0], "output_cents_per_mtok": p[1]} if p else None,
        "usage_month": usage,
        "remaining_cents": round(max(0.0, budget_eur * 100 - usage["spent_cents"]), 4),
    }


def _call_ai(
    purpose: str, user_prompt: str, actor: str, db: Database
) -> tuple[client.AIReply | None, str | None]:
    """Gate + call + account. Returns (reply, fallback_reason)."""
    st = status(db)
    model = st["model"]
    if not st["active"]:
        return None, st["reason"]
    p = budget.price(model, db)
    assert p is not None
    max_out = int(sr.get("ai.max_output_tokens"))
    est = budget.cost_microcents(budget.estimate_tokens(SYSTEM_PROMPT + user_prompt), max_out, p)
    ok, why = budget.check(est, db)
    if not ok:
        budget.record(model, purpose, "refused_budget", error=why, actor=actor, db=db)
        return None, why
    key = get_vault().get(KEY_NAME) or ""
    try:
        reply = client.call(
            url=str(sr.get("ai.api_url")),
            api_key=key,
            model=model,
            system=SYSTEM_PROMPT,
            user=redact(user_prompt),
            max_tokens=max_out,
            effort=str(sr.get("ai.effort")),
            timeout_s=float(sr.get("ai.timeout_s")),
        )
    except Exception as exc:  # noqa: BLE001  (AIError, UnsafeTarget, anything else)
        msg = redact(str(exc)).replace(key, "***") if key else redact(str(exc))
        budget.record(model, purpose, "error", error=msg[:500], actor=actor, db=db)
        return None, f"AI niet bereikbaar: {msg}"
    cost = budget.cost_microcents(reply.input_tokens, reply.output_tokens, p)
    if reply.stop_reason == "refusal" or not reply.text:
        budget.record(
            model,
            purpose,
            "error",
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost=cost,
            error=f"geen bruikbare tekst (stop_reason={reply.stop_reason})",
            actor=actor,
            db=db,
        )
        return None, "AI gaf geen bruikbaar antwoord"
    budget.record(
        model,
        purpose,
        "ok",
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        cost=cost,
        actor=actor,
        db=db,
    )
    return reply, None


def advise(
    subject: str, ref: str | None, actor: str, *, use_ai: bool = True, db: Database | None = None
) -> dict[str, Any]:
    db = db or get_db()
    ev = explain.evidence(subject, ref, db)
    det = explain.deterministic(ev)
    reply, fallback = (
        (None, "AI niet gevraagd")
        if not use_ai
        else _call_ai(
            f"advies:{subject}",
            "Waarnemingen (JSON):\n"
            + json.dumps({k: v for k, v in ev.items() if k != "key"}, ensure_ascii=False, default=str)
            + "\n\nRegelgebaseerde diagnose:\n"
            + det["text"],
            actor,
            db,
        )
    )
    mode = "ai" if reply else "deterministic"
    text = reply.text if reply else det["text"]
    model = reply.model if reply else None
    rec_id = recommend(
        det["area"],
        det["title"] + (" (AI-uitleg)" if reply else ""),
        text[:4000],
        risk="advies",
        subject=ev["key"],
        impact="Alleen uitleg; er wordt niets gewijzigd.",
        confidence=det["confidence"],
        dedupe_key=f"ai.advice:{ev['key']}",
        evidence={
            "generated_by": mode,
            "model": model,
            "rule_based": det["lines"],
            "observations": {k: v for k, v in ev.items() if k != "key"},
        },
        db=db,
    )
    # an advice can never carry or create a change set
    db.execute(
        "UPDATE recommendations SET change_id=NULL, risk='advies', status='open', updated_at=now() "
        "WHERE id=%s",
        (rec_id,),
    )
    audit(
        actor,
        "ai.advice",
        f"Advies gemaakt ({'AI' if reply else 'regelgebaseerd'}): {det['title']}",
        target=ev["key"],
        detail={"mode": mode, "fallback_reason": fallback, "model": model},
    )
    return {
        "mode": mode,
        "fallback_reason": None if reply else fallback,
        "title": det["title"],
        "text": text,
        "rule_based": det,
        "recommendation_id": rec_id,
        "model": model,
    }


def test_call(actor: str, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    reply, fallback = _call_ai("test", "Antwoord met precies: 'Verbinding werkt.'", actor, db)
    if reply is None:
        return {"ok": False, "message": fallback}
    return {
        "ok": True,
        "message": reply.text[:200],
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "model": reply.model,
    }
