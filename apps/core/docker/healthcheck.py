"""Container healthcheck: ``mso-healthcheck [api|worker]`` (default: detected from PID 1's command line).

api    → GET http://127.0.0.1:${MSO_API_PORT:-8200}/healthz must answer 200 (it pings the database).
worker → the worker heartbeat in ``system_state`` must be younger than 90 s.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request


def role() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        with open("/proc/1/cmdline", "rb") as fh:
            parts = fh.read().split(b"\0")
    except OSError:
        return "api"
    return "worker" if b"worker" in parts else "api"


def check_api() -> int:
    url = f"http://127.0.0.1:{os.environ.get('MSO_API_PORT', '8200')}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:  # noqa: S310 (fixed loopback URL)
            return 0 if resp.status == 200 else 1
    except Exception:  # noqa: BLE001
        return 1


def check_worker() -> int:
    # reuse the entrypoint's URL assembly (password file) without printing anything secret
    env = dict(os.environ)
    code = ("import os,psycopg,sys;"
            "c=psycopg.connect(os.environ['MSO_DATABASE_URL'],connect_timeout=3);"
            "r=c.execute(\"SELECT extract(epoch FROM now()-updated_at) FROM system_state "
            "WHERE key='worker_heartbeat'\").fetchone();"
            "sys.exit(0 if r and r[0] < 90 else 1)")
    proc = subprocess.run(["/usr/local/bin/mso-entrypoint", "python", "-c", code], env=env,  # noqa: S603
                          capture_output=True, timeout=10, check=False)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(check_worker() if role() == "worker" else check_api())
