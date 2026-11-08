"""Provider contribution, computed only from observed requested articles.

Two evidence sources, reported separately and with coverage/confidence:

- ``explicit_test``: STAT results on real NZB articles, same articles on every tested provider → unbiased
  availability and *unique* availability (available on exactly one of the providers compared).
- ``passive``: SABnzbd per-server bytes and article counters. These depend on server priority (a fallback
  server only sees what the primary missed), so they show *fill* of misses, not overall retention.

Bytes downloaded are reported as-is; they are never turned into a "unique retention" claim.
Used by Slice E (costs): ``provider_contribution(period_days)``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from mso.db import get_db
from mso.features.usenet import providers


def _confidence(checked: int, nzb_count: int, passive_tried: int) -> str:
    if checked >= 1000 and nzb_count >= 5:
        return "high"
    if checked >= 200 and nzb_count >= 2:
        return "medium"
    if checked > 0 or passive_tried > 0:
        return "low"
    return "none"


def provider_contribution(period_days: int) -> list[dict[str, Any]]:
    """One dict per configured provider:
    ``{source_id, label, articles_checked, available, unique_available, bytes_downloaded, failures_by_reason,
    coverage, confidence, method}`` plus ``slug``, ``kind``, ``passive``, ``nzbs_tested`` and
    ``compared_articles``.
    """
    period_days = max(1, min(int(period_days), 3650))
    db = get_db()
    since = f"{period_days} days"
    rows = providers.list_rows()
    explicit = {
        r["provider"]: r
        for r in db.fetchall(
            "SELECT r.provider, count(*) AS checked, count(*) FILTER (WHERE r.available) AS available, "
            "count(DISTINCT t.nzb_id) AS nzbs FROM usenet_article_results r JOIN usenet_article_tests t ON "
            "t.id=r.test_id "
            "WHERE r.checked_at > now() - %s::interval AND r.available IS NOT NULL GROUP BY r.provider",
            (since,),
        )
    }
    groups = db.fetchone(
        "WITH g AS (SELECT test_id, message_id, count(*) AS n, count(*) FILTER (WHERE available) AS navail "
        "FROM usenet_article_results WHERE checked_at > now() - %s::interval AND available IS NOT NULL "
        "GROUP BY test_id, message_id) "
        "SELECT count(*) AS total, count(*) FILTER (WHERE n >= 2) AS compared FROM g",
        (since,),
    )
    unique = {
        r["provider"]: int(r["n"])
        for r in db.fetchall(
            "WITH g AS (SELECT test_id, message_id, count(*) AS n, count(*) FILTER (WHERE available) AS "
            "navail, "
            "max(provider) FILTER (WHERE available) AS only_provider FROM usenet_article_results "
            "WHERE checked_at > now() - %s::interval AND available IS NOT NULL GROUP BY test_id, message_id) "
            "SELECT only_provider AS provider, count(*) AS n FROM g WHERE n >= 2 AND navail = 1 GROUP BY 1",
            (since,),
        )
    }
    tested_on = {
        r["provider"]: int(r["n"])
        for r in db.fetchall(
            "WITH g AS (SELECT test_id, message_id, array_agg(provider) AS ps, count(*) AS n "
            "FROM usenet_article_results WHERE checked_at > now() - %s::interval AND available IS NOT NULL "
            "GROUP BY test_id, message_id) SELECT p AS provider, count(*) AS n FROM g, unnest(ps) p "
            "WHERE n >= 2 GROUP BY 1",
            (since,),
        )
    }
    day0 = date.today() - timedelta(days=period_days)
    passive = {
        r["server"]: r
        for r in db.fetchall(
            "SELECT server, sum(bytes) AS bytes, sum(articles_tried) AS tried, sum(articles_success) AS "
            "success "
            "FROM usenet_server_stats WHERE day > %s GROUP BY server",
            (day0,),
        )
    }
    checks: dict[str, dict[str, int]] = {}
    # raw checks plus, past the raw retention window, the hourly rollups (values are 0/1, so the failed checks of an
    # hour are n_values - sum): a period of up to 365 days keeps counting every check
    for r in db.fetchall(
        "SELECT subject, status, sum(n)::bigint AS n FROM ("
        "SELECT subject, labels->>'status' AS status, count(*) AS n FROM observations "
        "WHERE metric='nntp.check_ok' AND value=0 AND ts > now() - %s::interval GROUP BY 1, 2 UNION ALL "
        "SELECT subject, labels->>'status', sum(n_values - COALESCE(sum, 0)) FROM observations_hourly "
        "WHERE metric='nntp.check_ok' AND hour > now() - %s::interval GROUP BY 1, 2"
        ") c GROUP BY 1, 2 HAVING sum(n) > 0",
        (since, since),
    ):
        checks.setdefault(r["subject"], {})[r["status"] or "unknown"] = int(r["n"])
    total_articles = int(groups["total"] or 0)
    out: list[dict[str, Any]] = []
    for p in rows:
        sid = providers.source_id(p["slug"], p["kind"])
        e = explicit.get(p["slug"])
        checked = int(e["checked"]) if e else 0
        available = int(e["available"]) if e else 0
        nzb_count = int(e["nzbs"]) if e else 0
        ps = passive.get(p["sab_server"] or "") if p["sab_server"] else None
        tried = int(ps["tried"] or 0) if ps and ps["tried"] is not None else 0
        success = int(ps["success"] or 0) if ps and ps["success"] is not None else 0
        failures = dict(checks.get(sid, {}))
        if checked - available:
            failures["article_missing"] = checked - available
        if tried - success > 0:
            failures["article_missing_passive"] = tried - success
        methods = [m for m, has in (("explicit_test", checked > 0), ("passive", ps is not None)) if has]
        out.append(
            {
                "source_id": sid,
                "label": p["label"],
                "slug": p["slug"],
                "kind": p["kind"],
                "articles_checked": checked,
                "available": available,
                # unique only where the same article was checked on ≥2 providers; None = not comparable
                "unique_available": unique.get(p["slug"], 0) if tested_on.get(p["slug"]) else None,
                "compared_articles": tested_on.get(p["slug"], 0),
                "bytes_downloaded": int(ps["bytes"] or 0) if ps else None,
                "failures_by_reason": failures,
                "coverage": round(checked / total_articles, 4) if total_articles else 0.0,
                "confidence": _confidence(checked, nzb_count, tried),
                "method": "+".join(methods) if methods else "none",
                "nzbs_tested": nzb_count,
                "passive": {
                    "server": p["sab_server"],
                    "articles_tried": tried if ps else None,
                    "articles_success": success if ps else None,
                    "success_ratio": round(success / tried, 4) if tried else None,
                },
                "availability": _availability(checked, available, tried, success),
                "has_credentials": providers.has_credentials(p["slug"]),
            }
        )
    return out


PASSIVE_LABEL = "passief uit SABnzbd"


def _availability(checked: int, available: int, tried: int, success: int) -> dict[str, Any]:
    """The one availability figure shown per row: MSO's own STAT sample when there is one, otherwise SABnzbd's own
    per-server article counters (passive; depends on server priority)."""
    if checked:
        return {"ratio": round(available / checked, 4), "source": "stat", "label": "STAT-steekproef",
                "articles": checked}
    if tried:
        return {"ratio": round(success / tried, 4), "source": "passive", "label": PASSIVE_LABEL, "articles": tried}
    return {"ratio": None, "source": None, "label": None, "articles": 0}


def _sab_servers_without_provider(period_days: int, linked: set[str]) -> list[dict[str, Any]]:
    """SABnzbd servers no MSO provider is linked to, from SABnzbd's own per-server statistics (passive)."""
    db = get_db()
    day0 = date.today() - timedelta(days=period_days)
    names = {
        r["server"]: r["displayname"] for r in db.fetchall("SELECT server, displayname FROM usenet_sab_servers")
    }
    stats = {
        r["server"]: r
        for r in db.fetchall(
            "SELECT server, sum(bytes) AS bytes, sum(articles_tried) AS tried, sum(articles_success) AS success "
            "FROM usenet_server_stats WHERE day > %s GROUP BY server",
            (day0,),
        )
    }
    out = []
    for server in sorted((set(names) | set(stats)) - linked):
        ps = stats.get(server)
        tried = int(ps["tried"] or 0) if ps and ps["tried"] is not None else 0
        success = int(ps["success"] or 0) if ps and ps["success"] is not None else 0
        from mso.features.usenet.serverstatus import short_name

        label = names.get(server) or short_name(server)
        out.append(
            {
                "source_id": f"sabnzbd:{server}",
                "label": label,
                "slug": None,
                "kind": "sab_server",
                "articles_checked": 0,
                "available": 0,
                "unique_available": None,
                "compared_articles": 0,
                "bytes_downloaded": int(ps["bytes"] or 0) if ps else None,
                "failures_by_reason": {"article_missing_passive": tried - success} if tried > success else {},
                "coverage": 0.0,
                "confidence": "low" if tried else "none",
                "method": "passive" if ps else "none",
                "nzbs_tested": 0,
                "passive": {
                    "server": server,
                    "articles_tried": tried if ps else None,
                    "articles_success": success if ps else None,
                    "success_ratio": round(success / tried, 4) if tried else None,
                },
                "availability": _availability(0, 0, tried, success),
                "has_credentials": False,
            }
        )
    return out


def _ensure_passive_stats() -> None:
    """No SABnzbd statistics stored yet (fresh install): read them once now so the table is never empty."""
    db = get_db()
    if db.scalar("SELECT 1 FROM usenet_server_stats LIMIT 1"):
        return
    try:
        from mso.connectors import base as cb

        if not cb.is_configured("sabnzbd"):
            return
        from mso.features.usenet import history

        history.snapshot_server_stats()
    except Exception:  # SABnzbd unreachable: the table shows what there is
        return


def overview(period_days: int) -> dict[str, Any]:
    _ensure_passive_stats()
    items = provider_contribution(period_days)
    linked = {p["sab_server"] for p in providers.list_rows() if p.get("sab_server")}
    items += _sab_servers_without_provider(period_days, linked)
    db = get_db()
    tests = int(
        db.scalar(
            "SELECT count(*) FROM usenet_article_tests WHERE status='done' AND created_at > now() - "
            "%s::interval",
            (f"{period_days} days",),
        )
        or 0
    )
    missing = [i["label"] for i in items if i["slug"] and not i["has_credentials"]]
    passive_only = sum(1 for i in items if i["availability"]["source"] == "passive")
    return {
        "period_days": period_days,
        "items": items,
        "tests": tests,
        "sources": {"stat": sum(1 for i in items if i["availability"]["source"] == "stat"), "passive": passive_only},
        # one gentle hint, never a blocker: without credentials MSO can't run its own STAT sample
        "credentials_hint": {
            "providers": missing,
            "text": "Inloggegevens toevoegen voor betere meting: met inloggegevens controleert MSO zelf of jouw "
            "aangevraagde artikelen bij elke provider aanwezig zijn (STAT-steekproef). Tot dan toont MSO "
            "SABnzbd's eigen tellers (passief).",
        }
        if missing
        else None,
        "notes": [
            "Alleen gemeten op artikelen die jij echt hebt aangevraagd; geen garantie voor andere releases.",
            "Uniek beschikbaar = in dezelfde test bij precies één van de vergeleken providers aanwezig.",
            "Passieve SABnzbd-tellers hangen af van de serverprioriteit: een fallback-server ziet alleen "
            "wat de "
            "primaire miste. Gedownloade bytes zeggen niets over unieke retentie.",
        ],
    }
