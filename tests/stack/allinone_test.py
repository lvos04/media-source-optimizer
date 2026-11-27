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
11  bouwgegevens uit de alles-in-één-image
12  readiness en connectorcontract via de publieke webpoort
13  rechten van de PID 1-boom en de aparte healthcheck bij beide startmodi
14  healthcheck weigert een onmogelijke rechtenwissel vóór HTTP
15  rechtenproef weigert een healthcheck met overgeslagen wissel of extra capabilities
16  PID 1 ruimt een overgenomen zombie op
17  upgrade van de gepubliceerde 1.5.0-image met gegevens en pre-upgrade-archief
18  gedeeltelijk herstel uit archief en volledige terugval met gestopte APPDATA op 1.5.0
19  gepinde S-P3-205-image weigert het inmiddels nieuwere schema zonder gegevenswijziging
20  upgrade van de gepubliceerde 1.6.0-image met gegevens en pre-upgrade-archief
21  gedeeltelijk archiefherstel en volledige APPDATA-terugval met 1.6.0
22  upgrade van de gepubliceerde 1.3.0-image met gegevens en pre-upgrade-archief
23  gedeeltelijk archiefherstel en volledige APPDATA-terugval met 1.3.0
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

import httpx

IMAGE = sys.argv[1] if len(sys.argv) > 1 else "media-source-optimizer:local"
MOCKS = os.environ.get("MSO_MOCKS_IMAGE", "mso-mocks:stack")
DATA_VOLUME = os.environ.get("MSO_AIO_DATA_VOLUME")
PUBLISHED_130 = ("ghcr.io/lvos04/media-source-optimizer"
                 "@sha256:df147ab09d112116f08920bef8c0c3e07c0b4eb7656917d95919574691d960be")
PUBLISHED_150 = ("ghcr.io/lvos04/media-source-optimizer"
                 "@sha256:7ee5cb5e3c37d8ca6e3c8445cd6ef24f8b175e2f3167ccd40541737564866be2")
PUBLISHED_160 = ("ghcr.io/lvos04/media-source-optimizer"
                 "@sha256:9bcdec69f7af0bc1747c42326d65f70eda28e6d00d2559d884e00a58ebfbf3da")
GUARD_REVISION = "2512cd761ad30f42a4787935687388e9a3f0d428"
UPGRADE_150 = (
    "17 upgrade vanaf de gepubliceerde 1.5.0-image",
    "18 gedeeltelijk archiefherstel en volledige APPDATA-terugval met 1.5.0",
)
SCHEMA_GUARD = "19 oudere image met schemabewaking weigert nieuwer schema"
UPGRADE_160 = (
    "20 upgrade vanaf de gepubliceerde 1.6.0-image",
    "21 gedeeltelijk archiefherstel en volledige APPDATA-terugval met 1.6.0",
)
UPGRADE_130 = (
    "22 upgrade vanaf de gepubliceerde 1.3.0-image",
    "23 gedeeltelijk archiefherstel en volledige APPDATA-terugval met 1.3.0",
)
SCENARIO_COUNT = 17 + len(UPGRADE_150) + len(UPGRADE_160) + len(UPGRADE_130)
# CI geeft een vaste naam per job mee, zodat de afsluitende stap ook na afbreken precies deze containers opruimt.
NAME = os.environ.get("MSO_AIO_NAME") or f"mso-aio-{secrets.token_hex(6)}"
NET, MOCK_NAME = f"{NAME}-net", f"{NAME}-sab"
PORT = int(os.environ.get("MSO_AIO_TEST_PORT", "48091"))
BASE = f"http://127.0.0.1:{PORT}"
ADMIN, ADMIN_PW = "beheerder", "Aio-" + secrets.token_urlsafe(16)
SAB_KEY = "sab" + secrets.token_hex(16)
RESULTS: list[tuple[str, bool | None, str]] = []
HEALTHCHECK = ["python", "/usr/local/lib/mso/healthcheck.py"]
ZERO_CAPS = "0000000000000000"
PROCESS_STATUS = """
import json
from pathlib import Path

processes = {}
for entry in Path("/proc").iterdir():
    if entry.name.isdecimal():
        try:
            processes[entry.name] = (entry / "status").read_text()
        except (FileNotFoundError, ProcessLookupError):
            pass  # Het proces is tussen de twee procfs-metingen afgelopen.
print(json.dumps(processes))
"""


def sh(*cmd: str, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        detail = r.stderr.strip() or "geen foutmelding"
        detail = detail.replace(ADMIN_PW, "[afgeschermd]").replace(SAB_KEY, "[afgeschermd]")
        if cmd[:2] != ("docker", "run"):
            detail = detail.splitlines()[-1][:400]
        raise RuntimeError(f"{' '.join(cmd[:2])} exitcode {r.returncode}: {detail}")
    return r.stdout.strip()


def make_data_dir(path: Path, *, mode: int | None = None) -> None:
    if DATA_VOLUME:
        permissions = 0o755 if mode is None else mode
        sh("docker", "run", "--rm", "--pull=never", "--network", "none", "--user", "0:0",
           "--mount", f"type=volume,src={DATA_VOLUME},dst=/fixture", "--entrypoint", "mkdir",
           IMAGE, "-m", format(permissions, "o"), f"/fixture/{path}")
    else:
        path.mkdir()
        if mode is not None:
            path.chmod(mode)


def data_mount(path: Path, target: str, *, readonly: bool = False) -> list[str]:
    if DATA_VOLUME:
        mount = f"type=volume,src={DATA_VOLUME},dst={target},volume-subpath={path.as_posix()},volume-nocopy"
        return ["--mount", mount + (",readonly" if readonly else "")]
    return ["-v", f"{path}:{target}" + (":ro" if readonly else "")]


def record(name: str, ok: bool | None, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    status = {True: "OK  ", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{status}] {name} — {detail}", flush=True)


def wait_healthy(name: str = NAME, timeout: float = 180) -> str:
    global BASE
    deadline = time.monotonic() + timeout
    status = "?"
    while time.monotonic() < deadline:
        status = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", name, check=False) or "?"
        running = sh("docker", "inspect", "-f", "{{.State.Running}}", name, check=False)
        if status == "healthy" or running == "false":
            if status == "healthy" and PORT == 0 and name == NAME:
                published = sh("docker", "port", name, "38081/tcp")
                BASE = f"http://localhost:{int(published.rsplit(':', 1)[1])}"
            return status if running != "false" else "exited"
        time.sleep(3)
    return status


def run(data: Path, *extra: str, name: str = NAME, image: str = IMAGE) -> None:
    sh("docker", "rm", "-f", name, check=False)
    sh(
        "docker",
        "run",
        "-d",
        "--pull=never",
        "--name",
        name,
        "--network",
        NET,
        "-p",
        f"127.0.0.1:{PORT}:38081",
        *data_mount(data, "/appdata"),
        *extra,
        image,
    )


def parse_status(text: str) -> dict[str, str]:
    return {key: value.strip() for key, _, value in (line.partition(":") for line in text.splitlines())}


def privilege_errors(status: dict[str, str], bounding: str) -> list[str]:
    expected = {
        "Uid": "568 568 568 568",
        "CapEff": ZERO_CAPS,
        "CapPrm": ZERO_CAPS,
        "CapAmb": ZERO_CAPS,
        "CapBnd": bounding,
        "NoNewPrivs": "1",
    }
    return [
        f"{field}={status.get(field, 'ontbreekt')}"
        for field, value in expected.items()
        if " ".join(status.get(field, "").split()) != value
    ]


def privilege_probe(*, root_start: bool) -> tuple[bool, str]:
    registered = json.loads(sh("docker", "inspect", "-f", "{{json .Config.Healthcheck.Test}}", NAME))
    if registered != ["CMD", *HEALTHCHECK]:
        return False, "Docker-healthcheck staat niet in de verwachte exec-vorm"
    initial = parse_status(sh("docker", "exec", NAME, "cat", "/proc/self/status"))
    bounding = ZERO_CAPS if root_start else initial["CapBnd"]
    raw = json.loads(sh("docker", "exec", NAME, "python", "-c", PROCESS_STATUS))
    processes = {pid: parse_status(status) for pid, status in raw.items()}
    tree = {"1"}
    while True:
        descendants = {pid for pid, status in processes.items() if status["PPid"] in tree} - tree
        if not descendants:
            break
        tree.update(descendants)
    errors = []
    if len(tree) < 5:
        errors.append("onvolledige procesboom")
    for pid in sorted(tree, key=int):
        wrong = privilege_errors(processes.get(pid, {}), bounding)
        if wrong:
            errors.append(f"PID {pid}: {', '.join(wrong)}")
    health = parse_status(sh("docker", "exec", NAME, *registered[1:], "--controle"))
    wrong = privilege_errors(health, bounding)
    if wrong:
        errors.append(f"healthcheck: {', '.join(wrong)}")
    if errors:
        return False, "; ".join(errors)
    return True, f"{len(tree)} processen incl. PID 1 plus healthcheck; CapBnd={bounding}"


def healthcheck_refusals() -> None:
    denied = subprocess.run(
        ["docker", "run", "--rm", "--network", f"container:{NAME}", "--cap-drop", "SETUID",
         "--entrypoint", HEALTHCHECK[0], IMAGE, HEALTHCHECK[1]],
        capture_output=True, text=True, check=False, timeout=30,
    )
    transition_failed = "Healthcheck: rechtenwissel" in denied.stderr
    record("14 healthcheck zonder SETUID", denied.returncode == 1 and transition_failed,
           f"exitcode {denied.returncode}; fout bij rechtenwissel={transition_failed}")

    # Injecteer de fout in één diagnoseproces van de testcontainer. De image en de normale healthcheck draaien door.
    skip_drop = """
import runpy
import sys
from mso import allinone_privileges

allinone_privileges.lower_privileges = lambda *_: None
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    bypassed = parse_status(sh("docker", "exec", NAME, "python", "-c", skip_drop, HEALTHCHECK[1], "--controle"))
    ordinary = parse_status(sh("docker", "exec", NAME, *HEALTHCHECK, "--controle"))
    failure_fields = [field.partition("=")[0] for field in privilege_errors(bypassed, ZERO_CAPS)]
    # Isoleer UID 0 en elk extra capability-veld van de andere afwijkingen.
    invalid = {"Uid": "0 0 0 0", **dict.fromkeys(("CapEff", "CapPrm", "CapAmb", "CapBnd"), "0000000000000001")}
    refused = [
        field for field, value in invalid.items()
        if privilege_errors({**ordinary, field: value}, ZERO_CAPS) == [f"{field}={value}"]
    ]
    bypassed_as_root = bypassed.get("Uid", "").split() == ["0"] * 4
    record("15 rechtenproef weigert overgeslagen wissel en afwijkende rechten",
           bypassed_as_root and "Uid" in failure_fields and refused == list(invalid),
           f"diagnose afgekeurd op {', '.join(failure_fields)}; afzonderlijk afgekeurd: {', '.join(refused)}")


def orphan_probe() -> None:
    orphan = """
import os
import time

if os.fork() == 0:
    deadline = time.monotonic() + 3
    while os.getppid() != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    print(os.getpid(), os.getppid(), flush=True)
os._exit(0)
"""
    pid, parent = sh("docker", "exec", "--user", "568:568", NAME, "python", "-c", orphan).split()
    wait_reaped = """
import sys
import time
from pathlib import Path

for _ in range(80):
    if not Path('/proc', sys.argv[1]).exists():
        sys.exit(0)
    time.sleep(0.1)
sys.exit(1)
"""
    reaped = subprocess.run(
        ["docker", "exec", "--user", "568:568", NAME, "python", "-c", wait_reaped, pid],
        check=False, timeout=15,
    )
    record("16 PID 1 ruimt overgenomen zombie op", parent == "1" and reaped.returncode == 0,
           f"overgenomen door PID {parent}; opruimcontrole exitcode {reaped.returncode}")


def login() -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/v1/auth/login", json={"username": ADMIN, "password": ADMIN_PW})
    r.raise_for_status()
    return c, r.json()["csrf_token"]


def container_logs() -> str:
    # Docker levert beide stromen op volgorde. Lees ze via dezelfde pijp.
    return subprocess.run(
        ["docker", "logs", NAME], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True,
    ).stdout


def web_key_fingerprints() -> list[str]:
    """Vergelijk de echte opstartmanifesten per installatie. Alleen vingerafdrukken verlaten het proefproces."""
    probe = """
import hashlib, json, os, stat, sys
from pathlib import Path
stored = Path('/appdata/web/server-actions.key')
assert stat.S_IMODE(stored.stat().st_mode) == 0o600 and stored.stat().st_uid == os.getuid()
root = Path('/opt/mso-web/.next')
actions = json.loads((root / 'server/server-reference-manifest.json').read_text())
preview = json.loads((root / 'server/preview-props.json').read_text())
assert stored.read_text() == actions['encryptionKey']
assert preview == json.loads((root / 'prerender-manifest.json').read_text())['preview']
values = [actions['encryptionKey'], *preview.values()]
logs = sys.stdin.read()
assert len(set(values)) == 4 and all(value and value not in logs for value in values)
print(json.dumps([hashlib.sha256(value.encode()).hexdigest() for value in values]))
"""
    result = subprocess.run(
        ["docker", "exec", "-i", "--user", "568:568", NAME, "python", "-c", probe],
        input=container_logs(), capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def assert_backup_before_migration(logs: str, filename: str, first_migration: list | tuple[int, str]) -> None:
    marker = "back-up vóór upgrade gemaakt: " + filename
    applied = f"migratie {first_migration[0]:04d}_{first_migration[1]} toegepast"
    assert 0 <= logs.find(marker) < logs.find(applied), "het archief moet klaar zijn vóór de eerste migratie"


def stop_cleanly() -> None:
    sh("docker", "stop", "-t", "60", NAME)
    assert sh("docker", "inspect", "-f", "{{.State.ExitCode}}", NAME) == "0", "containerstop mislukte"


def api_data(client: httpx.Client, route: str) -> dict | list:
    return client.get("/api/v1/" + route).raise_for_status().json()


def manual_backup(client: httpx.Client, csrf: str) -> tuple[str, dict | None]:
    job_id = client.post("/api/v1/backups", headers={"X-CSRF-Token": csrf},
                         json={"include_secrets": False}).raise_for_status().json()["job_id"]
    for _ in range(40):
        job = api_data(client, f"jobs/{job_id}")
        if job["status"] in ("done", "failed"):
            break
        time.sleep(1)
    filename = (job.get("result") or {}).get("file")
    backup = next((row for row in api_data(client, "backups")["items"]
                   if row["kind"] == "manual" and row["file"] == filename), None)
    return job["status"], backup


def upgrade_data(client: httpx.Client, change_id: int) -> tuple:
    users = api_data(client, "users")["items"]
    connector = next(row for row in api_data(client, "connectors") if row["id"] == "sabnzbd")
    change = api_data(client, f"changes/{change_id}")
    audit = api_data(client, "audit?action=settings.updated")["items"]
    return (
        [{k: row[k] for k in ("id", "username", "role", "disabled")} for row in users],
        {k: connector[k] for k in ("id", "url", "enabled", "options")},
        {k: change[k] for k in ("id", "target", "title", "status", "ops", "created_by")},
        api_data(client, "settings")["values"]["backup.keep"],
        [{k: row[k] for k in ("id", "actor", "action", "summary", "detail")} for row in audit],
    )


def check_sab_read(client: httpx.Client, csrf: str, *, expect_access: bool = True) -> None:
    # Geef enkel de sessie mee. MSO moet de proefsleutel uit de eigen kluis lezen.
    result = client.post("/api/v1/connectors/sabnzbd/test", headers={"X-CSRF-Token": csrf}).raise_for_status().json()
    if expect_access:
        assert result["ok"] is True and result["capabilities"].get("modes", {}).get("queue") is True, (
            "de geauthenticeerde SABnzbd-wachtrijlezing mislukte"
        )
    else:
        assert result["ok"] is False and result["status"] == "auth_failed", (
            "de SABnzbd-wachtrijlezing moet zonder de opgeslagen proefsleutel worden geweigerd"
        )


def known_migrations(image: str) -> list[list]:
    return json.loads(sh(
        "docker", "run", "--rm", "--pull=never", "--network", "none", "--entrypoint", "python", image, "-c",
        "import json; from mso.db import load_migrations; "
        "print(json.dumps([(m.version, m.name) for m in load_migrations()]))",
    ))


def migration_history(info: dict) -> list[tuple[int, str, datetime]]:
    return [(row["version"], row["name"], datetime.fromisoformat(row["applied_at"])) for row in info["migrations"]]


def restore_partial_archive(data: Path, backups: Path, filename: str, digest: str, image: str) -> None:
    # Alleen PostgreSQL draait tijdens het herstel. De oude app start pas na herstel van het oude schema.
    sh(
        "docker", "run", "--rm", "--pull=never", "--network", "none", *data_mount(data, "/appdata"),
        *data_mount(backups, "/archive", readonly=True), "--entrypoint", "python", image, "-c",
        """
import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/mso")
import supervisor

archive = Path("/archive") / sys.argv[1]
expected = sys.argv[2]
with archive.open("rb") as source:
    assert hashlib.file_digest(source, "sha256").hexdigest() == expected, "SHA-256 van het archief wijkt af"
assert archive.with_suffix(".sha256").read_text().split() == [expected, archive.name], "SHA-256-bestand wijkt af"
supervisor.prepare_dirs()
supervisor.init_database()
pg = supervisor.postgres_child()
pg.start()
try:
    supervisor.wait_for_db(pg)
    subprocess.run(
        ["pg_restore", "-h", str(supervisor.SOCKET_DIR), "-U", "mso", "-d", "mso",
         "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction", str(archive)],
        preexec_fn=supervisor.demote, check=True,
    )
finally:
    pg.stop()
""", filename, digest,
    )


def snapshot_stopped_appdata(data: Path, snapshot: Path, image: str) -> None:
    make_data_dir(snapshot)
    sh(
        "docker", "run", "--rm", "--pull=never", "--network", "none", *data_mount(data, "/source", readonly=True),
        *data_mount(snapshot, "/snapshot"), "--entrypoint", "sh", image, "-ec",
        "test ! -e /source/postgres/postmaster.pid; test -s /source/secrets/vault.key; cp -a /source/. /snapshot/",
    )


def stopped_data_fingerprint(data: Path) -> str:
    # Lees de applicatiemappen direct. Maak voor de logische databasedump een kopie,
    # omdat alleen het starten van PostgreSQL al WAL- en controlebestanden kan bijwerken.
    return sh(
        "docker", "run", "--rm", "--pull=never", "--network", "none", "--user", "568:568",
        *data_mount(data, "/source", readonly=True), "-e", "MSO_DATA_DIR=/tmp/probe",
        "--entrypoint", "python", IMAGE, "-c",
        """
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/mso")
import supervisor

source = Path("/source")
postgres = source / "postgres"
entries = [source, postgres]
for path in source.iterdir():
    if path != postgres:
        entries.append(path)
        if path.is_dir() and not path.is_symlink():
            entries.extend(path.rglob("*"))
manifest = []
for path in sorted(entries):
    info = path.lstat()
    content = None
    if stat.S_ISLNK(info.st_mode):
        content = os.readlink(path)
    elif stat.S_ISREG(info.st_mode):
        with path.open("rb") as stream:
            content = hashlib.file_digest(stream, "sha256").hexdigest()
    # Een normale PostgreSQL-stop wijzigt de mtime van zijn eigen databasemap.
    mtime = None if path == postgres else info.st_mtime_ns
    manifest.append((str(path.relative_to(source)), info.st_mode, info.st_uid, info.st_gid, mtime, content))
assert not (postgres / "postmaster.pid").exists(), "de bron is niet schoon gestopt"
shutil.copytree(postgres, supervisor.PGDATA)
supervisor.SOCKET_DIR.mkdir()
pg = supervisor.postgres_child()
pg.start()
try:
    supervisor.wait_for_db(pg)
    dump = subprocess.check_output(
        ["pg_dump", "-h", str(supervisor.SOCKET_DIR), "-U", "mso", "-d", "mso",
         "--restrict-key=msoSchemaGuardTest"],
    )
finally:
    pg.stop()
print(json.dumps([hashlib.sha256(dump).hexdigest(), hashlib.sha256(json.dumps(manifest).encode()).hexdigest()]))
""",
    ).splitlines()[-1]


def verify_schema_refusal(data: Path, image: str, migrated_schema: int) -> str:
    guard_schema = known_migrations(image)[-1][0]
    assert 501 < guard_schema < migrated_schema, "de proef vraagt een echt nieuwere schemaversie dan de weigerimage"
    before = stopped_data_fingerprint(data)
    run(data, image=image)
    message = "database is van een nieuwere MSO-versie; zet de bijpassende snapshot/back-up terug"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        logs = container_logs()
        if message in logs or "start api" in logs:
            break
        time.sleep(1)
    assert message in logs, "de Nederlandse schemaweigering ontbreekt in het containerlog"
    unexpected = ("start api", "start worker", "start web", "migraties:", "back-up vóór upgrade:")
    assert not any(entry in logs for entry in unexpected), "de weigering startte alsnog een applicatieproces"
    health = subprocess.run(["docker", "exec", NAME, *HEALTHCHECK], capture_output=True, check=False)
    assert health.returncode == 1, "de healthcheck moet de geweigerde start afkeuren"
    stop_cleanly()
    database, files = json.loads(before)
    database_after, files_after = json.loads(stopped_data_fingerprint(data))
    assert database == database_after, "de logische database wijzigde bij de weigering"
    assert files == files_after, "de applicatiebestanden of hun metadata wijzigden bij de weigering"
    return f"schema {migrated_schema} > {guard_schema}; schemaweigering; healthcheck 1; database en appbestanden gelijk"


def check_daily_resource_sample(client: httpx.Client) -> None:
    # Laat de gewone worker onder zijn bestaande rechten verzamelen.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        maintenance = api_data(client, "system/maintenance")
        latest = maintenance["growth"]["latest"]
        if latest is not None:
            assert latest["values"]["database_bytes"] > 0, "databasegrootte ontbreekt in de dagmeting"
            assert latest["values"]["database_connections"] > 0, "databaseverbindingen ontbreken in de dagmeting"
            assert latest["values"]["appdata_free_bytes"] >= 0, "vrije app-gegevensruimte ontbreekt"
            assert maintenance["provenance"]["database_bytes"]["quality"] == "gemeten", "herkomst ontbreekt"
            return
        time.sleep(1)
    raise AssertionError("de worker registreerde na upgrade geen opslagmeting")


def published_image_upgrade(tmp: Path, version: str, image: str, schema: int,
                            scenarios: tuple[str, str]) -> tuple[Path, int] | None:
    scenario = 0
    try:
        sh("docker", "pull", image)
    except RuntimeError:
        for name in scenarios:
            record(name, None, f"OVERGESLAGEN: de verplichte openbare {version}-digest is niet opgehaald")
        return None

    try:
        make_data_dir(tmp)
        data = tmp / "upgrade"
        make_data_dir(data)
        run(data, image=image)
        assert wait_healthy() == "healthy", f"{version} startte niet op de lege map"
        with httpx.Client(base_url=BASE, timeout=30) as client:
            initial = client.post("/api/v1/setup/admin", json={"username": ADMIN, "password": ADMIN_PW})
            csrf = initial.raise_for_status().json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}
            client.put("/api/v1/settings", headers=headers, json={"values": {"backup.keep": 23}}).raise_for_status()
            connector = {"url": "http://sabnzbd:8080", "enabled": True, "options": {"probe": "upgrade"}}
            client.put("/api/v1/connectors/sabnzbd", headers=headers,
                       json={**connector, "secrets": {"api_key": SAB_KEY}}).raise_for_status()
            client.post("/api/v1/setup/complete", headers=headers, json={"mode": "read_only"}).raise_for_status()
            check_sab_read(client, csrf)
            change_id = int(sh(
                "docker", "exec", "-u", "568:568", "-e", "MSO_DATABASE_URL=postgresql://mso@/mso?host=/tmp/mso-pg",
                NAME, "python", "-c",
                "from mso import changes; from mso.db import Database, set_db; from mso.config import get_settings; "
                "db = Database(get_settings().database_url, max_size=1); set_db(db); "
                "print(changes.create('sabnzbd', 'Upgradeproef', [{'path': 'proef', 'before': 1, 'after': 2}], "
                "risk='advies', db=db)['id']); db.close()",
            ))
            original = upgrade_data(client, change_id)
            users, stored_connector, change, setting, audit = original
            assert setting == 23 and any(row["actor"] == ADMIN for row in audit), "proefinstelling of audit ontbreekt"
            assert len(users) == 1 and users[0]["username"] == ADMIN, "proefgebruiker ontbreekt"
            assert stored_connector == {"id": "sabnzbd", **connector}, "ingestelde proefkoppeling ontbreekt"
            assert change["title"] == "Upgradeproef" and change["status"] == "preview", "proefvoorstel ontbreekt"
            old_info = api_data(client, "system/info")
            assert old_info["version"] == version, "de draaiende bronversie wijkt af"
            assert old_info["schema_version"] == schema, f"de upgrade moet op schema {schema} beginnen"
            old_history = migration_history(old_info)
            status, manual = manual_backup(client, csrf)
            assert status == "done" and manual and manual["present"] and manual["size_bytes"] > 0, (
                "de handmatige databaseback-up van de oude app ontbreekt"
            )
        stop_cleanly()
        before_db, before_files = json.loads(stopped_data_fingerprint(data))
        snapshot = tmp / "snapshot"
        snapshot_stopped_appdata(data, snapshot, image)

        run(data, "--user", "568:568")
        assert wait_healthy() == "healthy", f"de nieuwe image startte niet op de {version}-map"
        safe, detail = privilege_probe(root_start=False)
        assert safe, detail
        client, csrf = login()
        with closing(client):
            assert upgrade_data(client, change_id) == original, "proefgegevens wijzigden bij upgrade"
            check_sab_read(client, csrf)
            info = api_data(client, "system/info")
            candidate_version = (Path(__file__).resolve().parents[2] / "release/VERSION").read_text().strip()
            assert api_data(client, "setup/status")["version"] == candidate_version, "de doelversie wijkt af"
            expected = known_migrations(IMAGE)
            assert expected[-1][0] == info["schema_version"] > schema, "de kandidaat heeft geen nieuwer schema"
            assert [[row["version"], row["name"]] for row in info["migrations"]] == expected, "migraties ontbreken"
            # Bij 1.3.0 ontbreken ook migraties met een nummer ónder de toenmalige maximumversie 500.
            old_versions = {row[0] for row in old_history}
            assert [row for row in migration_history(info) if row[0] in old_versions] == old_history, (
                "migratiehistorie wijkt af"
            )
            archives = [row for row in api_data(client, "backups")["items"] if row["kind"] == "pre-upgrade"]
            assert len(archives) == 1 and archives[0]["schema_version"] == schema and archives[0]["present"], (
                f"verwacht één aanwezig pre-upgrade-archief van schema {schema}"
            )
            check_daily_resource_sample(client)
        archive = archives[0]
        first_migration = next(row for row in expected if row[0] not in old_versions)
        assert_backup_before_migration(container_logs(), archive["file"], first_migration)
        stop_cleanly()
        record(scenarios[0], True, f"{schema} → {info['schema_version']}; {len(expected)} migraties; "
               f"handmatige back-up en archief vooraf; {len(users)} gebruiker en {len(audit)} auditregel(s) behouden; "
               "instelling, koppeling en voorstel behouden; "
               "SABnzbd-wachtrijlezing werkt; "
               "healthy met UID 568 inclusief PID 1 en healthcheck; opslagmeting door de worker")
        scenario = 1

        partial = tmp / "partial"
        make_data_dir(partial)
        filename = archive["file"]
        assert filename == Path(filename).name and filename.endswith("-pre-upgrade.dump"), "ongeldige archiefnaam"
        restore_partial_archive(partial, data / "backups", filename, archive["sha256"], image)
        run(partial, image=image)
        assert wait_healthy() == "healthy", f"{version} startte niet na gedeeltelijk archiefherstel"
        client, csrf = login()
        with closing(client):
            partial_info = api_data(client, "system/info")
            assert migration_history(partial_info) == old_history, "de migratiehistorie uit het archief wijkt af"
            assert partial_info["schema_version"] == schema, f"archiefherstel moet terug naar schema {schema}"
            assert upgrade_data(client, change_id) == original, "proefgegevens uit het archief wijken af"
            check_sab_read(client, csrf, expect_access=False)
        stop_cleanly()

        # Vergelijk vóór de appstart: een nieuwe inlog en de worker mogen daarna rijen wijzigen.
        restored_db, restored_files = json.loads(stopped_data_fingerprint(snapshot))
        assert restored_db == before_db, "de tabelinhoud inclusief versleutelde kluisrijen wijkt af na APPDATA-herstel"
        assert restored_files == before_files, "APPDATA-bestanden inclusief vault-sleutel wijken af na herstel"
        run(snapshot, image=image)
        assert wait_healthy() == "healthy", f"{version} startte niet na volledige APPDATA-terugval"
        client, csrf = login()
        with closing(client):
            restored_info = api_data(client, "system/info")
            assert migration_history(restored_info) == old_history, "de migratiehistorie na volledige terugval wijkt af"
            assert restored_info["schema_version"] == schema, f"volledige terugval moet naar schema {schema}"
            assert upgrade_data(client, change_id) == original, "proefgegevens wijken af na volledige terugval"
            check_sab_read(client, csrf)
        stop_cleanly()
        record(scenarios[1], True, f"gedeeltelijk: SHA-256 gecontroleerd, schema {schema}, wachtrijlezing geweigerd; "
               f"volledig: schema {schema}, alle tabelinhoud en APPDATA-bestanden gelijk, SABnzbd-wachtrijlezing werkt")
        return data, info["schema_version"]
    except (AssertionError, RuntimeError, httpx.HTTPError, subprocess.CalledProcessError) as exc:
        record(scenarios[scenario], False, str(exc))
        for name in scenarios[scenario + 1:]:
            record(name, None, "OVERGESLAGEN: een voorgaande upgrade- of herstelproef faalde")
        return None


def schema_guard_case(upgrade: tuple[Path, int] | None) -> None:
    if not upgrade:
        record(SCHEMA_GUARD, None, "OVERGESLAGEN: de upgrade vanaf 1.5.0 of de herstelproef faalde")
        return
    guard = os.environ.get("MSO_SCHEMA_GUARD_IMAGE", "")
    if not guard:
        record(SCHEMA_GUARD, None, "OVERGESLAGEN: bouw eerst de gepinde image met build-schema-guard.sh")
        return
    try:
        metadata = json.loads(sh("docker", "image", "inspect", guard))[0]
        assert guard == metadata["Id"] and metadata["Config"]["Labels"].get(
            "org.opencontainers.image.revision"
        ) == GUARD_REVISION, "de weigerimage moet op image-ID en de vaste broncommit gepind zijn"
        data, schema = upgrade
        record(SCHEMA_GUARD, True, verify_schema_refusal(data, guard, schema))
    except (AssertionError, RuntimeError, httpx.HTTPError, subprocess.CalledProcessError) as exc:
        record(SCHEMA_GUARD, False, str(exc))


def main() -> int:
    if sys.argv[2:] not in ([], ["--vanaf-1.3.0"]):
        raise SystemExit("Gebruik: allinone_test.py IMAGE [--vanaf-1.3.0]")
    tmp = Path(".") if DATA_VOLUME else Path(tempfile.mkdtemp(prefix="mso-aio-"))
    data = tmp / "appdata"
    make_data_dir(data, mode=0o755)
    sh("docker", "network", "create", NET, check=False)
    sh(
        "docker",
        "run",
        "-d",
        "--name",
        MOCK_NAME,
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
        if sys.argv[2:] == ["--vanaf-1.3.0"]:
            published_image_upgrade(tmp / "published-1.3.0", "1.3.0", PUBLISHED_130, 500, UPGRADE_130)
            passed = sum(ok is True for _, ok, _ in RESULTS)
            print(f"Update en terugweg vanaf 1.3.0: {passed}/2 controles groen", flush=True)
            return 0 if passed == len(RESULTS) == 2 else 1
        # 1
        run(data)
        st = wait_healthy()
        owners = sh(
            "docker",
            "run",
            "--rm",
            *data_mount(data, "/d"),
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
        root_ok, root_details = privilege_probe(root_start=True)
        healthcheck_refusals()
        orphan_probe()

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

        def connector_response(response: httpx.Response, status: int, code: str) -> bool:
            if response.status_code != status or not response.headers.get("content-type", "").startswith(
                "application/json"
            ):
                return False
            body = response.json()
            return set(body) == {"code", "message", "details", "trace_id"} and body["code"] == code

        anonymous = httpx.get(f"{BASE}/connector/v1/discover", timeout=30)
        discovery = c.get("/connector/v1/discover")
        read_key = c.post("/api/v1/machine-keys", headers={"X-CSRF-Token": csrf},
                          json={"name": "Leessleutel voor de proef", "scopes": ["read"]})
        machine = httpx.get(f"{BASE}/connector/v1/discover", headers={"X-Api-Key": read_key.json()["key"]},
                            timeout=30)
        missing = c.get("/connector/v1/bestaat-niet")
        unavailable = c.post("/connector/v1/apply", headers={"X-CSRF-Token": csrf}, json={})
        oversized = httpx.post(f"{BASE}/connector/v1/apply", content=iter((b"x" * 1_048_576, b"x")), timeout=30)
        ready = httpx.get(f"{BASE}/readyz", timeout=30)
        live = httpx.get(f"{BASE}/healthz", timeout=30)
        key_file = "/appdata/secrets/vault.key"
        hidden_key = key_file + ".readyz-test"
        sh("docker", "exec", NAME, "mv", key_file, hidden_key)
        try:
            unready = httpx.get(f"{BASE}/readyz", timeout=30)
        finally:
            sh("docker", "exec", NAME, "mv", hidden_key, key_file)
        record(
            "12 connector en readiness via webpoort",
            connector_response(anonymous, 401, "unauthorized")
            and connector_response(missing, 404, "not_found")
            and connector_response(unavailable, 501, "capability_unavailable")
            and connector_response(oversized, 413, "payload_too_large")
            and discovery.status_code == 200 and discovery.json()["capabilities"] == ["read"]
            and machine.status_code == 200 and machine.json()["permissions"] == ["read"]
            and ready.status_code == 200 and ready.json()["status"] == "ready"
            and live.status_code == 200 and live.json()["status"] == "ok" and live.json()["version"]
            and unready.status_code == 503 and unready.json()["checks"]["vault"] is False,
            f"discover {anonymous.status_code}/{discovery.status_code}/{machine.status_code}; "
            f"onbekend {missing.status_code}; apply {unavailable.status_code}/{oversized.status_code}; "
            f"readiness {ready.status_code}/{unready.status_code}",
        )

        info = c.get("/api/v1/system/info")
        build_time = os.environ.get("MSO_BUILD_TIME", "").strip() or "onbekend"
        if build_time.endswith("+00:00"):
            build_time = build_time.removesuffix("+00:00") + "Z"
        expected = {
            "version": os.environ.get("MSO_BUILD_VERSION", "").strip() or "onbekend",
            "git_sha": os.environ.get("MSO_BUILD_GIT_SHA", "").strip() or "onbekend",
            "build_time": build_time,
            "channel": os.environ.get("MSO_BUILD_CHANNEL", "").strip() or "onbekend",
        }
        metadata = info.json()
        matches = [field for field, value in expected.items() if metadata.get(field) == value]
        record("11 buildgegevens uit de alles-in-één-image", info.status_code == 200 and len(matches) == len(expected),
               f"HTTP {info.status_code}; overeenkomende velden: {', '.join(matches)}")

        # 3
        status, b = manual_backup(c, csrf)
        restored = None
        if b:
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
            f"job {status}; handmatige back-up: {bool(b)}; herstel {restored.status_code if restored else '-'}; "
            f"sleutel na herstel: {sab['secret_fields'][0]['status']}",
        )

        # 4
        first_keys = web_key_fingerprints()
        sh("docker", "restart", NAME)
        st = wait_healthy()
        c, csrf = login()
        n_users = len(c.get("/api/v1/users").json()["items"])
        login_page = c.get("/login")
        record("4 docker restart → data, login en websleutels blijven",
               st == "healthy" and n_users == 1 and login_page.status_code == 200
               and "text/html" in login_page.headers.get("content-type", "")
               and web_key_fingerprints() == first_keys, f"health={st}, /login HTTP {login_page.status_code}")

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
        stale = sh("docker", "run", "--rm", "--network", "none", "--user", "0:0",
                   *data_mount(data, "/d", readonly=True), "--entrypoint", "sh", IMAGE, "-c",
                   'if test -f /d/postgres/postmaster.pid; then echo aanwezig; fi') == "aanwezig"
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
        record("6 netjes stoppen", took < 30 and code == "0", f"{took:.1f} s, exitcode {code}")

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
        make_data_dir(d2)
        # Gebruik de UID van de lokale Docker-runtime voor de tijdelijke testmap.
        sh("docker", "run", "--rm", "--network", "none", *data_mount(d2, "/d"),
           "--entrypoint", "chown", IMAGE, "568:568", "/d")
        # Lokale GET-controles blijven bereikbaar met een onbruikbare proxy in de omgeving.
        run(d2, "--user", "568:568", "-e", "http_proxy=http://localhost:1", "-e", "no_proxy=")
        st = wait_healthy()
        record("7 --user 568 op tweede appmap → gezond met eigen websleutels",
               st == "healthy" and set(first_keys).isdisjoint(web_key_fingerprints()), f"health={st}")
        user_ok, user_details = privilege_probe(root_start=False)
        record("13 procesboom en healthcheck bij beide startmodi", root_ok and user_ok,
               f"root-start: {root_details}; gebruiker 568: {user_details}")

        # 8
        d3 = tmp / "appdata-root"
        make_data_dir(d3, mode=0o755)
        run(d3, "--user", "568:568")
        time.sleep(8)
        log8 = subprocess.run(["docker", "logs", NAME], capture_output=True, text=True, check=False)
        msg = [ln for ln in (log8.stdout + log8.stderr).splitlines() if "FOUT" in ln]
        record("8 --user 568 op root-map → duidelijke foutmelding", bool(msg), msg[0] if msg else "geen melding")
        legacy_upgrade = published_image_upgrade(tmp / "published-1.5.0", "1.5.0", PUBLISHED_150, 501, UPGRADE_150)
        schema_guard_case(legacy_upgrade)
        published_image_upgrade(tmp / "published-1.6.0", "1.6.0", PUBLISHED_160, 607, UPGRADE_160)
        published_image_upgrade(tmp / "published-1.3.0", "1.3.0", PUBLISHED_130, 500, UPGRADE_130)
    finally:
        sh("docker", "rm", "-f", NAME, MOCK_NAME, check=False)
        sh("docker", "network", "rm", NET, check=False)
        if not DATA_VOLUME:
            sh("docker", "run", "--rm", "-v", f"{tmp}:/t", "--entrypoint", "sh", IMAGE,
               "-c", "rm -rf /t/*", check=False)
            tmp.rmdir()

    passed = sum(ok is True for _, ok, _ in RESULTS)
    skipped = sum(ok is None for _, ok, _ in RESULTS)
    ok = passed == len(RESULTS) == SCENARIO_COUNT
    out = Path(os.environ.get("MSO_STACK_RESULTS_ROOT", Path(__file__).parent / "results"))
    out.mkdir(exist_ok=True)
    lines = [
        "# All-in-one image — testrapport",
        "",
        f"Image: `{IMAGE}` · {len(RESULTS)} scenario's · {passed} groen · {skipped} overgeslagen",
        "",
        "| Scenario | Resultaat | Details |",
        "|---|---|---|",
    ]
    labels = {True: "ok", False: "MISLUKT", None: "OVERGESLAGEN"}
    for name, result, detail in RESULTS:
        cell = "<br>".join(detail.replace("|", r"\|").splitlines())
        lines.append(f"| {name} | {labels[result]} | {cell} |")
    (out / "allinone-report.md").write_text("\n".join(lines) + "\n")
    (out / "allinone-report.json").write_text(
        json.dumps([{"scenario": n, "ok": o, "detail": d} for n, o, d in RESULTS], indent=2, ensure_ascii=False)
    )
    print(f"Alles-in-één: {passed}/{SCENARIO_COUNT} scenario's groen, {skipped} overgeslagen", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
