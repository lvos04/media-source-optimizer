"""Automatisering: recommendations inbox, per-area 'automatisch veilig' toggles, the unattended
``automation.apply_safe`` job and post-apply evaluation via area-registered evaluators.

Other slices register evaluators with::

    from mso.features.automation import register_evaluator
    register_evaluator("qbittorrent", lambda change: "keep" | "rollback" | "unknown")
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
    help="Alleen voor automatisch toegepaste wijzigingen en alleen als een evaluatie expliciet "
    "'terugdraaien' concludeert. Anders verschijnt een voorstel om terug te draaien.",
)

__all__ = ["AREAS", "EVALUATORS", "TARGET_AREA", "register_evaluator"]
