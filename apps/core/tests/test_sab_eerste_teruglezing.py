"""Herkomst van een NZB bij de eerste teruglezing op het adres van de upload."""

import time
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
import test_download_commands as commands
import test_recovery_fallback as fallback
from fastapi.testclient import TestClient
from msomocks import sabnzbd as simulator

from mso import download_receipts
from mso.change_preview import digest
from mso.connectors import sabnzbd
from mso.features.usenet import nzbs


@pytest.mark.parametrize("geval", ["origineel", "andere-bestandslijst", "andere-naam"])
def test_eerste_bestandslijst_hoort_bij_de_verzonden_nzb(
    admin, operator, viewer, db, manage_mode, serve, monkeypatch, geval,
):
    seconde = int(time.time())
    klok = SimpleNamespace(time=lambda: seconde, monotonic=time.monotonic)
    monkeypatch.setattr(sabnzbd, "time", klok)
    monkeypatch.setattr(simulator, "time", klok)
    monkeypatch.setattr(simulator, "VERSION", "5.0.1")  # Deze wachtrij levert time_added.
    origineel = simulator.create_app(api_key=fallback.SAB_KEY, scenario="empty", categories="mso-tv")
    vervanger = simulator.create_app(api_key=fallback.SAB_KEY, scenario="empty", categories="mso-tv")
    delen = [("ander.bin", 700000)] if geval == "andere-naam" else [("een.bin", 300000), ("twee.bin", 400000)]
    bestanden = "".join(
        f'<file subject="{naam}"><segments><segment bytes="{grootte}" number="1">{nr}@test.invalid</segment>'
        '</segments></file>' for nr, (naam, grootte) in enumerate(delen)
    )
    bestand = ("Handmatig.nzb", f"<nzb>{bestanden}</nzb>".encode(), "application/x-nzb")
    with TestClient(vervanger) as client:
        antwoord = client.post("/api", params={"mode": "addfile", "cat": "mso-tv", "nzbname": fallback.GOOD,
                                               "apikey": fallback.SAB_KEY},
                               files={"name": bestand})
        assert antwoord.is_success
        ref, = antwoord.json()["nzo_ids"]

    uploads = []

    async def testadres(scope, receive, send):
        dienst = vervanger if geval != "origineel" and uploads else origineel
        if scope["type"] == "http" and scope["method"] == "POST":
            if parse_qs(scope["query_string"]).get(b"mode") == [b"addfile"]:
                uploads.append(dienst)
        await dienst(scope, receive, send)

    assert admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(testadres), "enabled": True,
                                                         "secrets": {"api_key": fallback.SAB_KEY}}).is_success
    item, route, sleutel = commands.approved_send(admin, viewer, monkeypatch)
    pad = f"/api/v1/recovery/items/{item['media_ref']}/send"
    eerste = operator.post(pad, json={"release_key": sleutel})
    opnieuw = operator.post(pad, json={"release_key": sleutel})
    toepassen = operator.post(route + "/apply")

    assert uploads == [origineel]
    for dienst in (origineel, vervanger):
        slot, = dienst.state.sab.queue
        assert (slot["nzo_id"], slot["filename"], slot["cat"], slot["time_added"]) == (
            ref, fallback.GOOD, "mso-tv", seconde,
        )
    verwacht = [("Lost.Signal.S02E05.mkv", 700000)]
    for dienst, inhoud in ((origineel, verwacht), (vervanger, delen)):
        lijst = dienst.state.sab.job_files[ref]
        assert [(f["filename"], int(f["bytes"])) for f in lijst] == inhoud
        assert sum(int(f["bytes"]) for f in lijst) == 700000
    rij = db.fetchone("SELECT proof FROM download_receipt_targets WHERE client='sabnzbd' AND download_id=%s", (ref,))
    assert db.scalar("SELECT count(*) FROM sab_send_receipts") == 1
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='recovery.sent'") == int(geval == "origineel")
    status = db.scalar("SELECT status FROM change_sets WHERE id=%s", (item["change_id"],))
    audits = commands.applied(db, item["change_id"])
    if geval == "origineel":
        assert eerste.is_success and opnieuw.json()["duplicate"] is True and toepassen.is_success
        assert rij["proof"]["manifest"] == digest(verwacht) and rij["proof"]["nzo_id"] == ref
        assert status == "applied" and audits == 1
    else:
        assert not eerste.is_success and not opnieuw.is_success and not toepassen.is_success
        assert rij["proof"] is None and status != "applied" and audits == 0


@pytest.mark.parametrize("geval", ["omgekeerde-volgorde", "verwisselde-groottes", "ontbrekende-naam",
                                   "dubbel-bestands-id", "afgeronde-grootte", "ontbrekend-bestand", "andere-binding",
                                   "antwoord-geweigerd", "antwoord-ander-id", "bestand-ander-id"])
def test_bestandsnamen_aantallen_en_exacte_groottes(monkeypatch, geval):
    nzb = (b'<nzb><file subject="een.bin"><segments>'
           b'<segment number="1" bytes="2">een@test.invalid</segment>'
           b'<segment number="2" bytes="3">twee@test.invalid</segment></segments></file>'
           b'<file subject="[2/2] &quot;twee.bin&quot; yEnc"><segments>'
           b'<segment number="1" bytes="7">drie@test.invalid</segment></segments></file></nzb>')
    manifest = nzbs.parse_nzb(nzb).file_manifest
    assert manifest == digest([["een.bin", 5], ["twee.bin", 7]])
    proof = {"nzo_id": "taak", "filename": "proef.nzb", "category": "proef", "added": 10,
             "bytes": 12, "manifest": manifest, "files": digest({"een": 5, "twee": 7}),
             "file_bindings": digest({"een": ["een.bin", 5], "twee": ["twee.bin", 7]})}
    slot = {"nzo_id": "taak", "filename": "proef", "cat": "proef", "time_added": 10}
    files = [{"filename": "twee.bin", "bytes": "7.00", "nzf_id": "twee"},
             {"filename": "een.bin", "bytes": "5.00", "nzf_id": "een"}]
    if geval == "verwisselde-groottes":
        files[0]["filename"], files[1]["filename"] = files[1]["filename"], files[0]["filename"]
    elif geval == "ontbrekende-naam":
        del files[0]["filename"]
    elif geval == "dubbel-bestands-id":
        files[0]["nzf_id"] = files[1]["nzf_id"]
    elif geval == "afgeronde-grootte":
        files[0]["bytes"] = "7.01"
    elif geval == "ontbrekend-bestand":
        files.pop()
    elif geval == "andere-binding":
        proof["file_bindings"] = digest({"een": ["twee.bin", 5], "twee": ["een.bin", 7]})
    elif geval == "bestand-ander-id":
        files[0]["nzo_id"] = "ander"
    monkeypatch.setattr(download_receipts, "target", lambda *_: {"proof": proof, "owner": "opdracht"})

    def get_files(request):
        assert request.method == "GET" and request.url.params["mode"] == "get_files"
        assert request.url.params["value"] == "taak"
        antwoord = {"files": files}
        if geval == "antwoord-geweigerd":
            antwoord["status"] = False
        elif geval == "antwoord-ander-id":
            antwoord["nzo_id"] = "ander"
        return httpx.Response(200, json=antwoord)

    monkeypatch.setattr(sabnzbd.cb, "http_client", lambda url, **kw: httpx.Client(
        base_url=url, transport=httpx.MockTransport(get_files)))
    with sabnzbd.SabClient("http://ontvanger.test", fallback.SAB_KEY) as client:
        monkeypatch.setattr(client, "_download_slot", lambda *_: (slot, False))
        assert client.has_download("taak", "proef", owner="opdracht") == (geval == "omgekeerde-volgorde")


@pytest.mark.parametrize(("subject", "extra"), [
    ("", ""), ("&quot;een&quot; of &quot;twee&quot;", ""), ("naam", '<segment number="2">twee@test.invalid</segment>'),
    ("naam", '<segment number="1" bytes="3">twee@test.invalid</segment>'),
])
def test_onvolledige_verzonden_bestandslijst_blijft_onbekend(subject, extra):
    nzb = (f'<nzb><file subject="{subject}"><segments><segment number="1" bytes="2">een@test.invalid</segment>'
           f'{extra}</segments></file></nzb>')
    assert nzbs.parse_nzb(nzb.encode()).file_manifest is None
