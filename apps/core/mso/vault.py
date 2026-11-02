"""Secret vault: AES-256-GCM, key file in APPDATA (0600), never logged, never returned by the API.

The UI only ever learns whether a secret is ``ingesteld`` (set) or ``leeg`` (empty).
Backups exclude the ``secrets`` table unless the admin explicitly opts in; even then the key file is
not part of the dump (restore on another host needs the key file copied separately).
"""

from __future__ import annotations

import os
import secrets as pysecrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mso.config import get_settings
from mso.db import Database


def _load_key(path: Path) -> bytes:
    if path.exists():
        data = path.read_bytes()
        if len(data) != 32:
            raise RuntimeError(f"vault-sleutel {path} heeft een ongeldige lengte")
        return data
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = AESGCM.generate_key(bit_length=256)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
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
