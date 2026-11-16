"""S-P3-200 (B05-001, P3-04/P3-14, besluit Z-10 optie 1): CI op de werkbranch en opgeruimde repo. Geen database nodig.

- ``.github/workflows/ci.yaml`` start bij een push op ``main`` én op de werkbranches ``ccr-*``; ``tags: ["v*"]``,
  ``pull_request``, ``workflow_dispatch`` en de invoer ``release_version`` blijven zoals ze waren.
- Publiceren naar ghcr.io blijft alleen bij een tag ``v*`` of een gevulde ``release_version``: een push op een
  werkbranch publiceert niets.
- ``release.yaml`` start alleen bij een wijziging van ``release/VERSION`` of met de hand.
- ``*.egg-info/`` staat in ``.gitignore`` en git volgt geen egg-info-bestanden meer.

De controles zijn losse functies; de negatieve gevallen onderaan bewijzen dat ze echt falen. Bewust zonder YAML-
bibliotheek (die is geen afhankelijkheid van ``mso-core``): het ``on:``-blok wordt als tekst gelezen.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CI = ROOT / ".github" / "workflows" / "ci.yaml"
RELEASE = ROOT / ".github" / "workflows" / "release.yaml"
GITIGNORE = ROOT / ".gitignore"


def on_blok(tekst: str) -> list[str]:
    """De regels onder ``on:`` tot de volgende sleutel op het hoogste niveau (zonder lege en commentaarregels)."""
    regels = tekst.splitlines()
    try:
        start = regels.index("on:")
    except ValueError:
        return []
    blok: list[str] = []
    for regel in regels[start + 1 :]:
        if regel and not regel[0].isspace() and not regel.startswith("#"):
            break
        if regel.strip() and not regel.strip().startswith("#"):
            blok.append(regel.rstrip())
    return blok


def lijst(waarde: str) -> list[str]:
    """``[main, "ccr-*"]`` -> ``["main", "ccr-*"]``."""
    m = re.fullmatch(r"\[(.*)\]", waarde.strip())
    if not m:
        return []
    return [d.strip().strip("\"'") for d in m.group(1).split(",") if d.strip()]


def ci_fouten(tekst: str) -> list[str]:
    blok = on_blok(tekst)
    fouten: list[str] = []
    if not blok:
        return ["ci.yaml heeft geen blok 'on:'"]
    sleutels = {r.strip().split(":")[0] for r in blok if re.match(r"^  [a-z_]+:", r)}
    for nodig in ("push", "pull_request", "workflow_dispatch"):
        if nodig not in sleutels:
            fouten.append(f"on.{nodig} ontbreekt")
    branches = [r for r in blok if r.startswith("    branches:")]
    tags = [r for r in blok if r.startswith("    tags:")]
    if len(branches) != 1:
        fouten.append("on.push.branches ontbreekt of staat er dubbel")
    else:
        b = lijst(branches[0].split(":", 1)[1])
        if "main" not in b:
            fouten.append("on.push.branches mist main")
        if "ccr-*" not in b:
            fouten.append("on.push.branches mist de werkbranches ccr-*")
        if set(b) - {"main", "ccr-*"}:
            fouten.append(f"on.push.branches heeft onverwachte branches: {sorted(set(b) - {'main', 'ccr-*'})}")
    if len(tags) != 1 or lijst(tags[0].split(":", 1)[1]) != ["v*"]:
        fouten.append('on.push.tags is niet meer precies ["v*"]')
    if not any(r.strip() == "release_version:" for r in blok):
        fouten.append("workflow_dispatch-invoer release_version ontbreekt")
    if any(r.strip().startswith("default:") and r.strip() != 'default: ""' for r in blok):
        fouten.append("release_version heeft niet meer de lege standaardwaarde")
    return fouten


def publiceer_fouten(tekst: str) -> list[str]:
    """Elke stap die bij ghcr.io inlogt of pusht hangt aan een tag ``v*`` of een gevulde ``release_version``."""
    fouten: list[str] = []
    stappen = re.split(r"\n\s*- (?=name:|uses:|run:)", tekst)
    for stap in stappen:
        if "ghcr.io" not in stap or not re.search(r"docker/login-action|push:\s*true|docker push", stap):
            continue
        voorwaarde = re.search(r"^\s*if:\s*(.+)$", stap, re.M)
        if not voorwaarde or "refs/tags/v" not in voorwaarde.group(1) or "release_version" not in voorwaarde.group(1):
            fouten.append(f"publicatiestap zonder voorwaarde op tag/release_version: {stap.strip().splitlines()[0]}")
    return fouten


def release_fouten(tekst: str) -> list[str]:
    blok = on_blok(tekst)
    sleutels = {r.strip().split(":")[0] for r in blok if re.match(r"^  [a-z_]+:", r)}
    fouten: list[str] = []
    if sleutels != {"push", "workflow_dispatch"}:
        fouten.append(f"release.yaml start op meer dan push(paths)/workflow_dispatch: {sorted(sleutels)}")
    paths = [r for r in blok if r.startswith("    paths:")]
    if len(paths) != 1 or lijst(paths[0].split(":", 1)[1]) != ["release/VERSION"]:
        fouten.append("release.yaml push hangt niet meer alleen aan release/VERSION")
    if any(r.startswith(("    branches:", "    tags:")) for r in blok):
        fouten.append("release.yaml push heeft branches/tags gekregen")
    return fouten


def gitignore_fouten(tekst: str) -> list[str]:
    regels = {r.strip() for r in tekst.splitlines()}
    return [] if "*.egg-info/" in regels else ["*.egg-info/ staat niet in .gitignore"]


# --- de echte bestanden ---------------------------------------------------------------------------------------------


def test_ci_start_ook_bij_push_op_werkbranch() -> None:
    assert ci_fouten(CI.read_text(encoding="utf-8")) == []


def test_push_op_werkbranch_publiceert_niets() -> None:
    tekst = CI.read_text(encoding="utf-8")
    assert "ghcr.io" in tekst
    assert publiceer_fouten(tekst) == []


def test_release_start_niet_door_een_gewone_push() -> None:
    assert release_fouten(RELEASE.read_text(encoding="utf-8")) == []


def test_egg_info_genegeerd() -> None:
    assert gitignore_fouten(GITIGNORE.read_text(encoding="utf-8")) == []


def test_git_volgt_geen_egg_info() -> None:
    git = shutil.which("git")
    assert git, "git is nodig voor deze controle"
    uit = subprocess.run(
        [git, "-C", str(ROOT), "ls-files"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert uit, "git ls-files gaf niets: geen checkout?"
    assert [p for p in uit if "egg-info" in p] == []


# --- negatieve gevallen: de controles falen echt -------------------------------------------------------------------

GOED = """name: ci

on:
  push:
    branches: [main, "ccr-*"]
    tags: ["v*"]
  pull_request:
  workflow_dispatch:
    inputs:
      release_version:
        description: "x"
        required: false
        default: ""

permissions:
  contents: read
"""


def test_goed_voorbeeld_heeft_geen_fouten() -> None:
    assert ci_fouten(GOED) == []


@pytest.mark.parametrize(
    ("oud", "nieuw", "fout"),
    [
        ('branches: [main, "ccr-*"]', "branches: [main]", "mist de werkbranches"),
        ('branches: [main, "ccr-*"]', 'branches: ["ccr-*"]', "mist main"),
        ('branches: [main, "ccr-*"]', 'branches: [main, "ccr-*", "*"]', "onverwachte branches"),
        ('tags: ["v*"]', 'tags: ["*"]', "tags"),
        ("      release_version:\n", "      versie:\n", "release_version ontbreekt"),
        ('default: ""', 'default: "1.0.0"', "lege standaardwaarde"),
        ("  pull_request:\n", "", "pull_request ontbreekt"),
    ],
)
def test_ci_fout_wordt_gezien(oud: str, nieuw: str, fout: str) -> None:
    assert oud in GOED
    fouten = ci_fouten(GOED.replace(oud, nieuw))
    assert any(fout in f for f in fouten), fouten


def test_publicatie_zonder_voorwaarde_wordt_gezien() -> None:
    stap = """    steps:
      - name: Inloggen bij ghcr.io
        if: (startsWith(github.ref, 'refs/tags/v') || inputs.release_version != '')
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
"""
    voorwaarde = "        if: (startsWith(github.ref, 'refs/tags/v') || inputs.release_version != '')\n"
    assert voorwaarde in stap
    assert publiceer_fouten(stap) == []
    assert publiceer_fouten(stap.replace(voorwaarde, ""))


def test_release_fout_wordt_gezien() -> None:
    goed = 'on:\n  push:\n    paths: ["release/VERSION"]\n  workflow_dispatch:\n\njobs:\n'
    assert release_fouten(goed) == []
    assert release_fouten(goed.replace('    paths: ["release/VERSION"]\n', '    branches: ["ccr-*"]\n'))
    assert release_fouten(goed.replace("  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n"))


def test_gitignore_fout_wordt_gezien() -> None:
    assert gitignore_fouten(".venv/\n*.egg-info/\n") == []
    assert gitignore_fouten(".venv/\n") == ["*.egg-info/ staat niet in .gitignore"]
