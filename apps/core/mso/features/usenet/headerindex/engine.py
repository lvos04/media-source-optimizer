"""Optional own header index — strictly opt-in and bounded.

Budgets (settings ``usenet.headerindex.*``): whitelisted groups only, retention window in days, storage MB,
headers per run, runs per day, download MB per run and seconds per run. On the first run of a group only the
newest headers that fit the budget are read — never a full group scan. When the storage budget is reached the
run stops, nothing more is stored and an alert (recommendation + audit) is raised.

Storage is accounted as row bytes (``pg_column_size``) × ``INDEX_FACTOR`` for indexes; the physical table size
is reported separately (PostgreSQL reuses freed space after VACUUM but does not shrink the file).

This index does NOT replace an indexer or a provider: it only sees subjects in a few groups for a short
window, cannot build NZBs, and obfuscated posts are unrecognisable.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from fastapi import HTTPException

from mso import jobs
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import get_db
from mso.domain import outcomes
from mso.domain.release import NormalizedRelease, parse_title
from mso.features.usenet import providers
from mso.features.usenet.headerindex.obfuscation import extract_name, is_obfuscated, is_usable
from mso.features.usenet.nntp import NNTPError

LOGGER = logging.getLogger(__name__)
HI = "usenet.headerindex"
INDEX_FACTOR = 1.6
ROW_OVERHEAD = 96  # tuple header + fixed-width columns + varlena headers (conservative)
CHUNK = 1000
MB = 1024 * 1024
MAX_GROUPS = 10
GROUP_RE = re.compile(r"^[A-Za-z0-9.+_-]{1,200}$")


def cfg() -> dict[str, Any]:
    return {
        k: sr.get(f"{HI}.{k}")
        for k in (
            "enabled",
            "groups",
            "provider",
            "retention_days",
            "max_storage_mb",
            "max_headers_per_run",
            "max_runs_per_day",
            "max_download_mb_per_run",
            "max_run_seconds",
        )
    }


SOURCE_ID = "headerindex:own"


def source_id(_slug: str = "") -> str:
    """One stable source for the own header index (whichever provider it reads through)."""
    return SOURCE_ID


def validate_groups(groups: list[str]) -> list[str]:
    clean = []
    for g in groups:
        g = g.strip()
        if not g:
            continue
        if not GROUP_RE.fullmatch(g) or "*" in g:
            raise HTTPException(422, detail=f"Ongeldige groepsnaam '{g}' (geen jokertekens toegestaan)")
        if g not in clean:
            clean.append(g)
    if len(clean) > MAX_GROUPS:
        raise HTTPException(422, detail=f"Maximaal {MAX_GROUPS} groepen")
    return clean


# ------------------------------------------------------------------ storage accounting
def usage() -> dict[str, Any]:
    db = get_db()
    row = db.fetchone(
        "SELECT count(*) AS n, coalesce(sum(pg_column_size(h.*)),0) AS logical FROM usenet_hi_headers h"
    )
    physical = db.scalar("SELECT pg_total_relation_size('usenet_hi_headers')")
    budget = int(sr.get(f"{HI}.max_storage_mb")) * 1024 * 1024
    used = int(int(row["logical"]) * INDEX_FACTOR)
    return {
        "headers": int(row["n"]),
        "estimated_bytes": used,
        "physical_bytes": int(physical or 0),
        "budget_bytes": budget,
        "ratio": round(used / budget, 4) if budget else None,
        "budget_reached": used >= budget,
    }


def _row_estimate(subject: str, mid: str, name: str, grp: str) -> int:
    return int(
        (len(subject.encode()) + len(mid) + len(name.encode()) + len(grp) + ROW_OVERHEAD) * INDEX_FACTOR
    )


def _alert_budget(u: dict[str, Any]) -> None:
    outcomes.recommend(
        "usenet",
        "Header-index: opslagbudget bereikt",
        f"De header-index gebruikt ~{u['estimated_bytes'] // MB} MB van {u['budget_bytes'] // MB} MB. "
        "Indexeren is gestopt. Verlaag de bewaartermijn, verklein de groepenlijst, wis de index of "
        "verhoog het budget.",
        risk="advies",
        subject=source_id(str(sr.get(f"{HI}.provider"))),
        dedupe_key="usenet.headerindex.budget",
        evidence=u,
    )
    audit(
        "system",
        "usenet.headerindex_budget",
        "Header-index gestopt: opslagbudget bereikt",
        target="headerindex",
        detail={"estimated_bytes": u["estimated_bytes"], "budget_bytes": u["budget_bytes"]},
    )


# ------------------------------------------------------------------ run
def _parse_date(s: str) -> datetime | None:
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        return None
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def runs_today() -> int:
    return int(
        get_db().scalar(
            "SELECT count(*) FROM usenet_hi_runs WHERE started_at >= date_trunc('day', now()) AND status "
            "<> 'skipped'"
        )
        or 0
    )


def run(ctx: jobs.JobContext | None = None, *, actor: str = "system") -> dict[str, Any]:
    c = cfg()
    db = get_db()
    if not c["enabled"]:
        return {"status": "skipped", "message": "Header-index staat uit"}
    groups = validate_groups(list(c["groups"] or []))
    if not groups:
        return {"status": "skipped", "message": "Geen toegestane groepen ingesteld"}
    try:
        prow = providers.get(str(c["provider"]))
    except HTTPException:
        return {"status": "skipped", "message": "Geen geldige provider gekozen"}
    if not prow["enabled"] or not all(providers.credentials(prow["slug"])):
        return {"status": "skipped", "message": "Provider uitgeschakeld of zonder inloggegevens"}
    if runs_today() >= int(c["max_runs_per_day"]):
        return {"status": "skipped", "message": "Maximum aantal rondes voor vandaag bereikt"}
    u = usage()
    if u["budget_reached"]:
        db.execute(
            "INSERT INTO usenet_hi_runs(status,finished_at,message) VALUES ('budget_reached',now(),%s)",
            ("Opslagbudget bereikt vóór start",),
        )
        _alert_budget(u)
        return {"status": "budget_reached", "message": "Opslagbudget bereikt"}
    run_id = int(db.scalar("INSERT INTO usenet_hi_runs DEFAULT VALUES RETURNING id"))
    outcomes.upsert_source(
        source_id(prow["slug"]),
        "header_index",
        f"Header-index ({prow['label']})",
        "mso",
        meta={"provider": prow["slug"], "groups": groups},
    )
    used = u["estimated_bytes"]
    budget = u["budget_bytes"]
    max_headers = int(c["max_headers_per_run"])
    max_bytes = int(c["max_download_mb_per_run"]) * 1024 * 1024
    deadline = time.monotonic() + int(c["max_run_seconds"])
    window_start = datetime.now(UTC) - timedelta(days=int(c["retention_days"]))
    per_group = max(1, max_headers // len(groups))
    fetched = stored = obf = usable = 0
    status, message = "done", None
    bytes_received = 0
    try:
        with providers.session(prow) as client:
            base_bytes = client.bytes_received
            for grp in groups:
                if status != "done":
                    break
                try:
                    _, first, last, _ = client.group(grp)
                except NNTPError as exc:
                    db.execute(
                        "INSERT INTO usenet_hi_groups(name,last_error,last_run_at) VALUES (%s,%s,now()) "
                        "ON CONFLICT (name) DO UPDATE SET last_error=EXCLUDED.last_error, last_run_at=now()",
                        (grp, str(exc)),
                    )
                    if exc.status in ("auth_failed", "account_expired", "rate_limited", "block_exhausted"):
                        raise
                    continue
                state = db.fetchone("SELECT last_article FROM usenet_hi_groups WHERE name=%s", (grp,))
                prev = state["last_article"] if state and state["last_article"] else None
                quota = min(per_group, max_headers - fetched)
                start = max(first, (prev + 1) if prev else last - quota + 1, last - quota + 1)
                pos = start
                while pos <= last:
                    if time.monotonic() > deadline:
                        status, message = (
                            "done",
                            "Tijdbudget van de ronde bereikt; volgende ronde gaat verder",
                        )
                        break
                    if fetched >= max_headers:
                        message = "Maximum aantal headers per ronde bereikt"
                        break
                    remaining_bytes = max_bytes - (client.bytes_received - base_bytes)
                    if remaining_bytes <= 0:
                        message = "Downloadbudget van de ronde bereikt"
                        break
                    end = min(last, pos + min(CHUNK, max_headers - fetched) - 1)
                    try:
                        entries = client.over(pos, end, max_bytes=remaining_bytes)
                    except NNTPError as exc:
                        if "limiet" in str(exc):
                            message = "Downloadbudget van de ronde bereikt"
                            break
                        raise
                    fetched += len(entries)
                    batch = []
                    batch_bytes = 0
                    for e in entries:
                        posted = _parse_date(e.date)
                        if posted is not None and posted < window_start:
                            continue
                        name = extract_name(e.subject)
                        ob = is_obfuscated(name)
                        us = not ob and is_usable(name)
                        est = _row_estimate(e.subject, e.message_id, name, grp)
                        if used + batch_bytes + est > budget:
                            status, message = "budget_reached", "Opslagbudget bereikt; indexeren gestopt"
                            break
                        batch_bytes += est
                        batch.append(
                            (
                                grp,
                                e.number,
                                e.message_id[:250],
                                e.subject[:500],
                                name,
                                posted,
                                e.bytes,
                                ob,
                                us,
                            )
                        )
                    if batch:
                        with db.tx() as conn:
                            cur = conn.cursor()
                            cur.executemany(
                                "INSERT INTO "
                                "usenet_hi_headers(grp,article_num,message_id,subject,release_name,"
                                "posted_at,bytes,obfuscated,usable) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                "ON CONFLICT (grp, article_num) DO NOTHING",
                                batch,
                            )
                        stored += len(batch)
                        obf += sum(1 for b in batch if b[7])
                        usable += sum(1 for b in batch if b[8])
                        used += batch_bytes
                    last_done = end if status == "done" else (batch[-1][1] if batch else pos - 1)
                    db.execute(
                        "INSERT INTO usenet_hi_groups(name,last_article,last_run_at,last_error) "
                        "VALUES (%s,%s,now(),NULL) ON CONFLICT (name) DO UPDATE SET "
                        "last_article=GREATEST(coalesce(usenet_hi_groups.last_article,0),EXCLUDED.last_article),"
                        " last_run_at=now(), last_error=NULL",
                        (grp, last_done),
                    )
                    if status != "done":
                        break
                    pos = end + 1
                    if ctx is not None:
                        ctx.heartbeat()
            bytes_received = client.bytes_received - base_bytes
    except NNTPError as exc:
        status, message = "failed", str(exc)
    db.execute(
        "UPDATE usenet_hi_runs SET finished_at=now(), status=%s, headers_fetched=%s, headers_stored=%s, "
        "bytes_received=%s, obfuscated=%s, usable=%s, message=%s WHERE id=%s",
        (status, fetched, stored, bytes_received, obf, usable, message, run_id),
    )
    sid = source_id(prow["slug"])
    outcomes.observe(sid, "headerindex.headers_stored", float(stored), {"run_id": run_id})
    if stored:
        outcomes.observe(sid, "headerindex.obfuscated_ratio", obf / stored, {"run_id": run_id})
    if status == "budget_reached":
        _alert_budget(usage())
    LOGGER.info("header-index ronde %s: %s, %d opgeslagen", run_id, status, stored)
    return {
        "run_id": run_id,
        "status": status,
        "message": message,
        "headers_fetched": fetched,
        "headers_stored": stored,
        "bytes_received": bytes_received,
        "obfuscated": obf,
        "usable": usable,
    }


# ------------------------------------------------------------------ purge (TTL + manual)
def _vacuum() -> None:
    db = get_db()
    with db.pool.connection() as conn:
        prev = conn.autocommit
        conn.autocommit = True
        try:
            conn.execute("VACUUM (ANALYZE) usenet_hi_headers")
        finally:
            conn.autocommit = prev


def purge(scope: str = "expired", *, actor: str = "system") -> dict[str, Any]:
    db = get_db()
    if scope == "all":
        n = int(db.scalar("SELECT count(*) FROM usenet_hi_headers") or 0)
        db.execute("TRUNCATE usenet_hi_headers")
        db.execute("UPDATE usenet_hi_groups SET last_article=NULL")
    elif scope == "expired":
        days = int(sr.get(f"{HI}.retention_days"))
        n = db.execute(
            "DELETE FROM usenet_hi_headers WHERE coalesce(posted_at, inserted_at) < "
            "now() - make_interval(days => %s)",
            (days,),
        )
        _vacuum()
    else:
        raise HTTPException(422, detail="scope moet 'expired' of 'all' zijn")
    if n or actor != "system":
        audit(
            actor,
            "usenet.headerindex_purge",
            f"Header-index opgeschoond ({scope}): {n} headers",
            target="headerindex",
            detail={"scope": scope, "deleted": n},
        )
    if not usage()["budget_reached"]:
        db.execute(
            "UPDATE recommendations SET status='resolved', updated_at=now() "
            "WHERE dedupe_key='usenet.headerindex.budget' AND status='open'"
        )
    return {"deleted": n, "usage": usage()}


@jobs.handler("usenet.headerindex_run")
def _job_run(ctx: jobs.JobContext) -> dict[str, Any]:
    return run(ctx, actor=str(ctx.payload.get("actor") or "system"))


@jobs.handler("usenet.headerindex_purge")
def _job_purge(ctx: jobs.JobContext) -> dict[str, Any]:
    return purge(str(ctx.payload.get("scope") or "expired"))


def enabled() -> bool:
    try:
        return bool(sr.get(f"{HI}.enabled"))
    except Exception:
        return False


def _run_due() -> bool:
    if not enabled():
        return False
    per_day = int(sr.get(f"{HI}.max_runs_per_day"))
    last = get_db().scalar("SELECT max(started_at) FROM usenet_hi_runs WHERE status <> 'skipped'")
    return last is None or (datetime.now(UTC) - last).total_seconds() >= 86400 / per_day - 60


jobs.every("usenet.headerindex_run", 900, enabled=_run_due)
jobs.every(
    "usenet.headerindex_purge",
    3600,
    enabled=lambda: bool(get_db().scalar("SELECT EXISTS (SELECT 1 FROM usenet_hi_headers)")),
)


# ------------------------------------------------------------------ metrics
def metrics() -> dict[str, Any]:
    db = get_db()
    rel = db.fetchone(
        "SELECT count(DISTINCT lower(release_name)) AS releases, "
        "count(DISTINCT lower(release_name)) FILTER (WHERE obfuscated) AS obfuscated, "
        "count(*) AS headers, count(*) FILTER (WHERE obfuscated) AS headers_obfuscated FROM usenet_hi_headers"
    )
    new = db.fetchone(
        "SELECT count(DISTINCT lower(release_name)) AS releases, "
        "count(DISTINCT lower(release_name)) FILTER (WHERE usable) AS usable FROM usenet_hi_headers "
        "WHERE coalesce(posted_at, inserted_at) > now() - interval '48 hours'"
    )
    releases = int(rel["releases"] or 0)
    headers = int(rel["headers"] or 0)
    new_rel = int(new["releases"] or 0)
    return {
        "releases": releases,
        "headers": headers,
        "obfuscated_ratio": round(int(rel["obfuscated"]) / releases, 4) if releases else None,
        "obfuscated_subject_ratio": round(int(rel["headers_obfuscated"]) / headers, 4) if headers else None,
        "new_releases_48h": new_rel,
        "usable_ratio_new": round(int(new["usable"]) / new_rel, 4) if new_rel else None,
    }


def status() -> dict[str, Any]:
    c = cfg()
    runs = get_db().fetchall("SELECT * FROM usenet_hi_runs ORDER BY id DESC LIMIT 20")
    groups = get_db().fetchall("SELECT * FROM usenet_hi_groups ORDER BY name")
    return {
        "config": c,
        "usage": usage(),
        "metrics": metrics(),
        "runs": runs,
        "groups": groups,
        "runs_today": runs_today(),
        "source_id": source_id(str(c["provider"])),
        "disclaimer": DISCLAIMER,
    }


DISCLAIMER = (
    "Een eigen header-index vervangt geen indexer of provider: hij ziet alleen onderwerpen in "
    "de toegestane groepen binnen een korte periode, maakt geen NZB's en kan geobfusceerde "
    "posts niet herkennen."
)


# ------------------------------------------------------------------ search
def search(q: str, *, limit: int = 50) -> list[NormalizedRelease]:
    if not enabled():
        return []
    tokens = [t for t in re.findall(r"[A-Za-z0-9]{2,}", q or "")][:6]
    if not tokens:
        return []
    where = " AND ".join(["lower(release_name) LIKE %s"] * len(tokens))
    params: list[Any] = [f"%{t.lower()}%" for t in tokens]
    rows = get_db().fetchall(
        "SELECT min(release_name) AS name, min(message_id) AS mid, min(grp) AS grp, sum(bytes) AS bytes, "
        "count(*) AS articles, min(posted_at) AS posted FROM usenet_hi_headers "
        f"WHERE NOT obfuscated AND {where} GROUP BY lower(release_name) ORDER BY min(posted_at) DESC NULLS "
        f"LAST "
        "LIMIT %s",
        (*params, max(1, min(limit, 200))),
    )
    slug = str(sr.get(f"{HI}.provider"))
    out = []
    for r in rows:
        name = r["name"]
        out.append(
            NormalizedRelease(
                protocol="usenet",
                source_id=source_id(slug),
                origin="header_index",
                title=name,
                guid="hi:" + hashlib.sha1(name.lower().encode(), usedforsecurity=False).hexdigest()[:20],
                message_id=r["mid"],
                size_bytes=int(r["bytes"] or 0) or None,
                publish_date=r["posted"],
                confidence=0.2,
                ttl_s=600,
                parsed=parse_title(name),
                extra={
                    "articles": int(r["articles"]),
                    "group": r["grp"],
                    "note": "Uit eigen header-index: geen NZB beschikbaar, alleen ter oriëntatie",
                },
            )
        )
    return out
