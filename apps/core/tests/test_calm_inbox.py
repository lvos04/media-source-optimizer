"""1.6.0 WP-G1 (C06, C30) — rust in de app: one real 'Wacht op jou' inbox (one decision = one row, malware bundle
once, the rest grouped, ``decisions``) and 'Wat MSO deed' without sync noise (``actions_total``, one sync status
line, per action wat / waarom / effect + rollback, local days)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from mso import changes, jobs, state
from mso.audit import audit
from mso.changes import Applier
from mso.db import jsonb
from mso.domain.outcomes import recommend

GB = 1024**3


@pytest.fixture()
def fake(app: Any) -> dict[str, Any]:
    store: dict[str, Any] = {"conns": 10, "srv": 1}
    changes.register_applier("fake_calm", lambda: Applier(read=store.get, write=store.__setitem__))
    yield store
    changes.APPLIERS.pop("fake_calm", None)


def _cleanup_change(db: Any, hashes: list[str], *, title: str, status: str = "preview") -> int:
    tok = "0123456789abcdef"
    ops = [{"path": f"cleanup:{tok}", "label": "Controle", "before": "x", "after": "x"}]
    ops += [{"path": f"torrent:{h}:delete_with_files:{tok}", "label": h[:8], "before": "aanwezig",
             "after": "verwijderd", "irreversible": True} for h in hashes]
    return int(db.scalar(
        "INSERT INTO change_sets(target,title,risk,ops,status,source,created_by) VALUES "
        "('qbittorrent',%s,'toestemming_nodig',%s,%s,'torrent-cleanup','autopilot') RETURNING id",
        (title, jsonb(ops), status)))


def _suspicious_rec(db: Any, h: str, *, age_days: float = 0) -> int:
    rid = recommend("torrent", f"Verdachte torrent verwijderen: {h[:6]}", "Uitvoerbaar bestand.",
                    risk="toestemming_nodig", dedupe_key=f"torrent-suspicious:btih:{h}", evidence={"hash": h}, db=db)
    if age_days:
        db.execute("UPDATE recommendations SET created_at = now() - make_interval(hours => %s) WHERE id=%s",
                   (age_days * 24, rid))
    return rid


# ------------------------------------------------------------------ C06: one inbox
def test_waiting_one_decision_per_row_bundle_once_rest_grouped(app: Any, db: Any, viewer: Any) -> None:
    hs = [c * 40 for c in "abcd"]
    bundle = _cleanup_change(db, hs[:3], title="Alle 3 verdachte torrents opruimen")
    single = _cleanup_change(db, hs[:1], title="Verdachte torrent opruimen: aaaa")  # covered by the bundle
    _suspicious_rec(db, hs[0], age_days=3)
    _suspicious_rec(db, hs[1])
    _suspicious_rec(db, hs[2])
    _suspicious_rec(db, hs[3])  # flagged after the bundle was made
    _suspicious_rec(db, "e" * 40)
    for i in range(3):
        recommend("usenet", f"Terugdraaien aanbevolen: wijziging {i}", "Slechter gemeten.", risk="toestemming_nodig",
                  dedupe_key=f"automation.rollback:{100 + i}", db=db)
    recommend("indexers", "Indexer X uitschakelen", "Faalt.", risk="toestemming_nodig", dedupe_key="idx.off:x", db=db)
    recommend("usenet", "Alleen advies", "Uitleg.", risk="advies", dedupe_key="advies:1", db=db)  # never waiting
    recommend("torrent", "Je hebt 'DHT' zelf aangepast in qBittorrent", "Ter info.", risk="advies",
              dedupe_key="footprint.override:qbittorrent:pref:dht", db=db)

    w = viewer.get("/api/v1/overview/waiting").json()
    rows = {i["id"]: i for i in w["items"]}
    b = rows[bundle]
    assert b["title"] == "3 nep-downloads opruimen (+2 nieuw sinds gisteren)" and b["bundle"] is True
    assert b["change_title"] == "Alle 3 verdachte torrents opruimen" and b["new_since_yesterday"] == 2
    assert b["decisions"] == 1 and b["deletes"] is True and single in b["change_ids"]
    assert single not in rows  # folded into the bundle
    sus = rows["group:torrent:torrent-suspicious"]
    assert sus["title"] == "2 verdachte torrents opruimen" and sus["decisions"] == 1 and sus["link"] == "/torrentclient"
    rb = rows["group:usenet:automation.rollback"]
    assert rb["title"] == "3× Terugdraaien aanbevolen" and rb["decisions"] == 3 and len(rb["examples"]) == 3
    one = next(i for i in w["items"] if i["title"] == "Indexer X uitschakelen")
    assert one["decisions"] == 1 and one["group"] is False
    assert w["count"] == 4 and w["decisions"] == 1 + 1 + 3 + 1
    assert w["info_count"] == 1 and w["info"][0]["title"].startswith("Je hebt 'DHT'")
    assert w["items"][0]["id"] == bundle  # deleting first
    # the same inbox in the full summary (API compatible: count/items stay)
    full = viewer.get("/api/v1/overview/autopilot").json()["waiting"]
    assert full["decisions"] == w["decisions"] and full["count"] == w["count"]


def test_waiting_endpoint_roles(client: Any, viewer: Any) -> None:
    assert client.get("/api/v1/overview/waiting").status_code == 401
    w = viewer.get("/api/v1/overview/waiting").json()
    assert w == {"count": 0, "decisions": 0, "items": [], "info": [], "info_count": 0}


def _qb_connect(admin: Any, serve: Any) -> Any:
    from msomocks.qbittorrent import create_app

    app = create_app(version="5.3.0", webapi="2.16.2")
    r = admin.put("/api/v1/connectors/qbittorrent",
                  json={"url": serve(app), "secrets": {"username": "admin", "password": "adminadmin"}})
    assert r.status_code == 200
    return app.state.qb


def test_waiting_over_http_41_fakes_become_one_decision(app: Any, db: Any, admin: Any, viewer: Any,
                                                        serve: Any) -> None:
    """qBittorrent mock with fake releases: ingest makes one recommendation per torrent; the inbox shows ONE row
    before and after the torrent autopilot keeps the cleanup bundle ready."""
    from msomocks.qbittorrent import _torrent

    from mso.features.torrent import autopilot as tap
    from mso.features.torrent.ingest import ingest

    qb = _qb_connect(admin, serve)
    pub = "https://tracker.publiek.example/announce"
    tr = [{"url": pub, "status": 2, "tier": 0, "num_peers": 3, "num_seeds": 9, "num_leeches": 3,
           "num_downloaded": 1, "msg": ""}]
    for n in ("Criminal Minds S19E06 1080p WEB h264-GRACE.exe", "House of the Dragon S03E05 1080p x265-MeGusta.scr",
              "South Park S27E01 1080p WEB h264-EDITH.exe"):
        qb.add(_torrent(n, category="tv-sonarr", size=GB, progress=1.0, state="stalledUP", tracker=pub,
                        private=False), tr, [(n, GB)])
    ingest()
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'torrent-suspicious:%%' "
                     "AND status='open'") == 3
    w = viewer.get("/api/v1/overview/waiting").json()
    assert w["decisions"] == 1 and w["count"] == 1
    assert w["items"][0]["title"] == "3 verdachte torrents opruimen"
    tap.maintain_cleanup_proposal()
    w = viewer.get("/api/v1/overview/waiting").json()
    assert w["decisions"] == 1 and w["count"] == 1
    item = w["items"][0]
    assert item["bundle"] is True and item["title"] == "3 nep-downloads opruimen (+3 nieuw sinds gisteren)"
    assert item["kind"] == "goedkeuren" and "één klik" in item["reason"]


# ------------------------------------------------------------------ C30: wat MSO deed
def test_actions_total_without_syncs_and_evaluations_and_sync_status_line(
    app: Any, db: Any, viewer: Any, fake: dict[str, Any]
) -> None:
    from mso.features.automation import autopilot

    state.set_state("mode", "autopilot")
    autopilot.propose_and_apply("fake_calm", "Verbindingen 20", [{"path": "conns", "before": 10, "after": 20}],
                                explanation="", impact=None, dedupe_key="a", area="usenet")
    for i, kind in enumerate(("usenet.sab_ingest", "torrent.ingest", "prowlarr.sync", "automation.footprint")):
        jobs.enqueue(kind, {}, idempotency_key=f"sched:{i}", created_by="scheduler")
    db.execute("UPDATE jobs SET status='done' WHERE idempotency_key LIKE 'sched:%%'")
    db.execute("INSERT INTO automation_log(kind,result,message) VALUES ('evaluate','gelijk','x')")
    body = viewer.get("/api/v1/overview/autopilot").json()
    cats = {c["key"]: c["count"] for c in body["categories"]}
    assert cats["syncs"] == 4 and cats["evaluated"] == 1 and cats["applied"] == 1
    assert body["total"] == 6 and body["actions_total"] == 1  # 'total' unchanged, actions without bookkeeping
    assert body["by_day"][0]["actions"] == 1 and body["by_day"][0]["total"] == 6
    # an evaluation recorded in the audit log (tuning by autotune) is no action either, also not per day
    audit("autotune", "torrent.tuning_evaluated", "Tuning gecontroleerd: behouden", target="qbittorrent")
    body = viewer.get("/api/v1/overview/autopilot").json()
    assert body["actions_total"] == 1 and body["by_day"][0]["actions"] == 1 and body["by_day"][0]["total"] == 7
    s = body["syncs"]
    assert s["state"] == "ok" and s["runs"] == 4 and s["failing_now"] == []
    assert s["link"] == "/systeem#automatische-taken"
    # one sync failing now → the status line says so; an older failure that recovered does not
    jobs.enqueue("prowlarr.sync", {}, idempotency_key="sched:fail", created_by="scheduler")
    db.execute("UPDATE jobs SET status='failed', updated_at=now() + interval '1 second' "
               "WHERE idempotency_key='sched:fail'")
    s = viewer.get("/api/v1/overview/autopilot").json()["syncs"]
    assert s["state"] == "failing" and s["failing_now"] == ["prowlarr.sync"] and s["failed"] == 1
    jobs.enqueue("prowlarr.sync", {}, idempotency_key="sched:ok", created_by="scheduler")
    db.execute("UPDATE jobs SET status='done', updated_at=now() + interval '2 seconds' "
               "WHERE idempotency_key='sched:ok'")
    s = viewer.get("/api/v1/overview/autopilot").json()["syncs"]
    assert s["state"] == "ok" and s["failed"] == 1


def test_nothing_done_in_read_only_even_with_syncs(app: Any, db: Any, viewer: Any) -> None:
    jobs.enqueue("usenet.sab_ingest", {}, idempotency_key="sched:x", created_by="scheduler")
    db.execute("UPDATE jobs SET status='done'")
    body = viewer.get("/api/v1/overview/autopilot").json()
    assert body["mode"] == "read_only" and body["actions_total"] == 0 and body["total"] == 1
    assert body["actions"] == []


def test_actions_what_why_effect_and_inline_rollback(
    app: Any, db: Any, viewer: Any, operator: Any, fake: dict[str, Any]
) -> None:
    from mso.features.automation import autopilot

    state.set_state("mode", "autopilot")
    ch = autopilot.propose_and_apply(
        "fake_calm", "newshosting uitgezet in SABnzbd", [{"path": "conns", "before": 10, "after": 4}],
        explanation="Newshosting: inloggen geweigerd. SABnzbd probeert hem bij elke download.",
        impact={"Reden": "502 inloggen geweigerd sinds 4 nov", "SABnzbd meldt": "502 Authentication Failed"},
        dedupe_key="w", evaluate_after_h=18, area="usenet")
    assert ch["auto_applied"] is True
    [a] = viewer.get("/api/v1/overview/autopilot").json()["actions"]
    assert a["change_id"] == ch["id"] and a["what"] == "newshosting uitgezet in SABnzbd" and a["category"] == "applied"
    assert a["why"] == "502 inloggen geweigerd sinds 4 nov: 502 Authentication Failed"
    assert a["effect"]["state"] == "pending" and a["effect"]["text"].startswith("Meting loopt, eindoordeel over 1")
    assert a["rollback"] == {"possible": True, "reason": None}
    # the evaluation stored by the engine → measured effect
    ev = {"result": "beter", "label": "Beter", "action": "behouden", "measures": [
        {"key": "failure_ratio", "label": "Faalratio downloads", "unit": "ratio", "baseline": {"n": 120, "value": 0.31},
         "current": {"n": 140, "value": 0.12}}]}
    db.execute("UPDATE change_sets SET impact = impact || %s WHERE id=%s", (jsonb({"evaluation": ev}), ch["id"]))
    [a] = viewer.get("/api/v1/overview/autopilot").json()["actions"]
    assert a["effect"] | {"due_at": None} == {"state": "measured", "result": "beter", "label": "Beter",
                                              "text": "Faalratio downloads 31 % → 12 % (n=140) · behouden",
                                              "tone": "success", "due_at": None}
    # inline 'Terugdraaien' = the existing endpoint
    assert operator.post(f"/api/v1/changes/{ch['id']}/rollback").status_code == 200
    assert fake["conns"] == 10
    audit("automatisering", "change.auto_rolled_back", "Automatisch teruggedraaid na controle: x", target="fake_calm",
          change_id=ch["id"])
    [a] = viewer.get("/api/v1/overview/autopilot").json()["actions"]
    assert a["status"] == "rolled_back" and a["effect"]["state"] == "rolled_back" and a["category"] == "rolled_back"
    assert a["rollback"]["possible"] is False


def test_why_falls_back_to_first_sentence_and_one_way_not_rollbackable() -> None:
    from mso.features.overview.autopilot import effect_of, rollback_of, why_of

    assert why_of({"explanation": "Eerste zin hier. Tweede zin.", "impact": {}}) == "Eerste zin hier."
    assert why_of({"explanation": "", "impact": {}}) is None
    ch = {"status": "applied", "ops": [{"path": "retry/x", "result": "toegepast", "one_way": True}]}
    assert rollback_of(ch)["possible"] is False and "eenmalige" in rollback_of(ch)["reason"]
    off = {"status": "applied", "impact": {}, "ops": [{"path": "s", "destructive": True, "result": "toegepast"}]}
    assert effect_of(off)["state"] == "not_measured"


def test_local_day_buckets(app: Any, db: Any, viewer: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Days follow the local zone (TZ), not UTC: an action at 00:30 local time belongs to that local day."""
    from mso.features.overview import autopilot as ov

    zone = "Pacific/Kiritimati"  # UTC+14: local midnight is always on the previous UTC day
    monkeypatch.setenv("TZ", zone)
    ov._TZ_OK.clear()
    local_today = db.scalar("SELECT (now() AT TIME ZONE %s)::date", (zone,))
    ts = db.scalar("SELECT (%s::date + time '00:30') AT TIME ZONE %s", (local_today, zone))
    assert ts.astimezone(UTC).date() != local_today
    aid = audit("automatisering", "usenet.server_recovered", "Server werkt weer: xsnews", target="sabnzbd")
    db.execute("UPDATE audit_log SET ts=%s WHERE id=%s", (ts, aid))
    body = viewer.get("/api/v1/overview/autopilot?days=2").json()
    assert body["timezone"] == zone and body["by_day"][0]["date"] == local_today.isoformat()
    assert [i["id"] for i in body["by_day"][0]["items"]] == [aid]
    # an unknown zone falls back to UTC (and that is remembered)
    monkeypatch.setenv("TZ", "Nergens/Bestaat")
    ov._TZ_OK.clear()
    assert ov.local_tz() == "UTC" and ov._TZ_OK == {"Nergens/Bestaat": "UTC"}
    # a database hiccup gives UTC for that one call only: the zone is checked again next time
    monkeypatch.setenv("TZ", zone)
    ov._TZ_OK.clear()

    class _Down:
        def scalar(self, *_a: Any, **_k: Any) -> Any:
            raise psycopg.OperationalError("verbinding verbroken")

    assert ov.local_tz(_Down()) == "UTC" and ov._TZ_OK == {}  # type: ignore[arg-type]
    assert ov.local_tz() == zone
    ov._TZ_OK.clear()


def test_measures_text_formats() -> None:
    from mso.features.overview.autopilot import measures_text

    ms = [{"label": "Tijd tot voltooiing (mediaan)", "unit": "s", "baseline": {"n": 30, "value": 5400},
           "current": {"n": 31, "value": 1800}},
          {"label": "Downloadsnelheid actieve torrents", "unit": "B/s", "baseline": {"n": 50, "value": 2_500_000},
           "current": {"n": 50, "value": None}, "why": "te weinig metingen"}]
    assert measures_text(ms) == ("Tijd tot voltooiing (mediaan) 1,5 u → 30 min (n=31) · "
                                 "Downloadsnelheid actieve torrents: te weinig metingen")


def test_evaluate_window_hours_text() -> None:
    from mso.features.overview.autopilot import effect_of

    now = datetime.now(UTC)
    ch = {"status": "applied", "ops": [{"path": "x", "result": "toegepast"}], "impact": {},
          "applied_at": now - timedelta(hours=1), "evaluate_after": now + timedelta(days=3)}
    assert effect_of(ch, now)["text"] == "Meting loopt, eindoordeel over 3 dagen"
    ch["evaluate_after"] = now - timedelta(minutes=5)
    assert "volgende controle" in effect_of(ch, now)["text"]


# ------------------------------------------------------------------ integration review (1.6.0)
def test_overview_counts_each_decision_once(app: Any, db: Any, viewer: Any) -> None:
    hs = [c * 40 for c in "ab"]
    bundle = _cleanup_change(db, hs, title="Alle 2 verdachte torrents opruimen")
    for h in hs:
        _suspicious_rec(db, h)
    cat = changes.create("sabnzbd_categories", "SABnzbd-categorie tv aanmaken",
                         [{"path": "categories/tv", "before": "ontbreekt", "after": "aanwezig · map tv"}],
                         risk="toestemming_nodig")
    recommend("usenet", "Categorie tv ontbreekt", "Uitleg.", risk="toestemming_nodig", change_id=cat["id"],
              dedupe_key="usenet.categories:tv", db=db)
    recommend("system", "Back-up overgeslagen", "Ruimte.", risk="toestemming_nodig", dedupe_key="backup.space", db=db)
    recommend("usenet", "Alleen advies", "Uitleg.", risk="advies", dedupe_key="advies:1", db=db)
    w = viewer.get("/api/v1/overview/waiting").json()
    o = viewer.get("/api/v1/overview").json()
    assert w["decisions"] == 3  # the bundle, the category change, the back-up item
    recs = o["recommendations"]
    assert recs["open"] == 1 and recs["by_risk"]["advies"] == 1 and recs["waiting"] == w["decisions"]
    assert [x["title"] for x in recs["top"]] == ["Alleen advies"]
    assert o["changes"]["open"] == 0 and o["changes"]["items"] == []  # both open changes are in the inbox
    assert bundle in next(i for i in w["items"] if i.get("bundle"))["change_ids"]
    # the inbox links lead to the item itself, not to the unfiltered Aanbevelingen list
    backup = next(i for i in w["items"] if i["title"] == "Back-up overgeslagen")
    assert backup["link"] == "/systeem"


def test_inbox_links_point_at_the_item() -> None:
    from mso.features.overview.autopilot import rec_link

    assert rec_link({"id": 1, "dedupe_key": "autopilot.hold:x"}) == "/automatisering?tab=veiligheid"
    assert rec_link({"id": 1, "dedupe_key": "automation.parked:k:fp", "evidence": {"change_id": 7}}) == \
        "/automatisering?tab=wijzigingen&change=7"
    assert rec_link({"id": 1, "dedupe_key": "change.interrupted:7", "evidence": {"change_id": 7}}) == \
        "/automatisering?tab=wijzigingen&change=7"
    assert rec_link({"id": 1, "dedupe_key": "connector.down:qbittorrent"}) == \
        "/instellingen?tab=verbindingen#connector-qbittorrent"
    assert rec_link({"id": 1, "dedupe_key": "usenet.server_expired:x"}) == "/usenet-providers"
    assert rec_link({"id": 1, "dedupe_key": "usenet.block_low:x:1"}) == "/usenet-providers"
    assert rec_link({"id": 1, "dedupe_key": "usenet.unpack:abc"}) == "/downloads"
    assert rec_link({"id": 9, "dedupe_key": "iets.anders"}) == "/automatisering?tab=aanbevelingen&rec=9"


def test_parked_change_is_one_decision_also_during_the_noodrem(app: Any, db: Any, viewer: Any,
                                                              fake: dict[str, Any]) -> None:
    from mso.features.automation import autopilot

    state.set_state("mode", "autopilot")
    ch = changes.create("fake_calm", "Verbindingen 1 → 2", [{"path": "conns", "before": 10, "after": 20}],
                        risk="automatisch_veilig", impact={"area": "usenet"}, dedupe_key="calm.conns")
    for _ in range(3):
        autopilot.note_failure(ch, "bron offline", "usenet", db)
    w = viewer.get("/api/v1/overview/waiting").json()
    assert w["decisions"] == 1
    autopilot.engage_hold("test", {"rollbacks": 0, "failures": 5})
    w = viewer.get("/api/v1/overview/waiting").json()
    titles = [i["title"] for i in w["items"]]
    assert "Verbindingen 1 → 2" not in titles  # the parked item is the one decision for it
    assert any(t.startswith("MSO kon “Verbindingen 1 → 2”") for t in titles)
    parked = next(i for i in w["items"] if i["title"].startswith("MSO kon"))
    assert parked["link"] == f"/automatisering?tab=wijzigingen&change={ch['id']}"


def test_change_held_back_by_the_owners_override_is_info_not_a_decision(
    app: Any, db: Any, viewer: Any, fake: dict[str, Any]
) -> None:
    from mso.features.automation import overrides

    state.set_state("mode", "autopilot")
    overrides.record("fake_calm", "srv", mso_value=0, current_value=1, change_id=None, days=30)
    ch = changes.create("fake_calm", "Server uitschakelen", [{"path": "srv", "before": 1, "after": 0}],
                        risk="automatisch_veilig", impact={"area": "usenet"})
    w = viewer.get("/api/v1/overview/waiting").json()
    assert w["decisions"] == 0 and not w["items"]
    assert any(i["id"] == f"change:{ch['id']}" for i in w["info"])


def test_cleanup_change_from_an_accepted_recommendation_folds_into_the_bundle(app: Any, db: Any,
                                                                               viewer: Any) -> None:
    from mso.features.overview import goals

    hs = [c * 40 for c in "abcde"]
    bundle = _cleanup_change(db, hs, title="5 verdachte torrents opruimen")
    single = _cleanup_change(db, hs[:1], title="Verdachte torrent opruimen: aaaa")
    db.execute("UPDATE change_sets SET source=%s WHERE id=%s", (f"recommendation:{single}", single))
    w = viewer.get("/api/v1/overview/waiting").json()
    assert w["decisions"] == 1 and single not in {i["id"] for i in w["items"]}
    # the goals card names the same change and number as the inbox
    diag = jsonb({"findings": [{"code": "suspicious_payload"}]})
    for h in hs:
        db.execute("INSERT INTO torrent_state(hash,name,diagnosis,first_seen) VALUES (%s,%s,%s,now())",
                   (h, f"Show {h[:4]}", diag))
    g = next(x for x in viewer.get("/api/v1/overview/goals").json()["goals"] if x["key"] == "suspicious_torrents")
    assert g["now"]["change_id"] == bundle and g["now"]["awaiting_approval"] == 5
    assert goals.suspicious_now(db) == 5


def test_read_only_checks_are_no_actions(app: Any, db: Any, viewer: Any) -> None:
    from mso.features.overview.autopilot import classify

    assert classify({"action": "usenet.server_test", "actor": "autopilot:usenet"}) is None
    assert classify({"action": "costs.renewal_prefilled", "actor": "systeem"}) is None
    audit("autopilot:usenet", "usenet.server_test", "SABnzbd-servertest news.eweka.test: Test geslaagd",
          target="sabnzbd")
    s = viewer.get("/api/v1/overview/autopilot").json()
    assert s["mode"] == "read_only" and s["actions_total"] == 0


def test_sync_line_leaves_outage_failures_to_the_connector_card(app: Any, db: Any, viewer: Any) -> None:
    for kind in ("torrent.ingest", "torrent.storage"):
        db.execute("INSERT INTO jobs(kind,status,created_by,connector_id,updated_at) VALUES (%s,'failed',"
                   "'scheduler','qbittorrent',now())", (kind,))
    db.execute("INSERT INTO jobs(kind,status,created_by,updated_at) VALUES ('prowlarr.sync','failed','scheduler',"
               "now())")
    syncs = viewer.get("/api/v1/overview/autopilot").json()["syncs"]
    assert syncs["failing_now"] == ["prowlarr.sync"] and syncs["waiting_on"] == ["qbittorrent"]
    assert syncs["failing_labels"] == ["Indexers synchroniseren met Prowlarr"]
    assert syncs["failed"] == 1  # the outage failures are on the connector card, not counted here
