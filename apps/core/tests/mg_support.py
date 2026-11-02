"""Shared helpers for Slice D tests (MediaGuard mock wiring, fake search/download clients)."""

from __future__ import annotations

import re
import sys
import types
from dataclasses import dataclass, field
from typing import Any

from msomocks import mediaguard as mgmock

from mso.domain.release import NormalizedRelease, parse_title

OPERATOR = ("mso-operator", "Operator-Wachtwoord1")
VIEWER = ("mso-viewer", "Viewer-Wachtwoord1")
ADMIN = ("admin", "Admin-Wachtwoord1")
ALLOWED_WRITE = [re.compile(r"^/api/v1/auth/login$"), re.compile(r"^/api/v1/wanted/\d+/search$")]


def start_mock(serve: Any, **faults: Any) -> tuple[Any, str]:
    app = mgmock.create_app(**faults)
    return app, serve(app)


def configure(
    admin: Any, url: str, *, creds: tuple[str, str] | None = OPERATOR, token: str | None = None
) -> None:
    from mso.connectors import mediaguard as mgc

    mgc.reset_sessions()
    secrets: dict[str, Any] = {"username": None, "password": None, "internal_token": None}
    options: dict[str, Any] = {}
    if creds:
        secrets.update(username=creds[0], password=creds[1])
        options["auth_method"] = "user"
    if token:
        secrets["internal_token"] = token
        options["auth_method"] = "token"
    r = admin.put(
        "/api/v1/connectors/mediaguard",
        json={"url": url, "enabled": True, "options": options, "secrets": secrets},
    )
    assert r.status_code == 200, r.text


def writes(mock_app: Any) -> list[tuple[str, str, str]]:
    return [c for c in mock_app.state.calls if c[0] not in ("GET", "HEAD")]


def assert_only_allowed_writes(mock_app: Any) -> None:
    bad = [c for c in writes(mock_app) if not any(p.match(c[1]) for p in ALLOWED_WRITE)]
    assert not bad, f"MSO riep verboden MediaGuard-schrijfroutes aan: {bad}"
    interactive = [c for c in mock_app.state.calls if "diagnosis" in c[1] and "interactive=true" in c[2]]
    assert not interactive, "MSO mag nooit een interactieve diagnose (zoekquotum) starten"


# ------------------------------------------------------------------ fake Slice A search service
@dataclass
class FakeSearchQuery:
    q: str | None = None
    media_category: str | None = None
    kind: str = "search"
    season: int | None = None
    episode: int | None = None
    absolute_episode: int | None = None
    imdb_id: str | None = None
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    protocols: set[str] | None = None
    limit: int = 100
    offset: int = 0


@dataclass
class FakeResult:
    items: list[NormalizedRelease]
    sources: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str = "trace-test"


def release(
    title: str,
    *,
    protocol: str = "usenet",
    cat: str | None = None,
    seeders: int | None = None,
    key: str | None = None,
    size_gb: float | None = 4.0,
    source_id: str = "direct:test",
    private: bool | None = None,
) -> NormalizedRelease:
    ident = key or re.sub(r"[^a-z0-9]", "", title.lower())[:32]
    r = NormalizedRelease(
        protocol=protocol,
        source_id=source_id,
        origin="direct",
        title=title,
        guid=f"g-{ident}",
        download_url=f"http://indexer.invalid/get/{ident}",
        media_category=cat,
        size_bytes=int(size_gb * 1e9) if size_gb else None,
        seeders=seeders,
        private=private,
        parsed=parse_title(title),
        confidence=0.8,
    )
    if protocol == "torrent":
        r.infohash_v1 = (ident * 3)[:40].ljust(40, "0")
    else:
        r.nzb_id = ident
    return r


def install_search(monkeypatch: Any, items: list[NormalizedRelease]) -> list[Any]:
    calls: list[Any] = []
    mod = types.ModuleType("mso.features.search.service")
    mod.SearchQuery = FakeSearchQuery  # type: ignore[attr-defined]

    by_token = {}
    for r in items:
        r.extra["token"] = f"tok-{r.release_key}"
        by_token[r.extra["token"]] = r

    def search(query: Any, *, trace: list[str] | None = None, actor: str = "system") -> FakeResult:
        calls.append(query)
        return FakeResult(
            items=list(items),
            sources=[
                {
                    "source_id": "direct:test",
                    "label": "Test",
                    "status": "ok",
                    "count": len(items),
                    "latency_ms": 3,
                    "message": "",
                }
            ],
        )

    def fetch_download(token: str) -> Any:
        r = by_token.get(token)
        if r is None:
            raise LookupError("Resultaat niet (meer) beschikbaar; zoek opnieuw")
        if r.protocol == "torrent":
            return types.SimpleNamespace(content=b"d8:announce...e", filename=None, final_url=None)
        return types.SimpleNamespace(content=b"<nzb/>", filename=f"{r.title}.nzb", final_url=None)

    mod.search = search  # type: ignore[attr-defined]
    mod.fetch_download = fetch_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mso.features.search.service", mod)
    return calls


def remove_search(monkeypatch: Any) -> None:
    monkeypatch.setitem(sys.modules, "mso.features.search.service", None)


def install_clients(monkeypatch: Any) -> dict[str, list[Any]]:
    sent: dict[str, list[Any]] = {"sab": [], "qbit": []}
    sab = types.ModuleType("mso.connectors.sabnzbd")

    class SabClient:
        @classmethod
        def from_config(cls) -> SabClient:
            return cls()

        def add_url(
            self, url: str, *, category: str, name: str | None = None, priority: int | None = None
        ) -> str:
            raise AssertionError("recovery must send NZB content fetched via Slice A, not a URL")

        def add_nzb(self, data: bytes, filename: str, *, category: str) -> str:
            sent["sab"].append({"bytes": len(data), "filename": filename, "category": category})
            return f"SABnzbd_nzo_{len(sent['sab'])}"

    sab.SabClient = SabClient  # type: ignore[attr-defined]
    qb = types.ModuleType("mso.connectors.qbittorrent")

    class QbitClient:
        @classmethod
        def from_config(cls) -> QbitClient:
            return cls()

        def add_torrent(
            self,
            *,
            url: str | None = None,
            data: bytes | None = None,
            category: str,
            paused: bool = False,
            tags: list[str] | None = None,
        ) -> None:
            sent["qbit"].append({"url": url, "bytes": len(data or b""), "category": category, "tags": tags})

    qb.QbitClient = QbitClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mso.connectors.sabnzbd", sab)
    monkeypatch.setitem(sys.modules, "mso.connectors.qbittorrent", qb)
    return sent
