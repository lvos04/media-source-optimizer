"""Typed settings registry. Modules declare their settings with ``define(...)``; values live in the
``settings`` table. Locked settings (hard integrity limits) are shown in the UI but can never be changed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import HTTPException

from mso.db import Database, get_db, jsonb


@dataclass
class SettingDef:
    key: str
    label: str
    type: str  # bool|int|float|string|enum|list|duration
    default: Any
    help: str = ""
    category: str = "algemeen"
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None
    risk: str = "advies"  # advies|automatisch_veilig|toestemming_nodig
    locked: bool = False
    restart: bool = False
    unit: str | None = None
    advanced: bool = False
    option_labels: dict[str, str] = field(default_factory=dict)


REGISTRY: dict[str, SettingDef] = {}


def define(key: str, label: str, type: str, default: Any, **kw: Any) -> SettingDef:
    d = SettingDef(key=key, label=label, type=type, default=default, **kw)
    REGISTRY[key] = d
    return d


def _coerce(d: SettingDef, value: Any) -> Any:
    try:
        if d.type == "bool":
            if not isinstance(value, bool):
                raise ValueError
            return value
        if d.type in {"int", "duration"}:
            v: Any = int(value)
        elif d.type == "float":
            v = float(value)
        elif d.type == "enum":
            v = str(value)
            if d.options and v not in d.options:
                raise HTTPException(422, detail=f"{d.label}: ongeldige keuze '{v}'")
            return v
        elif d.type == "list":
            if not isinstance(value, list):
                raise ValueError
            v = [str(x) for x in value]
            if d.options:
                bad = [x for x in v if x not in d.options]
                if bad:
                    raise HTTPException(422, detail=f"{d.label}: ongeldige waarde(n) {bad}")
            return v
        else:
            v = str(value)
            return v
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, detail=f"{d.label}: ongeldige waarde") from exc
    if d.min is not None and v < d.min:
        raise HTTPException(422, detail=f"{d.label}: minimaal {d.min}")
    if d.max is not None and v > d.max:
        raise HTTPException(422, detail=f"{d.label}: maximaal {d.max}")
    return v


def get(key: str, db: Database | None = None) -> Any:
    d = REGISTRY.get(key)
    if d is None:
        raise KeyError(key)
    if d.locked:
        return d.default
    db = db or get_db()
    row = db.fetchone("SELECT value FROM settings WHERE key=%s", (key,))
    return d.default if row is None else row["value"]


def all_values(db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    stored = {r["key"]: r["value"] for r in db.fetchall("SELECT key, value FROM settings")}
    return {k: (d.default if d.locked else stored.get(k, d.default)) for k, d in REGISTRY.items()}


def put(values: dict[str, Any], actor: str, db: Database | None = None) -> dict[str, tuple[Any, Any]]:
    """Validate everything first, then write atomically. Returns {key: (old, new)} of real changes."""
    db = db or get_db()
    current = all_values(db)
    changes: dict[str, tuple[Any, Any]] = {}
    for key, raw in values.items():
        d = REGISTRY.get(key)
        if d is None:
            raise HTTPException(422, detail=f"Onbekende instelling '{key}'")
        if d.locked:
            raise HTTPException(409, detail=f"'{d.label}' is een vergrendelde integriteitsgrens")
        new = _coerce(d, raw)
        if new != current.get(key):
            changes[key] = (current.get(key), new)
    with db.tx() as conn:
        for key, (_, new) in changes.items():
            conn.execute(
                "INSERT INTO settings(key,value,updated_at,updated_by) VALUES (%s,%s,now(),%s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now(), updated_by=EXCLUDED.updated_by",
                (key, jsonb(new), actor))
    return changes


def definitions() -> list[dict[str, Any]]:
    return [asdict(d) for d in REGISTRY.values()]


# ------------------------------------------------------------------ foundation settings
define("ui.theme", "Standaardthema", "enum", "system", options=["dark", "light", "system"],
       category="systeem", help="Standaard voor alle gebruikers; ieder kan in de topbar zelf kiezen.",
       option_labels={"dark": "Donker", "light": "Licht", "system": "Systeem"})
define("security.login_rate_per_min", "Inlogpogingen per minuut", "int", 10, min=3, max=60,
       category="beveiliging", help="Per IP-adres en per gebruikersnaam.")
define("security.api_rate_per_min", "API-verzoeken per minuut per gebruiker", "int", 600, min=60, max=6000,
       category="beveiliging")
define("backup.keep", "Aantal back-ups bewaren", "int", 14, min=1, max=365, category="systeem")
define("backup.auto_daily", "Dagelijkse automatische back-up", "bool", True, category="systeem",
       risk="automatisch_veilig", help="Database-dump zonder geheimen naar APPDATA/backups.")
define("integrity.never_delete_files", "Nooit bestanden verwijderen", "bool", True, locked=True,
       category="integriteit", help="De app verwijdert, hernoemt of verplaatst nooit media- of seedbestanden.")
define("integrity.never_rewrite_seeding", "Nooit actief seedende data herschrijven", "bool", True, locked=True,
       category="integriteit", help="AV1-hardlinks en seedbestanden blijven onaangeroerd (MediaGuard AV1-veiligheid).")
define("integrity.no_public_trackers_on_private", "Geen publieke trackers/DHT op private torrents", "bool", True,
       locked=True, category="integriteit")
define("integrity.no_auto_cancel_subscriptions", "Nooit automatisch abonnementen opzeggen", "bool", True,
       locked=True, category="integriteit")
define("integrity.respect_mediaguard_quality", "MediaGuard-kwaliteitseisen zijn leidend", "bool", True,
       locked=True, category="integriteit",
       help="Juiste aflevering, anime-nummering, audio/ondertitels en HDR/DV worden nooit omzeild.")
