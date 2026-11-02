"""Settings for recovery (category 'herstel')."""

from __future__ import annotations

from mso import settings_registry as sr

CAT = "herstel"
sr.define(
    "herstel.blacklist_ttl_h",
    "Blacklist-duur na mislukking",
    "int",
    168,
    min=1,
    max=8760,
    unit="uur",
    category=CAT,
    help="Een mislukte NZB/infohash wordt zo lang overgeslagen; verdubbelt bij herhaling.",
)
sr.define(
    "herstel.blacklist_max_failures",
    "Harde limiet herhalingen",
    "int",
    2,
    min=1,
    max=10,
    category=CAT,
    help="Na zoveel mislukkingen wordt dezelfde release permanent overgeslagen (nooit eindeloos opnieuw).",
)
sr.define(
    "herstel.media_backoff_base_h",
    "Wachttijd per item na mislukte download",
    "int",
    6,
    min=1,
    max=720,
    unit="uur",
    category=CAT,
    help="Verdubbelt per opeenvolgende mislukking voor hetzelfde item.",
)
sr.define(
    "herstel.media_backoff_max_h",
    "Maximale wachttijd per item",
    "int",
    168,
    min=1,
    max=8760,
    unit="uur",
    category=CAT,
)
sr.define(
    "herstel.manual_sends_per_hour",
    "Handmatige verzendingen per uur",
    "int",
    20,
    min=1,
    max=500,
    category=CAT,
    help="Limiet op 'Verstuur handmatig' naar SABnzbd/qBittorrent.",
)
sr.define(
    "herstel.send_category_mode",
    "Downloadcategorie bij handmatig versturen",
    "enum",
    "apart",
    options=["apart", "mediaguard"],
    category=CAT,
    option_labels={
        "apart": "Aparte MSO-categorie (mso-tv, …) — geen automatische import",
        "mediaguard": "MediaGuard-categorie (tv, tv-anime, movies, movies-anime)",
    },
    help="Standaard een aparte categorie, zodat er nooit automatisch iets in de bibliotheek verandert.",
)
sr.define(
    "herstel.allow_manual_send_connected",
    "Handmatig versturen ook bij MediaGuard-koppeling",
    "bool",
    False,
    category=CAT,
    risk="toestemming_nodig",
    help="Standaard vraagt MSO bij een actieve koppeling MediaGuard om te zoeken (MediaGuard is eigenaar).",
)
sr.define(
    "herstel.candidate_ttl_h", "Geldigheid kandidaten", "int", 24, min=1, max=168, unit="uur", category=CAT
)
sr.define(
    "herstel.baseline_window_days",
    "Venster Source Baseline",
    "int",
    30,
    min=1,
    max=365,
    unit="dagen",
    category=CAT,
)
sr.define(
    "herstel.auto_candidate_scan",
    "Automatisch fallback-kandidaten zoeken",
    "bool",
    False,
    category=CAT,
    risk="automatisch_veilig",
    help="Standaard uit (indexerlimieten). Zoekt periodiek kandidaten voor enkele ontbrekende items en "
    "maakt daar adviezen van; verstuurt nooit zelf iets.",
)
sr.define(
    "herstel.auto_candidates_per_run",
    "Items per automatische ronde",
    "int",
    3,
    min=1,
    max=50,
    category=CAT,
    advanced=True,
)
