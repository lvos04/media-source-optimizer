"""Settings of Slice A (zoeken / indexers / Newznab-server). Imported by every Slice A module so the keys
exist regardless of discovery order."""

from __future__ import annotations

from typing import Any

from mso import settings_registry as sr

LANGS = ["en", "ja", "nl", "de", "fr", "es", "it", "ko", "zh"]
NEWZNAB_BACKENDS = ["direct", "spotweb", "headerindex", "prowlarr"]

# ------------------------------------------------------------------ zoeken
sr.define(
    "search.timeout_s",
    "Time-out per bron",
    "int",
    15,
    min=2,
    max=120,
    unit="s",
    category="zoeken",
    help="Maximale wachttijd per indexer/backend. Een time-out wordt apart gerapporteerd, niet als 'leeg'.",
)
sr.define(
    "search.per_source_limit",
    "Maximaal aantal resultaten per bron",
    "int",
    100,
    min=10,
    max=500,
    category="zoeken",
)
sr.define(
    "search.max_hops",
    "Maximaal aantal hops in de zoekketen",
    "int",
    2,
    min=1,
    max=5,
    category="zoeken",
    help="Anti-lus: een zoekverzoek dat al via meer knooppunten is gegaan wordt geweigerd (hop_limit).",
)
sr.define(
    "search.required_languages",
    "Vereiste audiotalen",
    "list",
    [],
    options=LANGS,
    category="zoeken",
    help="Leeg = geen eis. Resultaten waarvan de taal aantoonbaar anders is worden weggefilterd; "
    "onbekende taal blijft zichtbaar met markering.",
)
sr.define(
    "search.excluded_languages",
    "Uitgesloten audiotalen",
    "list",
    [],
    options=LANGS,
    category="zoeken",
    help="Bijv. 'de' om Duitse dubs te verbergen (multi/dual audio blijft toegestaan).",
)
sr.define(
    "search.result_ttl_h",
    "Bewaartijd zoekresultaten",
    "int",
    24,
    min=1,
    max=168,
    unit="u",
    category="zoeken",
    help="Hoe lang een gekozen resultaat nog verstuurd of gedownload kan worden.",
)
sr.define(
    "search.send_rate_per_min",
    "Verstuur-acties per minuut per gebruiker",
    "int",
    20,
    min=1,
    max=120,
    category="zoeken",
)
sr.define(
    "search.prowlarr_enabled",
    "Zoeken via Prowlarr",
    "bool",
    True,
    category="zoeken",
    help="Prowlarr wordt nooit bevraagd voor verzoeken die zelf van Prowlarr komen (lusbeveiliging).",
)
sr.define(
    "search.prowlarr_excluded_indexer_ids",
    "Uitgesloten Prowlarr-indexers",
    "list",
    [],
    category="zoeken",
    advanced=True,
    help="Indexer-id's die MSO nooit via Prowlarr bevraagt (naast automatisch herkende MSO-registraties).",
)

# ------------------------------------------------------------------ indexers
sr.define(
    "indexers.health_interval_min",
    "Gezondheidscontrole indexers",
    "int",
    15,
    min=5,
    max=1440,
    unit="min",
    category="indexers",
)
sr.define(
    "indexers.default_rate_per_min",
    "Standaard verzoeken per minuut per indexer",
    "int",
    30,
    min=1,
    max=600,
    category="indexers",
)
sr.define(
    "indexers.backoff_max_min",
    "Maximale wachttijd na fouten",
    "int",
    360,
    min=5,
    max=1440,
    unit="min",
    category="indexers",
    help="Exponentiële backoff na opeenvolgende fouten, begrensd op deze waarde.",
)
sr.define(
    "indexers.reliability_window_d",
    "Venster betrouwbaarheidsscore",
    "int",
    14,
    min=1,
    max=90,
    unit="d",
    category="indexers",
)
sr.define(
    "prowlarr.sync_interval_min",
    "Prowlarr synchroniseren elke",
    "int",
    15,
    min=5,
    max=1440,
    unit="min",
    category="indexers",
)

# ------------------------------------------------------------------ newznab/torznab server
sr.define(
    "newznab.public_url",
    "Publieke URL van MSO (voor Prowlarr)",
    "string",
    "",
    category="indexers",
    help="Basis-URL waarop Prowlarr MSO bereikt, bijv. http://mso:8000. Leeg = MSO_PUBLIC_URL.",
)
sr.define(
    "newznab.backends",
    "Bronnen voor het Newznab/Torznab-endpoint",
    "list",
    ["direct", "spotweb", "headerindex"],
    options=NEWZNAB_BACKENDS,
    category="indexers",
    help="Prowlarr wordt nooit gebruikt als de aanroeper Prowlarr zelf is (cyclus).",
)
sr.define(
    "newznab.rate_per_min",
    "Newznab-verzoeken per minuut per sleutel",
    "int",
    60,
    min=1,
    max=1200,
    category="indexers",
)
sr.define(
    "newznab.daily_quota",
    "Newznab-verzoeken per dag per sleutel",
    "int",
    0,
    min=0,
    max=1_000_000,
    category="indexers",
    help="0 = geen daglimiet.",
)
sr.define(
    "newznab.max_limit", "Maximaal resultaten per pagina", "int", 100, min=10, max=500, category="indexers"
)


def get(key: str, default: Any = None) -> Any:
    try:
        return sr.get(key)
    except Exception:  # noqa: BLE001  (no DB in pure unit tests)
        return default if default is not None else sr.REGISTRY[key].default
