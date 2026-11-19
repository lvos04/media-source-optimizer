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
18  pre-upgrade-archief herstellen in een lege 1.5.0-installatie
19  gepinde S-P3-205-image weigert het inmiddels nieuwere schema zonder gegevenswijziging
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
PREVIOUS_IMAGE = ("ghcr.io/lvos04/media-source-optimizer"
                  "@sha256:7ee5cb5e3c37d8ca6e3c8445cd6ef24f8b175e2f3167ccd40541737564866be2")
GUARD_REVISION = "2512cd761ad30f42a4787935687388e9a3f0d428"
UPGRADE_SCENARIOS = (
    "17 upgrade vanaf de gepubliceerde 1.5.0-image",
    "18 pre-upgrade-archief herstellen met de 1.5.0-image",
    "19 oudere image met schemabewaking weigert nieuwer schema",
)
SCENARIO_COUNT = 16 + len(UPGRADE_SCENARIOS)
NAME = f"mso-aio-{secrets.token_hex(6)}"
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
        detail = (r.stderr.strip().splitlines() or ["geen foutmelding"])[-1]
        raise RuntimeError(f"{' '.join(cmd[:2])} exitcode {r.returncode}: {detail[:400]}")
    return r.stdout.strip()


def record(name: str, ok: bool | None, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    status = {True: "OK  ", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{status}] {name} — {detail}", flush=True)


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
        "-v",
        f"{data}:/appdata",
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
    result = subprocess.run(["docker", "logs", NAME], capture_output=True, text=True, check=True)
    return result.stdout + result.stderr


def stop_cleanly() -> None:
    sh("docker", "stop", "-t", "60", NAME)
    assert sh("docker", "inspect", "-f", "{{.State.ExitCode}}", NAME) == "0", "containerstop mislukte"


def api_data(client: httpx.Client, route: str) -> dict | list:
    return client.get("/api/v1/" + route).raise_for_status().json()


def upgrade_data(client: httpx.Client, change_id: int) -> tuple:
    users = api_data(client, "users")["items"]
    connector = next(row for row in api_data(client, "connectors") if row["id"] == "sabnzbd")
    change = api_data(client, f"changes/{change_id}")
    return (
        [{k: row[k] for k in ("id", "username", "role", "disabled")} for row in users],
        {k: connector[k] for k in ("id", "url", "enabled", "options")},
        {k: change[k] for k in ("id", "target", "title", "status", "ops", "created_by")},
    )


def known_migrations(image: str) -> list[list]:
    return json.loads(sh(
        "docker", "run", "--rm", "--pull=never", "--network", "none", "--entrypoint", "python", image, "-c",
        "import json; from mso.db import load_migrations; "
        "print(json.dumps([(m.version, m.name) for m in load_migrations()]))",
    ))


def migration_history(info: dict) -> list[tuple[int, str, datetime]]:
    return [(row["version"], row["name"], datetime.fromisoformat(row["applied_at"])) for row in info["migrations"]]


def restore_pre_upgrade(data: Path, backups: Path, filename: str, digest: str) -> None:
    # Alleen PostgreSQL draait tijdens het herstel. De oude app start pas met schema 501.
    sh(
        "docker", "run", "--rm", "--pull=never", "--network", "none", "-v", f"{data}:/appdata",
        "-v", f"{backups}:/archive:ro", "--entrypoint", "python", PREVIOUS_IMAGE, "-c",
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


def stopped_data_fingerprint(data: Path) -> str:
    # Lees de applicatiemappen direct. Maak voor de logische databasedump een kopie,
    # omdat alleen het starten van PostgreSQL al WAL- en controlebestanden kan bijwerken.
    return sh(
        "docker", "run", "--rm", "--pull=never", "--network", "none", "--user", "568:568",
        "-v", f"{data}:/source:ro", "-e", "MSO_DATA_DIR=/tmp/probe", "--entrypoint", "python", IMAGE, "-c",
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


def previous_image_upgrade(tmp: Path) -> None:
    scenario = 0
    try:
        sh("docker", "pull", PREVIOUS_IMAGE)
    except RuntimeError:
        for name in UPGRADE_SCENARIOS:
            record(name, None, "OVERGESLAGEN: de verplichte openbare 1.5.0-digest is niet opgehaald")
        return

    try:
        data = tmp / "upgrade"
        data.mkdir()
        run(data, image=PREVIOUS_IMAGE)
        assert wait_healthy() == "healthy", "1.5.0 startte niet op de lege map"
        with httpx.Client(base_url=BASE, timeout=30) as client:
            initial = client.post("/api/v1/setup/admin", json={"username": ADMIN, "password": ADMIN_PW})
            csrf = {"X-CSRF-Token": initial.raise_for_status().json()["csrf_token"]}
            connector = {"url": "http://sabnzbd:8080", "enabled": False, "options": {"probe": "upgrade"}}
            client.put("/api/v1/connectors/sabnzbd", headers=csrf, json=connector).raise_for_status()
            client.post("/api/v1/setup/complete", headers=csrf, json={"mode": "read_only"}).raise_for_status()
            change_id = int(sh(
                "docker", "exec", "-u", "568:568", "-e", "MSO_DATABASE_URL=postgresql://mso@/mso?host=/tmp/mso-pg",
                NAME, "python", "-c",
                "from mso import changes; from mso.db import Database, set_db; from mso.config import get_settings; "
                "db = Database(get_settings().database_url, max_size=1); set_db(db); "
                "print(changes.create('sabnzbd', 'Upgradeproef', [{'path': 'proef', 'before': 1, 'after': 2}], "
                "risk='advies', db=db)['id']); db.close()",
            ))
            original = upgrade_data(client, change_id)
            users, stored_connector, change = original
            assert len(users) == 1 and users[0]["username"] == ADMIN, "proefgebruiker ontbreekt"
            assert stored_connector == {"id": "sabnzbd", **connector}, "ingestelde proefkoppeling ontbreekt"
            assert change["title"] == "Upgradeproef" and change["status"] == "preview", "proefvoorstel ontbreekt"
            old_info = api_data(client, "system/info")
            assert old_info["schema_version"] == 501, "de upgrade moet op schema 501 beginnen"
            old_history = migration_history(old_info)
        stop_cleanly()

        run(data)
        assert wait_healthy() == "healthy", "de nieuwe image startte niet op de 1.5.0-map"
        with closing(login()[0]) as client:
            assert upgrade_data(client, change_id) == original, "proefgegevens wijzigden bij upgrade"
            info = api_data(client, "system/info")
            expected = known_migrations(IMAGE)
            assert [[row["version"], row["name"]] for row in info["migrations"]] == expected, "migraties ontbreken"
            assert migration_history(info)[:len(old_history)] == old_history, "migratiehistorie wijkt af"
            archives = [row for row in api_data(client, "backups")["items"] if row["kind"] == "pre-upgrade"]
            assert len(archives) == 1 and archives[0]["schema_version"] == 501 and archives[0]["present"], (
                "verwacht één aanwezig pre-upgrade-archief van schema 501"
            )
        archive = archives[0]
        logs = container_logs()
        marker = "back-up vóór upgrade gemaakt: " + archive["file"]
        first_migration = next(row for row in expected if row[0] > 501)
        applied = f"migratie {first_migration[0]:04d}_{first_migration[1]} toegepast"
        assert 0 <= logs.find(marker) < logs.find(applied), "het archief moet klaar zijn vóór de eerste migratie"
        stop_cleanly()
        record(UPGRADE_SCENARIOS[0], True, f"501 → {info['schema_version']}; {len(expected)} migraties; "
               "archief vooraf; gebruiker, koppeling, voorstel en inlog behouden")
        scenario = 1

        restored = tmp / "restored"
        restored.mkdir()
        filename = archive["file"]
        assert filename == Path(filename).name and filename.endswith("-pre-upgrade.dump"), "ongeldige archiefnaam"
        restore_pre_upgrade(restored, data / "backups", filename, archive["sha256"])
        run(restored, image=PREVIOUS_IMAGE)
        assert wait_healthy() == "healthy", "1.5.0 startte niet na herstel"
        with closing(login()[0]) as client:
            restored_info = api_data(client, "system/info")
            assert migration_history(restored_info) == old_history, "de herstelde migratiehistorie wijkt af"
            assert restored_info["schema_version"] == 501, "het herstel kwam niet terug op schema 501"
            assert upgrade_data(client, change_id) == original, "proefgegevens wijzigden bij herstel"
        stop_cleanly()
        record(UPGRADE_SCENARIOS[1], True, "SHA-256 gecontroleerd; schema 501; "
               "gebruiker, koppeling, voorstel en inlog hersteld")
        scenario = 2

        guard = os.environ.get("MSO_SCHEMA_GUARD_IMAGE", "")
        if not guard:
            record(UPGRADE_SCENARIOS[2], None, "OVERGESLAGEN: bouw eerst de gepinde image met build-schema-guard.sh")
            return
        metadata = json.loads(sh("docker", "image", "inspect", guard))[0]
        assert guard == metadata["Id"] and metadata["Config"]["Labels"].get(
            "org.opencontainers.image.revision"
        ) == GUARD_REVISION, "de weigerimage moet op image-ID en de vaste broncommit gepind zijn"
        record(UPGRADE_SCENARIOS[2], True, verify_schema_refusal(data, guard, info["schema_version"]))
    except (AssertionError, RuntimeError, httpx.HTTPError, subprocess.CalledProcessError) as exc:
        record(UPGRADE_SCENARIOS[scenario], False, str(exc))
        for name in UPGRADE_SCENARIOS[scenario + 1:]:
            record(name, None, "OVERGESLAGEN: een voorgaande upgrade- of herstelproef faalde")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mso-aio-"))
    data = tmp / "appdata"
    data.mkdir()
    os.chmod(data, 0o755)
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
        d2.mkdir()
        # Gebruik de UID van de lokale Docker-runtime voor de tijdelijke testmap.
        sh("docker", "run", "--rm", "--network", "none", "-v", f"{d2}:/d",
           "--entrypoint", "chown", IMAGE, "568:568", "/d")
        # Lokale GET-controles blijven bereikbaar met een onbruikbare proxy in de omgeving.
        run(d2, "--user", "568:568", "-e", "http_proxy=http://localhost:1", "-e", "no_proxy=")
        st = wait_healthy()
        record("7 --user 568 op map van 568 → gezond", st == "healthy", f"health={st}")
        user_ok, user_details = privilege_probe(root_start=False)
        record("13 procesboom en healthcheck bij beide startmodi", root_ok and user_ok,
               f"root-start: {root_details}; gebruiker 568: {user_details}")

        # 8
        d3 = tmp / "appdata-root"
        d3.mkdir()
        os.chmod(d3, 0o755)
        run(d3, "--user", "568:568")
        time.sleep(8)
        log8 = subprocess.run(["docker", "logs", NAME], capture_output=True, text=True, check=False)
        msg = [ln for ln in (log8.stdout + log8.stderr).splitlines() if "FOUT" in ln]
        record("8 --user 568 op root-map → duidelijke foutmelding", bool(msg), msg[0] if msg else "geen melding")
        previous_image_upgrade(tmp)
    finally:
        sh("docker", "rm", "-f", NAME, MOCK_NAME, check=False)
        sh("docker", "network", "rm", NET, check=False)
        sh("docker", "run", "--rm", "-v", f"{tmp}:/t", "--entrypoint", "sh", IMAGE, "-c", "rm -rf /t/*", check=False)
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
    lines += [f"| {n} | {labels[o]} | {d.replace('|', '/')} |" for n, o, d in RESULTS]
    (out / "allinone-report.md").write_text("\n".join(lines) + "\n")
    (out / "allinone-report.json").write_text(
        json.dumps([{"scenario": n, "ok": o, "detail": d} for n, o, d in RESULTS], indent=2, ensure_ascii=False)
    )
    print(f"Alles-in-één: {passed}/{SCENARIO_COUNT} scenario's groen, {skipped} overgeslagen", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
