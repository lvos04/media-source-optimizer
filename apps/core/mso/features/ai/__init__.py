"""Optionele AI-adviseur: off by default, explicit opt-in, monthly budget cap, deterministic fallback.

The advisor can only *explain/suggest*: output is stored as a recommendation with risk ``advies`` and never
creates or applies change sets, deletes files or pays for anything. Prompts carry only aggregated,
redacted, non-personal data (no titles, paths, usernames or secrets).
"""

from mso import settings_registry as sr

DEFAULT_URL = "https://api.anthropic.com/v1/messages"

sr.define(
    "ai.enabled",
    "AI-adviseur inschakelen",
    "bool",
    False,
    category="ai",
    help="Expliciete opt-in. Uit = alleen de ingebouwde (regelgebaseerde) uitleg, zonder externe aanroep.",
)
sr.define(
    "ai.monthly_budget_eur",
    "Maandbudget AI",
    "float",
    0.0,
    min=0,
    max=500,
    unit="EUR",
    category="ai",
    help="Bij €0 staat de AI-adviseur uit, ook als hij is ingeschakeld. Aanroepen die het budget zouden "
    "overschrijden worden geweigerd (schatting vooraf met het maximale aantal uitvoertokens).",
)
sr.define(
    "ai.provider",
    "AI-aanbieder",
    "enum",
    "anthropic",
    options=["anthropic"],
    category="ai",
    option_labels={"anthropic": "Anthropic (Claude)"},
)
sr.define(
    "ai.model",
    "Model",
    "string",
    "claude-sonnet-5-5",
    category="ai",
    help="Model-ID bij de aanbieder. Zonder ingestelde prijs voor dit model wordt elke aanroep geweigerd.",
)
sr.define(
    "ai.effort",
    "Denkinspanning",
    "enum",
    "low",
    options=["low", "medium", "high", "none"],
    category="ai",
    option_labels={
        "low": "Laag (goedkoopst)",
        "medium": "Gemiddeld",
        "high": "Hoog",
        "none": "Niet meesturen (oudere modellen)",
    },
    help="Wordt als output_config.effort meegestuurd; kies 'Niet meesturen' voor modellen zonder effort.",
)
sr.define(
    "ai.max_output_tokens",
    "Maximaal aantal uitvoertokens per aanroep",
    "int",
    2000,
    min=200,
    max=16000,
    category="ai",
    advanced=True,
)
sr.define(
    "ai.api_url",
    "API-adres",
    "string",
    DEFAULT_URL,
    category="ai",
    advanced=True,
    help="Alleen wijzigen voor een eigen proxy. Moet https:// zijn (of http:// in een testomgeving).",
)
sr.define(
    "ai.timeout_s", "Time-out per aanroep", "int", 60, min=5, max=300, unit="s", category="ai", advanced=True
)
