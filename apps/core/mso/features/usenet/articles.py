"""Article availability for *actually requested* articles.

A test takes a bounded, evenly spread sample of the real segment message-ids of one NZB (a job MSO sent,
or an NZB the user uploaded) and asks every selected provider ``STAT <message-id>`` over a single
connection. Results are per provider and per article, so availability and *unique* availability
(available on exactly one of the tested providers) are measured on the same articles — never inferred from
downloaded bytes.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from mso import jobs
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.features.usenet import nzbs, providers
from mso.features.usenet.nntp import NNTPError

LOGGER = logging.getLogger(__name__)
MAX_TEST_SECONDS = 300
AUTO_TRIGGER = "auto_send"
# Providers whose account MSO (or SABnzbd) currently reports as unusable are not sampled automatically.
SKIP_AUTO_STATUS = {"auth_failed", "account_expired", "block_exhausted", "access_denied"}
# Conservative per-connection overhead (greeting, AUTHINFO USER/PASS, QUIT) and per-STAT size estimate.
OVERHEAD_COMMANDS = 4
OVERHEAD_BYTES = 1024
BYTES_PER_STAT = 200
BUDGET_TEXT = "STAT-budget voor vandaag of deze maand is op; volgende steekproef zodra er weer budget is"


def _eligible(slugs: list[str] | None) -> list[dict[str, Any]]:
    rows = [r for r in providers.list_rows() if r["enabled"]]
    if slugs:
        unknown = set(slugs) - {r["slug"] for r in rows}
        if unknown:
            raise HTTPException(
                422, detail=f"Onbekende of uitgeschakelde provider(s): {', '.join(sorted(unknown))}"
            )
        rows = [r for r in rows if r["slug"] in slugs]
    rows = [r for r in rows if all(providers.credentials(r["slug"]))]
    return rows


def create_test(
    nzb_id: int,
    *,
    provider_slugs: list[str] | None = None,
    sample_size: int | None = None,
    trigger: str = "manual",
    actor: str = "system",
) -> dict[str, Any]:
    nzb = nzbs.get(nzb_id)
    if not nzb["segments"]:
        raise HTTPException(
            409,
            detail="Van deze NZB zijn geen artikelen bekend (verzonden als link). Upload de "
            "NZB om artikelen te kunnen testen.",
        )
    rows = _eligible(provider_slugs)
    if not rows:
        raise HTTPException(409, detail="Geen ingeschakelde provider met inloggegevens om te testen")
    max_n = int(sr.REGISTRY["usenet.article_sample_size"].max or 1000)
    n = int(sample_size or sr.get("usenet.article_sample_size"))
    n = max(1, min(n, max_n, len(nzb["segments"])))
    db = get_db()
    open_test = db.fetchone(
        "SELECT * FROM usenet_article_tests WHERE nzb_id=%s AND status IN ('queued','running')", (nzb_id,)
    )
    if open_test:
        return public(dict(open_test))
    slugs = [r["slug"] for r in rows]
    test = db.fetchone(
        "INSERT INTO usenet_article_tests(nzb_id,trigger,providers,sample_size,requested_by) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (nzb_id, trigger, slugs, n, actor),
    )
    job = jobs.enqueue(
        "usenet.article_test",
        {"test_id": test["id"]},
        idempotency_key=f"usenet-art:{test['id']}",
        max_attempts=1,
        created_by=actor,
    )
    db.execute("UPDATE usenet_article_tests SET job_id=%s WHERE id=%s", (job["id"], test["id"]))
    audit(
        actor,
        "usenet.article_test",
        f"Artikeltest gestart: {n} artikelen × {len(slugs)} provider(s)",
        target=str(nzb_id),
        detail={"providers": slugs, "trigger": trigger},
    )
    return public(dict(db.fetchone("SELECT * FROM usenet_article_tests WHERE id=%s", (test["id"],))))


def maybe_test_failed(nzo_id: str) -> dict[str, Any] | None:
    known = nzbs.by_nzo(nzo_id)
    if not known or not known["segments"]:
        return None
    try:
        return create_test(known["id"], trigger="failure", actor="system")
    except HTTPException:
        return None


# ------------------------------------------------------------------ STAT budgets (automatic samples)
def budget_limits() -> dict[str, dict[str, int]]:
    return {
        period: {
            "connections": int(sr.get(f"usenet.stat_budget_{period}_connections")),
            "commands": int(sr.get(f"usenet.stat_budget_{period}_commands")),
            "bytes": int(sr.get(f"usenet.stat_budget_{period}_kb")) * 1024,
        }
        for period in ("daily", "monthly")
    }


def usage(slug: str | None = None) -> dict[str, dict[str, dict[str, int]]]:
    """``{slug: {daily: {connections, commands, bytes}, monthly: {...}}}`` (today / this calendar month)."""
    rows = get_db().fetchall(
        "SELECT provider, "
        "coalesce(sum(connections) FILTER (WHERE day = current_date),0) AS d_conn, "
        "coalesce(sum(commands) FILTER (WHERE day = current_date),0) AS d_cmd, "
        "coalesce(sum(bytes) FILTER (WHERE day = current_date),0) AS d_bytes, "
        "coalesce(sum(connections),0) AS m_conn, coalesce(sum(commands),0) AS m_cmd, "
        "coalesce(sum(bytes),0) AS m_bytes FROM usenet_stat_usage "
        "WHERE day >= date_trunc('month', current_date)::date AND (%s::text IS NULL OR provider = %s) "
        "GROUP BY provider",
        (slug, slug),
    )
    return {
        r["provider"]: {
            "daily": {"connections": int(r["d_conn"]), "commands": int(r["d_cmd"]), "bytes": int(r["d_bytes"])},
            "monthly": {"connections": int(r["m_conn"]), "commands": int(r["m_cmd"]), "bytes": int(r["m_bytes"])},
        }
        for r in rows
    }


def record_usage(slug: str, *, connections: int, commands: int, nbytes: int) -> None:
    get_db().execute(
        "INSERT INTO usenet_stat_usage(day,provider,connections,commands,bytes) VALUES (current_date,%s,%s,%s,%s) "
        "ON CONFLICT (day, provider) DO UPDATE SET connections=usenet_stat_usage.connections+EXCLUDED.connections, "
        "commands=usenet_stat_usage.commands+EXCLUDED.commands, bytes=usenet_stat_usage.bytes+EXCLUDED.bytes",
        (slug, int(connections), int(commands), int(nbytes)),
    )


def budget_fit(slug: str) -> int:
    """How many STAT commands one more automatic connection to ``slug`` may still send (0 = budget is op)."""
    limits = budget_limits()
    used = usage(slug).get(slug) or {}
    fit: int | None = None
    for period, lim in limits.items():
        u = used.get(period) or {"connections": 0, "commands": 0, "bytes": 0}
        if u["connections"] + 1 > lim["connections"]:
            return 0
        by_cmd = lim["commands"] - u["commands"] - OVERHEAD_COMMANDS
        by_bytes = (lim["bytes"] - u["bytes"] - OVERHEAD_BYTES) // BYTES_PER_STAT
        n = max(0, min(by_cmd, by_bytes))
        fit = n if fit is None else min(fit, n)
    return int(fit or 0)


def budget_view() -> dict[str, Any]:
    limits = budget_limits()
    used = usage()
    return {
        "enabled": bool(sr.get("usenet.auto_sample_after_send")),
        "sample_size": int(sr.get("usenet.auto_sample_size")),
        "limits": limits,
        "providers": [
            {
                "slug": r["slug"],
                "label": r["label"],
                "eligible": bool(r["enabled"] and all(providers.credentials(r["slug"]))),
                "usage": used.get(r["slug"])
                or {p: {"connections": 0, "commands": 0, "bytes": 0} for p in ("daily", "monthly")},
                "fit": budget_fit(r["slug"]),
            }
            for r in providers.list_rows()
        ],
        "last_auto_test_at": get_db().scalar(
            "SELECT max(created_at) FROM usenet_article_tests WHERE trigger=%s", (AUTO_TRIGGER,)
        ),
    }


def queue_auto_sample(nzb_id: int, *, actor: str = "system") -> dict[str, Any] | None:
    """After MSO sent an NZB: queue one STAT sample of exactly those articles on every eligible provider
    (enabled, credentials in MSO, account not known to be unusable, budget left). Never raises."""
    if not sr.get("usenet.auto_sample_after_send"):
        return None
    db = get_db()
    nzb = nzbs.get(nzb_id)
    if not nzb["segments"]:
        return None
    if db.scalar(
        "SELECT 1 FROM usenet_article_tests WHERE nzb_id=%s AND trigger=%s LIMIT 1", (nzb_id, AUTO_TRIGGER)
    ):
        return None
    rows = [
        r
        for r in providers.list_rows()
        if r["enabled"] and all(providers.credentials(r["slug"])) and r["status"] not in SKIP_AUTO_STATUS
    ]
    rows = [r for r in rows if budget_fit(r["slug"]) >= 1]
    if not rows:
        return None
    n = max(1, min(int(sr.get("usenet.auto_sample_size")), len(nzb["segments"])))
    slugs = [r["slug"] for r in rows]
    test = db.fetchone(
        "INSERT INTO usenet_article_tests(nzb_id,trigger,providers,sample_size,requested_by) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (nzb_id, AUTO_TRIGGER, slugs, n, actor),
    )
    job = jobs.enqueue(
        "usenet.article_test",
        {"test_id": test["id"]},
        idempotency_key=f"usenet-art:{test['id']}",
        max_attempts=1,
        created_by=actor,
    )
    db.execute("UPDATE usenet_article_tests SET job_id=%s WHERE id=%s", (job["id"], test["id"]))
    audit(
        "systeem",
        "usenet.auto_sample",
        f"Automatische beschikbaarheidssteekproef na versturen: {n} artikelen × {len(slugs)} provider(s)",
        target=str(nzb_id),
        detail={"providers": slugs, "sample_size": n, "sent_by": actor},
    )
    return public(dict(test)) | {"job_id": job["id"]}


def run(test_id: int, ctx: jobs.JobContext | None = None) -> dict[str, Any]:
    db = get_db()
    test = db.fetchone("SELECT * FROM usenet_article_tests WHERE id=%s", (test_id,))
    if test is None:
        raise jobs.PermanentJobError("test bestaat niet")
    nzb = nzbs.get(test["nzb_id"])
    sample = nzbs.spread_sample(nzb["segments"], test["sample_size"])
    db.execute("UPDATE usenet_article_tests SET status='running' WHERE id=%s", (test_id,))
    deadline = time.monotonic() + MAX_TEST_SECONDS
    per_provider: dict[str, dict[str, Any]] = {}
    for slug in test["providers"]:
        info: dict[str, Any] = {"checked": 0, "available": 0, "missing": 0, "status": "ok", "message": None}
        per_provider[slug] = info
        try:
            row = providers.get(slug)
        except HTTPException:
            info.update(status="error", message="Provider bestaat niet meer")
            continue
        results: list[tuple[Any, ...]] = []
        mine = sample
        if test["trigger"] == AUTO_TRIGGER:
            fit = budget_fit(slug)
            if fit < 1:
                info.update(status="budget_exhausted", message=BUDGET_TEXT)
                continue
            mine = nzbs.spread_sample(sample, min(len(sample), fit))
        used: dict[str, Any] = {"client": None}
        try:
            with providers.session(row) as client:
                used["client"] = client
                for seg in mine:
                    if time.monotonic() > deadline:
                        info.update(status="timeout", message="Tijdslimiet van de test bereikt")
                        break
                    ok = client.stat(seg["m"])
                    results.append((test_id, slug, seg["m"], ok, 223 if ok else 430))
                    info["checked"] += 1
                    info["available" if ok else "missing"] += 1
                    if ctx is not None:
                        ctx.heartbeat()
        except NNTPError as exc:
            info.update(status=exc.status, message=str(exc))
            LOGGER.info("artikeltest %s: provider %s → %s", test_id, slug, exc.status)
        finally:
            c = used["client"]
            record_usage(
                slug,
                connections=1,
                commands=(c.commands_sent if c else 0),
                nbytes=(c.bytes_sent + c.bytes_received if c else 0),
            )
        if results:
            with db.tx() as conn:
                conn.cursor().executemany(
                    "INSERT INTO usenet_article_results(test_id,provider,message_id,available,code) "
                    "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (test_id,provider,message_id) DO UPDATE SET "
                    "available=EXCLUDED.available, code=EXCLUDED.code, checked_at=now()",
                    results,
                )
        if info["checked"]:
            outcomes.observe(
                providers.source_id(slug, row["kind"]),
                "nntp.article_available_ratio",
                info["available"] / info["checked"],
                {"test_id": test_id, "checked": info["checked"]},
            )
    summary = _summarise(test_id, per_provider, len(sample))
    status = "done" if any(p["checked"] for p in per_provider.values()) else "failed"
    db.execute(
        "UPDATE usenet_article_tests SET status=%s, summary=%s, finished_at=now(), error=%s WHERE id=%s",
        (
            status,
            jsonb(summary),
            None if status == "done" else "geen enkele provider kon worden getest",
            test_id,
        ),
    )
    return summary


def _summarise(test_id: int, per_provider: dict[str, dict[str, Any]], sample_n: int) -> dict[str, Any]:
    db = get_db()
    # only articles checked on every provider that answered count for overlap/unique
    answered = [s for s, p in per_provider.items() if p["checked"]]
    uniq = {s: 0 for s in per_provider}
    both_missing = 0
    comparable = 0
    if len(answered) >= 2:
        rows = db.fetchall(
            "SELECT message_id, array_agg(provider) FILTER (WHERE available) AS avail, count(*) AS n "
            "FROM usenet_article_results WHERE test_id=%s AND provider = ANY(%s) AND available IS NOT NULL "
            "GROUP BY message_id HAVING count(*) = %s",
            (test_id, answered, len(answered)),
        )
        comparable = len(rows)
        for r in rows:
            avail = r["avail"] or []
            if len(avail) == 1:
                uniq[avail[0]] += 1
            elif not avail:
                both_missing += 1
    for s, p in per_provider.items():
        p["unique_available"] = uniq.get(s, 0) if len(answered) >= 2 else None
        p["availability"] = round(p["available"] / p["checked"], 4) if p["checked"] else None
    return {
        "sample": sample_n,
        "providers": per_provider,
        "comparable_articles": comparable,
        "missing_everywhere": both_missing if len(answered) >= 2 else None,
        "finished_at": datetime.now(UTC).isoformat(),
    }


@jobs.handler("usenet.article_test")
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    try:
        return run(int(ctx.payload["test_id"]), ctx)
    except (KeyError, ValueError) as exc:
        raise jobs.PermanentJobError("ongeldige payload") from exc


def public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "nzb_id": row["nzb_id"],
        "trigger": row["trigger"],
        "status": row["status"],
        "providers": row["providers"],
        "sample_size": row["sample_size"],
        "summary": row["summary"],
        "error": row["error"],
        "requested_by": row["requested_by"],
        "job_id": row["job_id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def list_tests(limit: int = 50) -> list[dict[str, Any]]:
    rows = get_db().fetchall(
        "SELECT t.*, n.title AS nzb_title FROM usenet_article_tests t JOIN usenet_nzbs n ON n.id=t.nzb_id "
        "ORDER BY t.id DESC LIMIT %s",
        (limit,),
    )
    return [public(r) | {"nzb_title": r["nzb_title"]} for r in rows]


def get_test(test_id: int) -> dict[str, Any]:
    row = get_db().fetchone("SELECT * FROM usenet_article_tests WHERE id=%s", (test_id,))
    if row is None:
        raise HTTPException(404, detail="Test niet gevonden")
    return public(dict(row))
