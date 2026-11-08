"""1.6.0 WP-D — honest goals card and measurement.

- ``download_outcomes.failed_at``: set on the transition into 'failed', kept while failed, cleared after; backfill in
  migration 0604; windows use COALESCE(finished_at, failed_at) so a stuck torrent is not "failed in the last 24 h"
  forever (overview, evaluator failure series, …).
- failure ratio per episode/film (netto) next to per attempt (bruto), with n and "te weinig data" below 20.
- ``GET /api/v1/overview/goals``: trial start (setting / first Autopilot / setup), day X of 28, start value, now,
  target, daily trend (snapshots, reconstruction, local days), one blocker sentence per goal; suspicious torrents
  and failing servers over real HTTP against the qBittorrent and SABnzbd mocks.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from mso.domain import outcomes

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _row(status: str, at: datetime, title: str, ref: str | None = None, reason: str | None = None,
         protocol: str = "usenet") -> dict[str, Any]:
    return {"status": status, "at": at, "title": title, "client": "sabnzbd", "client_ref": ref or uuid.uuid4().hex,
            "failure_reason": reason, "protocol": protocol}


# ------------------------------------------------------------------ item identity + ratios (pure)
def test_item_key_uses_release_normalisation() -> None:
    k = outcomes.item_key
    assert k("Show.Name.S01E02.1080p.WEB-DL.x264-GRP") == k("Show Name S01E02 720p HDTV-OTHER") == "show name|s1e2"
    assert k("Show.Name.S01E03.1080p.WEB-DL.x264-GRP") != k("Show.Name.S01E02.1080p.WEB-DL.x264-GRP")
    assert k("[SubsPlease] Frieren - 05 (1080p) [ABCD].mkv") == k("[Erai-raws] Frieren - 05 [720p]") == "frieren|a5"
    assert k("Dune.Part.Two.2024.2160p.UHD.BluRay.x265-GRP") == k("Dune Part Two (2024) 1080p") == "dune part two|2024"
    assert k("Show.Name.S02.COMPLETE.1080p") == "show name|s2"
    assert k("a8f3k2l0q9w8e7r6t5y4") is None  # obfuscated: no identity
    assert k("") is None


def test_netto_counts_item_that_arrived_after_all_as_success() -> None:
    rows = []
    for i in range(1, 21):  # 20 episodes: failed once, then arrived via another release
        rows.append(_row("failed", NOW - timedelta(days=3), f"Serie.Een.S01E{i:02d}.1080p.WEB-DL-GRP",
                         reason="unpack_failed"))
        rows.append(_row("success", NOW - timedelta(days=2), f"Serie.Een.S01E{i:02d}.720p.HDTV-OTHER"))
    rows.append(_row("failed", NOW - timedelta(days=1), "Serie.Twee.S02E01.1080p-GRP", reason="incomplete"))
    rows.append(_row("failed", NOW - timedelta(hours=5), "Serie.Twee.S02E01.720p-GRP", reason="unpack_failed"))
    r = outcomes.failure_ratios(rows, NOW - timedelta(days=7), NOW)
    assert r["gross"] == {"failed": 22, "n": 42, "value": round(22 / 42, 4), "insufficient": False}
    net = r["net"]
    assert (net["failed"], net["n"], net["value"], net["insufficient"]) == (1, 21, round(1 / 21, 4), False)
    assert net["reasons"] == {"unpack_failed": 1}  # latest failure of the failed item
    assert net["by_protocol"] == {"usenet": {"n": 21, "failed": 1}}
    # as known at an earlier moment: the successes had not happened yet
    early = outcomes.failure_ratios(rows, NOW - timedelta(days=9), NOW - timedelta(days=2, hours=12))
    assert early["net"]["failed"] == 20 and early["net"]["n"] == 20


def test_ratio_insufficient_below_20_and_success_before_window_counts() -> None:
    rows = [_row("success", NOW - timedelta(days=20), "Film.2020.720p-GRP"),
            _row("failed", NOW - timedelta(days=1), "Film.2020.2160p-GRP", reason="incomplete")]
    r = outcomes.failure_ratios(rows, NOW - timedelta(days=7), NOW)
    assert r["gross"]["value"] == 1.0 and r["gross"]["insufficient"] is True
    assert r["net"]["failed"] == 0 and r["net"]["n"] == 1 and r["net"]["insufficient"] is True
    assert outcomes.failure_ratios([], NOW - timedelta(days=7), NOW)["net"]["value"] is None


def test_retry_links_merge_obfuscated_jobs() -> None:
    rows = [_row("failed", NOW - timedelta(days=1), "abcdef0123456789abcdef", ref="nzo_old", reason="incomplete"),
            _row("success", NOW - timedelta(hours=2), "fedcba9876543210fedcba", ref="nzo_new")]
    assert outcomes.failure_ratios(rows, NOW - timedelta(days=7), NOW)["net"]["failed"] == 1
    linked = outcomes.failure_ratios(rows, NOW - timedelta(days=7), NOW, links=[("nzo_old", "nzo_new")])
    assert linked["net"]["failed"] == 0 and linked["net"]["n"] == 1


def test_local_days_follow_the_configured_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    from mso.features.overview import goals

    monkeypatch.delenv("TZ", raising=False)
    tz = goals.local_tz()
    assert getattr(tz, "key", None) == "Europe/Amsterdam"
    # summer time: local midnight = 22:00 UTC; winter time: 23:00 UTC
    assert goals.day_end(date(2026, 10, 24), tz) == datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    assert goals.day_start(date(2026, 11, 8), tz) == datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    monkeypatch.setenv("TZ", "UTC")
    assert goals.day_start(date(2026, 11, 8), goals.local_tz()) == datetime(2026, 11, 8, tzinfo=UTC)
    monkeypatch.setenv("TZ", "Geen/Zone")
    assert getattr(goals.local_tz(), "key", None) == "Europe/Amsterdam"


# ------------------------------------------------------------------ failed_at
def _up(ref: str, status: str, **kw: Any) -> int:
    row = {"protocol": "torrent", "client": "qbittorrent", "client_ref": ref, "title": f"Serie.S01E01.{ref}",
           "status": status} | kw
    return outcomes.upsert_outcome(row)


def test_failed_at_set_on_transition_kept_and_cleared(db: Any) -> None:
    _up("t1", "active")
    assert db.scalar("SELECT failed_at FROM download_outcomes WHERE client_ref='t1'") is None
    _up("t1", "failed", failure_reason="stalled")
    first = db.scalar("SELECT failed_at FROM download_outcomes WHERE client_ref='t1'")
    assert first is not None and abs((datetime.now(UTC) - first).total_seconds()) < 60
    db.execute("UPDATE download_outcomes SET failed_at = now() - interval '3 days' WHERE client_ref='t1'")
    _up("t1", "failed", failure_reason="stalled")  # re-observed: the first moment stays
    kept = db.scalar("SELECT failed_at FROM download_outcomes WHERE client_ref='t1'")
    assert datetime.now(UTC) - kept > timedelta(days=2, hours=23)
    _up("t1", "active")  # progress again: no longer failed
    assert db.scalar("SELECT failed_at FROM download_outcomes WHERE client_ref='t1'") is None
    known = datetime.now(UTC) - timedelta(hours=30)
    _up("t1", "failed", failure_reason="stalled", failed_at=known)  # a writer that knows the moment
    assert db.scalar("SELECT failed_at FROM download_outcomes WHERE client_ref='t1'") == known
    _up("t2", "failed", failure_reason="client_failure", failed_at=known)  # insert as failed
    assert db.scalar("SELECT failed_at FROM download_outcomes WHERE client_ref='t2'") == known
    _up("t3", "success", finished_at=known)
    assert db.scalar("SELECT failed_at FROM download_outcomes WHERE client_ref='t3'") is None


def test_stuck_torrent_is_not_a_recent_failure_forever(db: Any, viewer: Any) -> None:
    from mso.features.automation.evaluators import failure_series

    long_ago = datetime.now(UTC) - timedelta(days=10)
    _up("stuck", "failed", failure_reason="stalled", failed_at=long_ago)
    _up("stuck", "failed", failure_reason="stalled")  # every ingest refreshes observed_at
    _up("fresh", "failed", failure_reason="stalled", failed_at=datetime.now(UTC) - timedelta(hours=30))
    f = viewer.get("/api/v1/overview").json()["failures"]
    assert f["last_7d"] == 1 and f["last_24h"] == 0
    now = datetime.now(UTC)
    assert failure_series(now - timedelta(hours=24), now) == []
    assert failure_series(now - timedelta(days=7), now) == [1.0]


def test_migration_backfill_and_down_up(db: Any) -> None:
    from mso.db import load_migrations

    mig = next(m for m in load_migrations() if m.version == 604)
    now = datetime.now(UTC)
    outcomes.upsert_outcome({"protocol": "usenet", "client": "sabnzbd", "client_ref": "u1", "title": "x",
                             "status": "failed", "finished_at": now - timedelta(days=2)})
    _up("aaaa", "failed", failure_reason="stalled", started_at=now - timedelta(days=20))
    _up("bbbb", "failed", failure_reason="false_positive")
    _up("cccc", "failed", failure_reason="client_failure", started_at=now - timedelta(days=9))
    _up("dddd", "success")
    db.execute("INSERT INTO torrent_state(hash, name, first_seen, last_progress_at) VALUES "
               "('aaaa','a', now() - interval '20 days', now() - interval '10 days'), "
               "('bbbb','b', now() - interval '4 days', now() - interval '1 day')")
    db.execute("INSERT INTO settings(key, value) VALUES ('torrent.stalled_fail_hours', '48')")
    db.execute("UPDATE download_outcomes SET failed_at = NULL")
    db.execute(mig.up)
    got = {r["client_ref"]: r["failed_at"] for r in db.fetchall("SELECT client_ref, failed_at FROM download_outcomes")}
    assert got["u1"] == now - timedelta(days=2)  # completion time
    assert abs((got["aaaa"] - (now - timedelta(days=8))).total_seconds()) < 120  # last progress + 48 h
    assert abs((got["bbbb"] - (now - timedelta(days=4))).total_seconds()) < 120  # first sighting
    assert abs((got["cccc"] - (now - timedelta(days=9))).total_seconds()) < 120  # no torrent state: start time
    assert got["dddd"] is None
    db.execute(mig.down)
    assert db.scalar("SELECT count(*) FROM information_schema.columns WHERE table_name='download_outcomes' "
                     "AND column_name='failed_at'") == 0
    assert db.scalar("SELECT to_regclass('goal_snapshots')") is None
    db.execute(mig.up)
    assert db.scalar("SELECT to_regclass('goal_snapshots')") == "goal_snapshots"
    assert db.scalar("SELECT count(*) FROM download_outcomes WHERE status='failed' AND failed_at IS NULL") == 0


# ------------------------------------------------------------------ goals API
def _local_today() -> date:
    return datetime.now(UTC).astimezone(ZoneInfo("Europe/Amsterdam")).date()


@pytest.fixture(autouse=True)
def _amsterdam(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Amsterdam")


def _goal(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(g for g in body["goals"] if g["key"] == key)


def _set(admin: Any, **values: Any) -> None:
    r = admin.put("/api/v1/settings", json={"values": values})
    assert r.status_code == 200, r.text


def test_goals_requires_login_and_is_honest_on_an_empty_install(client: Any, viewer: Any) -> None:
    assert client.get("/api/v1/overview/goals").status_code == 401
    r = viewer.get("/api/v1/overview/goals")
    assert r.status_code == 200, r.text
    b = r.json()
    tr = b["trial"]
    assert tr["start_source"] == "setup" and tr["day"] == 1 and tr["length_days"] == 28
    assert tr["timezone"] == "Europe/Amsterdam" and tr["start_date"] == _local_today().isoformat()
    fr = _goal(b, "failure_ratio")
    assert fr["status"] == "insufficient" and fr["now"]["n"] == 0 and fr["target"] == 0.10
    assert fr["blocker"]["text"].startswith("Te weinig data: 0 aflevering(en)/film(s) in 7 dagen (minimaal 20)")
    sus, srv = _goal(b, "suspicious_torrents"), _goal(b, "failing_servers")
    assert sus["status"] == "unavailable" and sus["reason"] == "qBittorrent is niet gekoppeld"
    assert srv["status"] == "unavailable" and srv["reason"] == "SABnzbd is niet gekoppeld"
    assert [len(g["trend"]) for g in b["goals"]] == [1, 0, 0]
    assert b["met"] == 0 and b["total"] == 3


def test_trial_start_from_autopilot_setting_and_invalid_value(admin: Any, viewer: Any, db: Any) -> None:
    for days, old in ((9, "manage"), (3, "read_only")):
        db.execute("INSERT INTO audit_log(ts, actor, action, summary, detail) VALUES "
                   "(now() - make_interval(days => %s), 'beheerder', 'system.mode', 'x', %s)",
                   (days, json.dumps({"old": old, "new": "autopilot"})))
    tr = viewer.get("/api/v1/overview/goals").json()["trial"]
    assert tr["start_source"] == "autopilot" and tr["days_since_start"] in (9, 10)
    assert tr["day"] == tr["days_since_start"]
    assert len(_goal(viewer.get("/api/v1/overview/goals").json(), "failure_ratio")["trend"]) == tr["day"]
    start = _local_today() - timedelta(days=30)
    _set(admin, **{"goals.trial_start": start.isoformat()})
    tr = viewer.get("/api/v1/overview/goals").json()["trial"]
    assert tr["start_source"] == "setting" and tr["start_date"] == start.isoformat()
    assert tr["ended"] is True and tr["day"] == 28 and tr["days_since_start"] == 31
    assert tr["end_date"] == (start + timedelta(days=27)).isoformat()
    assert len(_goal(viewer.get("/api/v1/overview/goals").json(), "failure_ratio")["trend"]) == 28
    _set(admin, **{"goals.trial_start": (_local_today() + timedelta(days=2)).isoformat()})
    tr = viewer.get("/api/v1/overview/goals").json()["trial"]
    assert tr["not_started"] is True and tr["day"] == 0
    _set(admin, **{"goals.trial_start": "volgende week"})
    tr = viewer.get("/api/v1/overview/goals").json()["trial"]
    assert tr["start_source"] == "autopilot" and "Ongeldige startdatum" in tr["start_warning"]


def _usenet(ref: str, title: str, status: str, hours_ago: float, reason: str | None = None) -> None:
    outcomes.upsert_outcome({"protocol": "usenet", "client": "sabnzbd", "client_ref": ref, "title": title,
                             "status": status, "failure_reason": reason,
                             "finished_at": datetime.now(UTC) - timedelta(hours=hours_ago)})


def test_failure_goal_netto_bruto_trend_and_blocker(admin: Any, viewer: Any) -> None:
    _set(admin, **{"goals.trial_start": (_local_today() - timedelta(days=5)).isoformat()})
    for i in range(1, 21):
        _usenet(f"f{i}", f"Serie.Een.S01E{i:02d}.1080p.WEB-DL-GRP", "failed", 72, "unpack_failed")
        _usenet(f"s{i}", f"Serie.Een.S01E{i:02d}.720p.HDTV-OTHER", "success", 48)
    _usenet("x1", "Serie.Twee.S02E01.1080p-GRP", "failed", 20, "unpack_failed")
    b = viewer.get("/api/v1/overview/goals").json()
    fr = _goal(b, "failure_ratio")
    assert fr["status"] == "met" and fr["now"]["value"] == round(1 / 21, 4) and fr["now"]["n"] == 21
    assert fr["now"]["gross"] == round(21 / 41, 4) and fr["now"]["gross_n"] == 41
    assert fr["blocker"] is None and len(fr["trend"]) == 6
    assert fr["trend"][-1]["value"] == fr["now"]["value"] and fr["trend"][-1]["source"] == "computed"
    assert fr["start"]["value"] is None and fr["start"]["n"] == 0  # nothing happened before the trial start
    for i in range(3):
        _usenet(f"y{i}", f"Serie.Drie.S03E0{i + 1}.1080p-GRP", "failed", 10, "incomplete")
    fr = _goal(viewer.get("/api/v1/overview/goals").json(), "failure_ratio")
    assert fr["status"] == "not_met" and fr["now"]["failed"] == 4 and fr["now"]["n"] == 24
    assert fr["blocker"]["text"] == (
        "Meeste mislukkingen: onvolledig (ontbrekende artikelen/blocks, repair faalde) (3 van 4).")
    assert fr["reasons"][0] == {"reason": "incomplete", "label": outcomes.FAILURE_REASONS["incomplete"], "count": 3}
    # the dashboard carries the compact block and the netto/bruto ratio with n
    ov = viewer.get("/api/v1/overview").json()
    assert ov["failures"]["ratio"]["net"] == {"value": round(4 / 24, 4), "failed": 4, "n": 24, "insufficient": False}
    assert ov["failures"]["ratio"]["gross"]["n"] == 44 and ov["failures"]["ratio"]["min_n"] == 20
    items = {g["key"]: g for g in ov["goals"]["items"]}
    assert items["failure_ratio"]["status"] == "not_met" and ov["goals"]["trial"]["day"] == 6


def test_suspicious_goal_reconstruction_snapshot_and_job(admin: Any, viewer: Any, db: Any, worker: Any) -> None:
    today = _local_today()
    _set(admin, **{"goals.trial_start": (today - timedelta(days=5)).isoformat()})
    sus = json.dumps({"findings": [{"code": "suspicious_payload"}]})
    db.execute(
        "INSERT INTO torrent_state(hash, name, diagnosis, first_seen, removed, updated_at) VALUES "
        "('a1','Oud.S01E01.mkv.exe', %s, now() - interval '10 days', true, now() - interval '2 days'),"
        "('b2','Nieuw.S01E02.exe', %s, now() - interval '1 day', false, now()),"
        "('c3','Schoon.S01E03.mkv', '{}', now() - interval '10 days', false, now())",
        (sus, sus),
    )
    db.execute("INSERT INTO goal_snapshots(day, metrics) VALUES (%s, %s)",
               (today - timedelta(days=1), json.dumps({"suspicious": 7})))
    g = _goal(viewer.get("/api/v1/overview/goals").json(), "suspicious_torrents")
    assert g["status"] == "not_met" and g["now"]["value"] == 1 and g["now"]["new_7d"] == 1
    assert g["start"] == {"value": 1, "source": "reconstructed", "date": (today - timedelta(days=5)).isoformat()}
    by_day = {t["date"]: t for t in g["trend"]}
    assert by_day[(today - timedelta(days=1)).isoformat()] == {
        "date": (today - timedelta(days=1)).isoformat(), "value": 7, "source": "snapshot"}
    assert by_day[(today - timedelta(days=3)).isoformat()]["source"] == "reconstructed"
    assert by_day[(today - timedelta(days=3)).isoformat()]["value"] == 1
    assert by_day[today.isoformat()]["source"] == "live"
    assert g["blocker"]["text"] == ("1 verdachte torrent(s) zonder opruimvoorstel; maak er een op Torrentclient"
                                    " · 1 nieuw in 7 dagen.")
    # hourly job stores today's values (local day), later runs overwrite the same day
    from mso import jobs

    jobs.enqueue("overview.goals_snapshot")
    worker.drain()
    snap = db.fetchone("SELECT * FROM goal_snapshots WHERE day=%s", (today,))
    assert snap["metrics"]["suspicious"] == 1 and snap["metrics"]["failing_servers"] is None
    assert snap["metrics"]["failure_net_n"] == 0
    jobs.enqueue("overview.goals_snapshot", idempotency_key="tweede")
    worker.drain()
    assert db.scalar("SELECT count(*) FROM goal_snapshots WHERE day=%s", (today,)) == 1
    assert any(s.kind == "overview.goals_snapshot" and s.every_s == 3600 for s in jobs.SCHEDULES)


# ------------------------------------------------------------------ over real HTTP (mocks)
@pytest.fixture()
def qb(admin: Any, serve: Any) -> Any:
    from msomocks.qbittorrent import create_app

    from mso.connectors.qbittorrent import QbitClient

    QbitClient.reset_cache()
    app = create_app()
    r = admin.put("/api/v1/connectors/qbittorrent",
                  json={"url": serve(app), "secrets": {"username": "admin", "password": "adminadmin"}})
    assert r.status_code == 200
    yield app.state.qb
    QbitClient.reset_cache()


def _hash(qbs: Any, prefix: str) -> str:
    return next(h for h, t in qbs.torrents.items() if t["name"].startswith(prefix))


def test_stalled_torrent_failed_at_and_suspicious_goal_via_qbittorrent(
    qb: Any, viewer: Any, operator: Any, db: Any
) -> None:
    from msomocks.qbittorrent import _torrent

    from mso.features.torrent.ingest import ingest

    ingest()
    h = _hash(qb, "Andere.Serie")
    db.execute("UPDATE torrent_state SET last_progress_at = now() - interval '100 hours' WHERE hash=%s", (h,))
    ingest()
    ingest()  # re-observed: the failure moment does not move
    row = db.fetchone("SELECT status, failure_reason, failed_at FROM download_outcomes WHERE client_ref=%s", (h,))
    assert row["status"] == "failed" and row["failure_reason"] == "tracker_error"
    age = datetime.now(UTC) - row["failed_at"]
    assert timedelta(hours=27) < age < timedelta(hours=29)  # last progress + 72 h stall threshold
    by_reason = {r["reason"]: r for r in viewer.get("/api/v1/overview").json()["failures"]["by_reason"]}
    assert by_reason["tracker_error"]["last_7d"] == 1 and by_reason["tracker_error"]["last_24h"] == 0

    pub = "https://tracker.publiek.example/announce"
    tr = [{"url": pub, "status": 2, "tier": 0, "num_peers": 3, "num_seeds": 9, "num_leeches": 3,
           "num_downloaded": 1, "msg": ""}]
    for n in ("Criminal Minds S19E06 1080p WEB h264-GRACE.exe", "House of the Dragon S03E05 1080p x265-MeGusta.scr"):
        qb.add(_torrent(n, category="tv-sonarr", size=2**30, progress=1.0, state="stalledUP", tracker=pub,
                        private=False), tr, [(n, 2**30)])
    ingest()
    g = _goal(viewer.get("/api/v1/overview/goals").json(), "suspicious_torrents")
    assert g["available"] and g["now"]["value"] == 2 and g["now"]["awaiting_approval"] == 0
    assert "zonder opruimvoorstel" in g["blocker"]["text"] and g["blocker"]["link"] == "/torrentclient"
    r = operator.post("/api/v1/torrent/suspicious/cleanup-preview", json={})
    assert r.status_code == 200, r.text
    g = _goal(viewer.get("/api/v1/overview/goals").json(), "suspicious_torrents")
    assert g["now"]["awaiting_approval"] == 2 and g["now"]["change_id"] == r.json()["id"]
    assert g["blocker"] == {"text": "2 wachten op jouw goedkeuring (opruimvoorstel) · 2 nieuw in 7 dagen.",
                            "link": f"/automatisering?tab=wijzigingen&change={r.json()['id']}"}
    assert g["status"] == "not_met"
    # nothing was deleted by looking
    assert not [c for c in qb.calls if "/delete" in c[1]]


SAB_KEY = "SABGEHEIM-doelen-0123456789"


def test_failing_servers_vs_switched_off_by_mso_via_sabnzbd(admin: Any, viewer: Any, db: Any, serve: Any) -> None:
    from msomocks import sabnzbd as mock

    from mso import state
    from mso.features.usenet import autoheal, serverstatus

    app = mock.create_app(api_key=SAB_KEY, server_errors="field")
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    _set(admin, **{"automation.max_per_hour": 60})
    g = _goal(viewer.get("/api/v1/overview/goals").json(), "failing_servers")
    assert g["status"] == "unavailable" and g["reason"] == "SABnzbd heeft nog geen serverstatus gegeven"

    serverstatus.refresh()
    g = _goal(viewer.get("/api/v1/overview/goals").json(), "failing_servers")
    assert g["available"] and g["now"]["value"] == 4 and g["disabled_by_mso"] == [] and g["disabled_text"] is None
    assert g["blocker"]["text"].startswith("Ingeschakeld maar falend: ") and g["blocker"]["link"] == "/usenet-providers"
    assert "(account verlopen)" in g["blocker"]["text"] and "goedkeuring" not in g["blocker"]["text"]
    assert g["start"]["value"] == 4 and g["start"]["source"] == "reconstructed"
    # an open "kapotte servers uitschakelen" proposal is what holds the goal back
    from mso import changes

    ch = changes.create("sabnzbd", "Kapotte servers uitschakelen",
                        [{"path": "servers/reader.xsnews.test/enable", "before": 1, "after": 0, "destructive": True}],
                        risk="toestemming_nodig", dedupe_key="usenet.disable_failing:test")
    g = _goal(viewer.get("/api/v1/overview/goals").json(), "failing_servers")
    assert g["blocker"]["text"].endswith(" — uitschakelen wacht op jouw goedkeuring.")
    changes.discard(ch["id"], "beheerder")

    state.set_state("mode", "autopilot")
    autoheal.run()  # "account verlopen" goes off at once
    serverstatus.refresh()
    g = _goal(viewer.get("/api/v1/overview/goals").json(), "failing_servers")
    assert g["now"]["value"] == 3 and g["now"]["disabled_by_mso"] == 1
    assert g["disabled_by_mso"][0]["server"] == "reader.xsnews.test"
    assert g["disabled_by_mso"][0]["reason_label"] == "Account verlopen"
    assert g["disabled_text"] == "1 uitgezet omdat hij niet werkt; herstellen of verwijderen in SABnzbd is jouw keuze"

    db.execute("UPDATE usenet_sab_servers SET since = now() - interval '7 hours'")
    db.execute("UPDATE usenet_sab_server_events SET first_at = now() - interval '7 hours'")
    autoheal.run()
    serverstatus.refresh()
    b = viewer.get("/api/v1/overview/goals").json()
    g = _goal(b, "failing_servers")
    assert g["status"] == "met" and g["now"]["value"] == 0 and g["blocker"] is None
    assert g["disabled_text"] == "4 uitgezet omdat ze niet werken; herstellen of verwijderen in SABnzbd is jouw keuze"
    assert g["direction"] == "better"
    # nothing secret leaks (SABnzbd key, user names)
    text = json.dumps(b)
    assert SAB_KEY not in text and "username" not in text
