"""Typed settings registry. Modules declare their settings with ``define(...)``; values live in the
``settings`` table. Locked settings (hard integrity limits) are shown in the UI but can never be changed.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
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


# ------------------------------------------------------------------ Hub address (Peil O-11, ADR-0006 punt 1, ADR-0009)
# Contract 1.3 §2.3 regel 5 + §3.2 (adresregels): only http(s)://<host>[:<port>] without path, query, fragment or
# credentials; IPv4 only as a.b.c.d (four decimal parts 0-255 without a leading zero) — shortened, decimal, octal or hex
# forms (127.1, 2130706433, 0x7f000001, 010.0.0.1, 0) are invalid, because a browser or resolver turns them into another
# address; IPv6 only between []; one trailing dot of a name does not count (localhost. = localhost); never loopback,
# link-local or 0.0.0.0/::. MSO never connects to this address (it is only the href of "Terug naar de Hub"), so the
# per-connection DNS check of §3.2 has nothing to check here. apps/web/lib/hub.ts applies exactly the same rules
# (shared vectors: tests/vectoren/hub_url.json).
HUB_URL_ERROR = "Hub-adres: gebruik http(s)://<NAS-adres>:<poort>, zonder pad"
HUB_URL_IP_ERROR = (
    "Hub-adres: schrijf een IP-adres als vier getallen van 0 tot 255 met punten ertussen, zonder voorloopnullen"
)
HUB_URL_BLOCKED_ERROR = (
    "Hub-adres: geen localhost, loopback-, link-local- of 0.0.0.0-adres; gebruik het LAN-adres van de NAS"
)
# a label that a resolver/browser reads as a number: decimal digits or 0x + hex (WHATWG "ends in a number")
_NUMERIC_LABEL = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)")
_NAME_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
_AUTHORITY = re.compile(r"(?P<host>\[[^\]]*\]|[^:\[\]]*)(?::(?P<port>[0-9]*))?")


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked_ip(ip.ipv4_mapped)
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.IPv4Network("0.0.0.0/8"):
        return True  # 0.0.0.0 and "this network": never a destination
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or (
        isinstance(ip, ipaddress.IPv4Address) and ip == ipaddress.IPv4Address("255.255.255.255")
    )


def _ipv6_text(ip: ipaddress.IPv6Address) -> str:
    """Serialisation as a browser writes it (WHATWG URL): lower-case hex groups, the first longest run of two or more
    zero groups as '::', an IPv4-mapped address in hex (::ffff:a00:1) — so core and UI normalise identically."""
    groups = [int.from_bytes(ip.packed[i : i + 2], "big") for i in range(0, 16, 2)]
    start, length, i = -1, 1, 0
    while i < 8:
        j = i
        while j < 8 and groups[j] == 0:
            j += 1
        if j - i > length:
            start, length = i, j - i
        i = max(j, i + 1)
    parts = [format(g, "x") for g in groups]
    if start < 0:
        return ":".join(parts)
    return ":".join(parts[:start]) + "::" + ":".join(parts[start + length :])


def _hub_host(raw_host: str) -> str:
    """Validated, normalised host (lower case; IPv6 compressed between []) or HTTPException 422."""
    h = raw_host.lower()
    if h.startswith("["):
        inner = h[1:-1]
        if "%" in inner:  # zone id: link-local only, never a Hub address
            raise HTTPException(422, detail=HUB_URL_BLOCKED_ERROR)
        try:
            ip6 = ipaddress.IPv6Address(inner)
        except ValueError as exc:
            raise HTTPException(422, detail=HUB_URL_ERROR) from exc
        if _blocked_ip(ip6):
            raise HTTPException(422, detail=HUB_URL_BLOCKED_ERROR)
        return f"[{_ipv6_text(ip6)}]"
    name = h[:-1] if h.endswith(".") else h  # one trailing dot does not count
    labels = name.split(".")
    if not name or len(name) > 253:
        raise HTTPException(422, detail=HUB_URL_ERROR)
    if _NUMERIC_LABEL.fullmatch(labels[-1]) or all(_NUMERIC_LABEL.fullmatch(lb) for lb in labels):
        try:
            ip4 = ipaddress.IPv4Address(name)  # Python 3.9.5+: no leading zeros, exactly four parts
        except ValueError as exc:
            raise HTTPException(422, detail=HUB_URL_IP_ERROR) from exc
        if str(ip4) != name:
            raise HTTPException(422, detail=HUB_URL_IP_ERROR)
        if _blocked_ip(ip4):
            raise HTTPException(422, detail=HUB_URL_BLOCKED_ERROR)
        return name
    if not all(_NAME_LABEL.fullmatch(lb) for lb in labels):
        raise HTTPException(422, detail=HUB_URL_ERROR)
    if name == "localhost" or name.endswith(".localhost"):
        raise HTTPException(422, detail=HUB_URL_BLOCKED_ERROR)
    return name


def validate_hub_url(d: SettingDef, value: str) -> str:
    """Empty = no link. Otherwise the normalised form ``scheme://host[:port]`` (lower-case scheme and host, IPv6
    compressed, no trailing slash or dot); anything else → 422 with a Dutch explanation (contract 1.3 §3.2)."""
    v = value.strip()
    if not v:
        return ""
    if any(c.isspace() for c in v) or "\\" in v or "?" in v or "#" in v or len(v) > 300:
        raise HTTPException(422, detail=HUB_URL_ERROR)
    scheme, sep, rest = v.partition("://")
    if not sep or scheme.lower() not in {"http", "https"}:
        raise HTTPException(422, detail=HUB_URL_ERROR)
    authority, slash, path = rest.partition("/")
    if (slash and path) or "@" in authority:
        raise HTTPException(422, detail=HUB_URL_ERROR)
    m = _AUTHORITY.fullmatch(authority)
    if not m or not m.group("host"):
        raise HTTPException(422, detail=HUB_URL_ERROR)
    host = _hub_host(m.group("host"))
    port_txt = m.group("port")
    port = int(port_txt) if port_txt else None
    if port is not None and not 1 <= port <= 65535:
        raise HTTPException(422, detail=HUB_URL_ERROR)
    return f"{scheme.lower()}://{host}" + (f":{port}" if port is not None else "")


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
