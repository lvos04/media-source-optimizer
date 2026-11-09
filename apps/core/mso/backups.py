"""Database backups (pg_dump custom format) and restore, with integrity check.

- Secrets are excluded by default (``--exclude-table-data=secrets``); only an explicit admin opt-in
  includes them. The vault key file is never part of a dump.
- Sessions are never backed up, and neither is data MSO fetches again by itself (``REFETCHABLE``: search-result
  cache, MediaGuard cache/snapshots, header index, short-term torrent samples): smaller, faster dumps. After a
  restore those are refilled automatically (``resync_after_restore``).
- Every dump gets a SHA-256 sidecar; restore refuses a file whose checksum or archive TOC is invalid
  (corrupt/missing backup) and keeps the current database intact.
- Restore runs ``pg_restore --clean --if-exists --single-transaction``: all-or-nothing. Audit entries written after
  the dump survive (copied server-side, never loaded into memory).
- A dump from an OLDER schema (e.g. the ``pre-upgrade`` dump of the previous version): the current schema is first
  taken down to the dump's version with this image's down scripts (``--clean`` only drops what the archive holds,
  so tables of newer migrations would survive and make the next ``migrate`` fail), then restored, then migrated up
  again right away so the running code matches its schema. A dump from a NEWER schema is refused.
- The operating state is not part of a restore: mode, pause and the noodrem (``KEEP_STATE``) stay as they were, so
  a restore never lifts a hold nor switches Alleen-lezen back to Autopilot. A change that was being applied when the
  dump was taken (``applying``) is settled as failed without touching any source (one "Wacht op jou" item): the
  reconciler must not take a dump-era journal for a fresh crash.
- Before a dump the free space is checked (2× the previous dump + the disk reserve). Too little space: no dump, one
  clear "Wacht op jou" item instead of a half-written file.
- Pruning: ``auto``/``manual`` keep ``backup.keep``; safety dumps before a restore (``pre-restore``) are removed after
  30 days (the newest 2 always stay); dumps before an upgrade (``pre-upgrade``) keep the newest 3, and the dump of
  an upgrade that has not finished yet (a failing migration in a restart loop) is never pruned.
- ``pre_upgrade_backup`` runs before pending migrations (all-in-one supervisor, ``mso migrate``, API/worker start).
  It runs new code against the OLD schema, so it only touches 0001-era tables/columns (``backups``,
  ``system_state``); the audit row and any "Wacht op jou" item are written after the migration
  (``flush_pre_upgrade``). It never raises: a failed dump is a clear log line and the start continues.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from mso import jobs
from mso.audit import audit
from mso.config import get_settings
from mso.db import (
    Database,
    applied_migrations,
    get_db,
    jsonb,
    load_migrations,
    migrate_down,
    migrate_up,
    schema_version,
)
from mso.security import redact
from mso.settings_registry import get as setting

LOGGER = logging.getLogger(__name__)

# data MSO fetches again by itself (checked against the migrations: 0100, 0200, 0300, 0400)
REFETCHABLE = ("release_cache", "mg_wanted_cache", "mg_snapshots", "usenet_hi_headers", "torrent_samples",
               "torrent_client_snapshot")
AUDIT_KEEP_TABLE = "mso_restore_audit_keep"  # temporary, server-side copy of audit_log during a restore
SAFETY_KIND, SAFETY_DAYS, SAFETY_KEEP = "pre-restore", 30, 2
UPGRADE_KIND, UPGRADE_KEEP = "pre-upgrade", 3
PRE_UPGRADE_LOCK = 727002  # advisory lock: api, worker and `mso migrate` never dump twice for one upgrade
PRE_UPGRADE_STATE = "backup.pre_upgrade_pending"
SPACE_ALERT_KEY = "backup.space"
# the operating state belongs to now, not to the dump: never lift a noodrem or leave Alleen-lezen through a restore
KEEP_STATE = ("mode", "paused", "autopilot_hold")
RESTORED_APPLYING_ERROR = ("Hersteld uit een back-up terwijl deze wijziging werd toegepast; MSO heeft niets "
                           "teruggezet — controleer de instellingen bij de bron")
# jobs that refill the refetchable tables after a restore (only those whose handler is loaded are queued);
# (job kind, payload, connector it reads from): only queued when that connector is configured, so a restore on a
# usenet-only install never leaves a failed 'torrent.ingest' behind
RESYNC_JOBS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("mediaguard.sync", {"force": True, "actor": "herstel"}, "mediaguard"),
    ("torrent.ingest", {}, "qbittorrent"),
)


class BackupError(RuntimeError):
    pass


class BackupSkipped(BackupError):
    """Not enough free space for a safe dump (nothing was written)."""


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


def _gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB" if n >= 1024**3 else f"{n / 1024**2:.0f} MB"


# ------------------------------------------------------------------ free space
def space_status(db: Database | None = None) -> dict[str, Any]:
    """Free space in the backup folder vs. what a safe dump needs: 2× the previous dump (or the database size when
    there is none yet) plus the disk reserve (``MSO_DISK_RESERVE_GB``)."""
    db = db or get_db()
    settings = get_settings()
    target = settings.backup_dir
    probe = target if target.exists() else target.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    last = db.scalar("SELECT size_bytes FROM backups WHERE size_bytes > 0 ORDER BY created_at DESC LIMIT 1")
    if last:
        reference, basis = int(last), "vorige back-up"
    else:
        reference, basis = int(db.scalar("SELECT pg_database_size(current_database())") or 0), "databasegrootte"
    reserve = int(settings.disk_reserve_gb * 1024**3)
    needed = 2 * reference + reserve
    return {"path": str(target), "free_bytes": free, "needed_bytes": needed, "reference_bytes": reference,
            "reference": basis, "reserve_bytes": reserve, "ok": free is None or free >= needed}


def _space_message(st: dict[str, Any]) -> str:
    return (f"Te weinig vrije schijfruimte voor een veilige back-up: {_gb(st['free_bytes'] or 0)} vrij, "
            f"{_gb(st['needed_bytes'])} nodig (2× de {st['reference']} van {_gb(st['reference_bytes'])} plus "
            f"{_gb(st['reserve_bytes'])} reserve)")


def _space_alert(st: dict[str, Any], kind: str, db: Database) -> None:
    """One "Wacht op jou" item (open recommendation with a person's decision) — refreshed, never duplicated."""
    msg = _space_message(st)
    db.execute(
        "INSERT INTO recommendations(area,subject,title,explanation,impact,risk,confidence,dedupe_key,evidence) "
        "VALUES ('system','backups',%s,%s,%s,'toestemming_nodig',1.0,%s,%s) "
        "ON CONFLICT (dedupe_key) DO UPDATE SET title=EXCLUDED.title, explanation=EXCLUDED.explanation, "
        "evidence=EXCLUDED.evidence, status='open', updated_at=now()",
        ("Back-up overgeslagen: te weinig vrije schijfruimte",
         f"{msg}. Er is niets weggeschreven, dus er is ook geen half bestand. Maak ruimte vrij op de map "
         f"{st['path']} (of op de dataset eronder), of verlaag het aantal te bewaren back-ups onder Instellingen "
         "→ Systeem. MSO probeert het bij de volgende back-up vanzelf opnieuw.",
         "Zonder recente back-up gaan bij een defecte schijf of mislukte upgrade je meetgegevens en "
         "wijzigingsgeschiedenis verloren.",
         SPACE_ALERT_KEY, jsonb({"kind": kind, **{k: st[k] for k in ("free_bytes", "needed_bytes", "path")}})))


def _space_resolve(db: Database) -> None:
    db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
               "AND status='open'", (SPACE_ALERT_KEY,))


# ------------------------------------------------------------------ dump
def _dump(*, kind: str, include_secrets: bool, timeout: int = 1800) -> Path:
    target_dir = get_settings().backup_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = target_dir / f"mso-{stamp}-{kind}.dump"
    args, env = _pg_env()
    cmd = [_bin("pg_dump"), *args, "-Fc", "--no-owner", "--no-privileges", "--exclude-table-data=sessions",
           f"--exclude-table={AUDIT_KEEP_TABLE}", "-f", str(path)]
    cmd += [f"--exclude-table-data={t}" for t in REFETCHABLE]
    if not include_secrets:
        cmd.append("--exclude-table-data=secrets")
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        path.unlink(missing_ok=True)
        raise BackupError(f"pg_dump duurde langer dan {timeout} s en is gestopt") from exc
    if proc.returncode != 0:
        path.unlink(missing_ok=True)
        raise BackupError(f"pg_dump mislukt: {redact(proc.stderr.strip())[:400]}")
    os.chmod(path, 0o600)
    return path


def _register(path: Path, kind: str, include_secrets: bool, db: Database) -> dict[str, Any]:
    """Sidecar + ``backups`` row (0001-era columns only: also used against an old schema before an upgrade)."""
    digest = sha256_file(path)
    path.with_suffix(".sha256").write_text(f"{digest}  {path.name}\n")
    row = db.fetchone(
        "INSERT INTO backups(file,kind,size_bytes,sha256,includes_secrets,schema_version) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
        (path.name, kind, path.stat().st_size, digest, include_secrets, schema_version(db)))
    return dict(row)


def create_backup(*, include_secrets: bool = False, kind: str = "manual", actor: str = "system") -> dict:
    db = get_db()
    st = space_status(db)
    if not st["ok"]:
        msg = _space_message(st)
        _space_alert(st, kind, db)
        audit(actor, "backup.skipped", "Back-up overgeslagen: te weinig vrije schijfruimte",
              detail={"kind": kind, "free_bytes": st["free_bytes"], "needed_bytes": st["needed_bytes"]})
        raise BackupSkipped(msg)
    path = _dump(kind=kind, include_secrets=include_secrets)
    row = _register(path, kind, include_secrets, db)
    audit(actor, "backup.created", f"Back-up gemaakt ({path.name})",
          detail={"include_secrets": include_secrets, "size": path.stat().st_size, "kind": kind})
    _space_resolve(db)
    prune(int(setting("backup.keep")))
    return row


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


# ------------------------------------------------------------------ restore
def restore_backup(backup_id: int, actor: str) -> dict:
    db = get_db()
    row = db.fetchone("SELECT * FROM backups WHERE id=%s", (backup_id,))
    if row is None:
        raise BackupError("Back-up niet gevonden")
    path = get_settings().backup_dir / row["file"]
    verify_backup(path, row["sha256"])
    dump_v, cur_v = int(row["schema_version"] or 0), schema_version(db)
    if dump_v > cur_v:
        raise BackupError(f"Herstel geweigerd: deze back-up komt van een nieuwere MSO-versie (schema {dump_v}; deze "
                          f"versie kent {cur_v}). Herstel hem met die versie of nieuwer.")
    # safety net: dump the current state first so a restore itself can be undone
    try:
        pre = create_backup(kind=SAFETY_KIND, actor=actor)
    except BackupSkipped as exc:
        raise BackupError(f"Herstel geweigerd: er kan geen veiligheidsback-up worden gemaakt. {exc}") from exc
    backups_rows = db.fetchall("SELECT * FROM backups")
    kept_state = {r["key"]: r["value"] for r in db.fetchall(
        "SELECT key, value FROM system_state WHERE key = ANY(%s)", (list(KEEP_STATE),))}
    # the audit trail is append-only evidence: entries written after the dump must survive the restore. Copied
    # server-side into a table that is not part of any dump (``--exclude-table``), so --clean leaves it alone.
    db.execute(f"DROP TABLE IF EXISTS {AUDIT_KEEP_TABLE}")
    db.execute(f"CREATE UNLOGGED TABLE {AUDIT_KEEP_TABLE} AS SELECT * FROM audit_log")
    args, env = _pg_env()
    cmd = [_bin("pg_restore"), *args, "-d", env["PGDATABASE"], "--clean", "--if-exists", "--no-owner",
           "--no-privileges", "--single-transaction", str(path)]
    # a dump without secrets would leave the vault empty after --clean: keep the current (encrypted) rows
    kept_secrets = [] if row["includes_secrets"] else db.fetchall("SELECT * FROM secrets")
    # an older dump: take the schema down to its version first (this image has every down script), otherwise the
    # tables of newer migrations survive ``--clean`` and the next ``migrate`` fails on them (DuplicateTable)
    downgraded: list[int] = []
    if 0 < dump_v < cur_v:
        try:
            downgraded = migrate_down(db, dump_v)
        except Exception as exc:
            _back_to_head(db)
            db.execute(f"DROP TABLE IF EXISTS {AUDIT_KEEP_TABLE}")
            raise BackupError(f"Herstel mislukt: schema niet terug te zetten naar versie {dump_v} "
                              f"({redact(str(exc))[:300]}); de veiligheidsback-up {pre['file']} bevat de huidige "
                              "stand") from exc
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800, check=False)  # noqa: S603
    db.pool.check()  # drop pooled connections that the restore may have invalidated
    if proc.returncode != 0:
        if downgraded:
            _back_to_head(db)
        db.execute(f"DROP TABLE IF EXISTS {AUDIT_KEEP_TABLE}")
        if downgraded:
            raise BackupError(f"Herstel mislukt: {redact(proc.stderr.strip())[:400]}. Het schema is weer bijgewerkt; "
                              f"gegevens van nieuwere functies staan in de veiligheidsback-up {pre['file']}")
        raise BackupError(f"Herstel mislukt (database ongewijzigd): {redact(proc.stderr.strip())[:400]}")
    try:
        upgraded = migrate_up(db) if dump_v > 0 else []  # the running code needs its own schema right away
    except Exception as exc:
        raise BackupError(f"Back-up {row['file']} is hersteld, maar het schema bijwerken mislukte "
                          f"({redact(str(exc))[:300]}); herstart MSO (migrate). De veiligheidsback-up "
                          f"{pre['file']} bevat de stand van vóór het herstel") from exc
    # the backups table was replaced by the dump's copy — re-register files known before the restore
    for b in backups_rows:
        db.execute("INSERT INTO backups(id,file,kind,size_bytes,sha256,includes_secrets,schema_version,created_at) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                   (b["id"], b["file"], b["kind"], b["size_bytes"], b["sha256"], b["includes_secrets"],
                    b["schema_version"], b["created_at"]))
    db.execute("SELECT setval(pg_get_serial_sequence('backups','id'), (SELECT max(id) FROM backups))")
    audit_kept = db.execute(
        f"INSERT INTO audit_log(id,ts,actor,action,target,summary,detail,change_id) "  # constant table name
        f"SELECT id,ts,actor,action,target,summary,detail,change_id FROM {AUDIT_KEEP_TABLE} "
        "ON CONFLICT (id) DO NOTHING")
    db.execute(f"DROP TABLE IF EXISTS {AUDIT_KEEP_TABLE}")
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
    _keep_state(kept_state, db)
    settled = _settle_applying(row["file"], db)
    resync = resync_after_restore(db)
    audit(actor, "backup.restored", f"Back-up {row['file']} hersteld",
          detail={"pre_restore_backup": pre["file"], "jobs_cancelled": cancelled,
                  "audit_entries_kept": audit_kept, "resync": resync, "schema": {"dump": dump_v, "current": cur_v},
                  "migrations_down": downgraded, "migrations_up": upgraded, "kept_state": kept_state,
                  "applying_settled": settled})
    return {"restored": row["file"], "pre_restore_backup": pre["file"], "resync": resync,
            "schema_version": schema_version(db), "applying_settled": settled}


def _back_to_head(db: Database) -> None:
    try:
        migrate_up(db)
    except Exception:  # reported by the caller's error; the next start runs `migrate` again
        LOGGER.exception("schema na mislukt herstel niet bijgewerkt")


def _keep_state(kept: dict[str, Any], db: Database) -> None:
    """Put mode/pause/noodrem back exactly as they were before the restore (absent stays absent)."""
    for key in KEEP_STATE:
        if key in kept:
            db.execute("INSERT INTO system_state(key,value,updated_at) VALUES (%s,%s,now()) "
                       "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                       (key, jsonb(kept[key])))
        else:
            db.execute("DELETE FROM system_state WHERE key=%s", (key,))


def _settle_applying(file: str, db: Database) -> list[int]:
    """Changes the dump caught while they were being applied: in reality that apply ended long ago. Mark them
    failed without touching any source (the reconciler would otherwise write dump-era values back) and raise one
    "Wacht op jou" item."""
    rows = db.fetchall("UPDATE change_sets SET status='failed', error=%s WHERE status='applying' "
                       "RETURNING id, title", (RESTORED_APPLYING_ERROR,))
    if not rows:
        return []
    from mso.domain.outcomes import recommend

    ids = sorted(int(r["id"]) for r in rows)
    titles = ", ".join(f"#{r['id']} {r['title']}" for r in rows)[:600]
    rec_id = recommend(
        "system", "Na herstel: wijzigingen controleren die halverwege waren",
        f"De back-up {file} is gemaakt terwijl MSO {len(ids)} wijziging(en) aan het toepassen was ({titles}). MSO "
        "weet niet wat daarna bij de bron is gebeurd en heeft niets teruggezet. Controleer de genoemde instellingen "
        "zelf; maak zo nodig een nieuw voorstel.",
        risk="toestemming_nodig", subject="backups", dedupe_key=f"backup.restored_applying:{file}",
        evidence={"kind": "restored_applying", "change_ids": ids, "file": file}, db=db)
    db.execute("UPDATE recommendations SET status='open', updated_at=now() WHERE id=%s", (rec_id,))
    return ids


def resync_after_restore(db: Database | None = None) -> list[str]:
    """Refill what a dump leaves out: queue the syncs (read-only towards the other systems, so fine in every
    mode) and let the header index start again from the newest headers."""
    db = db or get_db()
    if db.scalar("SELECT to_regclass('public.usenet_hi_groups') IS NOT NULL"):
        db.execute("UPDATE usenet_hi_groups SET last_article=NULL WHERE last_article IS NOT NULL")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    queued = []
    from mso.connectors import base as cb

    for kind, payload, connector in RESYNC_JOBS:
        if kind not in jobs.HANDLERS or not cb.is_configured(connector):
            continue
        jobs.enqueue(kind, payload, idempotency_key=f"restore-resync:{kind}:{stamp}", max_attempts=2,
                     created_by="herstel", db=db)
        queued.append(kind)
    return queued


# ------------------------------------------------------------------ pruning
def _delete(row: dict[str, Any], db: Database) -> None:
    path = get_settings().backup_dir / row["file"]
    path.unlink(missing_ok=True)
    path.with_suffix(".sha256").unlink(missing_ok=True)
    db.execute("DELETE FROM backups WHERE id=%s", (row["id"],))


def prune(keep: int) -> int:
    db = get_db()
    rows = db.fetchall("SELECT * FROM backups WHERE kind IN ('auto','manual') ORDER BY created_at DESC")
    removed = 0
    for row in rows[keep:]:
        _delete(row, db)
        removed += 1
    return removed + prune_safety(db)


def prune_safety(db: Database | None = None) -> int:
    """Safety dumps: ``pre-restore`` older than 30 days go (the newest 2 always stay); ``pre-upgrade`` keeps the
    newest 3."""
    db = db or get_db()
    removed = 0
    rows = db.fetchall("SELECT * FROM backups WHERE kind=%s ORDER BY created_at DESC", (SAFETY_KIND,))
    for row in rows[SAFETY_KEEP:]:
        if (datetime.now(UTC) - row["created_at"]).days >= SAFETY_DAYS:
            _delete(row, db)
            removed += 1
    rows = db.fetchall("SELECT * FROM backups WHERE kind=%s ORDER BY created_at DESC", (UPGRADE_KIND,))
    protected = _pending_upgrade_files(db)
    kept = sum(1 for r in rows if r["file"] in protected)
    for row in [r for r in rows if r["file"] not in protected][max(0, UPGRADE_KEEP - kept):]:
        _delete(row, db)
        removed += 1
    return removed


def _pending_marker(db: Database) -> dict[str, Any] | None:
    row = db.fetchone("SELECT value FROM system_state WHERE key=%s", (PRE_UPGRADE_STATE,))
    return dict(row["value"]) if row and isinstance(row["value"], dict) else None


def _pending_upgrade_files(db: Database) -> set[str]:
    """The dump(s) of the state before an upgrade that has not finished yet (the marker is removed only after a
    successful migration): never pruned, also not when a failing upgrade restarts in a loop."""
    m = _pending_marker(db) or {}
    if m.get("status") != "created":
        return set()
    return {str(f) for f in (m.get("file"), m.get("origin_file")) if f}


def list_backups() -> list[dict]:
    rows = get_db().fetchall("SELECT * FROM backups ORDER BY created_at DESC")
    out = []
    for r in rows:
        present = (get_settings().backup_dir / r["file"]).exists()
        out.append(dict(r) | {"present": present})
    return out


# ------------------------------------------------------------------ before an upgrade
def pending_migrations(db: Database) -> list[int]:
    applied = {r["version"] for r in applied_migrations(db)}
    return [m.version for m in load_migrations() if m.version not in applied]


def pre_upgrade_backup(db: Database | None = None, *, target: int | None = None,
                       log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Dump the database before pending migrations run (kind ``pre-upgrade``). Never raises.

    Status: ``not_needed`` (nothing pending / brand-new database), ``exists`` (another process just made it),
    ``created``, ``skipped`` (too little free space) or ``failed``. The audit row follows after the migration."""
    say = log or LOGGER.info
    warn = log or LOGGER.warning
    try:
        db = db or get_db()
        current = schema_version(db)
        pending = [v for v in pending_migrations(db) if target is None or v <= target]
        if not pending:
            return {"status": "not_needed", "reason": "geen openstaande migraties"}
        if current == 0:
            return {"status": "not_needed", "reason": "nieuwe installatie: nog niets om te bewaren"}
        with db.pool.connection() as conn:
            conn.autocommit = True
            conn.execute("SELECT pg_advisory_lock(%s)", (PRE_UPGRADE_LOCK,))
            try:
                current = schema_version(db)
                pending = [v for v in pending_migrations(db) if target is None or v <= target]
                if not pending:
                    return {"status": "not_needed", "reason": "migraties al uitgevoerd door een ander proces"}
                # one dump per upgrade, not per start: a failing migration (each one commits on its own) leaves
                # a half-migrated schema and a restart loop; the marker lives until a migration run succeeds
                marker = _pending_marker(db) or {}
                if marker.get("status") == "created" and int(marker.get("to") or 0) >= max(pending):
                    return {"status": "exists", "reason": "back-up vóór deze upgrade bestaat al",
                            "file": marker.get("file")}
                if db.scalar("SELECT count(*) FROM backups WHERE kind=%s AND schema_version=%s "
                             "AND created_at > now() - interval '1 hour'", (UPGRADE_KIND, current)):
                    return {"status": "exists", "reason": "back-up vóór deze upgrade bestaat al"}
                info: dict[str, Any] = {"from": current, "to": max(pending), "pending": pending}
                if marker.get("status") == "created":  # an even newer image on top of an unfinished upgrade
                    info |= {"origin_file": marker.get("origin_file") or marker.get("file"),
                             "origin_from": marker.get("origin_from") or marker.get("from")}
                st = space_status(db)
                if not st["ok"]:
                    msg = _space_message(st)
                    warn(f"back-up vóór upgrade overgeslagen: {msg}; MSO start gewoon door")
                    _mark(db, info | {"status": "skipped", "reason": msg,
                                      "space": {k: st[k] for k in ("free_bytes", "needed_bytes", "path",
                                                                   "reference", "reference_bytes",
                                                                   "reserve_bytes")}})
                    return {"status": "skipped", "reason": msg}
                path = _dump(kind=UPGRADE_KIND, include_secrets=False, timeout=900)
                row = _register(path, UPGRADE_KIND, False, db)
                _mark(db, info | {"status": "created", "file": row["file"], "size": row["size_bytes"]})
                prune_safety(db)
                say(f"back-up vóór upgrade gemaakt: {row['file']} ({_gb(row['size_bytes'])}; schema "
                    f"{current} → {max(pending)})")
                return {"status": "created", "file": row["file"], "size_bytes": row["size_bytes"]}
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (PRE_UPGRADE_LOCK,))
                conn.autocommit = False
    except Exception as exc:
        reason = redact(f"{exc}")[:400] or type(exc).__name__
        warn(f"back-up vóór upgrade mislukt: {reason}; MSO start gewoon door (maak zelf een back-up of "
            "ZFS-snapshot als je wilt terug kunnen)")
        try:
            if db is not None:
                _mark(db, {"status": "failed", "reason": reason})
        except Exception as mark_exc:  # e.g. the database itself is unreachable: the log line above is all we have
            LOGGER.debug("markering back-up vóór upgrade niet opgeslagen: %s", type(mark_exc).__name__)
        return {"status": "failed", "reason": reason}


def _mark(db: Database, value: dict[str, Any]) -> None:
    db.execute("INSERT INTO system_state(key,value,updated_at) VALUES (%s,%s,now()) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
               (PRE_UPGRADE_STATE, jsonb(value)))


def flush_pre_upgrade(db: Database | None = None) -> dict[str, Any] | None:
    """After the migration: audit what the pre-upgrade backup did (``backup.pre_upgrade``), raise the free-space
    item if it was skipped. Idempotent (the marker is removed)."""
    db = db or get_db()
    row = db.fetchone("DELETE FROM system_state WHERE key=%s RETURNING value", (PRE_UPGRADE_STATE,))
    if row is None:
        return None
    v = dict(row["value"] or {})
    status = v.get("status")
    if status == "created":
        audit("systeem", "backup.pre_upgrade", f"Back-up gemaakt vóór de upgrade ({v.get('file')})",
              detail={k: v.get(k) for k in ("file", "size", "from", "to", "pending")})
    elif status == "skipped":
        audit("systeem", "backup.pre_upgrade_skipped", "Back-up vóór de upgrade overgeslagen: te weinig vrije ruimte",
              detail={k: v.get(k) for k in ("reason", "from", "to")})
        if v.get("space"):
            _space_alert(v["space"] | {"ok": False}, UPGRADE_KIND, db)
    else:
        audit("systeem", "backup.pre_upgrade_failed", "Back-up vóór de upgrade mislukt; MSO is gewoon gestart",
              detail={"reason": v.get("reason")})
    return v


# ------------------------------------------------------------------ job
@jobs.handler("backup.create")
def _job_backup(ctx: jobs.JobContext) -> dict:
    kind = ctx.payload.get("kind", "manual")
    try:
        row = create_backup(include_secrets=bool(ctx.payload.get("include_secrets")), kind=kind,
                            actor=ctx.payload.get("actor", "worker"))
    except BackupSkipped as exc:
        if kind != "auto":
            raise jobs.PermanentJobError(str(exc)) from exc
        ctx.progress(1, "Overgeslagen: te weinig vrije schijfruimte (zie Wacht op jou)")
        return {"skipped": str(exc)}
    return {"file": row["file"], "size_bytes": row["size_bytes"]}


jobs.every("backup.create", 24 * 3600, {"kind": "auto", "actor": "scheduler"},
           enabled=lambda: bool(setting("backup.auto_daily")))
