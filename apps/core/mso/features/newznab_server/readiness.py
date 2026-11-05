"""Which of MSO's own sources stand behind the Newznab/Torznab endpoint, per protocol.

Prowlarr validates a newly added Newznab/Torznab indexer with a text-less test query (``t=search``, optionally
with ``cat``) and refuses the indexer when that returns nothing ("Query successful, but no results were
returned from your indexer"). MSO never asks Prowlarr back for requests that come from Prowlarr (loop
protection), so registering MSO only makes sense when MSO has *its own* sources for that protocol that can
answer such a query: an enabled direct Newznab/Torznab indexer or Spotweb (which answer RSS-style queries),
or a pluggable backend that declares ``rss=True``. Nothing here synthesises results.
"""

from __future__ import annotations

from typing import Any

from mso.features.indexers import registry
from mso.features.search import settings as S
from mso.features.search.backends import BACKENDS, Backend

PROTOCOL = {"newznab": "usenet", "torznab": "torrent"}
LABEL = {"newznab": "Newznab", "torznab": "Torznab"}

NO_BACKENDS = {
    "newznab": (
        "MSO heeft nog geen eigen bronnen achter dit endpoint. Prowlarr zou alleen zijn eigen resultaten "
        "terugkrijgen, dus registreren heeft (nog) geen zin. Voeg eerst een directe indexer of Spotweb toe "
        "of zet de header-index aan."
    ),
    "torznab": (
        "MSO heeft nog geen eigen bronnen achter dit endpoint. Prowlarr zou alleen zijn eigen resultaten "
        "terugkrijgen, dus registreren heeft (nog) geen zin. Voeg eerst een directe Torznab-indexer toe."
    ),
}
ONLY_NON_RSS = (
    "Achter dit endpoint staat alleen {labels}. Die geeft zonder zoekterm (nog) geen nieuwste releases, "
    "terwijl Prowlarr een nieuwe indexer juist met zo'n zoekopdracht test; registreren zou dus mislukken. "
    "Voeg eerst een directe indexer of Spotweb toe."
)
EXCLUDED_BY_SETTING = (
    " ({n} eigen bron(nen) staan uit voor het endpoint in Instellingen → indexers → "
    "'Bronnen voor het Newznab/Torznab-endpoint'.)"
)


def _backend_active(b: Backend) -> bool:
    if b.active is not None:
        return b.is_active()
    if b.kind == "headerindex":  # Slice B's opt-in header index registers without an `active` callback
        try:
            from mso.features.usenet.headerindex import engine

            return bool(engine.enabled())
        except Exception:
            return False
    return True


def endpoint_sources(kind: str) -> dict[str, Any]:
    """``{kind, protocol, ready, reason, sources:[{source_id,label,kind,rss}]}`` for 'newznab' | 'torznab'.

    ``ready`` = at least one enabled own source (never Prowlarr) for this protocol that answers a text-less
    (RSS-style) query, so Prowlarr's add-indexer validation can pass with real results."""
    proto = PROTOCOL[kind]
    allowed = {k for k in (S.get("newznab.backends", ["direct", "spotweb", "headerindex"]) or [])}
    allowed.discard("prowlarr")
    found: list[dict[str, Any]] = []
    excluded = 0
    for row in registry.list_rows(enabled_only=True):
        if registry.protocol_of(row) != proto:
            continue
        k = "spotweb" if row["kind"] == "spotweb" else "direct"
        if k not in allowed:
            excluded += 1
            continue
        found.append({"source_id": registry.source_id(row), "label": row["label"], "kind": k, "rss": True})
    for b in list(BACKENDS.values()):
        if proto not in b.protocols or b.kind == "prowlarr" or not _backend_active(b):
            continue
        if b.kind not in allowed:
            excluded += 1
            continue
        found.append({"source_id": b.source_id, "label": b.label, "kind": b.kind, "rss": bool(b.rss)})
    ready = any(s["rss"] for s in found)
    reason = None
    if not ready:
        if found:
            reason = ONLY_NON_RSS.format(labels=", ".join(s["label"] for s in found))
        else:
            reason = NO_BACKENDS[kind]
        if excluded:
            reason += EXCLUDED_BY_SETTING.format(n=excluded)
    return {"kind": kind, "protocol": proto, "ready": ready, "reason": reason, "sources": found}


def all_endpoint_sources() -> dict[str, dict[str, Any]]:
    return {k: endpoint_sources(k) for k in PROTOCOL}
