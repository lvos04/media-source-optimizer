"""Installatie op TrueNAS blijft geblokkeerd zolang P-INSTALLATIE open staat."""

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[3] / "docs"


@pytest.mark.parametrize("name", ["TRUENAS_INSTALL.md", "HARDWARE_ACCEPTANCE.md"])
def test_installatiepoort_blokkeert_uitvoering(name: str) -> None:
    document = (DOCS / name).read_text(encoding="utf-8")
    for legacy_instruction in ("sudo zfs create", "→ Install via YAML", "mso-compose.yaml"):
        assert legacy_instruction not in document
    before_steps = document.split("\n## ", 1)[0]
    assert "P-INSTALLATIE" in before_steps and "niet uitvoeren" in before_steps.lower()
    assert "](TRUENAS_CUSTOM_APP.md)" in before_steps
