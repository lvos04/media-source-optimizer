"""Origin graph and anti-loop protection.

Every search carries a :class:`Trace` (``trace_id``, ``hop_count``, ``visited`` node ids such as ``mso``,
``prowlarr``, ``prowlarr:12``). The graph of who-may-query-whom is an explicit, directed graph built from:

* configuration: ``mso → prowlarr`` (Prowlarr connector), ``prowlarr → prowlarr:<id>`` (its indexers),
  ``mso → direct:<slug>`` / ``spotweb:<slug>`` / pluggable backends, ``<consumer.node> → mso`` (every
  Newznab consumer key, e.g. Prowlarr), ``prowlarr:<k> → mso`` for indexers that are MSO's own registration,
  ``direct:<x> → prowlarr`` / ``→ mso`` when a direct indexer URL is really Prowlarr or MSO itself;
* explicit admin edges (table ``origin_edges``).

Rules (a request is refused or a source skipped with a reason code):

* ``cycle_detected`` — an inbound request whose trace already contains this node, or a target that is already
  visited / can reach a visited node in the graph (it would call us back);
* ``hop_limit`` — more hops than ``search.max_hops``.

Hubs (Prowlarr) are queried with an explicit indexer selection, so only the *selected children* must be
acyclic: MSO's own registration in Prowlarr is excluded from every Prowlarr search, and Prowlarr is never
queried at all for a request that came from Prowlarr. The caller is identified by the ``X-MSO-Trace`` header,
the consumer API key's node, the Prowlarr User-Agent, and — as a last line of defence — by matching an
inbound question against MSO's own in-flight outbound Prowlarr searches.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from mso.config import get_settings
from mso.features.search import settings as S

TRACE_HEADER = "X-MSO-Trace"
_NODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,80}$")
INFLIGHT_TTL_S = 120.0


def self_node() -> str:
    return get_settings().node_id or "mso"


class LoopRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # cycle_detected | hop_limit
        self.message = message


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    hop_count: int = 0
    visited: list[str] = field(default_factory=list)
    caller: str | None = None

    def header(self) -> str:
        return f"{self.trace_id};hop={self.hop_count};visited={','.join(self.visited)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "hop_count": self.hop_count,
            "visited": list(self.visited),
            "caller": self.caller,
        }


def parse_header(value: str | None) -> Trace | None:
    if not value:
        return None
    parts = [p.strip() for p in value.split(";") if p.strip()]
    if not parts:
        return None
    t = Trace(trace_id=re.sub(r"[^A-Za-z0-9_-]", "", parts[0])[:32] or uuid.uuid4().hex[:16])
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k == "hop":
            try:
                t.hop_count = max(0, min(int(v), 99))
            except ValueError:
                t.hop_count = 0
        elif k == "visited":
            t.visited = [n for n in (x.strip() for x in v.split(",")) if n and _NODE_RE.match(n)][:32]
    return t


def max_hops() -> int:
    return int(S.get("search.max_hops", 2))


def start(
    visited: list[str] | None = None, *, trace_id: str | None = None, hop_count: int | None = None
) -> Trace:
    """Trace for a search that starts in (or is handed to) this node. ``visited`` = nodes before us."""
    me = self_node()
    v = [n for n in (visited or []) if n]
    if me in v[:-1] or (v.count(me) > 1):
        raise LoopRefused("cycle_detected", f"Verzoek is al via {me} gegaan (cyclus: {' → '.join(v)})")
    hops = hop_count if hop_count is not None else max(0, len([n for n in v if n != me]))
    if hops > max_hops():
        raise LoopRefused("hop_limit", f"Maximaal {max_hops()} hops; dit verzoek heeft er {hops}")
    if not v or v[-1] != me:
        v.append(me)
    return Trace(
        trace_id=trace_id or uuid.uuid4().hex[:16],
        hop_count=hops,
        visited=v,
        caller=next((n for n in reversed(v[:-1])), None),
    )


# ------------------------------------------------------------------ inbound caller detection
def inbound(
    *, header: str | None, consumer_node: str | None, user_agent: str | None, fingerprint: str | None
) -> Trace:
    """Build the trace for a request arriving at the Newznab/Torznab server. Raises LoopRefused."""
    me = self_node()
    parsed = parse_header(header)
    visited = list(parsed.visited) if parsed else []
    if me in visited:
        raise LoopRefused(
            "cycle_detected", f"Dit verzoek is al door {me} gegaan: {' → '.join(visited + [me])}"
        )
    callers: list[str] = []
    if consumer_node:
        callers.append(consumer_node)
    if user_agent and "prowlarr" in user_agent.lower():
        callers.append("prowlarr")
    if fingerprint is not None and (target := inflight_match(fingerprint)):
        callers.append(target)
    for c in callers:
        if c not in visited:
            visited.append(c)
    hops = (parsed.hop_count if parsed else 0) + 1
    if hops > max_hops():
        raise LoopRefused("hop_limit", f"Maximaal {max_hops()} hops; dit verzoek heeft er {hops}")
    visited.append(me)
    return Trace(
        trace_id=parsed.trace_id if parsed else uuid.uuid4().hex[:16],
        hop_count=hops,
        visited=visited,
        caller=callers[0] if callers else None,
    )


# ------------------------------------------------------------------ in-flight outbound registry
_inflight: dict[str, dict[str, float]] = defaultdict(dict)  # target -> {fingerprint: started}
_lock = threading.Lock()


@contextmanager
def outbound(target: str, fingerprint: str) -> Iterator[None]:
    """Mark that MSO is currently asking ``target`` this question (so a call-back is recognised)."""
    key = f"{fingerprint}#{uuid.uuid4().hex[:6]}"
    with _lock:
        _inflight[target][key] = time.monotonic()
    try:
        yield
    finally:
        with _lock:
            _inflight[target].pop(key, None)


def inflight_match(fingerprint: str) -> str | None:
    now = time.monotonic()
    with _lock:
        for target, entries in _inflight.items():
            for key, started in list(entries.items()):
                if now - started > INFLIGHT_TTL_S:
                    entries.pop(key, None)
                    continue
                if key.rsplit("#", 1)[0] == fingerprint:
                    return target
    return None


# ------------------------------------------------------------------ graph
@dataclass
class Graph:
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    notes: dict[tuple[str, str], str] = field(default_factory=dict)
    hubs: set[str] = field(default_factory=set)

    def add(self, src: str, dst: str, note: str = "") -> None:
        self.edges[src].add(dst)
        if note:
            self.notes[(src, dst)] = note

    def nodes(self) -> set[str]:
        out = set(self.edges)
        for d in self.edges.values():
            out |= d
        return out

    def reaches(
        self, start: str, targets: set[str], *, skip_hubs_from_start: bool = False
    ) -> list[str] | None:
        """Path from start to any target (BFS), or None."""
        prev: dict[str, str | None] = {start: None}
        queue = [start]
        while queue:
            n = queue.pop(0)
            for m in self.edges.get(n, ()):
                if m in prev:
                    continue
                prev[m] = n
                if m in targets:
                    path = [m]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])  # type: ignore[arg-type]
                    return list(reversed(path))
                queue.append(m)
        return None

    def cycles(self) -> list[list[str]]:
        """Elementary cycles through each node (small graphs; for display)."""
        found: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for n in sorted(self.nodes()):
            for m in self.edges.get(n, ()):
                path = self.reaches(m, {n}) if m != n else [n]
                if path is None:
                    continue
                cyc = [n] + (path if m != n else [])
                if cyc[-1] != n:
                    cyc.append(n)
                key = tuple(sorted(set(cyc)))
                if key not in seen:
                    seen.add(key)
                    found.append(cyc)
        return found

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": sorted(self.nodes()),
            "edges": [
                {"src": s, "dst": d, "note": self.notes.get((s, d), "")}
                for s in sorted(self.edges)
                for d in sorted(self.edges[s])
            ],
            "hubs": sorted(self.hubs),
        }


def _hostport(url: str | None) -> tuple[str, int] | None:
    if not url:
        return None
    try:
        p = urlsplit(url)
        return (p.hostname or "").lower(), p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return None


def build_graph() -> Graph:
    """The configured origin graph (derived edges + explicit admin edges)."""
    from mso.connectors import base as cb
    from mso.db import get_db
    from mso.features.search.backends import BACKENDS

    me = self_node()
    g = Graph()
    db = get_db()
    prowlarr_hp = None
    if cb.is_configured("prowlarr"):
        g.hubs.add("prowlarr")
        g.add(me, "prowlarr", "MSO zoekt via Prowlarr")
        row = db.fetchone("SELECT url FROM connectors WHERE id='prowlarr'")
        prowlarr_hp = _hostport(row["url"] if row else None)
    for s in db.fetchall("SELECT id, meta FROM sources WHERE origin='prowlarr'"):
        g.add("prowlarr", s["id"])
        if (s["meta"] or {}).get("is_mso"):
            g.add(s["id"], me, "MSO-registratie in Prowlarr")
    from mso.features.newznab_server import keys

    pub_hp = _hostport(keys.public_base_url())
    for d in db.fetchall("SELECT slug, kind, url FROM direct_indexers"):
        sid = f"{'spotweb' if d['kind'] == 'spotweb' else 'direct'}:{d['slug']}"
        g.add(me, sid)
        hp = _hostport(d["url"])
        if hp and prowlarr_hp and hp == prowlarr_hp:
            g.add(sid, "prowlarr", "URL wijst naar Prowlarr")
        if hp and pub_hp and hp == pub_hp:
            g.add(sid, me, "URL wijst naar MSO zelf")
    for b in BACKENDS.values():
        g.add(me, b.source_id)
        for dst in b.edges_to:
            g.add(b.source_id, dst)
    for c in db.fetchall("SELECT DISTINCT node FROM newznab_consumers WHERE enabled"):
        g.add(c["node"], me, "Newznab/Torznab-consument van MSO")
    for e in db.fetchall("SELECT src, dst, note FROM origin_edges"):
        g.add(e["src"], e["dst"], e["note"] or "handmatig ingesteld")
    return g


@dataclass
class Decision:
    allowed: bool
    reason: str = ""  # cycle_detected | hop_limit | ''
    message: str = ""
    path: list[str] | None = None


def check_target(g: Graph, trace: Trace, target: str) -> Decision:
    """May this node query ``target`` for this trace?"""
    if trace.hop_count + 1 > max_hops():
        return Decision(False, "hop_limit", f"Maximaal {max_hops()} hops")
    visited = set(trace.visited)
    if target in visited:
        return Decision(
            False, "cycle_detected", f"{target} staat al in de keten ({' → '.join(trace.visited)})"
        )
    if target in g.hubs:
        return Decision(True)  # hub: children are checked individually
    path = g.reaches(target, visited)
    if path:
        return Decision(False, "cycle_detected", "Zou terugkomen bij de aanroeper: " + " → ".join(path), path)
    return Decision(True)


def hub_children(g: Graph, trace: Trace, hub: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(allowed children, [(excluded child, reason)]) of a hub for this trace."""
    visited = set(trace.visited)
    allowed, excluded = [], []
    for child in sorted(g.edges.get(hub, ())):
        if child in visited:
            excluded.append((child, "al bezocht"))
            continue
        path = g.reaches(child, visited)
        if path:
            excluded.append((child, " → ".join(path)))
            continue
        allowed.append(child)
    return allowed, excluded
