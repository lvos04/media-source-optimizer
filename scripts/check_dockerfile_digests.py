"""Controleer de digests van Dockerfile-basisimages."""

import re
import shlex
import subprocess
import sys
from argparse import ArgumentParser
from collections.abc import Iterator
from pathlib import Path

PINNED_IMAGE = re.compile(r"[^@$\s]+@sha256:[0-9a-f]{64}")
PARSER_DIRECTIVE = re.compile(r"#\s*(syntax|escape|check)\s*=\s*(\S.*)", re.IGNORECASE)


def instructions(dockerfile: Path) -> Iterator[tuple[int, str, str]]:
    """Voeg Dockerfile-vervolgregels samen, ook met een aangepaste escape."""
    escape = "\\"
    directives = True
    pending: str | None = None
    first_line = 0
    source = dockerfile.read_bytes().decode("utf-8-sig")
    for number, physical in enumerate(source.split("\n"), 1):
        physical = physical.removesuffix("\r")
        stripped = physical.strip()
        if directives and (directive := PARSER_DIRECTIVE.fullmatch(stripped)):
            if directive[1].lower() == "escape" and directive[2] in {"\\", "`"}:
                escape = directive[2]
            continue
        directives = False
        if not stripped or stripped.startswith("#"):
            continue
        line = physical.rstrip(" \t")
        if pending is None:
            first_line = number
            pending = ""
            line = line.lstrip()
        if line.endswith(escape) and not line.endswith(escape * 2):
            # Docker behoudt de inspringing en voegt zelf geen spatie toe.
            pending += line[:-1]
            continue
        yield first_line, (pending + line).strip(), escape
        pending = None
    if pending and pending.strip():
        yield first_line, pending.strip(), escape


def arg_words(body: str, escape: str) -> Iterator[str]:
    """Splits ARG zoals Docker, met behoud van aanhalingstekens en escapes."""
    quote = ""
    escaped = False
    start = 0
    for index, character in enumerate(body):
        if escaped:
            escaped = False
        elif character == escape and quote != "'":
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character.isspace():
            if start < index:
                yield body[start:index]
            start = index + 1
    if start < len(body):
        yield body[start:]


def check_dockerfile(dockerfile: Path) -> Iterator[str]:
    pinned_args: set[str] = set()
    stages: set[str] = set()
    in_stage = False
    for number, line, escape in instructions(dockerfile):
        instruction, *parts = line.split(maxsplit=1)
        body = parts[0] if parts else ""
        instruction = instruction.upper()
        if instruction == "ARG":
            for argument in arg_words(body, escape):
                name, _, value = argument.partition("=")
                pinned = PINNED_IMAGE.fullmatch(value.strip("\"'")) is not None
                if not in_stage:
                    pinned_args.discard(name)
                    if pinned:
                        pinned_args.add(name)
                if name.endswith("_IMAGE") and not pinned:
                    yield f"{dockerfile}:{number}: ARG {name} mist een geldige @sha256-digest."
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
