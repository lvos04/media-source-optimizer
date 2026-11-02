"""Kosten & besparing: manually entered service costs, bounded retrospective analysis, the
"Wat gebeurt er als ik deze dienst uitschakel?" simulation, scenario comparison and renewal reminders.

Honesty rules (PLAN §3 Kosten, §9): only user-entered prices and observed downloads are used; no
guaranteed savings, no invented backbone diversity, no unique-retention claims from byte totals and
never an automatic cancellation.
"""

from mso import settings_registry as sr

sr.define(
    "costs.renewal_reminder_days",
    "Herinnering voor verlenging (dagen vooraf)",
    "int",
    14,
    min=1,
    max=120,
    category="kosten",
    unit="dagen",
    help="Zoveel dagen vóór de verlengdatum verschijnt een advies op Automatisering. Er wordt nooit "
    "automatisch opgezegd.",
)
sr.define(
    "costs.default_period_days",
    "Standaard analyseperiode",
    "int",
    30,
    min=7,
    max=365,
    category="kosten",
    unit="dagen",
    help="Periode waarover gebruik, unieke hits en de uitschakel-simulatie worden berekend.",
)
sr.define(
    "costs.min_sample",
    "Minimale steekproef voor uitspraken",
    "int",
    10,
    min=1,
    max=1000,
    category="kosten",
    advanced=True,
    help="Onder dit aantal waargenomen downloads wordt de betrouwbaarheid altijd als 'laag' getoond.",
)
