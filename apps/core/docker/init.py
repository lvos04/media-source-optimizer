"""One-shot bootstrap for the ``init`` service (runs as root with only CHOWN/DAC_OVERRIDE/FOWNER).

Creates the directory layout on the APPDATA and WORK datasets, hands it to uid/gid 568 and generates
the PostgreSQL password file when it does not exist yet. Idempotent: existing files are never
overwritten, existing non-empty directories owned by someone else are left alone (only reported).

APPDATA (mounted at /bootstrap/appdata)          WORK (mounted at /bootstrap/work)
  postgres/   PostgreSQL data directory (0700)     cache/   caches (safe to delete)
  db/password database password (0400)             index/   header/search index (quota!)
  secrets/    vault key (created by core, 0700)    tmp/     scratch space
  backups/    pg_dump archives + .sha256
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

UID = int(os.environ.get("MSO_UID", "568"))
GID = int(os.environ.get("MSO_GID", "568"))
APPDATA = Path(os.environ.get("MSO_BOOTSTRAP_APPDATA", "/bootstrap/appdata"))
WORK = Path(os.environ.get("MSO_BOOTSTRAP_WORK", "/bootstrap/work"))


def ensure_dir(path: Path, mode: int, log: list[str], *, strict_mode: bool = False) -> None:
    """``strict_mode``: always enforce ``mode`` (postgres data dir, password dir). Otherwise the mode is only
    set on directories init created or took over, so dataset ACLs chosen by the admin stay untouched."""
    fresh = False
    if not path.exists():
        path.mkdir(parents=True, mode=mode)
        log.append(f"map aangemaakt: {path}")
        fresh = True
    st = path.stat()
    if (st.st_uid, st.st_gid) != (UID, GID):
        # A directory Docker created for a bind mount is root-owned and empty: take it over.
        # A directory with content owned by someone else is a deliberate choice: never chown recursively.
        if st.st_uid == 0 and not any(path.iterdir()):
            os.chown(path, UID, GID)
            log.append(f"eigenaar {UID}:{GID} gezet: {path}")
            fresh = True
        elif path.name == "postgres" and st.st_uid == 0:
            # an earlier run as root may have left files; postgres refuses a foreign-owned data dir
            for root, dirs, files in os.walk(path):
                for n in dirs + files:
                    os.lchown(os.path.join(root, n), UID, GID)
            os.chown(path, UID, GID)
            log.append(f"eigenaar {UID}:{GID} (recursief) gezet: {path}")
        else:
            log.append(f"LET OP: {path} is van {st.st_uid}:{st.st_gid}, niet {UID}:{GID} — controleer de ACL")
    if fresh or strict_mode:
        try:
            os.chmod(path, mode)
        except PermissionError:
            # e.g. a dataset with an NFSv4 ACL in 'restricted' aclmode refuses chmod
            log.append(f"LET OP: kon rechten {oct(mode)} niet zetten op {path} (ACL-modus?)")


def ensure_password(path: Path, log: list[str]) -> None:
    if path.exists() and path.read_text(encoding="utf-8").strip():
        os.chown(path, UID, GID)
        os.chmod(path, 0o400)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secrets.token_hex(32) + "\n")  # hex: no quoting issues in URLs or libpq
    os.chown(path, UID, GID)
    log.append(f"databasewachtwoord gegenereerd: {path}")


def main() -> int:
    if os.geteuid() != 0:
        print("init: moet als root draaien (alleen CHOWN/DAC_OVERRIDE/FOWNER)", file=sys.stderr)
        return 1
    for base in (APPDATA, WORK):
        if not base.is_dir():
            print(f"init: {base} is niet gemount", file=sys.stderr)
            return 1
    log: list[str] = []
    ensure_dir(APPDATA, 0o750, log)
    ensure_dir(APPDATA / "postgres", 0o700, log, strict_mode=True)
    ensure_dir(APPDATA / "db", 0o700, log, strict_mode=True)
    ensure_dir(APPDATA / "secrets", 0o700, log)
    ensure_dir(APPDATA / "backups", 0o750, log)
    ensure_dir(WORK, 0o750, log)
    for sub in ("cache", "index", "tmp"):
        ensure_dir(WORK / sub, 0o750, log)
    ensure_password(APPDATA / "db" / "password", log)
    for line in log or ["niets te doen (alles bestond al)"]:
        print(f"init: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
