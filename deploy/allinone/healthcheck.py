"""Controleer de interne API en de webapp nadat de rechten zijn verlaagd."""

import http.client
import os
import sys

from mso.allinone_privileges import lower_privileges, process_status, runtime_identity


def ok(port: str, path: str) -> bool:
    # Gebruik direct loopback, ook als een proxy in de omgeving staat.
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=6)
        try:
            conn.request("GET", path)
            return conn.getresponse().status == 200
        finally:
            conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        return False


def main(args: list[str]) -> int:
    try:
        lower_privileges(*runtime_identity())
    except (OSError, ValueError) as exc:
        print(f"Healthcheck: rechtenwissel mislukt: {exc}", file=sys.stderr)
        return 1

    if args == ["--controle"]:
        status = process_status()
        for field in ("Uid", "CapEff", "CapPrm", "CapAmb", "CapBnd", "NoNewPrivs"):
            print(f"{field}: {status[field]}")
        return 0
    if args:
        return 1
    api = os.environ.get("MSO_API_PORT", "8200")
    port = os.environ.get("MSO_PORT", "38081")
    return 0 if ok(api, "/healthz") and ok(port, "/login") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
