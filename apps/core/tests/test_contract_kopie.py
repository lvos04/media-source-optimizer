"""C1.3-MSO-01 / C1.3-ALG-01: drifttest van de byte-gelijke kopie van het platformcontract (contract 1.3, kop en §10).

``docs/platform-v2/PLATFORM_CONTRACTEN.md`` en ``docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md`` zijn byte-gelijk aan
``lvos04/ai-server-manager`` op de vaste bron-commit hieronder. Een repo past een kopie nooit aan; een nieuwe kopie
overnemen is een stap van de orkestratie (``scripts/bronkopie.py bijwerken --soort contract``). Staat de bron naast deze
repo uitgecheckt (zoals in de bouwomgeving), dan wordt ook de inhoud op die commit nagerekend; de vergelijking met de
bron op GitHub doet CI (``scripts/bronkopie.py vergelijk --online``, job python).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PV2 = ROOT / "docs" / "platform-v2"
BRON_REPO = "lvos04/ai-server-manager"
#: vaste bron-commit (branch ccr-c133d7a0-kuz1cv); contract 1.3 inclusief de reviewcorrecties RV1–RV13 (ADR-0028)
BRON_COMMIT = "0a7143bc0a94ed5317a9655fa785e73a1fb07ba1"
PINS = {
    "PLATFORM_CONTRACTEN.md": "15b30cf17f91b606451ef9c41f02327076ef76ce655ee94e40e3538106962310",
    "CONTRACT_1.3_CONFORMITEIT.md": "f0a2352ddb27088ae668013501c62a1eaaf879efe742a2d4422ffe8d33b43466",
}
NAAST = ROOT.parent / "ai-server-manager"


@pytest.mark.parametrize(("naam", "digest"), sorted(PINS.items()))
def test_kopie_is_byte_gelijk_aan_de_bron_commit(naam: str, digest: str) -> None:
    data = (PV2 / naam).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, (
        f"{naam} wijkt af van {BRON_REPO}@{BRON_COMMIT[:12]} — nooit lokaal aanpassen; nieuwe kopie via "
        "scripts/bronkopie.py bijwerken --soort contract"
    )


def test_kopie_is_contract_1_3() -> None:
    tekst = (PV2 / "PLATFORM_CONTRACTEN.md").read_text(encoding="utf-8")
    assert "Versie: `contracts 1.3`" in tekst
    lijst = (PV2 / "CONTRACT_1.3_CONFORMITEIT.md").read_text(encoding="utf-8")
    assert lijst.startswith("# Conformiteit met contract 1.3")
    assert "## 7. media-source-optimizer" in lijst


def test_pinbestand_noemt_dezelfde_commit_en_sha256() -> None:
    pin = json.loads((PV2 / "HERKOMST_CONTRACT.json").read_text(encoding="utf-8"))
    assert pin["soort"] == "contract" and pin["versie"] == "1.3"
    assert pin["bron_repo"] == BRON_REPO and pin["bron_commit"] == BRON_COMMIT
    assert {Path(b["kopie"]).name: b["sha256"] for b in pin["bestanden"]} == PINS
    assert all(b["kopie"] == b["bron"] and b["kop"] is None for b in pin["bestanden"])


@pytest.mark.skipif(not (NAAST / ".git").exists(), reason="bron ai-server-manager niet naast deze repo uitgecheckt")
@pytest.mark.parametrize(("naam", "digest"), sorted(PINS.items()))
def test_bron_commit_heeft_de_gepinde_inhoud(naam: str, digest: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(NAAST), "show", f"{BRON_COMMIT}:docs/platform-v2/{naam}"],
        capture_output=True, check=False, timeout=60,
    )
    if proc.returncode != 0:
        pytest.skip(f"bron-commit {BRON_COMMIT[:12]} niet in de naastliggende checkout: {proc.stderr[:120]!r}")
    assert hashlib.sha256(proc.stdout).hexdigest() == digest
