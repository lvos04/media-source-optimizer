"""Herkomst bij de eerste en latere lezing van hetzelfde SABnzbd-taak-ID."""

import json
from itertools import product
from xml.etree import ElementTree

import httpx
import pytest
from test_sabnzbd_connector import SAB_KEY

from mso import download_receipts
from mso.change_preview import digest
from mso.connectors import sabnzbd
from mso.features.usenet.nzbs import parse_nzb

TAAK = "taak-proef"
BINDING = {"een": ("deel.bin", 3), "twee": ("deel.bin", 3), "drie": ("slot.bin", 7)}
BESTANDEN = [{"nzf_id": ident, "filename": naam, "bytes": str(grootte)} for ident, (naam, grootte) in BINDING.items()]
EXTRA = {"nzf_id": "vier", "filename": "extra.bin", "bytes": "3"}


@pytest.fixture
def meet(monkeypatch):
    nzb = ElementTree.Element("nzb")
    for ident, (naam, grootte) in BINDING.items():
        bestand = ElementTree.SubElement(nzb, "file", subject=naam)
        segmenten = ElementTree.SubElement(bestand, "segments")
        ElementTree.SubElement(segmenten, "segment", number="1", bytes=str(grootte)).text = f"{ident}@test.invalid"
    verzonden = parse_nzb(ElementTree.tostring(nzb))
    assert verzonden.file_manifest == digest(sorted(BINDING.values()))
    bewijs = {"nzo_id": TAAK, "filename": "proef.nzb", "category": "proef", "added": 20,
              "bytes": verzonden.bytes_total, "manifest": verzonden.file_manifest}
    opgeslagen = dict(bewijs, files=digest({k: v[1] for k, v in BINDING.items()}), file_bindings=digest(BINDING))
    slot = {"nzo_id": TAAK, "filename": "proef", "cat": "proef", "time_added": 20}
    monkeypatch.setattr(download_receipts, "target", lambda *_: {"owner": "eigenaar", "proof": opgeslagen})

    def meet_antwoord(antwoord, status=200, koppen=None):
        def ontvang(verzoek):
            assert verzoek.method == "GET" and verzoek.url.params["mode"] == "get_files"
            assert verzoek.url.params["value"] == TAAK
            inhoud = antwoord if isinstance(antwoord, bytes) else json.dumps(antwoord).encode()
            headers = {"Content-Type": "application/json", **(koppen or {})}
            return httpx.Response(status, content=inhoud, headers=headers)

        monkeypatch.setattr(sabnzbd.cb, "http_client", lambda url, **_: httpx.Client(
            base_url=url, transport=httpx.MockTransport(ontvang)))
        with sabnzbd.SabClient("https://leesproef.invalid", SAB_KEY) as client:
            monkeypatch.setattr(client, "_download_slot", lambda *_: (slot, False))
            eerste = client._item_files(slot, False, bewijs) is not None
            hergebruik = client.has_download(TAAK, "proef", owner="eigenaar")
        return eerste, hergebruik

    return meet_antwoord


def test_alleen_de_exacte_multiset_bevestigt_de_herkomst(meet):
    # Alle geordende lijsten tot één bestand meer dan verzonden, met en zonder herhaling.
    kandidaten = [*BESTANDEN, EXTRA]
    for lengte in range(len(BESTANDEN) + 2):
        for lijst in product(kandidaten, repeat=lengte):
            ids = sorted(rij["nzf_id"] for rij in lijst)
            origineel = ids == sorted(BINDING)
            assert meet({"files": lijst, "total": lengte}) == (origineel, origineel), ids


@pytest.mark.parametrize(("metadata", "status", "koppen"), [
    ({"error": "Bestandslijst onvolledig"}, 200, {}),
    ({}, 206, {"Content-Range": "items 0-2/4"}),
    ({}, 206, {}),
    ({}, 200, {"Content-Range": "items 0-2/4"}),
    ({}, 200, {"X-Total-Count": "4"}),
    ({}, 200, {"Link": '</volgende>; rel="next"'}),
    ({}, 200, {"Link": '</laatste>; rel="alternate last"'}),
    ({"total": 4}, 200, {}),
    ({"total": 3, "nooffiles": 4}, 200, {}),
    ({"total": True}, 200, {}),
    ({"total": 3.0}, 200, {}),
    ({"error": ""}, 200, {}),
    ({"error": None, "status": True}, 200, {}),
    ({"partial": True}, 200, {}),
    ({"status": 1}, 200, {}),
    ({"nzo_ids": TAAK}, 200, {}),
])
def test_deelantwoord_of_foutveld_houdt_de_herkomst_onbekend(meet, metadata, status, koppen):
    # Drie zichtbare bestanden komen precies overeen met de NZB, ook als de bron een vierde heeft.
    bron = [*BESTANDEN, EXTRA]
    assert meet({"files": bron[:-1], **metadata}, status, koppen) == (False, False)


@pytest.mark.parametrize(("totaal", "koppen"), [
    ({}, {}),
    ({"total": 3}, {}),
    ({"nooffiles": "3"}, {}),
    ({"total_files": 3, "file_count": 3}, {"X-Total-Count": "3"}),
])
def test_volledige_bestandslijst_met_en_zonder_opgegeven_totaal(meet, totaal, koppen):
    bestanden = [dict(rij, mb="0.00", mbleft="0.00", age="1h", status="active") for rij in reversed(BESTANDEN)]
    assert meet({"files": bestanden, "nzo_ids": [TAAK], **totaal}, koppen=koppen) == (True, True)


def test_onleesbare_bestandsstructuur_houdt_de_herkomst_onbekend(meet):
    for antwoord in (None, [], {}, {"files": None}, {"files": {}}, {"files": "lijst"}, {"files": []}):
        assert meet(antwoord) == (False, False), antwoord
    for index, origineel in enumerate(BESTANDEN):
        ongeldige_rijen = [None, [], {}, dict(origineel, error="onvolledig"), dict(origineel, status=False),
                           dict(origineel, nzf_id=" " + origineel["nzf_id"])]
        for veld in origineel:
            ongeldige_rijen.append({k: v for k, v in origineel.items() if k != veld})
            for waarde in (None, False, [], {}, 3.5):
                ongeldige_rijen.append(dict(origineel, **{veld: waarde}))
        for rij in ongeldige_rijen:
            bestanden = [*BESTANDEN]
            bestanden[index] = rij
            assert meet({"files": bestanden}) == (False, False), (index, rij)
    data = json.dumps(BESTANDEN).encode()
    assert meet(b'{"files":[],"files":' + data + b'}') == (False, False)
