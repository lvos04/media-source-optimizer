"""Download outcome vocabulary shared by engines, baseline, recovery and costs."""

from __future__ import annotations

import re
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


# a date in the title (daily shows, news, wrestling, sports by date): 2026.11.03 / 2026-11-03 / 2026 11 03
_AIR_DATE_RE = re.compile(r"(?<![0-9])((?:19|20)[0-9]{2})[ ._-](0[1-9]|1[0-2])[ ._-](0[1-9]|[12][0-9]|3[01])(?![0-9])")
# words that make a title with only a year an event/episode rather than a film ("Formula1.2026.Round20",
# "NFL.2026.Week.10", "MotoGP.2026.Brazil.Grand.Prix.Race")
_EVENT_WORDS = frozenset({
    "round", "rnd", "week", "wk", "day", "race", "stage", "leg", "game", "match", "matchday", "episode", "ep",
    "chapter", "part", "pt", "qualifying", "quali", "practice", "sprint", "gp", "grand", "prix", "highlights",
    "final", "semifinal", "quarterfinal", "playoffs", "playoff", "vs", "v", "night", "event",
})
_MATCHUP_WORDS = frozenset({"vs", "v", "at"})


def _tokens_until_quality(text: str) -> list[str]:
    """Words of ``text`` up to the first quality/source token (1080p, WEB-DL, x264, PROPER, …)."""
    from mso.domain import release

    out = []
    for w in re.findall(r"[A-Za-z0-9]+", text):
        if release._TITLE_CUT_RE.fullmatch(w) and not release._YEAR_TOKEN_RE.match(w):
            break
        out.append(w.lower())
    return out


def _numbering_start(t: str, parsed: Any) -> int | None:
    """Where the episode/season numbering starts in the raw title (for the year-before-numbering check)."""
    from mso.domain import release

    if parsed.season is not None and parsed.episodes and (m := release.RE_SXXEXX.search(t)):
        return m.start()
    if parsed.season is not None and parsed.full_season and (m := release.RE_SEASON_ONLY.search(t)):
        return m.start()
    return None


@lru_cache(maxsize=65536)
def item_key(title: str, media_category: str | None = None) -> str | None:
    """Identity of the *wanted item* a download was for, from the existing release normalisation
    (:func:`mso.domain.release.title_part` + :func:`~mso.domain.release.parse_title`): the normalised name part plus

    - S01E02 / a season pack (with the year when the title has one before the numbering: ``Doctor.Who.2005.S01E01``
      and ``Doctor.Who.S01E01`` are different shows), or the absolute anime episode;
    - an air date (daily shows, news, wrestling, sports by date: ``WWE.Raw.2026.11.03``), plus the match-up when the
      title names one (``NBA.2026.11.03.Lakers.vs.Celtics``);
    - for a film: the year. A title with only a year is a film only when ``media_category`` says so, or (category
      unknown) when nothing numbering-like follows the year: ``Formula1.2026.Round20`` is an event, not a film.

    Other releases, qualities and retries of the same episode/film share the key. ``None`` when the title says too
    little (obfuscated, no name, an event without numbering): such an attempt is then only its own item, never
    merged with another episode."""
    from mso.domain import release

    t = (title or "").strip()
    parsed = release.parse_title(t)
    if parsed.obfuscated:
        return None
    name = " ".join(release._norm_tokens(release.title_part(t)))
    if not name:
        return None
    cat = (media_category or "").lower()
    year_m = release.RE_YEAR.search(t)
    num_at = _numbering_start(t, parsed)
    series = name
    if year_m and num_at is not None and year_m.start() < num_at and str(year_m.group(1)) not in name.split():
        series = f"{name} {year_m.group(1)}"
    if parsed.season is not None and parsed.episodes:
        return f"{series}|s{parsed.season}e{'-'.join(str(e) for e in parsed.episodes)}"
    if parsed.absolute_episodes:
        return f"{name}|a{'-'.join(str(e) for e in parsed.absolute_episodes)}"
    if m := _AIR_DATE_RE.search(t):
        key = f"{name}|{m.group(1)}-{m.group(2)}-{m.group(3)}"
        rest = _tokens_until_quality(t[m.end():])
        if _MATCHUP_WORDS & set(rest):
            key += "|" + " ".join(release._norm_tokens(" ".join(rest)))
        return key
    if parsed.season is not None and parsed.full_season:
        return f"{series}|s{parsed.season}"
    if cat.startswith("tv"):
        return None  # a series download without numbering or date: no reliable identity
    if year_m:
        if not cat.startswith("movies"):
            rest = _tokens_until_quality(t[year_m.end():])
            if any(any(c.isdigit() for c in w) or w in _EVENT_WORDS for w in rest):
                return None
        return f"{name}|{year_m.group(1)}"
    return name


def item_keys(rows: list[dict[str, Any]], links: list[tuple[str, str]] | None = None) -> list[str]:
    """Item key per outcome row (``title``, ``client``, ``client_ref``, optionally ``media_category``). ``links``
    are ``(client_ref, client_ref)`` pairs known to be the same item (e.g. a SABnzbd retry that got a new nzo_id)
    and are merged as well."""
    own = [item_key(str(r.get("title") or ""), r.get("media_category"))
           or f"ref:{r.get('client')}:{r.get('client_ref')}" for r in rows]
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
