"""Media Source Optimizer — alles-in-één-bewaker als PID 1.

One container runs everything: PostgreSQL 16 (unix socket only, no network, no password),
database migrations, the API, the worker and the web UI. Only ONE host folder (/appdata) and ONE
port are needed, so it installs as a normal TrueNAS "Custom App" without YAML.

Robustness rules (each fixes a classic self-hosting failure):
- Runs fine whether the platform starts it as root or as uid 568: as root it fixes ownership of
  /appdata once and then drops to 568 for every process (PostgreSQL refuses to run as root).
- Clear Dutch error + exit when /appdata is not writable, instead of a crash loop.
- A stale postmaster.pid from an unclean stop is removed (we are the only process in a fresh container).
- The database password problem does not exist: PostgreSQL listens on a unix socket in /tmp only.
- Bij uitval volgt herstart met uitstel. SIGTERM stopt web → worker → API → database.
- Before pending migrations a ``pre-upgrade`` backup is made (if there is room); a failure never blocks the start.
- Een nieuwer databaseschema houdt de gewone diensten uit; de bewaker wacht zonder herstart tot de app stopt.
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
from contextlib import ExitStack
from pathlib import Path

from mso.allinone_privileges import lower_privileges, runtime_identity
from mso.db import NEWER_DATABASE_EXIT_CODE, Database, NewerDatabaseError, check_schema_compatibility

UID, GID = 568, 568
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


class StartupError(Exception):
    """De bewaker kan niet veilig verder."""


def fail(msg: str, *, pause: bool = False) -> None:
    log("FOUT: " + msg)
    if pause:
        # Houd de melding zichtbaar zonder een snelle herstartlus.
        time.sleep(30)
    sys.exit(78)


# ------------------------------------------------------------------ privileges & folders
def demote() -> None:
    try:
        lower_privileges(UID, GID)
    except (OSError, ValueError) as exc:
        log(f"FOUT: rechtenwissel naar {UID}:{GID} mislukt: {exc}")
        raise


def secure_supervisor() -> None:
    try:
        demote()
    except (OSError, ValueError) as exc:
        raise StartupError("bewaker stopte omdat de rechtenwissel mislukte.") from exc


def prepare_dirs(*, database_only: bool = False) -> None:
    if not DATA.exists():
        raise StartupError(f"{DATA} bestaat niet. Koppel een host-map (dataset) aan {DATA} in de app-instellingen.")
    root = os.getuid() == 0
    managed = {"postgres"} if database_only else {"postgres", "secrets", "backups", "work", "logs"}
    if root:
        # first start or wrong owner: make 568 the owner of everything we manage (never of anything else)
        for path in [
            DATA,
            *[p for p in DATA.iterdir() if p.name in managed],
        ]:
            st = path.stat()
            if database_only and path == DATA:
                shift = 6 if st.st_uid == UID else 3 if st.st_gid == GID else 0
                if (st.st_mode >> shift) & 1:
                    continue  # De database heeft alleen doorgang door deze bovenmap nodig.
            if (st.st_uid, st.st_gid) != (UID, GID):
                log(f"eigenaar van {path} wordt {UID}:{GID}")
                if path == DATA:
                    os.chown(DATA, UID, GID)
                    continue
                for dirpath, dirnames, filenames in os.walk(path):
                    for name in [dirpath, *[os.path.join(dirpath, n) for n in dirnames + filenames]]:
                        try:
                            os.lchown(name, UID, GID)
                        except OSError:
                            pass
    folders = (PGDATA,) if database_only else (PGDATA, DATA / "secrets", DATA / "backups", WORK)
    for sub in folders:
        try:
            sub.mkdir(parents=True, exist_ok=True)
            if root and (sub.stat().st_uid, sub.stat().st_gid) != (UID, GID):
                os.chown(sub, UID, GID)
        except PermissionError as exc:
            raise StartupError(
                f"geen schrijfrechten op {sub}. Geef gebruiker {UID} (apps) schrijfrechten op de dataset, "
                f"of laat de app als root starten zodat MSO dit zelf regelt."
            ) from exc
    try:
        if PGDATA.stat().st_mode & 0o777 != 0o700:
            os.chmod(PGDATA, 0o700)  # PostgreSQL weigert te ruime databaserechten
    except PermissionError as exc:
        raise StartupError(
            f"{PGDATA} is niet van gebruiker {UID}; start de app als root (MSO regelt dan de rechten) "
            f"of maak {UID} eigenaar van de dataset."
        ) from exc
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    if root and (SOCKET_DIR.stat().st_uid, SOCKET_DIR.stat().st_gid) != (UID, GID):
        os.chown(SOCKET_DIR, UID, GID)
    if database_only:
        return
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
        raise StartupError(
            f"gebruiker {UID} kan niet schrijven in {DATA}. Pas de rechten van de dataset aan (Apps-gebruiker)."
        )


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
            raise StartupError(
                f"database in {PGDATA} is PostgreSQL {major}; deze image gebruikt 16. Zie docs/MIGRATION.md."
            )
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
        raise StartupError("initdb mislukt (zie log hierboven)")


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
            raise StartupError("database stopte tijdens het opstarten (zie log hierboven)")
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
        raise StartupError("database niet klaar binnen 90 s")
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
            raise StartupError("database 'mso' aanmaken mislukt")


def wait_on_newer_schema(pg: Child, children: list[Child] | None = None) -> None:
    """Proces blijft leven tot een stopverzoek; gewone gereedheid blijft onwaar."""
    for child in reversed(children or []):
        child.stop()
    pg.stop(60)
    log(f"FOUT: {NewerDatabaseError()}; API en worker blijven uit; wacht op stoppen van de app.")
    while not STOP.wait(2):
        reap_children([])


def reap_children(children: list[Child]) -> None:
    """Ruim als PID 1 ook geadopteerde wezen op en behoud de exitcodes van beheerde kinderen."""
    if os.getpid() != 1:
        return
    managed = {
        child.proc.pid: child.proc
        for child in children
        if child.proc is not None and child.proc.returncode is None
    }
    while True:
        try:
            exited = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return
        if exited is None:
            return
        process = managed.pop(exited.si_pid, None)
        if process is not None:
            process.poll()
        else:
            os.waitpid(exited.si_pid, 0)


def prepare_database(cleanup: ExitStack) -> tuple[Child, bool]:
    prepare_dirs(database_only=True)
    init_database()
    pg = postgres_child()
    cleanup.callback(pg.stop, 60)
    pg.start()
    wait_for_db(pg)
    newer_schema = False
    try:
        db = Database(DB_URL, max_size=1)
        try:
            check_schema_compatibility(db)
        finally:
            db.close()
    except NewerDatabaseError:
        newer_schema = True
    except Exception as exc:
        raise StartupError(
            "databaseschemacontrole mislukt; zie het databaselog en probeer de app opnieuw te starten."
        ) from exc
    if not newer_schema:
        prepare_dirs()  # Pas na de alleen-lezen schemacontrole applicatiemappen voorbereiden.
    return pg, newer_schema


def run_services(pg: Child, cleanup: ExitStack) -> None:
    # safety net for upgrades: a dump before pending migrations (only when there are any and the disk has room).
    # Een dumpfout blokkeert niet; een database van een nieuwere versie wel.
    rc = run_once("back-up vóór upgrade", [sys.executable, "-m", "mso", "backup", "--pre-upgrade"])
    if rc == NEWER_DATABASE_EXIT_CODE:
        wait_on_newer_schema(pg)
        return
    if rc != 0:
        log("back-up vóór upgrade mislukt (zie log hierboven); MSO start gewoon door")
    rc = run_once("migraties", [sys.executable, "-m", "mso", "migrate", "--no-backup"])
    if rc == NEWER_DATABASE_EXIT_CODE:
        wait_on_newer_schema(pg)
        return
    if rc != 0:
        raise StartupError(
            "databasemigratie mislukt; de vorige versie van de database is ongewijzigd. Zie docs/MIGRATION.md."
        )

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
        cleanup.callback(c.stop)
        c.start()

    backoff: dict[str, float] = {}
    while not STOP.is_set():
        reap_children([pg, *children])
        if any(c.proc is not None and c.proc.poll() == NEWER_DATABASE_EXIT_CODE for c in children):
            wait_on_newer_schema(pg, children)
            return
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
                if c.proc is not None and c.proc.returncode == NEWER_DATABASE_EXIT_CODE:
                    wait_on_newer_schema(pg, children)
                    return
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


def main() -> None:
    def shutdown(signum: int, _frame: object) -> None:
        log(f"signaal {signum}: netjes stoppen")
        STOP.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    root_start = os.geteuid() == 0
    safe_to_pause = False
    try:
        with ExitStack() as cleanup:
            if not root_start:
                secure_supervisor()
                safe_to_pause = True
            log(f"Media Source Optimizer (alles-in-één) · data {DATA} · poort {PORT}")
            pg, newer_schema = prepare_database(cleanup)
            if root_start:
                secure_supervisor()
                safe_to_pause = True
            if newer_schema:
                wait_on_newer_schema(pg)
            else:
                run_services(pg, cleanup)
            cleanup.pop_all()  # De normale stop en schemaweigering hebben hun eigen stopvolgorde.
    except StartupError as exc:
        fail(str(exc), pause=safe_to_pause)
    except (OSError, subprocess.SubprocessError):
        fail("een start- of stopopdracht is mislukt; zie het app-log.", pause=safe_to_pause)


if __name__ == "__main__":
    try:
        UID, GID = runtime_identity()
    except ValueError as exc:
        fail(str(exc))
    # sanity: the runtime user must exist for postgres
    try:
        pwd.getpwuid(UID)
        grp.getgrgid(GID)
    except KeyError:
        fail(f"gebruiker {UID}:{GID} bestaat niet in de image")
    if shutil.which("postgres") is None:
        fail("postgres ontbreekt in de image")
    main()
