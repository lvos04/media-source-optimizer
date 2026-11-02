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
        try:
            with providers.session(row) as client:
                for seg in sample:
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
