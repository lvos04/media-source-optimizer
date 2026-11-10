#!/usr/bin/env python3
"""Versie-gepinde kopieën uit ``lvos04/ai-server-manager`` bewaken (alleen de standaardbibliotheek).

Twee soorten kopieën, elk met één pinbestand (de enige plek waar versie, bron-commit en sha256 staan):

``docs/platform-v2/HERKOMST_CONTRACT.json``  (soort ``contract``)
    ``PLATFORM_CONTRACTEN.md`` en ``CONTRACT_1.3_CONFORMITEIT.md``, **byte-gelijk** aan de bron op een vaste bron-commit
    (contract 1.3, kop en §10). Een nieuwere of gewijzigde bron geeft een **waarschuwing**: een nieuwe kopie overnemen
    is een stap van de orkestratie, nooit een lokale aanpassing (ADR-0009).
``design/ontwerptaal/HERKOMST.json``  (soort ``peil``)
    De ontwerptaal Peil (ONTWERPTAAL §14, pin- en driftregel E08). CSS- en md-kopieën hebben een **herkomstkop van
    precies 2 regels** met versie, bron-commit, bronpad en de sha256 van het bronbestand; alles daaronder is byte-gelijk
    aan de bron. JSON-bestanden kunnen geen kop dragen en zijn byte-gelijk. Tegen de bron: dezelfde versie met andere
    inhoud = **drift (fout)** voor CSS en JSON (de kopieën van §14); voor de md-documenten een waarschuwing; een
    nieuwere versie = waarschuwing (bewust bijwerken, niet stil meenemen).

Opdrachten::

    controleer                                  offline: kopieën, koppen en pins kloppen (CI, elke run; unit-tests)
    vergelijk --bron <map> | --online           tegen de bron (een checkout van ai-server-manager of GitHub)
    bijwerken --soort peil|contract --bron <map> --commit <40 hex> [--ref <branch>]

Uitgang: 0 = in orde (eventueel met waarschuwingen), 1 = drift of fout, 2 = verkeerd gebruik, 3 = bron niet bereikbaar
(dat is nooit "in orde": de vergelijking is dan niet gedaan).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PINNEN = ("docs/platform-v2/HERKOMST_CONTRACT.json", "design/ontwerptaal/HERKOMST.json")
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{pad}"

#: versie zoals de bron hem zelf noemt (in het bestand ``versiebron`` van de pin)
VERSIE = {
    "contract": re.compile(r"Versie: `contracts (\d+\.\d+)`"),
    "peil": re.compile(r"Peil (\d+\.\d+\.\d+) — ontwerptaal"),
}
KOP = re.compile(
    r"HERKOMST — Peil (?P<versie>\d+\.\d+\.\d+) · bron: (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40}) "
    r"(?P<bron>\S+) · sha256 van het bronbestand \(zonder deze kop\): (?P<sha>[0-9a-f]{64})"
)
SHA40 = re.compile(r"[0-9a-f]{40}")


class Fout(Exception):
    """Verkeerd gebruik of een onleesbaar pinbestand."""


class BronOnbereikbaar(Exception):
    """De bron kon niet worden gelezen (netwerk of git); de vergelijking is dan niet gedaan."""


@dataclass(frozen=True)
class Bestand:
    kopie: str
    bron: str
    sha256: str
    kop: str | None  # "css" | "md" | None (byte-gelijk)
    toelichting: str = ""


@dataclass(frozen=True)
class Pin:
    pad: str
    soort: str
    versie: str
    bron_repo: str
    bron_ref: str
    bron_commit: str
    versiebron: str
    mappen: tuple[str, ...]
    bestanden: tuple[Bestand, ...]


@dataclass
class Uitkomst:
    fouten: list[str] = field(default_factory=list)
    waarschuwingen: list[str] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def versie_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def lees_pin(root: Path, rel: str) -> Pin:
    try:
        data = json.loads((root / rel).read_text(encoding="utf-8"))
        bestanden = tuple(
            Bestand(b["kopie"], b["bron"], b["sha256"], b.get("kop"), b.get("toelichting", ""))
            for b in data["bestanden"]
        )
        pin = Pin(
            rel, data["soort"], data["versie"], data["bron_repo"], data["bron_ref"], data["bron_commit"],
            data["versiebron"], tuple(data.get("mappen", [])), bestanden,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Fout(f"{rel}: pinbestand onleesbaar ({exc})") from exc
    if pin.soort not in VERSIE:
        raise Fout(f"{rel}: onbekende soort {pin.soort!r}")
    if not SHA40.fullmatch(pin.bron_commit):
        raise Fout(f"{rel}: bron_commit moet 40 hex-tekens zijn")
    if not any(b.bron == pin.versiebron for b in bestanden):
        raise Fout(f"{rel}: versiebron {pin.versiebron} staat niet in de bestanden")
    for b in bestanden:
        if b.kop not in (None, "css", "md"):
            raise Fout(f"{rel}: {b.kopie}: onbekend kopformaat {b.kop!r}")
    return pin


def maak_kop(pin: Pin, b: Bestand, versie: str, commit: str, sha: str) -> bytes:
    kern = (
        f"HERKOMST — Peil {versie} · bron: {pin.bron_repo}@{commit} {b.bron} · "
        f"sha256 van het bronbestand (zonder deze kop): {sha}"
    )
    if b.kop == "css":
        return f"/* {kern}\n   {b.toelichting} */\n".encode()
    return f"<!-- {kern} · {b.toelichting} -->\n\n".encode()


def splits_kop(data: bytes) -> tuple[str, str, bytes] | None:
    """(regel 1, regel 2, bronbytes) of None als er geen twee regels zijn."""
    eerste, nl1, rest = data.partition(b"\n")
    tweede, nl2, lijf = rest.partition(b"\n")
    if not nl1 or not nl2:
        return None
    return eerste.decode("utf-8", "replace"), tweede.decode("utf-8", "replace"), lijf


def _controleer_kop(pin: Pin, b: Bestand, data: bytes, uit: Uitkomst) -> bytes | None:
    delen = splits_kop(data)
    if delen is None:
        uit.fouten.append(f"{b.kopie}: geen herkomstkop van 2 regels")
        return None
    r1, r2, lijf = delen
    vorm_ok = (
        (r1.startswith("/* ") and "*/" not in r1 and r2.endswith(" */"))
        if b.kop == "css"
        else (r1.startswith("<!-- ") and r1.endswith(" -->") and r2 == "")
    )
    if not vorm_ok:
        uit.fouten.append(f"{b.kopie}: herkomstkop heeft niet de vorm voor {b.kop}")
    m = KOP.search(r1)
    if not m:
        uit.fouten.append(f"{b.kopie}: herkomstkop onleesbaar (versie, bron-commit, pad en sha256 verwacht)")
        return lijf
    verwacht = {"versie": pin.versie, "repo": pin.bron_repo, "commit": pin.bron_commit, "bron": b.bron, "sha": b.sha256}
    for veld, waarde in verwacht.items():
        if m.group(veld) != waarde:
            uit.fouten.append(f"{b.kopie}: kop noemt {veld} {m.group(veld)!r}, pin {pin.pad} zegt {waarde!r}")
    return lijf


def controleer(root: Path = REPO_ROOT, pinnen: tuple[str, ...] = PINNEN) -> Uitkomst:
    """Offline: elke kopie heeft de gepinde inhoud, de kop klopt met de pin en de inhoud noemt de pinversie."""
    uit = Uitkomst()
    for rel in pinnen:
        try:
            pin = lees_pin(root, rel)
        except Fout as exc:
            uit.fouten.append(str(exc))
            continue
        gepind = {b.kopie for b in pin.bestanden}
        for b in pin.bestanden:
            pad = root / b.kopie
            if not pad.is_file():
                uit.fouten.append(f"{b.kopie}: ontbreekt (gepind in {rel})")
                continue
            data = pad.read_bytes()
            lijf = _controleer_kop(pin, b, data, uit) if b.kop else data
            if lijf is None:
                continue
            if sha256(lijf) != b.sha256:
                uit.fouten.append(
                    f"{b.kopie}: inhoud wijkt af van de gepinde sha256 ({b.sha256[:12]}…) — met de hand gewijzigd? "
                    "Nieuwe versie alleen via 'bijwerken'"
                )
                continue
            if b.bron == pin.versiebron:
                m = VERSIE[pin.soort].search(lijf.decode("utf-8", "replace"))
                if not m or m.group(1) != pin.versie:
                    uit.fouten.append(f"{b.kopie}: inhoud noemt versie {m.group(1) if m else '?'}, pin {pin.versie}")
            uit.ok.append(f"{b.kopie}  {pin.soort} {pin.versie}  sha256 {b.sha256}")
        for map_rel in pin.mappen:
            for pad in sorted((root / map_rel).rglob("*")):
                rel_pad = pad.relative_to(root).as_posix()
                if pad.is_file() and not pad.name.startswith("HERKOMST.") and rel_pad not in gepind:
                    uit.fouten.append(f"{rel_pad}: staat in een gepinde map maar niet in {rel}")
    return uit


# ------------------------------------------------------------------------------------------------ bron lezen
Lezer = Callable[[str, str], bytes | None]  # (ref of commit, pad) → bytes, None = bestaat niet


def git_lezer(bron: Path) -> Lezer:
    if not (bron / ".git").exists():
        raise Fout(f"{bron}: geen git-checkout van de bron")

    bekend: set[str] = set()

    def lees(ref: str, pad: str) -> bytes | None:
        if ref not in bekend:  # een onbekende commit of branch is "niet leesbaar", nooit "bestand bestaat niet"
            probe = subprocess.run(
                ["git", "-C", str(bron), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                capture_output=True, check=False, timeout=60,
            )
            if probe.returncode != 0:
                raise BronOnbereikbaar(f"{ref} bestaat niet in {bron} (git fetch nodig?)")
            bekend.add(ref)
        proc = subprocess.run(
            ["git", "-C", str(bron), "show", f"{ref}:{pad}"], capture_output=True, check=False, timeout=60
        )
        if proc.returncode == 0:
            return proc.stdout
        fout = proc.stderr.decode("utf-8", "replace")
        if "does not exist" in fout or "exists on disk, but not in" in fout:
            return None
        raise BronOnbereikbaar(f"git show {ref}:{pad} in {bron}: {fout.strip()[:200]}")

    return lees


def online_lezer(repo: str, pogingen: int = 4) -> Lezer:
    def lees(ref: str, pad: str) -> bytes | None:
        url = RAW.format(repo=repo, ref=ref, pad=pad)
        laatste = ""
        for poging in range(pogingen):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (vaste https-URL)
                    return bytes(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                laatste = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                laatste = str(exc)
            if poging + 1 < pogingen:
                time.sleep(2 ** (poging + 1))
        raise BronOnbereikbaar(f"{url}: {laatste}")

    return lees


def vergelijk(
    lezer: Lezer, root: Path = REPO_ROOT, pinnen: tuple[str, ...] = PINNEN, ref: str | None = None
) -> Uitkomst:
    """Tegen de bron: (1) de gepinde commit heeft exact de gepinde inhoud (anders fout); (2) op de bronbranch: nieuwere
    versie = waarschuwing; zelfde versie met andere inhoud = fout bij Peil (E08), waarschuwing bij het contract."""
    uit = Uitkomst()
    for rel in pinnen:
        pin = lees_pin(root, rel)
        for b in pin.bestanden:
            data = lezer(pin.bron_commit, b.bron)
            if data is None:
                uit.fouten.append(f"{b.kopie}: {b.bron} bestaat niet op de gepinde bron-commit {pin.bron_commit[:12]}")
            elif sha256(data) != b.sha256:
                uit.fouten.append(
                    f"{b.kopie}: de gepinde bron-commit {pin.bron_commit[:12]} heeft andere inhoud voor {b.bron} "
                    f"({sha256(data)[:12]}… ≠ pin {b.sha256[:12]}…)"
                )
        kop_ref = ref or pin.bron_ref
        hoofd = {b.bron: lezer(kop_ref, b.bron) for b in pin.bestanden}
        versiedata = hoofd.get(pin.versiebron)
        m = VERSIE[pin.soort].search(versiedata.decode("utf-8", "replace")) if versiedata else None
        if not m:
            uit.fouten.append(f"{rel}: versie van de bron op {kop_ref} onleesbaar ({pin.versiebron})")
            continue
        bronversie = m.group(1)
        if versie_tuple(bronversie) > versie_tuple(pin.versie):
            uit.waarschuwingen.append(
                f"{rel}: de bron op {kop_ref} heeft {pin.soort} {bronversie} (gepind {pin.versie}); bijwerken is een "
                "bewuste stap (scripts/bronkopie.py bijwerken + de eigen tests), geen rode CI"
            )
            continue
        if versie_tuple(bronversie) < versie_tuple(pin.versie):
            uit.fouten.append(f"{rel}: de bron op {kop_ref} heeft een oudere versie {bronversie} dan pin {pin.versie}")
            continue
        for b in pin.bestanden:
            data = hoofd[b.bron]
            if data is not None and sha256(data) == b.sha256:
                uit.ok.append(f"{b.kopie}  gelijk aan {kop_ref}")
                continue
            reden = "ontbreekt" if data is None else "heeft andere inhoud"
            melding = f"{b.kopie}: {b.bron} {reden} op {kop_ref} bij dezelfde versie {pin.versie}"
            if pin.soort == "peil" and b.kop != "md":
                uit.fouten.append(melding + " — drift (ONTWERPTAAL §14, E08)")
            elif pin.soort == "peil":
                # de regels/handoff als document: geen kopie in de zin van §14 (die noemt CSS, aliaslagen en het
                # sjabloon); een tekstwijziging zonder nieuwe versie is daar geen reden voor een rode CI
                uit.waarschuwingen.append(melding + " — document gewijzigd zonder nieuwe Peil-versie; bijwerken")
            else:
                uit.waarschuwingen.append(melding + " — nieuwe kopie overnemen is een stap van de orkestratie")
    return uit


def bijwerken(root: Path, soort: str, lezer: Lezer, commit: str, ref: str | None = None) -> Pin:
    """Kopieën en pin opnieuw schrijven vanuit de bron op ``commit`` (bestandenlijst, koppen en toelichting blijven)."""
    if not SHA40.fullmatch(commit):
        raise Fout("--commit moet een volledige commit-SHA (40 hex) zijn")
    rel = next((p for p in PINNEN if lees_pin(root, p).soort == soort), None)
    if rel is None:
        raise Fout(f"geen pin voor soort {soort}")
    pin = lees_pin(root, rel)
    inhoud: dict[str, bytes] = {}
    for b in pin.bestanden:
        data = lezer(commit, b.bron)
        if data is None:
            raise Fout(f"{b.bron} bestaat niet op {commit}")
        inhoud[b.bron] = data
    m = VERSIE[soort].search(inhoud[pin.versiebron].decode("utf-8", "replace"))
    if not m:
        raise Fout(f"versie onleesbaar in {pin.versiebron} op {commit}")
    versie = m.group(1)
    nieuw = []
    for b in pin.bestanden:
        data = inhoud[b.bron]
        sha = sha256(data)
        kop = maak_kop(pin, b, versie, commit, sha) if b.kop else b""
        (root / b.kopie).parent.mkdir(parents=True, exist_ok=True)
        (root / b.kopie).write_bytes(kop + data)
        nieuw.append({"kopie": b.kopie, "bron": b.bron, "kop": b.kop, "sha256": sha, "toelichting": b.toelichting})
    doc = {
        "soort": soort,
        "versie": versie,
        "bron_repo": pin.bron_repo,
        "bron_ref": ref or pin.bron_ref,
        "bron_commit": commit,
        "versiebron": pin.versiebron,
        "mappen": list(pin.mappen),
        "bestanden": nieuw,
    }
    (root / rel).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return lees_pin(root, rel)


def _druk(uit: Uitkomst, stil: bool = False) -> int:
    if not stil:
        for regel in uit.ok:
            print(f"ok   {regel}")
    for regel in uit.waarschuwingen:
        print(f"WAARSCHUWING {regel}")
    for regel in uit.fouten:
        print(f"FOUT {regel}", file=sys.stderr)
    return 1 if uit.fouten else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = ap.add_subparsers(dest="opdracht", required=True)
    sub.add_parser("controleer")
    v = sub.add_parser("vergelijk")
    v.add_argument("--bron", type=Path)
    v.add_argument("--online", action="store_true")
    v.add_argument("--ref", help="branch of commit in de bron (standaard: bron_ref uit de pin)")
    w = sub.add_parser("bijwerken")
    w.add_argument("--soort", choices=sorted(VERSIE), required=True)
    w.add_argument("--bron", type=Path, required=True)
    w.add_argument("--commit", required=True)
    w.add_argument("--ref")
    args = ap.parse_args(argv)
    try:
        if args.opdracht == "controleer":
            return _druk(controleer())
        if args.opdracht == "vergelijk":
            if bool(args.bron) == bool(args.online):
                print("gebruik precies één van --bron <map> of --online", file=sys.stderr)
                return 2
            repos = {lees_pin(REPO_ROOT, p).bron_repo for p in PINNEN}
            if len(repos) != 1:
                print(f"FOUT de pins noemen meer dan één bronrepo: {sorted(repos)}", file=sys.stderr)
                return 1
            lezer = git_lezer(args.bron) if args.bron else online_lezer(repos.pop())
            return _druk(vergelijk(lezer, ref=args.ref))
        pin = bijwerken(REPO_ROOT, args.soort, git_lezer(args.bron), args.commit, args.ref)
        print(f"bijgewerkt: {pin.pad} → {pin.soort} {pin.versie} @ {pin.bron_commit}")
        return _druk(controleer(), stil=True)
    except Fout as exc:
        print(f"FOUT {exc}", file=sys.stderr)
        return 2
    except BronOnbereikbaar as exc:
        print(f"ONBEKEND bron niet bereikbaar — niets vergeleken: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
