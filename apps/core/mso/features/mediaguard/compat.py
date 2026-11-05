"""Compatibility matrix: every MediaGuard route MSO uses (and the ones it deliberately never uses).

Each entry was verified by reading the read-only reference source ``lvos04/media-guard-ultimate`` at commit
``e92c2ac`` (file + function in ``ref``). The live column comes from the last capability probe of the
configured instance (OpenAPI document, or route probing when OpenAPI is unavailable).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

REFERENCE_COMMIT = "e92c2ac"
REFERENCE_REPO = "lvos04/media-guard-ultimate"


@dataclass(frozen=True)
class RouteSpec:
    id: str
    method: str
    path: str
    role: str  # none|viewer|operator|admin (MediaGuard role required)
    access: str  # read|write|auth
    label: str
    purpose: str
    ref: str  # file::function in the reference source
    required: bool = False  # needed for mode 'connected'
    verified: bool = True  # verified against the reference source at REFERENCE_COMMIT
    probe_params: dict[str, Any] | None = field(default=None, hash=False, compare=False)


ROUTES: list[RouteSpec] = [
    RouteSpec(
        "health",
        "GET",
        "/api/v1/health",
        "none",
        "read",
        "Health/versie",
        "Bereikbaarheid en MediaGuard-versie (zonder login).",
        "mediaguard/api/system.py::health",
    ),
    RouteSpec(
        "auth_login",
        "POST",
        "/api/v1/auth/login",
        "none",
        "auth",
        "Inloggen",
        "Sessie voor de eigen MSO-gebruiker in MediaGuard (cookie mg_session + CSRF, alleen in geheugen).",
        "mediaguard/api/auth.py::login",
    ),
    RouteSpec(
        "auth_me",
        "GET",
        "/api/v1/auth/me",
        "viewer",
        "read",
        "Eigen rol",
        "Controle van de rol (least privilege).",
        "mediaguard/api/auth.py::me",
        required=True,
    ),
    RouteSpec(
        "status",
        "GET",
        "/api/v1/status",
        "viewer",
        "read",
        "Status/noodstop",
        "Versie en actieve noodstop (bij noodstop vraagt MSO geen zoekacties aan).",
        "mediaguard/api/system.py::status",
    ),
    RouteSpec(
        "openapi",
        "GET",
        "/api/v1/openapi.json",
        "none",
        "read",
        "OpenAPI-document",
        "Capability-detectie van routes.",
        "mediaguard/app.py::create_app(openapi_url)",
    ),
    RouteSpec(
        "wanted_list",
        "GET",
        "/api/v1/wanted",
        "viewer",
        "read",
        "Ontbrekende items",
        "Gepagineerde alleen-lezen synchronisatie van ontbrekende items (cache met TTL).",
        "mediaguard/wanted/api.py::wanted_list",
        required=True,
        probe_params={"page_size": 1},
    ),
    RouteSpec(
        "wanted_kpis",
        "GET",
        "/api/v1/wanted/kpis",
        "viewer",
        "read",
        "KPI's",
        "Aantallen en oorzaakverdeling voor de Source Baseline.",
        "mediaguard/wanted/api.py::kpis",
    ),
    RouteSpec(
        "wanted_reasons",
        "GET",
        "/api/v1/wanted/reasons",
        "viewer",
        "read",
        "Oorzaakcodes",
        "Labels en standaardfixes van MediaGuard-oorzaakcodes.",
        "mediaguard/wanted/api.py::reasons",
    ),
    RouteSpec(
        "backfill_quota",
        "GET",
        "/api/v1/wanted/backfill/quota",
        "viewer",
        "read",
        "Backfill-quotum",
        "Resterend MediaGuard-zoekquotum per instantie; MSO blijft daar altijd onder.",
        "mediaguard/wanted/api.py::quota",
    ),
    RouteSpec(
        "wanted_item",
        "GET",
        "/api/v1/wanted/{wanted_id}",
        "viewer",
        "read",
        "Item-detail",
        "Detail van één ontbrekend item.",
        "mediaguard/wanted/api.py::wanted_item",
    ),
    RouteSpec(
        "wanted_diagnosis",
        "GET",
        "/api/v1/wanted/{wanted_id}/diagnosis",
        "viewer",
        "read",
        "Diagnose (opgeslagen)",
        "Alleen met cached=true: laatst opgeslagen diagnose, zonder live-aanroepen of zoekquotum.",
        "mediaguard/wanted/api.py::diagnosis",
        probe_params={"cached": "true"},
    ),
    RouteSpec(
        "policy_effective",
        "GET",
        "/api/v1/policy/effective",
        "viewer",
        "read",
        "Effectieve policy",
        "Kwaliteitseisen (resolutie, NL-audio, grootte, groepen) — leidend, nooit versoepeld.",
        "mediaguard/policy/api.py::get_effective",
        probe_params={"media_type": "tv"},
    ),
    RouteSpec(
        "wanted_search",
        "POST",
        "/api/v1/wanted/{wanted_id}/search",
        "operator",
        "write",
        "Zoek opnieuw",
        "Enige schrijfactie: MediaGuard's eigen 'Zoek nu' (telt mee in en omzeilt nooit het MediaGuard-"
        "quotum), met MSO-quotum, idempotentie en backoff.",
        "mediaguard/wanted/api.py::search_now",
    ),
    RouteSpec(
        "instance_queue",
        "GET",
        "/api/v1/instances/{instance_id}/queue",
        "viewer",
        "read",
        "Wachtrij Sonarr/Radarr",
        "Zoekt het wachtrij-item van een verdachte torrent op (downloadId) voor de blokkade.",
        "mediaguard/instances/manage.py::queue (aec9e64)",
    ),
    RouteSpec(
        "instance_queue_block",
        "DELETE",
        "/api/v1/instances/{instance_id}/queue/{queue_id}",
        "operator",
        "write",
        "Release blokkeren",
        "Alleen bij een goedgekeurde malware-opruiming: blocklist=true, remove_from_client=false — de release "
        "komt op de blocklist van Sonarr/Radarr; MSO verwijdert de torrent zelf.",
        "mediaguard/instances/manage.py::queue_remove (aec9e64)",
    ),
]

NEVER_USED: list[dict[str, str]] = [
    {
        "method": "POST",
        "path": "/api/v1/wanted/{wanted_id}/retry-relaxed",
        "why": "Versoepelt kwaliteitseisen; alleen de gebruiker mag dat zelf in MediaGuard doen.",
    },
    {
        "method": "POST",
        "path": "/api/v1/wanted/actions",
        "why": "Bulkacties (zoeken/negeren) zijn MediaGuard-beheer.",
    },
    {
        "method": "POST",
        "path": "/api/v1/wanted/{wanted_id}/ignore|unignore|keep-monitoring",
        "why": "Wijzigt MediaGuard-status van items.",
    },
    {
        "method": "POST",
        "path": "/api/v1/wanted/backfill-plan",
        "why": "Ververst MediaGuard's eigen wanted-tabellen (bijwerking), ook al is het een dry-run.",
    },
    {
        "method": "GET",
        "path": "/api/v1/wanted/{wanted_id}/diagnosis?interactive=true",
        "why": "Bevraagt indexers en verbruikt MediaGuard-zoekquotum; MSO gebruikt alleen cached=true.",
    },
    {
        "method": "POST",
        "path": "/api/v1/hooks/*",
        "why": "Fork-hooks zijn uitsluitend voor de Sonarr/Radarr-forks.",
    },
    {
        "method": "*",
        "path": "/api/v1/instances|library|profiles|recovery|encoding|…",
        "why": "Bibliotheek, import, AV1 en bestandsvervanging zijn exclusief MediaGuard.",
    },
]


def by_id(route_id: str) -> RouteSpec:
    for r in ROUTES:
        if r.id == route_id:
            return r
    raise KeyError(route_id)


def matrix(
    live: dict[str, bool | None] | None, *, source: str | None = None, checked_at: str | None = None
) -> dict[str, Any]:
    rows = []
    for r in ROUTES:
        d = asdict(r)
        d.pop("probe_params", None)
        state = None if live is None else live.get(r.id)
        d["live"] = "onbekend" if state is None else ("aanwezig" if state else "ontbreekt")
        rows.append(d)
    return {
        "reference": {"repo": REFERENCE_REPO, "commit": REFERENCE_COMMIT},
        "live_source": source,
        "checked_at": checked_at,
        "routes": rows,
        "never_used": NEVER_USED,
    }
