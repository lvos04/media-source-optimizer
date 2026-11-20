"""Herkomstbewijs via de verzendroute, met twee onafhankelijke diensten achter hetzelfde adres."""

import hashlib
import time

import pytest
import test_download_commands as commands
import test_recovery_fallback as fallback
import test_release_gate as gate
from fastapi.testclient import TestClient
from mg_support import release
from msomocks import qbittorrent as qsim
from msomocks import sabnzbd as sim

from mso.connectors import base
from mso.connectors.sabnzbd import SabClient
from mso.features.recovery import actions

sab = fallback.sab
qbit = gate.qbit


@pytest.fixture
def sab_verzoeken(monkeypatch):
    verzoeken = []
    uitvoeren = SabClient._call

    def meet(client, modus, *args, **kwargs):
        verzoeken.append((kwargs.get("method", "GET"), modus))
        return uitvoeren(client, modus, *args, **kwargs)

    monkeypatch.setattr(SabClient, "_call", meet)
    return verzoeken


def test_sab_wachtrijregel_zonder_verzendbewijs(monkeypatch, sab_verzoeken):
    adres = "http://ontvanger.test"
    bron = sim.create_app(api_key=fallback.SAB_KEY, scenario="empty", categories="mso-tv")
    doel = {"nzo_id": "taak-uit-de-proef", "cat": "mso-tv", "filename": "Oorspronkelijk testitem"}
    bron.state.sab.queue.append(doel)
    monkeypatch.setattr(base, "http_client", lambda url, **kw: TestClient(bron, base_url=url))
    monkeypatch.setattr(actions, "download_client", lambda _: SabClient(adres, fallback.SAB_KEY))
    ontvangst = {"client": "sabnzbd", "client_ref": doel["nzo_id"]}
    opdracht = {"instance": base.instance_id(adres), "category": doel["cat"]}

    assert actions.confirmed_download(ontvangst, opdracht) is False
    assert bron.state.sab.queue == [doel]
    assert sab_verzoeken == [("GET", "queue"), ("GET", "history")]


def test_sab_leesbevestiging_met_verzendbewijs(
    admin, operator, viewer, sab, manage_mode, db, monkeypatch, sab_verzoeken,
):
    item, _, sleutel = commands.approved_send(admin, viewer, monkeypatch)
    start = int(time.time())
    antwoord = operator.post(f"/api/v1/recovery/items/{item['media_ref']}/send", json={"release_key": sleutel})
    einde = int(time.time())
    assert antwoord.status_code == 200 and antwoord.json()["duplicate"] is False
    ontvangst = db.fetchone("SELECT * FROM recovery_sends WHERE id=%s", (antwoord.json()["send"]["id"],))
    opdracht = ontvangst["command"]
    ref = ontvangst["client_ref"]
    slot, = sab.queue
    assert (ref, opdracht["category"]) == (slot["nzo_id"], slot["cat"])
    doel = db.fetchone("SELECT owner, proof FROM download_receipt_targets "
                       "WHERE client='sabnzbd' AND instance=%s AND download_id=%s AND NOT disputed",
                       (opdracht["instance"], ref))
    assert doel and doel["owner"] == ontvangst["idempotency_key"]
    bewijs = doel["proof"]
    assert bewijs["filename"] == f"{slot['filename']}.nzb" == f"{fallback.GOOD}.nzb"
    assert bewijs["upload_name"] == sab.nzb_names[ref]
    assert bewijs["sha256"] == hashlib.sha256(fallback.NZB).hexdigest()
    assert bewijs["payload_size"] == len(fallback.NZB)
    assert start <= bewijs["sent_at"] <= bewijs["added"] == slot["time_added"] <= einde
    assert sab_verzoeken.count(("POST", "addfile")) == 1

    sab_verzoeken.clear()
    assert actions.confirmed_download(ontvangst, opdracht) is True
    assert sab_verzoeken == [("GET", "queue"), ("GET", "history"), ("GET", "get_files")]


@pytest.mark.parametrize("geval", ["origineel", "herinstallatie", "andere-naam", "andere-tijd", "tijd-ontbreekt",
                                   "andere-grootte",
                                   "bewijs-ontbreekt", "oud-bewijs", "ander-antwoord-id", "historie",
                                   "historie-andere-inhoud", "historie-andere-upload", "historie-zonder-bestanden",
                                   "historie-andere-bestandsnaam"])
def test_nzb_herkomst_bij_hergebruik(admin, operator, viewer, sab, manage_mode, db, monkeypatch, geval):
    item, route, key = commands.approved_send(admin, viewer, monkeypatch)
    send_route = f"/api/v1/recovery/items/{item['media_ref']}/send"
    first = operator.post(send_route, json={"release_key": key})
    assert first.is_success
    slot, = sab.queue
    ref = first.json()["send"]["client_ref"]
    assert ref == slot["nzo_id"]
    if geval == "herinstallatie":
        other = sim.create_app(api_key=fallback.SAB_KEY, scenario="empty", categories="mso-tv")
        monkeypatch.setattr(base, "http_client", lambda url, **kw: TestClient(other, base_url=url))
        with SabClient.from_config() as client:
            reply = client._call("addfile", {"cat": "mso-tv"}, method="POST",
                                 files={"name": (fallback.GOOD + ".nzb", fallback.NZB, "application/x-nzb")})
        twin, = other.state.sab.queue
        assert reply["nzo_ids"] == [ref] and twin["filename"] == slot["filename"]
        twin["time_added"] = slot["time_added"]
        assert other.state.sab.job_files[ref] != sab.job_files[ref]
    elif geval == "andere-naam":
        slot["filename"] += ".ander"
    elif geval == "andere-tijd":
        slot["time_added"] += 1
    elif geval == "tijd-ontbreekt":
        del slot["time_added"]
    elif geval == "andere-grootte":
        sab.job_files[ref][0]["bytes"] = "1.00"
    elif geval == "bewijs-ontbreekt":
        db.execute("UPDATE download_receipt_targets SET proof=NULL")
    elif geval == "oud-bewijs":
        db.execute("UPDATE download_receipt_targets SET proof=proof - 'manifest' - 'nzo_id'")
    elif geval == "ander-antwoord-id":
        db.execute("""UPDATE download_receipt_targets SET proof=jsonb_set(proof,'{nzo_id}','"ander"')""")
    elif geval.startswith("historie"):
        sab.queue.clear()
        article = b"ander" if geval == "historie-andere-inhoud" else b"seg1@mock"
        sab.history.append({"nzo_id": ref, "category": "mso-tv", "completed": int(time.time()),
                            "name": slot["filename"], "nzb_name": sab.nzb_names[ref],
                            "time_added": slot["time_added"],
                            "md5sum": hashlib.md5(article, usedforsecurity=False).hexdigest()})
        if geval == "historie-andere-upload":
            sab.history[-1]["nzb_name"] = slot["filename"] + ".nzb"
        elif geval == "historie-zonder-bestanden":
            del sab.job_files[ref]
        elif geval == "historie-andere-bestandsnaam":
            sab.job_files[ref][0]["filename"] = "ander.mkv"
    again = operator.post(send_route, json={"release_key": key})
    applied = operator.post(route + "/apply")
    valid = geval in {"origineel", "historie"}
    assert again.status_code == (200 if valid else 409)
    if geval in {"bewijs-ontbreekt", "herinstallatie"}:
        assert again.json()["detail"] == "De herkomst van de eerdere download is onbekend"
    assert applied.is_success == valid
    assert commands.applied(db, item["change_id"]) == int(valid)
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='recovery.sent'") == 1
    assert db.scalar("SELECT count(*) FROM sab_send_receipts") == 1


@pytest.mark.parametrize("veld", [
    "origineel", "magnet", "name", "added_on", "tags", "bewijs-ontbreekt", "herinstallatie",
])
def test_torrent_herkomst_bij_hergebruik(admin, operator, viewer, qbit, manage_mode, db, monkeypatch, veld):
    candidate = release(fallback.GOOD, protocol="torrent", key="torrent", cat="tv", seeders=40)
    if veld == "magnet":
        candidate.extra["final_url"] = f"magnet:?xt=urn:btih:{candidate.infohash_v1}&dn={candidate.title}"
    fallback._search(monkeypatch, [candidate])
    fallback._failed(fallback.FAILED, key="btih:" + "a" * 40, protocol="torrent", ref="a" * 40)
    assert fallback._run()["searched"] == 1
    item, = fallback._fallbacks(viewer)
    route = f"/api/v1/changes/{item['change_id']}"
    assert admin.post(route + "/approve").is_success
    send_route = f"/api/v1/recovery/items/{item['media_ref']}/send"
    first = operator.post(send_route, json={"release_key": candidate.release_key})
    assert first.is_success
    ref = first.json()["send"]["client_ref"]
    source = qbit.torrents[ref]
    if veld == "herinstallatie":
        other = qsim.create_app(version="5.1.2", scenario="empty")
        state = other.state.qb
        state.categories["mso-tv"] = qbit.categories["mso-tv"]
        state.torrents[ref] = dict(source, tags="mso-herstel")
        monkeypatch.setattr(base, "http_client", lambda url, **kw: TestClient(other, base_url=url))
    elif veld == "bewijs-ontbreekt":
        db.execute("UPDATE download_receipt_targets SET proof=NULL")
    elif veld not in {"origineel", "magnet"}:
        qbit.torrents[ref] = dict(source, **{veld: source["added_on"] + 1 if veld == "added_on" else "ander"})
    again = operator.post(send_route, json={"release_key": candidate.release_key})
    answer = operator.post(route + "/apply")
    expected = veld in {"origineel", "magnet"}
    assert again.status_code == (200 if expected else 409)
    if veld in {"bewijs-ontbreekt", "herinstallatie"}:
        assert again.json()["detail"] == "De herkomst van de eerdere download is onbekend"
    assert answer.is_success == expected and commands.applied(db, item["change_id"]) == int(expected)
    assert sum(path.endswith("/torrents/add") for _, path, _ in qbit.calls) == 1
    if veld == "herinstallatie":
        assert sum(path.endswith("/torrents/add") for _, path, _ in state.calls) == 0
