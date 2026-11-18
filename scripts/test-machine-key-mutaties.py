"""Voer gerichte beveiligingsmutaties uit zonder productbestanden te wijzigen."""

from __future__ import annotations

import os
import shutil
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
    "migratiebehoud": ("test_migration_roundtrip_preserves_existing_tables", """original = database.migrate_down
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

SOURCE_MUTATIONS = {
    "M2_tweede_routecontrole": (
        "test_machine_keys.py::test_principal_checks_route_without_middleware", "auth.py",
        "        authorize(request, principal)\n        return principal", "        return principal",
    ),
    "M5_force_hoofdletters": (
        "test_machine_keys.py::test_duplicate_headers_and_uppercase_force_are_denied", "machine_keys.py",
        'any(k.lower() == "force" for k in request.query_params)',
        'any(k == "force" for k in request.query_params)',
    ),
    "M6_dubbele_koppen": (
        "test_machine_keys.py::test_duplicate_headers_and_uppercase_force_are_denied", "machine_keys.py",
        'raw = values[0] if len(values) == 1 else ""', 'raw = values[0] if values else ""',
    ),
    "M10_operator_beheert_sleutels": (
        "test_machine_keys.py::test_only_admin_can_manage_keys_and_each_operation_is_audited", "machine_keys.py",
        'if p.machine or not p.can("admin"):', 'if p.machine or not p.can("operator"):',
    ),
    "M12_rolcontrole_alleen_get": (
        "test_machine_key_sessions.py::test_existing_successful_writes_against_base", "auth.py",
        "        if not principal.can(role):",
        '        if not principal.can(role) and (request is None or request.method == "GET"):',
    ),
    "M14_hercontrole_ook_bij_fouten": (
        "test_machine_keys.py::test_revoke_during_request[True]", "machine_keys.py",
        "                if not await run_in_threadpool(check_active, principal.machine_key_id, principal.scopes):",
        "                if message['status'] < 400 and not await run_in_threadpool("
        "check_active, principal.machine_key_id, principal.scopes):",
    ),
    "M15_audit_mislukte_aanmelding": (
        "test_machine_key_limits.py::test_machine_failures_do_not_block_human_or_valid_machine_key", "machine_keys.py",
        '''            conn.execute("INSERT INTO audit_log(actor,action,target,summary,detail) VALUES (%s,%s,%s,%s,%s)",
                         ("machine", action, str(uuid.UUID(key_id)) if key_id else None, summary,
                          jsonb({"remote": redact(remote)})))''',
        '''            if wait:
                conn.execute("INSERT INTO audit_log(actor,action,target,summary,detail) VALUES (%s,%s,%s,%s,%s)",
                             ("machine", action, str(uuid.UUID(key_id)) if key_id else None, summary,
                              jsonb({"remote": redact(remote)})))''',
    ),
    "M16_mislukt_herstel_bewaart_sleutels": (
        "test_machine_key_restore.py::test_failed_restore_immediately_preserves_current_keys", "backups.py",
        "        except BaseException:\n            _recover_machine_keys(db)\n            raise",
        "        except BaseException:\n            raise",
    ),
    "M17_aanmaak_zonder_audit": (
        "test_machine_keys.py::test_only_admin_can_manage_keys_and_each_operation_is_audited", "machine_keys.py",
        '''            conn.execute("INSERT INTO audit_log(actor,action,target,summary,detail) VALUES (%s,%s,%s,%s,%s)",
                         (p.actor, "machine_keys.create", str(key_id), "Machine-sleutel aangemaakt.", jsonb({})))''',
        "",
    ),
    "foutvorm_bestaande_api": (
        "test_machine_key_sessions.py::test_existing_successful_writes_against_base", "app.py",
        'body = {"detail": detail, "code": code, "correlation_id": cid}',
        'body = {"detail": detail, "code": code, "correlation_id": cid, "extraveld": True}',
    ),
    "middleware_bestaande_sessie": (
        "test_machine_key_sessions.py::test_existing_successful_writes_against_base", "app.py",
        "        machine = has_machine_header(request)",
        """        machine = has_machine_header(request)
        if not machine and path == "/api/v1/system/info":
            return _error(503, "Onderbroken", "unavailable", request)""",
    ),
    "migratie_0608_wijzigt_audit": (
        "test_machine_keys.py::test_migration_roundtrip_preserves_existing_tables", "migrations/0608_machine_keys.sql",
        "-- migrate:up", "-- migrate:up\nUPDATE audit_log SET summary='overschreven';",
    ),
}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mso-mutaties-", dir=os.environ.get("TMPDIR")) as work:
        package = shutil.copytree(
            ROOT / "apps/core/mso", Path(work, "mso"), ignore=shutil.ignore_patterns("__pycache__"),
        )
        names = sys.argv[1:] or [*MUTATIONS, *SOURCE_MUTATIONS]
        for name in names:
            target = None
            args = ["-q", "-o", "addopts=", "--tb=no", "-p", "no:warnings"]
            if name in SOURCE_MUTATIONS:
                node, filename, before, after = SOURCE_MUTATIONS[name]
                target = package / filename
                original = target.read_text(encoding="utf-8")
                if original.count(before) != 1:
                    print(f"{name}: mutatieplek ontbreekt of is niet uniek", flush=True)
                    return 1
                target.write_text(original.replace(before, after), encoding="utf-8")
            else:
                test, mutation = MUTATIONS[name]
                node = f"test_machine_keys.py::{test}"
                args.extend(["-p", "mso_mutant"])
                Path(work, "mso_mutant.py").write_text('''import pytest
from fastapi import HTTPException
from mso import auth, db as database, machine_keys as keys

@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    with pytest.MonkeyPatch.context() as m:
        ''' + mutation + '''
        yield
''')
            args.append(f"tests/{node}")
            env = dict(os.environ, PYTHONPATH=work + os.pathsep + str(ROOT / "apps/core"), PYTHONDONTWRITEBYTECODE="1")
            try:
                # Laad de wegwerpkopie vóór pytest de repo aan zijn zoekpad toevoegt.
                runner = "import sys; sys.path.pop(0); import mso, pytest; sys.exit(pytest.main(sys.argv[1:]))"
                result = subprocess.run([sys.executable, "-c", runner, *args], cwd=ROOT / "apps/core",
                                        env=env, capture_output=True, text=True, timeout=180)
            finally:
                if target is not None:
                    target.write_text(original, encoding="utf-8")
            summaries = [s for s in result.stdout.splitlines() if " failed" in s or " passed" in s]
            summary = summaries[-1] if summaries else "geen testslotregel"
            print(f"{name}: exitcode {result.returncode}; {summary}", flush=True)
            if result.returncode != 1 or " failed" not in summary:
                print("MUTATIE NIET BEWEZEN: stop", flush=True)
                return 1
    print(f"{len(names)} mutaties rood bevestigd; productbestanden ongewijzigd", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
