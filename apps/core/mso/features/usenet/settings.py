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

sr.define(
    "usenet.server_error_reminder_days",
    "Herinnering bij serverfouten na",
    "int",
    3,
    min=1,
    max=60,
    unit="dagen",
    category=CAT,
    help="Meldt SABnzbd langer dan dit aantal dagen fouten voor een ingeschakelde server, dan maakt MSO een "
    "herinnering (vernieuwen, inloggegevens controleren of uitschakelen). Bij 'Account verlopen' komt de "
    "herinnering meteen.",
)

# ------------------------------------------------------------------ automatic availability sample after sending
sr.define(
    "usenet.auto_sample_after_send",
    "Beschikbaarheid automatisch meten na versturen",
    "bool",
    True,
    category=CAT,
    help="Na elke NZB die MSO zelf naar SABnzbd stuurt controleert MSO een kleine steekproef van precies die "
    "artikelen met STAT bij elke provider met inloggegevens in MSO: 1 verbinding per provider, binnen de "
    "dag- en maandbudgetten hieronder. STAT downloadt geen artikelinhoud.",
)
sr.define(
    "usenet.auto_sample_size",
    "Artikelen per automatische steekproef",
    "int",
    25,
    min=5,
    max=200,
    category=CAT,
    help="Aantal message-id's per provider per verstuurde NZB (gespreid over alle bestanden).",
)
sr.define(
    "usenet.stat_budget_daily_connections",
    "STAT-budget: verbindingen per provider per dag",
    "int",
    24,
    min=1,
    max=1000,
    category=CAT,
    help="Alleen voor automatische steekproeven; expliciete tests tellen mee maar worden niet geblokkeerd.",
)
sr.define(
    "usenet.stat_budget_daily_commands",
    "STAT-budget: commando's per provider per dag",
    "int",
    1000,
    min=10,
    max=100000,
    category=CAT,
)
sr.define(
    "usenet.stat_budget_daily_kb",
    "STAT-budget: dataverkeer per provider per dag",
    "int",
    512,
    min=16,
    max=102400,
    unit="KB",
    category=CAT,
    help="STAT is klein (~100 bytes per commando en antwoord).",
)
sr.define(
    "usenet.stat_budget_monthly_connections",
    "STAT-budget: verbindingen per provider per maand",
    "int",
    300,
    min=1,
    max=20000,
    category=CAT,
)
sr.define(
    "usenet.stat_budget_monthly_commands",
    "STAT-budget: commando's per provider per maand",
    "int",
    15000,
    min=10,
    max=2000000,
    category=CAT,
)
sr.define(
    "usenet.stat_budget_monthly_kb",
    "STAT-budget: dataverkeer per provider per maand",
    "int",
    8192,
    min=16,
    max=2000000,
    unit="KB",
    category=CAT,
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

# ------------------------------------------------------------------ Autopilot (1.5.0)
sr.define(
    "usenet.auto_disable_after_h",
    "Kapotte server automatisch uitschakelen na",
    "int",
    6,
    min=1,
    max=168,
    unit="uur",
    category=CAT,
    risk="automatisch_veilig",
    help="Alleen in Autopilot: een SABnzbd-server met een duidelijk accountprobleem (inloggen geweigerd, toegang "
    "geweigerd, blok op) wordt na zoveel uur automatisch uitgeschakeld; 'account verlopen' meteen. Nooit de "
    "laatste ingeschakelde server. Terug te draaien; MSO probeert de server later zelf opnieuw.",
)
sr.define(
    "usenet.auto_probe_interval_h",
    "Uitgeschakelde server opnieuw proberen na",
    "int",
    24,
    min=6,
    max=168,
    unit="uur",
    category=CAT,
    help="Alleen in Autopilot en alleen als SABnzbd de servertest niet aanbiedt: MSO zet een automatisch "
    "uitgeschakelde server na zoveel uur als proef weer aan. Keert de fout terug, dan gaat hij weer uit en verdubbelt "
    "de wachttijd (backoff). Met de servertest zet MSO een server pas weer aan als de test slaagt.",
)
sr.define(
    "usenet.auto_probe_max_interval_h",
    "Maximale wachttijd tussen proeven",
    "int",
    168,
    min=24,
    max=720,
    unit="uur",
    category=CAT,
    advanced=True,
)
sr.define(
    "usenet.auto_probe_max",
    "Maximaal aantal mislukte proeven",
    "int",
    5,
    min=1,
    max=20,
    category=CAT,
    advanced=True,
    help="Alleen zonder servertest: daarna zet MSO de server niet meer als proef aan; 'Opnieuw proberen' op de "
    "pagina Usenet-providers blijft beschikbaar.",
)
sr.define(
    "usenet.probe_watch_h",
    "Proef geslaagd na foutvrije periode van",
    "int",
    2,
    min=1,
    max=48,
    unit="uur",
    category=CAT,
    advanced=True,
)
sr.define(
    "usenet.retry_failed_downloads",
    "Mislukte downloads door serverproblemen opnieuw proberen",
    "bool",
    True,
    category=CAT,
    risk="automatisch_veilig",
    help="Downloads die mislukten terwijl een server faalde, laat MSO SABnzbd opnieuw proberen zodra de kapotte "
    "servers uit staan en er minstens één werkende server is. In Autopilot automatisch; anders één voorstel "
    "'N mislukte downloads opnieuw proberen'. Eén keer per download.",
)
sr.define(
    "usenet.retry_per_hour", "Max. opnieuw geprobeerde downloads per uur", "int", 20, min=1, max=500, category=CAT
)
sr.define(
    "usenet.retry_per_day", "Max. opnieuw geprobeerde downloads per dag", "int", 100, min=1, max=2000, category=CAT
)
sr.define(
    "usenet.retry_max_age_days",
    "Mislukte downloads opnieuw proberen tot",
    "int",
    14,
    min=1,
    max=90,
    unit="dagen",
    category=CAT,
    advanced=True,
    help="Oudere mislukte downloads in de SABnzbd-geschiedenis worden niet meer automatisch opnieuw geprobeerd.",
)

# ------------------------------------------------------------------ server tests + account data (1.6.0)
sr.define(
    "usenet.server_test_interval_h",
    "Uitgeschakelde server testen elke",
    "int",
    6,
    min=1,
    max=48,
    unit="uur",
    category=CAT,
    help="MSO laat SABnzbd een server die MSO heeft uitgezet regelmatig testen met SABnzbd's eigen servertest "
    "(dezelfde als 'Test server' in SABnzbd; SABnzbd gebruikt zijn eigen opgeslagen wachtwoord, MSO ziet het nooit). "
    "Pas als de test slaagt gaat de server in Autopilot weer aan. De test verandert niets en draait in elke "
    "bedrijfsmodus.",
)
sr.define(
    "usenet.server_test_slow_interval_h",
    "Testen na een week zonder geslaagde test",
    "int",
    24,
    min=6,
    max=168,
    unit="uur",
    category=CAT,
    advanced=True,
    help="Staat een server langer dan een week uit en slaagt de test nog steeds niet, dan test MSO minder vaak. MSO "
    "geeft nooit op: minstens één keer per week. Bij 'account verlopen' test MSO wekelijks (zo ziet MSO een "
    "verlenging) en meteen zodra je de server in SABnzbd aanpast.",
)
