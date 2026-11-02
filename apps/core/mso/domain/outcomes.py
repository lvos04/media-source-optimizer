"""Download outcome vocabulary shared by engines, baseline, recovery and costs."""

from __future__ import annotations

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


def upsert_outcome(row: dict[str, Any], db: Database | None = None) -> int:
    """Insert or update by (client, client_ref). Required: protocol, client, client_ref, title, status."""
    db = db or get_db()
    cols = ["protocol", "client", "client_ref", "release_key", "title", "client_category", "media_category",
            "source_id", "status", "failure_reason", "failure_detail", "size_bytes", "downloaded_bytes",
            "provider_bytes", "private", "started_at", "finished_at", "meta"]
    values = []
    for c in cols:
        v = row.get(c)
        if c in ("provider_bytes", "meta"):
            v = jsonb(v or {})
        values.append(v)
    if row.get("failure_reason") and row["failure_reason"] not in FAILURE_REASONS:
        raise ValueError(f"onbekende failure_reason {row['failure_reason']}")
    updates = ", ".join(f"{c}=COALESCE(EXCLUDED.{c}, download_outcomes.{c})" for c in cols[3:])
    sql = (f"INSERT INTO download_outcomes({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
           f"ON CONFLICT (client, client_ref) DO UPDATE SET {updates}, observed_at=now() RETURNING id")
    return int(db.scalar(sql, values))


def observe(subject: str, metric: str, value: float | None, labels: dict[str, Any] | None = None,
            db: Database | None = None) -> None:
    (db or get_db()).execute("INSERT INTO observations(subject,metric,value,labels) VALUES (%s,%s,%s,%s)",
                             (subject, metric, value, jsonb(labels or {})))


def upsert_source(sid: str, kind: str, label: str, origin: str, *, scopes: list[str] | None = None,
                  private: bool = False, enabled: bool = True, meta: dict[str, Any] | None = None,
                  db: Database | None = None) -> None:
    (db or get_db()).execute(
        "INSERT INTO sources(id,kind,label,origin,scopes,private,enabled,meta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO UPDATE SET kind=EXCLUDED.kind, label=EXCLUDED.label, origin=EXCLUDED.origin, "
        "scopes=EXCLUDED.scopes, private=EXCLUDED.private, enabled=EXCLUDED.enabled, "
        "meta=sources.meta || EXCLUDED.meta, updated_at=now()",
        (sid, kind, label, origin, scopes or ["normal", "anime"], private, enabled, jsonb(meta or {})))


def recommend(area: str, title: str, explanation: str, *, risk: str = "advies", subject: str | None = None,
              impact: str = "", confidence: float | None = None, change_id: int | None = None,
              dedupe_key: str | None = None, evidence: dict[str, Any] | None = None,
              db: Database | None = None) -> int:
    """Create or refresh an open recommendation (dedupe_key keeps it idempotent)."""
    db = db or get_db()
    return int(db.scalar(
        "INSERT INTO recommendations(area,subject,title,explanation,impact,risk,confidence,change_id,dedupe_key,evidence) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO UPDATE SET title=EXCLUDED.title, "
        "explanation=EXCLUDED.explanation, impact=EXCLUDED.impact, confidence=EXCLUDED.confidence, "
        "evidence=EXCLUDED.evidence, change_id=COALESCE(EXCLUDED.change_id, recommendations.change_id), "
        "updated_at=now() RETURNING id",
        (area, subject, title, explanation, impact, risk, confidence, change_id, dedupe_key, jsonb(evidence or {}))))
