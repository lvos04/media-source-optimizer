"""Recovery: failed-release blacklist (TTL, exponential, hard cap), per-item backoff, outcome processing,
Source Baseline, and a source scan proving MSO never touches MediaGuard files/DB."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mg_support import configure, install_clients, install_search, release, start_mock

from mso.domain.outcomes import upsert_outcome


def test_blacklist_ttl_escalates_and_hard_cap_is_permanent(app: Any, db: Any) -> None:
    from mso.features.recovery import blacklist

    e1 = blacklist.add("nzb:abc", "incompleet", 10, "manual:1", "usenet")
    assert e1["failures"] == 1 and not e1["permanent"]
    assert blacklist.is_blacklisted("nzb:abc") and not blacklist.is_blacklisted("nzb:other")
    first = (e1["expires_at"] - e1["created_at"]).total_seconds()
    assert 9.9 * 3600 < first < 10.1 * 3600
    e2 = blacklist.add("nzb:abc", "incompleet", 10)  # default hard cap = 2
    assert e2["failures"] == 2 and e2["permanent"] and e2["expires_at"] is None
    db.execute("UPDATE settings SET value='5' WHERE key='herstel.blacklist_max_failures'")
    db.execute(
        "INSERT INTO settings(key,value) VALUES ('herstel.blacklist_max_failures','5') ON CONFLICT DO NOTHING"
    )
    e3 = blacklist.add("btih:" + "a" * 40, "dood", 10, protocol="torrent")
    e4 = blacklist.add("btih:" + "a" * 40, "dood", 10, protocol="torrent")
    assert (e4["expires_at"] - e4["updated_at"]).total_seconds() > 1.9 * (
        e3["expires_at"] - e3["created_at"]
    ).total_seconds()
    # expiry
    db.execute(
        "UPDATE recovery_blacklist SET expires_at=now() - interval '1 minute' WHERE release_key LIKE "
        "'btih:%%'"
    )
    assert not blacklist.is_blacklisted("btih:" + "a" * 40)
    assert blacklist.is_blacklisted("nzb:abc")  # permanent never expires


def test_blacklist_api_roles(viewer: Any, operator: Any, admin: Any) -> None:
    r = operator.post(
        "/api/v1/recovery/blacklist",
        json={"release_key": "nzb:xyz", "reason": "vals-positief", "protocol": "usenet"},
    )
    assert r.status_code == 200, r.text
    entry = r.json()
    assert (
        operator.post("/api/v1/recovery/blacklist", json={"release_key": "titel", "reason": "x"}).status_code
        == 422
    )
    items = viewer.get("/api/v1/recovery/blacklist").json()["items"]
    assert items[0]["release_key"] == "nzb:xyz" and items[0]["active"] is True
    assert operator.delete(f"/api/v1/recovery/blacklist/{entry['id']}").status_code == 403
    assert admin.delete(f"/api/v1/recovery/blacklist/{entry['id']}").status_code == 200
    assert admin.delete(f"/api/v1/recovery/blacklist/{entry['id']}").status_code == 404
    assert viewer.get("/api/v1/recovery/blacklist").json()["items"] == []


def test_failed_outcomes_blacklisted_once_and_send_backoff(
    operator: Any, worker: Any, db: Any, monkeypatch: Any
) -> None:
    good = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP", key="sent1")
    alt = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-ALT", key="alt1")
    install_search(monkeypatch, [good, alt])
    sent = install_clients(monkeypatch)
    ref = operator.post(
        "/api/v1/recovery/wanted",
        json={"title": "Lost Signal", "media_category": "tv", "season_number": 2, "episode_number": 5},
    ).json()["media_ref"]
    operator.post(f"/api/v1/recovery/items/{ref}/candidates", json={})
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": good.release_key})
    assert r.status_code == 200, r.text
    nzo = r.json()["send"]["client_ref"]
    # SABnzbd engine reports the download failed (incomplete)
    upsert_outcome(
        {
            "protocol": "usenet",
            "client": "sabnzbd",
            "client_ref": nzo,
            "release_key": good.release_key,
            "title": good.title,
            "status": "failed",
            "failure_reason": "incomplete",
            "media_category": "tv",
        }
    )
    upsert_outcome(
        {
            "protocol": "usenet",
            "client": "sabnzbd",
            "client_ref": "other",
            "release_key": "nzb:diskfull",
            "title": "x",
            "status": "failed",
            "failure_reason": "disk_full",
        }
    )
    assert operator.post("/api/v1/recovery/analyze").status_code == 200
    worker.drain()
    from mso.features.recovery import blacklist

    assert blacklist.is_blacklisted(good.release_key)
    assert not blacklist.is_blacklisted("nzb:diskfull")  # client-side failure says nothing about the release
    assert db.scalar("SELECT status FROM recovery_sends WHERE client_ref=%s", (nzo,)) == "download_failed"
    # re-running does not count the same failure twice
    operator.post("/api/v1/recovery/analyze")
    worker.drain()
    assert db.scalar("SELECT failures FROM recovery_blacklist WHERE release_key=%s", (good.release_key,)) == 1
    # the same release can no longer be sent; the alternative is held back by per-item backoff
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": good.release_key})
    assert r.status_code == 422
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": alt.release_key})
    assert r.status_code == 429 and "backoff" in r.json()["detail"]
    assert len(sent["sab"]) == 1


def test_manual_send_rate_limit(operator: Any, admin: Any, monkeypatch: Any) -> None:
    rels = [release(f"Lost.Signal.S02E05.1080p.WEB-DL.H.264-G{i}", key=f"k{i}") for i in range(3)]
    install_search(monkeypatch, rels)
    install_clients(monkeypatch)
    admin.put("/api/v1/settings", json={"values": {"herstel.manual_sends_per_hour": 2}})
    ref = operator.post(
        "/api/v1/recovery/wanted",
        json={"title": "Lost Signal", "media_category": "tv", "season_number": 2, "episode_number": 5},
    ).json()["media_ref"]
    operator.post(f"/api/v1/recovery/items/{ref}/candidates", json={})
    codes = [
        operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": r.release_key}).status_code
        for r in rels
    ]
    assert codes == [200, 200, 429]


def test_source_baseline_buckets(
    serve: Any, admin: Any, operator: Any, viewer: Any, worker: Any, db: Any
) -> None:
    from mso.domain.outcomes import upsert_source

    upsert_source("prowlarr:1", "nzb_indexer", "NZBGeek", "prowlarr")
    for i, (reason, cat) in enumerate(
        [
            ("incomplete", "tv"),
            ("false_positive", "tv"),
            ("false_positive", "tv"),
            ("false_positive", "tv-anime"),
            ("no_retention", "movies"),
            ("auth_failure", "movies"),
            (None, "movies-anime"),
        ]
    ):
        upsert_outcome(
            {
                "protocol": "usenet",
                "client": "sabnzbd",
                "client_ref": f"n{i}",
                "release_key": f"nzb:{i}",
                "title": f"t{i}",
                "status": "failed",
                "failure_reason": reason,
                "media_category": cat,
                "source_id": "prowlarr:1",
            }
        )
    mock, url = start_mock(serve)
    configure(admin, url)
    assert operator.post("/api/v1/mediaguard/sync").status_code == 200
    worker.drain()
    b = viewer.get("/api/v1/recovery/baseline").json()
    assert [x["key"] for x in b["buckets"]][:7] == [
        "no_indexer_hit",
        "false_positive",
        "rejected_quality",
        "no_retention",
        "incomplete",
        "client_failure",
        "import_failure",
    ]
    assert b["by_category"]["tv"]["false_positive"] == 2 and b["by_category"]["tv"]["incomplete"] == 1
    assert b["by_category"]["movies"]["other"] == 1  # auth failure is not a release cause
    assert b["by_category"]["movies-anime"]["unknown"] >= 1  # no evidence → unknown
    src = next(s for s in b["by_source"] if s["source_id"] == "prowlarr:1")
    assert src["label"] == "NZBGeek" and src["total"] == 7
    mg = {s["source_id"]: s for s in b["by_source"] if s["origin"] == "mediaguard"}
    assert mg["mediaguard:sonarr-series"]["counts"]["no_indexer_hit"] == 1
    assert mg["mediaguard:sonarr-series"]["counts"]["rejected_quality"] == 1
    assert mg["mediaguard:sonarr-series"]["counts"]["import_failure"] == 1
    assert b["by_category"]["tv-anime"]["unknown"] >= 1  # MediaGuard item without diagnosis
    assert b["coverage"]["failed_downloads"] == 7 and b["coverage"]["known_share"] is not None
    # recommendations from baseline
    operator.post("/api/v1/recovery/analyze")
    worker.drain()
    assert (
        db.scalar(
            "SELECT count(*) FROM recommendations WHERE area='recovery' AND "
            "dedupe_key='recovery:fp:prowlarr:1'"
        )
        == 1
    )


def test_recovery_wanted_combines_mediaguard_and_manual(
    serve: Any, admin: Any, operator: Any, viewer: Any, worker: Any
) -> None:
    mock, url = start_mock(serve)
    configure(admin, url)
    operator.post("/api/v1/mediaguard/sync")
    worker.drain()
    operator.post(
        "/api/v1/recovery/wanted", json={"title": "Eigen film", "media_category": "movies", "year": 2020}
    )
    res = viewer.get("/api/v1/recovery/wanted").json()
    origins = {i["origin"] for i in res["items"]}
    assert origins == {"mediaguard", "manual"} and res["mode"] == "connected"
    assert viewer.get("/api/v1/recovery/wanted?source=manual").json()["total"] == 1
    assert (
        operator.post("/api/v1/recovery/wanted", json={"title": "Serie", "media_category": "tv"}).status_code
        == 422
    )
    item = viewer.get("/api/v1/recovery/items/mg:2").json()
    assert item["diagnosis"]["primary_code"] == "rejected-quality"
    assert viewer.get("/api/v1/recovery/items/mg:999").status_code == 404
    assert viewer.get("/api/v1/recovery/items/foo").status_code == 404


# ------------------------------------------------------------------ MediaGuard stays untouched
MSO_ROOT = Path(__file__).resolve().parents[1] / "mso"
FORBIDDEN = [
    r"sqlite",
    r"mediaguard\.db",
    r"\bmg_appdata\b",
    r"MG_DATA_ROOT",
    r"MG_APPDATA",
    r"MG_INTERNAL_TOKEN_FILE",
    r"/run/mediaguard",
    r"media-guard-ultimate/apps",
    r"\bwanted_items\b",
    r"\bmissing_diagnostics\b",
    r"\bbackfill_attempts\b",
    r"\bwanted_overrides\b",
    r"/config/core",
]


def test_no_code_path_touches_mediaguard_files_or_db() -> None:
    hits = []
    for path in MSO_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            if re.search(pat, text, re.I):
                hits.append(f"{path.relative_to(MSO_ROOT)}: {pat}")
    for path in MSO_ROOT.rglob("*.sql"):
        text = path.read_text(encoding="utf-8")
        for pat in (r"\bwanted_items\b", r"\bmissing_diagnostics\b", r"attach\s+database"):
            if re.search(pat, text, re.I):
                hits.append(f"{path.relative_to(MSO_ROOT)}: {pat}")
    assert not hits, hits
    # MediaGuard is only reached over HTTP through the SSRF-checked connector client
    mg_code = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [
            MSO_ROOT / "connectors" / "mediaguard.py",
            *(MSO_ROOT / "features" / "mediaguard").glob("*.py"),
        ]
    )
    assert "open(" not in mg_code and "psycopg.connect" not in mg_code and "subprocess" not in mg_code
    from mso.connectors import mediaguard as mgc

    assert [(m, p.pattern) for m, p in mgc.ALLOWED_WRITES] == [
        ("POST", r"^/api/v1/auth/login$"),
        ("POST", r"^/api/v1/wanted/\d+/search$"),
    ]
