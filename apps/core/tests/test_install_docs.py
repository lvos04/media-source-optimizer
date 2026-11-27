"""Toets de vrijgave, upgrade en terugval in de TrueNAS-documentatie."""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs"
FORMULIER = ROOT / "deploy/truenas/CUSTOM_APP.md"
MANIFEST = ROOT / "deploy/manifest/media-source-optimizer.manifest.yaml"


@pytest.mark.parametrize("name", ["TRUENAS_INSTALL.md", "HARDWARE_ACCEPTANCE.md"])
def test_installatiepoort_blokkeert_uitvoering(name: str) -> None:
    document = (DOCS / name).read_text(encoding="utf-8")
    for legacy_instruction in ("sudo zfs create", "→ Install via YAML", "mso-compose.yaml"):
        assert legacy_instruction not in document
    before_steps = document.split("\n## ", 1)[0]
    assert "P-INSTALLATIE" in before_steps and "niet uitvoeren" in before_steps.lower()
    assert "](TRUENAS_CUSTOM_APP.md)" in before_steps


def test_custom_app_formulier_en_manifest_zijn_aanwezig() -> None:
    formulier = FORMULIER.read_text(encoding="utf-8")
    assert "`/mnt/<POOL>/" in formulier
    assert "`/appdata`" in formulier
    assert MANIFEST.is_file()
    assert (ROOT / ".env.example").is_file()


@pytest.mark.parametrize("name", ["TRUENAS_CUSTOM_APP.md", "OPERATIONS.md", "MIGRATION.md"])
def test_gebruikersdocument_verwijst_naar_custom_app(name: str) -> None:
    document = (DOCS / name).read_text(encoding="utf-8")
    assert "install via yaml" not in document.lower()
    assert "docker compose up" not in document.lower()
    assert "](../deploy/truenas/CUSTOM_APP.md" in document


@pytest.mark.parametrize("name", ["TRUENAS_CUSTOM_APP.md", "MIGRATION.md"])
def test_terugval_zet_snapshot_terug_voor_de_vorige_image(name: str) -> None:
    document = (DOCS / name).read_text(encoding="utf-8")
    rollback = document.split("\n## Terugdraaien via TrueNAS\n", 1)[1].split("\n## ", 1)[0]
    assert rollback.index("**Rollback**") < rollback.index("**Edit**")
    assert "APPDATA" in rollback
    assert "docker exec" not in rollback


@pytest.mark.parametrize("name", ["OPERATIONS.md", "MIGRATION.md"])
def test_pre_upgrade_back_up_maakt_versiegrens_expliciet(name: str) -> None:
    document = (DOCS / name).read_text(encoding="utf-8")
    section = document.split("\n### Gedrag per imageversie\n", 1)[1].split("\n## ", 1)[0]
    published, source = section.split("**Huidige bronstand.**", 1)
    assert "**Gepubliceerde 1.6.0.**" in published
    assert "Bij ruimtegebrek of een dumpfout gaat de migratie door zonder nieuwe pre-upgradeback-up" in published
    assert "Bij ruimtegebrek, een dumpfout of een onbruikbaar archief stopt de migratie" in " ".join(source.split())
    assert "SHA-256" in source and "leesbaarheid" in source
    assert "vóór elke upgrade" in section and "volledige APPDATA-dataset van de gestopte app" in section


@pytest.mark.parametrize("dienst", ["MSO", "MediaGuard", "Prowlarr", "SABnzbd", "qBittorrent"])
def test_hardwareproef_vereist_geverifieerd_herstelpunt_voor_elke_dienst(dienst: str) -> None:
    document = (DOCS / "HARDWARE_ACCEPTANCE.md").read_text(encoding="utf-8")
    voorbereiding = document.split("\n## 0. Voorbereiding\n")[1].split("\n## 1.")[0]
    herstelpunten = voorbereiding.split("\n### Herstelpunten vóór de eerste schrijvende proef\n")[1]
    assert "geïsoleerde kopie" in " ".join(herstelpunten.split()) and "snapshot herstelbaar" in herstelpunten
    assert "Stop de checklist zodra voor één dienst het eigen geverifieerde herstelpunt ontbreekt" in herstelpunten
    regel = next(regel for regel in herstelpunten.splitlines() if regel.startswith(f"| {dienst} |"))
    assert "snapshot" in regel and regel.endswith("| NIET GEVERIFIEERD |")


def test_true_nas_ondergrens_voor_het_beschreven_formulier() -> None:
    manifest = MANIFEST.read_text(encoding="utf-8")
    hardware = (DOCS / "HARDWARE_ACCEPTANCE.md").read_text(encoding="utf-8")
    assert 'truenas_min_version: "24.10.2.2"' in manifest
    assert "TrueNAS SCALE 24.10.2.2" in hardware and "D01 blijft open" in hardware


def test_formulier_zet_resourcelimieten_aan_voor_cpu_en_geheugen() -> None:
    formulier = FORMULIER.read_text(encoding="utf-8")
    schakelaar = "| Resources Configuration | Enable Resource Limits | Aan. |"
    limieten = "| | CPUs / Memory | 2 CPU / 1536 MiB. |"
    assert formulier.index(schakelaar) < formulier.index(limieten)


def test_formulier_onderscheidt_huidige_installatie_releasepin_en_hersteldoelen() -> None:
    vooraf = FORMULIER.read_text(encoding="utf-8").split("\n## Formulier\n", 1)[0]
    assert "huidige installatie (D07)" in vooraf
    d07_velden = ("image-tags", "API-versies", "configuratieback-ups", "gezondheid", "actief gebruik")
    assert all(veld in vooraf for veld in d07_velden)
    assert "doeltag en registerdigest uit het releasebewijs" in vooraf
    assert "RPO (maximaal dataverlies)" in vooraf and "RTO (maximale hersteltijd)" in vooraf
    assert "open tot bevestiging (D16)" in vooraf


def test_formulier_vereist_digestcontrole_na_beide_tagroutes() -> None:
    formulier = FORMULIER.read_text(encoding="utf-8")
    tagregel = next(regel for regel in formulier.splitlines() if regel.startswith("| | Tag |"))
    versie = (ROOT / "release/VERSION").read_text(encoding="utf-8").strip()
    image = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["image"]
    assert image["tag"] == versie
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", image["digest"] or "")
    pin = f"`{versie}@{image['digest']}`"
    assert pin in tagregel and f"exacte tag `{versie}`" in tagregel
    assert pin in FORMULIER.with_name("BIJWERKEN.md").read_text(encoding="utf-8")
    assert "[digestcontrole](#na-het-opslaan)" in tagregel
    controle = formulier.split("\n## Na het opslaan\n", 1)[1].split("\n2. ", 1)[0]
    assert "elke installatie en update" in controle and "beide tagroutes" in controle
    assert "`app.image.query`" in controle and "`repo_digests`" in controle and "releasebewijs" in controle
    assert "Stop bij afwijking" in controle and "teruglezing ontbreekt" in controle
    assert "TrueNAS-registerinstellingen" in formulier and "alleen pakketten mag lezen" in formulier


def test_b05_010_benoemt_praktijkproef_en_keuze_terugdraaiknop() -> None:
    evidence = json.loads((DOCS / "platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    regel = next(regel for regel in evidence if regel["id"] == "B05-010")
    assert regel["status"] == "Bezig" and "NIET GEVALIDEERD (echte NAS)" in regel["validatie"]
    assert "N-P6-05" in regel["blokkade"] and "APPDATA-snapshot" in regel["blokkade"]
    assert "terugdraaiknop blijft een open gebruikerskeuze" in regel["blokkade"]
    assert "Nog te bouwen: P3-13" not in regel["blokkade"]
