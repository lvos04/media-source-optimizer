"""S-P3-200 (B05-001, P3-04/P3-14, besluit Z-10 optie 1): CI op de werkbranch en opgeruimde repo. Geen database nodig.

- ``.github/workflows/ci.yaml`` start bij een push op ``main`` én op de werkbranches ``ccr-*``; ``tags: ["v*"]``,
  ``pull_request``, ``workflow_dispatch`` en de invoer ``release_version`` blijven zoals ze waren.
- Publiceren naar ghcr.io blijft alleen bij een tag ``v*`` of een gevulde ``release_version``: een push op een
  werkbranch publiceert niets. Dat wordt op gedrag getoetst (R-TECH-1): het script van de stap ``Tags`` wordt uit
  ``ci.yaml`` gelezen en in bash uitgevoerd (``refs/heads/ccr-x`` zonder ``release_version`` geeft ``push=false`` en
  een tag zonder ghcr.io; ``refs/tags/v1.2.3`` geeft ``push=true``), de ``if:`` van de login-stap moet precies de
  huidige uitdrukking zijn en ``docker/build-push-action`` moet ``push: ${{ steps.tags.outputs.push }}`` hebben.
- ``release.yaml`` start alleen bij een wijziging van ``release/VERSION`` of met de hand.
- ``*.egg-info/`` staat in ``.gitignore`` en git volgt geen egg-info-bestanden meer.

De controles zijn losse functies; de negatieve gevallen onderaan bewijzen dat ze echt falen. Bewust zonder YAML-
bibliotheek (die is geen afhankelijkheid van ``mso-core``): het ``on:``-blok wordt als tekst gelezen.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
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


LOGIN_IF = "(startsWith(github.ref, 'refs/tags/v') || inputs.release_version != '') && matrix.name != 'mso-mocks'"
BUILD_PUSH = "push: ${{ steps.tags.outputs.push }}"


def stappen(tekst: str) -> list[str]:
    """De stappen van alle jobs als losse tekstblokken (vanaf ``- name:``/``- uses:``/``- run:``)."""
    return re.split(r"\n\s*- (?=name:|uses:|run:)", tekst)[1:]


def tags_script(tekst: str) -> str | None:
    """De ``run: |``-tekst van de stap ``Tags`` zonder inspringing; ``None`` als die er niet precies één keer is."""
    regels = tekst.splitlines()
    starts = [i for i, r in enumerate(regels) if re.fullmatch(r"\s*- name: Tags\s*", r)]
    if len(starts) != 1:
        return None
    stap_inspring = len(regels[starts[0]]) - len(regels[starts[0]].lstrip())
    for i in range(starts[0] + 1, len(regels)):
        regel = regels[i]
        inspring = len(regel) - len(regel.lstrip())
        if regel.strip() and inspring <= stap_inspring:
            return None
        if re.fullmatch(r"\s*run: \|\s*", regel):
            run_inspring = inspring
            script: list[str] = []
            for r in regels[i + 1 :]:
                if r.strip() and len(r) - len(r.lstrip()) <= run_inspring:
                    break
                script.append(r)
            breedte = min(len(r) - len(r.lstrip()) for r in script if r.strip())
            return "\n".join(r[breedte:] for r in script).strip() + "\n"
    return None


def tags_uitvoer(script: str, ref: str, release_version: str, naam: str) -> dict[str, str]:
    """Voer het Tags-script uit zoals GitHub dat doet (``bash -e``) en geef de regels uit ``$GITHUB_OUTPUT`` terug."""
    bash = shutil.which("bash")
    assert bash, "bash is nodig voor deze controle"
    script = script.replace("${{ matrix.name }}", naam)
    assert "${{" not in script, f"onbekende expressie in het Tags-script: {script}"
    with tempfile.TemporaryDirectory() as map_:
        uit = Path(map_) / "github_output"
        uit.write_text("", encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GITHUB_REF": ref,
            "GITHUB_REF_NAME": ref.split("/", 2)[2],
            "RELEASE_VERSION": release_version,
            "GITHUB_OUTPUT": str(uit),
        }
        subprocess.run(
            [bash, "--noprofile", "--norc", "-e", "-c", script], check=True, env=env, capture_output=True, text=True
        )
        paren = [r.split("=", 1) for r in uit.read_text(encoding="utf-8").splitlines() if "=" in r]
    return {k: v for k, v in paren}


def gedrag_fouten(tekst: str) -> list[str]:
    """R-TECH-1: toets wat de workflow dóét bij een push op ccr-* en bij een tag, niet alleen of tekst aanwezig is."""
    fouten: list[str] = []
    script = tags_script(tekst)
    if script is None:
        fouten.append("stap 'Tags' met 'run: |' niet (precies één keer) gevonden")
    else:
        for naam in ("media-source-optimizer-core", "media-source-optimizer-web", "mso-mocks"):
            werk = tags_uitvoer(script, "refs/heads/ccr-x", "", naam)
            if werk.get("push") != "false":
                fouten.append(f"Tags op refs/heads/ccr-x ({naam}) geeft push={werk.get('push')!r}, niet 'false'")
            if "ghcr.io" in werk.get("tags", "ghcr.io"):
                fouten.append(f"Tags op refs/heads/ccr-x ({naam}) geeft tag {werk.get('tags')!r}: ghcr.io of leeg")
        for naam in ("media-source-optimizer-core", "media-source-optimizer-web"):
            tag = tags_uitvoer(script, "refs/tags/v1.2.3", "", naam)
            if tag.get("push") != "true":
                fouten.append(f"Tags op refs/tags/v1.2.3 ({naam}) geeft push={tag.get('push')!r}, niet 'true'")
            if tag.get("tags") != f"ghcr.io/lvos04/{naam}:1.2.3":
                fouten.append(f"Tags op refs/tags/v1.2.3 ({naam}) geeft tag {tag.get('tags')!r}")
    logins = [s for s in stappen(tekst) if "docker/login-action" in s]
    if len(logins) != 1:
        fouten.append(f"verwacht precies één stap met docker/login-action, gevonden: {len(logins)}")
    for stap in logins:
        voorwaarde = re.search(r"^\s*if:\s*(.+?)\s*$", stap, re.M)
        if not voorwaarde or voorwaarde.group(1) != LOGIN_IF:
            fouten.append(f"login-if is niet precies {LOGIN_IF!r}: {voorwaarde.group(1) if voorwaarde else None!r}")
    bouwers = [s for s in stappen(tekst) if "docker/build-push-action" in s]
    if not bouwers:
        fouten.append("geen stap met docker/build-push-action")
    for stap in bouwers:
        pushes = [r.strip() for r in stap.splitlines() if re.match(r"^\s*push:", r)]
        if pushes != [BUILD_PUSH]:
            fouten.append(f"build-push-action heeft niet precies {BUILD_PUSH!r}: {pushes}")
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


def test_push_op_werkbranch_publiceert_niets_gedrag() -> None:
    assert gedrag_fouten(CI.read_text(encoding="utf-8")) == []


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


# --- R-TECH-1: de mutatie van de reviewer (login-if + Tags publiceren op ccr-*) moet rood worden --------------------

TAGS_ELSE_GOED = """            echo "tags=${{ matrix.name }}:ci" >> "$GITHUB_OUTPUT"
            echo "push=false" >> "$GITHUB_OUTPUT"
"""
TAGS_ELSE_FOUT = """            echo "tags=ghcr.io/lvos04/${{ matrix.name }}:ci" >> "$GITHUB_OUTPUT"
            echo "push=true" >> "$GITHUB_OUTPUT"
"""
LOGIN_IF_FOUT = (
    "(startsWith(github.ref, 'refs/tags/v') || inputs.release_version != ''"
    " || startsWith(github.ref, 'refs/heads/ccr-')) && matrix.name != 'mso-mocks'"
)


def test_mutatie_publiceren_op_werkbranch_wordt_gezien() -> None:
    echt = CI.read_text(encoding="utf-8")
    assert TAGS_ELSE_GOED in echt and LOGIN_IF in echt
    alleen_tags = echt.replace(TAGS_ELSE_GOED, TAGS_ELSE_FOUT)
    alleen_login = echt.replace(LOGIN_IF, LOGIN_IF_FOUT)
    beide = alleen_tags.replace(LOGIN_IF, LOGIN_IF_FOUT)
    assert any("ccr-x" in f and "push='true'" in f for f in gedrag_fouten(alleen_tags))
    assert any("ccr-x" in f and "ghcr.io" in f for f in gedrag_fouten(alleen_tags))
    assert any("login-if" in f for f in gedrag_fouten(alleen_login))
    fouten = gedrag_fouten(beide)
    assert any("ccr-x" in f for f in fouten) and any("login-if" in f for f in fouten), fouten


def test_build_push_zonder_tags_uitvoer_wordt_gezien() -> None:
    echt = CI.read_text(encoding="utf-8")
    assert BUILD_PUSH in echt
    assert any("build-push-action" in f for f in gedrag_fouten(echt.replace(BUILD_PUSH, "push: true")))
    assert any("build-push-action" in f for f in gedrag_fouten(echt.replace("          " + BUILD_PUSH + "\n", "")))


def test_tags_zonder_publicatie_bij_tag_wordt_gezien() -> None:
    echt = CI.read_text(encoding="utf-8")
    goed = (
        'echo "tags=ghcr.io/lvos04/${{ matrix.name }}:${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"\n'
        '            echo "push=true"'
    )
    assert goed in echt
    fout = goed.replace('echo "push=true"', 'echo "push=false"')
    assert any("v1.2.3" in f for f in gedrag_fouten(echt.replace(goed, fout)))
    assert any("Tags" in f for f in gedrag_fouten(echt.replace("- name: Tags\n", "- name: Labels\n")))
