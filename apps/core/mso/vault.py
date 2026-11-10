"""Secret vault: AES-256-GCM, key file in APPDATA (0600), never logged, never returned by the API.

The UI only ever learns whether a secret is ``ingesteld`` (set) or ``leeg`` (empty).
Backups exclude the ``secrets`` table unless the admin explicitly opts in; even then the key file is
not part of the dump (restore on another host needs the key file copied separately).
"""

from __future__ import annotations

import os
import secrets as pysecrets
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mso.config import get_settings
from mso.db import Database

#: how long a process waits for a key file that another process has just created but not yet completely written
KEY_WAIT_S = 5.0


def _read_key(path: Path, wait_s: float) -> bytes:
    deadline = time.monotonic() + wait_s
    while True:
        data = path.read_bytes()
        if len(data) == 32:
            return data
        if time.monotonic() >= deadline:
            raise RuntimeError(f"vault-sleutel {path} heeft een ongeldige lengte")
        time.sleep(0.05)


def _load_key(path: Path, wait_s: float = KEY_WAIT_S) -> bytes:
    """Read the vault key, or create it once (0600, never overwritten).

    api and worker start at the same moment on a fresh /appdata (compose: both after 'migrate'; all-in-one: the
    supervisor). Exactly one process wins the O_EXCL create; the other reads the winner's key instead of crashing
    (FileExistsError) and waits briefly while the winner is still writing (found in the stack smoke test, ADR-0009)."""
    if path.exists():
        return _read_key(path, wait_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = AESGCM.generate_key(bit_length=256)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another process created it between exists() and open()
        return _read_key(path, wait_s)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
        fh.flush()
        os.fsync(fh.fileno())
    return key


class Vault:
    def __init__(self, db: Database, key: bytes | None = None) -> None:
        self.db = db
        self._aes = AESGCM(key or _load_key(get_settings().secret_key_file))

    def set(self, name: str, value: str | None) -> None:
        if value is None or value == "":
            self.db.execute("DELETE FROM secrets WHERE name=%s", (name,))
            return
        nonce = pysecrets.token_bytes(12)
        ct = self._aes.encrypt(nonce, value.encode("utf-8"), name.encode("utf-8"))
        self.db.execute(
            "INSERT INTO secrets(name,nonce,ciphertext,updated_at) VALUES (%s,%s,%s,now()) "
            "ON CONFLICT (name) DO UPDATE SET nonce=EXCLUDED.nonce, ciphertext=EXCLUDED.ciphertext, "
            "updated_at=now()", (name, nonce, ct))

    def get(self, name: str) -> str | None:
        row = self.db.fetchone("SELECT nonce, ciphertext FROM secrets WHERE name=%s", (name,))
        if row is None:
            return None
        return self._aes.decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), name.encode("utf-8")).decode()

    def has(self, name: str) -> bool:
        return self.db.scalar("SELECT count(*) FROM secrets WHERE name=%s", (name,)) > 0

    def status(self, name: str) -> str:
        return "ingesteld" if self.has(name) else "leeg"

    def names(self, prefix: str = "") -> list[str]:
        rows = self.db.fetchall("SELECT name FROM secrets WHERE name LIKE %s ORDER BY name", (prefix + "%",))
        return [r["name"] for r in rows]

    def delete_prefix(self, prefix: str) -> int:
        return self.db.execute("DELETE FROM secrets WHERE name LIKE %s", (prefix + "%",))


_vault: Vault | None = None


def get_vault() -> Vault:
    if _vault is None:
        raise RuntimeError("vault niet geïnitialiseerd")
    return _vault


def set_vault(v: Vault | None) -> None:
    global _vault
    _vault = v
