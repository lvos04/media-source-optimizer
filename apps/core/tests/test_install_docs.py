"""Toets de vrijgave en terugvalroute in de TrueNAS-documentatie."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs"


@pytest.mark.parametrize("name", ["TRUENAS_INSTALL.md", "HARDWARE_ACCEPTANCE.md"])
def test_installatiepoort_blokkeert_uitvoering(name: str) -> None:
    document = (DOCS / name).read_text(encoding="utf-8")
    for legacy_instruction in ("sudo zfs create", "→ Install via YAML", "mso-compose.yaml"):
        assert legacy_instruction not in document
    before_steps = document.split("\n## ", 1)[0]
    assert "P-INSTALLATIE" in before_steps and "niet uitvoeren" in before_steps.lower()
    assert "](TRUENAS_CUSTOM_APP.md)" in before_steps


def test_custom_app_formulier_en_manifest_zijn_aanwezig() -> None:
    formulier = (ROOT / "deploy/truenas/CUSTOM_APP.md").read_text(encoding="utf-8")
    assert "`/mnt/<POOL>/" in formulier
    assert "`/appdata`" in formulier
    assert (ROOT / "deploy/manifest/media-source-optimizer.manifest.yaml").is_file()
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
