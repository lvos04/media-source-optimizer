"""1.6.0 WP-C (C34, reshaped): why SABnzbd puts downloads in '*' and where to fix it.

Over real HTTP against the SABnzbd and MediaGuard mocks: MediaGuard read routes (secrets dropped, optional on
403/404), the diagnosis cases (a) SABnzbd lacks the category Sonarr/Radarr send, (b) no category sent, (c) another
source, the '*' count per local day, and the single ``sabnzbd_categories`` proposal (risk ``toestemming_nodig``,
never automatic) with verification at the source and a guarded rollback."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from mg_support import VIEWER, assert_only_allowed_writes, configure, start_mock

SAB_KEY = "SABGEHEIM-0123456789"
NO_ANIME = "tv,movies,software"  # SABnzbd without the anime categories MediaGuard provisions


def _sab(serve: Any, admin: Any, **faults: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, **faults)
    url = serve(mock_app)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": url, "enabled": True, "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    return mock_app.state.sab


def _mg(serve: Any, admin: Any, **faults: Any) -> Any:
    mock, url = start_mock(serve, **faults)
    configure(admin, url, creds=VIEWER)  # rol viewer is enough for the diagnosis
    return mock


def _diag(client: Any, refresh: bool = True) -> dict[str, Any]:
    r = client.get("/api/v1/usenet/categories/diagnosis", params={"refresh": refresh})
    assert r.status_code == 200, r.text
    return r.json()


def _outcome(db: Any, ref: str, cat: str, *, when: datetime | None = None, status: str = "success") -> None:
    db.execute(
        "INSERT INTO download_outcomes(protocol,client,client_ref,title,client_category,status,finished_at) "
        "VALUES ('usenet','sabnzbd',%s,%s,%s,%s,%s)",
        (ref, f"Title {ref}", cat, status, when or datetime.now(UTC)),
    )


def _no_secrets(blob: Any) -> None:
    text = json.dumps(blob, default=str)
    for secret in ("sab-gebruiker", "qb-gebruiker", "********", "sabnzbd:8080", ":8989", SAB_KEY, "Viewer-Wacht"):
        assert secret not in text, secret


# ------------------------------------------------------------------ MediaGuard read routes
def test_mediaguard_reads_keep_only_whitelisted_fields(app: Any, serve: Any, admin: Any) -> None:
    from mso.connectors import mediaguard as mgc

    mock = _mg(serve, admin)
    mock.state.arr_health["sonarr-anime"] = [
        {"source": "DownloadClientCheck", "type": "error", "message": "Unable to communicate with SABnzbd. "
         "apikey=abcdef0123456789abcdef0123456789", "wikiUrl": "https://wiki.servarr.com/x"}
    ]
    c = mgc.MediaGuardClient.from_config()
    insts = {i["id"]: i for i in c.instances()}
    assert insts["sonarr-anime"]["media_type"] == "anime" and insts["sonarr-anime"]["name"] == "Anime"
    assert "url" not in insts["sonarr-anime"] and "api_key_set" not in insts["sonarr-anime"]
    dcs = c.download_clients("sonarr-anime")
    sab = next(d for d in dcs if d["implementation"] == "Sabnzbd")
    assert sab == {
        "id": 1, "name": "MediaGuard · SABnzbd", "implementation": "Sabnzbd", "protocol": "usenet", "enable": True,
        "priority": 1, "category_field": "tvCategory", "category": "tv-anime", "mediaguard_owned": True,
    }
    assert next(d for d in c.download_clients("radarr-films") if d["implementation"] == "Sabnzbd")[
        "category"] == "movies"
    health = c.instance_health("sonarr-anime")
    assert health[0]["source"] == "DownloadClientCheck" and "abcdef0123456789abcdef" not in health[0]["message"]
    assert set(health[0]) == {"source", "type", "message"}
    prov = c.provisioning_categories()
    assert prov["download_clients"] == [
        {"name": "SABnzbd", "implementation": "Sabnzbd", "enabled": True, "instances": None, "categories": {}}
    ]
    assert prov["default_categories"]["radarr-animefilms"] == "movies-anime"
    assert prov["instances"]["sonarr-series"]["downloadclients_disabled"] is False
    _no_secrets([insts, dcs, health, prov])
    with pytest.raises(ValueError):
        c.download_clients("../auth/me")
    assert_only_allowed_writes(mock)


def test_compat_matrix_lists_the_optional_read_routes(app: Any, serve: Any, admin: Any, viewer: Any) -> None:
    _mg(serve, admin, no_category_routes=1)
    st = viewer.get("/api/v1/mediaguard/status?refresh=true").json()
    rows = {r["id"]: r for r in st["compat"]["routes"]}
    assert rows["instances_list"]["live"] == "aanwezig" and rows["instances_list"]["access"] == "read"
    for rid in ("instance_download_clients", "instance_health", "provisioning_config"):
        assert rows[rid]["live"] == "ontbreekt" and rows[rid]["role"] == "viewer" and not rows[rid]["required"]
    assert st["mode"] == "connected"  # optional routes never degrade the connection
    assert any("download-clients" in n["path"] for n in st["compat"]["never_used"])
    from mso.connectors import mediaguard as mgc

    assert not any(m == "GET" for m, _ in mgc.ALLOWED_WRITES)  # ALLOWED_WRITES unchanged: no new write route
    assert len(mgc.ALLOWED_WRITES) == 3


# ------------------------------------------------------------------ SABnzbd connector + mock realism
def test_sab_category_reads_writes_and_unknown_category_lands_in_star(app: Any, serve: Any, admin: Any) -> None:
    from mso.connectors.sabnzbd import SabClient, validate_category_name

    sab = _sab(serve, admin, categories=NO_ANIME)
    with SabClient.from_config() as c:
        cats = {x["name"]: x for x in c.category_config()}
        assert list(cats)[0] == "*" and cats["*"]["pp"] == "3" and cats["tv"]["priority"] == -100
        assert c.complete_dir() == "/downloads/complete"
        nzo = c.add_url("https://indexer.test/get/1.nzb", category="tv-anime", name="Frieren.S01E01")
        assert next(s for s in sab.queue if s["nzo_id"] == nzo)["cat"] == "*"  # SABnzbd: unknown → '*'
        c.create_category("TV-Anime", "tv-anime", order=9)
        assert sab.category("tv-anime")["dir"] == "tv-anime" and sab.category("tv-anime")["pp"] == ""
        assert "tv-anime" in c.categories()
        c.delete_category("tv-anime")
        assert sab.category("tv-anime") is None
    for bad in ("*", "", "../x", "a/b", "default", "x" * 65):
        with pytest.raises(ValueError):
            validate_category_name(bad)
    assert validate_category_name(" Movies-Anime ") == "movies-anime"


# ------------------------------------------------------------------ diagnosis
def test_case_a_sab_lacks_category_with_health_and_counts(
    app: Any, serve: Any, admin: Any, viewer: Any, db: Any
) -> None:
    sab = _sab(serve, admin, categories=NO_ANIME)
    mock = _mg(serve, admin)
    mock.state.arr_health["sonarr-anime"] = [
        {"source": "DownloadClientCheck", "type": "warning", "message": "Unable to communicate with SABnzbd"},
        {"source": "IndexerRssCheck", "type": "warning", "message": "No indexers available with RSS sync enabled"},
    ]
    sab.queue.append({"nzo_id": "q1", "filename": "Frieren.S01E02", "cat": "*", "status": "Queued"})
    _outcome(db, "a1", "*")
    _outcome(db, "a2", "*", when=datetime.now(UTC) - timedelta(days=2))
    _outcome(db, "a3", "tv")
    _outcome(db, "old", "*", when=datetime.now(UTC) - timedelta(days=30))
    d = _diag(viewer)
    assert d["verdict"] == "a"
    assert [(m["category"], m["instances"], m["certainty"]) for m in d["missing"]] == [
        ("movies-anime", ["Animefilms"], "zeker"),
        ("tv-anime", ["Anime"], "zeker"),
    ]
    assert d["folders"]["tv-anime"] == "/downloads/complete/tv-anime"
    anime = next(i for i in d["instances"] if i["id"] == "sonarr-anime")
    assert anime["clients"][0]["status"] == "ontbreekt_in_sab" and anime["clients"][0]["fix_in"] == "mediaguard"
    assert [h["source"] for h in anime["health"]] == ["DownloadClientCheck"] and anime["health_source"] == "live"
    series = next(i for i in d["instances"] if i["id"] == "sonarr-series")
    assert series["clients"][0]["status"] == "ok"
    assert d["star"]["recent_star"] == 2 and d["star"]["recent_total"] == 3 and len(d["star"]["days"]) == 14
    assert d["star"]["days"][-1]["star"] == 1 and d["sab"]["queue_star"] == 1
    assert d["mediaguard"]["available"] and d["mediaguard"]["provisioning"] is True
    assert d["proposal"] is None  # a proposal is only made on request or by the periodic check
    _no_secrets(d)
    assert_only_allowed_writes(mock)
    # cached: a plain GET does not call MediaGuard again
    n = len(mock.state.calls)
    assert _diag(viewer, refresh=False)["verdict"] == "a" and len(mock.state.calls) == n


def test_case_b_no_category_fix_in_mediaguard_or_arr(app: Any, serve: Any, admin: Any, viewer: Any) -> None:
    _sab(serve, admin)
    mock = _mg(serve, admin)
    for f in mock.state.download_clients["sonarr-series"][0]["fields"]:
        if f["name"] == "tvCategory":
            f["value"] = ""
    films = mock.state.download_clients["radarr-films"][0]
    films["name"] = "SABnzbd"  # created by hand in Radarr, not by MediaGuard
    for f in films["fields"]:
        if f["name"] == "movieCategory":
            f["value"] = None
    mock.state.provisioning["instances"]["radarr-films"]["disabled_sections"] = ["downloadclients"]
    d = _diag(viewer)
    assert d["verdict"] == "b" and not d["missing"]
    by = {b["instance"]: b for b in d["no_category"]}
    assert by["Series"]["fix_in"] == "mediaguard" and by["Series"]["expected"] == "tv"
    assert by["Films"]["fix_in"] == "arr" and by["Films"]["client"] == "SABnzbd"


def test_case_c_categories_match_but_star_from_mso_sends(
    app: Any, serve: Any, admin: Any, viewer: Any, db: Any
) -> None:
    _sab(serve, admin)
    _mg(serve, admin)
    db.execute("INSERT INTO usenet_nzbs(nzo_id,origin,title,category) VALUES ('m1','send_nzb','x','mso-tv')")
    _outcome(db, "m1", "*")
    _outcome(db, "x1", "*")
    d = _diag(viewer)
    assert d["verdict"] == "c" and not d["missing"] and not d["no_category"]
    assert d["other"]["from_mso"] == 1 and d["other"]["unexplained"] == 1
    assert d["mso"]["missing"] == ["mso-tv"] and d["star"]["recent_star_mso"] == 1


def test_without_mediaguard_or_routes_degrades_gracefully(
    app: Any, serve: Any, admin: Any, viewer: Any, db: Any
) -> None:
    _sab(serve, admin, categories=NO_ANIME)
    _outcome(db, "s1", "*")
    d = _diag(viewer)
    assert d["verdict"] == "onbekend" and d["mediaguard"]["reason"] == "niet_gekoppeld" and not d["instances"]
    # older MediaGuard: only the instance list → MediaGuard's own category per instance, marked 'waarschijnlijk'
    _mg(serve, admin, no_category_routes=1)
    d = _diag(viewer)
    assert d["verdict"] == "a" and d["mediaguard"]["provisioning_reason"] == "route_ontbreekt"
    assert {m["category"]: m["certainty"] for m in d["missing"]} == {
        "tv-anime": "waarschijnlijk", "movies-anime": "waarschijnlijk"}
    anime = next(i for i in d["instances"] if i["id"] == "sonarr-anime")
    assert anime["clients_reason"] == "route_ontbreekt" and anime["health_source"] == "cached"
    # role without access to those routes: same graceful result
    _mg(serve, admin, category_routes_forbidden=1)
    d = _diag(viewer)
    anime = next(i for i in d["instances"] if i["id"] == "sonarr-anime")
    assert anime["clients_reason"] == "geen_toegang" and d["mediaguard"]["provisioning_reason"] == "geen_toegang"
    # SABnzbd unreachable: no false verdict
    admin.put("/api/v1/connectors/sabnzbd", json={"url": "http://127.0.0.1:9", "secrets": {"api_key": SAB_KEY}})
    d = _diag(viewer)
    assert d["verdict"] == "onbekend" and d["sab"]["available"] is False and d["sab"]["error"]


def test_star_series_uses_local_days(app: Any, db: Any) -> None:
    from mso.features.usenet import categories

    # 23:30 UTC yesterday = 00:30 (winter) local today in Europe/Amsterdam
    today_local = db.scalar("SELECT (now() AT TIME ZONE 'Europe/Amsterdam')::date")
    local_midnight = db.scalar("SELECT (date_trunc('day', now() AT TIME ZONE 'Europe/Amsterdam')) "
                               "AT TIME ZONE 'Europe/Amsterdam'")
    _outcome(db, "late", "*", when=local_midnight + timedelta(minutes=30))
    _outcome(db, "before", "*", when=local_midnight - timedelta(minutes=30))
    s = categories.star_series()
    assert s["days"][-1]["day"] == today_local.isoformat() and s["days"][-1]["star"] == 1
    assert s["days"][-2]["star"] == 1 and s["timezone"] == "Europe/Amsterdam"


# ------------------------------------------------------------------ the proposal (case a)
def test_proposal_preview_approval_apply_verify_and_guarded_rollback(
    app: Any, serve: Any, admin: Any, operator: Any, viewer: Any, db: Any
) -> None:
    from mso import changes, state

    sab = _sab(serve, admin, categories=NO_ANIME)
    mock = _mg(serve, admin)
    assert viewer.post("/api/v1/usenet/categories/propose").status_code == 403
    assert operator.post("/api/v1/usenet/categories/propose").status_code == 403
    r = admin.post("/api/v1/usenet/categories/propose", json={"names": ["tv-anime"]})
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["target"] == "sabnzbd_categories" and ch["risk"] == "toestemming_nodig" and ch["status"] == "preview"
    assert [(o["path"], o["before"], o["after"]) for o in ch["ops"]] == [
        ("categories/tv-anime", "ontbreekt", "aanwezig · map tv-anime")
    ]
    assert "/downloads/complete/tv-anime" in ch["ops"][0]["label"]
    for phrase in ("MediaGuard ruimt downloads", "kan MSO niet terugdraaien", "Anime", "wachtrij"):
        assert phrase in ch["explanation"], phrase
    assert ch["impact"]["Downloadmap"] == ["tv-anime → /downloads/complete/tv-anime"]
    assert ch["impact"]["Gestuurd door"] == ["tv-anime: Anime"]
    assert "niet terug te draaien" in ch["impact"]["Gevolg in MediaGuard"]
    # idempotent while open; unknown/invalid names are refused
    assert admin.post("/api/v1/usenet/categories/propose", json={"names": ["tv-anime"]}).json()["id"] == ch["id"]
    assert admin.post("/api/v1/usenet/categories/propose", json={"names": ["tv"]}).status_code == 422
    assert admin.post("/api/v1/usenet/categories/propose", json={"names": ["../x"]}).status_code == 422
    # waits for the owner in "Wacht op jou"
    waiting = viewer.get("/api/v1/overview/autopilot").json()["waiting"]
    assert any(i["id"] == ch["id"] and i["kind"] == "goedkeuren" for i in waiting["items"])
    # never automatic, also not in Autopilot; read-only applies nothing
    state.set_state("mode", "autopilot")
    with pytest.raises(HTTPException) as exc:
        changes.apply(ch["id"], "automatisering", automatic=True)
    assert exc.value.status_code == 409
    state.set_state("mode", "read_only")
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    assert operator.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 409
    assert sab.category("tv-anime") is None
    state.set_state("mode", "manage")
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    assert r.json()["ops"][0]["result"] == "toegepast"
    cat = sab.category("tv-anime")
    assert cat["dir"] == "tv-anime" and cat["pp"] == "" and cat["script"] == "Default" and cat["priority"] == -100
    # SABnzbd now files Sonarr's anime jobs under tv-anime instead of '*'
    from mso.connectors.sabnzbd import SabClient

    with SabClient.from_config() as c:
        nzo = c.add_url("https://indexer.test/get/2.nzb", category="tv-anime", name="Frieren.S01E03")
    assert next(s for s in sab.queue if s["nzo_id"] == nzo)["cat"] == "tv-anime"
    d = _diag(viewer)
    assert [m["category"] for m in d["missing"]] == ["movies-anime"] and d["last_applied"]["id"] == ch["id"]
    # rollback refused while a queued download uses the category
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "test"})
    assert r.status_code == 502 and "wachtrij" in r.json()["detail"]
    assert sab.category("tv-anime") is not None
    sab.queue.clear()
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback", json={"reason": "test"})
    assert r.status_code == 200, r.text
    assert sab.category("tv-anime") is None
    assert_only_allowed_writes(mock)


def test_rollback_refuses_category_changed_outside_mso(app: Any, serve: Any, admin: Any, operator: Any) -> None:
    from mso import state

    sab = _sab(serve, admin, categories=NO_ANIME)
    _mg(serve, admin)
    ch = admin.post("/api/v1/usenet/categories/propose").json()
    assert [o["path"] for o in ch["ops"]] == ["categories/movies-anime", "categories/tv-anime"]
    state.set_state("mode", "manage")
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    assert operator.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 200
    sab.category("tv-anime")["dir"] = "/elders/anime"  # the user changed it in SABnzbd
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback", json={})
    assert r.status_code == 502 and "buiten MSO gewijzigd" in r.json()["detail"]
    assert sab.category("tv-anime") is not None


def test_apply_detects_drift_and_failed_writes(app: Any, serve: Any, admin: Any, operator: Any) -> None:
    from mso import state

    sab = _sab(serve, admin, categories=NO_ANIME)
    _mg(serve, admin)
    ch = admin.post("/api/v1/usenet/categories/propose", json={"names": ["tv-anime"]}).json()
    state.set_state("mode", "manage")
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    sab.add_category("tv-anime", dir="anime")  # created meanwhile in SABnzbd with another folder
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 409 and "Afwijking" in r.json()["detail"]
    assert sab.category("tv-anime")["dir"] == "anime"


def test_apply_verification_failure_when_sab_config_is_locked(
    app: Any, serve: Any, admin: Any, operator: Any
) -> None:
    from mso import state

    sab = _sab(serve, admin, categories=NO_ANIME, config_locked=1)
    _mg(serve, admin)
    ch = admin.post("/api/v1/usenet/categories/propose", json={"names": ["tv-anime"]}).json()
    state.set_state("mode", "manage")
    admin.post(f"/api/v1/changes/{ch['id']}/approve")
    r = operator.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 502 and "Configuration locked" in r.json()["detail"]
    assert sab.category("tv-anime") is None
    assert admin.get(f"/api/v1/changes/{ch['id']}").json()["status"] == "failed"


def test_applier_policy_refuses_other_values(app: Any, serve: Any, admin: Any) -> None:
    from mso import changes

    _sab(serve, admin)
    for path, after in (("categories/*", "aanwezig · map *"), ("categories/tv", "aanwezig · map /etc"),
                        ("servers/x/enable", "ontbreekt")):
        with pytest.raises(HTTPException) as exc:
            changes.create("sabnzbd_categories", "x", [{"path": path, "before": "ontbreekt", "after": after}],
                           risk="toestemming_nodig")
        assert exc.value.status_code == 422


def test_periodic_check_keeps_one_proposal_and_retires_it(
    app: Any, serve: Any, admin: Any, viewer: Any, db: Any
) -> None:
    from mso import jobs
    from mso.features.usenet import categories

    sab = _sab(serve, admin, categories=NO_ANIME)
    _mg(serve, admin)
    assert any(s.kind == "usenet.categories_check" for s in jobs.SCHEDULES)
    res = categories.sync()
    assert res["verdict"] == "a" and res["missing"] == ["movies-anime", "tv-anime"] and res["proposal"]
    assert categories.sync()["proposal"] == res["proposal"]  # idempotent
    assert db.scalar("SELECT count(*) FROM change_sets WHERE source='usenet.categories'") == 1
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key LIKE 'usenet.categories:%%'")
    assert rec["status"] == "open" and rec["change_id"] == res["proposal"] and rec["risk"] == "toestemming_nodig"
    # the owner creates one category himself → the proposal is replaced by one for the rest
    sab.add_category("tv-anime", dir="tv-anime")
    res2 = categories.sync()
    assert res2["missing"] == ["movies-anime"] and res2["proposal"] != res["proposal"]
    assert db.scalar("SELECT status FROM change_sets WHERE id=%s", (res["proposal"],)) == "discarded"
    sab.add_category("movies-anime", dir="movies-anime")
    res3 = categories.sync()
    assert res3["verdict"] == "ok" and res3["proposal"] is None
    assert db.scalar("SELECT count(*) FROM change_sets WHERE source='usenet.categories' AND status='preview'") == 0
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key LIKE 'usenet.categories:%%' "
                     "AND status='open'") == 0
    # the problem comes back (category removed in SABnzbd): a fresh proposal and the recommendation reopens
    sab.categories.remove(sab.category("movies-anime"))
    res4 = categories.sync()
    assert res4["missing"] == ["movies-anime"] and res4["proposal"] not in (None, res2["proposal"])
    rec = db.fetchone("SELECT * FROM recommendations WHERE status='open' AND dedupe_key LIKE 'usenet.categories:%%'")
    assert rec["change_id"] == res4["proposal"]
    sched = {i["kind"]: i for i in viewer.get("/api/v1/automation/schedules").json()["items"]}
    assert sched["usenet.categories_check"]["area"] == "Usenet" and sched["usenet.categories_check"]["enabled"]


def test_periodic_check_without_mediaguard_changes_nothing(app: Any, serve: Any, admin: Any, db: Any) -> None:
    from mso.features.usenet import categories

    _sab(serve, admin, categories=NO_ANIME)
    assert categories._enabled() is False
    d = categories.diagnose(refresh=True)
    assert d["verdict"] == "geen_ster" and d["mediaguard"]["reason"] == "niet_gekoppeld"  # never a false "ok"
    assert "skipped" in categories.sync()
    assert db.scalar("SELECT count(*) FROM change_sets") == 0


# ------------------------------------------------------------------ pure rules (no network)
def test_evaluate_rules_unit(app: Any) -> None:
    from mso.features.usenet import categories

    def inst(iid: str, clients: list[dict[str, Any]] | None, media: str = "tv") -> dict[str, Any]:
        return {"id": iid, "name": iid.title(), "app": "sonarr", "media_type": media, "download_category": None,
                "clients": clients, "clients_reason": None if clients is not None else "route_ontbreekt",
                "health": [], "health_source": "cached"}

    def client(cat: str | None, *, enable: bool = True, owned: bool = False) -> dict[str, Any]:
        return {"name": "SABnzbd", "implementation": "Sabnzbd", "enable": enable, "category": cat,
                "mediaguard_owned": owned}

    remote = {
        "sab": {"available": True, "categories": [{"name": "*", "dir": ""}, {"name": "tv", "dir": ""}],
                "complete_dir": None, "queue_star": 0},
        "mediaguard": {
            "instances": [
                inst("upper", [client("TV")]),  # SABnzbd stores lower case: 'TV' is known
                inst("off", [client("nope", enable=False)]),  # disabled client: not a cause
                inst("hold", [client("")]),
                inst("nosab", []),
                inst("old", None, media="anime"),  # unreadable → MediaGuard's own category, probable
            ],
            "provisioning": {
                "download_clients": [{"name": "SABnzbd", "implementation": "Sabnzbd", "enabled": True,
                                      "instances": None, "categories": {"upper": "tv"}}],
                "instances": {"hold": {"mode": "hold", "downloadclients_disabled": False}},
                "default_categories": {},
            },
        },
    }
    stats = {"recent_star": 0, "recent_star_mso": 0}
    ev = categories._evaluate(remote, stats)
    by = {i["id"]: i for i in ev["instances"]}
    assert by["upper"]["clients"][0]["status"] == "ok" and by["upper"]["provisioned"] is True
    assert by["upper"]["clients"][0]["fix_in"] == "mediaguard"
    assert by["off"]["clients"][0]["status"] == "uit"
    assert by["hold"]["clients"][0]["status"] == "geen_categorie" and by["hold"]["clients"][0]["fix_in"] == "arr"
    assert by["nosab"]["no_sab_client"] is True and by["nosab"]["clients"] == []
    assert by["old"]["clients"][0]["certainty"] == "waarschijnlijk" and by["old"]["expected_category"] == "tv-anime"
    assert ev["verdict"] == "a" and [(m["category"], m["certainty"]) for m in ev["missing"]] == [
        ("tv-anime", "waarschijnlijk")]
    assert ev["no_category"] == [{"instance": "Hold", "client": "SABnzbd", "fix_in": "arr", "expected": "tv"}]
    assert ev["folders"] == {"tv-anime": "tv-anime"}
