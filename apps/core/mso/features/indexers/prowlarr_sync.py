"""Prowlarr → ``sources`` sync, and the single Prowlarr write MSO may propose (change-set applier
``prowlarr``).

Sync: every Prowlarr indexer becomes ``sources.id = 'prowlarr:<id>'`` with kind by protocol, private flag from
Prowlarr's privacy, and scopes from MediaGuard's tags ``anime``/``normal`` (untagged → both, flagged).
Indexers that point back at MSO itself are recognised (``meta.is_mso``) so the search aggregator never asks
Prowlarr to query MSO (anti-loop).

Applier ``prowlarr`` paths: ``indexer:newznab`` / ``indexer:torznab`` — the MSO Newznab/Torznab registration.
Detection of an existing registration is by baseUrl (idempotent). Tags are only *used* (existing ``anime`` and
``normal`` ids on create) — never created, renamed or changed; Applications are never touched.
"""

from __future__ import annotations

import copy
import logging
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
    db = get_db()
    seen: list[str] = []
    mso_ids: list[int] = []
    pub = keys.public_base_url()
    for idx in indexers:
        iid = int(idx["id"])
        sid = f"prowlarr:{iid}"
        seen.append(sid)
        scopes, untagged = scopes_from_tags(idx.get("tags") or [], tmap)
        mso = is_mso_indexer(idx, pub)
        if mso:
            mso_ids.append(iid)
        proto = str(idx.get("protocol") or "usenet").lower()
        st = statuses.get(iid) or {}
        sx = stats.get(iid) or {}
        meta = {
            "prowlarr_id": iid,
            "name": idx.get("name"),
            "implementation": idx.get("implementation"),
            "definition": idx.get("definitionName"),
            "protocol": proto,
            "privacy": idx.get("privacy"),
            "tags": sorted({tmap.get(int(t), str(t)) for t in idx.get("tags") or []}),
            "untagged": untagged,
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
    result = sync_sources()
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


def create_payload(
    kind: str,
    client: ProwlarrClient | None,
    *,
    tag_ids: list[int],
    api_key: str,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = REGISTRATIONS[kind]
    want = desired_registration(kind)
    if template is None and client is not None:
        template = next(
            (s for s in client.indexer_schema() if s.get("implementation") == spec["implementation"]), None
        )
    body = copy.deepcopy(template) if template else {"fields": []}
    body.pop("id", None)
    body.pop("presets", None)
    body.update(
        {
            "name": want["name"],
            "implementation": spec["implementation"],
            "configContract": spec["configContract"],
            "protocol": spec["protocol"],
            "enable": True,
            "appProfileId": body.get("appProfileId") or 1,
            "priority": body.get("priority") or 25,
            "tags": tag_ids,
        }
    )
    _set_field(body, "baseUrl", want["baseUrl"])
    _set_field(body, "apiPath", "/api")
    _set_field(body, "apiKey", api_key)
    return body


def _applier() -> changes.Applier:
    client = ProwlarrClient.from_config()

    def kind_of(path: str) -> str:
        kind = path.split(":", 1)[1] if path.startswith("indexer:") else ""
        if kind not in REGISTRATIONS:
            raise ValueError(f"MSO mag in Prowlarr alleen zijn eigen indexer registreren, niet '{path}'")
        return kind

    def read(path: str) -> Any:
        found = find_registrations(client.indexers(), kind_of(path))
        return _normalize(found[0]) if found else None

    def write(path: str, value: Any) -> None:
        kind = kind_of(path)
        found = find_registrations(client.indexers(), kind)
        if value is None:  # rollback of a registration MSO created
            for rec in found:
                if str(rec.get("name") or "").startswith(MSO_INDEXER_PREFIX):
                    client.delete_indexer(int(rec["id"]))
            return
        key = keys.default_key()
        if found:
            body = copy.deepcopy(found[0])
            body["name"] = value["name"]
            body["enable"] = value["enable"]
            _set_field(body, "baseUrl", value["baseUrl"])
            _set_field(body, "apiPath", value["apiPath"])
            _set_field(body, "apiKey", key)
            client.update_indexer(body)  # tags untouched (MediaGuard may manage them)
            return
        client.create_indexer(create_payload(kind, client, tag_ids=_scope_tag_ids(client), api_key=key))

    def validate(path: str, value: Any) -> None:
        kind_of(path)
        if value is None:
            return
        if not isinstance(value, dict) or set(value) != {
            "name",
            "implementation",
            "baseUrl",
            "apiPath",
            "enable",
        }:
            raise ValueError("ongeldige registratie")
        if not str(value["name"]).startswith(MSO_INDEXER_PREFIX):
            raise ValueError("alleen de eigen MSO-indexer mag worden geregistreerd")
        pub = keys.public_base_url()
        if pub and not norm_url(value["baseUrl"]).startswith(norm_url(pub)):
            raise ValueError("baseUrl wijst niet naar MSO")

    return changes.Applier(read=read, write=write, validate=validate)


changes.register_applier(PROWLARR_ID, _applier)


def mask_payload(body: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(body)
    for f in out.get("fields") or []:
        if f.get("name") == "apiKey":
            f["value"] = "*** (consumer-sleutel 'Prowlarr' van MSO, ingevuld bij toepassen)"
    return out


def propose_registration(actor: str, kinds: list[str] | None = None) -> dict[str, Any]:
    """Preview (change set, risk toestemming_nodig) registering MSO's endpoints in Prowlarr. Idempotent: an
    existing registration (matched by baseUrl) is reported, never duplicated."""
    kinds = kinds or ["newznab", "torznab"]
    client = ProwlarrClient.from_config()
    indexers = client.indexers()
    keys.ensure_default_consumer()
    tag_ids = _scope_tag_ids(client)
    schema = client.indexer_schema()
    ops, payloads, already = [], {}, []
    for kind in kinds:
        if kind not in REGISTRATIONS:
            raise HTTPException(422, detail=f"Onbekend type '{kind}'")
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
        template = next(
            (s for s in schema if s.get("implementation") == REGISTRATIONS[kind]["implementation"]), None
        )
        payloads[kind] = mask_payload(
            create_payload(kind, None, tag_ids=tag_ids, api_key="x", template=template)
            if cur is None
            else {**found[0], "fields": found[0].get("fields")}
        )
        ops.append(
            {
                "path": f"indexer:{kind}",
                "label": f"Prowlarr-indexer '{want['name']}'"
                + (" aanmaken" if cur is None else " bijwerken"),
                "before": cur,
                "after": want,
            }
        )
    if not ops:
        return {"status": "already_registered", "kinds": already}
    tag_labels = sorted(tag_map(client.tags()).get(i, str(i)) for i in tag_ids)
    explanation = (
        "MSO registreert alleen zijn eigen Newznab/Torznab-endpoint als indexer in Prowlarr. "
        "Bestaande tags worden alleen gebruikt (" + (", ".join(tag_labels) or "geen") + "), nooit "
        "aangemaakt of gewijzigd; Applications en MediaGuard-instellingen blijven onaangeroerd. "
        "Verzoeken die Prowlarr via deze indexer doet, bevraagt MSO nooit terug bij Prowlarr."
    )
    ch = changes.create(
        PROWLARR_ID,
        "MSO als indexer registreren in Prowlarr",
        ops,
        risk="toestemming_nodig",
        explanation=explanation,
        impact={"payloads": payloads, "already": already, "tags_used": tag_labels},
        actor=actor,
        dedupe_key="prowlarr:register-mso",
    )
    return {"status": "preview", "change": ch, "already": already}
