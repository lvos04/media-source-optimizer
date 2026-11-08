"""Account data SABnzbd keeps per server (1.6.0, C11): expiry date and block quota.

SABnzbd ≥ 3.2 stores per server ``expire_date``, ``quota`` and ``usage_at_start`` — only when the owner filled them in
SABnzbd. MSO reads them with ``get_config`` (:meth:`SabClient.servers`) and:

- reminds the owner in the app 14, 7 and 1 day(s) before an enabled server's account expires (one item per step,
  earlier steps are resolved; a step the owner dismissed stays dismissed). SABnzbd clears ``expire_date`` on the day
  itself, so MSO remembers the last date it saw (``usenet_server_accounts``);
- shows what is left of a block (SABnzbd quota: total − ``usage_at_start``; otherwise the block size entered under
  Usenet-providers minus SABnzbd's total when that size was set) with a run-out forecast from the daily usage of the
  last 14 days, and reminds when the block is nearly used up (≥ 90 % or ≤ 7 days left);
- fills an EMPTY renewal date under Kosten for the service linked to that server (and moves a date it filled itself
  along when SABnzbd's date changes); a date the owner entered is never overwritten. Only MSO's own data is written.

The duplicate "Verlenging X over N dag(en)" reminder from Kosten for the same service and date is suppressed: the
Usenet reminder is the one that counts. Endpoint: ``GET /api/v1/usenet/sab/accounts`` (viewer, no SABnzbd call).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from mso.audit import audit
from mso.auth import Principal, require
from mso.db import get_db
from mso.domain import outcomes
from mso.features.usenet import providers, serverstatus

LOGGER = logging.getLogger(__name__)
ACTOR = "systeem"
REMINDER_STEPS = (14, 7, 1)
BLOCK_WARN_RATIO = 0.9
BLOCK_WARN_DAYS = 7
FORECAST_WINDOW_DAYS = 14
FORECAST_MIN_DAYS = 3
GB = 1024**3

router = APIRouter(prefix="/api/v1/usenet", tags=["usenet"])


def _today() -> date:
    return date.today()


def _date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _fmt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _gb(n: float) -> str:
    return f"{n / GB:.1f}".replace(".", ",") + " GB"


# ------------------------------------------------------------------ persistence
def load() -> dict[str, dict[str, Any]]:
    return {r["server"]: dict(r) for r in get_db().fetchall("SELECT * FROM usenet_server_accounts")}


def persist(servers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Store SABnzbd's account fields; keep the last expiry date after SABnzbd cleared it. Returns the rows as they
    were BEFORE this call (the previous SABnzbd expiry date matters for the Kosten pre-fill)."""
    db = get_db()
    before = load()
    names = []
    for s in servers:
        names.append(s["name"])
        exp = _date(s.get("expire_date")) or (before.get(s["name"]) or {}).get("expire_date")
        db.execute(
            "INSERT INTO usenet_server_accounts(server,expire_date,quota_bytes,usage_at_start,updated_at) "
            "VALUES (%s,%s,%s,%s,now()) ON CONFLICT (server) DO UPDATE SET expire_date=EXCLUDED.expire_date, "
            "quota_bytes=EXCLUDED.quota_bytes, usage_at_start=EXCLUDED.usage_at_start, updated_at=now()",
            (s["name"], exp, s.get("quota_bytes"), s.get("usage_at_start") or 0),
        )
    if names:
        db.execute("DELETE FROM usenet_server_accounts WHERE NOT (server = ANY(%s))", (names,))
    return before


# ------------------------------------------------------------------ block remaining + forecast
def daily_average(server: str) -> float | None:
    """Average bytes per day over the last 14 full days (days without traffic count as 0), or None with less than
    3 days of history."""
    today = _today()
    rows = get_db().fetchall(
        "SELECT day, bytes FROM usenet_server_stats WHERE server=%s AND day >= %s AND day < %s",
        (server, today - timedelta(days=FORECAST_WINDOW_DAYS), today),
    )
    if not rows:
        return None
    span = (today - min(r["day"] for r in rows)).days
    if span < FORECAST_MIN_DAYS:
        return None
    return sum(int(r["bytes"] or 0) for r in rows) / span


def block_info(
    server: str | None,
    *,
    account: dict[str, Any] | None,
    provider: dict[str, Any] | None,
    totals: dict[str, int],
) -> dict[str, Any] | None:
    """What is left of a block for SABnzbd server ``server``: SABnzbd's own quota when set, otherwise the block size
    of the linked provider (kind ``block``) minus SABnzbd's total when that size was set."""
    total = totals.get(server or "")
    since = None
    if account and account.get("quota_bytes"):
        size, source = int(account["quota_bytes"]), "sabnzbd"
        base = int(account.get("usage_at_start") or 0)
    elif provider and provider.get("kind") == "block" and provider.get("block_size_gb"):
        size, source = int(float(provider["block_size_gb"]) * GB), "mso"
        base = int(provider.get("block_baseline_bytes") or 0)
        since = provider.get("block_baseline_at")
    else:
        return None
    used = max(0, int(total) - base) if total is not None else None
    remaining = max(0, size - used) if used is not None else None
    avg = daily_average(server) if server else None
    days_left = None
    if remaining is not None and avg:
        days_left = round(remaining / avg, 1)
    low = False
    if used is not None and size:
        low = used / size >= BLOCK_WARN_RATIO or (days_left is not None and days_left <= BLOCK_WARN_DAYS)
    return {
        "source": source,
        "size_bytes": size,
        "used_bytes": used,
        "remaining_bytes": remaining,
        "used_ratio": round(used / size, 4) if used is not None and size else None,
        "avg_daily_bytes": int(avg) if avg else None,
        "days_left": days_left,
        "run_out_date": (_today() + timedelta(days=int(days_left))).isoformat() if days_left is not None else None,
        "baseline_at": since,
        "low": low,
    }


def expiry_info(account: dict[str, Any] | None) -> dict[str, Any] | None:
    exp = _date((account or {}).get("expire_date"))
    if exp is None:
        return None
    return {"expire_date": exp.isoformat(), "days_left": (exp - _today()).days}


# ------------------------------------------------------------------ reminders
def _upsert_once(key: str, title: str, explanation: str, *, subject: str, impact: str,
                 evidence: dict[str, Any]) -> None:
    """Create the reminder, or refresh its text when it changed; a dismissed/accepted reminder stays as it is."""
    db = get_db()
    row = db.fetchone("SELECT status, title FROM recommendations WHERE dedupe_key=%s", (key,))
    if row and (row["status"] in ("dismissed", "accepted") or (row["status"] == "open" and row["title"] == title)):
        return
    outcomes.recommend("usenet", title, explanation, risk="toestemming_nodig", subject=subject, impact=impact,
                       dedupe_key=key, evidence=evidence)
    db.execute("UPDATE recommendations SET status='open', updated_at=now() WHERE dedupe_key=%s AND status='resolved'",
               (key,))


def _resolve_prefix(prefix: str, keep: str | None = None) -> None:
    get_db().execute(
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE status='open' AND dedupe_key LIKE %s "
        "AND dedupe_key IS DISTINCT FROM %s",
        (prefix.replace("_", "\\_") + "%", keep),
    )


def _linked_services(server: str) -> list[dict[str, Any]]:
    sids = [providers.source_id(p["slug"], p["kind"]) for p in providers.list_rows() if p.get("sab_server") == server]
    if not sids:
        return []
    return get_db().fetchall("SELECT id, label, renewal_date FROM services WHERE active AND source_id = ANY(%s)",
                             (sids,))


def _suppress_costs_duplicate(server: str, exp: date) -> None:
    """Kosten would remind 'Verlenging X over N dag(en)' for the same service and date: keep only the Usenet one."""
    db = get_db()
    for svc in _linked_services(server):
        if svc["renewal_date"] != exp:
            continue
        db.execute(
            "INSERT INTO recommendations(area,subject,title,explanation,impact,risk,status,dedupe_key) "
            "VALUES ('costs',%s,%s,%s,'Alleen informatief','advies','resolved',%s) ON CONFLICT (dedupe_key) DO UPDATE "
            "SET status='resolved', updated_at=now() WHERE recommendations.status='open'",
            (f"service:{svc['id']}", f"Verlenging {svc['label']} op {exp.isoformat()}",
             "Samengevoegd met de herinnering op Usenet-providers (vervaldatum uit SABnzbd).",
             f"costs.renewal:{svc['id']}:{exp.isoformat()}"),
        )


def sync_expiry_reminders(servers: list[dict[str, Any]], labels: dict[str, str] | None = None) -> list[str]:
    """14/7/1-day reminders for enabled servers whose SABnzbd account expires soon. Returns the reminder keys."""
    labels = serverstatus._provider_labels() if labels is None else labels
    today = _today()
    keys = []
    for s in servers:
        prefix = f"usenet.server_expiry:{s['name']}:"
        exp = _date(s.get("expire_date"))
        days = (exp - today).days if exp else None
        if exp is None or days is None or not s.get("enable") or days < 0 or days > REMINDER_STEPS[0]:
            _resolve_prefix(prefix)
            continue
        step = next(st for st in reversed(REMINDER_STEPS) if days <= st)
        key = f"{prefix}{exp.isoformat()}:{step}"
        _resolve_prefix(prefix, keep=key)
        name = serverstatus.display_name(
            {"server": s["name"], "displayname": s.get("displayname") or s["name"],
             "short": serverstatus.short_name(s.get("displayname") or s["name"])}, labels)
        when = "vandaag" if days == 0 else "morgen" if days == 1 else f"over {days} dagen"
        title = f"Account bij {name} verloopt {when} ({_fmt(exp)}) — verlengen?"
        explanation = (
            f"Volgens SABnzbd verloopt het account bij {name} op {_fmt(exp)}. Verleng je het, pas dan in SABnzbd de "
            "vervaldatum van deze server aan; MSO ziet de nieuwe datum vanzelf. Verleng je het niet, dan meldt de "
            "provider na die datum 'account verlopen': MSO zet de server dan in Autopilot uit en test hem elke week "
            "opnieuw. MSO zegt nooit iets op en verwijdert niets. Bron: SABnzbd (vervaldatum die jij daar invulde)."
        )
        _upsert_once(key, title, explanation, subject=f"sabnzbd:{s['name']}",
                     impact="Na de vervaldatum geen artikelen meer van deze server",
                     evidence={"server": s["name"], "expire_date": exp.isoformat(), "days_left": days, "step": step,
                               "source": "sabnzbd"})
        _suppress_costs_duplicate(s["name"], exp)
        keys.append(key)
    return keys


def sync_block_reminders(servers: list[dict[str, Any]], accounts: dict[str, dict[str, Any]],
                         labels: dict[str, str] | None = None) -> list[str]:
    """Reminder when a block (SABnzbd quota or MSO block size) of an enabled server is nearly used up."""
    from mso.features.usenet import history  # lazy: history imports a lot

    labels = serverstatus._provider_labels() if labels is None else labels
    totals = history.server_totals()
    linked = {p["sab_server"]: p for p in providers.list_rows() if p.get("sab_server")}
    keys = []
    for s in servers:
        prefix = f"usenet.block_low:{s['name']}:"
        info = block_info(s["name"], account=accounts.get(s["name"]), provider=linked.get(s["name"]), totals=totals)
        if not info or not info["low"] or not s.get("enable"):
            _resolve_prefix(prefix)
            continue
        baseline = (accounts.get(s["name"]) or {}).get("usage_at_start") if info["source"] == "sabnzbd" else (
            linked[s["name"]].get("block_baseline_bytes"))
        key = f"{prefix}{info['size_bytes']}:{baseline or 0}"
        _resolve_prefix(prefix, keep=key)
        name = serverstatus.display_name(
            {"server": s["name"], "displayname": s.get("displayname") or s["name"],
             "short": serverstatus.short_name(s.get("displayname") or s["name"])}, labels)
        left = _gb(info["remaining_bytes"] or 0)
        source = ("het quotum in SABnzbd" if info["source"] == "sabnzbd"
                  else "de blokgrootte op Usenet-providers en de byteteller van SABnzbd")
        forecast = ""
        if info["days_left"] is not None:
            forecast = f" (bij het huidige verbruik nog ~{int(info['days_left'])} dag(en))"
        _upsert_once(
            key,
            f"Blok bij {name} bijna op: nog {left}{forecast}",
            f"Van het blok bij {name} ({_gb(info['size_bytes'])}) is nog {left} over{forecast}. Koop op tijd een nieuw "
            "blok of zet een andere server eerder in de volgorde. Is het blok op, dan meldt SABnzbd 'blok op' en zet "
            f"MSO de server in Autopilot uit. Bron: {source}.",
            subject=f"sabnzbd:{s['name']}",
            impact="Als het blok op is, vult deze server geen ontbrekende artikelen meer aan",
            evidence={"server": s["name"], **{k: v for k, v in info.items() if k != "baseline_at"}},
        )
        keys.append(key)
    return keys


def prefill_renewal(servers: list[dict[str, Any]], before: dict[str, dict[str, Any]]) -> list[int]:
    """Fill an empty renewal date under Kosten from SABnzbd's expiry date (only MSO's own data; a date the owner
    entered is never changed, a date MSO filled earlier moves along with SABnzbd)."""
    today = _today()
    done: list[int] = []
    db = get_db()
    for s in servers:
        exp = _date(s.get("expire_date"))
        if exp is None or exp < today:
            continue
        prev = _date((before.get(s["name"]) or {}).get("expire_date"))
        sids = [providers.source_id(p["slug"], p["kind"]) for p in providers.list_rows()
                if p.get("sab_server") == s["name"]]
        if not sids:
            continue
        for svc in db.fetchall(
            "UPDATE services SET renewal_date=%s, updated_at=now() WHERE active AND source_id = ANY(%s) "
            "AND (renewal_date IS NULL OR (renewal_date = %s AND renewal_date <> %s)) RETURNING id, label",
            (exp, sids, prev, exp),
        ):
            done.append(svc["id"])
            audit(ACTOR, "costs.renewal_prefilled",
                  f"Verlengdatum van '{svc['label']}' ingevuld uit SABnzbd: {_fmt(exp)}", target="costs",
                  detail={"service_id": svc["id"], "renewal_date": exp.isoformat(), "server": s["name"],
                          "previous_sab_date": prev.isoformat() if prev else None, "source": "sabnzbd"})
    return done


def sync(servers: list[dict[str, Any]]) -> dict[str, Any]:
    """After every SABnzbd server-status refresh: store the account fields, reminders, Kosten pre-fill."""
    before = persist(servers)
    labels = serverstatus._provider_labels()
    prefilled = prefill_renewal(servers, before)  # first, so the Kosten duplicate of the reminder can be merged
    return {
        "renewal_prefilled": len(prefilled),
        "expiry_reminders": len(sync_expiry_reminders(servers, labels)),
        "block_reminders": len(sync_block_reminders(servers, load(), labels)),
    }


# ------------------------------------------------------------------ API
@router.get("/sab/accounts")
def accounts_view(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """Per SABnzbd server: expiry (days left) and block remaining with forecast (persisted data, no SABnzbd call)."""
    from mso.features.usenet import history

    totals = history.server_totals()
    linked = {p["sab_server"]: p for p in providers.list_rows() if p.get("sab_server")}
    items = {}
    for server, acc in sorted(load().items()):
        items[server] = {
            "expiry": expiry_info(acc),
            "block": block_info(server, account=acc, provider=linked.get(server), totals=totals),
            "updated_at": acc.get("updated_at"),
        }
    return {"items": items, "reminder_days": list(REMINDER_STEPS), "generated_at": datetime.now().astimezone()}
