"""Typed settings registry. Modules declare their settings with ``define(...)``; values live in the
``settings`` table. Locked settings (hard integrity limits) are shown in the UI but can never be changed.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

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
# Extra validation for string settings with a format (key → function(def, value) → normalised value, raises 422).
# Kept outside SettingDef so the /settings definitions response keeps exactly the same shape.
VALIDATORS: dict[str, Callable[[SettingDef, str], str]] = {}


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
            check = VALIDATORS.get(d.key)
            return check(d, v) if check else v
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
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now(), "
                "updated_by=EXCLUDED.updated_by",
                (key, jsonb(new), actor),
            )
    return changes


def definitions() -> list[dict[str, Any]]:
    return [asdict(d) for d in REGISTRY.values()]


# ------------------------------------------------------------------ Hub address (Peil O-11, ADR-0006 punt 1)
HUB_URL_ERROR = "Hub-adres: gebruik http(s)://<NAS-adres>:<poort>, zonder pad"


def _blocked_host(host: str) -> bool:
    """Loopback, link-local and unspecified addresses (contract 1.2 §3.2, HUB_URL rules); 'localhost' too."""
    h = host.strip("[]").lower().rstrip(".")
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def validate_hub_url(d: SettingDef, value: str) -> str:
    """Empty = no link. Otherwise http(s)://<host>[:<port>] without path, query, fragment or credentials; never
    loopback, link-local or 0.0.0.0/::. Returns the normalised form (lower-case scheme and host, no trailing slash)."""
    v = value.strip()
    if not v:
        return ""
    try:
        parts = urlsplit(v)
        port = parts.port  # raises ValueError for an invalid port
    except ValueError as exc:
        raise HTTPException(422, detail=HUB_URL_ERROR) from exc
    host = parts.hostname or ""
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or "\\" in v
        or any(c.isspace() for c in v)
        or _blocked_host(host)
    ):
        raise HTTPException(422, detail=HUB_URL_ERROR)
    shown_host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme.lower()}://{shown_host}" + (f":{port}" if port is not None else "")


# ------------------------------------------------------------------ foundation settings
define(
    "systeem.hub_url",
    "Hub-adres",
    "string",
    "",
    category="systeem",
    help="Vorm: http(s)://<NAS-adres>:<poort>. Geen localhost. Leeg = geen link 'Terug naar de Hub'.",
)
VALIDATORS["systeem.hub_url"] = validate_hub_url
define(
    "ui.theme",
    "Standaardthema",
    "enum",
    "system",
    options=["dark", "light", "system"],
    category="systeem",
    help="Standaard voor alle gebruikers; ieder kan in de topbar zelf kiezen.",
    option_labels={"dark": "Donker", "light": "Licht", "system": "Systeem"},
)
define(
    "security.login_rate_per_min",
    "Inlogpogingen per minuut",
    "int",
    10,
    min=3,
    max=60,
    category="beveiliging",
    help="Per IP-adres en per gebruikersnaam.",
)
define(
    "security.api_rate_per_min",
    "API-verzoeken per minuut per gebruiker",
    "int",
    600,
    min=60,
    max=6000,
    category="beveiliging",
)
define("backup.keep", "Aantal back-ups bewaren", "int", 14, min=1, max=365, category="systeem")
define(
    "backup.auto_daily",
    "Dagelijkse automatische back-up",
    "bool",
    True,
    category="systeem",
    risk="automatisch_veilig",
    help="Database-dump zonder geheimen naar APPDATA/backups.",
)
define(
    "integrity.never_delete_files",
    "Nooit zelfstandig bestanden verwijderen",
    "bool",
    True,
    locked=True,
    category="integriteit",
    help="MSO verwijdert, hernoemt of verplaatst nooit uit zichzelf media- of seedbestanden. Enige uitzondering: "
    "verdachte (uitvoerbare) downloads, en alleen via een voorstel dat een beheerder expliciet goedkeurt; "
    "nooit automatisch.",
)
define(
    "integrity.never_rewrite_seeding",
    "Nooit actief seedende data herschrijven",
    "bool",
    True,
    locked=True,
    category="integriteit",
    help="AV1-hardlinks en seedbestanden blijven onaangeroerd (MediaGuard AV1-veiligheid).",
)
define(
    "integrity.no_public_trackers_on_private",
    "Geen publieke trackers/DHT op private torrents",
    "bool",
    True,
    locked=True,
    category="integriteit",
)
define(
    "integrity.no_auto_cancel_subscriptions",
    "Nooit automatisch abonnementen opzeggen",
    "bool",
    True,
    locked=True,
    category="integriteit",
)
define(
    "integrity.respect_mediaguard_quality",
    "MediaGuard-kwaliteitseisen zijn leidend",
    "bool",
    True,
    locked=True,
    category="integriteit",
    help="Juiste aflevering, anime-nummering, audio/ondertitels en HDR/DV worden nooit omzeild.",
)
