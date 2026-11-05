"""Automatisering: recommendations inbox, per-area 'automatisch veilig' toggles, the unattended
``automation.apply_safe`` job and post-apply evaluation via area-registered evaluators.

Other slices register evaluators with::

    from mso.features.automation import register_evaluator
    from mso.features.automation.evaluators import compare, measure, evaluation, windows
    register_evaluator("qbittorrent", fn)
    # fn(change) -> evaluation([...]) | (verdict, details) | legacy 'keep'|'rollback'|'unknown'
    # verdict: 'better'|'same'|'worse'|'insufficient'

Targets without an own evaluator use ``evaluators.generic_evaluator`` (download outcomes of the target's
protocol/client, torrent speed for torrent clients). See ``evaluators.py`` for the measures.
"""

from mso import settings_registry as sr
from mso.features.automation.evaluators import EVALUATORS, register_evaluator

AREAS: dict[str, str] = {
    "usenet": "Usenet (SABnzbd, providers)",
    "torrent": "Torrent (qBittorrent)",
    "indexers": "Indexers (Prowlarr)",
    "mediaguard": "MediaGuard-koppeling",
    "recovery": "Ontbrekend & herstel",
}
# change_sets.target -> area (a linked recommendation's area wins; impact.area as fallback)
TARGET_AREA: dict[str, str] = {
    "sabnzbd": "usenet",
    "nntp": "usenet",
    "qbittorrent": "torrent",
    "transmission": "torrent",
    "deluge": "torrent",
    "prowlarr": "indexers",
    "mediaguard": "mediaguard",
}

for _area, _label in AREAS.items():
    sr.define(
        f"automation.{_area}.auto_apply_safe",
        f"Automatisch veilig toepassen: {_label}",
        "bool",
        False,
        category="automatisering",
        risk="automatisch_veilig",
        help="Alleen wijzigingen met risicoklasse 'automatisch veilig' worden zonder bevestiging toegepast, "
        "en alleen in beheermodus zonder pauze. Advies en 'toestemming nodig' nooit.",
    )
sr.define(
    "automation.max_age_h",
    "Maximale leeftijd van een automatisch toe te passen voorstel",
    "int",
    24,
    min=1,
    max=720,
    unit="uur",
    category="automatisering",
    help="Oudere voorstellen kunnen achterhaald zijn en worden niet meer automatisch toegepast.",
)
sr.define(
    "automation.max_per_hour",
    "Maximaal aantal automatische wijzigingen per uur",
    "int",
    5,
    min=1,
    max=60,
    category="automatisering",
    help="Globale limiet over alle onderdelen samen.",
)
sr.define(
    "automation.auto_rollback",
    "Automatisch terugdraaien na negatieve evaluatie",
    "bool",
    False,
    category="automatisering",
    risk="automatisch_veilig",
    help="Als de controle na een wijziging 'slechter' meet, wordt de wijziging automatisch teruggedraaid "
    "(alleen in beheermodus zonder pauze, met een auditregel). Voor qBittorrent geldt ook "
    "'torrent.autotune_auto_rollback'. Verwijderen/uitschakelen wordt nooit geëvalueerd of automatisch "
    "teruggedraaid, en een terugdraai die zelf iets zou uitschakelen wordt alleen voorgesteld.",
)
sr.define(
    "automation.evaluation_hours",
    "Standaard meetperiode na een wijziging",
    "int",
    24,
    min=1,
    max=336,
    unit="uur",
    category="automatisering",
    help="Voor toegepaste wijzigingen zonder eigen meetperiode. Daarna wordt vergeleken met de basislijn "
    "(dezelfde periode vóór het toepassen).",
)
sr.define(
    "automation.evaluation_hysteresis_pct",
    "Hysterese bij evaluatie",
    "float",
    10.0,
    min=1,
    max=100,
    unit="%",
    category="automatisering",
    help="Alleen een relatief verschil van minstens dit percentage telt als beter of slechter; kleiner is "
    "'gelijk'.",
)
sr.define(
    "automation.evaluation_min_samples",
    "Minimaal aantal metingen per periode",
    "int",
    10,
    min=1,
    max=10000,
    category="automatisering",
    advanced=True,
    help="Minder metingen in de basislijn of de meetperiode geeft 'onvoldoende data'; er wordt dan niets "
    "teruggedraaid.",
)
sr.define(
    "automation.evaluation_baseline_days",
    "Maximale lengte basislijn",
    "int",
    7,
    min=1,
    max=60,
    unit="dagen",
    category="automatisering",
    advanced=True,
    help="De basislijn is even lang als de meetperiode, maar nooit langer dan dit.",
)

__all__ = ["AREAS", "EVALUATORS", "TARGET_AREA", "register_evaluator"]
