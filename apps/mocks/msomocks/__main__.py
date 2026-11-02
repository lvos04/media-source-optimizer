"""Run a mock service: ``python -m msomocks <name> --port N [--fault key=value ...]``.

Mocks are test doubles that follow the real public APIs (Prowlarr v1, SABnzbd api, qBittorrent WebAPI v2,
MediaGuard core /api/v1, Newznab/Torznab, NNTP). They are used by unit tests and by the container test
stack (tests/stack). They are never part of the production stack.
"""

from __future__ import annotations

import argparse
import importlib

# name -> module path (each module exposes create_app(**faults) or, for TCP mocks, serve(host, port, **faults))
REGISTRY: dict[str, str] = {
}


def main() -> None:
    p = argparse.ArgumentParser(prog="msomocks")
    p.add_argument("name", choices=sorted(REGISTRY))
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--fault", action="append", default=[], help="key=value fault injection")
    args = p.parse_args()
    faults = dict(f.split("=", 1) for f in args.fault)
    mod = importlib.import_module(REGISTRY[args.name])
    if hasattr(mod, "serve"):
        mod.serve(args.host, args.port, **faults)
        return
    import uvicorn
    uvicorn.run(mod.create_app(**faults), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
