"""Why SABnzbd puts downloads in its default category '*', and where to fix it (1.6.0, C34 reshaped).

SABnzbd files a job whose category it does not know under '*' (``misc.cat_to_opts``: "Ignore the input category if
we don't know it"). Sonarr/Radarr only list SABnzbd jobs in their own category, so such jobs are invisible to them:
no automatic import and no failed-download handling (blocklist + search again). MediaGuard's delivery and cleanup
only look at its own categories (tv, tv-anime, movies, movies-anime, mso-*), so '*' jobs fall outside those too.

The diagnosis compares, read-only, what Sonarr/Radarr send (MediaGuard ``GET /instances/{id}/download-clients``,
``tvCategory``/``movieCategory``) with SABnzbd's categories (``get_config&section=categories``):

(a) Sonarr/Radarr send category X that SABnzbd does not know → SABnzbd puts it in '*'. Fix: create X in SABnzbd —
    one change set ``sabnzbd_categories`` (risk ``toestemming_nodig``, never automatic: once X exists, MediaGuard
    cleans downloads in X by its own rules, and MSO cannot undo that part).
(b) Sonarr/Radarr send no category → fix it where the download client is managed: MediaGuard (provisioning) when
    MediaGuard created/manages that client, otherwise in Sonarr/Radarr. MSO never writes to either.
(c) The categories match, yet '*' downloads keep appearing → another source (MSO's own sends with a category
    SABnzbd lacks, manual uploads, RSS, another app).

Plus Sonarr/Radarr's own health messages about download client/category/import/paths (via MediaGuard) and the
number of '*' downloads per (local) day, so the effect of a fix is visible.

Without MediaGuard (or with an older MediaGuard without these routes) the diagnosis degrades gracefully: what
MediaGuard's instance list says (``download_category`` / its default per media type) is used as a *probable*
category, and without any MediaGuard information only the '*' counts and general advice remain.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes, jobs, state
from mso import settings_registry as sr
from mso.connectors import base as cb
from mso.connectors.sabnzbd import (
    CATEGORY_ABSENT,
    CATEGORY_TARGET,
    SabClient,
    SabError,
    category_present,
    validate_category_name,
)
from mso.db import get_db
from mso.domain import outcomes
from mso.domain.categories import MEDIA_CATEGORIES
from mso.security import redact

LOGGER = logging.getLogger(__name__)
JOB = "usenet.categories_check"
SOURCE = "usenet.categories"  # change_sets.source and recommendations dedupe prefix
STATE_KEY = "usenet.categories.remote"  # last SABnzbd + MediaGuard reads (no secrets)
# categories whose proposal the owner discarded or rolled back: the periodic check does not propose them again
DECLINED_KEY = "usenet.categories.declined"
SYSTEM_ACTORS = ("systeem", "automatisering")  # decisions by these are MSO's own, not the owner's
CACHE_S = 600
DAYS = 14
RECENT_DAYS = 7
LOCAL_TZ = "Europe/Amsterdam"  # default for the owner's local days (``TZ`` wins, like the goals card)


def _local_tz() -> str:
    """The owner's zone for day buckets: the same helper the Overzicht cards use (``TZ``, else Europe/Amsterdam)."""
    from mso.features.overview.autopilot import local_tz  # lazy: overview imports many features

    try:
        return local_tz()
    except Exception:  # day buckets must never break the diagnosis
        return LOCAL_TZ


# MediaGuard provisioning/templates.py DEFAULT_CATEGORY: the category MediaGuard gives each media type
MG_DEFAULT_CATEGORY = {"tv": "tv", "anime": "tv-anime", "movie": "movies", "anime_movie": "movies-anime"}
# Sonarr/Radarr health checks that are about the download client, its category, import or remote paths
HEALTH_SOURCES = (
    "DownloadClientCheck",
    "DownloadClientStatusCheck",
    "DownloadClientSortingCheck",
    "DownloadClientRootFolderCheck",
    "DownloadClientRemovesCompletedDownloadsCheck",
    "ImportMechanismCheck",
    "RemotePathMappingCheck",
)
HEALTH_WORDS = ("categor", "download client", "downloadclient", "sabnzbd", "remote path")
# never about SABnzbd categories, even when the text matches a word above (e.g. "All import lists are unavailable")
HEALTH_EXCLUDED_PREFIXES = ("ImportList", "Indexer")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


# ------------------------------------------------------------------ SABnzbd (read)
def _sab() -> dict[str, Any]:
    out: dict[str, Any] = {
        "configured": cb.is_configured("sabnzbd"),
        "available": False,
        "error": None,
        "categories": [],
        "complete_dir": None,
        "queue_total": None,
        "queue_star": None,
    }
    if not out["configured"]:
        return out
    try:
        with SabClient.from_config() as client:
            try:
                cats = client.category_config()
            except SabError as exc:
                if exc.code in ("auth", "timeout", "unreachable"):
                    raise
                cats = [{"name": n, "dir": ""} for n in ["*", *client.categories()]]  # very old SABnzbd
            out["categories"] = [{"name": c["name"].lower(), "dir": c.get("dir") or ""} for c in cats]
            try:
                out["complete_dir"] = client.complete_dir()
            except SabError:
                out["complete_dir"] = None
            slots = client.queue(limit=500).get("slots") or []
            out["queue_total"] = len(slots)
            out["queue_star"] = sum(1 for s in slots if str(s.get("cat") or "*") == "*")
        out["available"] = True
    except (SabError, cb.ConnectorNotConfigured) as exc:
        out["error"] = redact(str(exc))[:300]
    return out


# ------------------------------------------------------------------ MediaGuard (read, optional)
def _mg_reason(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    return {
        "forbidden": "geen_toegang",
        "auth_failed": "geen_toegang",
        "route_missing": "route_ontbreekt",
        "offline": "offline",
        "not_found": "niet_gevonden",
    }.get(code, "fout")


def _relevant_health(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for h in items:
        msg = str(h.get("message") or "").lower()
        source = str(h.get("source") or "")
        if source.startswith(HEALTH_EXCLUDED_PREFIXES):
            continue
        if source in HEALTH_SOURCES or any(w in msg for w in HEALTH_WORDS):
            out.append({k: h.get(k) for k in ("source", "type", "message")})
    return out


def _mediaguard(*, refresh: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "configured": cb.is_configured("mediaguard"),
        "available": False,
        "reason": None,
        "instances": [],
        "provisioning": None,
        "provisioning_reason": None,
    }
    if not out["configured"]:
        out["reason"] = "niet_gekoppeld"
        return out
    from mso.connectors import mediaguard as mgc
    from mso.features.mediaguard import service as mgs

    try:
        probe = mgs.refresh_probe(force=refresh)
    except Exception as exc:  # probe problems never break the diagnosis
        LOGGER.info("MediaGuard-probe mislukt: %s", redact(str(exc)))
        probe = None
    if probe is None or not probe.get("reachable"):
        out["reason"] = "offline"
        return out
    if not probe.get("auth_ok"):
        out["reason"] = "geen_toegang"
        return out
    caps = probe.get("capabilities") or {}
    client = mgc.MediaGuardClient.from_config()
    if caps.get("instances_list") is False:
        out["reason"] = "route_ontbreekt"
        return out
    try:
        instances = client.instances()
    except mgc.MediaGuardError as exc:
        out["reason"] = _mg_reason(exc)
        return out
    out["available"] = True
    if caps.get("provisioning_config") is False:
        out["provisioning_reason"] = "route_ontbreekt"
    else:
        try:
            out["provisioning"] = client.provisioning_categories()
        except mgc.MediaGuardError as exc:
            out["provisioning_reason"] = _mg_reason(exc)
    for inst in instances:
        if inst["archived"] or inst["app"] not in ("sonarr", "radarr"):
            continue
        row: dict[str, Any] = {
            "id": inst["id"],
            "name": inst["name"],
            "app": inst["app"],
            "media_type": inst["media_type"],
            "suspended": inst["suspended"],
            "download_category": inst["download_category"],
            "clients": None,
            "clients_reason": None,
            "health": [],
            "health_source": None,
        }
        if caps.get("instance_download_clients") is False:
            row["clients_reason"] = "route_ontbreekt"
        else:
            try:
                row["clients"] = [
                    {k: dc[k] for k in ("name", "implementation", "enable", "category", "mediaguard_owned")}
                    for dc in client.download_clients(inst["id"])
                    if dc["implementation"].lower() == "sabnzbd"
                ]
            except mgc.MediaGuardError as exc:
                row["clients_reason"] = _mg_reason(exc)
        health = None
        if caps.get("instance_health") is not False:
            try:
                health = client.instance_health(inst["id"])
                row["health_source"] = "live"
            except mgc.MediaGuardError:
                health = None
        if health is None:
            health = inst["health"]
            row["health_source"] = "cached"
        row["health"] = _relevant_health(health)
        out["instances"].append(row)
    return out


def _remote(*, refresh: bool) -> dict[str, Any]:
    """SABnzbd + MediaGuard reads, cached ``CACHE_S`` in ``system_state`` (only names/categories, no secrets)."""
    cached = state.get_state(STATE_KEY)
    if not refresh and isinstance(cached, dict) and cached.get("checked_at"):
        try:
            age = (_now() - datetime.fromisoformat(cached["checked_at"])).total_seconds()
        except ValueError:
            age = CACHE_S
        if age < CACHE_S:
            return cached
    data = {"checked_at": _iso(_now()), "sab": _sab(), "mediaguard": _mediaguard(refresh=refresh)}
    state.set_state(STATE_KEY, data)
    return data


# ------------------------------------------------------------------ MSO's own sends + '*' counts (local DB)
def _mso_sends(sab_names: set[str] | None) -> dict[str, Any]:
    try:
        mode = sr.get("herstel.send_category_mode")
    except KeyError:  # recovery module not loaded
        mode = None
    rows = get_db().fetchall(
        "SELECT lower(category) AS category, count(*) AS n FROM usenet_nzbs WHERE origin <> 'upload' "
        "AND nzo_id IS NOT NULL AND category IS NOT NULL AND created_at > now() - interval '30 days' "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    used = [{"category": r["category"], "count": int(r["n"])} for r in rows]
    missing = [u["category"] for u in used if sab_names is not None and u["category"] not in sab_names]
    return {"send_category_mode": mode, "used": used, "missing": missing}


def star_series(days: int = DAYS) -> dict[str, Any]:
    """SABnzbd downloads per local day (finished, or first seen while still busy): total and in '*', and how many
    of those '*' downloads MSO sent itself."""
    db = get_db()
    tz = _local_tz()
    rows = db.fetchall(
        f"SELECT ({outcomes.outcome_at('o')} AT TIME ZONE %s)::date AS day, count(*) AS total, "
        "count(*) FILTER (WHERE o.client_category = '*') AS star, "
        "count(*) FILTER (WHERE o.client_category = '*' AND n.id IS NOT NULL) AS star_mso "
        "FROM download_outcomes o LEFT JOIN usenet_nzbs n ON n.nzo_id = o.client_ref "
        f"WHERE o.client = 'sabnzbd' AND {outcomes.outcome_at('o')} >= "
        "(date_trunc('day', now() AT TIME ZONE %s) - make_interval(days => %s)) AT TIME ZONE %s "
        "GROUP BY 1 ORDER BY 1",
        (tz, tz, days - 1, tz),
    )
    by_day = {r["day"]: r for r in rows}
    today = db.scalar("SELECT (now() AT TIME ZONE %s)::date", (tz,))
    series = []
    for i in range(days - 1, -1, -1):
        d: date = today - timedelta(days=i)
        r = by_day.get(d)
        series.append(
            {
                "day": d.isoformat(),
                "total": int(r["total"]) if r else 0,
                "star": int(r["star"]) if r else 0,
                "star_mso": int(r["star_mso"]) if r else 0,
            }
        )
    recent = series[-RECENT_DAYS:]
    return {
        "days": series,
        "timezone": tz,
        "recent_days": RECENT_DAYS,
        "recent_total": sum(d["total"] for d in recent),
        "recent_star": sum(d["star"] for d in recent),
        "recent_star_mso": sum(d["star_mso"] for d in recent),
    }


# ------------------------------------------------------------------ diagnosis
def _provisioned_for(prov: dict[str, Any] | None, instance_id: str) -> dict[str, Any]:
    """Whether MediaGuard provisioning manages this instance's SABnzbd download client, and its category."""
    if not prov:
        return {"managed": None, "category": None}
    inst = (prov.get("instances") or {}).get(instance_id) or {}
    if inst.get("downloadclients_disabled") or inst.get("mode") == "hold":
        return {"managed": False, "category": None}
    for dc in prov.get("download_clients") or []:
        if dc.get("implementation", "").lower() != "sabnzbd" or not dc.get("enabled", True):
            continue
        if dc.get("instances") is not None and instance_id not in dc["instances"]:
            continue
        cat = (dc.get("categories") or {}).get(instance_id) or inst.get("category")
        return {"managed": True, "category": cat or (prov.get("default_categories") or {}).get(instance_id)}
    return {"managed": False, "category": None}


def _evaluate(remote: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    sab, mg = remote["sab"], remote["mediaguard"]
    sab_names = {c["name"] for c in sab["categories"]} if sab["available"] else None
    prov = mg.get("provisioning")
    instances = []
    missing: dict[str, dict[str, Any]] = {}  # case (a): category -> {category, instances, certainty}
    no_category = []  # case (b)
    for inst in mg.get("instances") or []:
        p = _provisioned_for(prov, inst["id"])
        expected = (
            p["category"] or inst.get("download_category") or MG_DEFAULT_CATEGORY.get(inst.get("media_type") or "")
        )
        clients = []
        if inst.get("clients") is not None:
            certainty = "zeker"
            source = [(c["name"], c["enable"], c["category"], c["mediaguard_owned"]) for c in inst["clients"]]
        elif expected:
            certainty = "waarschijnlijk"  # MediaGuard's own category for this instance; the client list is unreadable
            source = [(None, True, expected, True)]
        else:
            certainty, source = None, []
        for name, enabled, cat, owned in source:
            cat_norm = (cat or "").strip().lower() or None
            if not enabled:
                status = "uit"
            elif not cat_norm:
                status = "geen_categorie"
            elif sab_names is None:
                status = "onbekend"
            elif cat_norm in sab_names:
                status = "ok"
            else:
                status = "ontbreekt_in_sab"
            fix_in = "mediaguard" if (owned or p["managed"]) else "arr"
            clients.append(
                {
                    "name": name,
                    "category": cat_norm,
                    "enabled": enabled,
                    "mediaguard_owned": owned,
                    "status": status,
                    "fix_in": fix_in,
                    "certainty": certainty,
                }
            )
            if status == "ontbreekt_in_sab" and cat_norm:
                m = missing.setdefault(
                    cat_norm,
                    {"category": cat_norm, "instances": [], "certainty": certainty, "creatable": _creatable(cat_norm)},
                )
                m["instances"].append(inst["name"])
                if certainty == "zeker":
                    m["certainty"] = "zeker"
            if status == "geen_categorie":
                no_category.append(
                    {"instance": inst["name"], "client": name, "fix_in": fix_in, "expected": expected}
                )
        instances.append(
            {
                "id": inst["id"],
                "name": inst["name"],
                "app": inst["app"],
                "media_type": inst.get("media_type"),
                "expected_category": expected,
                "provisioned": p["managed"],
                "clients": clients,
                "clients_reason": inst.get("clients_reason"),
                "no_sab_client": inst.get("clients") == [],
                "health": inst.get("health") or [],
                "health_source": inst.get("health_source"),
            }
        )
    mso = _mso_sends(sab_names)
    star_recent = stats["recent_star"] + (sab.get("queue_star") or 0)
    other = None  # case (c); recent_star 0 with queue_star > 0 = only the live queue has '*' downloads
    if star_recent and sab["available"] and not missing and not no_category:
        other = {
            "recent_star": stats["recent_star"],
            "queue_star": sab.get("queue_star") or 0,
            "from_mso": stats["recent_star_mso"],
            "mso_missing": mso["missing"],
            "unexplained": max(stats["recent_star"] - stats["recent_star_mso"], 0),
        }
    # only client lists MSO actually read count: MediaGuard's own category ('waarschijnlijk', when the
    # download-clients route is missing or forbidden) is an assumption and never makes the verdict 'ok' or 'c'
    knows_arr = any(c["certainty"] == "zeker" for i in instances for c in i["clients"])
    if missing:
        verdict = "a"
    elif no_category:
        verdict = "b"
    elif not sab["available"] or (star_recent and not knows_arr):
        verdict = "onbekend"
    elif other:
        verdict = "c"
    elif not knows_arr:
        verdict = "geen_ster"  # nothing in '*' lately, but what Sonarr/Radarr send is unknown (no MediaGuard)
    else:
        verdict = "ok"
    folders = {
        name: (f"{sab['complete_dir'].rstrip('/')}/{name}" if sab.get("complete_dir") else name) for name in missing
    }
    return {
        "verdict": verdict,
        "missing": sorted(missing.values(), key=lambda m: m["category"]),
        "folders": folders,
        "no_category": no_category,
        "other": other,
        "instances": instances,
        "mso": mso,
    }


def _op_names(ops: Any) -> list[str]:
    return [str(op.get("path", "")).split("/", 1)[-1] for op in ops or []]


def _open_proposal(db: Any = None) -> dict[str, Any] | None:
    row = (db or get_db()).fetchone(
        "SELECT id, status, title, ops, created_at, created_by, dedupe_key FROM change_sets WHERE source=%s AND "
        "status IN ('preview','approved') ORDER BY id DESC LIMIT 1",
        (SOURCE,),
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "title": row["title"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "dedupe_key": row["dedupe_key"],
        "categories": _op_names(row["ops"]),
    }


def _last_applied(db: Any = None) -> dict[str, Any] | None:
    row = (db or get_db()).fetchone(
        "SELECT id, status, title, ops, applied_at, rolled_back_at FROM change_sets WHERE source=%s AND status IN "
        "('applied','rolled_back') ORDER BY id DESC LIMIT 1",
        (SOURCE,),
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "title": row["title"],
        "applied_at": row["applied_at"],
        "rolled_back_at": row["rolled_back_at"],
        "categories": _op_names(row["ops"]),
    }


def diagnose(*, refresh: bool = False) -> dict[str, Any]:
    remote = _remote(refresh=refresh)
    stats = star_series()
    ev = _evaluate(remote, stats)
    sab, mg = remote["sab"], remote["mediaguard"]
    return {
        "checked_at": remote["checked_at"],
        **ev,
        "sab": {
            "configured": sab["configured"],
            "available": sab["available"],
            "error": sab["error"],
            "categories": [c["name"] for c in sab["categories"]],
            "complete_dir": sab["complete_dir"],
            "queue_total": sab["queue_total"],
            "queue_star": sab["queue_star"],
        },
        "mediaguard": {
            "configured": mg["configured"],
            "available": mg["available"],
            "reason": mg["reason"],
            "provisioning": mg.get("provisioning") is not None,
            "provisioning_reason": mg.get("provisioning_reason"),
        },
        "star": stats,
        "proposal": _public_proposal(_open_proposal()),
        "declined": declined_names(),
        "last_applied": _last_applied(),
        "media_categories": list(MEDIA_CATEGORIES),
    }


# ------------------------------------------------------------------ proposal (case a)
def _explanation(names: list[str], d: dict[str, Any]) -> str:
    by_name = {m["category"]: m for m in d["missing"]}
    parts = []
    for n in names:
        insts = ", ".join(by_name[n]["instances"]) if n in by_name else ""
        parts.append(f"'{n}'" + (f" (gebruikt door {insts})" if insts else ""))
    folders = "; ".join(f"'{n}' → map {d['folders'].get(n, n)}" for n in names)
    probable = [n for n in names if by_name.get(n, {}).get("certainty") == "waarschijnlijk"]
    note = (
        f" MSO kon de downloadclients niet uitlezen; {', '.join(repr(n) for n in probable)} is de categorie die "
        "MediaGuard voor die instantie instelt."
        if probable
        else ""
    )
    return (
        f"SABnzbd kent de categorie{'ën' if len(names) != 1 else ''} {', '.join(parts)} niet. SABnzbd zet zulke "
        "downloads daarom in zijn standaardcategorie '*', en Sonarr/Radarr zien hun eigen downloads dan niet terug: "
        "geen automatische import en geen afhandeling van mislukte downloads (blokkeren en opnieuw zoeken)."
        f"{note} Wat er gebeurt: MSO maakt in SABnzbd een nieuwe categorie aan met een eigen downloadmap ({folders}); "
        "nabewerking, script en prioriteit volgen '*' (SABnzbd-standaard). Daarna leest MSO de SABnzbd-configuratie "
        "terug ter controle. Nieuwe downloads komen dan in die map; downloads die nu al in '*' staan blijven daar. "
        "Let op: MediaGuard ruimt downloads in deze categorie daarna volgens zijn eigen regels op (geïmporteerd of "
        "afgehandeld: meteen; voltooid maar nooit geïmporteerd: na 7 dagen; eerst naar de MediaGuard-prullenbak). "
        "Dat deel kan MSO niet terugdraaien. Terugdraaien verwijdert alleen een categorie die MSO zelf heeft "
        "aangemaakt, en alleen zolang geen download in de SABnzbd-wachtrij of in nabewerking die categorie "
        "gebruikt. MSO draait dit nooit automatisch terug."
    )


def _creatable(name: str) -> bool:
    try:
        return validate_category_name(name) == name
    except ValueError:
        return False


def propose(names: list[str] | None, actor: str, *, d: dict[str, Any] | None = None) -> dict[str, Any]:
    """One change set that creates the SABnzbd categories Sonarr/Radarr send but SABnzbd lacks (case a). Risk
    ``toestemming_nodig``: preview → approval by an admin → apply; never applied automatically."""
    d = d if d is not None else diagnose(refresh=True)
    if not d["sab"]["available"]:
        raise HTTPException(409, detail="SABnzbd is niet bereikbaar; MSO kan de categorieën niet controleren")
    allowed = [m["category"] for m in d["missing"] if m["creatable"]]
    if not allowed:
        raise HTTPException(
            409,
            detail="Geen ontbrekende SABnzbd-categorieën die MSO kan aanmaken: SABnzbd kent alles wat Sonarr/Radarr "
            "meesturen, of de naam bevat tekens die MSO niet in SABnzbd zet (maak hem dan zelf aan)",
        )
    if names:
        # missing, but a name MSO does not put in SABnzbd (e.g. with '/' or non-ASCII): say so explicitly
        manual_only = {m["category"] for m in d["missing"] if not m["creatable"]}
        manual = sorted({str(n).strip().lower() for n in names} & manual_only)
        if manual:
            raise HTTPException(
                422,
                detail=f"Categorie {', '.join(manual)} ontbreekt wel, maar de naam bevat tekens die MSO niet in "
                "SABnzbd zet; maak hem zelf aan in SABnzbd (Config → Categories)",
            )
        try:
            wanted = sorted({validate_category_name(n) for n in names})
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        extra = [n for n in wanted if n not in allowed]
        if extra:
            raise HTTPException(
                422, detail=f"Categorie {', '.join(extra)} ontbreekt niet volgens de diagnose; geen voorstel gemaakt"
            )
    else:
        wanted = sorted(allowed)
    ops = [
        {
            "path": f"categories/{n}",
            "label": f"SABnzbd-categorie '{n}' (map {d['folders'].get(n, n)})",
            "before": CATEGORY_ABSENT,
            "after": category_present(n),
        }
        for n in wanted
    ]
    title = (
        f"SABnzbd-categorie {wanted[0]} aanmaken"
        if len(wanted) == 1
        else f"SABnzbd-categorieën {', '.join(wanted)} aanmaken"
    )
    explanation = _explanation(wanted, d)
    key = f"{SOURCE}:{hashlib.sha256(','.join(wanted).encode()).hexdigest()[:16]}"
    change = changes.create(
        CATEGORY_TARGET,
        title,
        ops,
        risk="toestemming_nodig",
        explanation=explanation,
        impact={  # human keys: the change preview shows them as they are
            "Nieuwe SABnzbd-categorieën": wanted,
            "Downloadmap": [f"{n} → {d['folders'].get(n, n)}" for n in wanted],
            "Gestuurd door": [f"{m['category']}: {', '.join(m['instances'])}" for m in d["missing"]
                              if m["category"] in wanted],
            "Nabewerking": "volgt '*' (SABnzbd-standaard)",
            "Gevolg in MediaGuard": "ruimt downloads in deze categorie op volgens zijn eigen regels (niet terug te "
            "draaien door MSO)",
            "Terugdraaien": "alleen met de hand; verwijdert alleen een door MSO aangemaakte categorie, zolang geen "
            "download in de wachtrij of in nabewerking hem gebruikt",
        },
        source=SOURCE,
        actor=actor,
        dedupe_key=key,
    )
    outcomes.recommend(
        "usenet",
        title,
        explanation,
        risk="toestemming_nodig",
        subject="sabnzbd:categories",
        impact="Sonarr/Radarr zien hun SABnzbd-downloads weer: import en afhandeling van mislukte downloads",
        change_id=change["id"],
        dedupe_key=key,
        evidence={"categories": wanted, "verdict": d["verdict"]},
    )
    get_db().execute(  # the same set can come back after it was solved once
        "UPDATE recommendations SET status='open', change_id=%s, updated_at=now() WHERE dedupe_key=%s "
        "AND status='resolved'",
        (change["id"], key),
    )
    return change


def _public_proposal(p: dict[str, Any] | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {k: v for k, v in p.items() if k not in ("created_by", "dedupe_key")}


def declined_names() -> list[str]:
    st = state.get_state(DECLINED_KEY)
    return sorted((st or {}).get("names") or []) if isinstance(st, dict) else []


def _update_declined(d: dict[str, Any], actor: str, db: Any) -> set[str]:
    """Categories the owner said no to: a ``usenet.categories`` change set discarded or rolled back by someone
    other than MSO itself (audit ``change.discarded``/``change.rolled_back``). The periodic check never proposes
    them again; the admin can still make a proposal by hand. A decision lapses once the problem is gone (SABnzbd
    knows the category, or every client list was read and nobody sends it any more), so it is proposed again if
    the same problem comes back later. Kept in ``system_state`` with an audit-id watermark."""
    st = state.get_state(DECLINED_KEY)
    st = st if isinstance(st, dict) else {}
    names = set(st.get("names") or [])
    last = int(st.get("audit_id") or 0)
    rows = db.fetchall(
        "SELECT a.id, c.ops FROM audit_log a JOIN change_sets c ON c.id = a.change_id WHERE a.id > %s "
        "AND a.action IN ('change.discarded','change.rolled_back') AND c.source = %s AND a.actor <> ALL(%s) "
        "ORDER BY a.id",
        (last, SOURCE, sorted({actor, *SYSTEM_ACTORS})),
    )
    for r in rows:
        names |= set(_op_names(r["ops"]))
        last = max(last, int(r["id"]))
    sab_names = set(d["sab"]["categories"])
    missing = {m["category"] for m in d["missing"]}
    all_read = all(i.get("clients_reason") is None for i in d["instances"])
    kept = {n for n in names if n not in sab_names and (n in missing or not all_read)}
    if kept != set(st.get("names") or []) or last != int(st.get("audit_id") or 0):
        state.set_state(DECLINED_KEY, {"names": sorted(kept), "audit_id": last})
    return kept


def sync(actor: str = "systeem") -> dict[str, Any]:
    """Periodic: keep exactly one open proposal for the *certain* case (a) and retire proposals/recommendations
    that no longer apply. Nothing is applied; a missing SABnzbd/MediaGuard answer changes nothing.

    The owner's decisions win: categories whose proposal a person discarded or rolled back are not proposed again
    (:func:`_update_declined`), and a proposal an admin made himself (or one that is already approved) is never
    replaced; only MSO's own preview is replaced when the set changes. A preview is discarded once it is moot:
    MSO's own when none of its categories is missing any more, an admin's only when SABnzbd knows all of them
    (then it could not be applied anyway)."""
    d = diagnose(refresh=True)
    if not d["sab"]["available"] or not d["mediaguard"]["available"]:
        return {"skipped": "SABnzbd of MediaGuard niet beschikbaar", "verdict": d["verdict"]}
    db = get_db()
    declined = _update_declined(d, actor, db)
    certain = sorted(
        m["category"]
        for m in d["missing"]
        if m["certainty"] == "zeker" and m["creatable"] and m["category"] not in declined
    )
    missing = {m["category"] for m in d["missing"]}
    sab_names = set(d["sab"]["categories"])
    current = _open_proposal(db)
    own = current is not None and current["created_by"] in {actor, *SYSTEM_ACTORS}
    created = None
    if current and current["status"] == "preview":
        moot = (
            not any(c in missing for c in current["categories"])
            if own
            else all(c in sab_names for c in current["categories"])
        )
        if moot:
            changes.discard(current["id"], actor)
            current = None
    replaceable = own and current is not None and current["status"] == "preview"
    if certain and (current is None or (replaceable and sorted(current["categories"]) != certain)):
        if current is not None:
            changes.discard(current["id"], actor)
        try:
            created = propose(certain, actor, d=d)
        except HTTPException as exc:  # e.g. SABnzbd's policy check; try again next round
            LOGGER.warning("Voorstel SABnzbd-categorieën niet gemaakt: %s", exc.detail)
    keep = _open_proposal(db)
    db.execute(
        "UPDATE recommendations SET status='resolved', updated_at=now() WHERE status='open' AND "
        "dedupe_key LIKE %s AND dedupe_key IS DISTINCT FROM %s",
        (f"{SOURCE}:%", keep["dedupe_key"] if keep else None),
    )
    return {
        "verdict": d["verdict"],
        "missing": certain,
        "declined": sorted(declined),
        "proposal": created["id"] if created else (keep["id"] if keep else None),
    }


@jobs.handler(JOB)
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    return sync()


def _enabled() -> bool:
    return cb.is_configured("sabnzbd") and cb.is_configured("mediaguard")


jobs.every(JOB, 6 * 3600, enabled=_enabled)
