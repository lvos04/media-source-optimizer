"""Per-model prices (user-editable) and monthly budget accounting for the AI advisor."""

from __future__ import annotations

import math
from typing import Any

from mso import settings_registry as sr
from mso.db import Database, get_db

# Seeded defaults (Anthropic list prices in US cents per million tokens, 2026-10). Users edit these;
# the migration seeds them too, this copy covers a truncated table.
DEFAULT_PRICES: dict[str, tuple[int, int]] = {
    "claude-sonnet-5-5": (200, 1000),
    "claude-opus-5-5": (400, 2000),
    "claude-haiku-5-5": (10, 50),
}
MICRO = 1_000_000  # microcents per cent


def price(model: str, db: Database | None = None) -> tuple[int, int] | None:
    row = (db or get_db()).fetchone(
        "SELECT input_cents_per_mtok, output_cents_per_mtok FROM ai_model_prices WHERE model=%s", (model,)
    )
    if row:
        return int(row["input_cents_per_mtok"]), int(row["output_cents_per_mtok"])
    return DEFAULT_PRICES.get(model)


def prices(db: Database | None = None) -> list[dict[str, Any]]:
    rows = {
        r["model"]: dict(r) for r in (db or get_db()).fetchall("SELECT * FROM ai_model_prices ORDER BY model")
    }
    for m, (i, o) in DEFAULT_PRICES.items():
        rows.setdefault(
            m,
            {
                "model": m,
                "input_cents_per_mtok": i,
                "output_cents_per_mtok": o,
                "updated_by": "standaard",
                "updated_at": None,
            },
        )
    return sorted(rows.values(), key=lambda r: r["model"])


def set_price(
    model: str, input_cents: int, output_cents: int, actor: str, db: Database | None = None
) -> None:
    (db or get_db()).execute(
        "INSERT INTO ai_model_prices(model,input_cents_per_mtok,output_cents_per_mtok,updated_by) VALUES "
        "(%s,%s,%s,%s) ON CONFLICT (model) DO UPDATE SET input_cents_per_mtok=EXCLUDED.input_cents_per_mtok, "
        "output_cents_per_mtok=EXCLUDED.output_cents_per_mtok, updated_by=EXCLUDED.updated_by, "
        "updated_at=now()",
        (model, input_cents, output_cents, actor),
    )


def cost_microcents(input_tokens: int, output_tokens: int, p: tuple[int, int]) -> int:
    # cents/Mtok × tokens = microcents (exact integer)
    return input_tokens * p[0] + output_tokens * p[1]


def estimate_tokens(text: str) -> int:
    """Conservative: ~3 characters per token for Dutch/JSON plus overhead."""
    return math.ceil(len(text) / 3) + 50


def month_usage(db: Database | None = None) -> dict[str, Any]:
    row = (db or get_db()).fetchone(
        "SELECT COALESCE(sum(cost_microcents),0) AS micro, count(*) FILTER (WHERE status='ok') AS calls, "
        "count(*) FILTER (WHERE status<>'ok') AS refused_or_failed, COALESCE(sum(input_tokens),0) AS "
        "input_tokens, "
        "COALESCE(sum(output_tokens),0) AS output_tokens FROM ai_usage WHERE ts >= date_trunc('month', now())"
    )
    micro = int(row["micro"]) if row else 0
    return {
        "spent_cents": round(micro / MICRO, 4),
        "spent_microcents": micro,
        "calls": int(row["calls"] or 0),
        "refused_or_failed": int(row["refused_or_failed"] or 0),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
    }


def budget_cents() -> float:
    return float(sr.get("ai.monthly_budget_eur")) * 100


def check(estimated_microcents: int, db: Database | None = None) -> tuple[bool, str]:
    cap = budget_cents() * MICRO
    spent = month_usage(db)["spent_microcents"]
    if spent + estimated_microcents > cap:
        return False, (
            f"Maandbudget zou worden overschreden (besteed {spent / MICRO:.2f} ct, geschat "
            f"{estimated_microcents / MICRO:.2f} ct, plafond {cap / MICRO:.0f} ct)"
        )
    return True, ""


def record(
    model: str,
    purpose: str,
    status: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: int = 0,
    error: str | None = None,
    actor: str | None = None,
    db: Database | None = None,
) -> None:
    (db or get_db()).execute(
        "INSERT INTO ai_usage(model,purpose,status,input_tokens,output_tokens,cost_microcents,error,actor) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (model, purpose, status, input_tokens, output_tokens, cost, error, actor),
    )
