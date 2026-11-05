"""Database backups (pg_dump custom format) and restore, with integrity check.

- Secrets are excluded by default (``--exclude-table-data=secrets``); only an explicit admin opt-in
  includes them. The vault key file is never part of a dump.
- Sessions are never backed up.
- Every dump gets a SHA-256 sidecar; restore refuses a file whose checksum or archive TOC is invalid
  (corrupt/missing backup) and keeps the current database intact.
- Restore runs ``pg_restore --clean --if-exists --single-transaction``: all-or-nothing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from mso import jobs
from mso.audit import audit
from mso.config import get_settings
from mso.db import get_db, jsonb, schema_version
from mso.settings_registry import get as setting

LOGGER = logging.getLogger(__name__)


class BackupError(RuntimeError):
    pass


def _bin(name: str) -> str:
    explicit = os.environ.get("MSO_PG_BIN")
    if explicit:
        return str(Path(explicit) / name)
    found = shutil.which(name)
    if found:
        return found
    for candidate in sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"), reverse=True):
        return str(candidate)
    raise BackupError(f"{name} niet gevonden")


def _pg_env() -> tuple[list[str], dict[str, str]]:
    url = urlsplit(get_settings().database_url)
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = unquote(url.password)
    # libpq URLs may name a unix-socket directory as ?host=/path (all-in-one image: no TCP at all)
    host = parse_qs(url.query).get("host", [None])[0] or url.hostname or "localhost"
    args = ["-h", host, "-p", str(url.port or 5432), "-U", unquote(url.username or "postgres")]
    return args, env | {"PGDATABASE": url.path.lstrip("/") or "postgres"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(*, include_secrets: bool = False, kind: str = "manual", actor: str = "system") -> dict:
    target_dir = get_settings().backup_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = target_dir / f"mso-{stamp}-{kind}.dump"
    args, env = _pg_env()
    cmd = [_bin("pg_dump"), *args, "-Fc", "--no-owner", "--no-privileges", "--exclude-table-data=sessions",
           "-f", str(path)]
    if not include_secrets:
        cmd.append("--exclude-table-data=secrets")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800, check=False)  # noqa: S603
    if proc.returncode != 0:
        path.unlink(missing_ok=True)
        raise BackupError(f"pg_dump mislukt: {proc.stderr.strip()[:400]}")
    os.chmod(path, 0o600)
    digest = sha256_file(path)
    path.with_suffix(".sha256").write_text(f"{digest}  {path.name}\n")
    row = get_db().fetchone(
        "INSERT INTO backups(file,kind,size_bytes,sha256,includes_secrets,schema_version) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
        (path.name, kind, path.stat().st_size, digest, include_secrets, schema_version(get_db())))
    audit(actor, "backup.created", f"Back-up gemaakt ({path.name})",
          detail={"include_secrets": include_secrets, "size": path.stat().st_size})
    prune(int(setting("backup.keep")))
    return dict(row)


def verify_backup(path: Path, expected_sha: str | None) -> None:
    if not path.exists():
        raise BackupError("Back-upbestand ontbreekt")
    if path.stat().st_size == 0:
        raise BackupError("Back-upbestand is leeg")
    if expected_sha and sha256_file(path) != expected_sha:
        raise BackupError("Checksum klopt niet: back-up is beschadigd")
    args, env = _pg_env()
    proc = subprocess.run([_bin("pg_restore"), "--list", str(path)], env=env, capture_output=True, text=True,  # noqa: S603
                          timeout=120, check=False)
    if proc.returncode != 0:
        raise BackupError("Back-up is geen geldig pg_dump-archief (beschadigd)")


def restore_backup(backup_id: int, actor: str) -> dict:
    db = get_db()
    row = db.fetchone("SELECT * FROM backups WHERE id=%s", (backup_id,))
    if row is None:
        raise BackupError("Back-up niet gevonden")
    path = get_settings().backup_dir / row["file"]
    verify_backup(path, row["sha256"])
    # safety net: dump the current state first so a restore itself can be undone
    pre = create_backup(kind="pre-restore", actor=actor)
    backups_rows = db.fetchall("SELECT * FROM backups")
    # the audit trail is append-only evidence: entries written after the dump must survive the restore
    audit_rows = db.fetchall("SELECT * FROM audit_log")
    args, env = _pg_env()
    cmd = [_bin("pg_restore"), *args, "-d", env["PGDATABASE"], "--clean", "--if-exists", "--no-owner",
           "--no-privileges", "--single-transaction", str(path)]
    # a dump without secrets would leave the vault empty after --clean: keep the current (encrypted) rows
    kept_secrets = [] if row["includes_secrets"] else db.fetchall("SELECT * FROM secrets")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800, check=False)  # noqa: S603
    db.pool.check()  # drop pooled connections that the restore may have invalidated
    if proc.returncode != 0:
        raise BackupError(f"Herstel mislukt (database ongewijzigd): {proc.stderr.strip()[:400]}")
    # the backups table was replaced by the dump's copy — re-register files known before the restore
    for b in backups_rows:
        db.execute("INSERT INTO backups(id,file,kind,size_bytes,sha256,includes_secrets,schema_version,created_at) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                   (b["id"], b["file"], b["kind"], b["size_bytes"], b["sha256"], b["includes_secrets"],
                    b["schema_version"], b["created_at"]))
    db.execute("SELECT setval(pg_get_serial_sequence('backups','id'), (SELECT max(id) FROM backups))")
    for a in audit_rows:
        db.execute("INSERT INTO audit_log(id,ts,actor,action,target,summary,detail,change_id) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                   (a["id"], a["ts"], a["actor"], a["action"], a["target"], a["summary"], jsonb(a["detail"]),
                    a["change_id"]))
    db.execute("SELECT setval(pg_get_serial_sequence('audit_log','id'), (SELECT max(id) FROM audit_log))")
    # jobs that were queued/running in the dump belong to the past (e.g. the backup job that produced this very
    # dump): never re-run them after a restore
    cancelled = db.execute(
        "UPDATE jobs SET status='cancelled', locked_by=NULL, updated_at=now(), "
        "summary=COALESCE(summary,'') || ' · geannuleerd door herstel van een back-up' "
        "WHERE status IN ('queued','running')")
    for sec in kept_secrets:
        db.execute("INSERT INTO secrets(name,nonce,ciphertext,updated_at) VALUES (%s,%s,%s,%s) "
                   "ON CONFLICT (name) DO NOTHING", (sec["name"], sec["nonce"], sec["ciphertext"], sec["updated_at"]))
    audit(actor, "backup.restored", f"Back-up {row['file']} hersteld",
          detail={"pre_restore_backup": pre["file"], "jobs_cancelled": cancelled,
                  "audit_entries_kept": len(audit_rows)})
    return {"restored": row["file"], "pre_restore_backup": pre["file"]}


def prune(keep: int) -> int:
    db = get_db()
    rows = db.fetchall("SELECT * FROM backups WHERE kind IN ('auto','manual') ORDER BY created_at DESC")
    removed = 0
    for row in rows[keep:]:
        path = get_settings().backup_dir / row["file"]
        path.unlink(missing_ok=True)
        path.with_suffix(".sha256").unlink(missing_ok=True)
        db.execute("DELETE FROM backups WHERE id=%s", (row["id"],))
        removed += 1
    return removed


def list_backups() -> list[dict]:
    rows = get_db().fetchall("SELECT * FROM backups ORDER BY created_at DESC")
    out = []
    for r in rows:
        present = (get_settings().backup_dir / r["file"]).exists()
        out.append(dict(r) | {"present": present})
    return out


@jobs.handler("backup.create")
def _job_backup(ctx: jobs.JobContext) -> dict:
    row = create_backup(include_secrets=bool(ctx.payload.get("include_secrets")),
                        kind=ctx.payload.get("kind", "manual"), actor=ctx.payload.get("actor", "worker"))
    return {"file": row["file"], "size_bytes": row["size_bytes"]}


jobs.every("backup.create", 24 * 3600, {"kind": "auto", "actor": "scheduler"},
           enabled=lambda: bool(setting("backup.auto_daily")))
