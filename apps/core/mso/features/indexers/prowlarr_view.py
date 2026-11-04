"""Read-only view of MediaGuard's Prowlarr state (tags ``anime``/``normal``, Applications, indexer scopes) and
detection of disagreements. MSO never fights MediaGuard's reconcile: conflicts become *recommendations*
with an
idempotent configuration preview (what to change, where — usually in MediaGuard), never an automatic write.
The only change MSO can itself propose is registering its own endpoint (see
prowlarr_sync.propose_registration).
"""

from __future__ import annotations

from typing import Any

from mso.connectors.prowlarr import ProwlarrClient, field_value
from mso.db import get_db
from mso.domain import outcomes
from mso.features.indexers.prowlarr_sync import (
    MG_APP_PREFIX,
    REGISTRATIONS,
    derive_scope,
    find_registrations,
    is_anime_name,
    is_mso_indexer,
    tag_map,
)
from mso.features.newznab_server import keys

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _conflict(
    code: str,
    severity: str,
    subject: str,
    title: str,
    explanation: str,
    preview: dict[str, Any] | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "subject": subject,
        "title": title,
        "explanation": explanation,
        "preview": preview or {},
        "action": action,
    }


def build_view(client: ProwlarrClient | None = None) -> dict[str, Any]:
    client = client or ProwlarrClient.from_config()
    tags = client.tags()
    apps = client.applications()
    indexers = client.indexers()
    tmap = tag_map(tags)
    by_label = {v: k for k, v in tmap.items()}
    pub = keys.public_base_url()
    conflicts: list[dict[str, Any]] = []
    mg_apps = [a for a in apps if str(a.get("name") or "").startswith(MG_APP_PREFIX)]
    managed = bool(mg_apps) or ("anime" in by_label and "normal" in by_label)

    missing = [t for t in ("anime", "normal") if t not in by_label]
    if missing:
        conflicts.append(
            _conflict(
                "scope_tags_missing",
                "info",
                "prowlarr:tags",
                f"Bereik-tags ontbreken: {', '.join(missing)}",
                "MediaGuard maakt de tags 'anime' en 'normal' aan bij het inrichten van Prowlarr. Zonder "
                "deze tags "
                "behandelt MSO alle Prowlarr-indexers als 'beide' en houdt anime/normaal alleen via "
                "categorieën uit "
                "elkaar.",
                {"where": "MediaGuard → Inrichten → Indexers (Prowlarr-modus)", "create_tags": missing},
            )
        )

    idx_view = []
    scope_counts = {"anime": 0, "normal": 0}
    for idx in indexers:
        labels = sorted({tmap.get(int(t), str(t)) for t in idx.get("tags") or []})
        scopes = [s for s in ("normal", "anime") if s in labels]
        for s in scopes:
            scope_counts[s] += 1
        mso = is_mso_indexer(idx, pub)
        anime_name = is_anime_name(idx.get("name"), idx.get("definitionName"))
        derived = derive_scope(idx, tmap)
        idx_view.append(
            {
                "id": idx.get("id"),
                "name": idx.get("name"),
                "protocol": idx.get("protocol"),
                "privacy": idx.get("privacy"),
                "enable": idx.get("enable"),
                "tags": labels,
                "scopes": derived["scopes"],
                "untagged": not scopes,
                "scope_source": derived["scope_source"],
                "library_relevant": derived["library_relevant"],
                "is_mso": mso,
                "anime_name": anime_name,
            }
        )
        sid = f"prowlarr:{idx.get('id')}"
        if not scopes and not mso and managed and derived["library_relevant"] is not False:
            conflicts.append(
                _conflict(
                    "indexer_untagged",
                    "warning",
                    sid,
                    f"'{idx.get('name')}' heeft geen bereik-tag",
                    "Applicaties met tags krijgen alleen indexers met een overeenkomende tag; deze "
                    "indexer wordt dus "
                    "mogelijk niet naar de MediaGuard-instanties gesynchroniseerd. MSO leidt het bereik af "
                    f"uit de Prowlarr-categorieën: {' + '.join(derived['scopes'])}.",
                    {
                        "where": "MediaGuard → Inrichten → Indexers → bereik per indexer",
                        "indexer": idx.get("name"),
                        "current_tags": labels,
                        "suggested_tags": ["anime"]
                        if anime_name or derived["scopes"] == ["anime"]
                        else ["anime", "normal"],
                    },
                )
            )
        if anime_name and "normal" in scopes and "anime" not in scopes:
            conflicts.append(
                _conflict(
                    "anime_tagged_normal",
                    "warning",
                    sid,
                    f"Anime-indexer '{idx.get('name')}' heeft alleen tag 'normal'",
                    "De naam wijst op een anime-indexer, maar hij is alleen aan normale series/films "
                    "gekoppeld. Anime-"
                    "instanties krijgen hem daardoor niet en normale instanties krijgen anime-resultaten.",
                    {
                        "where": "MediaGuard → Inrichten → Indexers → bereik per indexer",
                        "indexer": idx.get("name"),
                        "current_tags": labels,
                        "suggested_tags": ["anime"],
                    },
                )
            )

    for kind, spec in REGISTRATIONS.items():
        regs = find_registrations(indexers, kind, pub)
        if len(regs) > 1:
            conflicts.append(
                _conflict(
                    "mso_registered_twice",
                    "error",
                    f"mso:{kind}",
                    f"MSO staat {len(regs)}× als {spec['label']}-indexer in Prowlarr",
                    "Dubbele registraties geven dubbele zoekopdrachten en dubbele resultaten. Verwijder "
                    "in Prowlarr "
                    "alle behalve één (MSO verwijdert niets zelf).",
                    {
                        "where": "Prowlarr → Indexers",
                        "registrations": [
                            {"id": r.get("id"), "name": r.get("name"), "baseUrl": field_value(r, "baseUrl")}
                            for r in regs
                        ],
                        "keep": regs[0].get("id"),
                    },
                )
            )
        elif len(regs) == 1 and not regs[0].get("enable"):
            conflicts.append(
                _conflict(
                    "mso_registration_disabled",
                    "info",
                    f"mso:{kind}",
                    f"MSO-{spec['label']}-indexer staat uit in Prowlarr",
                    "Prowlarr gebruikt het MSO-endpoint nu niet.",
                    {"where": "Prowlarr → Indexers", "indexer": regs[0].get("name")},
                )
            )
    mso_any = [i for i in idx_view if i["is_mso"]]
    if not mso_any:
        conflicts.append(
            _conflict(
                "mso_not_registered",
                "info",
                "mso:registration",
                "MSO is (nog) niet als indexer in Prowlarr geregistreerd",
                "Registreren is optioneel. Via een wijzigingsvoorstel (toestemming nodig) voegt MSO "
                "alleen zijn eigen "
                "Newznab/Torznab-endpoint toe; tags en applicaties blijven ongewijzigd.",
                {"where": "MSO → NZB-indexers → Newznab/Torznab-endpoint"},
                action="register",
            )
        )

    app_view = []
    for a in apps:
        labels = sorted({tmap.get(int(t), str(t)) for t in a.get("tags") or []})
        owned = str(a.get("name") or "").startswith(MG_APP_PREFIX)
        app_view.append(
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "implementation": a.get("implementation"),
                "syncLevel": a.get("syncLevel"),
                "tags": labels,
                "mediaguard": owned,
            }
        )
        for s in ("anime", "normal"):
            if s in labels and scope_counts[s] == 0:
                conflicts.append(
                    _conflict(
                        "scope_without_indexers",
                        "warning",
                        f"app:{a.get('id')}:{s}",
                        f"Applicatie '{a.get('name')}' wacht op indexers met tag '{s}', maar die zijn er "
                        f"niet",
                        "Deze instantie krijgt geen indexers via Prowlarr.",
                        {
                            "where": "MediaGuard → Inrichten → Indexers",
                            "application": a.get("name"),
                            "scope": s,
                        },
                    )
                )
        if owned and not any(s in labels for s in ("anime", "normal")):
            conflicts.append(
                _conflict(
                    "mediaguard_app_untagged",
                    "info",
                    f"app:{a.get('id')}",
                    f"MediaGuard-applicatie '{a.get('name')}' heeft geen bereik-tag",
                    "MediaGuard herstelt dit zelf bij de volgende reconcile; MSO wijzigt applicaties nooit.",
                    {"where": "MediaGuard → Inrichten → Toepassen"},
                )
            )
    conflicts.sort(key=lambda c: (SEVERITY_ORDER[c["severity"]], c["code"], c["subject"]))
    return {
        "mediaguard_managed": managed,
        "tags": [{"id": k, "label": v} for k, v in sorted(tmap.items())],
        "applications": app_view,
        "indexers": idx_view,
        "conflicts": conflicts,
        "policy": "MSO leest dit alleen. Tags en applicaties zijn van MediaGuard; MSO stelt hooguit voor.",
    }


def refresh_recommendations(view: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Turn conflicts into (idempotent) recommendations; resolve ones that disappeared. Never applies
    anything."""
    view = view or build_view()
    keys_now: list[str] = []
    for c in view["conflicts"]:
        if c["severity"] == "info" and c["code"] != "mso_not_registered":
            continue
        dk = f"prowlarr_view:{c['code']}:{c['subject']}"
        keys_now.append(dk)
        outcomes.recommend(
            "indexers",
            c["title"],
            c["explanation"],
            risk="toestemming_nodig" if c["action"] == "register" else "advies",
            subject=c["subject"],
            impact="Configuratievoorbeeld; niets wordt automatisch toegepast",
            confidence=0.9 if c["severity"] != "info" else 0.5,
            dedupe_key=dk,
            evidence={
                "code": c["code"],
                "severity": c["severity"],
                "preview": c["preview"],
                "action": c["action"],
            },
        )
    get_db().execute(
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE status='open' "
        "AND dedupe_key LIKE 'prowlarr\\_view:%%' AND NOT (dedupe_key = ANY(%s))",
        (keys_now,),
    )
    return view["conflicts"]
