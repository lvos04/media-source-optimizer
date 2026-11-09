"""End-to-end test of the all-in-one image (deploy/allinone) the way TrueNAS runs a Custom App.

    python tests/stack/allinone_test.py [image]        (default image: media-source-optimizer:local)

Scenarios (each prints OK/FAIL, report → tests/stack/results/allinone-report.md):
 1  start as root on an EMPTY host folder → ownership fixed, healthy, all processes run as 568
 2  first-run setup, login, CSRF, connector to a mock SABnzbd on the same network + connection test
 3  backup via worker job + restore (pg_dump/pg_restore over the unix socket)
 4  docker restart → data and login persist
 5  docker kill (SIGKILL, unclean) → start again → stale postmaster.pid handled, data intact
 6  graceful stop finishes well within TrueNAS' stop timeout
 7  start as --user 568 on a 568-owned folder → healthy
 8  start as --user 568 on a root-owned folder → clear Dutch error in the log (no crash loop spam)
 9  no secret (admin password, connector key) in the container log
10  upgrade: database back to the 1.5.0 schema (migrate --down 501), restart → a ``pre-upgrade`` backup is made
    before the migrations, every migration is applied again, data and login intact
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

IMAGE = sys.argv[1] if len(sys.argv) > 1 else "media-source-optimizer:local"
MOCKS = os.environ.get("MSO_MOCKS_IMAGE", "mso-mocks:stack")
NAME, NET, PORT = "mso-aio-test", "mso-aio-net", 48091
BASE = f"http://127.0.0.1:{PORT}"
ADMIN, ADMIN_PW = "beheerder", "Aio-" + secrets.token_urlsafe(16)
SAB_KEY = "sab" + secrets.token_hex(16)
RESULTS: list[tuple[str, bool, str]] = []


def sh(*cmd: str, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()[:400]}")
    return r.stdout.strip()


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'OK  ' if ok else 'FAIL'}] {name} — {detail}", flush=True)


def wait_healthy(name: str = NAME, timeout: float = 180) -> str:
    deadline = time.monotonic() + timeout
    status = "?"
    while time.monotonic() < deadline:
        status = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", name, check=False) or "?"
        running = sh("docker", "inspect", "-f", "{{.State.Running}}", name, check=False)
        if status == "healthy" or running == "false":
            return status if running != "false" else "exited"
        time.sleep(3)
    return status


def run(data: Path, *extra: str, name: str = NAME) -> None:
    sh("docker", "rm", "-f", name, check=False)
    sh(
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        NET,
        "-p",
        f"127.0.0.1:{PORT}:38081",
        "-v",
        f"{data}:/appdata",
        *extra,
        IMAGE,
    )


def login() -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/v1/auth/login", json={"username": ADMIN, "password": ADMIN_PW})
    r.raise_for_status()
    return c, r.json()["csrf_token"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mso-aio-"))
    data = tmp / "appdata"
    data.mkdir()
    os.chmod(data, 0o755)
    sh("docker", "network", "create", NET, check=False)
    sh("docker", "rm", "-f", "mock-sabnzbd-aio", check=False)
    sh(
        "docker",
        "run",
        "-d",
        "--name",
        "mock-sabnzbd-aio",
        "--network",
        NET,
        "--network-alias",
        "sabnzbd",
        MOCKS,
        "sabnzbd",
        "--port",
        "8080",
        "--fault",
        f"api_key={SAB_KEY}",
    )
    try:
        # 1
        run(data)
        st = wait_healthy()
        owners = sh(
            "docker",
            "run",
            "--rm",
            "-v",
            f"{data}:/d",
            "--entrypoint",
            "stat",
            IMAGE,
            "-c",
            "%u:%g %n",
            "/d/postgres",
            "/d/secrets",
            "/d/backups",
        )
        procs = sh(
            "docker",
            "exec",
            NAME,
            "python",
            "-c",
            "import os;me=str(os.getpid());print(sorted({os.stat('/proc/'+p).st_uid for p in os.listdir('/proc') "
            "if p.isdigit() and p not in ('1', me) and open('/proc/'+p+'/cmdline','rb').read()}))",
        )
        record(
            "1 root + lege map → gezond, eigenaar 568, processen als 568",
            st == "healthy" and all(line.startswith("568:568") for line in owners.splitlines()) and procs == "[568]",
            f"health={st}; {owners.replace(chr(10), ', ')}; proces-uids (excl. PID 1)={procs}",
        )

        # 2
        c = httpx.Client(base_url=BASE, timeout=30)
        assert c.get("/api/v1/setup/status").json()["setup_required"] is True
        r = c.post("/api/v1/setup/admin", json={"username": ADMIN, "password": ADMIN_PW})
        csrf = r.json()["csrf_token"]
        no_csrf = c.post("/api/v1/system/pause").status_code
        put = c.put(
            "/api/v1/connectors/sabnzbd",
            headers={"X-CSRF-Token": csrf},
            json={"url": "http://sabnzbd:8080", "enabled": True, "secrets": {"api_key": SAB_KEY}},
        )
        test = c.post("/api/v1/connectors/sabnzbd/test", headers={"X-CSRF-Token": csrf}).json()
        done = c.post("/api/v1/setup/complete", headers={"X-CSRF-Token": csrf}, json={"mode": "read_only"})
        record(
            "2 setup, login, CSRF, koppeling + verbindingstest",
            r.status_code == 200
            and no_csrf == 403
            and put.status_code == 200
            and test.get("ok") is True
            and done.status_code == 200
            and SAB_KEY not in put.text,
            f"admin {r.status_code}; zonder CSRF {no_csrf}; SABnzbd-test {test.get('status')} v{test.get('version')}",
        )

        # 3
        job = c.post("/api/v1/backups", headers={"X-CSRF-Token": csrf}, json={"include_secrets": False}).json()
        status = "?"
        for _ in range(40):
            status = c.get(f"/api/v1/jobs/{job['job_id']}").json()["status"]
            if status in ("done", "failed"):
                break
            time.sleep(1)
        items = c.get("/api/v1/backups").json()["items"]
        restored = None
        if items:
            b = items[0]
            restored = c.post(
                f"/api/v1/backups/{b['id']}/restore", headers={"X-CSRF-Token": csrf}, json={"confirm": b["file"]}
            )
        c, csrf = login()  # restore invalidates sessions
        still = c.get("/api/v1/connectors").json()
        sab = next(x for x in still if x["id"] == "sabnzbd")
        record(
            "3 back-up via worker + herstel",
            status == "done"
            and restored is not None
            and restored.status_code == 200
            and sab["secret_fields"][0]["status"] == "ingesteld",
            f"job {status}; {len(items)} back-up(s); herstel {restored.status_code if restored else '-'}; "
            f"sleutel na herstel: {sab['secret_fields'][0]['status']}",
        )

        # 4
        sh("docker", "restart", NAME)
        st = wait_healthy()
        c, csrf = login()
        n_users = len(c.get("/api/v1/users").json()["items"])
        record("4 docker restart → data en login blijven", st == "healthy" and n_users == 1, f"health={st}")

        # 10 (upgrade from the previous release's schema)
        latest = c.get("/api/v1/system/info").json()["schema_version"]
        before_kinds = [b.get("kind") for b in c.get("/api/v1/backups").json()["items"]]
        down = subprocess.run(["docker", "exec", "-u", "568", NAME, "mso", "migrate", "--down", "501"],
                              capture_output=True, text=True, check=False, timeout=180)
        sh("docker", "restart", NAME)
        st = wait_healthy()
        c, csrf = login()
        info10 = c.get("/api/v1/system/info").json()
        kinds = [b.get("kind") for b in c.get("/api/v1/backups").json()["items"]]
        n_users = len(c.get("/api/v1/users").json()["items"])
        log10 = sh("docker", "logs", NAME, check=False)
        record(
            "10 upgrade vanaf 1.5.0-schema → eerst back-up, dan migraties",
            down.returncode == 0 and st == "healthy" and info10["schema_version"] == latest
            and kinds.count("pre-upgrade") == before_kinds.count("pre-upgrade") + 1 and n_users == 1,
            f"down rc={down.returncode}; schema {info10['schema_version']}/{latest}; pre-upgrade back-ups "
            f"{before_kinds.count('pre-upgrade')}→{kinds.count('pre-upgrade')}; "
            f"melding={'back-up vóór upgrade' in log10}; health={st}",
        )

        # 5
        sh("docker", "kill", "-s", "KILL", NAME)
        stale = (data / "postgres" / "postmaster.pid").exists()
        sh("docker", "start", NAME)
        st = wait_healthy()
        c, csrf = login()
        mode = c.get("/api/v1/system/mode").json()["mode"]
        logs = sh("docker", "logs", NAME, check=False) + sh("docker", "logs", NAME, check=False)
        record(
            "5 harde kill (SIGKILL) → opnieuw starten werkt",
            st == "healthy" and mode == "read_only",
            f"postmaster.pid achtergebleven={stale}; health={st}; melding={'oude postmaster.pid' in logs}",
        )

        # 6
        t0 = time.monotonic()
        sh("docker", "stop", "-t", "60", NAME)
        took = time.monotonic() - t0
        code = sh("docker", "inspect", "-f", "{{.State.ExitCode}}", NAME)
        record("6 netjes stoppen", took < 30, f"{took:.1f} s, exitcode {code}")

        # 9 (before the container is removed)
        all_logs = subprocess.run(["docker", "logs", NAME], capture_output=True, text=True, check=False)
        text = all_logs.stdout + all_logs.stderr
        record(
            "9 geen geheimen in de log",
            ADMIN_PW not in text and SAB_KEY not in text,
            f"{len(text)} bytes log gecontroleerd",
        )

        # 7
        d2 = tmp / "appdata568"
        d2.mkdir()
        os.chown(d2, 568, 568)
        run(d2, "--user", "568:568")
        st = wait_healthy()
        record("7 --user 568 op map van 568 → gezond", st == "healthy", f"health={st}")

        # 8
        d3 = tmp / "appdata-root"
        d3.mkdir()
        os.chmod(d3, 0o755)
        run(d3, "--user", "568:568")
        time.sleep(8)
        log8 = subprocess.run(["docker", "logs", NAME], capture_output=True, text=True, check=False)
        msg = [ln for ln in (log8.stdout + log8.stderr).splitlines() if "FOUT" in ln]
        record("8 --user 568 op root-map → duidelijke foutmelding", bool(msg), msg[0] if msg else "geen melding")
    finally:
        sh("docker", "rm", "-f", NAME, "mock-sabnzbd-aio", check=False)
        sh("docker", "network", "rm", NET, check=False)
        sh("docker", "run", "--rm", "-v", f"{tmp}:/t", "--entrypoint", "sh", IMAGE, "-c", "rm -rf /t/*", check=False)

    ok = all(r[1] for r in RESULTS)
    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    lines = [
        "# All-in-one image — testrapport",
        "",
        f"Image: `{IMAGE}` · {len(RESULTS)} scenario's · {sum(r[1] for r in RESULTS)} geslaagd",
        "",
        "| Scenario | Resultaat | Details |",
        "|---|---|---|",
    ]
    lines += [f"| {n} | {'ok' if o else 'MISLUKT'} | {d.replace('|', '/')} |" for n, o, d in RESULTS]
    (out / "allinone-report.md").write_text("\n".join(lines) + "\n")
    (out / "allinone-report.json").write_text(
        json.dumps([{"scenario": n, "ok": o, "detail": d} for n, o, d in RESULTS], indent=2, ensure_ascii=False)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
