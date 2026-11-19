"""Controleer de digests van Dockerfile-basisimages."""

import re
import shlex
import subprocess
import sys
from argparse import ArgumentParser
from collections.abc import Iterator
from pathlib import Path

PINNED_IMAGE = re.compile(r"[^@$\s]+@sha256:[0-9a-f]{64}")


def instructions(dockerfile: Path) -> Iterator[tuple[int, str]]:
    """Voeg Dockerfile-vervolgregels samen, ook met een aangepaste escape."""
    escape = "\\"
    pending = ""
    first_line = 0
    for number, line in enumerate(dockerfile.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            directive = re.fullmatch(r"#\s*escape\s*=\s*([`\\])", line, re.IGNORECASE)
            if first_line == 0 and directive:
                escape = directive[1]
            continue
        if not pending:
            first_line = number
        if line.endswith(escape):
            pending += line[:-1] + " "
            continue
        yield first_line, pending + line
        pending = ""
    if pending:
        yield first_line, pending.rstrip()


def check_dockerfile(dockerfile: Path) -> Iterator[str]:
    pinned_args: set[str] = set()
    stages: set[str] = set()
    in_stage = False
    for number, line in instructions(dockerfile):
        instruction, *parts = line.split(maxsplit=1)
        body = parts[0] if parts else ""
        instruction = instruction.upper()
        if instruction == "ARG":
            name, _, value = body.partition("=")
            name = name.strip()
            pinned = PINNED_IMAGE.fullmatch(value.strip().strip("\"'")) is not None
            if not in_stage:
                pinned_args.discard(name)
                if pinned:
                    pinned_args.add(name)
            if name.endswith("_IMAGE") and not pinned:
                yield f"{dockerfile}:{number}: {line} mist een geldige @sha256-digest."
        if instruction != "FROM":
            continue

        in_stage = True
        tokens = shlex.split(body)
        if tokens and tokens[0].startswith("--platform="):
            tokens.pop(0)
        image = tokens[0] if tokens else ""
        variable = re.fullmatch(r"\$(?:\{([A-Za-z_][A-Za-z_0-9]*)\}|([A-Za-z_][A-Za-z_0-9]*))", image)
        pinned_arg = variable is not None and (variable[1] or variable[2]) in pinned_args
        if not (PINNED_IMAGE.fullmatch(image) or pinned_arg or image.lower() in stages or image == "scratch"):
            yield f"{dockerfile}:{number}: {line} mist een geldige @sha256-digest."
        if len(tokens) == 3 and tokens[1].upper() == "AS":
            stages.add(tokens[2].lower())


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("dockerfiles", type=Path, nargs="*", help="standaard: alle Dockerfiles in Git")
    dockerfiles = parser.parse_args().dockerfiles
    if not dockerfiles:
        files = subprocess.check_output(["git", "ls-files", "-z", "--", "*Dockerfile*"], text=True)
        for filename in filter(None, files.split("\0")):
            path = Path(filename)
            if path.suffix != ".dockerignore" and (
                path.name == "Dockerfile" or path.name.startswith("Dockerfile.") or path.suffix == ".Dockerfile"
            ):
                dockerfiles.append(path)
    if not dockerfiles:
        parser.error("geen Dockerfiles gevonden")

    errors = [error for dockerfile in dockerfiles for error in check_dockerfile(dockerfile)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Basisimage-digests gecontroleerd in {len(dockerfiles)} Dockerfiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
