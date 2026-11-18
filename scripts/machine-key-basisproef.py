"""Meet basiscode op schema 0608 in een eigen tijdelijke MSO-database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg
from mso.auth import Principal
from mso.config import Settings, set_settings
from mso.db import Database, migrate_up, set_db
from mso.machine_keys import MachineKeyCreate, create_key
from psycopg import sql


def snapshot(db: Database) -> str:
    values = {}
    for row in db.fetchall("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"):
        name = row["tablename"]
        values[name] = db.fetchall(sql.SQL("SELECT to_jsonb(t) AS row FROM {} t ORDER BY to_jsonb(t)::text")
                                  .format(sql.Identifier(name)))
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--basis", type=Path, required=True)
    args = parser.parse_args()
    base = args.basis.resolve()
    if not (base / "mso/auth.py").is_file() or (base / "mso/migrations/0608_machine_keys.sql").exists():
        raise RuntimeError("Geef de uitgepakte, ongewijzigde basiscode tot en met 0607 op")
    server = os.environ["MSO_TEST_DATABASE_URL"]
    name = "mso_s_p3_220_basis_" + uuid.uuid4().hex[:8]
    url = server.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db = Database(url)
    try:
        with tempfile.TemporaryDirectory(prefix="mso-basis-", dir=os.environ.get("TMPDIR")) as work:
            set_settings(Settings(database_url=url, data_dir=Path(work), work_dir=Path(work), testing=True))
            set_db(db)
            migrate_up(db)
            create_key(MachineKeyCreate(name="Basisproef", scopes=["read"]), Principal("proefbeheerder", "admin"))
            before = snapshot(db)
            env = dict(os.environ, PYTHONPATH=str(base), MSO_DATABASE_URL=url,
                       MSO_DATA_DIR=work, MSO_WORK_DIR=work)
            for command in ["migrate", "api", "worker"]:
                result = subprocess.run([sys.executable, "-m", "mso", command], cwd=base, env=env,
                                        text=True, capture_output=True, timeout=20)
                assert result.returncode == 79, command
                assert "database is van een nieuwere MSO-versie" in result.stderr
                assert snapshot(db) == before
                print(f"basis {command}: exitcode 79; schema 0608 geweigerd; database ongewijzigd", flush=True)
            code = '''from starlette.requests import Request
from mso.auth import principal_from_request
request=Request({'type':'http','headers':[(b'x-api-key',b'DIT-IS-GEEN-SLEUTEL')]})
assert principal_from_request(request) is None
print('basis-auth: machineheader niet herkend; geen principal')
'''
            result = subprocess.run([sys.executable, "-c", code], cwd=base, env=env,
                                    text=True, capture_output=True, timeout=20)
            assert result.returncode == 0
            print(result.stdout.strip())
            print("Basisproef: nieuw schema geweigerd; machine-sleutel werkt niet; niets verloren", flush=True)
    finally:
        db.close()
        set_db(None)
        with psycopg.connect(server, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
