"""Source Baseline (PLAN §5 step 4): why are downloads/items missing, per category and per source?

Inputs: failed ``download_outcomes`` (written by the Usenet/torrent engines) and the cached MediaGuard
wanted items with MediaGuard's own diagnosis code (``last_reason_code``). A cause is only assigned when the
input states it; everything else is ``unknown``. Codes that are not failures at all (not yet aired,
unmonitored, still downloading, …) are excluded and counted separately.
"""

from __future__ import annotations

from typing import Any

from mso import settings_registry as sr
from mso.db import get_db
from mso.domain import categories
from mso.domain.outcomes import FAILURE_REASONS, outcome_at

BUCKETS: list[tuple[str, str]] = [
    ("no_indexer_hit", "Geen indexerresultaat"),
    ("false_positive", "Vals-positief"),
    ("rejected_quality", "Afgewezen op kwaliteit"),
    ("no_retention", "Geen retentie"),
    ("incomplete", "Onvolledig"),
    ("client_failure", "Fout in downloadclient"),
    ("import_failure", "Importfout"),
    ("other", "Andere bekende oorzaak"),
    ("unknown", "Onbekend"),
]
BUCKET_KEYS = [b[0] for b in BUCKETS]

# download_outcomes.failure_reason → bucket (only exact statements; account/tracker/disk problems are 'other')
OUTCOME_MAP: dict[str, str] = {
    "no_indexer_hit": "no_indexer_hit",
    "false_positive": "false_positive",
    "rejected_quality": "rejected_quality",
    "no_retention": "no_retention",
    "incomplete": "incomplete",
    "client_failure": "client_failure",
    "disk_full": "client_failure",
    "import_failure": "import_failure",
    "auth_failure": "other",
    "account_expired": "other",
    "quota_exceeded": "other",
    "stalled": "other",
    "tracker_error": "other",
    "encrypted": "other",
    "unknown": "unknown",
}

# MediaGuard diagnosis code (wanted/diagnosis.py REASONS) → bucket
MG_MAP: dict[str, str] = {
    "no-indexer-hit": "no_indexer_hit",
    "rejected-quality": "rejected_quality",
    "rejected-language": "rejected_quality",
    "rejected-custom-format": "rejected_quality",
    "rejected-size": "rejected_quality",
    "import-failed": "import_failure",
    "file-not-linked": "import_failure",
    "download-client-failed": "client_failure",
    "download-client-missing": "client_failure",
    "stuck-queue": "other",
    "incorrect-mapping": "other",
    "rejected-other": "other",
    "no-indexers": "other",
    "indexer-auth": "other",
    "indexer-rate-limit": "other",
    "indexer-offline": "other",
    "root-folder-missing": "other",
    "instance-suspended": "other",
    "searched-no-grab": "unknown",
    "unknown": "unknown",
}
# not a failure: excluded from the baseline but reported
MG_EXCLUDED = {
    "intentional-exception",
    "unmonitored",
    "not-yet-aired",
    "downloading",
    "release-available",
    "not-searched-yet",
}


def _empty() -> dict[str, int]:
    return {k: 0 for k in BUCKET_KEYS}


def compute(days: int | None = None) -> dict[str, Any]:
    days = int(days or sr.get("herstel.baseline_window_days"))
    db = get_db()
    by_cat: dict[str, dict[str, int]] = {c: _empty() for c in categories.MEDIA_CATEGORIES}
    by_cat["onbekend"] = _empty()
    by_source: dict[str, dict[str, Any]] = {}
    raw: dict[str, dict[str, int]] = {}
    totals = _empty()

    def bump(cat: str | None, src: str, label: str, bucket: str, raw_code: str, origin: str) -> None:
        by_cat[cat if cat in by_cat else "onbekend"][bucket] += 1
        s = by_source.setdefault(
            src, {"source_id": src, "label": label, "origin": origin, "counts": _empty(), "total": 0}
        )
        s["counts"][bucket] += 1
        s["total"] += 1
        totals[bucket] += 1
        raw.setdefault(bucket, {})
        raw[bucket][raw_code] = raw[bucket].get(raw_code, 0) + 1

    outcomes = db.fetchall(
        "SELECT o.media_category, o.source_id, o.failure_reason, o.protocol, s.label FROM "
        "download_outcomes o "
        "LEFT JOIN sources s ON s.id=o.source_id WHERE o.status='failed' "
        f"AND {outcome_at('o')} > now() - make_interval(days => %s)",
        (days,),
    )
    with_reason = 0
    for o in outcomes:
        reason = o["failure_reason"] or "unknown"
        bucket = OUTCOME_MAP.get(reason, "unknown")
        with_reason += bucket != "unknown"
        src = o["source_id"] or f"onbekend:{o['protocol']}"
        label = o["label"] or (o["source_id"] or f"Onbekende bron ({o['protocol']})")
        bump(o["media_category"], src, label, bucket, reason, "download")

    mg_rows = db.fetchall(
        "SELECT instance_id, media_category, last_reason_code, raw->>'instance_name' AS iname "
        "FROM mg_wanted_cache"
    )
    excluded: dict[str, int] = {}
    mg_counted = mg_known = 0
    for w in mg_rows:
        code = w["last_reason_code"]
        if code in MG_EXCLUDED:
            excluded[code] = excluded.get(code, 0) + 1
            continue
        bucket = MG_MAP.get(code or "", "unknown")
        mg_counted += 1
        mg_known += bucket != "unknown"
        bump(
            w["media_category"],
            f"mediaguard:{w['instance_id']}",
            f"MediaGuard · {w['iname'] or w['instance_id']}",
            bucket,
            code or "niet-gediagnosticeerd",
            "mediaguard",
        )

    total = len(outcomes) + mg_counted
    known = with_reason + mg_known
    coverage = round(known / total, 3) if total else None
    confidence = (
        "geen data"
        if not total
        else (
            "hoog"
            if (coverage or 0) >= 0.8 and total >= 20
            else "middel"
            if (coverage or 0) >= 0.5
            else "laag"
        )
    )
    if "onbekend" in by_cat and not any(by_cat["onbekend"].values()):
        by_cat.pop("onbekend")
    return {
        "window_days": days,
        "buckets": [{"key": k, "label": label} for k, label in BUCKETS],
        "totals": totals,
        "by_category": by_cat,
        "by_source": sorted(by_source.values(), key=lambda s: -s["total"]),
        "raw_reasons": raw,
        "raw_labels": {**{k: v for k, v in FAILURE_REASONS.items()}},
        "excluded_mediaguard": excluded,
        "coverage": {
            "failed_downloads": len(outcomes),
            "failed_downloads_with_reason": with_reason,
            "mediaguard_items": mg_counted,
            "mediaguard_items_with_reason": mg_known,
            "known_share": coverage,
            "confidence": confidence,
        },
        "note": "Een oorzaak wordt alleen toegekend als de downloadclient of MediaGuard die "
        "vastlegde; anders "
        "'onbekend'. MediaGuard-data komt uit de cache en is niet leidend.",
    }
