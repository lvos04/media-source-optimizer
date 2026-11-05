"""Malware cleanup (1.4.0): suspicious torrents removed WITH their files by ONE approved change set.

Covers: preview (impact per torrent, irreversible line), read-only/role/approval gates, apply verified at the
source (torrent gone, MSO blacklist entry), audit lines, rollback refused, no secrets, drift refusal (one gone /
new one / no longer suspicious), a non-suspicious torrent can never be deleted (also with crafted ops), the
recommendation → proposal link, and the optional Sonarr/Radarr blocklist via MediaGuard.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from mso import changes, state
from mso.connectors.qbittorrent import QbitClient

GB = 1024**3
PASSKEY = "GEHEIM123"


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


@pytest.fixture()
def qb(admin: Any, serve: Any) -> Qb:
    from msomocks.qbittorrent import create_app

    app = create_app(version="5.3.0", webapi="2.16.2")
    url = serve(app)
    r = admin.put(
        "/api/v1/connectors/qbittorrent",
        json={"url": url, "secrets": {"username": "admin", "password": "adminadmin"}},
    )
    assert r.status_code == 200
    return Qb(app)


def ingest() -> dict[str, Any]:
    from mso.features.torrent.ingest import ingest as run

    return run()


PUB = f"https://tracker.publiek.example/{PASSKEY}/announce"
NAMES = {
    "exe": "Criminal Minds S19E06 1080p WEB h264-GRACE.exe",
    "scr": "House of the Dragon S03E05 1080p HEVC x265-MeGusta.scr",
    "file": "Clean.Name.S01E01.1080p.WEB.h264-GRP",
}


def _add(qb: Qb, key: str, name: str, files: list[tuple[str, int]], category: str = "tv-sonarr") -> str:
    from msomocks.qbittorrent import _torrent

    tr = [{"url": PUB, "status": 2, "tier": 0, "num_peers": 3, "num_seeds": 9, "num_leeches": 3,
           "num_downloaded": 1, "msg": ""}]
    t = _torrent(name, category=category, size=GB, progress=1.0, state="stalledUP", tracker=PUB, private=False,
                 comment=f"https://indexer.voorbeeld.example/details/42?passkey={PASSKEY}")
    qb.st.add(t, tr, files)
    return qb.hash(name)


def _setup(qb: Qb) -> dict[str, str]:
    hs = {
        "exe": _add(qb, "exe", NAMES["exe"], [(NAMES["exe"], GB)]),
        "scr": _add(qb, "scr", NAMES["scr"], [(NAMES["scr"], GB)]),
        "file": _add(qb, "file", NAMES["file"], [(f"{NAMES['file']}/{NAMES['file']}.mkv.exe", GB),
                                                 (f"{NAMES['file']}/sample.mkv", 1000)]),
        "app": _add(qb, "app", "SomeApp 2.0 Portable", [("SomeApp/setup.exe", GB)], category="software"),
    }
    ingest()
    return hs


def _healthy(qb: Qb) -> list[str]:
    return [h for h, t in qb.st.torrents.items() if not str(t["name"]).startswith(("Criminal", "House", "Clean",
                                                                                     "SomeApp"))]


def _approve_apply(admin: Any, cid: int) -> Any:
    assert admin.post(f"/api/v1/changes/{cid}/approve").status_code == 200
    return admin.post(f"/api/v1/changes/{cid}/apply")


def _no_secret(*blobs: Any) -> None:
    dump = json.dumps(blobs, default=str)
    assert PASSKEY not in dump and "adminadmin" not in dump


# ------------------------------------------------------------------ bundle: preview → approve → apply
def test_bundle_preview_apply_verified_audited_not_reversible(
    qb: Qb, admin: Any, operator: Any, viewer: Any, db: Any, manage_mode: None, caplog: Any
) -> None:
    caplog.set_level(logging.DEBUG)
    hs = _setup(qb)
    healthy_before = set(_healthy(qb))
    # read-only listing for the banner
    lst = viewer.get("/api/v1/torrent/suspicious").json()
    assert {i["hash"] for i in lst["items"]} == {hs["exe"], hs["scr"], hs["file"]} and lst["open_change"] is None
    # viewers cannot create a proposal
    assert viewer.post("/api/v1/torrent/suspicious/cleanup-preview").status_code == 403
    r = operator.post("/api/v1/torrent/suspicious/cleanup-preview")
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["title"] == "Alle 3 verdachte torrents opruimen"
    assert ch["risk"] == "toestemming_nodig" and ch["status"] == "preview" and ch["evaluate_after"] is None
    # ops: guard → blacklist ×3 → delete ×3 (destructive + irreversible); the app torrent is never included
    kinds = [o["path"].split(":")[0] for o in ch["ops"]]
    assert kinds == ["cleanup", "blacklist", "blacklist", "blacklist", "torrent", "torrent", "torrent"]
    deletes = [o for o in ch["ops"] if o["path"].startswith("torrent:")]
    assert {o["path"].split(":")[1] for o in deletes} == {hs["exe"], hs["scr"], hs["file"]}
    assert all(o["destructive"] and o["irreversible"] and o["before"] == "aanwezig" and o["after"] == "verwijderd"
               and ":delete_with_files:" in o["path"] for o in deletes)
    assert all(o["irreversible"] for o in ch["ops"] if o["path"].startswith("blacklist:"))
    # impact per torrent: name, suspicious file(s), tracker host, category, size + the irreversible line
    imp = ch["impact"]
    assert imp["Gevolg"] == "Niet terug te draaien: torrent en bestanden worden verwijderd"
    rows = {t["Naam"]: t for t in imp["Torrents"]}
    assert set(rows) == set(NAMES.values())
    f = rows[NAMES["file"]]
    assert f["Verdacht bestand"].endswith(".mkv.exe") and f["Tracker"] == "tracker.publiek.example"
    assert f["Categorie"] == "tv-sonarr" and f["Grootte"] and f["Gevolg"].startswith("Niet terug te draaien")
    assert "MSO-blacklist" in imp["Blokkeren"] and "MediaGuard is niet gekoppeld" in imp["Blokkeren"]
    # nothing happened yet; approval is required; read-only refuses
    assert not qb.st.deletes and viewer.get("/api/v1/torrent/suspicious").json()["open_change"]["id"] == ch["id"]
    assert admin.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 409  # needs approval
    assert admin.post(f"/api/v1/changes/{ch['id']}/approve").status_code == 200
    with pytest.raises(HTTPException) as exc:
        changes.apply(ch["id"], "automatisering", automatic=True)  # never automatic
    assert exc.value.status_code == 409 and not qb.st.deletes
    state.set_state("mode", "read_only")
    assert admin.post(f"/api/v1/changes/{ch['id']}/apply").status_code == 409
    assert not qb.st.deletes
    state.set_state("mode", "manage")
    r = admin.post(f"/api/v1/changes/{ch['id']}/apply")
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "applied"
    # verified at the source: exactly the three are gone (with files), healthy and the app torrent stay
    assert sorted(h for d in qb.st.deletes for h in d[0]) == sorted([hs["exe"], hs["scr"], hs["file"]])
    assert all(files for _, files in qb.st.deletes)
    assert set(qb.st.torrents) == healthy_before | {hs["app"]}
    assert all(o["result"] == "toegepast" for o in done["ops"] if not o["path"].startswith("cleanup:"))
    from mso.features.recovery import blacklist

    for k in ("exe", "scr", "file"):
        assert blacklist.is_blacklisted("btih:" + hs[k])
    # audit: change.applied + one torrent.suspicious_removed per torrent (name + tracker host)
    removed = db.fetchall("SELECT * FROM audit_log WHERE action='torrent.suspicious_removed' ORDER BY id")
    assert len(removed) == 3 and all(a["change_id"] == ch["id"] for a in removed)
    assert {a["detail"]["name"] for a in removed} == set(NAMES.values())
    assert {a["detail"]["tracker"] for a in removed} == {"tracker.publiek.example"}
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.applied' AND change_id=%s", (ch["id"],))
    # rollback is refused with a clear message; nothing restored
    r = admin.post(f"/api/v1/changes/{ch['id']}/rollback")
    assert r.status_code == 409 and "niet terug te draaien" in r.json()["detail"]
    # the engine never evaluates a deletion
    from mso.features.automation import engine

    engine.evaluate_due()
    assert changes.get(ch["id"])["impact"]["evaluation"]["result"] == "niet_van_toepassing"
    # no secrets (tracker passkey, comment passkey, password) anywhere
    _no_secret(ch, done, [dict(a) for a in db.fetchall("SELECT * FROM audit_log")],
               [dict(c) for c in db.fetchall("SELECT * FROM change_sets")], caplog.text)
    # gone from the suspicious list after the next ingest
    ingest()
    assert viewer.get("/api/v1/torrent/suspicious").json()["items"] == []


# ------------------------------------------------------------------ drift: refuse the whole bundle
@pytest.mark.parametrize("drift", ["gone", "new", "clean"])
def test_drift_refuses_whole_bundle(qb: Qb, admin: Any, operator: Any, manage_mode: None, drift: str) -> None:
    hs = _setup(qb)
    ch = operator.post("/api/v1/torrent/suspicious/cleanup-preview").json()
    if drift == "gone":
        del qb.st.torrents[hs["scr"]]
    elif drift == "new":
        _add(qb, "new", "Some Show S02E02 720p WEB.scr", [("Some Show S02E02 720p WEB.scr", GB)])
    else:  # the file list no longer contains a program (e.g. replaced by the real release)
        h = hs["file"]
        qb.st.files[h] = [{**qb.st.files[h][1], "name": f"{NAMES['file']}/episode.mkv"}]
    before = dict(qb.st.torrents)
    r = _approve_apply(admin, ch["id"])
    assert r.status_code == 422, r.text
    assert "lijst gewijzigd, maak een nieuw voorstel" in r.json()["detail"]
    assert not qb.st.deletes and set(qb.st.torrents) == set(before)
    assert changes.get(ch["id"])["status"] == "approved"  # nothing written, can be discarded
    from mso.features.recovery import blacklist

    assert not blacklist.is_blacklisted("btih:" + hs["exe"])


# ------------------------------------------------------------------ a non-suspicious torrent is never deletable
def test_non_suspicious_never_deletable(qb: Qb, admin: Any, operator: Any, db: Any, manage_mode: None) -> None:
    from mso.features.torrent import cleanup

    hs = _setup(qb)
    healthy = _healthy(qb)[0]
    # 1. endpoint refuses explicit hashes that are not suspicious (healthy torrent, software with an .exe)
    for h in (healthy, hs["app"]):
        r = operator.post("/api/v1/torrent/suspicious/cleanup-preview", json={"hashes": [h]})
        assert r.status_code == 422 and "alleen torrents die" in r.json()["detail"]
    # 2. crafted delete op injected next to a real preview's guard → refused (not in preview, not suspicious)
    real = operator.post("/api/v1/torrent/suspicious/cleanup-preview", json={"hashes": [hs["exe"]]}).json()
    token = real["ops"][0]["path"].split(":")[1]
    crafted = [dict(real["ops"][0]), {"path": f"torrent:{healthy}:delete_with_files:{token}", "label": "x",
                                      "before": "aanwezig", "after": "verwijderd", "destructive": True,
                                      "irreversible": True}]
    with pytest.raises(HTTPException) as exc:
        changes.create("qbittorrent", "kwaadaardig", crafted, risk="toestemming_nodig")
    assert exc.value.status_code == 422 and "niet in het voorbeeld" in exc.value.detail
    # 3. a delete op without guard → refused
    with pytest.raises(HTTPException) as exc:
        changes.create("qbittorrent", "zonder controle", crafted[1:], risk="toestemming_nodig")
    assert exc.value.status_code == 422 and "geldig opruimvoorstel" in exc.value.detail
    # 4. a forged preview that lists the healthy torrent → the fresh scan refuses it
    tok = cleanup.store_preview("selection", [healthy])
    forged = [{"path": f"cleanup:{tok}", "label": "c", "before": cleanup.guard_value(1),
               "after": cleanup.guard_value(1)},
              {"path": f"torrent:{healthy}:delete_with_files:{tok}", "label": "x", "before": "aanwezig",
               "after": "verwijderd", "destructive": True, "irreversible": True}]
    with pytest.raises(HTTPException) as exc:
        changes.create("qbittorrent", "vervalst", forged, risk="toestemming_nodig")
    assert exc.value.status_code == 422 and "lijst gewijzigd" in exc.value.detail
    # 5. old-style / other delete paths are refused by the policy
    for path in (f"torrent:{healthy}:delete", f"torrent:{healthy}:delete_with_files", f"torrent:{healthy}:remove"):
        with pytest.raises(HTTPException):
            changes.create("qbittorrent", "x", [{"path": path, "after": True, "destructive": True}],
                           risk="toestemming_nodig")
    # 6. even if a crafted row reaches the database, apply re-validates against the fresh scan
    row = db.fetchone(
        "INSERT INTO change_sets(target,title,risk,ops,status,approved_by) VALUES ('qbittorrent','rauw',"
        "'toestemming_nodig',%s,'approved','admin') RETURNING id",
        (json.dumps(forged),),
    )
    r = admin.post(f"/api/v1/changes/{row['id']}/apply")
    assert r.status_code == 422
    assert healthy in qb.st.torrents and not qb.st.deletes
    # 7. a single suspicious torrent that is cleaned up between preview and apply is refused too
    qb.st.torrents[hs["exe"]]["name"] = "Criminal Minds S19E06 1080p WEB h264-GRACE"
    qb.st.files[hs["exe"]] = [{**qb.st.files[hs["exe"]][0], "name": "Criminal Minds S19E06.mkv"}]
    r = _approve_apply(admin, real["id"])
    assert r.status_code == 422 and not qb.st.deletes


# ------------------------------------------------------------------ recommendation → proposal
def test_recommendation_accept_creates_cleanup_proposal(
    qb: Qb, admin: Any, operator: Any, viewer: Any, db: Any, manage_mode: None
) -> None:
    hs = _setup(qb)
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s", ("torrent-suspicious:btih:" + hs["scr"],))
    assert rec["title"].startswith("Verdachte torrent verwijderen")
    items = viewer.get("/api/v1/automation/recommendations?area=torrent").json()["items"]
    assert next(i for i in items if i["id"] == rec["id"])["can_create_change"] is True
    r = operator.post(f"/api/v1/automation/recommendations/{rec['id']}/accept")
    assert r.status_code == 200, r.text
    ch = changes.get(r.json()["change"]["id"])
    assert ch["risk"] == "toestemming_nodig" and ch["status"] == "preview"
    assert [o["path"].split(":")[0] for o in ch["ops"]] == ["cleanup", "blacklist", "torrent"]
    assert ch["ops"][2]["path"].split(":")[1] == hs["scr"]
    # per-torrent change is scoped to its torrent: other suspicious torrents do not count as drift
    res = _approve_apply(admin, ch["id"])
    assert res.status_code == 200, res.text
    assert qb.st.deletes == [([hs["scr"]], True)]
    assert hs["exe"] in qb.st.torrents and hs["file"] in qb.st.torrents
    _no_secret(ch, res.json())


# ------------------------------------------------------------------ Sonarr/Radarr blocklist via MediaGuard
def _mg_app(download_id: str) -> FastAPI:
    app = FastAPI()
    app.state.calls = []
    app.state.queue = [{"id": 77, "downloadId": download_id.upper(), "title": "x", "protocol": "torrent"}]

    @app.middleware("http")
    async def rec(request: Request, call_next):  # type: ignore[no-untyped-def]
        app.state.calls.append((request.method, request.url.path, str(request.url.query)))
        return await call_next(request)

    def ok(request: Request) -> bool:
        return request.headers.get("x-api-key") == "mgk_testsleutel"

    @app.get("/api/v1/instances")
    async def instances(request: Request) -> Any:
        if not ok(request):
            return JSONResponse({"detail": "Ongeldige of ingetrokken API-sleutel"}, 401)
        return [{"id": "sonarr", "name": "Sonarr", "archived": False}]

    @app.get("/api/v1/instances/{iid}/queue")
    async def queue(iid: str, request: Request) -> Any:
        return {"page": 1, "pageSize": 200, "totalRecords": len(app.state.queue), "records": app.state.queue}

    @app.delete("/api/v1/instances/{iid}/queue/{qid}")
    async def remove(iid: str, qid: int, request: Request) -> Any:
        assert request.query_params["blocklist"] == "true"
        assert request.query_params["remove_from_client"] == "false"
        app.state.queue = [q for q in app.state.queue if q["id"] != qid]
        return {"removed": True}

    @app.get("/api/v1/{rest:path}")
    async def other(rest: str) -> Any:
        return JSONResponse({"detail": "Not Found"}, 404)

    return app


def test_mediaguard_blocklist_when_route_allowed(
    qb: Qb, admin: Any, operator: Any, serve: Any, manage_mode: None, monkeypatch: Any
) -> None:
    import re

    from mso.connectors import mediaguard as mgc
    from mso.features.torrent import cleanup

    hs = _setup(qb)
    mg = _mg_app(hs["exe"])
    url = serve(mg)
    r = admin.put("/api/v1/connectors/mediaguard", json={
        "url": url, "enabled": True, "options": {"auth_method": "apikey"},
        "secrets": {"api_key": "mgk_testsleutel"}})
    assert r.status_code == 200, r.text
    # today MSO's MediaGuard client does not whitelist the route → MSO blacklist only, explained in the preview
    ok, why = cleanup.mediaguard_block_status()
    assert not ok and "nog niet toe" in why
    # with the whitelist entry the coordinator is asked to add:
    monkeypatch.setattr(mgc, "ALLOWED_WRITES", mgc.ALLOWED_WRITES + (
        ("DELETE", re.compile(r"^/api/v1/instances/[A-Za-z0-9_.-]+/queue/\d+$")),))
    ch = operator.post("/api/v1/torrent/suspicious/cleanup-preview", json={"hashes": [hs["exe"]]}).json()
    assert [o["path"].split(":")[0] for o in ch["ops"]] == ["cleanup", "arrblock", "blacklist", "torrent"]
    assert "via MediaGuard" in ch["impact"]["Blokkeren"]
    res = _approve_apply(admin, ch["id"])
    assert res.status_code == 200, res.text
    assert ("DELETE", "/api/v1/instances/sonarr/queue/77", "remove_from_client=false&blocklist=true") in mg.state.calls
    assert qb.st.deletes == [([hs["exe"]], True)]
    # blocked first, deleted after
    assert [o["result"] for o in res.json()["ops"][1:]] == ["toegepast", "toegepast", "toegepast"]
    _no_secret(res.json())
    assert "mgk_testsleutel" not in json.dumps(res.json())
