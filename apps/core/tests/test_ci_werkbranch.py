"""Toets CI-triggers, lokale testbouw en de publicatiegrens voor versietags."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from textwrap import dedent

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
    if any(r.strip().startswith("inputs:") for r in blok):
        fouten.append("de gewone CI heeft publicatie-invoer")
    return fouten


def publiceer_fouten(tekst: str) -> list[str]:
    """De CI bouwt op alle gebeurtenissen lokaal, ook bij een tag."""
    fouten = []
    for verboden in ("docker/login-action", "docker push", "packages: write", "release_version"):
        if verboden in tekst:
            fouten.append(verboden)
    stappen = re.split(r"\n\s*- (?=name:|uses:|run:)", tekst)
    bouwers = [stap for stap in stappen if "uses: docker/build-push-action@" in stap]
    if not bouwers:
        fouten.append("testbouw ontbreekt")
    for stap in bouwers:
        velden = [regel.strip() for regel in stap.splitlines()]
        if "push: false" not in velden or "tags: ${{ matrix.name }}:ci" not in velden:
            fouten.append("build-push-action moet lokaal bouwen met testtag")
    return fouten


def release_trigger(tekst: str) -> bool:
    return on_blok(tekst) == ["  push:", '    tags: ["v*.*.*"]']


def gitignore_fouten(tekst: str) -> list[str]:
    regels = {r.strip() for r in tekst.splitlines()}
    return [] if "*.egg-info/" in regels else ["*.egg-info/ staat niet in .gitignore"]


# --- de echte bestanden ---------------------------------------------------------------------------------------------


def test_ci_start_ook_bij_push_op_werkbranch() -> None:
    assert ci_fouten(CI.read_text(encoding="utf-8")) == []


def test_ci_publiceert_bij_geen_enkele_gebeurtenis() -> None:
    assert publiceer_fouten(CI.read_text()) == []


@pytest.mark.parametrize("pad", [RELEASE, ROOT / ".forgejo/workflows/release.yaml"])
def test_release_start_alleen_op_versietags(pad) -> None:
    tekst = pad.read_text()
    assert release_trigger(tekst)
    registercontrole = "bash scripts/controleer-releasetag.sh "
    assert tekst.index(registercontrole) < tekst.index('docker push "$image"')


def test_release_hecht_digest_aan_de_bestaande_tag() -> None:
    tekst = RELEASE.read_text()
    assert tekst.index('docker push "$image"') < tekst.index('gh release create')
    bijlage = tekst.split("gh release create", 1)[1]
    assert '"$GITHUB_REF_NAME" --verify-tag' in bijlage and "release/digests.json" in bijlage
    assert "--clobber" not in tekst and "git push" not in tekst
    for workflow in (RELEASE, CI):
        acties = re.findall(r"uses: ([^\s]+)", workflow.read_text())
        assert acties and all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", actie) for actie in acties)


@pytest.mark.parametrize("storing", ["geen", "ongeldige_digest", "pushfout"])
def test_releasebijlage_bevat_drie_registerdigests_of_ontbreekt(tmp_path, storing):
    tekst = RELEASE.read_text()
    stap = tekst.split("      - name: Push de drie release-images", 1)[1].split("\n      - ", 1)[0]
    script = dedent(stap.split("        run: |\n", 1)[1])
    (tmp_path / "release").mkdir()
    docker = tmp_path / "docker"
    docker.write_text("""#!/bin/bash
set -eu
[[ "$1" == push ]]
digest=$(printf '%s' "$2" | sha256sum | cut -d' ' -f1)
if [[ "$2" == *-core:* ]]; then
    case "$STORING" in
        ongeldige_digest) digest=onvolledig ;;
        pushfout) exit 1 ;;
    esac
fi
echo "${2##*:}: digest: sha256:$digest size: 123"
""")
    docker.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "RUNNER_TEMP": str(tmp_path),
           "STORING": storing, "MSO_BUILD_VERSION": "1.2.3"}
    run = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=tmp_path, env=env, capture_output=True)
    bijlage = tmp_path / "release/digests.json"
    if storing != "geen":
        assert run.returncode != 0 and not bijlage.exists()
        return
    assert run.returncode == 0
    images = [f"ghcr.io/lvos04/media-source-optimizer{suffix}:1.2.3" for suffix in ("", "-core", "-web")]
    verwacht = {image: "sha256:" + sha256(image.encode()).hexdigest() for image in images}
    assert json.loads(bijlage.read_text()) == verwacht


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
        ("  workflow_dispatch:\n", "  workflow_dispatch:\n    inputs:\n", "publicatie-invoer"),
        ("  pull_request:\n", "", "pull_request ontbreekt"),
    ],
)
def test_ci_fout_wordt_gezien(oud: str, nieuw: str, fout: str) -> None:
    assert oud in GOED
    fouten = ci_fouten(GOED.replace(oud, nieuw))
    assert any(fout in f for f in fouten), fouten


@pytest.mark.parametrize("verboden", ["docker/login-action", "docker push", "packages: write", "release_version"])
def test_publicatiemogelijkheid_in_ci_wordt_gezien(verboden) -> None:
    assert verboden in publiceer_fouten(CI.read_text() + "\n" + verboden)


@pytest.mark.parametrize("vervanging", ["push: true", "", "push: ${{ inputs.publiceren }}"])
def test_bouw_met_gewijzigde_push_wordt_gezien(vervanging) -> None:
    assert publiceer_fouten(CI.read_text().replace("push: false", vervanging))


def test_ci_weigert_registertag_in_testbouw() -> None:
    assert publiceer_fouten(CI.read_text().replace("tags: ${{ matrix.name }}:ci", "tags: ghcr.io/test/image:ci"))


@pytest.mark.parametrize("extra", ["  workflow_dispatch:", "  pull_request:", '    branches: ["ccr-*"]',
                                   '    paths: ["release/VERSION"]'])
def test_extra_releasetrigger_wordt_gezien(extra) -> None:
    assert not release_trigger(RELEASE.read_text().replace("  push:", extra + "\n  push:"))


def test_gitignore_fout_wordt_gezien() -> None:
    assert gitignore_fouten(".venv/\n*.egg-info/\n") == []
    assert gitignore_fouten(".venv/\n") == ["*.egg-info/ staat niet in .gitignore"]


def controleer_tag(tmp_path, ref, *args):
    env = {**os.environ, "GITHUB_REF": ref, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    return subprocess.run(["bash", str(ROOT / "scripts/controleer-releasetag.sh"), *args], cwd=tmp_path,
                          env=env, capture_output=True, text=True)


@pytest.mark.parametrize(("versie", "ref", "geldig"), [
    ("1.2.3", "refs/tags/v1.2.3", True), ("1.2.3", "refs/tags/v1.2.2", False),
    ("1.2.3", "refs/heads/v1.2.3", False), ("1.2.3", "refs/heads/ccr-x", False),
    ("latest", "refs/tags/vlatest", False), ("1.2.3-test", "refs/tags/v1.2.3-test", False),
    ("01.2.3", "refs/tags/v01.2.3", False),
])
def test_gitref_en_versiebestand_moeten_exact_overeenkomen(tmp_path, versie, ref, geldig) -> None:
    (tmp_path / "release").mkdir()
    (tmp_path / "release/VERSION").write_text(versie + "\n")
    resultaat = controleer_tag(tmp_path, ref)
    assert (resultaat.returncode == 0) == geldig
    if geldig:
        assert resultaat.stdout.strip() == versie


@pytest.mark.parametrize("bestaand", ["media-source-optimizer", "media-source-optimizer-core",
                                     "media-source-optimizer-web"])
@pytest.mark.parametrize(("antwoord", "toestaan"), [
    ("manifest unknown", True), ("bestaat", False), ("unauthorized", False), ("timeout", False),
])
def test_register_moet_elke_tag_uitdrukkelijk_vrij_melden(tmp_path, bestaand, antwoord, toestaan):
    (tmp_path / "release").mkdir()
    (tmp_path / "release/VERSION").write_text("1.2.3\n")
    # De andere twee manifesten ontbreken. Een fout bij één naam blokkeert de volledige publicatiestap.
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/bash\n[[ "$1 $2" == "manifest inspect" ]] || exit 2\n'
        f'if [[ "$3" == "register/{bestaand}:1.2.3" ]]; then\n'
        + ('  exit 0\n' if antwoord == "bestaat" else f'  echo "{antwoord}" >&2; exit 1\n')
        + 'fi\necho "manifest unknown" >&2; exit 1\n'
    )
    docker.chmod(0o700)
    resultaat = controleer_tag(tmp_path, "refs/tags/v1.2.3", "register")
    assert (resultaat.returncode == 0) == toestaan
