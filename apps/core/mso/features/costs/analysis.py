"""Bounded, retrospective cost/benefit analysis over observed data only.

Nothing here predicts the future or promises savings. Every number carries its method, the share of
downloads that had enough evidence (``coverage``) and an explicit low/high range where the data cannot
decide.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mso.db import Database, get_db
from mso.domain.outcomes import FAILURE_REASONS, outcome_at

LOGGER = logging.getLogger(__name__)
USENET_KINDS = {"usenet_provider", "block_account"}
INDEXER_KINDS = {"nzb_indexer", "torrent_indexer", "tracker", "spotweb", "header_index"}
MAX_ROWS = 50_000
GB = 1024**3

DISCLAIMERS = [
    "Dit is een terugblik op waargenomen downloads in de gekozen periode, geen voorspelling.",
    "Er is geen garantie dat downloads zonder deze dienst wel of niet zouden slagen.",
    "Kosten zijn de bedragen die je zelf hebt ingevuld; de app zegt nooit automatisch iets op.",
]
USENET_DISCLAIMER = (
    "Bij Usenet vraagt de downloadclient een back-upprovider pas om artikelen als de primaire provider ze "
    "mist. Dat een download volledig via één provider binnenkwam, bewijst dus niet dat andere providers de "
    "artikelen niet hadden — daarom staan die downloads in de onzekerheidsmarge."
)


# ------------------------------------------------------------------ helpers
def _now() -> datetime:
    return datetime.now(UTC)


def ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def confidence_label(coverage: float | None, n: int, min_sample: int, cap: str | None = None) -> str:
    order = ["onbekend", "laag", "middel", "hoog"]
    if coverage is None or n == 0:
        label = "onbekend"
    elif n < min_sample or coverage < 0.5:
        label = "laag"
    elif coverage < 0.8:
        label = "middel"
    else:
        label = "hoog"
    if cap and order.index(label) > order.index(cap):
        label = cap
    return label


def _min_sample() -> int:
    from mso import settings_registry as sr

    try:
        return int(sr.get("costs.min_sample"))
    except Exception:
        return 10


def provider_keys(source_id: str | None) -> set[str]:
    """``provider_bytes`` keys may be the full sources.id ('provider:eweka') or the bare slug."""
    if not source_id:
        return set()
    keys = {source_id}
    if ":" in source_id:
        keys.add(source_id.split(":", 1)[1])
    return keys


def provider_bytes_of(row: dict[str, Any], keys: set[str]) -> int:
    pb = row.get("provider_bytes") or {}
    return sum(int(v or 0) for k, v in pb.items() if k in keys)


def _other_provider_bytes(row: dict[str, Any], keys: set[str]) -> int:
    pb = row.get("provider_bytes") or {}
    return sum(int(v or 0) for k, v in pb.items() if k not in keys)


def outcomes_in_period(
    period_days: int, protocol: str | None = None, db: Database | None = None
) -> list[dict[str, Any]]:
    db = db or get_db()
    sql = (
        "SELECT id, protocol, client, status, failure_reason, source_id, release_key, title, provider_bytes, "
        f"meta, size_bytes, downloaded_bytes, {outcome_at()} AS ts FROM download_outcomes "
        f"WHERE {outcome_at()} >= now() - make_interval(days => %s)"
    )
    params: list[Any] = [period_days]
    if protocol:
        sql += " AND protocol=%s"
        params.append(protocol)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(MAX_ROWS)
    return db.fetchall(sql, params)


def service_protocol(service: dict[str, Any]) -> str | None:
    if service["kind"] in USENET_KINDS:
        return "usenet"
    if service["kind"] in INDEXER_KINDS:
        return None  # nzb indexers -> usenet, trackers -> torrent; filter by source_id instead
    return None


# ------------------------------------------------------------------ cost normalisation
def monthly_cost(service: dict[str, Any], gb_per_month: float | None = None) -> dict[str, Any]:
    """Normalise the user-entered amount to cents per month (``None`` + reason when not derivable)."""
    billing, amount = service["billing"], int(service["amount_cents"] or 0)
    if service.get("price_missing"):
        return {
            "monthly_cents": None,
            "method": "prijs ontbreekt",
            "reason": "Automatisch toegevoegd; vul de prijs in om kosten te berekenen",
        }
    if billing == "free":
        return {"monthly_cents": 0, "method": "gratis"}
    if billing == "monthly":
        return {"monthly_cents": amount, "method": "maandbedrag"}
    if billing == "yearly":
        return {"monthly_cents": round(amount / 12), "method": "jaarbedrag ÷ 12"}
    size = service.get("block_size_gb")
    if not size:
        return {
            "monthly_cents": None,
            "method": "blockaccount",
            "reason": "Blokgrootte ontbreekt; vul de grootte in GB in om per maand te normaliseren",
        }
    per_gb = amount / float(size)
    if gb_per_month is None:
        return {
            "monthly_cents": None,
            "per_gb_cents": round(per_gb, 2),
            "method": "blockaccount",
            "reason": "Nog geen waargenomen verbruik via dit blockaccount; kosten per maand onbekend",
        }
    return {
        "monthly_cents": round(per_gb * gb_per_month),
        "per_gb_cents": round(per_gb, 2),
        "method": "blockprijs per GB × waargenomen GB per maand",
    }


def get_service(service_id: int, db: Database | None = None) -> dict[str, Any] | None:
    return (db or get_db()).fetchone(
        "SELECT s.*, src.label AS source_label, src.kind AS source_kind FROM services s "
        "LEFT JOIN sources src ON src.id = s.source_id WHERE s.id=%s",
        (service_id,),
    )


def _observed_gb_per_month(
    service: dict[str, Any], period_days: int, rows: list[dict[str, Any]]
) -> float | None:
    keys = provider_keys(service.get("source_id"))
    if not keys:
        return None
    total = sum(provider_bytes_of(r, keys) for r in rows if r["protocol"] == "usenet")
    if total <= 0:
        return None
    return total / GB / period_days * 30


def monthly_total(db: Database | None = None) -> dict[str, Any]:
    """Sum of normalised monthly costs of active services (block accounts via observed usage)."""
    db = db or get_db()
    services = db.fetchall("SELECT * FROM services WHERE active ORDER BY id")
    rows = outcomes_in_period(30, "usenet", db) if any(s["billing"] == "block" for s in services) else []
    total, unknown, by_kind = 0, [], {}
    for s in services:
        m = monthly_cost(s, _observed_gb_per_month(s, 30, rows) if s["billing"] == "block" else None)
        if m["monthly_cents"] is None:
            unknown.append({"id": s["id"], "label": s["label"], "reason": m.get("reason")})
            continue
        total += m["monthly_cents"]
        by_kind[s["kind"]] = by_kind.get(s["kind"], 0) + m["monthly_cents"]
    currencies = sorted({s["currency"] for s in services})
    return {
        "monthly_cents": total,
        "currency": currencies[0] if len(currencies) == 1 else "EUR",
        "mixed_currencies": len(currencies) > 1,
        "services": len(services),
        "unknown": unknown,
        "price_missing": sum(1 for s in services if s.get("price_missing")),
        "by_kind": by_kind,
    }


# ------------------------------------------------------------------ external evidence (Slice B, lazy)
def usenet_contribution(source_id: str | None, period_days: int) -> dict[str, Any] | None:
    """Slice B's passive article-level contribution metrics, if that module is present.

    Returns None when unavailable; otherwise the provider's entry (shape passed through) with an
    ``unique_downloads`` int when Slice B could establish it."""
    if not source_id:
        return None
    try:
        from mso.features.usenet.metrics import provider_contribution  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        data = provider_contribution(period_days)
    except Exception as exc:
        LOGGER.info("provider_contribution niet beschikbaar: %s", exc)
        return {"error": "Usenet-bijdragemeting gaf een fout"}
    keys = provider_keys(source_id)
    entries: list[Any]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        entries = data["items"]
    elif isinstance(data, dict):
        for k, v in data.items():
            if k in keys and isinstance(v, dict):
                return dict(v)
        entries = []
    elif isinstance(data, list):
        entries = data
    else:
        entries = []
    for e in entries:
        if isinstance(e, dict) and (
            e.get("provider_id") in keys or e.get("source_id") in keys or e.get("id") in keys
        ):
            return dict(e)
    return None


def _int_or_none(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ disable simulation
def simulate_disable(service: dict[str, Any], period_days: int, db: Database | None = None) -> dict[str, Any]:
    """Retrospective "what if this service had been off": which observed successful downloads had no
    observed alternative (upper bound of impact), which had one, and how much of the data carries evidence."""
    db = db or get_db()
    sid = service.get("source_id")
    base: dict[str, Any] = {
        "service_id": service["id"],
        "label": service["label"],
        "period_days": period_days,
        "disclaimers": list(DISCLAIMERS),
        "never_auto_cancel": True,
    }
    if not sid:
        return base | {
            "available": False,
            "method": "none",
            "reason": "Deze dienst is niet gekoppeld aan een bron; koppel een bron om waarnemingen "
            "te kunnen gebruiken.",
        }
    if service["kind"] in USENET_KINDS:
        return base | _simulate_usenet(service, sid, period_days, db)
    return base | _simulate_source(service, sid, period_days, db)


def _range(low: int, high: int, total: int) -> dict[str, Any]:
    return {"low": low, "high": high, "low_pct": ratio(low, total), "high_pct": ratio(high, total)}


def _simulate_usenet(service: dict[str, Any], sid: str, period_days: int, db: Database) -> dict[str, Any]:
    keys = provider_keys(sid)
    rows = [r for r in outcomes_in_period(period_days, "usenet", db) if r["status"] == "success"]
    total = len(rows)
    with_evidence = [r for r in rows if r.get("provider_bytes")]
    only_this, shared, not_involved = [], [], []
    for r in with_evidence:
        mine, other = provider_bytes_of(r, keys), _other_provider_bytes(r, keys)
        if mine <= 0:
            not_involved.append(r)
        elif other <= 0:
            only_this.append(r)
        else:
            shared.append(r)
    involved = len(only_this) + len(shared)
    contrib = usenet_contribution(sid, period_days)
    proven_unique = _int_or_none((contrib or {}).get("unique_downloads"))
    low = min(proven_unique, involved) if proven_unique is not None else 0
    method = "usenet_artikelbewijs" if proven_unique is not None else "usenet_providerbytes"
    cov = ratio(len(with_evidence), total)
    cap = None if proven_unique is not None else "laag"
    buckets = [
        {
            "key": "niet_geraakt",
            "count": len(not_involved),
            "label": "Niet via deze dienst binnengekomen",
            "explanation": "Deze downloads gebruikten geen bytes van deze provider; uitschakelen raakt ze "
            "niet.",
        },
        {
            "key": "alleen_deze",
            "count": len(only_this),
            "label": "Volledig via deze dienst",
            "explanation": "Alle bytes kwamen van deze provider. Of een andere provider de artikelen ook "
            "had, is "
            "niet gemeten (die werd niet gevraagd).",
        },
        {
            "key": "gedeeld",
            "count": len(shared),
            "label": "Deels via deze dienst, deels via andere",
            "explanation": "Deze provider vulde een deel aan. Of de andere provider(s) dat deel ook hadden, "
            "is "
            "onbekend tenzij artikelmetingen het aantonen.",
        },
        {
            "key": "geen_bewijs",
            "count": total - len(with_evidence),
            "label": "Geen providergegevens",
            "explanation": "Voor deze downloads is niet vastgelegd welke provider welke bytes leverde.",
        },
    ]
    out: dict[str, Any] = {
        "available": total > 0,
        "method": method,
        "protocol": "usenet",
        "observed_successes": total,
        "involved": involved,
        "buckets": buckets,
        "affected_range": _range(low, involved, total),
        "coverage": {"with_evidence": len(with_evidence), "total": total, "ratio": cov},
        "confidence": confidence_label(cov, total, _min_sample(), cap),
        "slice_b_evidence": contrib,
        "examples": [{"title": r["title"], "bucket": "alleen_deze", "ts": r["ts"]} for r in only_this[:10]],
    }
    if total == 0:
        out["reason"] = "Geen geslaagde Usenet-downloads waargenomen in deze periode."
    out_disc = [USENET_DISCLAIMER]
    if proven_unique is None:
        out_disc.append(
            "Artikelniveau-metingen (passieve bijdrage per provider) zijn niet beschikbaar; de "
            "ondergrens is daarom 0 en de bovengrens alle downloads waarbij deze provider bytes "
            "leverde."
        )
    out["method_notes"] = out_disc
    return out


def _simulate_source(service: dict[str, Any], sid: str, period_days: int, db: Database) -> dict[str, Any]:
    rows = [r for r in outcomes_in_period(period_days, None, db) if r["status"] == "success"]
    mine = [r for r in rows if r["source_id"] == sid]
    keys = [r["release_key"] for r in mine if r["release_key"]]
    others_by_key: dict[str, set[str]] = {}
    if keys:
        for o in db.fetchall(
            "SELECT release_key, source_id FROM download_outcomes WHERE release_key = ANY(%s) "
            "AND source_id IS NOT NULL AND source_id <> %s",
            (keys, sid),
        ):
            others_by_key.setdefault(o["release_key"], set()).add(o["source_id"])
    alt, no_alt, unknown = [], [], []
    for r in mine:
        meta = r.get("meta") or {}
        also = meta.get("also_from")
        other_sources = set(others_by_key.get(r["release_key"], set()))
        if isinstance(also, list):
            other_sources |= {s for s in also if s and s != sid}
        if other_sources:
            alt.append((r, sorted(other_sources)))
        elif isinstance(also, list):
            no_alt.append(r)  # the search recorded every source that returned this release: only this one
        else:
            unknown.append(r)
    total = len(mine)
    with_ev = len(alt) + len(no_alt)
    cov = ratio(with_ev, total)
    buckets = [
        {
            "key": "alternatief",
            "count": len(alt),
            "label": "Ook bij een andere bron gezien",
            "explanation": "Dezelfde release werd ook door een andere bron gevonden; die had waarschijnlijk "
            "kunnen dienen.",
        },
        {
            "key": "geen_alternatief",
            "count": len(no_alt),
            "label": "Alleen via deze bron gezien",
            "explanation": "Bij het zoeken leverde geen andere bron deze release. Een andere release van "
            "hetzelfde "
            "item had mogelijk wel gekund; dat is niet gemeten.",
        },
        {
            "key": "onbekend",
            "count": len(unknown),
            "label": "Onbekend",
            "explanation": "Niet vastgelegd welke bronnen deze release nog meer hadden.",
        },
    ]
    out: dict[str, Any] = {
        "available": total > 0,
        "method": "release_overlap",
        "protocol": None,
        "observed_successes": total,
        "involved": total,
        "buckets": buckets,
        "affected_range": _range(len(no_alt), len(no_alt) + len(unknown), total),
        "coverage": {"with_evidence": with_ev, "total": total, "ratio": cov},
        "confidence": confidence_label(cov, total, _min_sample()),
        "examples": (
            [{"title": r["title"], "bucket": "geen_alternatief", "ts": r["ts"]} for r in no_alt[:10]]
            + [
                {"title": r["title"], "bucket": "alternatief", "ts": r["ts"], "also_from": a}
                for r, a in alt[:5]
            ]
        ),
        "method_notes": [
            "Alleen geslaagde downloads die via deze bron zijn verstuurd tellen mee. "
            "'Alleen via deze bron' is de ondergrens; samen met 'onbekend' de bovengrens."
        ],
    }
    if total == 0:
        out["reason"] = "Geen geslaagde downloads via deze bron waargenomen in deze periode."
    return out


# ------------------------------------------------------------------ per-service analysis
def analyse(service: dict[str, Any], period_days: int, db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    sid = service.get("source_id")
    rows = outcomes_in_period(period_days, None, db)
    is_usenet = service["kind"] in USENET_KINDS
    keys = provider_keys(sid)
    if not sid:
        relevant: list[dict[str, Any]] = []
    elif is_usenet:
        relevant = [r for r in rows if r["protocol"] == "usenet" and provider_bytes_of(r, keys) > 0]
    else:
        relevant = [r for r in rows if r["source_id"] == sid]
    successes = [r for r in relevant if r["status"] == "success"]
    failures = [
        r
        for r in rows
        if r["status"] == "failed"
        and (
            (is_usenet and keys and set((r.get("provider_bytes") or {}).keys()) & keys)
            or (not is_usenet and sid and r["source_id"] == sid)
        )
    ]
    used_bytes = (
        sum(provider_bytes_of(r, keys) for r in relevant)
        if is_usenet
        else sum(int(r["downloaded_bytes"] or r["size_bytes"] or 0) for r in successes)
    )
    gb_pm = (used_bytes / GB / period_days * 30) if used_bytes else None
    cost = monthly_cost(service, gb_pm if service["billing"] == "block" else None)
    if cost["monthly_cents"] is not None and successes:
        per_success = cost["monthly_cents"] * (period_days / 30) / len(successes)
        cost["per_success_cents"] = round(per_success, 1)
    if cost["monthly_cents"] is not None and used_bytes:
        cost.setdefault(
            "per_gb_cents", round(cost["monthly_cents"] * (period_days / 30) / (used_bytes / GB), 2)
        )

    reasons: dict[str, int] = {}
    for r in failures:
        k = r["failure_reason"] or "unknown"
        reasons[k] = reasons.get(k, 0) + 1
    failures_by_reason = sorted(
        ({"reason": k, "label": FAILURE_REASONS.get(k, k), "count": v} for k, v in reasons.items()),
        key=lambda x: -x["count"],
    )

    sim = simulate_disable(service, period_days, db)
    if not sim.get("available"):
        unique = {
            "value": None,
            "method": sim.get("method"),
            "confidence": "onbekend",
            "note": sim.get("reason", "Geen gegevens"),
        }
    elif sim["method"] == "usenet_providerbytes":
        unique = {
            "value": None,
            "method": sim["method"],
            "confidence": "onbekend",
            "range": sim["affected_range"],
            "note": "Unieke bijdrage is niet aantoonbaar zonder artikelmetingen; de marge toont het "
            "maximale aantal downloads waarbij deze provider bytes leverde. Totaal gedownloade "
            "bytes zeggen niets over unieke retentie.",
        }
    else:
        unique = {
            "value": sim["affected_range"]["low"],
            "method": sim["method"],
            "confidence": sim["confidence"],
            "range": sim["affected_range"],
            "coverage": sim["coverage"]["ratio"],
            "note": "Aantal geslaagde downloads waarvoor alleen deze dienst bewijs van beschikbaarheid gaf.",
        }

    ev_b = sim.get("slice_b_evidence") if isinstance(sim.get("slice_b_evidence"), dict) else None
    if is_usenet and ev_b and "error" not in ev_b:
        # article-level evidence from the Usenet engine (explicit/passive tests on requested articles)
        unique["articles"] = {
            k: ev_b.get(k)
            for k in (
                "unique_available",
                "compared_articles",
                "articles_checked",
                "available",
                "coverage",
                "confidence",
                "method",
            )
            if k in ev_b
        }
        unique["articles_note"] = (
            "Artikelen die bij vergelijkende tests alleen bij deze provider beschikbaar waren. Dit zegt iets "
            "over unieke beschikbaarheid van geteste artikelen, niet over het aantal downloads."
        )

    latency = _latency(sid, period_days, db)
    recovered = _recovered(sid, period_days, db)
    block = None
    if service["billing"] == "block" or service.get("block_size_gb"):
        size, used = service.get("block_size_gb"), service.get("block_used_gb")
        remaining = (float(size) - float(used or 0)) if size else None
        months_left = (remaining / gb_pm) if (remaining is not None and gb_pm) else None
        block = {
            "size_gb": size,
            "used_gb": used,
            "remaining_gb": remaining,
            "observed_gb_per_month": round(gb_pm, 2) if gb_pm else None,
            "est_months_left": round(months_left, 1) if months_left else None,
            "note": "Schatting op basis van het waargenomen verbruik in deze periode; kan sterk wisselen.",
        }
    renewal = None
    if service.get("renewal_date"):
        rd: date = service["renewal_date"]
        renewal = {"date": rd.isoformat(), "days_left": (rd - _now().date()).days}
    return {
        "service": serialise_service(service),
        "period_days": period_days,
        "available": bool(sid),
        "reason": None if sid else "Niet gekoppeld aan een bron; alleen kosten bekend.",
        "cost": cost,
        "usage": {
            "bytes": used_bytes,
            "gb": round(used_bytes / GB, 2),
            "successful_downloads": len(successes),
            "failed_downloads": len(failures),
            "bytes_method": "providerbytes per download" if is_usenet else "grootte van geslaagde downloads",
        },
        "unique": unique,
        "latency": latency,
        "failures_by_reason": failures_by_reason,
        "recovered": recovered,
        "block": block,
        "renewal": renewal,
        "simulation_summary": {
            k: sim.get(k) for k in ("available", "affected_range", "coverage", "confidence")
        },
        "disclaimers": list(DISCLAIMERS),
    }


def _latency(sid: str | None, period_days: int, db: Database) -> dict[str, Any]:
    if not sid:
        return {"available": False, "n": 0}
    # percentiles need the individual measurements, which are kept for the raw retention window only (older ones
    # are rolled up per hour): a longer period is measured over that window and says so
    from mso import retention

    window = min(int(period_days), int(retention.raw_retention(db)["effective_days"]))
    note = (
        f"Gemeten over de laatste {window} dagen: oudere losse metingen zijn alleen per uur samengevat bewaard."
        if window < period_days
        else None
    )
    row = db.fetchone(
        "SELECT count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS p50, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY value) AS p95, array_agg(DISTINCT metric) AS metrics "
        "FROM observations WHERE subject=%s AND metric ILIKE '%%latency%%' AND value IS NOT NULL "
        "AND ts >= now() - make_interval(days => %s)",
        (sid, window),
    )
    n = int(row["n"] or 0) if row else 0
    if not n:
        return {"available": False, "n": 0, "reason": "Geen latentiemetingen in deze periode", "window_days": window,
                "note": note}
    return {
        "available": True,
        "n": n,
        "p50_ms": round(row["p50"], 1),
        "p95_ms": round(row["p95"], 1),
        "metrics": [m for m in (row["metrics"] or []) if m],
        "window_days": window,
        "note": note,
    }


def _recovered(sid: str | None, period_days: int, db: Database) -> dict[str, Any]:
    if not sid:
        return {"count": 0, "available": False}
    recs = (
        db.scalar(
            "SELECT count(*) FROM recommendations WHERE area='recovery' AND status='resolved' "
            "AND (subject=%s OR evidence->>'source_id'=%s) AND updated_at >= now() - make_interval(days => "
            "%s)",
            (sid, sid, period_days),
        )
        or 0
    )
    outs = (
        db.scalar(
            "SELECT count(*) FROM download_outcomes WHERE status='success' AND source_id=%s "
            "AND (meta ? 'recovery' OR meta ? 'recovered_for') "
            f"AND {outcome_at()} >= now() - make_interval(days => %s)",
            (sid, period_days),
        )
        or 0
    )
    return {
        "count": int(recs) + int(outs),
        "available": True,
        "method": "opgeloste herstelmeldingen + geslaagde downloads gemarkeerd als herstel",
    }


def serialise_service(s: dict[str, Any]) -> dict[str, Any]:
    out = {
        k: s.get(k)
        for k in (
            "id",
            "source_id",
            "kind",
            "label",
            "billing",
            "amount_cents",
            "currency",
            "block_size_gb",
            "block_used_gb",
            "renewal_date",
            "notes",
            "active",
            "backbone",
            "origin",
            "price_missing",
            "created_at",
            "updated_at",
            "source_label",
        )
    }
    if isinstance(out.get("renewal_date"), date):
        out["renewal_date"] = out["renewal_date"].isoformat()
    return out


# ------------------------------------------------------------------ per-service list metrics
def _contributions(period_days: int) -> dict[str, dict[str, Any]]:
    try:
        from mso.features.usenet.metrics import provider_contribution  # type: ignore[import-not-found]
    except ImportError:
        return {}
    try:
        data = provider_contribution(period_days)
    except Exception as exc:
        LOGGER.info("provider_contribution niet beschikbaar: %s", exc)
        return {}
    items = data.get("items") if isinstance(data, dict) else data
    out: dict[str, dict[str, Any]] = {}
    for e in items or []:
        if isinstance(e, dict):
            for k in ("source_id", "provider_id", "slug", "id"):
                if e.get(k):
                    out[str(e[k])] = e
    return out


def unique_share(entry: dict[str, Any] | None) -> dict[str, Any]:
    """'Deze provider leverde X % uniek' — only from articles checked on ≥2 providers (Usenet engine)."""
    if not entry:
        return {"available": False, "reason": "Nog geen artikelmetingen voor deze provider"}
    compared = _int_or_none(entry.get("compared_articles")) or 0
    unique = _int_or_none(entry.get("unique_available"))
    if not compared or unique is None:
        return {"available": False, "reason": "Nog geen artikelen bij meerdere providers vergeleken",
                "articles_checked": _int_or_none(entry.get("articles_checked"))}
    return {
        "available": True,
        "pct": round(unique / compared, 4),
        "unique_articles": unique,
        "compared_articles": compared,
        "confidence": entry.get("confidence"),
        "method": entry.get("method"),
        "note": "Aandeel van de vergeleken artikelen dat alleen bij deze provider beschikbaar was. Geen garantie "
        "voor andere releases; zegt niets over besparing.",
    }


def service_metrics(
    services: list[dict[str, Any]], period_days: int, db: Database | None = None
) -> dict[int, dict[str, Any]]:
    """Per service over the period: successful downloads, successful GB, entered cost per successful GB and per
    successful download (cost ÷ observed successes — a retrospective ratio, no savings promise) and, for Usenet
    providers, the measured unique share. Byte attribution for Usenet uses provider bytes (bandwidth) and reports
    the coverage of downloads that carry that evidence."""
    db = db or get_db()
    rows = outcomes_in_period(period_days, None, db) if services else []
    usenet_ok = [r for r in rows if r["protocol"] == "usenet" and r["status"] == "success"]
    usenet_cov = ratio(len([r for r in usenet_ok if r.get("provider_bytes")]), len(usenet_ok))
    contrib = _contributions(period_days) if any(s["kind"] in USENET_KINDS for s in services) else {}
    out: dict[int, dict[str, Any]] = {}
    for s in services:
        sid = s.get("source_id")
        m: dict[str, Any] = {"period_days": period_days, "linked": bool(sid)}
        if not sid:
            out[s["id"]] = m | {"available": False, "reason": "Niet gekoppeld aan een bron"}
            continue
        if s["kind"] in USENET_KINDS:
            keys = provider_keys(sid)
            mine = [r for r in usenet_ok if provider_bytes_of(r, keys) > 0]
            ok_bytes = sum(provider_bytes_of(r, keys) for r in mine)
            m["bytes_method"] = "providerbytes van geslaagde downloads (bandbreedte)"
            m["coverage"] = usenet_cov
            entry = next((contrib[k] for k in keys if k in contrib), None)
            m["unique"] = unique_share(entry)
        else:
            mine = [r for r in rows if r["status"] == "success" and r["source_id"] == sid]
            ok_bytes = sum(int(r["downloaded_bytes"] or r["size_bytes"] or 0) for r in mine)
            m["bytes_method"] = "grootte van geslaagde downloads via deze bron"
        gb = ok_bytes / GB
        gb_pm = gb / period_days * 30 if gb else None
        cost = monthly_cost(s, gb_pm if s["billing"] == "block" else None)
        period_cost = cost["monthly_cents"] * period_days / 30 if cost["monthly_cents"] is not None else None
        m |= {
            "available": True,
            "successful_downloads": len(mine),
            "successful_gb": round(gb, 2),
            "period_cost_cents": round(period_cost) if period_cost is not None else None,
            "per_success_cents": round(period_cost / len(mine), 1) if period_cost is not None and mine else None,
            "per_gb_cents": round(period_cost / gb, 2) if period_cost is not None and gb > 0 else None,
        }
        if cost["monthly_cents"] is None:
            m["cost_reason"] = cost.get("reason")
        elif not mine:
            m["cost_reason"] = "Nog geen geslaagde downloads via deze dienst in deze periode"
        out[s["id"]] = m
    return out


# ------------------------------------------------------------------ scenarios
def scenario(
    label: str,
    services: list[dict[str, Any]],
    all_usenet: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    period_days: int,
) -> dict[str, Any]:
    excluded_keys: set[str] = set()
    for s in all_usenet:
        if s not in services:
            excluded_keys |= provider_keys(s.get("source_id"))
    monthly, unknown_cost = 0, []
    for s in services:
        m = monthly_cost(s, _observed_gb_per_month(s, period_days, rows) if s["billing"] == "block" else None)
        if m["monthly_cents"] is None:
            unknown_cost.append(s["label"])
        else:
            monthly += m["monthly_cents"]
    backbones = [{"label": s["label"], "backbone": s.get("backbone") or None} for s in services]
    known = sorted({(b["backbone"] or "").strip().lower() for b in backbones if b["backbone"]})
    unknown_bb = [b["label"] for b in backbones if not b["backbone"]]
    successes = [r for r in rows if r["status"] == "success" and r.get("provider_bytes")]
    within, needs_excluded = 0, 0
    for r in successes:
        pb_keys = {k for k, v in (r["provider_bytes"] or {}).items() if int(v or 0) > 0}
        if pb_keys & excluded_keys:
            needs_excluded += 1
        elif pb_keys:
            within += 1
    candidates = [s["label"] for s in services if not s["active"]]
    notes = []
    if unknown_bb:
        notes.append(
            "Backbone onbekend voor: " + ", ".join(unknown_bb) + ". Vul de backbone handmatig in; de "
            "app verzint geen backbone-diversiteit."
        )
    if len(known) == 1 and len(services) > 1 and not unknown_bb:
        notes.append(
            "Alle diensten in dit scenario zitten op dezelfde (ingevulde) backbone; extra retentie of "
            "aanvulling is dan beperkt te verwachten."
        )
    if candidates:
        notes.append(
            "Kandidaat (niet actief) zonder waarnemingen: " + ", ".join(candidates) + ". Dekking door "
            "deze dienst is onbekend."
        )
    if unknown_cost:
        notes.append("Kosten per maand onbekend voor: " + ", ".join(unknown_cost) + ".")
    total = len(successes)
    return {
        "label": label,
        "service_ids": [s["id"] for s in services],
        "services": [s["label"] for s in services],
        "monthly_cents": monthly,
        "monthly_complete": not unknown_cost,
        "backbones": backbones,
        "distinct_known_backbones": len(known),
        "unknown_backbones": len(unknown_bb),
        "observed": {
            "successes_with_evidence": total,
            "fully_within_set": within,
            "needed_excluded_provider": needs_excluded,
            "needed_excluded_pct": ratio(needs_excluded, total),
            "unobserved_candidates": len(candidates),
        },
        "notes": notes,
    }


def scenarios(
    period_days: int, custom: list[dict[str, Any]] | None = None, db: Database | None = None
) -> dict[str, Any]:
    db = db or get_db()
    all_services = db.fetchall(
        "SELECT * FROM services WHERE kind = ANY(%s) ORDER BY id", (list(USENET_KINDS),)
    )
    active = [s for s in all_services if s["active"]]
    rows = outcomes_in_period(period_days, "usenet", db)
    by_id = {s["id"]: s for s in all_services}
    out = []
    if custom:
        for sc in custom:
            chosen = [by_id[i] for i in sc.get("service_ids", []) if i in by_id]
            out.append(scenario(sc.get("label") or "Scenario", chosen, active, rows, period_days))
    else:
        if active:
            out.append(scenario("Huidige combinatie", active, active, rows, period_days))
        if len(active) > 1:
            for s in active:
                rest = [x for x in active if x["id"] != s["id"]]
                out.append(scenario(f"Zonder {s['label']}", rest, active, rows, period_days))
        for cand in (s for s in all_services if not s["active"]):
            for s in active:
                rest = [x for x in active if x["id"] != s["id"]] + [cand]
                out.append(
                    scenario(f"{cand['label']} in plaats van {s['label']}", rest, active, rows, period_days)
                )
    return {
        "period_days": period_days,
        "items": out,
        "disclaimers": list(DISCLAIMERS) + [USENET_DISCLAIMER],
        "available": bool(all_services),
        "reason": None if all_services else "Voeg eerst Usenet-providers of blockaccounts met prijs toe.",
    }


# ------------------------------------------------------------------ renewals
def upcoming_renewals(days: int, db: Database | None = None) -> list[dict[str, Any]]:
    db = db or get_db()
    rows = db.fetchall(
        "SELECT * FROM services WHERE active AND renewal_date IS NOT NULL "
        "AND renewal_date <= current_date + %s ORDER BY renewal_date",
        (days,),
    )
    today = _now().date()
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "date": r["renewal_date"].isoformat(),
            "days_left": (r["renewal_date"] - today).days,
            "billing": r["billing"],
            "amount_cents": r["amount_cents"],
            "currency": r["currency"],
        }
        for r in rows
    ]


def period_bounds(period_days: int) -> dict[str, str]:
    end = _now()
    return {"from": (end - timedelta(days=period_days)).isoformat(), "to": end.isoformat()}
