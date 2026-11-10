"""scripts/bronkopie.py: pin- en driftregel van de versie-gepinde kopieën (contract 1.3 kop/§10; Peil ONTWERPTAAL §14,
E08). Positief tegen deze repo zelf; negatief met een nagebootste bron (een tijdelijke git-repo) zodat bewezen is dat de
controle echt faalt. Geen database nodig."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
NAAST = ROOT.parent / "ai-server-manager"


def _laad() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bronkopie", ROOT / "scripts" / "bronkopie.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bronkopie"] = mod
    spec.loader.exec_module(mod)
    return mod


bk = _laad()


def test_deze_repo_is_in_orde() -> None:
    uit = bk.controleer(ROOT)
    assert uit.fouten == []
    soorten = {regel.split("  ")[1].split()[0] for regel in uit.ok}
    assert soorten == {"contract", "peil"}
    assert len(uit.ok) == 11  # 2 contractbestanden + 9 Peil-kopieën


def test_peil_pin_is_1_1_1_met_volledige_commit() -> None:
    pin = bk.lees_pin(ROOT, "design/ontwerptaal/HERKOMST.json")
    assert pin.versie == "1.1.1" and len(pin.bron_commit) == 40
    kopieen = {b.kopie for b in pin.bestanden}
    assert {"apps/web/styles/peil/ontwerptaal.css", "apps/web/styles/peil/mediaguard-mso.css"} <= kopieen


@pytest.mark.skipif(not (NAAST / ".git").exists(), reason="bron ai-server-manager niet naast deze repo uitgecheckt")
def test_naastliggende_bron_heeft_de_gepinde_inhoud() -> None:
    """In de bouwomgeving: de gepinde commit heeft exact de gepinde inhoud. Een nieuwere bronversie mag (waarschuwing);
    dezelfde Peil-versie met andere inhoud is drift en faalt (E08)."""
    try:
        uit = bk.vergelijk(bk.git_lezer(NAAST), ROOT)
    except bk.BronOnbereikbaar as exc:  # bv. een ondiepe checkout zonder de gepinde commit
        pytest.skip(f"bron niet leesbaar: {exc}")
    assert uit.fouten == []
    for regel in uit.waarschuwingen:
        print("WAARSCHUWING", regel)


# --------------------------------------------------------------------------------------- nagebootste bron en repo
CSS_V1 = "/* ===\n   Peil 1.1.1 — ontwerptaal test\n   === */\n:root { --x: #000; }\n"
CSS_V1_ANDERS = "/* ===\n   Peil 1.1.1 — ontwerptaal test\n   === */\n:root { --x: #111; }\n"
CSS_V2 = "/* ===\n   Peil 1.1.2 — ontwerptaal test\n   === */\n:root { --x: #222; }\n"
CONTRACT_V1 = "# Contract\n\nVersie: `contracts 1.3` · test\n"
CONTRACT_V1_ANDERS = "# Contract\n\nVersie: `contracts 1.3` · test (gewijzigd)\n"
CONTRACT_V2 = "# Contract\n\nVersie: `contracts 1.4` · test\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(bron: Path, css: str, contract: str) -> str:
    (bron / "design").mkdir(parents=True, exist_ok=True)
    (bron / "design" / "ontwerptaal.css").write_text(css, encoding="utf-8")
    (bron / "design" / "tokens.json").write_text('{"v": 1}\n', encoding="utf-8")
    (bron / "docs").mkdir(parents=True, exist_ok=True)
    (bron / "docs" / "CONTRACT.md").write_text(contract, encoding="utf-8")
    _git(bron, "add", "-A")
    _git(bron, "commit", "-q", "-m", "bron")
    return _git(bron, "rev-parse", "HEAD")


@pytest.fixture()
def opzet(tmp_path: Path) -> tuple[Path, Path, str, tuple[str, str]]:
    """(repo, bron, gepinde commit, pinnen): een bron met één commit en een repo met kopieën op die commit."""
    bron = tmp_path / "bron"
    bron.mkdir()
    _git(bron, "init", "-q", "-b", "werk")
    commit = _commit(bron, CSS_V1, CONTRACT_V1)
    repo = tmp_path / "repo"
    (repo / "pin").mkdir(parents=True)
    peil = {
        "soort": "peil", "versie": "0.0.0", "bron_repo": "test/bron", "bron_ref": "werk", "bron_commit": "0" * 40,
        "versiebron": "design/ontwerptaal.css", "mappen": ["kopie/peil"],
        "bestanden": [
            {"kopie": "kopie/peil/ontwerptaal.css", "bron": "design/ontwerptaal.css", "kop": "css", "sha256": "0" * 64,
             "toelichting": "Versie-gepinde kopie (test)."},
            {"kopie": "kopie/peil/tokens.json", "bron": "design/tokens.json", "kop": None, "sha256": "0" * 64},
        ],
    }
    contract = {
        "soort": "contract", "versie": "0.0", "bron_repo": "test/bron", "bron_ref": "werk", "bron_commit": "0" * 40,
        "versiebron": "docs/CONTRACT.md", "mappen": [],
        "bestanden": [{"kopie": "kopie/CONTRACT.md", "bron": "docs/CONTRACT.md", "kop": None, "sha256": "0" * 64}],
    }
    (repo / "pin" / "peil.json").write_text(json.dumps(peil), encoding="utf-8")
    (repo / "pin" / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    pinnen = ("pin/contract.json", "pin/peil.json")
    lezer = bk.git_lezer(bron)
    for soort in ("peil", "contract"):
        _bijwerken(repo, pinnen, soort, lezer, commit)
    return repo, bron, commit, pinnen


def _bijwerken(repo: Path, pinnen: tuple[str, str], soort: str, lezer: object, commit: str) -> None:
    oud = bk.PINNEN
    bk.PINNEN = pinnen
    try:
        bk.bijwerken(repo, soort, lezer, commit)
    finally:
        bk.PINNEN = oud


def test_bijgewerkte_kopieen_zijn_in_orde(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, bron, _, pinnen = opzet
    assert bk.controleer(repo, pinnen).fouten == []
    uit = bk.vergelijk(bk.git_lezer(bron), repo, pinnen)
    assert uit.fouten == [] and uit.waarschuwingen == []
    kop = (repo / "kopie/peil/ontwerptaal.css").read_text(encoding="utf-8").splitlines()[:2]
    assert kop[0].startswith("/* HERKOMST — Peil 1.1.1 · bron: test/bron@") and kop[1].endswith(" */")
    assert (repo / "kopie/CONTRACT.md").read_text(encoding="utf-8") == CONTRACT_V1  # byte-gelijk, geen kop


def test_met_de_hand_gewijzigde_kopie_faalt(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, _, _, pinnen = opzet
    pad = repo / "kopie/peil/ontwerptaal.css"
    pad.write_text(pad.read_text(encoding="utf-8").replace("#000", "#001"), encoding="utf-8")
    (repo / "kopie/CONTRACT.md").write_text(CONTRACT_V1 + "\nlokaal toegevoegd\n", encoding="utf-8")
    fouten = "\n".join(bk.controleer(repo, pinnen).fouten)
    assert "kopie/peil/ontwerptaal.css: inhoud wijkt af" in fouten
    assert "kopie/CONTRACT.md: inhoud wijkt af" in fouten


def test_kop_met_andere_versie_of_commit_faalt(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, _, commit, pinnen = opzet
    pad = repo / "kopie/peil/ontwerptaal.css"
    tekst = pad.read_text(encoding="utf-8")
    pad.write_text(tekst.replace("Peil 1.1.1 · bron", "Peil 1.1.0 · bron", 1).replace(commit, "f" * 40, 1), "utf-8")
    fouten = "\n".join(bk.controleer(repo, pinnen).fouten)
    assert "noemt versie '1.1.0'" in fouten and "noemt commit 'ffff" in fouten


def test_kopie_zonder_kop_faalt(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, _, _, pinnen = opzet
    (repo / "kopie/peil/ontwerptaal.css").write_text(CSS_V1, encoding="utf-8")
    assert any("herkomstkop" in f for f in bk.controleer(repo, pinnen).fouten)


def test_ongepind_bestand_in_een_gepinde_map_faalt(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, _, _, pinnen = opzet
    (repo / "kopie/peil/extra.css").write_text(":root{}", encoding="utf-8")
    (repo / "kopie/peil/HERKOMST.md").write_text("uitleg", encoding="utf-8")  # HERKOMST.* mag
    assert bk.controleer(repo, pinnen).fouten == ["kopie/peil/extra.css: staat in een gepinde map maar niet in "
                                                  "pin/peil.json"]


def test_ontbrekende_kopie_en_kapotte_pin_falen(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, _, _, pinnen = opzet
    (repo / "kopie/peil/tokens.json").unlink()
    (repo / "pin/contract.json").write_text("{", encoding="utf-8")
    fouten = "\n".join(bk.controleer(repo, pinnen).fouten)
    assert "kopie/peil/tokens.json: ontbreekt" in fouten and "pinbestand onleesbaar" in fouten


Opzet = tuple[Path, Path, str, tuple[str, str]]


def test_zelfde_peilversie_met_andere_inhoud_in_de_bron_is_drift(opzet: Opzet) -> None:
    """E08: dezelfde versie met andere inhoud op de bronbranch = fout; bij het contract alleen een waarschuwing
    (een nieuwe contractkopie overnemen is een stap van de orkestratie)."""
    repo, bron, _, pinnen = opzet
    _commit(bron, CSS_V1_ANDERS, CONTRACT_V1_ANDERS)
    uit = bk.vergelijk(bk.git_lezer(bron), repo, pinnen)
    assert any("ontwerptaal.css" in f and "drift" in f for f in uit.fouten), uit
    assert any("CONTRACT.md" in w and "orkestratie" in w for w in uit.waarschuwingen), uit
    assert not any("CONTRACT.md" in f for f in uit.fouten)


def test_nieuwere_bronversie_is_alleen_een_waarschuwing(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, bron, _, pinnen = opzet
    _commit(bron, CSS_V2, CONTRACT_V2)
    uit = bk.vergelijk(bk.git_lezer(bron), repo, pinnen)
    assert uit.fouten == []
    assert len(uit.waarschuwingen) == 2 and all("bewuste stap" in w for w in uit.waarschuwingen)
    assert bk.controleer(repo, pinnen).fouten == []  # de kopie zelf blijft in orde


def test_gepinde_commit_met_andere_inhoud_faalt(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    """Een pin die naar een commit wijst waar de bron iets anders is (verkeerd overgenomen of verkeerde SHA)."""
    repo, bron, _, pinnen = opzet
    tweede = _commit(bron, CSS_V1_ANDERS, CONTRACT_V1)
    for rel in pinnen:
        data = json.loads((repo / rel).read_text(encoding="utf-8"))
        data["bron_commit"] = tweede
        (repo / rel).write_text(json.dumps(data), encoding="utf-8")
    uit = bk.vergelijk(bk.git_lezer(bron), repo, pinnen)
    assert any("ontwerptaal.css" in f and "gepinde bron-commit" in f for f in uit.fouten), uit
    assert not any("CONTRACT.md" in f for f in uit.fouten)


def test_onbekende_commit_is_onbereikbaar_nooit_in_orde(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, bron, _, pinnen = opzet
    data = json.loads((repo / "pin/peil.json").read_text(encoding="utf-8"))
    data["bron_commit"] = "e" * 40
    (repo / "pin/peil.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(bk.BronOnbereikbaar):
        bk.vergelijk(bk.git_lezer(bron), repo, pinnen)


def test_bijwerken_weigert_een_korte_commit(opzet: tuple[Path, Path, str, tuple[str, str]]) -> None:
    repo, bron, commit, pinnen = opzet
    with pytest.raises(bk.Fout):
        _bijwerken(repo, pinnen, "peil", bk.git_lezer(bron), commit[:12])


def test_cli_controleer_geeft_uitgang_0_voor_deze_repo() -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "bronkopie.py"), "controleer"],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "peil 1.1.1" in proc.stdout and "contract 1.3" in proc.stdout


def test_cli_vergelijk_zonder_bron_is_verkeerd_gebruik() -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "bronkopie.py"), "vergelijk"],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 2
