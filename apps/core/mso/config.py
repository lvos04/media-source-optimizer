"""Runtime configuration (environment only — no credentials ever live here; those go into the vault)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "1.1.0"


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> list[str]:
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


def _database_url() -> str:
    """MSO_DATABASE_URL, or assembled from MSO_DB_* with the password read from MSO_DB_PASSWORD_FILE
    (TrueNAS: the init container generates that file on APPDATA; the password never lives in YAML)."""
    url = os.environ.get("MSO_DATABASE_URL")
    if url:
        return url
    pw_file = os.environ.get("MSO_DB_PASSWORD_FILE")
    if pw_file and Path(pw_file).exists():
        from urllib.parse import quote
        pw = quote(Path(pw_file).read_text().strip(), safe="")
        user = os.environ.get("MSO_DB_USER", "mso")
        host = os.environ.get("MSO_DB_HOST", "postgres")
        port = os.environ.get("MSO_DB_PORT", "5432")
        name = os.environ.get("MSO_DB_NAME", "mso")
        return f"postgresql://{user}:{pw}@{host}:{port}/{name}"
    return "postgresql://mso@127.0.0.1:55432/mso"


@dataclass
class Settings:
    database_url: str = field(default_factory=_database_url)
    # APPDATA dataset: secret key, backups, logs. WORK dataset: caches, header index.
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MSO_DATA_DIR", "/appdata")))
    work_dir: Path = field(default_factory=lambda: Path(os.environ.get("MSO_WORK_DIR", "/work")))
    # Optional read-only media mount for diagnostics (API-only preferred).
    media_dir: Path | None = field(
        default_factory=lambda: Path(p) if (p := os.environ.get("MSO_MEDIA_DIR")) else None)
    # Browser origins allowed for CORS. Empty = same-origin only (the web container proxies /api).
    cors_origins: list[str] = field(default_factory=lambda: _list("MSO_CORS_ORIGINS"))
    # Hosts that outbound connector calls may never reach (always includes cloud metadata ranges).
    ssrf_extra_blocked: list[str] = field(default_factory=lambda: _list("MSO_SSRF_BLOCK"))
    # Allow connector URLs on loopback (only useful in dev/test where mocks run on 127.0.0.1).
    allow_loopback_targets: bool = field(default_factory=lambda: _bool("MSO_ALLOW_LOOPBACK"))
    # Secure cookie flag: set when served behind TLS reverse proxy.
    secure_cookies: bool = field(default_factory=lambda: _bool("MSO_SECURE_COOKIES"))
    session_hours: int = field(default_factory=lambda: int(os.environ.get("MSO_SESSION_HOURS", "12")))
    # Public base URL as seen by Prowlarr (for Newznab/Torznab download links).
    public_url: str = field(default_factory=lambda: os.environ.get("MSO_PUBLIC_URL", "").rstrip("/"))
    node_id: str = field(default_factory=lambda: os.environ.get("MSO_NODE_ID", "mso"))
    worker_poll_s: float = field(default_factory=lambda: float(os.environ.get("MSO_WORKER_POLL_S", "2")))
    disk_reserve_gb: float = field(default_factory=lambda: float(os.environ.get("MSO_DISK_RESERVE_GB", "2")))
    testing: bool = field(default_factory=lambda: _bool("MSO_TESTING"))

    @property
    def secret_key_file(self) -> Path:
        return self.data_dir / "secrets" / "vault.key"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
