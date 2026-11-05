"""Prowlarr → ``sources`` sync, and the single Prowlarr write MSO may propose (change-set applier
``prowlarr``).

Sync: every Prowlarr indexer becomes ``sources.id = 'prowlarr:<id>'`` with kind by protocol, private flag from
Prowlarr's privacy, and scopes from MediaGuard's tags ``anime``/``normal``. Untagged indexers get scopes
derived from their Prowlarr category capabilities (``meta.scope_source`` tag|derived|default); indexers
without any TV/Movies category are marked ``library_relevant=false``, and indexers that Prowlarr's health
check names as not working (no definition / unavailable due to failures) get ``prowlarr_broken=true`` —
both are skipped by the search aggregator by default.
Indexers that point back at MSO itself are recognised (``meta.is_mso``) so the search aggregator never asks
Prowlarr to query MSO (anti-loop).

Applier ``prowlarr`` paths: ``indexer:newznab`` / ``indexer:torznab`` — the MSO Newznab/Torznab registration.
Detection of an existing registration is by baseUrl (idempotent). Tags are only *used* (existing ``anime`` and
``normal`` ids on create) — never created, renamed or changed; Applications are never touched.
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from mso import changes, jobs
from mso.connectors import base as cb
from mso.connectors.prowlarr import (
    MSO_INDEXER_PREFIX,
    PROWLARR_ID,
    ProwlarrClient,
    ProwlarrError,
    field_value,
    norm_url,
)
from mso.db import get_db, jsonb
from mso.domain import categories as C
from mso.domain import outcomes
from mso.features.newznab_server import keys
from mso.features.search import settings as S

LOGGER = logging.getLogger(__name__)
ANIME_HINTS = (
    "anime",
    "nyaa",
    "tosho",
    "animebytes",
    "anidex",
    "tokyotosho",
    "erai",
    "subsplease",
    "shana",
    "bakabt",
    "anirena",
)  # mirrors MediaGuard provisioning/templates.py ANIME_HINTS
MG_APP_PREFIX = "MediaGuard · "
REGISTRATIONS = {
    "newznab": {
        "implementation": "Newznab",
        "configContract": "NewznabSettings",
        "protocol": "usenet",
        "path": "/newznab",
        "label": "Newznab",
    },
    "torznab": {
        "implementation": "Torznab",
        "configContract": "TorznabSettings",
        "protocol": "torrent",
        "path": "/torznab",
        "label": "Torznab",
    },
}


def is_anime_name(*texts: Any) -> bool:
    blob = " ".join(str(t or "") for t in texts).lower()
    return any(h in blob for h in ANIME_HINTS)


def tag_map(tags: list[dict[str, Any]]) -> dict[int, str]:
    return {int(t["id"]): str(t.get("label", "")).lower() for t in tags if t.get("id") is not None}


def scopes_from_tags(tag_ids: list[int], tmap: dict[int, str]) -> tuple[list[str], bool]:
    labels = {tmap.get(int(t), "") for t in tag_ids or []}
    scopes = [s for s in C.SCOPE_TAGS if s in labels]
    if not scopes:
        return ["normal", "anime"], True
    return scopes, False


def capability_categories(idx: dict[str, Any]) -> list[tuple[int, str]]:
    """All (id, name) Newznab categories an indexer declares in Prowlarr (``capabilities.categories``,
    including sub-categories). Empty when Prowlarr did not report capabilities."""
    out: list[tuple[int, str]] = []

    def walk(items: Any) -> None:
        for c in items or []:
            if not isinstance(c, dict):
                continue
            try:
                out.append((int(c.get("id")), str(c.get("name") or "")))
            except (TypeError, ValueError):
                pass
            walk(c.get("subCategories"))

    walk((idx.get("capabilities") or {}).get("categories"))
    return out


def derive_scope(idx: dict[str, Any], tmap: dict[int, str]) -> dict[str, Any]:
    """Scopes of a Prowlarr indexer and where they come from.

    * tagged ``anime``/``normal`` (MediaGuard) → those scopes, ``scope_source='tag'`` (tags always win);
    * untagged with category capabilities → ``'derived'``: only TV/Anime (5070) as video category → anime,
      otherwise both scopes;
    * untagged without capabilities → both (``'default'``).

    ``library_relevant`` is False when Prowlarr reports categories but none under TV (5xxx) or Movies (2xxx)
    — e.g. XXX-, console- or book-only indexers: they never serve a MediaGuard library category.
    """
    scopes, untagged = scopes_from_tags(idx.get("tags") or [], tmap)
    cats = capability_categories(idx)
    ids = {c for c, _ in cats}
    tv = {c for c in ids if 5000 <= c < 6000}
    movies = {c for c in ids if 2000 <= c < 3000}
    relevant: bool | None = None
    reason = None
    if cats:
        relevant = bool(tv or movies)
        if not relevant:
            tops = sorted({name.split("/")[0].strip() for c, name in cats if c < 100000 and name} or {"?"})
            reason = "Alleen categorieën " + ", ".join(tops[:6]) + " — geen series of films"
    # Prowlarr lists the parent (e.g. 5000 TV) of every mapped sub-category: judge "anime only" on leaves
    leaves: set[int] = set()
    for top in (idx.get("capabilities") or {}).get("categories") or []:
        if not isinstance(top, dict):
            continue
        subs = [c.get("id") for c in top.get("subCategories") or [] if isinstance(c, dict)]
        for c in subs or [top.get("id")]:
            try:
                leaves.add(int(c))
            except (TypeError, ValueError):
                pass
    tv_leaves = {c for c in leaves if 5000 <= c < 6000}
    source = "tag"
    if untagged:
        source = "derived" if cats else "default"
        if cats and tv_leaves and tv_leaves <= {C.TV_ANIME} and not movies:
            scopes = ["anime"]
    return {
        "scopes": scopes,
        "untagged": untagged,
        "scope_source": source,
        "library_relevant": relevant,
        "relevance_reason": reason,
        "video_categories": sorted(tv | movies),
    }


# Prowlarr health checks that name indexers (message "...: Name A, Name B. Please ...").
_HEALTH_KINDS = (
    ("no definition", "no_definition", True),
    ("obsolete", "no_definition", True),
    ("unavailable due to failures", "failing", True),
    ("vip", "vip", False),
    ("invalid download client", "download_client", False),
)


def parse_health(health: list[dict[str, Any]], names: dict[int, str]) -> dict[int, list[dict[str, Any]]]:
    """Map Prowlarr ``/api/v1/health`` messages to indexer ids. ``broken`` = Prowlarr says the indexer does
    not work (no definition / unavailable due to failures) → MSO shows a warning and skips it in searches.
    Proxy/application/download-client checks that do not concern indexers are ignored."""
    out: dict[int, list[dict[str, Any]]] = {}
    by_name = {str(n).strip().lower(): i for i, n in names.items() if n}
    for h in health or []:
        if not isinstance(h, dict):
            continue
        src = str(h.get("source") or "")
        msg = str(h.get("message") or "")
        low = msg.lower()
        if "proxy" in src.lower() or "application" in src.lower() or "indexer" not in low:
            continue
        kind = next(((k, b) for needle, k, b in _HEALTH_KINDS if needle in low), None)
        if kind is None:
            continue
        hit: set[int] = set()
        if low.startswith("all indexers"):
            hit = set(names)
        elif ":" in msg:
            listed = msg.split(":", 1)[1]
            listed = re.sub(r"\.\s+[A-Z].*$", "", listed.strip(), flags=re.S).rstrip(". ")
            items = {x.strip().lower() for x in listed.split(",") if x.strip()}
            hit = {by_name[x] for x in items if x in by_name}
            if not hit:  # names containing ', ' or odd punctuation: fall back to containment
                hit = {i for n, i in by_name.items() if n and n in listed.lower()}
        for iid in hit:
            out.setdefault(iid, []).append(
                {"kind": kind[0], "broken": kind[1], "message": msg[:400], "source": src}
            )
    return out


def is_mso_indexer(idx: dict[str, Any], public_base: str | None = None) -> bool:
    """Is this Prowlarr indexer MSO's own Newznab/Torznab endpoint? (name, or baseUrl → MSO path)."""
    if str(idx.get("name") or "").startswith(MSO_INDEXER_PREFIX):
        return True
    base = norm_url(field_value(idx, "baseUrl"))
    if not base:
        return False
    pub = norm_url(public_base if public_base is not None else keys.public_base_url())
    if pub and (base == pub + "/newznab" or base == pub + "/torznab" or base.startswith(pub + "/")):
        return True
    path = urlsplit(base).path
    if path.endswith("/newznab") or path.endswith("/torznab"):
        if not pub:
            return True  # conservative: unknown public URL, MSO-shaped path → treat as MSO (never query it)
        return (urlsplit(base).hostname or "") == (urlsplit(pub).hostname or "")
    return False


def sync_sources() -> dict[str, Any]:
    client = ProwlarrClient.from_config()
    indexers = client.indexers()
    tmap = tag_map(client.tags())
    try:
        statuses = {int(s["indexerId"]): s for s in client.indexer_status() if s.get("indexerId") is not None}
    except ProwlarrError:
        statuses = {}
    try:
        stats = {
            int(s["indexerId"]): s
            for s in (client.indexer_stats() or {}).get("indexers", [])
            if s.get("indexerId") is not None
        }
    except ProwlarrError:
        stats = {}
    try:
        health = parse_health(
            client.health(), {int(i["id"]): str(i.get("name") or "") for i in indexers if "id" in i}
        )
    except ProwlarrError:
        health = {}
    db = get_db()
    seen: list[str] = []
    mso_ids: list[int] = []
    pub = keys.public_base_url()
    prev_broken = {
        r["id"]: r["since"]
        for r in db.fetchall(
            "SELECT id, meta->>'prowlarr_broken_since' AS since FROM sources WHERE origin='prowlarr'"
        )
    }
    now_iso = datetime.now(UTC).isoformat()
    for idx in indexers:
        iid = int(idx["id"])
        sid = f"prowlarr:{iid}"
        seen.append(sid)
        scope = derive_scope(idx, tmap)
        scopes, untagged = scope["scopes"], scope["untagged"]
        issues = health.get(iid, [])
        mso = is_mso_indexer(idx, pub)
        if mso:
            mso_ids.append(iid)
        proto = str(idx.get("protocol") or "usenet").lower()
        st = statuses.get(iid) or {}
        sx = stats.get(iid) or {}
        meta = {
            "prowlarr_id": iid,
            "removed": False,
            "name": idx.get("name"),
            "implementation": idx.get("implementation"),
            "definition": idx.get("definitionName"),
            "protocol": proto,
            "privacy": idx.get("privacy"),
            "tags": sorted({tmap.get(int(t), str(t)) for t in idx.get("tags") or []}),
            "untagged": untagged,
            "scope_source": scope["scope_source"],
            "library_relevant": scope["library_relevant"],
            "relevance_reason": scope["relevance_reason"],
            "video_categories": scope["video_categories"],
            "prowlarr_health": issues,
            "prowlarr_broken": any(i["broken"] for i in issues),
            # first sync that saw the "not working" health message (kept while it lasts; None once gone), so
            # the UI can tell results from *before* the breakage from results despite it
            "prowlarr_broken_since": (prev_broken.get(sid) or now_iso)
            if any(i["broken"] for i in issues)
            else None,
            "is_mso": mso,
            "anime_name": is_anime_name(idx.get("name"), idx.get("definitionName")),
            "disabled_till": st.get("disabledTill"),
            "most_recent_failure": st.get("mostRecentFailure"),
            "avg_response_ms": sx.get("averageResponseTime"),
            "queries": sx.get("numberOfQueries"),
            "grabs": sx.get("numberOfGrabs"),
            "failed_queries": sx.get("numberOfFailedQueries"),
            "failed_grabs": sx.get("numberOfFailedGrabs"),
            "priority": idx.get("priority"),
        }
        outcomes.upsert_source(
            sid,
            "torrent_indexer" if proto == "torrent" else "nzb_indexer",
            str(idx.get("name") or sid),
            "prowlarr",
            scopes=scopes,
            private=str(idx.get("privacy") or "").lower() in ("private", "semiprivate"),
            enabled=bool(idx.get("enable", True)) and not mso,
            meta=meta,
            db=db,
        )
        if sx.get("averageResponseTime") is not None:
            outcomes.observe(sid, "prowlarr.avg_response_ms", float(sx["averageResponseTime"]), db=db)
    # indexers removed from Prowlarr: keep history, mark disabled
    db.execute(
        "UPDATE sources SET enabled=false, meta = meta || '{\"removed\": true}'::jsonb, updated_at=now() "
        "WHERE origin='prowlarr' AND NOT (id = ANY(%s))",
        (seen,),
    )
    db.execute(
        "INSERT INTO system_state(key,value,updated_at) VALUES ('prowlarr.mso_indexer_ids',%s,now()) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
        (jsonb(mso_ids),),
    )
    return {"indexers": len(indexers), "mso_indexer_ids": mso_ids}


def mso_indexer_ids() -> set[int]:
    row = get_db().fetchone("SELECT value FROM system_state WHERE key='prowlarr.mso_indexer_ids'")
    ids = set(int(i) for i in (row["value"] if row else []) or [])
    for raw in S.get("search.prowlarr_excluded_indexer_ids", []) or []:
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


@jobs.handler("prowlarr.sync")
def _sync_job(ctx: jobs.JobContext) -> dict[str, Any]:
    if not cb.is_configured(PROWLARR_ID):
        return {"skipped": "niet ingesteld"}
    last = get_db().fetchone("SELECT updated_at FROM system_state WHERE key='prowlarr.mso_indexer_ids'")
    interval = timedelta(minutes=int(S.get("prowlarr.sync_interval_min", 15)))
    if (
        not ctx.payload.get("force")
        and last
        and last["updated_at"] > datetime.now(UTC) - interval + timedelta(seconds=30)
    ):
        return {"skipped": "recent gesynchroniseerd"}
    try:
        result = sync_sources()
    except ProwlarrError as exc:
        if exc.code == "not_configured":  # URL saved before the key (setup in progress): nothing to do yet
            return {"skipped": exc.message}
        raise
    try:
        from mso.features.indexers import prowlarr_view

        result["conflicts"] = len(prowlarr_view.refresh_recommendations())
    except ProwlarrError as exc:
        result["view_error"] = exc.message
    return result


jobs.every("prowlarr.sync", 300, enabled=lambda: cb.is_configured(PROWLARR_ID))  # interval: setting


# ------------------------------------------------------------------ registration (the one write)
def _scope_tag_ids(client: ProwlarrClient) -> list[int]:
    tmap = tag_map(client.tags())
    return sorted(i for i, label in tmap.items() if label in C.SCOPE_TAGS)


def desired_registration(kind: str, *, public_base: str | None = None) -> dict[str, Any]:
    spec = REGISTRATIONS[kind]
    pub = (public_base if public_base is not None else keys.public_base_url()).rstrip("/")
    if not pub:
        raise HTTPException(
            422,
            detail="Stel eerst de publieke URL van MSO in (Instellingen → indexers → "
            "'Publieke URL van MSO') zodat Prowlarr MSO kan bereiken",
        )
    return {
        "name": f"{MSO_INDEXER_PREFIX} ({spec['label']})",
        "implementation": spec["implementation"],
        "baseUrl": pub + spec["path"],
        "apiPath": "/api",
        "enable": True,
    }


REG_KEYS = ("name", "implementation", "baseUrl", "apiPath", "enable")
SUMMARY_LINES = (
    ("name", "Naam"),
    ("implementation", "Type"),
    ("baseUrl", "URL"),
    ("apiPath", "API-pad"),
    ("enable", "Ingeschakeld"),
)
# Prowlarr's schema entries are *definitions* (presets such as abNZB or AnimeTosho plus "Generic …"); these keys
# only describe the definition and must never be sent back as if they were MSO's own values.
DEFINITION_ONLY = ("id", "presets", "added", "sortName", "description", "infoLink", "legacyUrls", "message")
NO_RESULTS_HINT = "no results were returned from your indexer"
CAT_NAMES_NL = {"Movies": "Films", "TV": "Series"}


def _normalize(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if rec is None:
        return None
    return {
        "name": rec.get("name"),
        "implementation": rec.get("implementation"),
        "baseUrl": str(field_value(rec, "baseUrl") or "").rstrip("/"),
        "apiPath": field_value(rec, "apiPath") or "/api",
        "enable": bool(rec.get("enable")),
    }


def summary(reg: dict[str, Any] | None) -> str | None:
    """Change-set op value for a registration: compact Dutch lines (what the preview shows and what read()
    returns), e.g. ``Naam: Media Source Optimizer (Newznab)\\nType: Newznab\\nURL: …``."""
    if reg is None:
        return None
    out = []
    for key, label in SUMMARY_LINES:
        v = reg.get(key)
        out.append(f"{label}: {('ja' if v else 'nee') if key == 'enable' else v}")
    return "\n".join(out)


def parse_registration(value: Any) -> dict[str, Any]:
    """Inverse of :func:`summary`; also accepts the older dict form of open change sets."""
    if isinstance(value, dict):
        if set(value) != set(REG_KEYS):
            raise ValueError("ongeldige registratie")
        return dict(value)
    if not isinstance(value, str):
        raise ValueError("ongeldige registratie")
    by_label = {label: key for key, label in SUMMARY_LINES}
    out: dict[str, Any] = {}
    for line in value.splitlines():
        label, sep, v = line.partition(": ")
        if not sep or label not in by_label:
            raise ValueError("ongeldige registratie")
        key = by_label[label]
        out[key] = (v.strip() == "ja") if key == "enable" else v.strip()
    if set(out) != set(REG_KEYS):
        raise ValueError("ongeldige registratie")
    return out


def find_registrations(
    indexers: list[dict[str, Any]], kind: str, public_base: str | None = None
) -> list[dict[str, Any]]:
    spec = REGISTRATIONS[kind]
    pub = norm_url(public_base if public_base is not None else keys.public_base_url())
    target = pub + spec["path"] if pub else None
    out = []
    for idx in indexers:
        if idx.get("implementation") != spec["implementation"]:
            continue
        base = norm_url(field_value(idx, "baseUrl"))
        if (target and base == target) or str(
            idx.get("name") or ""
        ) == f"{MSO_INDEXER_PREFIX} ({spec['label']})":
            out.append(idx)
    return out


def _set_field(body: dict[str, Any], name: str, value: Any) -> None:
    for f in body.setdefault("fields", []):
        if f.get("name") == name:
            f["value"] = value
            return
    body["fields"].append({"name": name, "value": value})


def pick_template(schema: list[dict[str, Any]], implementation: str) -> dict[str, Any] | None:
    """The *generic* Newznab/Torznab definition from ``GET /api/v1/indexer/schema``. Prowlarr lists presets
    first (abNZB, AnimeTosho, …, with their own URL, description and categories); copying one of those would
    send another indexer's identity (e.g. ``sortName: animetosho``). Prefer "Generic …", then any entry
    without a preset baseUrl, else the first one."""
    cands = [s for s in schema if s.get("implementation") == implementation]

    def generic(s: dict[str, Any]) -> bool:
        return str(s.get("name") or "").strip().lower().startswith("generic")

    def no_preset_url(s: dict[str, Any]) -> bool:
        return not str(field_value(s, "baseUrl") or "").strip()

    for pred in (generic, no_preset_url):
        for s in cands:
            if pred(s):
                return s
    return cands[0] if cands else None


def create_payload(
    kind: str,
    client: ProwlarrClient | None,
    *,
    tag_ids: list[int],
    api_key: str,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Body for ``POST /api/v1/indexer``: Prowlarr's generic schema template with MSO's own values filled in
    (name, implementation, configContract, protocol, baseUrl, apiPath, apiKey, existing scope tags)."""
    spec = REGISTRATIONS[kind]
    want = desired_registration(kind)
    if template is None and client is not None:
        template = pick_template(client.indexer_schema(), spec["implementation"])
    body = copy.deepcopy(template) if template else {"fields": []}
    for k in DEFINITION_ONLY:
        body.pop(k, None)
    preset = bool(str(field_value(body, "baseUrl") or "").strip())
    body.update(
        {
            "name": want["name"],
            "implementation": spec["implementation"],
            "implementationName": spec["implementation"],
            "configContract": spec["configContract"],
            "protocol": spec["protocol"],
            "enable": True,
            "appProfileId": body.get("appProfileId") or 1,
            "priority": body.get("priority") or 25,
            "tags": tag_ids,
        }
    )
    if preset:  # fallback template was a preset: never carry its URLs/parameters over
        body["indexerUrls"] = []
        if any(f.get("name") == "additionalParameters" for f in body.get("fields") or []):
            _set_field(body, "additionalParameters", "")
    _set_field(body, "baseUrl", want["baseUrl"])
    _set_field(body, "apiPath", "/api")
    _set_field(body, "apiKey", api_key)
    return body


def _readiness(kind: str) -> dict[str, Any]:
    from mso.features.newznab_server import readiness

    return readiness.endpoint_sources(kind)


def _translate_prowlarr_refusal(exc: ProwlarrError, kind: str) -> ProwlarrError:
    if NO_RESULTS_HINT not in exc.message.lower():
        return exc
    labels = ", ".join(s["label"] for s in _readiness(kind)["sources"]) or "geen"
    return ProwlarrError(
        f"Prowlarr weigert de {REGISTRATIONS[kind]['label']}-indexer: zijn testzoekopdracht (zonder zoekterm) "
        f"kreeg van MSO 0 resultaten. Eigen bronnen achter het endpoint: {labels}. Controleer of die bereikbaar "
        f"zijn en recente releases geven (Bronrapport bij Zoeken). Prowlarr meldde: {exc.message}",
        exc.code,
        exc.status,
    )


def _applier() -> changes.Applier:
    client = ProwlarrClient.from_config()

    def kind_of(path: str) -> str:
        kind = path.split(":", 1)[1] if path.startswith("indexer:") else ""
        if kind not in REGISTRATIONS:
            raise ValueError(f"MSO mag in Prowlarr alleen zijn eigen indexer registreren, niet '{path}'")
        return kind

    def read(path: str) -> Any:
        found = find_registrations(client.indexers(), kind_of(path))
        return summary(_normalize(found[0])) if found else None

    def write(path: str, value: Any) -> None:
        kind = kind_of(path)
        found = find_registrations(client.indexers(), kind)
        if value is None:  # rollback of a registration MSO created
            for rec in found:
                if str(rec.get("name") or "").startswith(MSO_INDEXER_PREFIX):
                    client.delete_indexer(int(rec["id"]))
            return
        reg = parse_registration(value)
        key = keys.default_key()
        try:
            if found:
                body = copy.deepcopy(found[0])
                body["name"] = reg["name"]
                body["enable"] = reg["enable"]
                _set_field(body, "baseUrl", reg["baseUrl"])
                _set_field(body, "apiPath", reg["apiPath"])
                _set_field(body, "apiKey", key)
                client.update_indexer(body)  # tags untouched (MediaGuard may manage them)
                return
            client.create_indexer(create_payload(kind, client, tag_ids=_scope_tag_ids(client), api_key=key))
        except ProwlarrError as exc:
            raise _translate_prowlarr_refusal(exc, kind) from exc

    def validate(path: str, value: Any) -> None:
        kind = kind_of(path)
        if value is None:
            return
        reg = parse_registration(value)
        if not str(reg["name"]).startswith(MSO_INDEXER_PREFIX):
            raise ValueError("alleen de eigen MSO-indexer mag worden geregistreerd")
        pub = keys.public_base_url()
        if pub and not norm_url(reg["baseUrl"]).startswith(norm_url(pub)):
            raise ValueError("baseUrl wijst niet naar MSO")
        if reg["enable"]:
            ready = _readiness(kind)
            if not ready["ready"]:
                raise ValueError(ready["reason"])

    return changes.Applier(read=read, write=write, validate=validate)


changes.register_applier(PROWLARR_ID, _applier)


def mask_payload(body: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(body)
    for f in out.get("fields") or []:
        if f.get("name") == "apiKey":
            f["value"] = "*** (consumer-sleutel 'Prowlarr' van MSO, ingevuld bij toepassen)"
    return out


def _categories_text() -> str:
    from mso.features.newznab_server.xmlout import CATEGORY_TREE

    parts = [f"{CAT_NAMES_NL.get(name, name)} ({cid})" for cid, name, _ in CATEGORY_TREE]
    return ", ".join(parts) + " incl. Anime (5070) — Prowlarr leest ze uit de caps van MSO"


def _impact_row(
    reg: dict[str, Any], body: dict[str, Any], tag_labels: list[str], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    proto = "usenet" if reg["implementation"] == "Newznab" else "torrents"
    return {
        "Naam": reg["name"],
        "Type": f"{reg['implementation']} ({proto})",
        "URL": f"{reg['baseUrl']}{reg['apiPath']}",
        "Categorieën": _categories_text(),
        "Prioriteit": body.get("priority") or 25,
        "Tags": ", ".join(tag_labels) or "geen",
        "Bronnen achter het endpoint": ", ".join(s["label"] for s in sources) or "geen",
        "API-sleutel": "consumer-sleutel 'Prowlarr' van MSO (pas bij toepassen ingevuld)",
    }


def _refusal_detail(reasons: dict[str, str]) -> str:
    from mso.features.newznab_server import readiness

    if len(reasons) == 1:
        return next(iter(reasons.values()))
    if all(r.startswith(readiness.NO_BACKENDS[k][:60]) for k, r in reasons.items()):
        return readiness.NO_BACKENDS["newznab"]
    return " ".join(f"{REGISTRATIONS[k]['label']}: {r}" for k, r in reasons.items())


def propose_registration(actor: str, kinds: list[str] | None = None) -> dict[str, Any]:
    """Preview (change set, risk toestemming_nodig) registering MSO's endpoints in Prowlarr. Idempotent: an
    existing registration (matched by baseUrl) is reported, never duplicated. Refused per protocol when MSO has
    no own source behind that endpoint (Prowlarr's add-indexer test would only get 0 results)."""
    kinds = kinds or ["newznab", "torznab"]
    for kind in kinds:
        if kind not in REGISTRATIONS:
            raise HTTPException(422, detail=f"Onbekend type '{kind}'")
        desired_registration(kind)  # public URL first
    client = ProwlarrClient.from_config()
    indexers = client.indexers()
    keys.ensure_default_consumer()
    tag_ids = _scope_tag_ids(client)
    tag_labels = sorted(tag_map(client.tags()).get(i, str(i)) for i in tag_ids)
    schema: list[dict[str, Any]] | None = None
    ops, payloads, already, impact_rows = [], {}, [], {}
    not_ready: dict[str, str] = {}
    for kind in kinds:
        want = desired_registration(kind)
        found = find_registrations(indexers, kind)
        if len(found) > 1:
            raise HTTPException(
                409,
                detail=f"MSO staat {len(found)}× als {REGISTRATIONS[kind]['label']}-indexer "
                "in Prowlarr; los de dubbele registratie eerst op (zie conflicten)",
            )
        cur = _normalize(found[0]) if found else None
        if cur == want:
            already.append(kind)
            continue
        ready = _readiness(kind)
        if not ready["ready"]:
            not_ready[kind] = ready["reason"]
            continue
        if cur is None:
            if schema is None:
                schema = client.indexer_schema()
            template = pick_template(schema, REGISTRATIONS[kind]["implementation"])
            body = create_payload(kind, None, tag_ids=tag_ids, api_key="x", template=template)
        else:
            body = {**found[0], "fields": found[0].get("fields")}
        payloads[kind] = mask_payload(body)
        label = REGISTRATIONS[kind]["label"]
        impact_rows[label] = _impact_row(want, body, tag_labels, ready["sources"])
        ops.append(
            {
                "path": f"indexer:{kind}",
                "label": f"{label}-indexer in Prowlarr " + ("aanmaken" if cur is None else "bijwerken"),
                "before": summary(cur),
                "after": summary(want),
            }
        )
    if not ops:
        if not_ready and not already:
            raise HTTPException(422, detail=_refusal_detail(not_ready))
        return {"status": "already_registered", "kinds": already, "not_ready": not_ready}
    explanation = (
        "MSO registreert alleen zijn eigen Newznab/Torznab-endpoint als indexer in Prowlarr. "
        "Bestaande tags worden alleen gebruikt (" + (", ".join(tag_labels) or "geen") + "), nooit "
        "aangemaakt of gewijzigd; Applications en MediaGuard-instellingen blijven onaangeroerd. "
        "Verzoeken die Prowlarr via deze indexer doet, bevraagt MSO nooit terug bij Prowlarr; ze worden "
        "beantwoord door MSO's eigen bronnen. Prowlarr test een nieuwe indexer met een zoekopdracht zonder "
        "zoekterm; MSO antwoordt daarop met de nieuwste echte releases van die bronnen."
    )
    impact: dict[str, Any] = {"Te registreren": impact_rows}
    if already:
        impact["Al aanwezig"] = [REGISTRATIONS[k]["label"] for k in already]
    if not_ready:
        impact["Niet geregistreerd"] = {REGISTRATIONS[k]["label"]: r for k, r in not_ready.items()}
    ch = changes.create(
        PROWLARR_ID,
        "MSO als indexer registreren in Prowlarr",
        ops,
        risk="toestemming_nodig",
        explanation=explanation,
        impact=impact,
        actor=actor,
        dedupe_key="prowlarr:register-mso:" + ",".join(sorted(op["path"] for op in ops)),
    )
    return {
        "status": "preview",
        "change": ch,
        "already": already,
        "not_ready": not_ready,
        "payloads": payloads,
    }
