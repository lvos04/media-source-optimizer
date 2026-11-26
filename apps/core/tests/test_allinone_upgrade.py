"""Regressieproef voor de logvolgorde in het vorige-image-scenario."""

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

STACK_TEST = Path(__file__).resolve().parents[3] / "tests/stack/allinone_test.py"
FILENAME = "test-pre-upgrade.dump"
MARKER = f"back-up vóór upgrade gemaakt: {FILENAME}"


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
