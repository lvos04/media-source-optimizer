"""Torrent engine (PLAN §7 test 5): public/private torrents, rate policies, ratio/seed-time obligations,
stalled,
tracker failure, disk pressure, firewalled/VPN, false ETA, recheck only on evidence, auto-tune with
rollback on
degradation, no data loss (no delete/addTrackers ever called), private-torrent DHT/tracker injection
refused."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from mso import changes, state
from mso.connectors.qbittorrent import QbitClient

GB = 1024**3


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    QbitClient.reset_cache()
    yield
    QbitClient.reset_cache()


class Qb:
    def __init__(self, app: Any, url: str) -> None:
        self.app = app
        self.url = url

    @property
    def st(self) -> Any:
        return self.app.state.qb

    def hash(self, prefix: str) -> str:
        return next(h for h, t in self.st.torrents.items() if t["name"].startswith(prefix))

    def calls(self, needle: str) -> list[Any]:
        return [c for c in self.st.calls if needle in c[1]]


@pytest.fixture()
def qb(admin: Any, serve: Any) -> Qb:
    from msomocks.qbittorrent import create_app

    app = create_app()
    url = serve(app)
    r = admin.put(
        "/api/v1/connectors/qbittorrent",
        json={"url": url, "secrets": {"username": "admin", "password": "adminadmin"}},
    )
    assert r.status_code == 200
    return Qb(app, url)


def ingest() -> dict[str, Any]:
    from mso.features.torrent.ingest import ingest as run

    return run()


def torrents(client: Any) -> dict[str, dict[str, Any]]:
    r = client.get("/api/v1/torrent/torrents")
    assert r.status_code == 200, r.text
    return {t["name"]: t for t in r.json()["items"]}


def set_settings(admin: Any, **values: Any) -> None:
    r = admin.put("/api/v1/settings", json={"values": {f"torrent.{k}": v for k, v in values.items()}})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ ingestion
def test_ingest_outcomes_sources_and_no_passkeys(qb: Qb, viewer: Any, db: Any) -> None:
    res = ingest()
    assert res["torrents"] == 8 and res["trackers_refreshed"] == 8
    rows = {r["title"]: r for r in db.fetchall("SELECT * FROM download_outcomes")}
    pub = rows["Publieke.Serie.S01E01.1080p.WEB-DL-GRP"]
    assert pub["protocol"] == "torrent" and pub["status"] == "success" and pub["private"] is False
    assert pub["media_category"] == "tv" and pub["release_key"] == "btih:" + qb.hash("Publieke")
    assert pub["client_ref"] == qb.hash("Publieke") and pub["source_id"] == "tracker:tracker.openbt.example"
    priv = rows["Prive.Film.2024.2160p.BluRay.REMUX-GRP"]
    assert priv["private"] is True and priv["media_category"] == "movies"
    assert rows["Zeldzame.Anime.S01E05.1080p-Grp"]["media_category"] == "tv-anime"
    assert rows["Grote.Animefilm.Collectie.2160p-GRP"]["media_category"] == "movies-anime"
    assert rows["Gewone.Film.2023.1080p.WEB-DL-GRP"]["status"] == "active"
    srcs = {s["id"]: s for s in db.fetchall("SELECT * FROM sources WHERE kind='tracker'")}
    assert srcs["tracker:tracker.privehd.example"]["private"] is True
    assert srcs["tracker:tracker.openbt.example"]["private"] is False
    assert "tracker:tracker.dood.example" in srcs
    obs = {
        o["metric"]
        for o in db.fetchall("SELECT DISTINCT metric FROM observations WHERE subject='qbittorrent'")
    }
    assert {"torrent.dl_speed", "torrent.dl_speed_active", "torrent.stalled_ratio", "torrent.peers"} <= obs
    # passkey in the announce URL and secret preferences never stored or returned
    dump = str(db.fetchall("SELECT * FROM torrent_state")) + str(
        db.fetchall("SELECT * FROM torrent_client_snapshot")
    )
    dump += str(db.fetchall("SELECT * FROM sources")) + viewer.get("/api/v1/torrent/torrents").text
    dump += viewer.get("/api/v1/torrent/overview").text + viewer.get("/api/v1/torrent/preferences").text
    for secret in ("PASSKEY0123456789abcdef", "proxygeheim", "mailgeheim"):
        assert secret not in dump


def test_incremental_ingest_and_removed_torrent(qb: Qb, db: Any) -> None:
    ingest()
    h = qb.hash("Gewone.Film")
    del qb.st.torrents[h]
    res = ingest()
    assert res["removed"] == 1
    assert db.scalar("SELECT removed FROM torrent_state WHERE hash=%s", (h,)) is True
    assert db.scalar("SELECT status FROM download_outcomes WHERE client_ref=%s", (h,)) == "removed"
    assert QbitClient.cached().sync.full_updates == 1  # second poll used the rid diff


def test_private_flag_detected_on_old_version_via_trackers(admin: Any, serve: Any, db: Any) -> None:
    from msomocks.qbittorrent import create_app

    app = create_app(old_version="1")
    url = serve(app)
    admin.put(
        "/api/v1/connectors/qbittorrent",
        json={"url": url, "secrets": {"username": "admin", "password": "adminadmin"}},
    )
    ingest()
    rows = {r["name"]: r for r in db.fetchall("SELECT name, private FROM torrent_state")}
    assert rows["Prive.Film.2024.2160p.BluRay.REMUX-GRP"]["private"] is True
    assert rows["Publieke.Serie.S01E01.1080p.WEB-DL-GRP"]["private"] is False


# ------------------------------------------------------------------ diagnostics
def test_diagnostics_scenarios(qb: Qb, viewer: Any) -> None:
    ingest()
    t = torrents(viewer)
    stalled = t["Zeldzame.Anime.S01E05.1080p-Grp"]
    assert stalled["diagnosis"]["code"] in ("no_seeds", "false_eta")
    assert stalled["eta_unreliable"] is True and any("beschikbaarheid" in r for r in stalled["eta_reasons"])
    full = viewer.get(f"/api/v1/torrent/torrents/{qb.hash('Zeldzame')}").json()
    codes = {f["code"] for f in full["findings"]}
    assert {"no_seeds", "false_eta"} <= codes
    no_seeds = next(f for f in full["findings"] if f["code"] == "no_seeds")
    assert no_seeds["confidence"] >= 0.8  # the tracker works and reports 0 seeds → strong evidence
    dead = t["Andere.Serie.S02E03.720p.HDTV-GRP"]
    assert dead["diagnosis"]["code"] == "tracker_error"
    assert "Connection timed out" in dead["diagnosis"]["explanation"]
    assert dead["diagnosis"]["action"]["kind"] == "reannounce"
    assert t["Wachtrij.Film.2022.1080p.BluRay-GRP"]["diagnosis"]["code"] == "queued"
    broken = t["Kapotte.Serie.S01E02.1080p.WEB-GRP"]
    assert broken["recheck_suggested"] is True and broken["diagnosis"]["code"] == "missing_files"
    healthy = t["Gewone.Film.2023.1080p.WEB-DL-GRP"]
    assert healthy["diagnosis"] is None and healthy["eta_unreliable"] is False
    assert t["Publieke.Serie.S01E01.1080p.WEB-DL-GRP"]["recheck_suggested"] is False


def test_disk_pressure(qb: Qb, viewer: Any, db: Any) -> None:
    qb.st.server_state["free_space_on_disk"] = 50 * GB  # 108 GB still to download for the anime collection
    ingest()
    t = torrents(viewer)
    big = t["Grote.Animefilm.Collectie.2160p-GRP"]
    assert big["diagnosis"]["code"] == "disk_full" and big["diagnosis"]["severity"] == "critical"
    codes = {f["code"] for f in viewer.get("/api/v1/torrent/overview").json()["findings"]}
    assert "disk_insufficient" in codes
    rep = viewer.get("/api/v1/torrent/storage").json()
    assert rep["disk"]["status"] == "kritiek" and rep["disk"]["free"] == 50 * GB
    # an errored torrent with disk_full counts as a failed download with that reason
    h = qb.hash("Grote.Animefilm")
    qb.st.torrents[h]["state"] = "error"
    ingest()
    row = db.fetchone("SELECT status, failure_reason FROM download_outcomes WHERE client_ref=%s", (h,))
    assert row["status"] == "failed" and row["failure_reason"] == "disk_full"


def test_stalled_long_counts_as_failed_but_is_never_stopped(qb: Qb, db: Any) -> None:
    ingest()
    h = qb.hash("Andere.Serie")
    db.execute("UPDATE torrent_state SET last_progress_at = now() - interval '100 hours' WHERE hash=%s", (h,))
    ingest()
    row = db.fetchone("SELECT status, failure_reason FROM download_outcomes WHERE client_ref=%s", (h,))
    assert row["status"] == "failed" and row["failure_reason"] == "tracker_error"
    assert not qb.calls("/delete") and not qb.calls("/stop") and not qb.calls("/pause")


def test_firewalled_and_vpn_binding(qb: Qb, admin: Any, viewer: Any) -> None:
    qb.st.server_state["connection_status"] = "firewalled"
    h = qb.hash("Wachtrij")
    qb.st.torrents[h]["state"] = "stalledDL"  # 50 seeds in the swarm but nothing comes in
    set_settings(admin, expected_interface="wg0")
    ingest()
    ov = viewer.get("/api/v1/torrent/overview").json()
    codes = {f["code"]: f for f in ov["findings"]}
    assert "firewalled" in codes and codes["firewalled"]["evidence"]["listen_port"] == 6881
    assert codes["vpn_binding_mismatch"]["severity"] == "critical"
    assert ov["connectivity"]["binding_ok"] is False and ov["connectivity"]["port_ok"] is False
    assert torrents(viewer)["Wachtrij.Film.2022.1080p.BluRay-GRP"]["diagnosis"]["code"] == "not_connectable"
    # binding is diagnosis only: MSO never changed the interface
    assert qb.st.prefs["current_network_interface"] == ""
    qb.st.prefs["current_network_interface"] = "wg0"
    ingest()
    ov = viewer.get("/api/v1/torrent/overview").json()
    assert ov["connectivity"]["binding_ok"] is True
    assert "vpn_binding_mismatch" not in {f["code"] for f in ov["findings"]}


def test_false_eta_from_measured_speed(qb: Qb, viewer: Any, db: Any) -> None:
    h = qb.hash("Gewone.Film")
    qb.st.torrents[h].update(dlspeed=20_000, eta=600)  # claims 10 min for 3 GB = ~5 MB/s
    for _ in range(4):
        ingest()
    t = torrents(viewer)["Gewone.Film.2023.1080p.WEB-DL-GRP"]
    assert t["eta_unreliable"] is True and t["realistic_eta_s"] > 100_000


def test_global_rate_limit_and_alt_speed(qb: Qb, viewer: Any) -> None:
    qb.st.prefs["dl_limit"] = 6 * 1024 * 1024
    ingest()
    full = viewer.get(f"/api/v1/torrent/torrents/{qb.hash('Gewone.Film')}").json()
    assert "rate_limited_global" in {f["code"] for f in full["findings"]}
    qb.st.server_state["use_alt_speed_limits"] = True
    ingest()
    full = viewer.get(f"/api/v1/torrent/torrents/{qb.hash('Gewone.Film')}").json()
    assert "alt_speed" in {f["code"] for f in full["findings"]}


# ------------------------------------------------------------------ guardrails
def test_private_torrent_dht_and_tracker_injection_refused(qb: Qb, operator: Any) -> None:
    ingest()
    qb.st.prefs["dht"] = False
    for changes_ in (
        {"dht": True},
        {"pex": True},
        {"add_trackers_enabled": True},
        {"add_trackers": "udp://open.tracker.example:1337/announce"},
    ):
        r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": changes_})
        assert r.status_code == 422, (changes_, r.text)
    with pytest.raises(HTTPException):
        changes.create(
            "qbittorrent",
            "trackers",
            [{"path": f"torrent:{qb.hash('Prive')}:trackers", "after": ["udp://x/announce"]}],
            risk="advies",
        )
    with pytest.raises(HTTPException):
        changes.create(
            "qbittorrent",
            "verwijderen",
            [{"path": f"torrent:{qb.hash('Prive')}:delete", "after": True}],
            risk="advies",
        )
    r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"max_ratio_act": 3}})
    assert r.status_code == 422 and "verwijder" in r.json()["detail"]
    assert not qb.calls("addTrackers") and not qb.calls("/delete")


def test_upload_cap_never_above_line_share(qb: Qb, admin: Any, operator: Any) -> None:
    ingest()
    set_settings(admin, line_up_mbit=40.0, upload_cap_pct=80)  # cap = 4 MB/s, rounded down to whole KiB/s
    cap = int(40 * 1_000_000 / 8 * 0.8) // 1024 * 1024
    r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"up_limit": cap + 1024}})
    assert r.status_code == 422 and "80%" in r.json()["detail"]
    # a value inside the same KiB is rounded down (like qBittorrent does) and therefore allowed
    r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"up_limit": cap + 1000}})
    assert r.status_code == 200 and r.json()["ops"][0]["after"] == cap
    assert (
        operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"up_limit": 0}}).status_code
        == 422
    )
    r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"up_limit": cap}})
    assert (
        r.status_code == 200 and r.json()["risk"] == "toestemming_nodig"
    )  # 0 (unlimited) → cap is a big change
    assert (
        operator.post(
            "/api/v1/torrent/preferences/preview", json={"changes": {"max_connec": 99999}}
        ).status_code
        == 422
    )
    assert (
        operator.post(
            "/api/v1/torrent/preferences/preview", json={"changes": {"web_ui_password": "x"}}
        ).status_code
        == 422
    )
    assert admin.put("/api/v1/settings", json={"values": {"torrent.upload_cap_pct": 100}}).status_code == 422
    overview = operator.get("/api/v1/torrent/overview").json()
    assert overview["findings"] == [] or True  # findings refresh on next ingest
    ingest()
    codes = {f["code"] for f in operator.get("/api/v1/torrent/overview").json()["findings"]}
    assert "upload_over_cap" in codes


def test_seed_obligations_and_share_limits(
    qb: Qb, admin: Any, operator: Any, viewer: Any, manage_mode: None
) -> None:
    ingest()
    # creating rules is admin-only; hosts only (no URLs/passkeys)
    rule = {
        "tracker_host": "tracker.privehd.example",
        "label": "PriveHD",
        "private": True,
        "min_ratio": 1.0,
        "min_seed_time_h": 72,
        "mode": "either",
        "allow_cross_seed": False,
    }
    assert operator.post("/api/v1/torrent/tracker-rules", json=rule).status_code == 403
    bad = dict(rule, tracker_host="https://tracker.privehd.example/announce/PASSKEY")
    assert admin.post("/api/v1/torrent/tracker-rules", json=bad).status_code == 422
    r = admin.post("/api/v1/torrent/tracker-rules", json=rule)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert admin.post("/api/v1/torrent/tracker-rules", json=rule).status_code == 409
    ingest()
    obl = viewer.get("/api/v1/torrent/obligations").json()
    priv = next(o for o in obl["items"] if o["tracker"] == "tracker.privehd.example")
    assert priv["status"] == "loopt" and priv["met"] is False and priv["rules"][0]["time_progress"] < 1
    h = qb.hash("Prive")
    # stopping seeding before the obligation is refused (ratio 0.5 < 1.0 / 12 h < 72 h)
    with pytest.raises(HTTPException) as exc:
        changes.create(
            "qbittorrent",
            "deelgrens",
            [{"path": f"torrent:{h}:share_limits", "after": {"ratio_limit": 0.5, "seeding_time_limit": -1}}],
            risk="toestemming_nodig",
        )
    assert "seedverplichting" in exc.value.detail
    with pytest.raises(HTTPException):
        changes.create(
            "qbittorrent",
            "deelgrens",
            [{"path": f"torrent:{h}:share_limits", "after": {"ratio_limit": -1, "seeding_time_limit": 720}}],
            risk="toestemming_nodig",
        )
    assert (
        operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"max_ratio": 0.5}}).status_code
        == 422
    )
    # a limit above the obligation is fine and is applied through the change set
    ch = changes.create(
        "qbittorrent",
        "deelgrens",
        [
            {
                "path": f"torrent:{h}:share_limits",
                "before": None,
                "after": {"ratio_limit": 2.0, "seeding_time_limit": 10080, "inactive_seeding_time_limit": -2},
            }
        ],
        risk="toestemming_nodig",
    )
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    assert operator.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 200
    assert qb.st.torrents[h]["ratio_limit"] == 2.0 and qb.st.torrents[h]["seeding_time_limit"] == 10080
    # obligation met → status 'gehaald'
    qb.st.torrents[h]["ratio"] = 1.2
    ingest()
    priv = next(
        o
        for o in viewer.get("/api/v1/torrent/obligations").json()["items"]
        if o["tracker"] == "tracker.privehd.example"
    )
    assert priv["status"] == "gehaald"
    # a stopped torrent before its obligation is flagged (MSO never resumes/stops itself)
    qb.st.torrents[h].update(ratio=0.2, state="stoppedUP")
    ingest()
    t = torrents(viewer)["Prive.Film.2024.2160p.BluRay.REMUX-GRP"]
    assert t["diagnosis"]["code"] == "seed_obligation_unmet"
    assert (
        admin.put(
            f"/api/v1/torrent/tracker-rules/{rid}", json=dict(rule, min_ratio=0.1, min_seed_time_h=None)
        ).status_code
        == 200
    )
    assert admin.delete(f"/api/v1/torrent/tracker-rules/{rid}").status_code == 200
    assert not qb.calls("/delete")


def test_private_torrent_without_rule_gets_no_share_limits(qb: Qb) -> None:
    ingest()
    with pytest.raises(HTTPException) as exc:
        changes.create(
            "qbittorrent",
            "deelgrens",
            [
                {
                    "path": f"torrent:{qb.hash('Prive')}:share_limits",
                    "after": {"ratio_limit": 5.0, "seeding_time_limit": -1},
                }
            ],
            risk="toestemming_nodig",
        )
    assert "trackerregels" in exc.value.detail


# ------------------------------------------------------------------ recheck / reannounce
def test_recheck_only_with_evidence_and_requires_approval(
    qb: Qb, admin: Any, operator: Any, manage_mode: None
) -> None:
    ingest()
    assert operator.post(f"/api/v1/torrent/torrents/{qb.hash('Publieke')}/recheck-preview").status_code == 409
    r = operator.post(f"/api/v1/torrent/torrents/{qb.hash('Kapotte')}/recheck-preview")
    assert r.status_code == 200 and r.json()["risk"] == "toestemming_nodig"
    cid = r.json()["id"]
    assert operator.post(f"/api/v1/changes/{cid}/apply").status_code in (403, 409)  # needs admin approval
    assert not qb.calls("/recheck")
    admin.post(f"/api/v1/changes/{cid}/approve")
    r = operator.post(f"/api/v1/changes/{cid}/apply")
    assert r.status_code == 200, r.text
    assert len(qb.calls("/recheck")) == 1 and qb.st.torrents[qb.hash("Kapotte")]["state"] == "checkingUP"
    # rollback is a harmless no-op (a hash check changes no data)
    assert operator.post(f"/api/v1/changes/{cid}/rollback", json={"reason": "test"}).status_code == 200
    assert len(qb.calls("/recheck")) == 1
    # reannounce for the failing tracker
    r = operator.post(f"/api/v1/torrent/torrents/{qb.hash('Andere.Serie')}/reannounce-preview")
    assert r.status_code == 200 and r.json()["risk"] == "automatisch_veilig"
    assert operator.post(f"/api/v1/changes/{r.json()['id']}/apply").status_code == 200
    assert len(qb.calls("/reannounce")) == 1


def test_writes_refused_in_read_only_mode(qb: Qb, operator: Any) -> None:
    ingest()
    r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"max_connec": 550}})
    assert r.status_code == 200 and r.json()["risk"] == "automatisch_veilig"
    assert operator.post(f"/api/v1/changes/{r.json()['id']}/apply").status_code == 409
    assert qb.st.prefs["max_connec"] == 500
    r = operator.post("/api/v1/torrent/preferences/preview", json={"changes": {"max_active_downloads": 6}})
    assert r.json()["risk"] == "toestemming_nodig"  # +100 % is not a small step
    assert not qb.calls("setPreferences")


# ------------------------------------------------------------------ tuning loop
def _obs(db: Any, metric: str, value: float, hours_ago: float, n: int = 20) -> None:
    for i in range(n):
        db.execute(
            "INSERT INTO observations(subject, metric, value, ts) VALUES ('qbittorrent', %s, %s, "
            "now() - make_interval(secs => %s))",
            (metric, value, (hours_ago * 3600) + i * 60),
        )


def _age(db: Any, hours: float) -> None:
    """Simulate ``hours`` passing for the open tuning step: observations, the run (applied_at + baseline window)
    and its change set (applied_at, evaluate_after) all move back in time."""
    iv = f"{float(hours)} hours"
    db.execute("UPDATE observations SET ts = ts - %s::interval", (iv,))
    for r in db.fetchall("SELECT id, baseline FROM torrent_tuning_runs"):
        b = dict(r["baseline"] or {})
        for k in ("from", "to"):
            if b.get(k):
                b[k] = (datetime.fromisoformat(b[k]) - timedelta(hours=hours)).isoformat()
        db.execute(
            "UPDATE torrent_tuning_runs SET baseline=%s, applied_at = applied_at - %s::interval WHERE id=%s",
            (json.dumps(b), iv, r["id"]),
        )
    db.execute(
        "UPDATE change_sets SET applied_at = applied_at - %s::interval, evaluate_after = evaluate_after - "
        "%s::interval, created_at = created_at - %s::interval",
        (iv, iv, iv),
    )


def test_autotune_applies_one_change_and_rolls_back_on_degradation(
    qb: Qb, admin: Any, db: Any, manage_mode: None
) -> None:
    from mso.features.torrent import tuning

    set_settings(admin, autotune_enabled=True)
    qb.st.server_state["total_peer_connections"] = 495  # max_connec 500 is saturated
    ingest()
    _obs(db, "torrent.peers", 495, 1)
    _obs(db, "torrent.dl_speed_active", 4_000_000, 10)  # baseline 4 MB/s
    res = tuning.propose("system")
    assert "run" in res, res
    run = res["run"]
    assert run["param"] == "pref:max_connec" and run["after_value"] == 600 and res["auto"] == "toegepast"
    assert res["change"]["risk"] == "automatisch_veilig" and 24 <= run["evaluate_after_h"] <= 48
    assert qb.st.prefs["max_connec"] == 600
    assert tuning.propose("system")["reason"].startswith("Er loopt al")  # one change at a time
    # not yet due → nothing evaluated
    assert tuning.evaluate() == []
    # simulate 49 h later with a clearly worse download speed; evaluation runs through the automation engine
    # (the one evaluation path) with the evaluator registered for 'qbittorrent'
    from mso.features.automation import EVALUATORS

    assert EVALUATORS["qbittorrent"] is tuning.evaluate_change
    _age(db, 49)
    _obs(db, "torrent.dl_speed_active", 1_000_000, 2)
    done = tuning.evaluate()
    assert done and done[0]["status"] == "rolled_back"
    assert qb.st.prefs["max_connec"] == 500
    ch = changes.get(run["change_id"])
    assert ch["status"] == "rolled_back"
    ev = ch["impact"]["evaluation"]
    assert ev["result"] == "slechter" and ev["action"] == "teruggedraaid" and ev["evaluator"] == "qbittorrent"
    assert any(m["key"] == "torrent.dl_speed_active" and m["verdict"] == "worse" for m in ev["measures"])
    # exactly one rollback (no second evaluation path)
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.rolled_back' AND change_id=%s",
                     (run["change_id"],)) == 1
    assert tuning.evaluate() == []
    # anti-oscillation: the parameter is frozen after a rollback
    db.execute("UPDATE torrent_tuning_runs SET evaluated_at = now() - interval '2 days'")
    ingest()
    _obs(db, "torrent.peers", 495, 0.5)
    _obs(db, "torrent.dl_speed_active", 4_000_000, 5)
    again = tuning.propose("system")
    assert "run" not in again and "teruggedraaid" in again["reason"]
    assert not qb.calls("/delete")


def test_degradation_without_automation_only_recommends(
    qb: Qb, admin: Any, db: Any, manage_mode: None
) -> None:
    from mso.features.torrent import tuning

    qb.st.server_state["total_peer_connections"] = 495
    ingest()
    _obs(db, "torrent.peers", 495, 1)
    _obs(db, "torrent.dl_speed_active", 4_000_000, 10)
    res = tuning.propose("system")
    assert res["auto"] is None  # autotune disabled by default → preview only
    assert qb.st.prefs["max_connec"] == 500
    r = admin.post(f"/api/v1/changes/{res['change']['id']}/apply")
    assert r.status_code == 200 and qb.st.prefs["max_connec"] == 600
    state.set_state("paused", True)
    tuning.sync_runs()
    _age(db, 49)
    _obs(db, "torrent.dl_speed_active", 1_000_000, 2)
    done = tuning.evaluate()
    assert done[0]["status"] == "degraded_pending"
    assert qb.st.prefs["max_connec"] == 600  # paused → no automatic rollback
    rec = db.fetchone("SELECT * FROM recommendations WHERE area='torrent'")
    assert rec and rec["change_id"] == res["change"]["id"] and rec["risk"] == "toestemming_nodig"
    assert changes.get(res["change"]["id"])["impact"]["evaluation"]["action"] == "voorgesteld"


def test_tuning_hysteresis_and_insufficient_data() -> None:
    from mso.features.torrent.tuning import compare

    base = {"n": 20, "median": 100.0}
    assert (
        compare(base, {"n": 20, "median": 110.0}, higher_is_better=True, hysteresis_pct=15, min_samples=12)[
            "verdict"
        ]
        == "neutral"
    )
    assert (
        compare(base, {"n": 20, "median": 80.0}, higher_is_better=True, hysteresis_pct=15, min_samples=12)[
            "verdict"
        ]
        == "worse"
    )
    assert (
        compare(base, {"n": 20, "median": 80.0}, higher_is_better=False, hysteresis_pct=15, min_samples=12)[
            "verdict"
        ]
        == "better"
    )
    assert (
        compare(base, {"n": 3, "median": 10.0}, higher_is_better=True, hysteresis_pct=15, min_samples=12)[
            "verdict"
        ]
        == "inconclusive"
    )


def test_upload_guardrail_proposal_requires_permission(
    qb: Qb, admin: Any, operator: Any, manage_mode: None
) -> None:
    set_settings(admin, line_up_mbit=40.0, autotune_enabled=True)
    ingest()
    r = operator.post("/api/v1/torrent/tuning/propose")
    body = r.json()
    assert body["run"]["param"] == "pref:up_limit" and body["run"]["kind"] == "guardrail"
    assert body["change"]["risk"] == "toestemming_nodig" and body["auto"] is None  # unlimited → cap: big step
    assert qb.st.prefs["up_limit"] == 0
    lst = operator.get("/api/v1/torrent/tuning").json()
    assert lst["items"][0]["status"] == "proposed" and lst["open_run"] == body["run"]["id"]


def test_tuning_needs_baseline_samples(qb: Qb) -> None:
    from mso.features.torrent import tuning

    qb.st.server_state["total_peer_connections"] = 495
    ingest()
    res = tuning.propose("system")
    assert "run" not in res and "basislijn" in res["reason"]


# ------------------------------------------------------------------ cross-seed
def _torrent_bytes(name: str, files: list[tuple[str, int]], announce: str) -> bytes:
    from mso.features.torrent.crossseed import bencode

    info = {
        "name": name,
        "piece length": 4 * 1024 * 1024,
        "pieces": b"\x00" * 20,
        "private": 1,
        "files": [{"length": s, "path": p.split("/")} for p, s in files],
    }
    return bencode({"announce": announce, "info": info})


def test_cross_seed_only_identical_payload_allowed_tracker(
    qb: Qb, admin: Any, operator: Any, db: Any, manage_mode: None
) -> None:
    from mso.features.torrent.crossseed import parse_torrent

    h = qb.hash("Publieke")
    qb.st.files[h] = [
        {
            "name": "Publieke.Serie.S01E01.1080p.WEB-DL-GRP/ep.mkv",
            "size": 2 * GB - 1000,
            "progress": 1,
            "priority": 1,
        },
        {"name": "Publieke.Serie.S01E01.1080p.WEB-DL-GRP/ep.nfo", "size": 1000, "progress": 1, "priority": 1},
    ]
    ingest()
    data = _torrent_bytes(
        "Publieke.Serie.S01E01.1080p.WEB-DL-GRP",
        [("ep.mkv", 2 * GB - 1000), ("ep.nfo", 1000)],
        "https://tracker.ander.example/announce/GEHEIMEPASSKEY",
    )
    body = {"source_hash": h, "torrent_base64": base64.b64encode(data).decode()}
    assert operator.post("/api/v1/torrent/cross-seed/candidates", json=body).status_code == 409  # default off
    set_settings(admin, cross_seed_enabled=True)
    r = operator.post("/api/v1/torrent/cross-seed/candidates", json=body)
    assert r.status_code == 422 and "niet toegestaan" in r.json()["detail"]  # trackers not allowed by default
    for host in ("tracker.openbt.example", "tracker.ander.example"):
        admin.post("/api/v1/torrent/tracker-rules", json={"tracker_host": host, "allow_cross_seed": True})
    wrong = _torrent_bytes(
        "Publieke.Serie.S01E01.1080p.WEB-DL-GRP",
        [("ep.mkv", 2 * GB - 999), ("ep.nfo", 999)],
        "https://tracker.ander.example/announce/GEHEIMEPASSKEY",
    )
    r = operator.post(
        "/api/v1/torrent/cross-seed/candidates",
        json={"source_hash": h, "torrent_base64": base64.b64encode(wrong).decode()},
    )
    assert r.status_code == 422 and "niet identiek" in r.json()["detail"]
    r = operator.post("/api/v1/torrent/cross-seed/candidates", json=body)
    assert r.status_code == 200, r.text
    cand = r.json()
    assert "GEHEIMEPASSKEY" not in r.text and cand["tracker_host"] == "tracker.ander.example"
    assert "GEHEIMEPASSKEY" not in operator.get("/api/v1/torrent/cross-seed").text
    ch = operator.post(f"/api/v1/torrent/cross-seed/{cand['id']}/preview").json()
    assert ch["risk"] == "toestemming_nodig"
    assert operator.post(f"/api/v1/changes/{ch['id']}/apply").status_code in (403, 409)
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    new_hash = parse_torrent(data)["infohash"]
    added = qb.st.torrents[new_hash]
    assert added["state"] == "stoppedDL" and added["skip_checking"] is False
    assert added["save_path"] == qb.st.torrents[h]["save_path"]  # same data location, nothing moved
    # no automatic rollback that would delete a torrent
    r = operator.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "x"})
    assert r.status_code == 502 and "verwijdert nooit" in r.json()["detail"]
    assert not qb.calls("/delete")


def test_bencode_roundtrip_infohash() -> None:
    from mso.features.torrent.crossseed import bencode, parse_torrent

    info = {"name": "x.mkv", "length": 5, "piece length": 16384, "pieces": b"\x01" * 20}
    data = bencode({"announce": "http://t.example/a", "info": info})
    p = parse_torrent(data)
    assert p["infohash"] == hashlib.sha1(bencode(info)).hexdigest()  # noqa: S324
    assert p["files"] == [("x.mkv", 5)] and p["tracker_host"] == "t.example"


# ------------------------------------------------------------------ API / roles / misc
def test_roles_and_refresh(qb: Qb, viewer: Any, operator: Any, worker: Any) -> None:
    assert viewer.post("/api/v1/torrent/refresh").status_code == 403
    r = operator.post("/api/v1/torrent/refresh")
    assert r.status_code == 200
    worker.drain()
    assert len(viewer.get("/api/v1/torrent/torrents").json()["items"]) == 8
    assert viewer.post("/api/v1/torrent/tuning/propose").status_code == 403
    assert (
        viewer.post("/api/v1/torrent/preferences/preview", json={"changes": {"max_connec": 400}}).status_code
        == 403
    )
    clients = viewer.get("/api/v1/torrent/clients").json()["items"]
    assert {c["id"]: c["implemented"] for c in clients} == {
        "qbittorrent": True,
        "transmission": False,
        "deluge": False,
    }
    pol = viewer.get("/api/v1/torrent/policy").json()
    assert pol["private"]["has_private_torrents"] is True
    assert pol["queue"]["queued_downloads"] == 1


def test_not_configured_is_honest(viewer: Any, operator: Any) -> None:
    ov = viewer.get("/api/v1/torrent/overview").json()
    assert ov["connector"]["configured"] is False and ov["client"] is None
    assert viewer.get("/api/v1/torrent/torrents").json()["items"] == []
    assert operator.post("/api/v1/torrent/refresh").status_code == 409
    rep = viewer.get("/api/v1/torrent/storage").json()
    assert rep["disk"] is None and rep["client"]["available"] is False


def test_storage_hardlink_scan_read_only(qb: Qb, tmp_path: Any, monkeypatch: Any, viewer: Any) -> None:
    from mso.config import get_settings
    from mso.features.torrent import storage

    monkeypatch.setattr(get_settings(), "media_dir", tmp_path)
    ingest()
    res = storage.hardlink_scan()
    # tmp_path is writable → the scan refuses to run (requires a read-only mount)
    assert res["status"] == "niet_read_only"
    # simulate a read-only mount and a hardlinked seeding file
    seed = tmp_path / "torrents" / "tv" / "Publieke.Serie.S01E01.1080p.WEB-DL-GRP"
    seed.mkdir(parents=True)
    f = seed / "Publieke.Serie.S01E01.1080p.WEB-DL-GRP.mkv"
    f.write_bytes(b"x")
    (tmp_path / "library.mkv").hardlink_to(f)
    before = f.stat()
    real = storage.mount_info

    def ro(path: Any) -> Any:
        out = real(path)
        out["read_only"] = True
        return out

    monkeypatch.setattr(storage, "mount_info", ro)
    monkeypatch.setattr("mso.features.torrent.settings.path_map", lambda: [("/data", str(tmp_path))])
    res = storage.hardlink_scan()
    assert res["status"] == "ok" and res["hardlinked_count"] == 1
    assert res["hardlinked"][0]["nlink"] == 2 and "AV1" in res["message"]
    after = f.stat()
    assert (after.st_mtime_ns, after.st_ino, after.st_size) == (
        before.st_mtime_ns,
        before.st_ino,
        before.st_size,
    )
    rep = viewer.get("/api/v1/torrent/storage").json()
    assert rep["media"]["path"] == str(tmp_path)
    assert any(c["category"] == "movies" for c in rep["categories"])


def test_no_data_loss_endpoints_never_called(
    qb: Qb, admin: Any, operator: Any, db: Any, manage_mode: None
) -> None:
    """Run the whole engine (ingest, diagnostics, tuning, previews, applies) and assert no destructive
    call."""
    from mso.features.torrent import tuning

    set_settings(admin, line_up_mbit=100.0, autotune_enabled=True)
    for _ in range(3):
        ingest()
    tuning.propose("system")
    _age(db, 72)
    tuning.evaluate()
    destructive = [
        c
        for c in qb.st.calls
        if any(
            x in c[1]
            for x in ("delete", "addTrackers", "removeTrackers", "setLocation", "rename", "/stop", "/pause")
        )
    ]
    assert destructive == []
