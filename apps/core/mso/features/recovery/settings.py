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
sr.define(
    "recovery.fallback_on_failure",
    "Na mislukte download een vervanger zoeken",
    "bool",
    True,
    category=CAT,
    risk="advies",
    help="Bij een mislukte download (SABnzbd/qBittorrent) zet MSO de release op de blacklist, zoekt een "
    "vervanger die aan alle MediaGuard-eisen voldoet en maakt daar een wijzigingsvoorstel van. Verstuurt "
    "zelf niets zonder goedkeuring.",
)
sr.define(
    "recovery.fallback_searches_per_run",
    "Zoekopdrachten per ronde (mislukte downloads)",
    "int",
    5,
    min=1,
    max=50,
    category=CAT,
    advanced=True,
    help="Beperkt het aantal indexer-zoekopdrachten per analyse-ronde (elke 30 minuten).",
)
sr.define(
    "recovery.auto_send_fallback",
    "Vervanger automatisch versturen",
    "bool",
    False,
    category=CAT,
    risk="toestemming_nodig",
    help="Standaard uit: elk voorstel 'Versturen naar SABnzbd/qBittorrent' vraagt goedkeuring. Aan: nieuwe "
    "voorstellen krijgen risico 'automatisch veilig' en worden door Automatisering verstuurd (alleen in "
    "beheermodus, met 'Automatisch veilig toepassen: Ontbrekend & herstel' aan, binnen de limieten per uur). "
    "Versturen kan niet worden teruggedraaid.",
)
sr.define(
    "recovery.autopilot_fallback",
    "In Autopilot: mislukte downloads zelf afhandelen",
    "bool",
    True,
    category=CAT,
    risk="automatisch_veilig",
    help="Alleen in Autopilot. MediaGuard-items: MSO vraagt MediaGuard opnieuw te zoeken via de eigen route "
    "(binnen de MSO- en MediaGuard-quota; MediaGuard blijft eigenaar, MSO verstuurt niets zelf). Zelfstandige "
    "items: MSO verstuurt de beste vervanger automatisch (binnen 'Handmatige verzendingen per uur'). "
    "Serverproblemen: SABnzbd probeert de download opnieuw zodra de kapotte servers uit staan.",
)
