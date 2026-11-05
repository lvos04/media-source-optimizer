"""Settings (category ``torrent``)."""

from __future__ import annotations

from typing import Any

from mso import settings_registry as sr
from mso.domain.categories import MEDIA_CATEGORIES

CAT = "torrent"

sr.define(
    "torrent.line_down_mbit",
    "Lijncapaciteit download",
    "float",
    0.0,
    category=CAT,
    min=0,
    max=100000,
    unit="Mbit/s",
    help="Wat je internetabonnement echt levert. 0 = onbekend (geen snelheidsadvies).",
)
sr.define(
    "torrent.line_up_mbit",
    "Lijncapaciteit upload",
    "float",
    0.0,
    category=CAT,
    min=0,
    max=100000,
    unit="Mbit/s",
    help="Nodig voor de uploadlimiet-bewaking. 0 = onbekend.",
)
sr.define(
    "torrent.upload_cap_pct",
    "Uploadlimiet maximaal",
    "int",
    80,
    category=CAT,
    min=10,
    max=95,
    unit="%",
    help="qBittorrent mag nooit meer dan dit percentage van de uploadcapaciteit gebruiken (nooit 100%).",
)
sr.define(
    "torrent.max_connections_cap",
    "Maximum verbindingen (bovengrens)",
    "int",
    1000,
    category=CAT,
    min=50,
    max=5000,
    help="Tuning stelt nooit meer globale verbindingen voor dan dit.",
)
sr.define(
    "torrent.max_connections_per_torrent_cap",
    "Maximum verbindingen per torrent (bovengrens)",
    "int",
    200,
    category=CAT,
    min=10,
    max=1000,
)
sr.define(
    "torrent.expected_interface",
    "Verwachte VPN-netwerkinterface",
    "string",
    "",
    category=CAT,
    help="Bijv. 'wg0' of 'tun0'. Alleen diagnose: MSO waarschuwt als qBittorrent niet aan deze interface "
    "gebonden is, maar wijzigt de binding nooit zelf.",
)
sr.define(
    "torrent.disk_reserve_gb",
    "Schijfreserve downloads",
    "float",
    50.0,
    category=CAT,
    min=0,
    max=100000,
    unit="GB",
    help="Waarschuwing wanneer vrije ruimte min resterende downloads onder deze reserve zakt.",
)
sr.define(
    "torrent.category_map",
    "Categorie-koppeling qBittorrent → MediaGuard",
    "list",
    [
        "tv=tv",
        "tv-anime=tv-anime",
        "movies=movies",
        "movies-anime=movies-anime",
        "sonarr=tv",
        "tv-sonarr=tv",
        "radarr=movies",
        "anime=tv-anime",
    ],
    category=CAT,
    help="Regels 'qbittorrent-categorie=mediacategorie' (tv, tv-anime, movies, movies-anime).",
)
sr.define(
    "torrent.path_map",
    "Padkoppeling qBittorrent → MSO",
    "list",
    [],
    category=CAT,
    advanced=True,
    help="Regels 'pad-in-qbittorrent=pad-in-MSO', bijv. '/data=/data' (alleen voor de read-only "
    "hardlinkcontrole).",
)
sr.define(
    "torrent.stalled_fail_hours",
    "Vastgelopen als mislukt na",
    "int",
    72,
    category=CAT,
    min=6,
    max=720,
    unit="uur",
    help="Zonder voortgang telt een torrent na deze tijd als mislukt in de statistiek. "
    "MSO stopt of verwijdert hem nooit.",
)
sr.define(
    "torrent.false_eta_factor",
    "Valse-ETA-factor",
    "float",
    3.0,
    category=CAT,
    min=1.5,
    max=20,
    advanced=True,
    help="ETA is onbetrouwbaar als de gemeten snelheid deze factor lager ligt dan de ETA veronderstelt.",
)
sr.define(
    "torrent.sample_retention_days",
    "Bewaartermijn torrentmetingen",
    "int",
    14,
    category=CAT,
    min=2,
    max=90,
    unit="dagen",
)
sr.define(
    "torrent.autotune_enabled",
    "Automatisch tunen",
    "bool",
    False,
    category=CAT,
    risk="automatisch_veilig",
    help="Alleen kleine, begrensde wijzigingen ('automatisch veilig'), één tegelijk, alleen in beheermodus "
    "en niet gepauzeerd. Standaard uit. In Autopilot tunet MSO altijd (met meting en automatisch terugdraaien).",
)
sr.define(
    "torrent.autotune_auto_rollback",
    "Automatisch terugdraaien bij verslechtering",
    "bool",
    True,
    category=CAT,
    risk="automatisch_veilig",
    help="Alleen als automatisering is toegestaan (beheermodus, niet gepauzeerd).",
)
sr.define(
    "torrent.autopilot_remedies_per_hour",
    "Autopilot: herstelacties per uur",
    "int",
    6,
    category=CAT,
    min=0,
    max=30,
    risk="automatisch_veilig",
    help="Alleen in Autopilot: maximaal zoveel veilige herstelacties per uur (opnieuw aankondigen bij een "
    "trackerfout, recheck bij ontbrekende bestanden van een gestopte torrent). 0 = uit. MSO verwijdert nooit.",
)
sr.define(
    "torrent.evaluation_hours",
    "Meetperiode na wijziging",
    "int",
    36,
    category=CAT,
    min=24,
    max=48,
    unit="uur",
)
sr.define("torrent.baseline_hours", "Basislijnperiode", "int", 24, category=CAT, min=6, max=168, unit="uur")
sr.define(
    "torrent.tuning_hysteresis_pct",
    "Hysterese",
    "int",
    15,
    category=CAT,
    min=5,
    max=50,
    unit="%",
    help="Pas bij een verschil groter dan dit percentage telt een wijziging als beter of slechter.",
)
sr.define(
    "torrent.tuning_jitter_pct",
    "Jitter meetmoment",
    "int",
    10,
    category=CAT,
    min=0,
    max=25,
    unit="%",
    advanced=True,
)
sr.define(
    "torrent.tuning_cooldown_hours",
    "Rust tussen tuningstappen",
    "int",
    12,
    category=CAT,
    min=1,
    max=168,
    unit="uur",
)
sr.define(
    "torrent.tuning_min_samples",
    "Minimum metingen per vergelijking",
    "int",
    12,
    category=CAT,
    min=3,
    max=1000,
    advanced=True,
)
sr.define(
    "torrent.cross_seed_enabled",
    "Cross-seed zoeken",
    "bool",
    False,
    category=CAT,
    risk="toestemming_nodig",
    help="Alleen op basis van identieke payload (bestandsnamen + groottes) en alleen voor trackers waar je "
    "cross-seeden expliciet toestaat. Ieder voorstel vereist goedkeuring.",
)
sr.define(
    "torrent.cross_seed_max_per_run",
    "Cross-seed: torrents per zoekronde",
    "int",
    10,
    category=CAT,
    min=1,
    max=100,
    advanced=True,
)


def get(key: str) -> Any:
    return sr.get(f"torrent.{key}")


def category_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in get("category_map") or []:
        if "=" not in entry:
            continue
        k, v = (s.strip() for s in entry.split("=", 1))
        if v in MEDIA_CATEGORIES:
            out[k.lower()] = v
    return out


def media_category(qbit_category: str | None) -> str | None:
    if not qbit_category:
        return None
    m = category_map()
    c = qbit_category.lower()
    if c in m:
        return m[c]
    # sub-categories 'tv/xyz' map by their first segment
    head = c.split("/", 1)[0]
    return m.get(head)


def path_map() -> list[tuple[str, str]]:
    out = []
    for entry in get("path_map") or []:
        if "=" in entry:
            a, b = (s.strip() for s in entry.split("=", 1))
            if a and b:
                out.append((a.rstrip("/"), b.rstrip("/")))
    return out


KIB = 1024


def kib_floor(bps: int) -> int:
    """qBittorrent stores speed limits in whole KiB/s (it rounds a written byte value down to a multiple of
    1024: 200000000 → 199999488). Every limit MSO computes is rounded the same way (down, so a cap is never
    exceeded) so what is written equals what qBittorrent reports back."""
    return (int(bps) // KIB) * KIB if bps > 0 else int(bps)


def upload_cap_bps() -> int | None:
    """Maximum allowed upload (bytes/s) = cap% of the declared line upload capacity, rounded down to whole
    KiB/s (qBittorrent's unit); None when unknown."""
    up = float(get("line_up_mbit") or 0)
    if up <= 0:
        return None
    cap = kib_floor(int(up * 1_000_000 / 8 * int(get("upload_cap_pct")) / 100))
    return cap if cap > 0 else None


def line_down_bps() -> int | None:
    d = float(get("line_down_mbit") or 0)
    return int(d * 1_000_000 / 8) if d > 0 else None
