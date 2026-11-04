"""Provider priority and fallback suggestions → change set on the ``sabnzbd`` applier (after user
confirmation).

Rules (deterministic, conservative):
1. Block accounts go last (highest SAB priority number): they are a last-resort fill for missing articles.
2. Unlimited providers are only re-ordered by *explicitly tested* availability on your own requested articles,
   and only when every one of them has at least ``usenet.min_articles_for_recommendation`` compared articles
   and the difference is at least 1 percentage point. Otherwise the current order is kept and MSO says why.
3. A server whose account is expired or whose login is refused — according to MSO's own check of the linked
   provider OR to SABnzbd's own server status/warnings — but still enabled in SABnzbd, gets a suggestion to
   disable that server (it only produces errors/delays), and an advisory per failing server.
4. A server where SABnzbd found only a small share of the articles it tried gets an observation (passive).
Nothing is written until an admin creates the change set, approves it and applies it in beheermodus.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes
from mso import settings_registry as sr
from mso.connectors.sabnzbd import SERVER_FIELDS, SabClient
from mso.db import get_db
from mso.domain import outcomes
from mso.features.usenet import metrics, providers, serverstatus

LOGGER = logging.getLogger(__name__)
BAD_STATUS = {"account_expired", "auth_failed", "access_denied", "block_exhausted"}
LOW_SUCCESS_RATIO = 0.5
LOW_SUCCESS_MIN_TRIED = 10_000
_DISABLE_REASON = {
    "account_expired": "account verlopen",
    "auth_failed": "inloggen geweigerd",
    "access_denied": "toegang geweigerd",
    "block_exhausted": "blok op",
}


def _bad(p: dict[str, Any], sab: dict[str, dict[str, Any]]) -> str | None:
    """Failing status of a linked provider: MSO's own check, else SABnzbd's view of its server."""
    if p["status"] in BAD_STATUS:
        return str(p["status"])
    st = (sab.get(p["sab_server"] or "") or {}).get("status")
    return st if st in BAD_STATUS else None


def suggestions(period_days: int = 30) -> dict[str, Any]:
    with SabClient.from_config() as client:
        servers = client.servers()
        try:
            serverstatus.refresh(client)
        except Exception as exc:  # fall back to the last persisted SABnzbd status
            LOGGER.info("SABnzbd-serverstatus niet ververst: %s", type(exc).__name__)
    sab = serverstatus.current()
    by_name = {s["name"]: s for s in servers}
    contrib = {c["slug"]: c for c in metrics.provider_contribution(period_days)}
    linked = [p for p in providers.list_rows() if p["sab_server"] in by_name]
    linked_by_server = {p["sab_server"]: p for p in linked}
    min_n = int(sr.get("usenet.min_articles_for_recommendation"))
    notes: list[str] = []
    insights: list[dict[str, Any]] = []

    unlimited = [p for p in linked if p["kind"] == "unlimited" and not _bad(p, sab)]
    blocks = [p for p in linked if p["kind"] == "block" and not _bad(p, sab)]
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
    issues: list[dict[str, Any]] = []
    labels = {p["sab_server"]: p["label"] for p in linked}
    for srv in sorted(servers, key=lambda s: (s["priority"], s["name"])):
        p = linked_by_server.get(srv["name"])
        st = sab.get(srv["name"]) or {}
        own_bad = p["status"] if p and p["status"] in BAD_STATUS else None
        sab_bad = st.get("status") if st.get("status") in serverstatus.FAILING else None
        status = own_bad or sab_bad
        if not status or not srv["enable"]:
            continue
        state = {
            **st,
            "server": srv["name"],
            "displayname": srv["displayname"],
            "short": serverstatus.short_name(srv["displayname"]),
            "status": status,
            "message": st.get("message") if sab_bad == status else p and p["status_detail"],
        }
        title, explanation = serverstatus.recommendation_text(state, labels)
        issues.append(
            {
                "server": srv["name"],
                "name": serverstatus.display_name(state, labels),
                "status": status,
                "label": serverstatus.LABELS.get(status, status),
                "title": title,
                "explanation": explanation,
                "message": state["message"],
                "source": "sabnzbd" if sab_bad == status else "mso",
                "risk": "advies",
            }
        )
        if status in serverstatus.DISABLE_SUGGESTED:
            ops.append(
                {
                    "path": f"servers/{srv['name']}/enable",
                    "label": f"{serverstatus.display_name(state, labels)}: server uitschakelen",
                    "before": srv["enable"],
                    "after": 0,
                    "reason": _DISABLE_REASON.get(status, "server faalt")
                    + (" (volgens SABnzbd)" if sab_bad == status and own_bad != status else ""),
                }
            )
    if issues:
        notes.insert(
            0,
            f"{len(issues)} van de {sum(1 for s in servers if s['enable'])} ingeschakelde SABnzbd-servers "
            "geven fouten: "
            + "; ".join(f"{i['name']}: {i['label'].lower()}" for i in issues)
            + ". Zolang ze aanstaan probeert SABnzbd ze bij elke download.",
        )
    base_prio = min((s["priority"] for s in servers if s["enable"]), default=0)
    for srv in servers:
        stt = sab.get(srv["name"]) or {}
        if not srv["enable"] or stt.get("status") in serverstatus.FAILING:
            continue
        ps = _passive(srv["name"], period_days)
        if ps and ps["tried"] >= LOW_SUCCESS_MIN_TRIED and ps["ratio"] < LOW_SUCCESS_RATIO:
            name = labels.get(srv["name"]) or serverstatus.short_name(srv["displayname"])
            fallback = srv["priority"] > base_prio
            insights.append(
                {
                    "slug": (linked_by_server.get(srv["name"]) or {}).get("slug") or srv["name"],
                    "label": name,
                    "kind": "low_success",
                    "text": f"SABnzbd vond bij {name} maar {_pct(ps['ratio'])} van de {_n(ps['tried'])} "
                    f"geprobeerde artikelen (laatste {period_days} dagen, SABnzbd-teller)."
                    + (
                        " Als fallback-server ziet hij alleen wat eerdere servers misten."
                        if fallback
                        else " Daardoor missen downloads artikelen; oorzaak kan liggen bij verwijderde of "
                        "oude posts of bij het account. Dit zegt niets over retentie in het algemeen."
                    ),
                    "confidence": "low" if fallback else "medium",
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
        "issues": issues,
        "servers": [
            {**s, "provider": next((p["slug"] for p in linked if p["sab_server"] == s["name"]), None)}
            for s in sorted(servers, key=lambda s: s["priority"])
        ],
        "risk": "toestemming_nodig",
        "period_days": period_days,
    }


def _pct(r: float) -> str:
    return f"{r * 100:.1f}".replace(".", ",") + "%"


def _n(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def _passive(server: str, period_days: int) -> dict[str, Any] | None:
    r = get_db().fetchone(
        "SELECT sum(articles_tried) AS tried, sum(articles_success) AS success FROM usenet_server_stats "
        "WHERE server=%s AND day > %s",
        (server, date.today() - timedelta(days=period_days)),
    )
    if not r or not r["tried"] or r["success"] is None:
        return None
    return {"tried": int(r["tried"]), "ratio": int(r["success"]) / int(r["tried"])}


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
