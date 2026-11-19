"""Gebonden opdrachten bij een nieuwe verzending, een bestaand ontvangstbewijs en een SABnzbd-retry."""

import sys
from types import SimpleNamespace

import pytest
import test_recovery_fallback as fallback
import test_release_gate as gate
import test_usenet_retry as retry
from mg_support import release
from msomocks import sabnzbd as sim

from mso.connectors.sabnzbd import SabClient, SabError, _nzo_id
from mso.domain.releasegate import torrent_download_id

sab = fallback.sab
qbit = gate.qbit


def test_magnetverzending_vereist_eenduidige_torrenthash():
    first, other = "a" * 40, "b" * 40
    magnet = "magnet:?xt=urn:btih:" + first
    assert torrent_download_id(SimpleNamespace(final_url=magnet), first) == first
    for extra in ["&xt=urn:btih:" + other, "&xt=urn:btih:ongeldig", "\n" + magnet]:
        with pytest.raises(ValueError):
            torrent_download_id(SimpleNamespace(final_url=magnet + extra), first)


def applied(db, change_id):
    return db.scalar("SELECT count(*) FROM audit_log WHERE change_id=%s AND action='change.applied'", (change_id,))


def approved_send(admin, viewer, monkeypatch):
    candidate = release(fallback.GOOD, key="good1", cat="tv")
    fallback._search(monkeypatch, [candidate])
    fallback._failed(fallback.FAILED)
    assert fallback._run()["searched"] == 1
    item, = fallback._fallbacks(viewer)
    route = f"/api/v1/changes/{item['change_id']}"
    preview = viewer.get(route).json()
    assert preview["ops"][0]["command"]["fields"]["cat"] == "mso-tv"
    assert admin.post(route + "/approve").status_code == 200
    return item, route, candidate.release_key


@pytest.mark.parametrize("wijziging", ["instelling", "categorie-ontbreekt", "inhoud"])
def test_herstelverzending_weigert_gewijzigde_opdracht(admin, operator, viewer, sab, manage_mode, db,
                                                       monkeypatch, wijziging):
    item, route, _ = approved_send(admin, viewer, monkeypatch)
    if wijziging == "instelling":
        assert admin.put("/api/v1/settings", json={"values": {"herstel.send_category_mode": "mediaguard"}}).is_success
    elif wijziging == "categorie-ontbreekt":
        sab.categories.remove(sab.category("mso-tv"))
    else:
        search = sys.modules["mso.features.search.service"]
        fetch = search.fetch_download
        monkeypatch.setattr(search, "fetch_download", lambda token: SimpleNamespace(
            content=fetch(token).content + b"\n", filename=fetch(token).filename, final_url=None))
    assert operator.post(route + "/apply").status_code >= 400
    assert not sab.queue and applied(db, item["change_id"]) == 0
    assert db.scalar("SELECT count(*) FROM sab_send_receipts") == 0


@pytest.mark.parametrize("afwijking", ["andere-instantie", "oud-zonder-bewijs", "andere-categorie", "origineel"])
def test_eerdere_verzending_bindt_instantie_en_download(admin, operator, viewer, sab, serve, manage_mode, db,
                                                       monkeypatch, afwijking):
    item, route, key = approved_send(admin, viewer, monkeypatch)
    sent = operator.post(f"/api/v1/recovery/items/{item['media_ref']}/send", json={"release_key": key})
    assert sent.is_success
    receipt = sent.json()["send"]
    download, = sab.queue
    proof = db.fetchone("SELECT * FROM sab_send_receipts WHERE nzo_id=%s", (download["nzo_id"],))
    assert receipt["client_ref"] == download["nzo_id"] and proof["instance"] == receipt["command"]["instance"]
    if afwijking == "andere-instantie":
        other = sim.create_app(api_key=fallback.SAB_KEY, scenario="empty", categories="mso-tv")
        other.state.sab.queue.append(dict(download, filename="Andere download"))
        sab.queue.clear()
        assert admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(other)}).is_success
    elif afwijking == "oud-zonder-bewijs":
        db.execute("UPDATE recovery_sends SET command=NULL WHERE id=%s", (receipt["id"],))
    elif afwijking == "andere-categorie":
        download["cat"] = "tv"
    answer = operator.post(route + "/apply")
    if afwijking == "origineel":
        assert answer.is_success and applied(db, item["change_id"]) == 1
    else:
        assert answer.status_code >= 400 and applied(db, item["change_id"]) == 0
    assert db.scalar("SELECT count(*) FROM sab_send_receipts") == 1


@pytest.mark.parametrize("dubbel", ["bron", "antwoord", "bestaand-doel"])
def test_retry_weigert_dubbelzinnig_download_id(admin, operator, serve, manage_mode, db, monkeypatch, dubbel):
    sab = retry._start(serve, admin, retry_noop=1)
    target, = retry._fail_jobs(sab, 1)
    retry._gain(sab, db)
    retry._switch_off_failing(sab)
    other = "ander-doel"
    sab.queue.append({"nzo_id": other, "filename": "Ander testitem", "cat": "tv", "status": "Queued"})
    cid = operator.post("/api/v1/usenet/retries/propose").json()["change_id"]
    if dubbel == "bron":
        sab.history.append(dict(next(s for s in sab.history if s["nzo_id"] == target)))
    else:
        call = SabClient._call

        def reply(client, mode, *args, **kwargs):
            result = call(client, mode, *args, **kwargs)
            if mode == "retry":
                assert result["nzo_id"] == target
                return {"status": True, "nzo_ids": [other, target] if dubbel == "antwoord" else [other]}
            return result

        monkeypatch.setattr(SabClient, "_call", reply)
    assert operator.post(f"/api/v1/changes/{cid}/apply").status_code >= 400
    assert sab.retried == ([] if dubbel == "bron" else [target])
    assert [s["nzo_id"] for s in sab.queue] == [other] and applied(db, cid) == 0
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='sabnzbd.retry'") == 0


@pytest.mark.parametrize("antwoord", [{}, {"nzo_ids": []}, {"nzo_ids": "taak"}, {"nzo_id": False},
                                     {"nzo_id": "taak", "nzo_ids": ["ander"]}, {"nzo_ids": ["een", "twee"]}])
def test_sab_ontvangstbewijs_heeft_een_download_id(antwoord):
    with pytest.raises(SabError, match="eenduidig"):
        _nzo_id({"status": True, **antwoord})


@pytest.mark.parametrize("wissel", ["stabiel", "oud-naar-nieuw", "nieuw-naar-oud", "eerder-verstuurd"])
def test_torrentverzending_bindt_hash_categorie_en_pauzeveld(
    admin, operator, viewer, qbit, manage_mode, db, monkeypatch, wissel,
):
    qbit.webapi = "2.16.2" if wissel == "nieuw-naar-oud" else "2.8.3"
    candidate = release(fallback.GOOD, protocol="torrent", key="torrent", cat="tv", seeders=40)
    fallback._search(monkeypatch, [candidate])
    fallback._failed(fallback.FAILED, key="btih:" + "a" * 40, protocol="torrent", ref="a" * 40)
    assert fallback._run()["searched"] == 1
    item, = fallback._fallbacks(viewer)
    cid = item["change_id"]
    route = f"/api/v1/changes/{cid}"
    command = viewer.get(route).json()["ops"][0]["command"]
    pause = "stopped" if wissel == "nieuw-naar-oud" else "paused"
    assert command["download_id"] == candidate.infohash_v1 and command["category"] == "mso-tv"
    assert {f for f in command["fields"] if f in {"stopped", "paused"}} == {pause}
    assert admin.post(route + "/approve").is_success
    if wissel == "eerder-verstuurd":
        answer = operator.post(f"/api/v1/recovery/items/{item['media_ref']}/send",
                               json={"release_key": candidate.release_key})
        assert answer.is_success and answer.json()["send"]["command"] == command
    before = sum(path.endswith("/torrents/add") for _, path, _ in qbit.calls)
    if wissel != "stabiel":
        qbit.webapi = "2.8.3" if wissel == "nieuw-naar-oud" else "2.16.2"
    answer = operator.post(route + "/apply")
    calls = sum(path.endswith("/torrents/add") for _, path, _ in qbit.calls) - before
    if wissel == "stabiel":
        assert answer.is_success and calls == applied(db, cid) == 1
        assert qbit.torrents[candidate.infohash_v1]["category"] == "mso-tv"
        row = db.fetchone("SELECT client_ref,command FROM recovery_sends WHERE status='sent'")
        assert row == {"client_ref": candidate.infohash_v1, "command": command}
    else:
        assert answer.status_code >= 400 and calls == applied(db, cid) == 0


@pytest.mark.parametrize("wijziging", ["naam", "verdwenen", "dubbel", "dubbel-bewijs", "gelijk"])
def test_herstel_controleert_het_eerdere_sab_formulier(admin, operator, viewer, sab, manage_mode, db,
                                                       monkeypatch, wijziging):
    item, route, _ = approved_send(admin, viewer, monkeypatch)
    filename = "Andere naam" if wijziging == "naam" else fallback.GOOD
    with SabClient.from_config() as client:
        ref = client.add_nzb(fallback.NZB, filename, category="mso-tv")
    slot, = sab.queue
    assert slot["nzo_id"] == ref
    if wijziging == "verdwenen":
        sab.queue.clear()
    elif wijziging == "dubbel":
        sab.history.append(dict(slot, status="Completed", completed=1))
    elif wijziging == "dubbel-bewijs":
        db.execute("INSERT INTO sab_send_receipts(instance,category,nzo_id,request_hash,sha256) "
                   "SELECT instance,category,'tweede-doel',request_hash,sha256 FROM sab_send_receipts")
    before = list(sab.queue)
    receipts = db.scalar("SELECT count(*) FROM sab_send_receipts")
    answer = operator.post(route + "/apply")
    if wijziging == "gelijk":
        assert answer.is_success and applied(db, item["change_id"]) == 1
    else:
        assert answer.status_code >= 400 and applied(db, item["change_id"]) == 0
        error = db.scalar("SELECT error FROM recovery_sends WHERE status='failed'")
        reason = {"naam": "formuliervelden", "verdwenen": "ontbreekt", "dubbel": "niet eenduidig",
                  "dubbel-bewijs": "meerdere verschillende ontvangstbewijzen"}
        assert reason[wijziging] in error
    assert sab.queue == before and db.scalar("SELECT count(*) FROM sab_send_receipts") == receipts
