"""Torrent Autopilot (1.5.0): everything reversible automatically, deleting never.

Covers: prevention preference "Uitvoerbare bestanden nooit downloaden" (merge with user patterns, apply, read-back
verification, rollback to the original string, capability detection on old versions, policy refusing media
patterns); Autopilot applies the guardrail bundle and prevention automatically but never the malware cleanup;
read-only/pause blocks every automatic write; the one open cleanup proposal is deduplicated and refreshed when the
suspicious set changes; P5 (suspicious file name shown once); the "Sonarr kan opnieuw grijpen" note; safe
remedies (reannounce / recheck) in Autopilot only, bounded per hour.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from mso import changes, state
from mso.connectors.qbittorrent import QbitClient

GB = 1024**3
AUTO_ACTORS = ("automatisering", "autopilot")  # shared Autopilot helper / torrent fallback


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    QbitClient.reset_cache()
    yield
    QbitClient.reset_cache()


class Qb:
    def __init__(self, app: Any) -> None:
        self.app = app

    @property
    def st(self) -> Any:
        return self.app.state.qb

    def hash(self, prefix: str) -> str:
        return next(h for h, t in self.st.torrents.items() if t["name"].startswith(prefix))

    def calls(self, needle: str) -> list[Any]:
        return [c for c in self.st.calls if needle in c[1]]


def _connect(admin: Any, serve: Any, **faults: Any) -> Qb:
    from msomocks.qbittorrent import create_app

    app = create_app(**faults)
    url = serve(app)
    r = admin.put(
        "/api/v1/connectors/qbittorrent",
        json={"url": url, "secrets": {"username": "admin", "password": "adminadmin"}},
    )
    assert r.status_code == 200
    return Qb(app)


@pytest.fixture()
def qb(admin: Any, serve: Any) -> Qb:
    return _connect(admin, serve, version="5.3.0", webapi="2.16.2", excluded_file_names="*.txt\n  sample.*\n")


@pytest.fixture()
def autopilot_mode(app: Any) -> None:
    state.set_state("mode", "autopilot")


def ingest() -> dict[str, Any]:
    from mso.features.torrent.ingest import ingest as run

    return run()


def _add_suspicious(qb: Qb, names: dict[str, str] | None = None) -> dict[str, str]:
    from msomocks.qbittorrent import _torrent

    pub = "https://tracker.publiek.example/announce"
    tr = [{"url": pub, "status": 2, "tier": 0, "num_peers": 3, "num_seeds": 9, "num_leeches": 3,
           "num_downloaded": 1, "msg": ""}]
    names = names or {
        "exe": "Criminal Minds S19E06 1080p WEB h264-GRACE.exe",
        "scr": "House of the Dragon S03E05 1080p HEVC x265-MeGusta.scr",
    }
    for n in names.values():
        t = _torrent(n, category="tv-sonarr", size=GB, progress=1.0, state="stalledUP", tracker=pub, private=False)
        qb.st.add(t, tr, [(n, GB)])
    return {k: qb.hash(v) for k, v in names.items()}


def _open_cleanups(db: Any) -> list[dict[str, Any]]:
    return db.fetchall(
        "SELECT * FROM change_sets WHERE source='torrent-cleanup' AND status IN ('preview','approved') ORDER BY id"
    )


# ------------------------------------------------------------------ 1. prevention
def test_prevention_merge_apply_verify_rollback(
    qb: Qb, admin: Any, operator: Any, viewer: Any, manage_mode: None
) -> None:
    from mso.features.torrent import autopilot as tap
    from mso.features.torrent import tuning

    ingest()
    st = viewer.get("/api/v1/torrent/prevention").json()
    assert st["supported"] is True and st["active"] is False and st["user_patterns"] == ["*.txt", "sample.*"]
    assert "*.exe" in st["missing"] and "*.com" not in st["recommended"]

    # outside Autopilot: a proposal (automatisch_veilig), nothing written yet
    ch = operator.post("/api/v1/torrent/prevention/preview").json()
    assert ch["auto"] is None and ch["status"] == "preview" and ch["risk"] == "automatisch_veilig"
    assert ch["title"] == "Uitvoerbare bestanden nooit downloaden"
    ops = {o["path"]: o for o in ch["ops"]}
    pat = ops["pref:excluded_file_names"]
    assert pat["before"] == "*.txt\nsample.*"  # normalised like qBittorrent keeps it
    after = pat["after"].split("\n")
    assert after[:2] == ["*.txt", "sample.*"]  # the user's patterns stay first, never removed
    assert set(tap.PREVENTION_PATTERNS) <= set(after)
    assert ops["pref:excluded_file_names_enabled"] == {**ops["pref:excluded_file_names_enabled"],
                                                       "before": False, "after": True}
    assert qb.st.prefs["excluded_file_names_enabled"] is False
    # same proposal again (dedupe)
    assert operator.post("/api/v1/torrent/prevention/preview").json()["id"] == ch["id"]

    # apply (beheermodus, no approval needed for automatisch_veilig): written AND read back
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    applied = r.json()
    assert all(o["result"] == "toegepast" for o in applied["ops"])
    assert qb.st.prefs["excluded_file_names_enabled"] is True
    assert qb.st.prefs["excluded_file_names"].split("\n") == after
    st = viewer.get("/api/v1/torrent/prevention").json()
    assert st["active"] is True and st["missing"] == []
    # never measured
    ev = tuning.evaluate_change(changes.get(ch["id"]))
    assert ev["verdict"] == "not_applicable"
    # already active → nothing to propose
    assert operator.post("/api/v1/torrent/prevention/preview").status_code == 409

    # rollback restores the original list and switch
    assert admin.post(f"/api/v1/changes/{ch['id']}/rollback").status_code == 200
    assert qb.st.prefs["excluded_file_names"] == "*.txt\nsample.*"
    assert qb.st.prefs["excluded_file_names_enabled"] is False
    # a rolled-back prevention is not proposed again by itself (only manually)
    assert "teruggedraaid" in tap.ensure_prevention()["reason"]
    assert operator.post("/api/v1/torrent/prevention/preview").status_code == 200


def test_prevention_unsupported_on_old_qbittorrent(admin: Any, operator: Any, viewer: Any, serve: Any,
                                                   manage_mode: None) -> None:
    qb = _connect(admin, serve, version="4.5.2", webapi="2.8.19")
    assert "excluded_file_names" not in qb.st.prefs
    ingest()
    st = viewer.get("/api/v1/torrent/prevention").json()
    assert st["supported"] is False and "4.6" in st["message"]
    r = operator.post("/api/v1/torrent/prevention/preview")
    assert r.status_code == 409 and "4.6" in r.json()["detail"]
    assert not qb.calls("setPreferences")


def test_prevention_policy_refuses_media_patterns(qb: Qb, operator: Any, manage_mode: None) -> None:
    from mso.features.torrent import policy

    for bad in ("*", "*.mkv", "*.*", "Season*", "*1080p*"):
        with pytest.raises(policy.PolicyError):
            policy.validate("pref:excluded_file_names", f"*.exe\n{bad}")
    policy.validate("pref:excluded_file_names", "*.exe\n*.scr\n*.mkv.com\n*.txt")
    ingest()
    r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"excluded_file_names": "*.mkv"}})
    assert r.status_code == 422 and "mediabestanden" in r.json()["detail"]


# ---------------------------------------------------- 2+3. Autopilot: bundle & prevention yes, cleanup never
def test_autopilot_applies_bundle_and_prevention_never_cleanup(
    qb: Qb, admin: Any, viewer: Any, db: Any, autopilot_mode: None
) -> None:
    from mso.features.torrent import autopilot as tap
    from mso.features.torrent import tuning

    qb.st.prefs.update({"max_connec": 50000, "max_connec_per_torrent": 100000, "max_uploads": -1})
    hs = _add_suspicious(qb)
    ingest()
    out = tap.run()
    # prevention applied automatically, verified at the source
    assert out["prevention"]["auto"] == "toegepast", out
    assert qb.st.prefs["excluded_file_names_enabled"] is True and "*.exe" in qb.st.prefs["excluded_file_names"]
    # cleanup prepared, waiting for approval, never applied
    opens = _open_cleanups(db)
    assert len(opens) == 1 and opens[0]["status"] == "preview" and opens[0]["risk"] == "toestemming_nodig"
    assert opens[0]["created_by"] == "autopilot"
    assert qb.st.deletes == [] and all(h in qb.st.torrents for h in hs.values())
    with pytest.raises(HTTPException) as exc:
        changes.apply(opens[0]["id"], "autopilot", automatic=True)
    assert exc.value.status_code == 409
    assert qb.st.deletes == []
    # preview tells the truth about re-grabbing and that prevention is on
    blk = opens[0]["impact"]["Blokkeren"]
    assert "Sonarr kan deze releases opnieuw grijpen" in blk and "(staat aan)" in blk

    # guardrail bundle: automatisch_veilig and applied at once, impact "beleidsgrens"
    res = tuning.propose("system")
    assert res.get("bundle") is True and res["auto"] == "toegepast", res
    ch = res["change"]
    assert ch["risk"] == "automatisch_veilig" and ch["status"] == "applied" and ch["applied_by"] in AUTO_ACTORS
    assert "beleidsgrens" in ch["impact"]["Soort"] and ch["evaluate_after"] is None
    assert (qb.st.prefs["max_connec"], qb.st.prefs["max_connec_per_torrent"], qb.st.prefs["max_uploads"]) == (
        1000, 200, 500)
    assert viewer.get("/api/v1/torrent/tuning").json()["items"][0]["status"] == "kept"
    # still nothing deleted, and the audit shows the automatic applies
    assert qb.st.deletes == []
    acts = {r["action"] for r in db.fetchall("SELECT action FROM audit_log WHERE actor = ANY(%s)",
                                             (list(AUTO_ACTORS),))}
    assert {"change.applied", "torrent.prevention_applied", "torrent.cleanup_proposed"} <= acts


def test_autopilot_replaces_waiting_guardrail_proposal(qb: Qb, operator: Any, db: Any, manage_mode: None) -> None:
    from mso.features.torrent import tuning

    qb.st.prefs.update({"max_connec": 50000, "max_connec_per_torrent": 100000})
    ingest()
    old = operator.post("/api/v1/torrent/tuning/propose").json()["change"]
    assert old["risk"] == "toestemming_nodig" and old["status"] == "preview"  # manage mode: unchanged behaviour
    state.set_state("mode", "autopilot")
    res = tuning.propose("system")
    assert res["change"]["id"] != old["id"] and res["change"]["status"] == "applied"
    assert changes.get(old["id"])["status"] == "discarded"


def test_read_only_and_pause_block_every_automatic_write(qb: Qb, db: Any) -> None:
    from mso.features.torrent import autopilot as tap
    from mso.features.torrent import tuning

    qb.st.prefs.update({"max_connec": 50000, "max_connec_per_torrent": 100000})
    _add_suspicious(qb)
    ingest()
    for mode, paused in (("read_only", False), ("autopilot", True)):
        state.set_state("mode", mode)
        state.set_state("paused", paused)
        out = tap.run()
        assert out["prevention"].get("auto") is None, out
        assert out["remedies"]["applied"] == []
        res = tuning.propose("system")
        assert res.get("auto") is None or "niet" in str(res.get("auto")), res
        assert not qb.calls("setPreferences") and not qb.calls("reannounce") and not qb.calls("recheck")
        assert qb.st.prefs["max_connec"] == 50000 and qb.st.prefs["excluded_file_names_enabled"] is False
        assert qb.st.deletes == []
    # proposals are kept ready (a preview is not a write)
    assert db.scalar("SELECT count(*) FROM change_sets WHERE dedupe_key='torrent-prevention:excluded-files' "
                     "AND status='preview'") == 1
    assert len(_open_cleanups(db)) == 1


# ------------------------------------------------------------------ 3. one up-to-date cleanup proposal
def test_cleanup_proposal_dedupe_refresh_and_p5(qb: Qb, viewer: Any, db: Any, manage_mode: None) -> None:
    from mso.features.torrent import autopilot as tap

    hs = _add_suspicious(qb)
    ingest()
    first = tap.maintain_cleanup_proposal()
    assert first["created"] is True
    again = tap.maintain_cleanup_proposal()
    assert again["change"]["id"] == first["change"]["id"] and len(_open_cleanups(db)) == 1
    # P5: a single-file torrent is flagged by name AND file; the preview shows the name once
    rows = {r["Naam"]: r for r in first["change"]["impact"]["Torrents"]}
    exe = "Criminal Minds S19E06 1080p WEB h264-GRACE.exe"
    assert rows[exe]["Verdacht bestand"] == exe
    items = {i["hash"]: i for i in viewer.get("/api/v1/torrent/suspicious").json()["items"]}
    assert items[hs["exe"]]["files"] == [exe]
    # the "Wacht op jou" source: an open change needing approval, with a clear title
    open_ch = viewer.get("/api/v1/torrent/suspicious").json()["open_change"]
    assert open_ch["id"] == first["change"]["id"] and open_ch["title"] == "Alle 2 verdachte torrents opruimen"

    # the set changes → ONE new proposal, the old one discarded
    _add_suspicious(qb, {"new": "The Bear S04E01 1080p WEB h264-GRP.scr"})
    ingest()
    ref = tap.maintain_cleanup_proposal()
    assert ref["refreshed"] is True and ref["change"]["id"] != first["change"]["id"]
    opens = _open_cleanups(db)
    assert [o["id"] for o in opens] == [ref["change"]["id"]] and "Alle 3" in opens[0]["title"]
    assert changes.get(first["change"]["id"])["status"] == "discarded"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='torrent.cleanup_refreshed'") == 1

    # nothing suspicious any more → the proposal is withdrawn
    for h in list(qb.st.torrents):
        if qb.st.torrents[h]["name"].endswith((".exe", ".scr")):
            qb.st.torrents.pop(h)
    ingest()
    tap.maintain_cleanup_proposal()
    assert _open_cleanups(db) == []
    assert qb.st.deletes == []


def test_cleanup_preview_says_sonarr_can_regrab(qb: Qb, operator: Any, manage_mode: None) -> None:
    _add_suspicious(qb)
    ingest()
    ch = operator.post("/api/v1/torrent/suspicious/cleanup-preview").json()
    blk = ch["impact"]["Blokkeren"]
    assert "Sonarr kan deze releases opnieuw grijpen" in blk
    assert "voorkomt dat de bestanden opnieuw binnenkomen" in blk and "staat nog uit" in blk


# ------------------------------------------------------------------ 5. safe remedies in Autopilot
def test_autopilot_remedies_reannounce_and_recheck_bounded(
    qb: Qb, admin: Any, db: Any, autopilot_mode: None
) -> None:
    from mso.features.torrent import autopilot as tap

    ingest()
    rows = db.fetchall("SELECT hash, name, state, diagnosis FROM torrent_state WHERE NOT removed")
    cands, _ = tap.remedy_candidates(rows)
    kinds = {c["action"] for c in cands}
    assert kinds == {"reannounce", "recheck"}, cands
    missing = next(c["hash"] for c in cands if c["action"] == "recheck")
    assert qb.st.torrents[missing]["state"] == "missingFiles"

    res = tap.auto_remedies()
    assert {a["action"] for a in res["applied"]} == kinds and all(a["result"] == "toegepast" for a in res["applied"])
    assert qb.calls("reannounce") and qb.calls("recheck")
    for a in res["applied"]:
        ch = changes.get(a["change_id"])
        assert ch["status"] == "applied" and ch["risk"] == "automatisch_veilig" and ch["applied_by"] in AUTO_ACTORS
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='torrent.autopilot_remedy'") == len(res["applied"])
    # back-off: not again within the window
    assert tap.auto_remedies()["applied"] == []
    assert qb.st.deletes == []


def test_autopilot_remedies_budget_and_storage_guard(
    qb: Qb, admin: Any, db: Any, autopilot_mode: None
) -> None:
    from mso.features.torrent import autopilot as tap

    r = admin.put("/api/v1/settings", json={"values": {"torrent.autopilot_remedies_per_hour": 1}})
    assert r.status_code == 200, r.text
    ingest()
    assert len(tap.auto_remedies()["applied"]) == 1
    assert "maximum" in tap.auto_remedies()["reason"]
    # several torrents missing files at once → storage problem → no recheck at all
    rows = [{"hash": f"{i:040x}", "name": f"T{i}", "state": "missingFiles",
             "diagnosis": {"findings": [{"code": "missing_files"}]}} for i in range(3)]
    cands, notes = tap.remedy_candidates(rows)
    assert cands == [] and "opslag" in notes[0]
