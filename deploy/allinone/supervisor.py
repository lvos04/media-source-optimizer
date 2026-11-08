"""Media Source Optimizer — all-in-one supervisor (PID 1 child of dumb-init).

One container runs everything: PostgreSQL 16 (unix socket only, no network, no password),
database migrations, the API, the worker and the web UI. Only ONE host folder (/appdata) and ONE
port are needed, so it installs as a normal TrueNAS "Custom App" without YAML.

Robustness rules (each fixes a classic self-hosting failure):
- Runs fine whether the platform starts it as root or as uid 568: as root it fixes ownership of
  /appdata once and then drops to 568 for every process (PostgreSQL refuses to run as root).
- Clear Dutch error + exit when /appdata is not writable, instead of a crash loop.
- A stale postmaster.pid from an unclean stop is removed (we are the only process in a fresh container).
- The database password problem does not exist: PostgreSQL listens on a unix socket in /tmp only.
- Children that crash are restarted with backoff; SIGTERM stops web → api → worker → database cleanly.
- Before pending migrations a ``pre-upgrade`` backup is made (if there is room); a failure never blocks the start.
"""

from __future__ import annotations

import grp
import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

UID = int(os.environ.get("MSO_UID", "568"))
GID = int(os.environ.get("MSO_GID", "568"))
DATA = Path(os.environ.get("MSO_DATA_DIR", "/appdata"))
WORK = Path(os.environ.get("MSO_WORK_DIR", str(DATA / "work")))
PGDATA = DATA / "postgres"
SOCKET_DIR = Path("/tmp/mso-pg")
PORT = os.environ.get("MSO_PORT", "38081")
API_PORT = os.environ.get("MSO_API_PORT", "8200")
DB_URL = f"postgresql://mso@/mso?host={SOCKET_DIR}"
STOP = threading.Event()


def log(msg: str) -> None:
    print(f"[mso] {msg}", flush=True)


def fail(msg: str, code: int = 78) -> None:
    log("FOUT: " + msg)
    # keep the message visible in the TrueNAS app log instead of a fast crash loop
    time.sleep(30)
    sys.exit(code)


# ------------------------------------------------------------------ privileges & folders
def demote() -> None:
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)


def prepare_dirs() -> None:
    if not DATA.exists():
        fail(f"{DATA} bestaat niet. Koppel een host-map (dataset) aan {DATA} in de app-instellingen.")
    root = os.getuid() == 0
    if root:
        # first start or wrong owner: make 568 the owner of everything we manage (never of anything else)
        for path in [
            DATA,
            *[p for p in DATA.iterdir() if p.name in {"postgres", "secrets", "backups", "work", "logs"}],
        ]:
            st = path.stat()
            if (st.st_uid, st.st_gid) != (UID, GID):
                log(f"eigenaar van {path} wordt {UID}:{GID}")
                for dirpath, dirnames, filenames in os.walk(path):
                    for name in [dirpath, *[os.path.join(dirpath, n) for n in dirnames + filenames]]:
                        try:
                            os.lchown(name, UID, GID)
                        except OSError:
                            pass
    for sub in (PGDATA, DATA / "secrets", DATA / "backups", WORK):
        try:
            sub.mkdir(parents=True, exist_ok=True)
            if root:
                os.chown(sub, UID, GID)
        except PermissionError:
            fail(
                f"geen schrijfrechten op {sub}. Geef gebruiker {UID} (apps) schrijfrechten op de dataset, "
                f"of laat de app als root starten zodat MSO dit zelf regelt."
            )
    try:
        os.chmod(PGDATA, 0o700)  # PostgreSQL refuses a group/world-readable data directory
    except PermissionError:
        fail(
            f"{PGDATA} is niet van gebruiker {UID}; start de app als root (MSO regelt dan de rechten) "
            f"of maak {UID} eigenaar van de dataset."
        )
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    if root:
        os.chown(SOCKET_DIR, UID, GID)
    # final writability probe as the runtime user
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            f"open('{DATA}/.mso-write-test','w').close();import os;os.remove('{DATA}/.mso-write-test')",
        ],
        preexec_fn=demote,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        fail(f"gebruiker {UID} kan niet schrijven in {DATA}. Pas de rechten van de dataset aan (Apps-gebruiker).")


# ------------------------------------------------------------------ processes
def env_for(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": "/tmp",
            "MSO_DATA_DIR": str(DATA),
            "MSO_WORK_DIR": str(WORK),
            "MSO_DATABASE_URL": DB_URL,
            "PYTHONUNBUFFERED": "1",
        }
    )
    try:
        env["USER"] = pwd.getpwuid(UID).pw_name
    except KeyError:
        pass
    env.update(extra or {})
    return env


def run_once(name: str, cmd: list[str], extra_env: dict[str, str] | None = None) -> int:
    log(f"{name}: {' '.join(cmd)}")
    return subprocess.run(cmd, env=env_for(extra_env), preexec_fn=demote, check=False).returncode


class Child:
    def __init__(
        self, name: str, cmd: list[str], extra_env: dict[str, str] | None = None, stop_signal: int = signal.SIGTERM
    ) -> None:
        self.name, self.cmd, self.extra_env, self.stop_signal = name, cmd, extra_env, stop_signal
        self.proc: subprocess.Popen[bytes] | None = None
        self.restarts = 0

    def start(self) -> None:
        log(f"start {self.name}")
        self.proc = subprocess.Popen(self.cmd, env=env_for(self.extra_env), preexec_fn=demote)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 25) -> None:
        if not self.alive():
            return
        assert self.proc is not None
        self.proc.send_signal(self.stop_signal)
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            log(f"{self.name} reageert niet; geforceerd stoppen")
            self.proc.kill()
            self.proc.wait(5)


def init_database() -> None:
    if (PGDATA / "PG_VERSION").exists():
        major = (PGDATA / "PG_VERSION").read_text().strip()
        if major != "16":
            fail(f"database in {PGDATA} is PostgreSQL {major}; deze image gebruikt 16. Zie docs/MIGRATION.md.")
        pid = PGDATA / "postmaster.pid"
        if pid.exists():
            log("oude postmaster.pid van een onderbroken stop verwijderd")
            pid.unlink()
        return
    log("eerste start: database aanmaken")
    rc = run_once(
        "initdb",
        [
            "initdb",
            "-D",
            str(PGDATA),
            "-U",
            "mso",
            "--auth-local=trust",
            "--auth-host=reject",
            "--encoding=UTF8",
            "--locale=C.UTF-8",
            "--no-instructions",
        ],
    )
    if rc != 0:
        fail("initdb mislukt (zie log hierboven)")


def postgres_child() -> Child:
    return Child(
        "database",
        [
            "postgres",
            "-D",
            str(PGDATA),
            "-c",
            "listen_addresses=",
            "-c",
            f"unix_socket_directories={SOCKET_DIR}",
            "-c",
            "max_connections=40",
            "-c",
            "shared_buffers=64MB",
            "-c",
            "jit=off",
            "-c",
            "logging_collector=off",
            "-c",
            "log_min_messages=warning",
        ],
        stop_signal=signal.SIGINT,
    )  # SIGINT = fast shutdown


def wait_for_db(pg: Child, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pg.alive():
            fail("database stopte tijdens het opstarten (zie log hierboven)")
        rc = subprocess.run(
            ["pg_isready", "-h", str(SOCKET_DIR), "-U", "mso", "-d", "postgres", "-q"],
            env=env_for(),
            preexec_fn=demote,
            check=False,
        ).returncode
        if rc == 0:
            break
        time.sleep(1)
    else:
        fail("database niet klaar binnen 90 s")
    exists = subprocess.run(
        [
            "psql",
            "-h",
            str(SOCKET_DIR),
            "-U",
            "mso",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='mso'",
        ],
        env=env_for(),
        preexec_fn=demote,
        capture_output=True,
        text=True,
        check=False,
    )
    if exists.stdout.strip() != "1":
        if (
            run_once(
                "createdb", ["psql", "-h", str(SOCKET_DIR), "-U", "mso", "-d", "postgres", "-qc", "CREATE DATABASE mso"]
            )
            != 0
        ):
            fail("database 'mso' aanmaken mislukt")


def main() -> None:
    log(f"Media Source Optimizer (alles-in-één) · data {DATA} · poort {PORT}")
    prepare_dirs()
    demote()  # least privilege: from here on nothing runs as root (only the ownership fix above needed it)
    init_database()
    pg = postgres_child()
    pg.start()
    wait_for_db(pg)
    # safety net for upgrades: a dump before pending migrations (only when there are any and the disk has room).
    # It never blocks the start: on failure the step logs a clear line and MSO starts as usual.
    if run_once("back-up vóór upgrade", [sys.executable, "-m", "mso", "backup", "--pre-upgrade"]) != 0:
        log("back-up vóór upgrade mislukt (zie log hierboven); MSO start gewoon door")
    if run_once("migraties", [sys.executable, "-m", "mso", "migrate", "--no-backup"]) != 0:
        pg.stop()
        fail("databasemigratie mislukt; de vorige versie van de database is ongewijzigd. Zie docs/MIGRATION.md.")

    py = sys.executable
    children = [
        Child(
            "api",
            [py, "-m", "mso", "api", "--host", "127.0.0.1", "--port", API_PORT, "--forwarded-allow-ips", "127.0.0.1"],
        ),
        Child("worker", [py, "-m", "mso", "worker"]),
        Child(
            "web",
            ["node", "/opt/mso-web/server.mjs"],
            {
                "PORT": PORT,
                "MSO_WEB_HOST": "0.0.0.0",
                "MSO_CORE_URL": f"http://127.0.0.1:{API_PORT}",
                "NODE_ENV": "production",
                "NEXT_TELEMETRY_DISABLED": "1",
            },
        ),
    ]
    for c in children:
        c.start()

    def shutdown(signum: int, _frame: object) -> None:
        log(f"signaal {signum}: netjes stoppen")
        STOP.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    backoff: dict[str, float] = {}
    while not STOP.is_set():
        if not pg.alive():
            log("database gestopt; opnieuw starten")
            for c in children:
                c.stop()
            init_database()
            pg.start()
            wait_for_db(pg)
            for c in children:
                c.start()
        for c in children:
            if not c.alive() and not STOP.is_set():
                wait = min(60.0, backoff.get(c.name, 1.0))
                log(f"{c.name} gestopt (code {c.proc.returncode if c.proc else '?'}); herstart over {wait:.0f} s")
                STOP.wait(wait)
                backoff[c.name] = wait * 2
                if not STOP.is_set():
                    c.start()
            elif c.alive():
                backoff[c.name] = max(1.0, backoff.get(c.name, 1.0) * 0.9)
        STOP.wait(2)

    for c in reversed(children):  # web → worker → api
        c.stop()
    pg.stop(60)
    log("gestopt")


if __name__ == "__main__":
    # sanity: the runtime user must exist for postgres
    try:
        pwd.getpwuid(UID)
        grp.getgrgid(GID)
    except KeyError:
        fail(f"gebruiker {UID}:{GID} bestaat niet in de image")
    if shutil.which("postgres") is None:
        fail("postgres ontbreekt in de image")
    main()
