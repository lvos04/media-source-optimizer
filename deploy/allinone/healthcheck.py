"""All-in-one healthcheck: the web UI answers AND the API reports a reachable database."""

import os
import sys
import urllib.request


def ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:  # noqa: S310 (fixed localhost URLs)
            return resp.status == 200
    except Exception:
        return False


port = os.environ.get("MSO_PORT", "38081")
api = os.environ.get("MSO_API_PORT", "8200")
sys.exit(0 if ok(f"http://127.0.0.1:{api}/healthz") and ok(f"http://127.0.0.1:{port}/login") else 1)
