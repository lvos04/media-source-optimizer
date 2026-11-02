"""Generate ``results/compose.mocks.yaml``: one container per mock registered in msomocks.REGISTRY.

New mocks added by other contributors are picked up automatically (no edit here needed). Each mock
listens on port 8080 inside its container and is reachable from api/worker as ``http://mock-<name>:8080``
(or ``mock-<name>:8080`` for TCP mocks such as NNTP). Extra per-mock arguments (fault injection) can be
given via MSO_STACK_MOCK_ARGS_<NAME>="--fault key=value".
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "apps" / "mocks"))

from msomocks.__main__ import REGISTRY


def main() -> None:
    image = os.environ.get("MSO_MOCKS_IMAGE", "mso-mocks:local")
    services = {}
    for name in sorted(REGISTRY):
        extra = os.environ.get(f"MSO_STACK_MOCK_ARGS_{name.upper()}", "").split()
        services[f"mock-{name}"] = {
            "image": image,
            "command": [name, "--port", "8080", *extra],
            "restart": "unless-stopped",
            "read_only": True,
            "user": "568:568",
            "security_opt": ["no-new-privileges:true"],
            "cap_drop": ["ALL"],
            "tmpfs": ["/tmp"],
            "networks": ["app"],
            "mem_limit": "256m",
            "pids_limit": 64,
        }
    out = HERE / "results" / "compose.mocks.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML; an empty mapping keeps compose happy when no mocks exist yet
    out.write_text(json.dumps({"services": services} if services else {}, indent=2) + "\n")
    print(" ".join(services) or "(geen mocks geregistreerd)")


if __name__ == "__main__":
    main()
