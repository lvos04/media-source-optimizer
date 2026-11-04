"""Usenet providers and block accounts.

Each provider is mirrored into ``sources`` (``provider:<slug>`` or ``block:<slug>``). Credentials live only in
the vault (``provider.<slug>.username`` / ``provider.<slug>.password``); the API only reports ``ingesteld``/
``leeg``. Logs and audit entries name the provider by slug, never by host or username.

MSO opens at most ``usenet.check_connections`` (1–2) NNTP connections per provider, and never more than the
account's configured maximum, so SABnzbd keeps its own connections.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException

from mso import jobs
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.features.usenet.nntp import NNTPClient, NNTPError, check_login
from mso.security import UnsafeTarget, check_url
from mso.vault import get_vault

LOGGER = logging.getLogger(__name__)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

# Publicly documented server addresses; the user still fills in credentials, connections and retention.
PRESETS: list[dict[str, Any]] = [
    {
        "slug": "eweka",
        "label": "Eweka",
        "kind": "unlimited",
        "host": "news.eweka.nl",
        "port": 563,
        "ssl": True,
    },
    {
        "slug": "newshosting",
        "label": "Newshosting",
        "kind": "unlimited",
        "host": "news.newshosting.com",
        "port": 563,
        "ssl": True,
    },
    {"slug": "blok", "label": "Blokaccount", "kind": "block", "host": "", "port": 563, "ssl": True},
]

_slots: dict[str, threading.BoundedSemaphore] = {}
_slots_size: dict[str, int] = {}
_slots_lock = threading.Lock()


def source_id(slug: str, kind: str) -> str:
    return f"{'block' if kind == 'block' else 'provider'}:{slug}"


def secret_name(slug: str, field: str) -> str:
    return f"provider.{slug}.{field}"


def validate_host(host: str, port: int) -> None:
    if not HOST_RE.fullmatch(host or ""):
        raise HTTPException(422, detail="Ongeldige hostnaam")
    try:
        check_url(f"http://{host}:{port}/", purpose="Usenet-provider")
    except UnsafeTarget as exc:
        raise HTTPException(422, detail=str(exc)) from exc


# ------------------------------------------------------------------ CRUD
def get(slug: str) -> dict[str, Any]:
    row = get_db().fetchone("SELECT * FROM usenet_providers WHERE slug=%s", (slug,))
    if row is None:
        raise HTTPException(404, detail="Provider niet gevonden")
    return dict(row)


def list_rows() -> list[dict[str, Any]]:
    return get_db().fetchall("SELECT * FROM usenet_providers ORDER BY kind DESC, label")


MISSING_CREDENTIALS_TEXT = (
    "Geen gebruikersnaam/wachtwoord in MSO ingesteld (SABnzbd geeft wachtwoorden niet door). Vul ze in via "
    "Bewerken om MSO zelf te laten testen; tot die tijd toont MSO de status die SABnzbd meldt."
)


def has_credentials(slug: str) -> bool:
    user, password = credentials(slug)
    return bool(user and password)


def health(
    row: dict[str, Any], sab: dict[str, Any] | None, creds_complete: bool
) -> dict[str, Any]:
    """Primary health shown for a provider: MSO's own NNTP check when MSO has credentials and has tested;
    otherwise SABnzbd's view of the linked server; otherwise 'missing_credentials'/'unknown'."""
    own_tested = row["status"] not in ("unknown", "missing_credentials")
    if creds_complete and own_tested:
        return {"status": row["status"], "detail": row["status_detail"], "source": "mso"}
    if sab and sab["status"] not in ("unknown",):
        return {
            "status": sab["status"],
            "detail": sab.get("message") or sab.get("explanation"),
            "source": "sabnzbd",
        }
    if not creds_complete:
        return {"status": "missing_credentials", "detail": MISSING_CREDENTIALS_TEXT, "source": "mso"}
    return {"status": row["status"], "detail": row["status_detail"], "source": "mso"}


def public(
    row: dict[str, Any],
    *,
    block_usage: dict[str, int] | None = None,
    sab_states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from mso.features.usenet import serverstatus  # lazy: serverstatus imports the SAB connector

    vault = get_vault()
    slug = row["slug"]
    if sab_states is None:
        sab_states = serverstatus.current() if row["sab_server"] else {}
    sab = serverstatus.public(sab_states.get(row["sab_server"] or ""))
    cred = {
        "username": vault.status(secret_name(slug, "username")),
        "password": vault.status(secret_name(slug, "password")),
    }
    complete = all(v == "ingesteld" for v in cred.values())
    used = (block_usage or {}).get(row["sab_server"] or "") if row["kind"] == "block" else None
    size_b = int(row["block_size_gb"] * 1024**3) if row["block_size_gb"] else None
    return {
        "slug": slug,
        "source_id": source_id(slug, row["kind"]),
        "label": row["label"],
        "kind": row["kind"],
        "host": row["host"],
        "port": row["port"],
        "ssl": row["ssl"],
        "connections": row["connections"],
        "mso_connections": effective_connections(row["connections"]),
        "retention_days": row["retention_days"],
        "block_size_gb": row["block_size_gb"],
        "block_used_bytes": used,
        "block_size_bytes": size_b,
        "block_used_ratio": round(used / size_b, 4) if used is not None and size_b else None,
        "sab_server": row["sab_server"],
        "enabled": row["enabled"],
        "notes": row["notes"],
        "status": row["status"],
        "status_detail": row["status_detail"],
        "last_check_at": row["last_check_at"],
        "last_ok_at": row["last_ok_at"],
        "capabilities": row["capabilities"],
        "credentials": cred,
        "credentials_complete": complete,
        "sab_status": sab,
        "health": health(row, sab, complete),
        "updated_at": row["updated_at"],
    }


def _mirror(row: dict[str, Any]) -> None:
    outcomes.upsert_source(
        source_id(row["slug"], row["kind"]),
        "block_account" if row["kind"] == "block" else "usenet_provider",
        row["label"],
        "mso",
        enabled=row["enabled"],
        meta={
            "sab_server": row["sab_server"],
            "retention_days": row["retention_days"],
            "kind": row["kind"],
            "block_size_gb": row["block_size_gb"],
        },
    )


FIELDS = (
    "label",
    "kind",
    "host",
    "port",
    "ssl",
    "connections",
    "retention_days",
    "block_size_gb",
    "sab_server",
    "enabled",
    "notes",
)


def create(data: dict[str, Any], *, username: str | None, password: str | None, actor: str) -> dict[str, Any]:
    slug = (data.get("slug") or "").strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise HTTPException(422, detail="Slug: kleine letters, cijfers en '-' (max 40)")
    db = get_db()
    if db.scalar("SELECT count(*) FROM usenet_providers WHERE slug=%s", (slug,)):
        raise HTTPException(409, detail="Er bestaat al een provider met deze slug")
    validate_host(data["host"], int(data.get("port") or 563))
    vals = {k: data.get(k) for k in FIELDS}
    vals["kind"] = vals["kind"] or "unlimited"
    vals["port"] = vals["port"] or 563
    vals["ssl"] = True if vals["ssl"] is None else vals["ssl"]
    vals["connections"] = vals["connections"] or 10
    vals["enabled"] = True if vals["enabled"] is None else vals["enabled"]
    vals["notes"] = vals["notes"] or ""
    db.execute(
        f"INSERT INTO usenet_providers(slug,{','.join(FIELDS)}) VALUES (%s,{','.join(['%s'] * len(FIELDS))})",
        (slug, *[vals[k] for k in FIELDS]),
    )
    _set_credentials(slug, username, password)
    _sync_credential_status(slug)
    row = get(slug)
    _mirror(row)
    audit(
        actor,
        "usenet.provider_created",
        f"Usenet-provider '{row['label']}' toegevoegd",
        target=slug,
        detail={"kind": row["kind"], "credentials_set": bool(username or password)},
    )
    return row


def update(
    slug: str, data: dict[str, Any], *, username: str | None, password: str | None, actor: str
) -> dict[str, Any]:
    cur = get(slug)
    vals = {k: (data[k] if k in data and data[k] is not None else cur[k]) for k in FIELDS}
    for nullable in ("retention_days", "block_size_gb", "sab_server"):
        if nullable in data:  # explicit null clears
            vals[nullable] = data[nullable] or None
    if vals["host"] != cur["host"] or vals["port"] != cur["port"]:
        validate_host(vals["host"], int(vals["port"]))
    get_db().execute(
        f"UPDATE usenet_providers SET {', '.join(f'{k}=%s' for k in FIELDS)}, updated_at=now() WHERE slug=%s",
        (*[vals[k] for k in FIELDS], slug),
    )
    _set_credentials(slug, username, password)
    _sync_credential_status(slug)
    row = get(slug)
    if row["kind"] != cur["kind"]:  # source id changes with the kind; keep the old one but disabled
        outcomes.upsert_source(
            source_id(slug, cur["kind"]),
            "block_account" if cur["kind"] == "block" else "usenet_provider",
            cur["label"],
            "mso",
            enabled=False,
        )
    _mirror(row)
    changed = [k for k in FIELDS if vals[k] != cur[k] and k not in ("host",)]
    if vals["host"] != cur["host"]:
        changed.append("server")
    audit(
        actor,
        "usenet.provider_updated",
        f"Usenet-provider '{row['label']}' bijgewerkt",
        target=slug,
        detail={
            "fields": changed,
            "credentials_changed": [
                n for n, v in (("username", username), ("password", password)) if v is not None
            ],
        },
    )
    return row


def delete(slug: str, *, actor: str) -> None:
    cur = get(slug)
    db = get_db()
    db.execute("DELETE FROM usenet_providers WHERE slug=%s", (slug,))
    get_vault().delete_prefix(f"provider.{slug}.")
    outcomes.upsert_source(
        source_id(slug, cur["kind"]),
        "block_account" if cur["kind"] == "block" else "usenet_provider",
        cur["label"],
        "mso",
        enabled=False,
        meta={"removed": True},
    )
    audit(
        actor,
        "usenet.provider_deleted",
        f"Usenet-provider '{cur['label']}' verwijderd (inloggegevens gewist)",
        target=slug,
    )


def _set_credentials(slug: str, username: str | None, password: str | None) -> None:
    vault = get_vault()
    if username is not None:
        vault.set(secret_name(slug, "username"), username.strip())
    if password is not None:
        vault.set(secret_name(slug, "password"), password)


def _sync_credential_status(slug: str) -> None:
    """No credentials → 'missing_credentials' (never 'auth_failed'); credentials (re)entered → 'unknown' so
    the next check tests them."""
    db = get_db()
    if has_credentials(slug):
        db.execute(
            "UPDATE usenet_providers SET status='unknown', status_detail=NULL WHERE slug=%s "
            "AND status='missing_credentials'",
            (slug,),
        )
    else:
        db.execute(
            "UPDATE usenet_providers SET status='missing_credentials', status_detail=%s WHERE slug=%s",
            (MISSING_CREDENTIALS_TEXT, slug),
        )


def credentials(slug: str) -> tuple[str, str]:
    vault = get_vault()
    return (vault.get(secret_name(slug, "username")) or "", vault.get(secret_name(slug, "password")) or "")


# ------------------------------------------------------------------ connections
def effective_connections(configured: int) -> int:
    try:
        mine = int(sr.get("usenet.check_connections"))
    except Exception:
        mine = 1
    return max(1, min(2, mine, int(configured or 1)))


def _semaphore(slug: str, size: int) -> threading.BoundedSemaphore:
    with _slots_lock:
        if slug not in _slots or _slots_size.get(slug) != size:
            _slots[slug] = threading.BoundedSemaphore(size)
            _slots_size[slug] = size
        return _slots[slug]


@contextmanager
def connection_slot(row: dict[str, Any], wait_s: float = 30.0) -> Iterator[None]:
    """Process-wide limit per provider. (The API and the worker are separate processes; each check uses 1
    connection and the default is 1, so the worst case stays within 2 even then.)"""
    sem = _semaphore(row["slug"], effective_connections(row["connections"]))
    if not sem.acquire(timeout=wait_s):
        raise NNTPError("rate_limited", "MSO gebruikt al het maximum aantal verbindingen voor deze provider")
    try:
        yield
    finally:
        sem.release()


def timeout_s() -> float:
    try:
        return float(sr.get("usenet.nntp_timeout_s"))
    except Exception:
        return 15.0


@contextmanager
def session(row: dict[str, Any]) -> Iterator[NNTPClient]:
    """Connected and authenticated client inside a connection slot."""
    try:
        validate_host(row["host"], row["port"])
    except HTTPException as exc:
        raise NNTPError("unreachable", str(exc.detail)) from None
    user, password = credentials(row["slug"])
    with connection_slot(row):
        client = NNTPClient(row["host"], row["port"], use_ssl=row["ssl"], timeout=timeout_s())
        try:
            client.connect()
            if user:
                client.login(user, password)
            yield client
        finally:
            client.quit()


# ------------------------------------------------------------------ status check
def check(slug: str, *, actor: str = "system") -> dict[str, Any]:
    row = get(slug)
    user, password = credentials(slug)
    if not user or not password:
        # Nothing was tested: never report this as a refused login.
        if row["status"] != "missing_credentials":
            get_db().execute(
                "UPDATE usenet_providers SET status='missing_credentials', status_detail=%s WHERE slug=%s",
                (MISSING_CREDENTIALS_TEXT, slug),
            )
        return {
            "status": "missing_credentials",
            "message": MISSING_CREDENTIALS_TEXT,
            "capabilities": [],
            "latency_ms": None,
            "code": None,
            "tested": False,
            "provider": public(get(slug)),
        }
    else:
        try:
            validate_host(row["host"], row["port"])
            with connection_slot(row):
                result = check_login(
                    row["host"],
                    row["port"],
                    use_ssl=row["ssl"],
                    username=user,
                    password=password,
                    timeout=timeout_s(),
                )
        except HTTPException as exc:
            result = {
                "status": "unreachable",
                "message": str(exc.detail),
                "capabilities": [],
                "latency_ms": None,
                "code": None,
            }
        except NNTPError as exc:
            result = {
                "status": exc.status,
                "message": str(exc),
                "capabilities": [],
                "latency_ms": None,
                "code": exc.code,
            }
    ok = result["status"] == "ok"
    db = get_db()
    db.execute(
        "UPDATE usenet_providers SET status=%s, status_detail=%s, last_check_at=now(), "
        "last_ok_at=CASE WHEN %s THEN now() ELSE last_ok_at END, "
        "capabilities=CASE WHEN %s THEN %s ELSE capabilities END WHERE slug=%s",
        (result["status"], result["message"], ok, ok, jsonb(result["capabilities"]), slug),
    )
    sid = source_id(slug, row["kind"])
    outcomes.observe(
        sid, "nntp.check_ok", 1.0 if ok else 0.0, {"status": result["status"], "code": result["code"]}
    )
    if result["latency_ms"] is not None:
        outcomes.observe(sid, "nntp.latency_ms", float(result["latency_ms"]))
    if row["status"] != result["status"]:
        LOGGER.info("provider %s: status %s → %s", slug, row["status"], result["status"])
        audit(
            actor,
            "usenet.provider_status",
            f"Provider '{row['label']}': {result['status']}",
            target=slug,
            detail={"before": row["status"], "after": result["status"], "message": result["message"]},
        )
    return {**result, "tested": True, "provider": public(get(slug))}


# ------------------------------------------------------------------ periodic checks
def due_for_check() -> list[str]:
    every_min = int(sr.get("usenet.check_interval_min"))
    rows = get_db().fetchall(
        "SELECT slug FROM usenet_providers WHERE enabled AND (last_check_at IS NULL OR "
        "last_check_at < now() - make_interval(mins => %s)) ORDER BY last_check_at NULLS FIRST",
        (every_min,),
    )
    # Providers without credentials are never checked periodically (they would only produce a false
    # "login refused"); their health comes from SABnzbd's own server status.
    return [r["slug"] for r in rows if has_credentials(r["slug"])]


@jobs.handler("usenet.provider_checks")
def _job_checks(ctx: jobs.JobContext) -> dict[str, Any]:
    slugs = ctx.payload.get("slugs") or due_for_check()
    results = {}
    for slug in slugs:
        if not has_credentials(slug):
            results[slug] = "missing_credentials"
            continue
        try:
            results[slug] = check(slug)["status"]
        except HTTPException:
            results[slug] = "missing"
        ctx.heartbeat()
    return {"checked": results}


def _any_provider() -> bool:
    return bool(get_db().scalar("SELECT count(*) FROM usenet_providers WHERE enabled"))


jobs.every("usenet.provider_checks", 600, enabled=_any_provider)


def import_from_sab(actor: str) -> list[dict[str, Any]]:
    """Create provider rows for SABnzbd servers that are not linked yet (host/port/SSL/connections/retention
    from SABnzbd; SABnzbd never exposes passwords, so credentials stay empty until the admin fills them)."""
    from mso.connectors.sabnzbd import SabClient

    with SabClient.from_config() as client:
        servers = client.servers()
    linked = {r["sab_server"] for r in list_rows() if r["sab_server"]}
    existing = {r["slug"] for r in list_rows()}
    created = []
    for s in servers:
        if s["name"] in linked or not s["host"]:
            continue
        base = (
            re.sub(r"[^a-z0-9-]+", "-", (s["displayname"] or s["name"]).lower()).strip("-")[:36] or "server"
        )
        slug, n = base, 2
        while slug in existing:
            slug, n = f"{base}-{n}", n + 1
        try:
            validate_host(s["host"], s["port"] or 563)
        except HTTPException:
            continue
        row = create(
            {
                "slug": slug,
                "label": s["displayname"] or s["name"],
                "kind": "unlimited",
                "host": s["host"],
                "port": s["port"] or 563,
                "ssl": s["ssl"],
                "connections": max(1, min(200, s["connections"] or 1)),
                "retention_days": s["retention"] or None,
                "sab_server": s["name"],
                "enabled": bool(s["enable"]),
            },
            username=None,
            password=None,
            actor=actor,
        )
        existing.add(slug)
        created.append(row)
    if created:
        from mso.features.usenet import serverstatus

        try:  # imported providers have no credentials: show SABnzbd's own server status right away
            serverstatus.refresh()
        except Exception as exc:  # import itself succeeded
            LOGGER.info("SABnzbd-serverstatus na import niet beschikbaar: %s", type(exc).__name__)
    return created
