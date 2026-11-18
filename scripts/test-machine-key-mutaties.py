"""Voer gerichte beveiligingsmutaties uit zonder productbestanden te wijzigen."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "centrale_scopegrens": ("test_all_writes_denied", "m.setattr(keys, 'authorize', lambda *args: None)"),
    "corrupte_scopes": ("test_corrupt_scopes_fail_closed", """def broken(value):
            if not isinstance(value,list):
                return None
            return frozenset(s for s in value if isinstance(s,str) and s in keys.SCOPES)
        m.setattr(keys, 'scopes_or_none', broken)"""),
    "rolvrij": ("test_role_free_and_internal_admin_denied", "m.setattr(auth.Principal, 'can', lambda *args: True)"),
    "geheimopslag": ("test_secret_never_returned_or_logged", "m.setattr(keys, 'token_hash', lambda value: value)"),
    "intrek_wedloop": ("test_revoke_during_request", "m.setattr(keys, 'check_active', lambda *args: True)"),
    "gebruikstijd": ("test_last_used_does_not_wait_for_locked_row", """def broken(*args):
            raise RuntimeError('Telemetrie onderbreekt lezen')
        m.setattr(keys, 'touch', broken)"""),
    "begrenzing": ("test_rate_limit_shared_by_processes", """def broken(*args):
            raise HTTPException(401, 'Geweigerd')
        m.setattr(keys, '_failed', broken)"""),
    "migratiebehoud": ("test_migration_roundtrip_preserves_users_and_sessions", """original = database.migrate_down
        def broken(db, target):
            result = original(db, target)
            db.execute('DELETE FROM sessions')
            return result
        m.setattr(database, 'migrate_down', broken)"""),
    "sessiebehoud": ("test_existing_sessions", "m.setattr(auth, 'principal_from_request', lambda *args: None)"),
    "neveneffect_get": ("test_known_sensitive_and_side_effect_gets_stay_closed",
                       "m.setattr(keys, 'READ_ROUTES', keys.READ_ROUTES | {'/api/v1/costs/services'})"),
    "redactielaag": ("test_redaction_layer_on_machine_secret", """from mso import security
        m.setattr(security, 'redact', lambda value: value)"""),
    "hashvergelijking": ("test_hash_comparison_and_no_cookie_fallback",
                        "m.setattr(keys.hmac, 'compare_digest', lambda *args: True)"),
    "interne_beheerweg": ("test_internal_key_management_denied", "m.setattr(keys, 'admin_only', lambda p: p)"),
    "nieuwer_schema": ("test_newer_schema_refused_after_machine_migration",
                      "m.setattr(database, '_check_schema_compatibility', lambda *args: None)"),
    "intrek_binding": ("test_revoke_idempotency_and_contract_errors",
                      "m.setattr(keys, 'token_hash', lambda value: '0' * 64)"),
    "herstel_grens": ("test_backup_restore_never_reactivates_revoked_or_old_scopes",
                      "m.setattr(keys, 'restore_security_snapshot', lambda *args: None)"),
    "herstel_wedloop": ("test_revoke_during_backup_restore_is_not_lost", """import contextlib
        m.setattr(keys, 'administration_lock', lambda *args: contextlib.nullcontext())"""),
    "herstel_leesvenster": ("test_machine_reads_closed_during_restore", """def broken(key_id):
            query = 'SELECT id,key_hash,scopes,revoked_at FROM machine_keys WHERE id=%s'
            return database.get_db().fetchone(query, (key_id,))
        m.setattr(keys, '_read_key', broken)"""),
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mso-mutaties-", dir=os.environ.get("TMPDIR")) as work:
        for name, (test, mutation) in MUTATIONS.items():
            Path(work, "mso_mutant.py").write_text('''import pytest
from fastapi import HTTPException
from mso import auth, db as database, machine_keys as keys

@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    with pytest.MonkeyPatch.context() as m:
        ''' + mutation + '''
        yield
''')
            env = dict(os.environ, PYTHONPATH=work + os.pathsep + str(ROOT / "apps/core"))
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "--tb=no",
                                     "-p", "no:warnings", "-p", "mso_mutant",
                                     f"tests/test_machine_keys.py::{test}"],
                                    cwd=ROOT / "apps/core", env=env, capture_output=True, text=True, timeout=180)
            summaries = [s for s in result.stdout.splitlines() if " failed" in s or " passed" in s]
            summary = summaries[-1] if summaries else "geen testslotregel"
            print(f"{name}: exitcode {result.returncode}; {summary}", flush=True)
            if result.returncode != 1 or " failed" not in summary:
                print("MUTATIE NIET BEWEZEN: stop", flush=True)
                return 1
    print(f"{len(MUTATIONS)} mutaties rood bevestigd; productbestanden ongewijzigd", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
