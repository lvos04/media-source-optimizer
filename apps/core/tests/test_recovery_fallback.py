"""1.4.0 — failed download → other candidate: blacklist, mapping, change set "Versturen naar SABnzbd/qBittorrent"
(preview, read-only, approval, apply verified at the client, quotas, irreversible, no secrets)."""

from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime
from typing import Any

import pytest
from mg_support import install_search, release

SAB_KEY = "SABGEHEIM-fallback-0123456789"
NZB = b"""<?xml version="1.0" encoding="utf-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
 <file poster="someone@example.invalid" date="1700000000" subject="Lost.Signal.S02E05 [1/1] yEnc">
  <groups><group>alt.binaries.mso.test</group></groups>
  <segments><segment bytes="700000" number="1">seg1@mock</segment></segments>
 </file>
</nzb>"""


def _search(monkeypatch: Any, items: list[Any]) -> list[Any]:
    """Fake Slice A search whose NZBs are valid for the real SABnzbd connector."""
    calls = install_search(monkeypatch, items)
    mod = sys.modules["mso.features.search.service"]
    orig = mod.fetch_download

    def fetch(token: str) -> Any:
        f = orig(token)
        if f.filename:  # usenet
            return types.SimpleNamespace(content=NZB, filename=f.filename, final_url=None)
        return f

    monkeypatch.setattr(mod, "fetch_download", fetch)
    return calls


@pytest.fixture()
def sab(app: Any, serve: Any, admin: Any) -> Any:
    from msomocks import sabnzbd as mock

    # SABnzbd files a job with an unknown category under '*' (1.6.0 mock); MSO's own category must exist there
    mock_app = mock.create_app(api_key=SAB_KEY, categories="tv,tv-anime,movies,movies-anime,software,mso-tv")
    url = serve(mock_app)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "enabled": True, "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    return mock_app.state.sab


def _failed(
    title: str,
    *,
    key: str | None = "nzb:failed-1",
    reason: str = "incomplete",
    cat: str | None = "tv",
    protocol: str = "usenet",
    ref: str = "SABnzbd_nzo_failed1",
    meta: dict[str, Any] | None = None,
) -> int:
    from mso.domain import outcomes

    return outcomes.upsert_outcome(
        {
            "protocol": protocol,
            "client": "sabnzbd" if protocol == "usenet" else "qbittorrent",
            "client_ref": ref,
            "release_key": key,
            "title": title,
            "client_category": cat,
            "media_category": cat,
            "status": "failed",
            "failure_reason": reason,
            "finished_at": datetime.now(UTC),
            "meta": meta or {},
        }
    )


def _run() -> dict[str, Any]:
    """The two steps of the ``recovery.analyze`` job, in job order."""
    from mso.features.recovery import fallback, tasks

    tasks.process_outcomes()
    return fallback.process_failed()


def _fallbacks(viewer: Any) -> list[dict[str, Any]]:
    r = viewer.get("/api/v1/recovery/fallbacks")
    assert r.status_code == 200, r.text
    return r.json()["items"]


GOOD = "Lost.Signal.S02E05.1080p.WEB-DL.H.264-GRP"
FAILED = "Lost.Signal.S02E05.1080p.WEB-DL.H.264-BAD"


def test_failed_download_becomes_send_proposal_and_is_blacklisted(
    admin: Any, operator: Any, viewer: Any, sab: Any, monkeypatch: Any
) -> None:
    good = release(GOOD, key="good1", cat="tv", source_id="direct:test")
    _search(monkeypatch, [good, release(FAILED, key="failed-1", cat="tv")])
    _failed(FAILED)
    out = _run()
    assert out.get("searched") == 1, out
    from mso.features.recovery import blacklist

    assert blacklist.is_blacklisted("nzb:failed-1")  # failed release is on the blacklist
    items = _fallbacks(viewer)
    assert len(items) == 1
    fb = items[0]
    assert fb["status"] == "proposed" and fb["mapping"] == "outcome_meta" and fb["media_ref"].startswith("manual:")
    assert fb["candidate_release_key"] == good.release_key
    ch = viewer.get(f"/api/v1/changes/{fb['change_id']}").json()
    # preview: release, source, quality-gate evidence, target client + category, irreversible
    assert ch["target"] == "download_send" and ch["risk"] == "toestemming_nodig" and ch["status"] == "preview"
    assert ch["title"] == f"Versturen naar SABnzbd: {GOOD}"
    op = ch["ops"][0]
    assert op["path"] == f"send:{fb['media_ref']}:{good.release_key}"
    assert op["before"] == "niet verstuurd" and op["after"] == "verstuurd" and op["irreversible"] is True
    imp = ch["impact"]
    assert imp["Vervangende release"] == GOOD and "direct:test" in imp["Bron"]
    assert imp["Doel"] == "SABnzbd · categorie 'mso-tv'"
    assert any(e.startswith("✓ Aflevering/film") for e in imp["Kwaliteitscontrole"])
    assert "Niet mogelijk" in imp["Terugdraaien"]
    assert "Terugdraaien is niet mogelijk" in ch["explanation"]
    # idempotent: a second run does not create another proposal for the same outcome
    _run()
    assert len(viewer.get("/api/v1/changes?target=download_send").json()["items"]) == 1
    # no secrets anywhere in the proposal / list
    blob = json.dumps(ch) + json.dumps(items)
    assert SAB_KEY not in blob and "indexer.invalid" not in blob


def test_read_only_blocks_and_approval_needed_then_apply_verified_at_sab(
    app: Any, admin: Any, operator: Any, viewer: Any, sab: Any, monkeypatch: Any
) -> None:
    _search(monkeypatch, [release(GOOD, key="good1", cat="tv")])
    _failed(FAILED)
    _run()
    cid = _fallbacks(viewer)[0]["change_id"]
    # read-only (default mode): nothing is applied, even after approval
    assert admin.post(f"/api/v1/changes/{cid}/approve").status_code == 200
    r = admin.post(f"/api/v1/changes/{cid}/apply")
    assert r.status_code == 409 and "alleen-lezen" in r.json()["detail"]
    assert not any(s["filename"] == GOOD for s in sab.queue)
    from mso import state

    state.set_state("mode", "manage")
    # a fresh proposal needs approval: operator cannot apply it unapproved
    admin.post(f"/api/v1/changes/{cid}/discard")
    from mso.db import get_db

    get_db().execute("DELETE FROM recovery_fallbacks")
    _run()
    cid = _fallbacks(viewer)[0]["change_id"]
    assert cid is not None
    r = operator.post(f"/api/v1/changes/{cid}/apply")
    assert r.status_code == 403
    assert viewer.post(f"/api/v1/changes/{cid}/approve").status_code == 403
    assert admin.post(f"/api/v1/changes/{cid}/approve").status_code == 200
    r = operator.post(f"/api/v1/changes/{cid}/apply")
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["status"] == "applied" and ch["ops"][0]["result"] == "toegepast"
    # verified at the source: SABnzbd really has the NZB, in the separate MSO category
    slot = next(s for s in sab.queue if s["filename"] == GOOD)
    assert slot["cat"] == "mso-tv"
    send = get_db().fetchone(
        "SELECT * FROM recovery_sends WHERE release_key=%s", (ch["ops"][0]["path"].split(":", 3)[3],)
    )
    assert send["status"] == "sent" and send["client_ref"] == slot["nzo_id"]
    assert _fallbacks(viewer)[0]["status"] == "sent"
    # irreversible: rollback refused with an explanation
    r = admin.post(f"/api/v1/changes/{cid}/rollback")
    assert r.status_code == 409 and "niet terug te draaien" in r.json()["detail"]
    # audit trail, without secrets
    rows = get_db().fetchall("SELECT action, summary, detail FROM audit_log ORDER BY id")
    actions = [r["action"] for r in rows]
    assert "recovery.fallback_proposed" in actions and "change.applied" in actions and "recovery.sent" in actions
    assert any("via wijzigingsvoorstel" in r["summary"] for r in rows if r["action"] == "recovery.sent")
    assert SAB_KEY not in json.dumps([dict(r) for r in rows], default=str)


def test_quota_respected_change_stays_open(
    app: Any, admin: Any, operator: Any, viewer: Any, sab: Any, monkeypatch: Any, manage_mode: Any
) -> None:
    _search(monkeypatch, [release(GOOD, key="good1", cat="tv")])
    _failed(FAILED)
    _run()
    cid = _fallbacks(viewer)[0]["change_id"]
    assert admin.put("/api/v1/settings", json={"values": {"herstel.manual_sends_per_hour": 1}}).status_code == 200
    from mso.db import get_db

    get_db().execute(
        "INSERT INTO recovery_sends(idempotency_key,media_ref,release_key,protocol,client,title,status,actor) "
        "VALUES ('send:manual:999:nzb:x','manual:999','nzb:x','usenet','sabnzbd','x','sent','t')"
    )
    admin.post(f"/api/v1/changes/{cid}/approve")
    r = admin.post(f"/api/v1/changes/{cid}/apply")
    assert r.status_code == 429 and "per uur" in r.json()["detail"]
    assert viewer.get(f"/api/v1/changes/{cid}").json()["status"] == "approved"  # still open, nothing sent
    assert not any(s["filename"] == GOOD for s in sab.queue)


def test_auto_send_setting_makes_proposal_safe_and_automation_applies_it(
    app: Any, admin: Any, viewer: Any, monkeypatch: Any, manage_mode: Any
) -> None:
    tor = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-TGRP", protocol="torrent", seeders=40, key="tgood", cat="tv")
    install_search(monkeypatch, [tor])
    added: list[dict[str, Any]] = []
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
            added.append({"category": category, "tags": tags})

        def torrent(self, h: str) -> dict[str, Any] | None:
            return {"hash": h} if added and h == tor.infohash_v1 else None

    qb.QbitClient = QbitClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mso.connectors.qbittorrent", qb)
    r = admin.put(
        "/api/v1/settings",
        json={"values": {"recovery.auto_send_fallback": True, "automation.recovery.auto_apply_safe": True}},
    )
    assert r.status_code == 200, r.text
    _failed(
        "Lost.Signal.S02E05.1080p.WEB-DL.H.264-MALWARE",
        key="btih:" + "a" * 40,
        reason="false_positive",
        protocol="torrent",
        ref="a" * 40,
    )
    _run()
    fb = _fallbacks(viewer)[0]
    ch = viewer.get(f"/api/v1/changes/{fb['change_id']}").json()
    assert ch["risk"] == "automatisch_veilig" and not ch["ops"][0].get("irreversible")
    assert ch["title"].startswith("Versturen naar qBittorrent")
    from mso.features.automation import engine

    res = engine.apply_safe()
    assert res["applied"] == [ch["id"]], res
    assert added == [{"category": "mso-tv", "tags": ["mso-herstel"]}]
    assert viewer.get(f"/api/v1/changes/{ch['id']}").json()["status"] == "applied"


def test_unmappable_or_client_caused_failures_are_skipped_with_reason(viewer: Any, monkeypatch: Any) -> None:
    calls = _search(monkeypatch, [release(GOOD, key="good1", cat="tv")])
    _failed(FAILED, key="nzb:auth", reason="auth_failure", ref="n1")
    _failed("a8f7d6e5c4b3a29181726354", key="nzb:obf", reason="incomplete", ref="n2")
    _failed(FAILED, key="nzb:nocat", reason="unknown", ref="n3", cat=None)
    _run()
    by_key = {f["failed_release_key"]: f for f in _fallbacks(viewer)}
    # a login/account failure is a server problem, not a release problem. 1.6.0: SABnzbd only retries the same job
    # after the coverage improved (none here), so until then it is skipped with the cause instead of 'retry'
    assert (
        by_key["nzb:auth"]["status"] == "skipped" and "andere release lost dit niet op" in by_key["nzb:auth"]["reason"]
    )
    assert by_key["nzb:obf"]["status"] == "skipped" and "niet herleidbaar" in by_key["nzb:obf"]["reason"]
    assert by_key["nzb:nocat"]["status"] == "skipped" and "Categorie onbekend" in by_key["nzb:nocat"]["reason"]
    assert not calls  # no indexer searches spent on these
    from mso.features.recovery import blacklist

    assert blacklist.is_blacklisted("nzb:nocat")  # failed release still blacklisted
    assert not blacklist.is_blacklisted("nzb:auth")  # account problem: release itself not blamed


def test_setting_off_does_nothing(admin: Any, viewer: Any, monkeypatch: Any) -> None:
    calls = _search(monkeypatch, [release(GOOD, key="good1", cat="tv")])
    admin.put("/api/v1/settings", json={"values": {"recovery.fallback_on_failure": False}})
    _failed(FAILED)
    assert _run() == {"skipped": "uit"}
    assert not calls and not _fallbacks(viewer)


def test_maps_to_mediaguard_cache_by_title_and_numbering(viewer: Any, monkeypatch: Any) -> None:
    from datetime import timedelta

    from mso.db import get_db

    get_db().execute(
        "INSERT INTO mg_wanted_cache(mg_id,instance_id,media_category,kind,title,item_title,season_number,"
        "episode_number,expires_at) VALUES (77,'sonarr-main','tv','episode','Lost Signal S02E05','Lost Signal',2,5,%s),"
        "(78,'sonarr-main','tv','episode','Lost Signal S02E06','Lost Signal',2,6,%s)",
        (datetime.now(UTC) + timedelta(hours=1), datetime.now(UTC) + timedelta(hours=1)),
    )
    _search(monkeypatch, [release(GOOD, key="good1", cat="tv")])
    _failed(FAILED)
    _run()
    fb = _fallbacks(viewer)[0]
    assert fb["media_ref"] == "mg:77" and fb["mapping"] == "mediaguard_title"
    # MediaGuard not reachable → its policy is unreadable → no proposal, reason recorded
    assert fb["status"] == "skipped" and "niet leesbaar" in fb["reason"] and fb["change_id"] is None
