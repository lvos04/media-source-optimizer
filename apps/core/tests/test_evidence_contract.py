"""Contract 1.3 §11 (C1.3-ALG-02, C1.3-MSO-01): ``docs/platform-v2/EVIDENCE.json`` volgt de vaste Excel-statussen en
validatiewaarden. Geen database nodig.

- ``status`` uit de Excel-lijst van het blad van de ID; ``Gereed`` en ``Geslaagd`` worden in deze bouwomgeving nooit
  gezet.
- ``validatie``: één of meer waarden uit de vaste lijst, gescheiden door ``"; "``, zonder dubbele; nooit leeg.
  ``Sim-geslaagd``/``Sim-mislukt`` alleen bij Testmatrix-ID's (``EX..``, ``JLT-..``), ``Sim-getest``/``Unit-getest``
  alleen daarbuiten; ``Niet getest`` alleen bij status ``Niet gestart`` en nooit naast een getest-waarde.
- Elke ``NIET GEVALIDEERD (…)`` in ``blokkade`` staat ook in ``validatie``.
- ``id``: een Excel-ID van deze repo (``EXCEL_TAKEN.json``) of een ID van een ander Excel-blad (``EX..``, ``D..``,
  ``I..-..-..``, ``APP-..``, ``JLT-..``); een contractpunt ``C1.3-<code>`` dat in ``CONTRACT_1.3_CONFORMITEIT.md``
  staat (of ``C1.2-<code>``); of een review ``REVIEW-media-source-optimizer-<onderwerp>``.

``fouten()`` is los te testen; de negatieve gevallen onderaan bewijzen dat de controle echt faalt.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO = "media-source-optimizer"
PV2 = Path(__file__).resolve().parents[3] / "docs" / "platform-v2"
VELDEN = {"id", "status", "validatie", "bewijs", "blokkade", "opmerking", "repo", "bijgewerkt"}
STATUS_WERK = {"Niet gestart", "Bezig", "Gereed", "Geblokkeerd", "N.v.t."}
STATUS_TEST = {"Niet gestart", "Bezig", "Geslaagd", "Mislukt", "Geblokkeerd", "N.v.t."}
STATUS_BESLUIT = {"Open", "Ingevuld", "Getest", "Geblokkeerd", "N.v.t."}
NOOIT = {"Gereed", "Geslaagd"}
NIET_GEVALIDEERD = {
    "NIET GEVALIDEERD (echte NAS)",
    "NIET GEVALIDEERD (hardware)",
    "NIET GEVALIDEERD (account)",
    "NIET GEVALIDEERD (Mac/Xcode)",
}
GETEST = {"Sim-getest", "Unit-getest", "Intel-iGPU-getest (ontwikkelmachine)"}
TESTMATRIX_WAARDEN = {"Sim-geslaagd", "Sim-mislukt"}
VALIDATIE = GETEST | TESTMATRIX_WAARDEN | NIET_GEVALIDEERD | {"Niet getest"}
TESTMATRIX_ID = re.compile(r"EX\d{2}|JLT-\d+")
BESLUIT_ID = re.compile(r"D\d{2}")
ANDER_EXCEL_ID = re.compile(r"I\d{2}-\d{2}-\d{2}|APP-\d{2}")
CONTRACTPUNT = re.compile(r"C1\.3-[A-Z]+-\d{2}|C1\.2-[A-Z]+\d+")
REVIEW = re.compile(rf"REVIEW-{REPO}-[a-z0-9][a-z0-9.-]*")
NIET_GEVALIDEERD_IN_TEKST = re.compile(r"NIET GEVALIDEERD \([^)]*\)")


def _excel_ids() -> set[str]:
    return {t["ID"] for t in json.loads((PV2 / "EXCEL_TAKEN.json").read_text(encoding="utf-8"))}


def _contractpunten() -> set[str]:
    tekst = (PV2 / "CONTRACT_1.3_CONFORMITEIT.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| (C1\.3-[A-Z]+-\d{2}) \|", tekst, flags=re.M))


def fouten(items: Any, excel_ids: set[str], contractpunten: set[str], vandaag: dt.date) -> list[str]:
    """Alle overtredingen van §11 (lege lijst = in orde)."""
    if not isinstance(items, list) or not items:
        return ["EVIDENCE.json moet een niet-lege lijst zijn"]
    uit: list[str] = []
    gezien: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != VELDEN:
            uit.append(f"{item!r:.80}: velden moeten precies {sorted(VELDEN)} zijn")
            continue
        rid = item["id"]
        if rid in gezien:
            uit.append(f"{rid}: dubbele ID")
        gezien.add(rid)
        if item["repo"] != REPO:
            uit.append(f"{rid}: repo moet {REPO!r} zijn")
        try:
            datum = dt.date.fromisoformat(item["bijgewerkt"])
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["bijgewerkt"]) or datum > vandaag + dt.timedelta(days=1):
                raise ValueError
        except (TypeError, ValueError):
            uit.append(f"{rid}: bijgewerkt moet een datum JJJJ-MM-DD zijn, niet in de toekomst")
        # ID en het blad waar de status vandaan komt
        is_test = bool(TESTMATRIX_ID.fullmatch(rid))
        if is_test:
            statussen = STATUS_TEST
        elif BESLUIT_ID.fullmatch(rid):
            statussen = STATUS_BESLUIT
        elif rid in excel_ids or ANDER_EXCEL_ID.fullmatch(rid) or REVIEW.fullmatch(rid):
            statussen = STATUS_WERK
        elif CONTRACTPUNT.fullmatch(rid):
            statussen = STATUS_WERK
            if rid.startswith("C1.3-") and rid not in contractpunten:
                uit.append(f"{rid}: staat niet in CONTRACT_1.3_CONFORMITEIT.md")
        else:
            uit.append(f"{rid}: geen Excel-ID, contractpunt (C1.3-…/C1.2-…) of REVIEW-{REPO}-…")
            statussen = STATUS_WERK
        status = item["status"]
        if status in NOOIT:
            uit.append(f"{rid}: status {status!r} wordt in deze bouwomgeving nooit gezet (§11)")
        elif status not in statussen:
            uit.append(f"{rid}: status {status!r} staat niet in de Excel-lijst {sorted(statussen)}")
        # validatie
        validatie = item["validatie"]
        if not isinstance(validatie, str) or not validatie.strip():
            uit.append(f"{rid}: een lege validatie is nooit toegestaan")
            continue
        waarden = validatie.split("; ")
        onbekend = [w for w in waarden if w not in VALIDATIE]
        if onbekend:
            uit.append(f"{rid}: validatie {onbekend} staat niet in de vaste lijst (toelichting hoort in opmerking)")
        if len(waarden) != len(set(waarden)):
            uit.append(f"{rid}: dubbele validatiewaarde")
        if is_test and GETEST & set(waarden):
            fout = sorted(GETEST & set(waarden))
            uit.append(f"{rid}: Testmatrix-ID's gebruiken Sim-geslaagd/Sim-mislukt, niet {fout}")
        if not is_test and TESTMATRIX_WAARDEN & set(waarden):
            uit.append(f"{rid}: Sim-geslaagd/Sim-mislukt alleen bij Testmatrix-ID's (EX.., JLT-..)")
        if "Niet getest" in waarden:
            if status != "Niet gestart":
                uit.append(f"{rid}: 'Niet getest' alleen bij status 'Niet gestart'")
            if (GETEST | TESTMATRIX_WAARDEN) & set(waarden):
                uit.append(f"{rid}: 'Niet getest' kan niet samen met een getest-waarde")
        for gevonden in NIET_GEVALIDEERD_IN_TEKST.findall(item["blokkade"]):
            if gevonden not in NIET_GEVALIDEERD:
                uit.append(f"{rid}: {gevonden!r} in blokkade is geen waarde uit de vaste lijst")
            elif gevonden not in waarden:
                uit.append(f"{rid}: {gevonden!r} staat in blokkade maar niet in validatie")
    return uit


def _items() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((PV2 / "EVIDENCE.json").read_text(encoding="utf-8"))
    return data


def test_evidence_volgt_paragraaf_11() -> None:
    assert fouten(_items(), _excel_ids(), _contractpunten(), dt.date.today()) == []


def test_alle_excel_taken_van_deze_repo_staan_erin() -> None:
    ids = {i["id"] for i in _items()}
    assert _excel_ids() <= ids, sorted(_excel_ids() - ids)


def test_conformiteitslijst_levert_de_eigen_contractpunten() -> None:
    eigen = {p for p in _contractpunten() if p.startswith("C1.3-MSO-")}
    assert {f"C1.3-MSO-{n:02d}" for n in range(1, 7)} <= eigen


# ------------------------------------------------------------------------------------------- negatieve gevallen
GOED = {
    "id": "B05-001",
    "status": "Bezig",
    "validatie": "Sim-getest; NIET GEVALIDEERD (echte NAS)",
    "bewijs": "x",
    "blokkade": "NIET GEVALIDEERD (echte NAS): hardware-acceptatie",
    "opmerking": "",
    "repo": REPO,
    "bijgewerkt": "2026-11-10",
}


def _met(**wijziging: Any) -> list[dict[str, Any]]:
    item = copy.deepcopy(GOED)
    item.update(wijziging)
    return [item]


def _check(items: Any) -> list[str]:
    return fouten(items, {"B05-001"}, {"C1.3-MSO-01"}, dt.date(2026, 11, 10))


def test_goed_voorbeeld_is_in_orde() -> None:
    assert _check([GOED]) == []


@pytest.mark.parametrize(
    ("wijziging", "melding"),
    [
        ({"status": "Gereed"}, "nooit gezet"),
        ({"status": "Geslaagd"}, "nooit gezet"),
        ({"status": "Klaar"}, "Excel-lijst"),
        ({"validatie": ""}, "lege validatie"),
        ({"validatie": "   "}, "lege validatie"),
        ({"validatie": "Sim-getest (stack 19/19)"}, "vaste lijst"),
        ({"validatie": "Sim-getest; Sim-getest; NIET GEVALIDEERD (echte NAS)"}, "dubbele validatiewaarde"),
        ({"validatie": "Sim-geslaagd; NIET GEVALIDEERD (echte NAS)"}, "alleen bij Testmatrix"),
        ({"validatie": "Niet getest; NIET GEVALIDEERD (echte NAS)"}, "alleen bij status 'Niet gestart'"),
        ({"status": "Niet gestart", "validatie": "Niet getest; Unit-getest; NIET GEVALIDEERD (echte NAS)"},
         "samen met een getest-waarde"),
        ({"validatie": "Sim-getest"}, "staat in blokkade maar niet in validatie"),
        ({"blokkade": "NIET GEVALIDEERD (echte TV)"}, "geen waarde uit de vaste lijst"),
        ({"repo": "media-guard-ultimate"}, "repo moet"),
        ({"bijgewerkt": "10-11-2026"}, "bijgewerkt"),
        ({"bijgewerkt": "2027-01-01"}, "bijgewerkt"),
        ({"id": "B05-999"}, "geen Excel-ID"),
        ({"id": "C1.3-MSO-99"}, "staat niet in CONTRACT_1.3_CONFORMITEIT.md"),
        ({"id": "REVIEW-my_house-peil"}, "geen Excel-ID"),
    ],
)
def test_overtreding_wordt_gemeld(wijziging: dict[str, Any], melding: str) -> None:
    gevonden = _check(_met(**wijziging))
    assert any(melding in f for f in gevonden), gevonden


def test_testmatrix_id_met_sim_getest_wordt_gemeld() -> None:
    gevonden = _check(_met(id="EX16", validatie="Sim-getest; NIET GEVALIDEERD (echte NAS)"))
    assert any("Testmatrix-ID's gebruiken" in f for f in gevonden), gevonden
    assert _check(_met(id="EX16", validatie="Sim-geslaagd; NIET GEVALIDEERD (echte NAS)")) == []


def test_ontbrekend_veld_dubbele_id_en_lege_lijst() -> None:
    zonder = copy.deepcopy(GOED)
    del zonder["opmerking"]
    assert any("velden" in f for f in _check([zonder]))
    assert any("dubbele ID" in f for f in _check([GOED, copy.deepcopy(GOED)]))
    assert _check([]) == ["EVIDENCE.json moet een niet-lege lijst zijn"]
    assert _check({"id": "B05-001"}) == ["EVIDENCE.json moet een niet-lege lijst zijn"]


def test_contractpunt_en_review_met_de_vaste_vorm_zijn_toegestaan() -> None:
    assert _check(_met(id="C1.3-MSO-01")) == []
    assert _check(_met(id=f"REVIEW-{REPO}-contract-1.3")) == []
    assert _check(_met(id="C1.3-MSO-01", status="Niet gestart", validatie="Niet getest", blokkade="")) == []
