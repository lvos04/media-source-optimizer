"""Settings for the Usenet engine (category ``usenet``)."""

from __future__ import annotations

from mso import settings_registry as sr

CAT = "usenet"

sr.define(
    "usenet.history_poll_s",
    "SABnzbd-geschiedenis ophalen elke",
    "int",
    300,
    min=60,
    max=3600,
    unit="s",
    category=CAT,
    help="Incrementeel: alleen wijzigingen sinds de vorige keer worden verwerkt.",
)
sr.define(
    "usenet.history_limit",
    "Max. geschiedenisregels per ronde",
    "int",
    200,
    min=20,
    max=2000,
    category=CAT,
    advanced=True,
)
sr.define(
    "usenet.category_map",
    "SABnzbd-categorie → bibliotheekcategorie",
    "list",
    [],
    category=CAT,
    help="Regels 'sab-categorie=categorie' met als doel tv, tv-anime, movies, movies-anime of none. "
    "SABnzbd-categorieën zonder regel worden automatisch op naam herkend (tv/series/sonarr → tv, "
    "anime → tv-anime, movies/films/radarr → movies, anime + film/radarr → movies-anime); '*' en "
    "onbekende namen krijgen geen bibliotheekcategorie.",
)
sr.define(
    "usenet.stats_poll_min",
    "Serverstatistieken ophalen elke",
    "int",
    60,
    min=10,
    max=1440,
    unit="min",
    category=CAT,
    help="Passieve per-server bytes en artikeltellers uit SABnzbd.",
)
sr.define(
    "usenet.check_interval_min",
    "Providerstatus controleren elke",
    "int",
    60,
    min=10,
    max=1440,
    unit="min",
    category=CAT,
    help="Eén korte NNTP-verbinding per provider (inloggen + uitloggen).",
)
sr.define(
    "usenet.check_connections",
    "Max. NNTP-verbindingen per provider voor MSO",
    "int",
    1,
    min=1,
    max=2,
    category=CAT,
    help="MSO gebruikt nooit meer dan dit aantal (en nooit meer dan het ingestelde maximum "
    "van het account), zodat SABnzbd zijn verbindingen houdt.",
)
sr.define(
    "usenet.nntp_timeout_s", "NNTP-time-out", "int", 15, min=3, max=60, unit="s", category=CAT, advanced=True
)
sr.define(
    "usenet.article_sample_size",
    "Artikelsteekproef per test",
    "int",
    100,
    min=10,
    max=1000,
    category=CAT,
    help="Aantal echte artikelen (message-id's) uit de NZB dat per provider met STAT wordt "
    "gecontroleerd. Gespreid over alle bestanden.",
)
sr.define(
    "usenet.auto_test_failed",
    "Mislukte downloads automatisch op artikelbeschikbaarheid testen",
    "bool",
    False,
    category=CAT,
    risk="automatisch_veilig",
    help="Alleen voor NZB's waarvan MSO de inhoud kent (via MSO verzonden of geüpload). Uit = alleen "
    "expliciete tests.",
)
sr.define(
    "usenet.min_articles_for_recommendation",
    "Min. gecontroleerde artikelen voor prioriteitsadvies",
    "int",
    200,
    min=20,
    max=100000,
    category=CAT,
    help="Onder deze dekking geeft MSO geen volgorde-advies op basis van beschikbaarheid.",
)

# ------------------------------------------------------------------ optional header index (opt-in)
HI = "usenet.headerindex"
sr.define(
    f"{HI}.enabled",
    "Eigen header-index (opt-in)",
    "bool",
    False,
    category=CAT,
    risk="toestemming_nodig",
    help="Begrensde eigen index van berichtkoppen in een paar toegestane groepen. Vervangt GEEN indexer of "
    "provider; veel moderne posts zijn geobfusceerd en daardoor niet te herkennen.",
)
sr.define(
    f"{HI}.groups",
    "Toegestane nieuwsgroepen",
    "list",
    [],
    category=CAT,
    help="Alleen deze groepen worden gelezen (whitelist), maximaal 10.",
)
sr.define(
    f"{HI}.provider",
    "Provider voor header-index",
    "string",
    "",
    category=CAT,
    help="Slug van een ingestelde provider.",
)
sr.define(
    f"{HI}.retention_days", "Bewaartermijn headers", "int", 7, min=1, max=30, unit="dagen", category=CAT
)
sr.define(
    f"{HI}.max_storage_mb",
    "Max. opslag header-index",
    "int",
    256,
    min=16,
    max=4096,
    unit="MB",
    category=CAT,
    help="Bij het bereiken stopt indexeren en verschijnt een melding.",
)
sr.define(
    f"{HI}.max_headers_per_run", "Max. headers per ronde", "int", 20000, min=500, max=200000, category=CAT
)
sr.define(f"{HI}.max_runs_per_day", "Max. rondes per dag", "int", 4, min=1, max=24, category=CAT)
sr.define(
    f"{HI}.max_download_mb_per_run",
    "Max. download per ronde",
    "int",
    50,
    min=1,
    max=1000,
    unit="MB",
    category=CAT,
    help="Bandbreedtebudget voor XOVER-data per ronde.",
)
sr.define(
    f"{HI}.max_run_seconds",
    "Max. duur per ronde",
    "int",
    120,
    min=10,
    max=1800,
    unit="s",
    category=CAT,
    help="CPU/tijdbudget per ronde.",
)
