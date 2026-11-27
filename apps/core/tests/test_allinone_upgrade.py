"""Regressieproeven voor de diagnostiek bij upgrades vanaf gepubliceerde images."""

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

STACK_TEST = Path(__file__).resolve().parents[3] / "tests/stack/allinone_test.py"
FILENAME = "test-pre-upgrade.dump"
MARKER = f"back-up vóór upgrade gemaakt: {FILENAME}"


def test_docker_run_bewaart_de_hele_fout_zonder_proefgeheimen(monkeypatch):
    scenario = runpy.run_path(str(STACK_TEST))
    cause = "docker: Error response from daemon: proefvolume ontbreekt"
    tail = "Run 'docker run --help' for more information"
    private = scenario["SAB_KEY"]
    password = scenario["ADMIN_PW"]

    def refused(command, **kwargs):
        return subprocess.CompletedProcess(command, 125, stdout=password,
                                           stderr=f"{cause}\n{private} {password}\n\n{tail}\n")

    monkeypatch.setattr(subprocess, "run", refused)
    with pytest.raises(RuntimeError) as error:
        scenario["sh"]("docker", "run", "--env", private)
    assert str(error.value) == f"docker run exitcode 125: {cause}\n[afgeschermd] [afgeschermd]\n\n{tail}"


def test_geweigerde_testmap_telt_als_mislukte_upgrade(monkeypatch):
    monkeypatch.setenv("MSO_AIO_DATA_VOLUME", "proefvolume")
    scenario = runpy.run_path(str(STACK_TEST))
    upgrade, rollback = scenario["UPGRADE_130"]

    def docker(command, **kwargs):
        if command[:2] == ("docker", "pull"):
            return subprocess.CompletedProcess(command, 0, "", "")
        assert command[:2] == ("docker", "run") and "--entrypoint" in command and "mkdir" in command
        return subprocess.CompletedProcess(command, 125, "", "proefvolume geweigerd")

    monkeypatch.setattr(subprocess, "run", docker)
    result = scenario["published_image_upgrade"](Path("published-1.3.0"), "1.3.0", scenario["PUBLISHED_130"], 500,
                                                 (upgrade, rollback))
    assert result is None
    assert scenario["RESULTS"] == [
        (upgrade, False, "docker run exitcode 125: proefvolume geweigerd"),
        (rollback, None, "OVERGESLAGEN: een voorgaande upgrade- of herstelproef faalde"),
    ]


@pytest.mark.parametrize("first_migration", [(202, "usenet_missing_credentials"), (600, "v160_base"),
                                             (608, "machine_keys")], ids=["1.3.0", "1.5.0", "1.6.0"])
@pytest.mark.parametrize("migration_first", [True, False], ids=["weigert-omgekeerd", "accepteert-correct"])
def test_backup_moet_voor_eerste_migratie_staan(monkeypatch, migration_first, first_migration):
    scenario = runpy.run_path(str(STACK_TEST))
    number, name = first_migration
    applied = f"migratie {number:04d}_{name} toegepast"
    backup = "print(sys.argv[1], file=sys.stdout, flush=True)"
    migration = "print(sys.argv[2], file=sys.stderr, flush=True)"
    lines = ["import sys", backup, migration]
    if migration_first:
        lines[1:] = [migration, backup]
    run = subprocess.run

    def docker_logs(command, **kwargs):
        assert command == ["docker", "logs", scenario["NAME"]]
        return run([sys.executable, "-c", "\n".join(lines), MARKER, applied], **kwargs)

    monkeypatch.setattr(subprocess, "run", docker_logs)
    logs = scenario["container_logs"]()
    if migration_first:
        with pytest.raises(AssertionError, match="het archief moet klaar zijn vóór de eerste migratie"):
            scenario["assert_backup_before_migration"](logs, FILENAME, first_migration)
    else:
        scenario["assert_backup_before_migration"](logs, FILENAME, first_migration)
