"""Local authentication with RBAC (admin/operator/viewer), HttpOnly session cookie and CSRF token.

- Passwords: scrypt (n=2**15, r=8, p=1) with per-user salt.
- Session: random token in ``mso_session`` cookie (HttpOnly, SameSite=Strict, Secure when configured);
  only its SHA-256 hash is stored.
- CSRF: every mutating request with a session must echo ``X-CSRF-Token`` (synchroniser token).
- Login is rate limited per remote address and per username (brute force).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request

from mso.config import get_settings
from mso.db import Database, get_db

SESSION_COOKIE = "mso_session"
CSRF_HEADER = "X-CSRF-Token"
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    username: str
    role: str
    user_id: int | None = None
    csrf_token: str | None = None
    machine: bool = False

    @property
    def actor(self) -> str:
        return self.username

    def can(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK[role]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return hmac.compare_digest(digest, expected)


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(422, detail="Wachtwoord moet minimaal 10 tekens hebben")
    mixed_case = password.lower() != password and password.upper() != password
    if not (mixed_case or any(c.isdigit() for c in password)):
        raise HTTPException(422, detail="Gebruik hoofd- en kleine letters of cijfers in het wachtwoord")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Database, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = datetime.now(UTC) + timedelta(hours=get_settings().session_hours)
    db.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES (%s,%s,%s,%s)",
               (token_hash(token), user_id, csrf, expires))
    db.execute("UPDATE users SET last_login_at=now() WHERE id=%s", (user_id,))
    db.execute("DELETE FROM sessions WHERE expires_at < now()")
    return token, csrf


def delete_session(db: Database, token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token_hash=%s", (token_hash(token),))


def principal_from_request(request: Request) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = get_db().fetchone(
        "SELECT s.csrf_token, s.expires_at, u.id, u.username, u.role, u.disabled "
        "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=%s", (token_hash(token),))
    if row is None or row["disabled"] or row["expires_at"] < datetime.now(UTC):
        return None
    return Principal(username=row["username"], role=row["role"], user_id=int(row["id"]),
                     csrf_token=row["csrf_token"])


def current_principal(request: Request) -> Principal:
    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Niet ingelogd")
    if request.method not in SAFE_METHODS and principal.csrf_token is not None:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, principal.csrf_token):
            raise HTTPException(status_code=403, detail="CSRF-controle mislukt")
    request.state.principal = principal
    return principal


def require(role: str):  # type: ignore[no-untyped-def]
    """FastAPI dependency: at least ``role`` (viewer < operator < admin)."""

    def dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(role):
            raise HTTPException(status_code=403, detail=f"Rol '{role}' of hoger vereist")
        return principal

    return dep


def users_exist(db: Database) -> bool:
    return bool(db.scalar("SELECT count(*) FROM users"))
