"""Download outcome vocabulary shared by engines, baseline, recovery and costs."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from mso.db import Database, get_db, jsonb

# Failure reasons are distinguished with certainty where the data allows; otherwise 'unknown'.
FAILURE_REASONS: dict[str, str] = {
    "no_indexer_hit": "Geen indexerresultaat",
    "false_positive": "Vals-positief resultaat (verkeerde inhoud)",
    "rejected_quality": "Afgewezen op kwaliteit (MediaGuard-eisen)",
    "no_retention": "Buiten retentie / artikelen verlopen",
    "incomplete": "Onvolledig (ontbrekende artikelen/blocks, repair faalde)",
    "client_failure": "Fout in downloadclient",
    "unpack_failed": "Uitpakken mislukt",
    "import_failure": "Importfout (na download)",
    "auth_failure": "Authenticatie mislukt bij provider/tracker",
    "account_expired": "Account verlopen",
    "quota_exceeded": "Quotum/limiet bereikt",
    "stalled": "Vastgelopen (geen peers/voortgang)",
    "tracker_error": "Trackerfout",
    "disk_full": "Onvoldoende schijfruimte",
    "encrypted": "Versleuteld/wachtwoord-archief",
    "unknown": "Onbekende oorzaak",
}


def outcome_at(alias: str = "") -> str:
    """SQL expression for the moment an outcome happened: completion time, else the moment it became failed.

    Every window over ``download_outcomes`` uses this. ``observed_at`` is never part of it: it is refreshed on every
    ingest, so a torrent stuck in the client would count as a "recent failure" forever. Rows without either (still
    active, removed) fall outside every outcome window."""
    p = f"{alias}." if alias else ""
    return f"COALESCE({p}finished_at, {p}failed_at)"


def upsert_outcome(row: dict[str, Any], db: Database | None = None) -> int:
    """Insert or update by (client, client_ref). Required: protocol, client, client_ref, title, status.

    ``failed_at`` is maintained here: set on the transition into ``failed`` (to ``row['failed_at']`` when the writer
    knows the moment, else now), kept while the outcome stays failed and cleared when it leaves ``failed``."""
    db = db or get_db()
    cols = [
        "protocol",
        "client",
        "client_ref",
        "release_key",
        "title",
        "client_category",
        "media_category",
        "source_id",
        "status",
        "failure_reason",
        "failure_detail",
        "size_bytes",
        "downloaded_bytes",
        "provider_bytes",
        "private",
        "started_at",
        "finished_at",
        "meta",
    ]
    values = []
    for c in cols:
        v = row.get(c)
        if c in ("provider_bytes", "meta"):
            v = jsonb(v or {})
        values.append(v)
    if row.get("failure_reason") and row["failure_reason"] not in FAILURE_REASONS:
        raise ValueError(f"onbekende failure_reason {row['failure_reason']}")
    updates = ", ".join(f"{c}=COALESCE(EXCLUDED.{c}, download_outcomes.{c})" for c in cols[3:])
    sql = (
        f"INSERT INTO download_outcomes({', '.join(cols)}, failed_at) VALUES ({', '.join(['%s'] * len(cols))}, "
        "CASE WHEN %s = 'failed' THEN COALESCE(%s::timestamptz, now()) END) "
        f"ON CONFLICT (client, client_ref) DO UPDATE SET {updates}, observed_at=now(), "
        "failed_at=CASE WHEN EXCLUDED.status = 'failed' "
        "THEN COALESCE(download_outcomes.failed_at, EXCLUDED.failed_at) END RETURNING id"
    )
    return int(db.scalar(sql, [*values, row.get("status"), row.get("failed_at")]))


def observe(
    subject: str, metric: str, value: float | None, labels: dict[str, Any] | None = None, db: Database | None = None
) -> None:
    (db or get_db()).execute(
        "INSERT INTO observations(subject,metric,value,labels) VALUES (%s,%s,%s,%s)",
        (subject, metric, value, jsonb(labels or {})),
    )


def upsert_source(
    sid: str,
    kind: str,
    label: str,
    origin: str,
    *,
    scopes: list[str] | None = None,
    private: bool = False,
    enabled: bool = True,
    meta: dict[str, Any] | None = None,
    db: Database | None = None,
) -> None:
    (db or get_db()).execute(
        "INSERT INTO sources(id,kind,label,origin,scopes,private,enabled,meta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO UPDATE SET kind=EXCLUDED.kind, label=EXCLUDED.label, origin=EXCLUDED.origin, "
        "scopes=EXCLUDED.scopes, private=EXCLUDED.private, enabled=EXCLUDED.enabled, "
        "meta=sources.meta || EXCLUDED.meta, updated_at=now()",
        (sid, kind, label, origin, scopes or ["normal", "anime"], private, enabled, jsonb(meta or {})),
    )


def recommend(
    area: str,
    title: str,
    explanation: str,
    *,
    risk: str = "advies",
    subject: str | None = None,
    impact: str = "",
    confidence: float | None = None,
    change_id: int | None = None,
    dedupe_key: str | None = None,
    evidence: dict[str, Any] | None = None,
    db: Database | None = None,
) -> int:
    """Create or refresh an open recommendation (dedupe_key keeps it idempotent)."""
    db = db or get_db()
    return int(
        db.scalar(
            "INSERT INTO recommendations(area,subject,title,explanation,impact,risk,confidence,change_id,dedupe_key,"
            "evidence) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO UPDATE SET title=EXCLUDED.title, "
            "explanation=EXCLUDED.explanation, impact=EXCLUDED.impact, confidence=EXCLUDED.confidence, "
            "evidence=EXCLUDED.evidence, change_id=COALESCE(EXCLUDED.change_id, recommendations.change_id), "
            "updated_at=now() RETURNING id",
            (area, subject, title, explanation, impact, risk, confidence, change_id, dedupe_key, jsonb(evidence or {})),
        )
    )


# ------------------------------------------------------------------ failure ratio per item (netto) and per attempt
MIN_RATIO_N = 20  # below this many items/attempts a ratio is shown as "te weinig data"


@lru_cache(maxsize=20000)
def item_key(title: str) -> str | None:
    """Identity of the *wanted item* a download was for (episode, season pack, film), from the existing release
    normalisation (:func:`mso.domain.release.title_part` + :func:`~mso.domain.release.parse_title`): the normalised
    name part plus its numbering (S01E02, absolute anime episode, season) or, for a film, the year. Other releases,
    qualities and retries of the same episode share the key. ``None`` when the title says too little (obfuscated,
    no name): such an attempt is then only its own item."""
    from mso.domain import release

    t = (title or "").strip()
    parsed = release.parse_title(t)
    if parsed.obfuscated:
        return None
    name = " ".join(release._norm_tokens(release.title_part(t)))
    if not name:
        return None
    if parsed.season is not None and parsed.episodes:
        return f"{name}|s{parsed.season}e{'-'.join(str(e) for e in parsed.episodes)}"
    if parsed.absolute_episodes:
        return f"{name}|a{'-'.join(str(e) for e in parsed.absolute_episodes)}"
    if parsed.season is not None and parsed.full_season:
        return f"{name}|s{parsed.season}"
    if parsed.year:
        return f"{name}|{parsed.year}"
    return name


def item_keys(rows: list[dict[str, Any]], links: list[tuple[str, str]] | None = None) -> list[str]:
    """Item key per outcome row (``title``, ``client``, ``client_ref``). ``links`` are ``(client_ref, client_ref)``
    pairs known to be the same item (e.g. a SABnzbd retry that got a new nzo_id) and are merged as well."""
    own = [item_key(str(r.get("title") or "")) or f"ref:{r.get('client')}:{r.get('client_ref')}" for r in rows]
    parent: dict[str, str] = {}

    def find(k: str) -> str:
        root = k
        while parent.get(root, root) != root:
            root = parent[root]
        while k != root:  # path compression
            parent[k], k = root, parent[k]
        return root

    by_ref = {str(r.get("client_ref")): k for r, k in zip(rows, own, strict=True)}
    for a, b in links or []:
        ka, kb = by_ref.get(str(a)), by_ref.get(str(b))
        if ka and kb:
            ra, rb = find(ka), find(kb)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    return [find(k) for k in own]


def _ratio(failed: int, n: int) -> dict[str, Any]:
    return {"failed": failed, "n": n, "value": round(failed / n, 4) if n else None,
            "insufficient": n < MIN_RATIO_N}


def failure_ratios(rows: list[dict[str, Any]], start: Any, end: Any,
                   links: list[tuple[str, str]] | None = None, keys: list[str] | None = None) -> dict[str, Any]:
    """Failure ratio over the outcomes whose moment ``at`` lies in ``(start, end]``.

    - ``gross`` (bruto, per attempt): failed attempts / finished attempts.
    - ``net`` (netto, per episode/film): items with an attempt in the window that never succeeded up to ``end``
      / items with an attempt in the window. An item that failed and then arrived after all counts as succeeded.

    ``rows`` need ``status`` (success|failed), ``at``, ``title``, ``client``, ``client_ref`` and optionally
    ``failure_reason``/``protocol``; pass rows from before ``start`` too so earlier successes are known. ``net``
    carries ``reasons`` (latest failure reason per failed item) and ``by_protocol``."""
    keys = keys if keys is not None else item_keys(rows, links)
    attempts_failed = attempts = 0
    in_window: dict[str, dict[str, Any]] = {}
    succeeded: set[str] = set()
    for r, k in zip(rows, keys, strict=True):
        at = r.get("at")
        if at is None or at > end:
            continue
        if r["status"] == "success":
            succeeded.add(k)
        if at <= start:
            continue
        attempts += 1
        item = in_window.setdefault(k, {"protocol": r.get("protocol"), "reason": None, "last_failed": None})
        if r["status"] == "failed":
            attempts_failed += 1
            if item["last_failed"] is None or at >= item["last_failed"]:
                item["last_failed"], item["reason"] = at, r.get("failure_reason") or "unknown"
    failed_items = {k: v for k, v in in_window.items() if k not in succeeded}
    reasons: dict[str, int] = {}
    by_protocol: dict[str, dict[str, int]] = {}
    for k, v in in_window.items():
        p = by_protocol.setdefault(str(v["protocol"] or "onbekend"), {"n": 0, "failed": 0})
        p["n"] += 1
        if k in failed_items:
            p["failed"] += 1
            reasons[v["reason"]] = reasons.get(v["reason"], 0) + 1
    return {
        "gross": _ratio(attempts_failed, attempts),
        "net": _ratio(len(failed_items), len(in_window)) | {"reasons": reasons, "by_protocol": by_protocol},
        "min_n": MIN_RATIO_N,
    }
