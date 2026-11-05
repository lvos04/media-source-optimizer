"""Settings for the MediaGuard integration (category 'mediaguard')."""

from __future__ import annotations

from mso import settings_registry as sr

CAT = "mediaguard"
sr.define(
    "mediaguard.sync_interval_min",
    "Synchronisatie-interval",
    "int",
    15,
    min=5,
    max=1440,
    unit="min",
    category=CAT,
    help="Hoe vaak MSO de ontbrekende items, KPI's en het quotum uit MediaGuard leest.",
)
sr.define(
    "mediaguard.cache_ttl_min",
    "Geldigheid cache",
    "int",
    60,
    min=5,
    max=1440,
    unit="min",
    category=CAT,
    help="Na deze tijd geldt de gecachte MediaGuard-data als verouderd (nooit leidend).",
)
sr.define(
    "mediaguard.sync_max_pages",
    "Max. pagina's per synchronisatieronde",
    "int",
    25,
    min=1,
    max=100,
    category=CAT,
    advanced=True,
    help="Per pagina 200 items (25 pagina's = 5.000 items per ronde); begrenst de belasting van MediaGuard. "
    "Een langere wachtlijst wordt over meerdere rondes gelezen: de volgende ronde (na 5 minuten) gaat verder "
    "waar de vorige stopte.",
)
sr.define(
    "mediaguard.search_quota_per_hour",
    "Zoekverzoeken per uur (MSO)",
    "int",
    10,
    min=0,
    max=100,
    category=CAT,
    help="MSO-eigen limiet voor 'Vraag MediaGuard opnieuw te zoeken'. MediaGuard's eigen "
    "backfill-quotum geldt daarbovenop en wordt nooit overschreden.",
)
sr.define(
    "mediaguard.search_quota_per_day", "Zoekverzoeken per dag (MSO)", "int", 40, min=0, max=1000, category=CAT
)
sr.define(
    "mediaguard.quota_reserve",
    "Reserve MediaGuard-quotum",
    "int",
    2,
    min=0,
    max=100,
    category=CAT,
    help="Zoveel zoekacties per instantie laat MSO altijd over voor MediaGuard's eigen backfill.",
)
sr.define(
    "mediaguard.search_idempotency_window_min",
    "Idempotentievenster per item",
    "int",
    360,
    min=10,
    max=10080,
    unit="min",
    category=CAT,
    help="Binnen dit venster wordt een tweede verzoek voor hetzelfde item niet opnieuw verstuurd.",
)
sr.define(
    "mediaguard.search_backoff_base_min",
    "Wachttijd na mislukt verzoek",
    "int",
    30,
    min=1,
    max=1440,
    unit="min",
    category=CAT,
    help="Verdubbelt bij elke opeenvolgende mislukking (exponential backoff).",
)
sr.define(
    "mediaguard.search_backoff_max_h",
    "Maximale wachttijd",
    "int",
    48,
    min=1,
    max=720,
    unit="uur",
    category=CAT,
)
sr.define(
    "mediaguard.auto_search_requests",
    "Automatisch zoekverzoeken sturen",
    "bool",
    False,
    category=CAT,
    risk="toestemming_nodig",
    help="Standaard uit. Alleen in beheermodus en zonder pauze vraagt MSO MediaGuard zelf om opnieuw te "
    "zoeken voor items waarvoor MSO een kandidaat vond die aan alle MediaGuard-eisen voldoet.",
)
