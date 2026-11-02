"""Provider priority and fallback suggestions → change set on the ``sabnzbd`` applier (after user
confirmation).

Rules (deterministic, conservative):
1. Block accounts go last (highest SAB priority number): they are a last-resort fill for missing articles.
2. Unlimited providers are only re-ordered by *explicitly tested* availability on your own requested articles,
   and only when every one of them has at least ``usenet.min_articles_for_recommendation`` compared articles
   and the difference is at least 1 percentage point. Otherwise the current order is kept and MSO says why.
3. A provider whose account is expired or whose login is refused, but still enabled in SABnzbd, gets a
   suggestion to disable that server (it only produces errors/delays).
Nothing is written until an admin creates the change set, approves it and applies it in beheermodus.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException

from mso import changes
from mso import settings_registry as sr
from mso.connectors.sabnzbd import SERVER_FIELDS, SabClient
from mso.domain import outcomes
from mso.features.usenet import metrics, providers

BAD_STATUS = {"account_expired", "auth_failed"}


def suggestions(period_days: int = 30) -> dict[str, Any]:
    with SabClient.from_config() as client:
        servers = client.servers()
    by_name = {s["name"]: s for s in servers}
    contrib = {c["slug"]: c for c in metrics.provider_contribution(period_days)}
    linked = [p for p in providers.list_rows() if p["sab_server"] in by_name]
    min_n = int(sr.get("usenet.min_articles_for_recommendation"))
    notes: list[str] = []
    insights: list[dict[str, Any]] = []

    unlimited = [p for p in linked if p["kind"] == "unlimited" and p["status"] not in BAD_STATUS]
    blocks = [p for p in linked if p["kind"] == "block" and p["status"] not in BAD_STATUS]
    cur_prio = lambda p: by_name[p["sab_server"]]["priority"]  # noqa: E731
    unlimited.sort(key=cur_prio)
    blocks.sort(key=cur_prio)

    def avail(p: dict[str, Any]) -> float | None:
        c = contrib.get(p["slug"])
        if not c or not c["articles_checked"]:
            return None
        return c["available"] / c["articles_checked"]

    enough = len(unlimited) >= 2 and all(
        (contrib.get(p["slug"]) or {}).get("compared_articles", 0) >= min_n for p in unlimited
    )
    method = "current_order"
    if enough:
        ranked = sorted(unlimited, key=lambda p: (-(avail(p) or 0), cur_prio(p)))
        if ranked != unlimited and abs((avail(ranked[0]) or 0) - (avail(unlimited[0]) or 0)) >= 0.01:
            unlimited = ranked
            method = "explicit_test"
            notes.append(
                "Volgorde op basis van gemeten beschikbaarheid van jouw eigen aangevraagde artikelen."
            )
        else:
            notes.append(
                "Gemeten beschikbaarheid geeft geen reden om de volgorde van onbeperkte providers te "
                "wijzigen."
            )
    elif len(unlimited) >= 2:
        notes.append(
            f"Onvoldoende dekking voor volgorde-advies (minimaal {min_n} vergeleken artikelen per "
            "provider nodig). Draai artikeltests op mislukte of recente NZB's."
        )

    ops: list[dict[str, Any]] = []
    order = unlimited + blocks
    base = min((cur_prio(p) for p in order), default=0)
    for i, p in enumerate(order):
        srv = by_name[p["sab_server"]]
        want = base + i  # SABnzbd: lower number = tried first; blocks come after all unlimited providers
        if srv["priority"] != want:
            why = (
                "blokaccount als laatste redmiddel (alleen voor ontbrekende artikelen)"
                if p["kind"] == "block"
                else "betere gemeten beschikbaarheid eerst"
                if method == "explicit_test"
                else "volgorde behouden"
            )
            ops.append(
                {
                    "path": f"servers/{srv['name']}/priority",
                    "label": f"{p['label']}: prioriteit",
                    "before": srv["priority"],
                    "after": want,
                    "reason": why,
                }
            )
    for p in linked:
        srv = by_name[p["sab_server"]]
        if p["status"] in BAD_STATUS and srv["enable"]:
            ops.append(
                {
                    "path": f"servers/{srv['name']}/enable",
                    "label": f"{p['label']}: server uitschakelen",
                    "before": srv["enable"],
                    "after": 0,
                    "reason": "account verlopen"
                    if p["status"] == "account_expired"
                    else "inloggen geweigerd",
                }
            )
    for p in linked:
        c = contrib.get(p["slug"]) or {}
        if c.get("unique_available"):
            insights.append(
                {
                    "slug": p["slug"],
                    "label": p["label"],
                    "kind": "unique",
                    "text": f"{c['unique_available']} van {c['compared_articles']} vergeleken artikelen "
                    f"waren alleen bij {p['label']} beschikbaar.",
                    "confidence": c["confidence"],
                }
            )
        passive = c.get("passive") or {}
        if passive.get("articles_tried") and by_name[p["sab_server"]]["priority"] > base:
            insights.append(
                {
                    "slug": p["slug"],
                    "label": p["label"],
                    "kind": "fill",
                    "text": f"Als fallback vulde {p['label']} {passive['success_ratio']:.0%} van de "
                    f"{passive['articles_tried']} artikelen die het daar probeerde (passief, "
                    "afhankelijk van serverprioriteit).",
                    "confidence": "low",
                }
            )
    unlinked = [p["label"] for p in providers.list_rows() if p["sab_server"] not in by_name]
    if unlinked:
        notes.append("Niet gekoppeld aan een SABnzbd-server (geen advies mogelijk): " + ", ".join(unlinked))
    return {
        "ops": ops,
        "method": method,
        "notes": notes,
        "insights": insights,
        "servers": [
            {**s, "provider": next((p["slug"] for p in linked if p["sab_server"] == s["name"]), None)}
            for s in sorted(servers, key=lambda s: s["priority"])
        ],
        "risk": "toestemming_nodig",
        "period_days": period_days,
    }


def propose(actor: str, ops: list[dict[str, Any]] | None = None, period_days: int = 30) -> dict[str, Any]:
    """Create a change set (status preview) for the suggestion or for explicit user ops."""
    explanation = (
        "Serverinstellingen in SABnzbd aanpassen. Terugdraaien zet de oorspronkelijke waarden terug."
    )
    if ops is None:
        s = suggestions(period_days)
        ops = s["ops"]
        explanation = " ".join(s["notes"]) or explanation
    else:
        with SabClient.from_config() as client:
            current = {sv["name"]: sv for sv in client.servers()}
        clean = []
        for op in ops:
            path = str(op.get("path", ""))
            parts = path.split("/")
            if len(parts) < 3 or parts[0] != "servers":
                raise HTTPException(422, detail=f"Ongeldig pad '{path}'")
            name, field = "/".join(parts[1:-1]), parts[-1]
            if name not in current:
                raise HTTPException(422, detail=f"SABnzbd-server '{name}' bestaat niet")
            if field not in SERVER_FIELDS:
                raise HTTPException(422, detail="Alleen priority, enable en optional kunnen worden gewijzigd")
            label = {"priority": "prioriteit", "enable": "ingeschakeld", "optional": "optioneel"}[field]
            clean.append(
                {
                    "path": path,
                    "label": f"{current[name]['displayname']}: {label}",
                    "before": current[name][field],
                    "after": op.get("after"),
                }
            )
        ops = [o for o in clean if o["before"] != o["after"]]
    if not ops:
        raise HTTPException(409, detail="Geen wijzigingen nodig: SABnzbd staat al zo ingesteld")
    key = hashlib.sha256(
        json.dumps([(o["path"], o["after"]) for o in ops], sort_keys=True).encode()
    ).hexdigest()[:16]
    change = changes.create(
        "sabnzbd",
        "Usenet-serverprioriteiten aanpassen",
        [{k: o[k] for k in ("path", "label", "before", "after")} for o in ops],
        risk="toestemming_nodig",
        explanation=explanation,
        impact={
            "servers": sorted({o["path"].split("/")[1] for o in ops}),
            "reasons": [o.get("reason") for o in ops if o.get("reason")],
        },
        source="usenet.recommend",
        actor=actor,
        dedupe_key=f"usenet.priority:{key}",
        evaluate_after_h=24 * 7,
    )
    outcomes.recommend(
        "usenet",
        "Usenet-serverprioriteiten aanpassen",
        explanation,
        risk="toestemming_nodig",
        subject="sabnzbd",
        change_id=change["id"],
        dedupe_key=f"usenet.priority:{key}",
        evidence={"ops": [{"label": o["label"], "after": o["after"]} for o in ops]},
    )
    return change
