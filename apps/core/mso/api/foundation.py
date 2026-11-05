"""Foundation API: auth, setup, users, settings, system mode/health/info, audit, jobs, changes,
connectors, backups."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mso import backups, changes, jobs, settings_registry, state
from mso.audit import audit
from mso.auth import (
    SESSION_COOKIE,
    Principal,
    create_session,
    delete_session,
    hash_password,
    principal_from_request,
    require,
    users_exist,
    validate_password,
    verify_password,
)
from mso.config import VERSION, get_settings
from mso.connectors import base as connectors
from mso.db import applied_migrations, get_db, jsonb, schema_version
from mso.security import UnsafeTarget, check_url, limiter
from mso.vault import get_vault

router = APIRouter(prefix="/api/v1")
STARTED = time.time()


# ------------------------------------------------------------------ helpers
def _remote(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=get_settings().secure_cookies,
        path="/",
        max_age=get_settings().session_hours * 3600,
    )


def _user_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "disabled": row["disabled"],
        "created_at": row["created_at"],
        "last_login_at": row.get("last_login_at"),
    }


# ------------------------------------------------------------------ setup
class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=1, max_length=256)


@router.get("/setup/status")
def setup_status() -> dict[str, Any]:
    db = get_db()
    admin_exists = users_exist(db)
    completed = bool(state.get_state("setup_completed", False))
    rows = db.fetchall("SELECT id, status, url, enabled FROM connectors ORDER BY id")
    return {
        "setup_required": not completed,
        "admin_exists": admin_exists,
        "completed": completed,
        "version": VERSION,
        "connectors": {r["id"]: {"configured": bool(r["url"]) and r["enabled"], "status": r["status"]} for r in rows},
    }


@router.post("/setup/admin")
def setup_admin(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    db = get_db()
    ok, _ = limiter.hit("setup", _remote(request), 10, 60)
    if not ok:
        raise HTTPException(429, detail="Te veel pogingen")
    validate_password(body.password)
    with db.tx() as conn:
        conn.execute("LOCK TABLE users IN EXCLUSIVE MODE")
        if conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]:
            raise HTTPException(409, detail="Er bestaat al een beheerder; log in")
        row = conn.execute(
            "INSERT INTO users(username,password_hash,role) VALUES (%s,%s,'admin') RETURNING *",
            (body.username, hash_password(body.password)),
        ).fetchone()
    token, csrf = create_session(db, row["id"])
    _set_cookie(response, token)
    audit(body.username, "setup.admin_created", f"Beheerder '{body.username}' aangemaakt")
    return {"user": _user_view(row), "csrf_token": csrf}


class SetupComplete(BaseModel):
    mode: str | None = Field(default=None, pattern=r"^(read_only|manage|autopilot)$")


@router.post("/setup/complete")
def setup_complete(body: SetupComplete | None = None, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    if body and body.mode:
        state.set_state("mode", body.mode)
    state.set_state("setup_completed", True)
    audit(p.actor, "setup.completed", "Eerste-startwizard afgerond", detail={"mode": state.mode()})
    return setup_status()


# ------------------------------------------------------------------ auth
@router.post("/auth/login")
def login(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    db = get_db()
    per_min = int(settings_registry.get("security.login_rate_per_min"))
    ok_ip, wait_ip = limiter.hit("login-ip", _remote(request), per_min, 60)
    ok_user, wait_user = limiter.hit("login-user", body.username.lower(), per_min, 60)
    if not (ok_ip and ok_user):
        raise HTTPException(429, detail=f"Te veel inlogpogingen; probeer over {int(max(wait_ip, wait_user)) + 1} s")
    row = db.fetchone("SELECT * FROM users WHERE lower(username)=lower(%s)", (body.username,))
    good = bool(row) and not row["disabled"] and verify_password(body.password, row["password_hash"])
    db.execute(
        "INSERT INTO login_attempts(username,remote,ok) VALUES (%s,%s,%s)", (body.username[:64], _remote(request), good)
    )
    if not good:
        # security trail without secrets: who tried, from where (never the password)
        audit(body.username[:64] or "?", "auth.login_failed", "Inloggen mislukt (onjuiste gegevens of uitgeschakeld)",
              detail={"remote": _remote(request), "known_user": bool(row)})
        raise HTTPException(401, detail="Onjuiste gebruikersnaam of wachtwoord")
    token, csrf = create_session(db, row["id"])
    _set_cookie(response, token)
    audit(row["username"], "auth.login", "Ingelogd", detail={"remote": _remote(request)})
    return {"user": _user_view(row), "csrf_token": csrf}


@router.post("/auth/logout")
def logout(request: Request, response: Response, p: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(get_db(), token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    audit(p.actor, "auth.logout", "Uitgelogd")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request) -> dict[str, Any]:
    p = principal_from_request(request)
    if p is None:
        raise HTTPException(401, detail="Niet ingelogd")
    return {"user": {"id": p.user_id, "username": p.username, "role": p.role}, "csrf_token": p.csrf_token}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/password")
def change_password(body: PasswordChange, p: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    db = get_db()
    row = db.fetchone("SELECT * FROM users WHERE id=%s", (p.user_id,))
    if not row or not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(403, detail="Huidig wachtwoord klopt niet")
    validate_password(body.new_password)
    db.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(body.new_password), p.user_id))
    audit(p.actor, "auth.password_changed", "Wachtwoord gewijzigd")
    return {"ok": True}


# ------------------------------------------------------------------ users (admin)
class NewUser(Credentials):
    role: str = Field(pattern=r"^(admin|operator|viewer)$")


class UserPatch(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(admin|operator|viewer)$")
    disabled: bool | None = None
    password: str | None = None


@router.get("/users")
def list_users(_: Principal = Depends(require("admin"))) -> dict[str, Any]:
    return {"items": [_user_view(r) for r in get_db().fetchall("SELECT * FROM users ORDER BY id")]}


@router.post("/users")
def create_user(body: NewUser, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    validate_password(body.password)
    db = get_db()
    if db.fetchone("SELECT 1 FROM users WHERE lower(username)=lower(%s)", (body.username,)):
        raise HTTPException(409, detail="Gebruikersnaam bestaat al")
    row = db.fetchone(
        "INSERT INTO users(username,password_hash,role) VALUES (%s,%s,%s) RETURNING *",
        (body.username, hash_password(body.password), body.role),
    )
    audit(p.actor, "user.created", f"Gebruiker '{body.username}' ({body.role}) aangemaakt")
    return _user_view(row)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    db = get_db()
    row = db.fetchone("SELECT * FROM users WHERE id=%s", (user_id,))
    if row is None:
        raise HTTPException(404, detail="Gebruiker niet gevonden")
    demoting = (body.role and body.role != "admin") or body.disabled
    if row["role"] == "admin" and demoting:
        admins = db.scalar("SELECT count(*) FROM users WHERE role='admin' AND NOT disabled")
        if admins <= 1:
            raise HTTPException(409, detail="De laatste actieve beheerder kan niet worden gedegradeerd")
    if body.role:
        db.execute("UPDATE users SET role=%s WHERE id=%s", (body.role, user_id))
    if body.disabled is not None:
        db.execute("UPDATE users SET disabled=%s WHERE id=%s", (body.disabled, user_id))
        if body.disabled:
            db.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))
    if body.password:
        validate_password(body.password)
        db.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(body.password), user_id))
        db.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))
    audit(
        p.actor,
        "user.updated",
        f"Gebruiker '{row['username']}' bijgewerkt",
        detail={"role": body.role, "disabled": body.disabled, "password_reset": bool(body.password)},
    )
    return _user_view(db.fetchone("SELECT * FROM users WHERE id=%s", (user_id,)))


# ------------------------------------------------------------------ settings
class SettingsPut(BaseModel):
    values: dict[str, Any]


@router.get("/settings")
def get_settings_api(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {"definitions": settings_registry.definitions(), "values": settings_registry.all_values()}


@router.put("/settings")
def put_settings(body: SettingsPut, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    changed = settings_registry.put(body.values, p.actor)
    if changed:
        audit(
            p.actor,
            "settings.updated",
            f"{len(changed)} instelling(en) gewijzigd",
            detail={k: {"old": o, "new": n} for k, (o, n) in changed.items()},
        )
    return {"changed": list(changed), "values": settings_registry.all_values()}


# ------------------------------------------------------------------ system
class ModeBody(BaseModel):
    mode: str = Field(pattern=r"^(read_only|manage|autopilot)$")


_MODE_NL = {"read_only": "Alleen-lezen", "manage": "Beheermodus", "autopilot": "Autopilot"}


@router.get("/system/mode")
def get_mode(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return {"mode": state.mode(), "paused": state.paused(), "autopilot": state.autopilot(),
            "automation_allowed": state.automation_allowed()}


@router.put("/system/mode")
def put_mode(body: ModeBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    old = state.mode()
    state.set_state("mode", body.mode)
    unpaused = body.mode == "autopilot" and state.paused()
    if unpaused:  # Autopilot = set it and forget it: switching it on also lifts a pause
        state.set_state("paused", False)
    audit(
        p.actor,
        "system.mode",
        f"Bedrijfsmodus: {_MODE_NL.get(old, old)} → {_MODE_NL.get(body.mode, body.mode)}"
        + (" (pauze opgeheven)" if unpaused else ""),
        detail={"old": old, "new": body.mode, "unpaused": unpaused},
    )
    return get_mode(p)


@router.post("/system/pause")
def pause(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    state.set_state("paused", True)
    audit(p.actor, "system.paused", "Automatisering gepauzeerd")
    return get_mode(p)


@router.post("/system/resume")
def resume(p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    state.set_state("paused", False)
    audit(p.actor, "system.resumed", "Automatisering hervat")
    return get_mode(p)


def _disk(path: Any) -> dict[str, Any] | None:
    import shutil

    try:
        u = shutil.disk_usage(path)
    except OSError:
        return None
    return {"path": str(path), "total": u.total, "free": u.free, "used": u.used}


_STATUS_NL = {
    "healthy": "Gezond",
    "ok": "Gezond",
    "degraded": "Verstoord",
    "offline": "Offline",
    "error": "Fout",
    "unknown": "Onbekend",
    "unconfigured": "Niet ingesteld",
    "auth_failed": "Inloggen geweigerd",
}


@router.get("/system/health")
def health(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    db = get_db()
    comps: list[dict[str, Any]] = [
        {
            "name": "database",
            "status": "healthy" if db.ping() else "offline",
            "message": "PostgreSQL bereikbaar" if db.ping() else "PostgreSQL onbereikbaar",
        }
    ]
    hb = db.fetchone("SELECT value, updated_at FROM system_state WHERE key='worker_heartbeat'")
    if hb is None:
        comps.append({"name": "worker", "status": "degraded", "message": "Worker nog niet gezien"})
    else:
        age = db.scalar("SELECT extract(epoch FROM now() - updated_at) FROM system_state WHERE key='worker_heartbeat'")
        comps.append(
            {
                "name": "worker",
                "status": "healthy" if age < 90 else "offline",
                "message": f"Laatste hartslag {int(age)} s geleden",
            }
        )
    settings = get_settings()
    for label, path in (("appdata", settings.data_dir), ("work", settings.work_dir)):
        d = _disk(path)
        if d is None:
            comps.append({"name": label, "status": "degraded", "message": f"{path} niet beschikbaar"})
            continue
        reserve = settings.disk_reserve_gb * 1024**3
        comps.append(
            {
                "name": label,
                "status": "healthy" if d["free"] > reserve else "degraded",
                "message": f"{d['free'] / 1024**3:.1f} GB vrij",
                "disk": d,
            }
        )
    for row in db.fetchall("SELECT id, label, status, enabled, url, last_error FROM connectors ORDER BY id"):
        if not row["enabled"] or not row["url"]:
            continue
        comps.append(
            {
                "name": f"connector:{row['id']}",
                "label": row["label"],
                "status": row["status"],
                "message": row["last_error"] or _STATUS_NL.get(row["status"] or "", row["status"]),
            }
        )
    statuses = [c["status"] for c in comps]
    overall = (
        "offline"
        if comps[0]["status"] == "offline"
        else ("healthy" if all(s == "healthy" for s in statuses) else "degraded")
    )
    return {"status": overall, "components": comps}


@router.get("/system/info")
def info(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    db = get_db()
    return {
        "version": VERSION,
        "schema_version": schema_version(db),
        "migrations": [
            {"version": m["version"], "name": m["name"], "applied_at": m["applied_at"]} for m in applied_migrations(db)
        ],
        "uptime_s": int(time.time() - STARTED),
        "mode": state.mode(),
        "paused": state.paused(),
    }


# ------------------------------------------------------------------ audit
@router.get("/audit")
def audit_list(
    limit: int = Query(100, ge=1, le=500),
    action: str | None = None,
    before_id: int | None = None,
    target: str | None = None,
    q: str | None = None,
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    where, params = [], []
    if action:
        where.append("action LIKE %s")
        params.append(action.rstrip("*") + "%")
    if target:
        where.append("target=%s")
        params.append(target)
    if before_id:
        where.append("id < %s")
        params.append(before_id)
    if q:
        where.append("(summary ILIKE %s OR actor ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT %s"
    rows = get_db().fetchall(sql, [*params, limit])
    return {"items": rows, "next_before_id": rows[-1]["id"] if len(rows) == limit else None}


# ------------------------------------------------------------------ jobs
@router.get("/jobs")
def jobs_list(
    status: str | None = None,
    kind: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    where, params = [], []
    if status:
        where.append("status = ANY(%s)")
        params.append(status.split(","))
    if kind:
        where.append("kind LIKE %s")
        params.append(kind.rstrip("*") + "%")
    sql = (
        "SELECT id,kind,status,attempts,max_attempts,progress,summary,error,created_by,created_at,updated_at,"
        "run_after,result FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT %s"
    )
    return {"items": get_db().fetchall(sql, [*params, limit])}


@router.get("/jobs/{job_id}")
def job_get(job_id: int, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    row = get_db().fetchone("SELECT * FROM jobs WHERE id=%s", (job_id,))
    if row is None:
        raise HTTPException(404, detail="Job niet gevonden")
    return row


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not jobs.cancel(job_id):
        raise HTTPException(409, detail="Job is al afgerond")
    audit(p.actor, "job.cancelled", f"Job {job_id} geannuleerd")
    return job_get(job_id, p)


@router.post("/jobs/{job_id}/retry")
def job_retry(job_id: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    if not jobs.retry(job_id):
        raise HTTPException(409, detail="Alleen mislukte of geannuleerde jobs kunnen opnieuw")
    audit(p.actor, "job.retried", f"Job {job_id} opnieuw ingepland")
    return job_get(job_id, p)


# ------------------------------------------------------------------ changes
@router.get("/changes")
def changes_list(
    status: str | None = None,
    target: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: Principal = Depends(require("viewer")),
) -> dict[str, Any]:
    return {"items": changes.list_changes(status, target, limit), "mode": state.mode()}


@router.get("/changes/{change_id}")
def change_get(change_id: int, _: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    return changes.get(change_id)


@router.post("/changes/{change_id}/approve")
def change_approve(change_id: int, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    return changes.approve(change_id, p.actor)


@router.post("/changes/{change_id}/apply")
def change_apply(change_id: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    ch = changes.get(change_id)
    if ch["risk"] == "toestemming_nodig" and not p.can("admin") and ch["status"] != "approved":
        raise HTTPException(403, detail="Goedkeuring door een beheerder vereist")
    return changes.apply(change_id, p.actor)


class RollbackBody(BaseModel):
    reason: str = ""


@router.post("/changes/{change_id}/rollback")
def change_rollback(
    change_id: int, body: RollbackBody | None = None, p: Principal = Depends(require("operator"))
) -> dict[str, Any]:
    return changes.rollback(change_id, p.actor, (body.reason if body else ""))


@router.post("/changes/{change_id}/discard")
def change_discard(change_id: int, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    return changes.discard(change_id, p.actor)


# ------------------------------------------------------------------ connectors
class ConnectorPut(BaseModel):
    url: str | None = None
    enabled: bool | None = None
    options: dict[str, Any] | None = None
    secrets: dict[str, str | None] | None = None  # value None/"" clears; omitted = unchanged


class ConnectorTest(BaseModel):
    url: str | None = None
    options: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None


@router.get("/connectors")
def connectors_list(_: Principal = Depends(require("viewer"))) -> list[dict[str, Any]]:
    rows = get_db().fetchall("SELECT * FROM connectors ORDER BY id")
    order = {cid: i for i, cid in enumerate(connectors.SPECS)}
    return [connectors.public_view(r) for r in sorted(rows, key=lambda r: order.get(r["id"], 99))]


def _spec(cid: str) -> connectors.ConnectorSpec:
    spec = connectors.SPECS.get(cid)
    if spec is None:
        raise HTTPException(404, detail="Onbekende koppeling")
    return spec


@router.put("/connectors/{cid}")
def connector_put(cid: str, body: ConnectorPut, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    spec = _spec(cid)
    db = get_db()
    row = db.fetchone("SELECT * FROM connectors WHERE id=%s", (cid,))
    url = row["url"]
    if body.url is not None:
        try:
            url = check_url(body.url, purpose=spec.label) if body.url.strip() else None
        except UnsafeTarget as exc:
            raise HTTPException(422, detail=str(exc)) from exc
    enabled = row["enabled"] if body.enabled is None else body.enabled
    if body.url and body.enabled is None and not row["url"]:
        enabled = True
    options = row["options"] if body.options is None else body.options
    db.execute(
        "UPDATE connectors SET url=%s, enabled=%s, options=%s, updated_at=now() WHERE id=%s",
        (url, enabled, jsonb(options), cid),
    )
    changed_secrets = []
    if body.secrets:
        allowed = {f.name for f in spec.secret_fields}
        vault = get_vault()
        for name, value in body.secrets.items():
            if name not in allowed:
                raise HTTPException(422, detail=f"Onbekend geheimveld '{name}'")
            vault.set(connectors.secret_name(cid, name), value)
            changed_secrets.append(name)
    audit(
        p.actor,
        "connector.updated",
        f"Koppeling {spec.label} opgeslagen",
        target=cid,
        detail={"url": url, "enabled": enabled, "secrets_changed": changed_secrets},
    )
    return connectors.public_view(db.fetchone("SELECT * FROM connectors WHERE id=%s", (cid,)))


@router.post("/connectors/{cid}/test")
def connector_test(
    cid: str, request: Request, body: ConnectorTest | None = None, p: Principal = Depends(require("operator"))
) -> dict[str, Any]:
    spec = _spec(cid)
    ok, _ = limiter.hit("connector-test", p.actor, 30, 60)
    if not ok:
        raise HTTPException(429, detail="Te veel testen; wacht een minuut")
    row = get_db().fetchone("SELECT * FROM connectors WHERE id=%s", (cid,))
    vault = get_vault()
    stored = {f.name: v for f in spec.secret_fields if (v := vault.get(connectors.secret_name(cid, f.name)))}
    body = body or ConnectorTest()
    url = body.url or row["url"]
    if not url:
        raise HTTPException(422, detail="Vul eerst een URL in")
    cfg = connectors.ConnectorConfig(
        id=cid,
        url=url,
        secrets=stored | {k: v for k, v in (body.secrets or {}).items() if v},
        options=(body.options if body.options is not None else row["options"]) or {},
    )
    result = connectors.run_test(spec, cfg)
    # only persist status when testing the saved configuration
    if not body.url or body.url.rstrip("/") == (row["url"] or "").rstrip("/"):
        connectors.record_test(cid, result)
    audit(
        p.actor,
        "connector.tested",
        f"Test {spec.label}: {_STATUS_NL.get(result.status, result.status)}",
        target=cid,
        detail={"ok": result.ok, "status": result.status, "message": result.message},
    )
    return result.as_dict()


# ------------------------------------------------------------------ backups
class BackupBody(BaseModel):
    include_secrets: bool = False


@router.get("/backups")
def backups_list(_: Principal = Depends(require("admin"))) -> dict[str, Any]:
    return {"items": backups.list_backups()}


@router.post("/backups")
def backups_create(body: BackupBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    job = jobs.enqueue(
        "backup.create", {"include_secrets": body.include_secrets, "actor": p.actor}, created_by=p.actor, max_attempts=1
    )
    audit(p.actor, "backup.requested", "Back-up aangevraagd", detail={"include_secrets": body.include_secrets})
    return {"job_id": job["id"], "status": job["status"]}


class RestoreBody(BaseModel):
    confirm: str


@router.post("/backups/{backup_id}/restore")
def backups_restore(backup_id: int, body: RestoreBody, p: Principal = Depends(require("admin"))) -> dict[str, Any]:
    row = get_db().fetchone("SELECT file FROM backups WHERE id=%s", (backup_id,))
    if row is None:
        raise HTTPException(404, detail="Back-up niet gevonden")
    if body.confirm != row["file"]:
        raise HTTPException(422, detail="Typ de bestandsnaam van de back-up ter bevestiging")
    try:
        return backups.restore_backup(backup_id, p.actor)
    except backups.BackupError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
