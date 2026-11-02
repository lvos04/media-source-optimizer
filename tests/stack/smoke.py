"""Container-stack smoke / end-to-end test for Media Source Optimizer (PLAN §7 tests 2 and 10).

Runs against a stack started by ``tests/stack/run.sh`` (compose project ``msostack``) and checks, over real
HTTP and real containers:

  health of every container · first-run admin setup · login/logout · CSRF and role enforcement · every
  connector configured against its mock + connection test · SSRF refusals · read APIs · backup create +
  restore (all-or-nothing, sessions invalidated) · restart-safe jobs (crashed job lease recovered, worker
  process killed with SIGKILL and restarted by the restart policy) · persistence across
  ``docker compose restart`` and ``down``/``up`` · no secret in ``docker compose logs`` · no ERROR/Traceback in
  the logs · container CPU/memory at idle and under load (``docker stats``).

Writes ``results/smoke-report.json`` and ``results/smoke-report.md``. Exit code 1 when a check failed.

Environment (set by run.sh):
  MSO_STACK_COMPOSE   the compose command prefix (``docker compose -p msostack -f … -f …``)
  MSO_STACK_BASE      URL used for API calls (the web container when present, else the API port)
  MSO_STACK_API       direct API URL (127.0.0.1:38200, test override only)
  MSO_STACK_WEB       web URL or empty when the web image is not part of the run
  MSO_STACK_SECRETS   JSON {mock_name: {field: value}} — credentials the mocks were started with
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import shlex
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
COMPOSE = shlex.split(os.environ.get("MSO_STACK_COMPOSE", "docker compose -p msostack"))
BASE = os.environ.get("MSO_STACK_BASE", "http://127.0.0.1:38200").rstrip("/")
API = os.environ.get("MSO_STACK_API", "http://127.0.0.1:38200").rstrip("/")
WEB = os.environ.get("MSO_STACK_WEB", "").rstrip("/")
PROJECT = COMPOSE[COMPOSE.index("-p") + 1] if "-p" in COMPOSE else "msostack"
MOCK_SECRETS: dict[str, dict[str, str]] = json.loads(os.environ.get("MSO_STACK_SECRETS", "{}") or "{}")
ONE_SHOT = {"init", "migrate"}

ADMIN = "stackadmin"
ADMIN_PW = os.environ.get("MSO_STACK_ADMIN_PW") or "Stack-" + secrets.token_urlsafe(18)
VIEWER = "stackviewer"
VIEWER_PW = "Viewer-" + secrets.token_urlsafe(18)
# every secret we hand to the stack; none may ever appear in container logs
SENSITIVE: set[str] = {ADMIN_PW, VIEWER_PW}


# ---------------------------------------------------------------- helpers
@dataclass
class Check:
    name: str
    status: str = "pending"  # ok | failed | skipped
    detail: str = ""
    seconds: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)


CHECKS: list[Check] = []
LOG_ARCHIVE: dict[str, str] = {}  # logs of containers removed by `down` (checked for secrets/errors too)
STATS: dict[str, Any] = {}


class Skip(Exception):
    pass


def check(name: str) -> Callable[[Callable[[Check], None]], Callable[[], None]]:
    def deco(fn: Callable[[Check], None]) -> Callable[[], None]:
        def run() -> None:
            c = Check(name)
            CHECKS.append(c)
            start = time.monotonic()
            try:
                fn(c)
                c.status = "ok"
            except Skip as exc:
                c.status, c.detail = "skipped", str(exc)
            except Exception as exc:
                c.status = "failed"
                c.detail = (c.detail + " | " if c.detail else "") + f"{type(exc).__name__}: {exc}"
                c.data["traceback"] = traceback.format_exc(limit=4)
            c.seconds = round(time.monotonic() - start, 2)
            print(f"[{c.status.upper():7}] {name} ({c.seconds}s) {c.detail[:300]}", flush=True)

        return run

    return deco


def compose(*args: str, check_rc: bool = True, timeout: int = 300) -> str:
    proc = subprocess.run([*COMPOSE, *args], capture_output=True, text=True, timeout=timeout, check=False)
    if check_rc and proc.returncode != 0:
        raise RuntimeError(f"compose {' '.join(args)} → rc {proc.returncode}: {proc.stderr.strip()[-600:]}")
    return proc.stdout


def ps() -> list[dict[str, Any]]:
    out = compose("ps", "-a", "--format", "json")
    rows: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        rows.extend(parsed if isinstance(parsed, list) else [parsed])
    return rows


def wait_healthy(timeout: float = 240) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    last: dict[str, str] = {}
    while time.monotonic() < deadline:
        rows = ps()
        last = {r["Service"]: f"{r['State']}/{r.get('Health') or '-'}/{r.get('ExitCode')}" for r in rows}
        ready = True
        for r in rows:
            if r["Service"] in ONE_SHOT:
                ready &= r["State"] == "exited" and int(r.get("ExitCode") or 0) == 0
                if r["State"] == "exited" and int(r.get("ExitCode") or 0) != 0:
                    raise RuntimeError(f"{r['Service']} faalde (exit {r.get('ExitCode')})")
            elif r.get("Health"):
                ready &= r["State"] == "running" and r["Health"] == "healthy"
            else:
                ready &= r["State"] == "running"
        if ready and rows:
            return last
        time.sleep(2)
    raise TimeoutError(f"stack niet gezond binnen {timeout}s: {last}")


class Session:
    """Browser-like client: cookie jar + CSRF header from login."""

    def __init__(self, base: str = BASE) -> None:
        self.c = httpx.Client(base_url=base, timeout=60, follow_redirects=False)
        self.csrf: str | None = None

    def login(self, user: str, pw: str) -> httpx.Response:
        r = self.c.post("/api/v1/auth/login", json={"username": user, "password": pw})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def req(self, method: str, path: str, *, csrf: bool = True, **kw: Any) -> httpx.Response:
        headers = dict(kw.pop("headers", {}) or {})
        if csrf and self.csrf and method.upper() not in ("GET", "HEAD", "OPTIONS"):
            headers["X-CSRF-Token"] = self.csrf
        return self.c.request(method, path, headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.req("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.req("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> httpx.Response:
        return self.req("PUT", path, **kw)


def expect(r: httpx.Response, *codes: int) -> httpx.Response:
    if r.status_code not in codes:
        raise AssertionError(
            f"{r.request.method} {r.request.url.path} → {r.status_code} (verwacht {codes}): {r.text[:300]}"
        )
    return r


def wait_job(s: Session, job_id: int, timeout: float = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        job = expect(s.get(f"/api/v1/jobs/{job_id}"), 200).json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(1)
    raise TimeoutError(f"job {job_id} niet klaar: {job.get('status')}")


def psql(sql: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "mso", "-d", "mso", "-At", "-c", sql)


def docker_stats() -> dict[str, dict[str, Any]]:
    proc = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    out: dict[str, dict[str, Any]] = {}
    for line in proc.stdout.splitlines():
        row = json.loads(line)
        name = row["Name"]
        if not name.startswith(f"{PROJECT}-"):
            continue
        svc = name[len(PROJECT) + 1 :].rsplit("-", 1)[0]
        mem_used = row["MemUsage"].split("/")[0].strip()
        out[svc] = {
            "cpu_pct": float(row["CPUPerc"].rstrip("%") or 0),
            "mem": mem_used,
            "mem_mib": round(_to_mib(mem_used), 1),
            "pids": int(row.get("PIDs") or 0),
            "limit": row["MemUsage"].split("/")[1].strip(),
        }
    return out


def _to_mib(v: str) -> float:
    units = {
        "B": 1 / 2**20,
        "KiB": 1 / 1024,
        "MiB": 1,
        "GiB": 1024,
        "kB": 1e3 / 2**20,
        "MB": 1e6 / 2**20,
        "GB": 1e9 / 2**20,
    }
    for u in sorted(units, key=len, reverse=True):
        if v.endswith(u):
            return float(v[: -len(u)]) * units[u]
    return 0.0


# ---------------------------------------------------------------- state shared between checks
S: dict[str, Any] = {}


@check("stack: alle containers gezond, init + migrate exit 0")
def c_health(c: Check) -> None:
    c.data["services"] = wait_healthy()
    r = httpx.get(f"{API}/healthz", timeout=10)
    expect(r, 200)
    c.data["healthz"] = r.json()
    c.detail = ", ".join(f"{k}={v}" for k, v in sorted(c.data["services"].items()))


@check("web: UI en same-origin proxy naar core")
def c_web(c: Check) -> None:
    if not WEB:
        raise Skip("web-image niet in deze run")
    r = httpx.get(f"{WEB}/login", timeout=20)
    expect(r, 200)
    r2 = httpx.get(f"{WEB}/api/v1/setup/status", timeout=20)
    expect(r2, 200)
    if "X-Correlation-Id" not in r2.headers:
        raise AssertionError("antwoord kwam niet van core (geen X-Correlation-Id)")
    nz = httpx.get(f"{WEB}/newznab/api", params={"t": "caps"}, timeout=20)
    tz = httpx.get(f"{WEB}/torznab/api", params={"t": "caps"}, timeout=20)
    for name, resp in (("newznab", nz), ("torznab", tz)):
        if resp.status_code == 502:
            raise AssertionError(f"/{name} proxy bereikt core niet (502)")
    c.data = {
        "login": r.status_code,
        "setup_status": r2.status_code,
        "newznab_caps": nz.status_code,
        "torznab_caps": tz.status_code,
        "newznab_content_type": nz.headers.get("content-type"),
        "newznab_from_core": "X-Correlation-Id" in nz.headers or "xml" in nz.headers.get("content-type", ""),
    }
    c.detail = (
        f"/login 200, /api/v1 via proxy 200, /newznab/api?t=caps → {nz.status_code}, "
        f"/torznab/api?t=caps → {tz.status_code}"
    )


@check("setup: eerste beheerder aanmaken, tweede keer geweigerd")
def c_setup(c: Check) -> None:
    s = Session()
    st = expect(s.get("/api/v1/setup/status"), 200).json()
    if st["admin_exists"]:
        raise AssertionError("verse stack heeft al een beheerder")
    r = expect(s.c.post("/api/v1/setup/admin", json={"username": ADMIN, "password": ADMIN_PW}), 200)
    s.csrf = r.json()["csrf_token"]
    expect(Session().c.post("/api/v1/setup/admin", json={"username": "x" + ADMIN, "password": ADMIN_PW}), 409)
    S["admin"] = s
    c.detail = f"beheerder '{ADMIN}' aangemaakt; tweede setup → 409"


@check("auth: uitloggen, foutief wachtwoord 401, inloggen, /auth/me")
def c_auth(c: Check) -> None:
    s: Session = S["admin"]
    expect(s.post("/api/v1/auth/logout"), 200)
    expect(s.get("/api/v1/auth/me"), 401)
    expect(Session().login(ADMIN, ADMIN_PW + "x"), 401)
    expect(s.login(ADMIN, ADMIN_PW), 200)
    me = expect(s.get("/api/v1/auth/me"), 200).json()
    if me["user"]["role"] != "admin":
        raise AssertionError(me)
    c.detail = "logout → me 401; fout wachtwoord 401; login → me admin"


@check("security: CSRF-token verplicht bij mutaties")
def c_csrf(c: Check) -> None:
    s: Session = S["admin"]
    r = s.post("/api/v1/system/pause", csrf=False)
    expect(r, 403)
    r_bad = s.post("/api/v1/system/pause", csrf=False, headers={"X-CSRF-Token": "fout"})
    expect(r_bad, 403)
    expect(s.post("/api/v1/system/pause"), 200)
    expect(s.post("/api/v1/system/resume"), 200)
    c.detail = "zonder token 403, fout token 403, met token 200"


@check("security: rollen (viewer mag geen config/gebruikers wijzigen)")
def c_roles(c: Check) -> None:
    s: Session = S["admin"]
    expect(s.post("/api/v1/users", json={"username": VIEWER, "password": VIEWER_PW, "role": "viewer"}), 200)
    v = Session()
    expect(v.login(VIEWER, VIEWER_PW), 200)
    expect(v.get("/api/v1/system/health"), 200)
    expect(v.put("/api/v1/settings", json={"values": {"backup.keep": 3}}), 403)
    expect(v.get("/api/v1/users"), 403)
    expect(v.post("/api/v1/backups", json={}), 403)
    expect(Session().get("/api/v1/system/health"), 401)
    c.detail = "viewer: lezen 200; settings/users/backups 403; anoniem 401"


def _mock_names() -> list[str]:
    return sorted(svc.removeprefix("mock-") for svc in {r["Service"] for r in ps()} if svc.startswith("mock-"))


@check("koppelingen: elke connector naar zijn mock + verbindingstest")
def c_connectors(c: Check) -> None:
    s: Session = S["admin"]
    conns = expect(s.get("/api/v1/connectors"), 200).json()
    mocks = _mock_names()
    c.data["connectors"] = [x["id"] for x in conns]
    c.data["mocks"] = mocks
    if not conns:
        raise Skip("nog geen connectoren geregistreerd")
    results: dict[str, Any] = {}
    failures = []
    for conn in conns:
        cid = conn["id"]
        if cid not in mocks:
            results[cid] = {"status": "geen mock in deze stack"}
            continue
        creds = dict(MOCK_SECRETS.get(cid, {}))
        fields = [f["name"] for f in conn["secret_fields"]]
        sec = {k: v for k, v in creds.items() if k in fields}
        if not sec:  # mock without known credentials: give required fields a unique value
            sec = {
                f["name"]: f"stack-{cid}-{f['name']}-{secrets.token_hex(6)}"
                for f in conn["secret_fields"]
                if f["required"]
            }
        SENSITIVE.update(v for v in sec.values() if len(v) >= 6)
        body: dict[str, Any] = {"url": f"http://mock-{cid}:8080", "enabled": True, "secrets": sec}
        opts = MOCK_SECRETS.get(f"{cid}.__options__")
        if opts:
            body["options"] = opts
        put = expect(s.put(f"/api/v1/connectors/{cid}", json=body), 200).json()
        for f in put["secret_fields"]:
            if f["name"] in sec and f["status"] == "leeg":
                failures.append(f"{cid}: geheim {f['name']} niet opgeslagen")
        if any(v in json.dumps(put) for v in sec.values()):
            failures.append(f"{cid}: geheim komt terug in API-antwoord")
        t = expect(s.post(f"/api/v1/connectors/{cid}/test"), 200).json()
        results[cid] = {
            "ok": t["ok"],
            "status": t["status"],
            "message": t["message"],
            "version": t.get("version"),
            "latency_ms": t.get("latency_ms"),
        }
        if not t["ok"]:
            failures.append(f"{cid}: test {t['status']} — {t['message']}")
    c.data["results"] = results
    S["configured"] = [k for k, v in results.items() if "ok" in v]
    c.detail = "; ".join(f"{k}: {v.get('status')}" for k, v in results.items())
    if failures:
        raise AssertionError("; ".join(failures))


@check("security: SSRF-weigeringen (metadata, schema, credentials in URL)")
def c_ssrf(c: Check) -> None:
    s: Session = S["admin"]
    conns = expect(s.get("/api/v1/connectors"), 200).json()
    if not conns:
        raise Skip("geen connector om tegen te testen")
    cid = conns[0]["id"]
    before = conns[0]["url"]
    cases = {
        "http://169.254.169.254/latest/meta-data/": 422,
        "file:///etc/passwd": 422,
        "http://user:pw@mock-x:8080": 422,
        "gopher://127.0.0.1:25/": 422,
        "http://127.0.0.1:8200/": 422,
        "http://0.0.0.0:5432/": 422,
    }
    got = {}
    for url, code in cases.items():
        r = s.put(f"/api/v1/connectors/{cid}", json={"url": url})
        got[url] = r.status_code
        expect(r, code)
    t = expect(s.post(f"/api/v1/connectors/{cid}/test", json={"url": "http://169.254.169.254/"}), 200).json()
    if t["ok"]:
        raise AssertionError("test naar metadata-adres slaagde")
    after = expect(s.get("/api/v1/connectors"), 200).json()
    if next(x for x in after if x["id"] == cid)["url"] != before:
        raise AssertionError("geweigerde URL toch opgeslagen")
    c.data = {"connector": cid, "status_codes": got, "test_metadata": t["message"]}
    c.detail = f"{len(cases)} onveilige URL's → 422 (connector {cid}); test naar 169.254.169.254 geweigerd"


@check("API: lees-endpoints en OpenAPI")
def c_reads(c: Check) -> None:
    s: Session = S["admin"]
    paths = [
        "/api/v1/system/health",
        "/api/v1/system/info",
        "/api/v1/system/mode",
        "/api/v1/settings",
        "/api/v1/audit",
        "/api/v1/jobs",
        "/api/v1/changes",
        "/api/v1/users",
        "/api/v1/backups",
        "/api/v1/connectors",
        "/api/v1/setup/status",
    ]
    codes = {p: s.get(p).status_code for p in paths}
    bad = {p: code for p, code in codes.items() if code != 200}
    spec = expect(httpx.get(f"{API}/api/openapi.json", timeout=20), 200).json()
    info = s.get("/api/v1/system/info").json()
    health = s.get("/api/v1/system/health").json()
    ep = s.get("/api/v1/newznab/endpoint")
    c.data["newznab_endpoint"] = ep.json() if ep.status_code == 200 else ep.status_code
    c.data |= {
        "codes": codes,
        "openapi_paths": len(spec["paths"]),
        "schema_version": info["schema_version"],
        "migrations": [m["version"] for m in info["migrations"]],
        "health": health,
    }
    c.detail = (
        f"{len(paths)} endpoints 200, OpenAPI {len(spec['paths'])} paden, schema v{info['schema_version']}, "
        f"health={health['status']}"
    )
    if bad:
        raise AssertionError(f"niet-200: {bad}")
    comps = {x["name"]: x["status"] for x in health["components"]}
    for name in ("database", "worker", "appdata", "work"):
        if comps.get(name) != "healthy":
            raise AssertionError(f"health-component {name} = {comps.get(name)}")


@check("back-up: maken via worker-job, checksum, bestand aanwezig")
def c_backup(c: Check) -> None:
    s: Session = S["admin"]
    job = expect(s.post("/api/v1/backups", json={}), 200).json()
    done = wait_job(s, job["job_id"])
    if done["status"] != "done":
        raise AssertionError(f"backup-job {done['status']}: {done.get('error')}")
    items = expect(s.get("/api/v1/backups"), 200).json()["items"]
    mine = next(b for b in items if b["file"] == done["result"]["file"])
    if not mine["present"] or not mine["sha256"] or mine["includes_secrets"]:
        raise AssertionError(mine)
    S["backup"] = mine
    c.data = {"file": mine["file"], "size_bytes": mine["size_bytes"], "schema_version": mine["schema_version"]}
    c.detail = f"{mine['file']} ({mine['size_bytes']} B), zonder geheimen"


@check("herstel: restore zet DB terug, sessies vervallen, koppelingsgeheimen blijven")
def c_restore(c: Check) -> None:
    s: Session = S["admin"]
    b = S.get("backup") or {}
    if not b:
        raise Skip("geen back-up")
    expect(
        s.post("/api/v1/users", json={"username": "na-backup", "password": "Na-Backup-12345", "role": "viewer"}), 200
    )
    # restore requires typing the file name as confirmation
    r = expect(s.post(f"/api/v1/backups/{b['id']}/restore", json={"confirm": b["file"]}), 200).json()
    expect(s.get("/api/v1/auth/me"), 401)  # sessions are never part of a backup
    expect(s.login(ADMIN, ADMIN_PW), 200)
    users = [u["username"] for u in expect(s.get("/api/v1/users"), 200).json()["items"]]
    if "na-backup" in users or ADMIN not in users:
        raise AssertionError(f"gebruikers na restore: {users}")
    conns = expect(s.get("/api/v1/connectors"), 200).json()
    lost = [
        f"{x['id']}.{f['name']}"
        for x in conns
        if x["id"] in S.get("configured", [])
        for f in x["secret_fields"]
        if f["status"] == "leeg" and f["required"]
    ]
    if lost:
        raise AssertionError(f"geheimen kwijt na restore zonder geheimen: {lost}")
    corrupt = s.post("/api/v1/backups/999999/restore", json={"confirm": "bestaat-niet.dump"})
    c.data = {"restore": r, "users_after": users, "missing_backup_status": corrupt.status_code}
    expect(corrupt, 404, 422)
    c.detail = f"hersteld {r['restored']} (vangnet {r['pre_restore_backup']}); onbekende back-up → 422"


@check("herstel: beschadigde en ontbrekende back-up geweigerd, database ongewijzigd")
def c_restore_corrupt(c: Check) -> None:
    s: Session = S["admin"]
    job = expect(s.post("/api/v1/backups", json={}), 200).json()
    done = wait_job(s, job["job_id"])
    if done["status"] != "done":
        raise AssertionError(done)
    fname = done["result"]["file"]
    b = next(x for x in expect(s.get("/api/v1/backups"), 200).json()["items"] if x["file"] == fname)
    users_before = expect(s.get("/api/v1/users"), 200).json()["items"]
    # flip bytes in the middle of the archive (as the app user, inside the api container)
    compose(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        f"p='/appdata/backups/{fname}';f=open(p,'r+b');f.seek(2000);f.write(b'CORRUPT!'*8);f.close()",
    )
    r1 = s.post(f"/api/v1/backups/{b['id']}/restore", json={"confirm": fname})
    expect(r1, 422)
    compose("exec", "-T", "api", "rm", f"/appdata/backups/{fname}")
    r2 = s.post(f"/api/v1/backups/{b['id']}/restore", json={"confirm": fname})
    expect(r2, 422)
    expect(s.get("/api/v1/auth/me"), 200)  # nothing was restored → session still valid
    if expect(s.get("/api/v1/users"), 200).json()["items"] != users_before:
        raise AssertionError("gebruikers gewijzigd na geweigerd herstel")
    c.data = {"corrupt": r1.json().get("detail"), "missing": r2.json().get("detail")}
    c.detail = f"beschadigd → 422 ({r1.json().get('detail')}); ontbrekend → 422 ({r2.json().get('detail')})"


@check("jobs: onderbroken job (verlopen lease) wordt na worker-start hervat")
def c_job_recovery(c: Check) -> None:
    s: Session = S["admin"]
    compose("stop", "worker")
    try:
        job = expect(s.post("/api/v1/backups", json={}), 200).json()
        jid = int(job["job_id"])
        # simulate a worker that crashed mid-job: running, lease long expired
        psql(
            f"UPDATE jobs SET status='running', locked_by='stack-crashed-worker', attempts=attempts+1, "
            f"heartbeat_at=now() - interval '10 minutes' WHERE id={jid}"
        )
        state = psql(f"SELECT status FROM jobs WHERE id={jid}").strip()
        if state != "running":
            raise AssertionError(f"job-status niet gezet: {state}")
    finally:
        compose("start", "worker")
    done = wait_job(s, jid, timeout=180)
    c.data = {"job": {k: done.get(k) for k in ("id", "status", "attempts", "summary", "error")}}
    if done["status"] != "done":
        raise AssertionError(f"job {jid}: {done['status']} {done.get('error')}")
    c.detail = f"job {jid} running(crash) → {done['status']}; samenvatting: {done.get('summary')!r}"


@check("jobs: worker-proces SIGKILL → restart policy herstart, worker weer gezond en functioneel")
def c_worker_kill(c: Check) -> None:
    s: Session = S["admin"]
    cid = compose("ps", "-q", "worker").strip()
    before = int(subprocess.check_output(["docker", "inspect", "-f", "{{.RestartCount}}", cid], text=True))
    killer = (
        "import os,signal\n"
        "me=os.getpid()\n"
        "for p in os.listdir('/proc'):\n"
        "  if p.isdigit() and int(p) not in (1, me):\n"
        "    try: cmd=open(f'/proc/{p}/cmdline','rb').read().split(b'\\0')\n"
        "    except OSError: continue\n"
        "    if b'mso' in cmd and b'worker' in cmd: os.kill(int(p), signal.SIGKILL); print(p)\n"
    )
    killed = compose("exec", "-T", "worker", "python", "-c", killer, check_rc=False).strip()
    deadline = time.monotonic() + 120
    after = before
    while time.monotonic() < deadline:
        after = int(subprocess.check_output(["docker", "inspect", "-f", "{{.RestartCount}}", cid], text=True))
        if after > before:
            break
        time.sleep(1)
    if after <= before:
        raise AssertionError(f"worker niet herstart (gedood pid {killed!r})")
    wait_healthy(180)
    job = expect(s.post("/api/v1/backups", json={}), 200).json()
    done = wait_job(s, job["job_id"])
    if done["status"] != "done":
        raise AssertionError(done)
    c.data = {"killed_pid": killed, "restart_count": [before, after]}
    c.detail = f"pid {killed} gedood; RestartCount {before}→{after}; nieuwe job {done['status']}"


def _persisted(s: Session) -> dict[str, Any]:
    users = sorted(u["username"] for u in expect(s.get("/api/v1/users"), 200).json()["items"])
    conns = {
        x["id"]: {"url": x["url"], "secrets": {f["name"]: f["status"] for f in x["secret_fields"]}}
        for x in expect(s.get("/api/v1/connectors"), 200).json()
    }
    backups = sorted(b["file"] for b in expect(s.get("/api/v1/backups"), 200).json()["items"])
    return {"users": users, "connectors": conns, "backups": backups}


@check("persistentie: docker compose restart (alle services) — data en login blijven")
def c_restart(c: Check) -> None:
    s: Session = S["admin"]
    before = _persisted(s)
    compose("restart", timeout=600)
    wait_healthy(300)
    s2 = Session()
    expect(s2.login(ADMIN, ADMIN_PW), 200)
    after = _persisted(s2)
    if after != before:
        raise AssertionError(f"verschil na restart: voor={before} na={after}")
    S["admin"] = s2
    c.detail = (
        f"{len(after['users'])} gebruikers, {len(after['connectors'])} koppelingen, "
        f"{len(after['backups'])} back-ups ongewijzigd"
    )


@check("persistentie: docker compose down + up (containers opnieuw aangemaakt)")
def c_down_up(c: Check) -> None:
    s: Session = S["admin"]
    before = _persisted(s)
    services = os.environ.get("MSO_STACK_SERVICES", "").split()
    for svc in sorted({r["Service"] for r in ps()}):
        LOG_ARCHIVE[svc] = LOG_ARCHIVE.get(svc, "") + compose("logs", "--no-color", "--no-log-prefix", svc)
    compose("down", timeout=600)  # no -v: bind-mounted datasets stay
    compose("up", "-d", *services, timeout=600)
    wait_healthy(300)
    s2 = Session()
    expect(s2.login(ADMIN, ADMIN_PW), 200)
    after = _persisted(s2)
    if after != before:
        raise AssertionError(f"verschil na down/up: voor={before} na={after}")
    S["admin"] = s2
    c.detail = "gebruikers, koppelingen (incl. versleutelde geheimen) en back-ups intact; login werkt"


@check("resources: docker stats in rust en onder belasting")
def c_stats(c: Check) -> None:
    s: Session = S["admin"]
    time.sleep(20)  # settle after the restart
    idle = [docker_stats() for _ in range(3)]
    STATS["idle"] = _aggregate(idle)
    stop = threading.Event()
    samples: list[dict[str, dict[str, Any]]] = []

    def sampler() -> None:
        while not stop.is_set():
            samples.append(docker_stats())

    t = threading.Thread(target=sampler, daemon=True)
    t.start()
    paths = [
        "/api/v1/system/health",
        "/api/v1/jobs",
        "/api/v1/audit",
        "/api/v1/connectors",
        "/api/v1/settings",
        "/api/v1/system/info",
    ]
    n = 0
    errors = 0
    start = time.monotonic()
    lat: list[float] = []

    def hammer() -> None:
        nonlocal n, errors
        local = Session()
        local.c.cookies = s.c.cookies
        for i in range(60):
            t0 = time.monotonic()
            r = local.get(paths[i % len(paths)])
            lat.append(time.monotonic() - t0)
            n += 1
            if r.status_code != 200:
                errors += 1

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for th in threads:
        th.start()
    # one backup job in parallel exercises worker + pg_dump
    job = expect(s.post("/api/v1/backups", json={}), 200).json()
    for th in threads:
        th.join()
    wait_job(s, job["job_id"])
    elapsed = time.monotonic() - start
    stop.set()
    t.join(timeout=60)
    STATS["load"] = _aggregate(samples)
    lat.sort()
    STATS["load_requests"] = {
        "requests": n,
        "errors": errors,
        "seconds": round(elapsed, 1),
        "rps": round(n / elapsed, 1),
        "p50_ms": round(lat[len(lat) // 2] * 1000, 1) if lat else None,
        "p95_ms": round(lat[int(len(lat) * 0.95)] * 1000, 1) if lat else None,
        "via": BASE,
    }
    c.data = STATS
    c.detail = (
        f"{n} verzoeken in {elapsed:.1f}s ({errors} fouten), p95 {STATS['load_requests']['p95_ms']} ms; "
        + ", ".join(
            f"{k} idle {v['mem_mib_max']} MiB / load {STATS['load'].get(k, {}).get('mem_mib_max')} MiB"
            for k, v in sorted(STATS["idle"].items())
            if not k.startswith("mock-")
        )
    )
    if errors:
        raise AssertionError(f"{errors} fouten onder belasting")


def _aggregate(samples: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for sample in samples:
        for svc, v in sample.items():
            a = agg.setdefault(
                svc, {"cpu_pct_max": 0.0, "mem_mib_max": 0.0, "pids_max": 0, "limit": v["limit"], "samples": 0}
            )
            a["cpu_pct_max"] = max(a["cpu_pct_max"], v["cpu_pct"])
            a["mem_mib_max"] = max(a["mem_mib_max"], v["mem_mib"])
            a["pids_max"] = max(a["pids_max"], v["pids"])
            a["samples"] += 1
    return agg


def _all_logs() -> dict[str, str]:
    """Per service: logs of containers removed by `down` + current logs. Also archived to results/logs."""
    out: dict[str, str] = {}
    (RESULTS / "logs").mkdir(parents=True, exist_ok=True)
    for svc in sorted({r["Service"] for r in ps()} | set(LOG_ARCHIVE)):
        out[svc] = LOG_ARCHIVE.get(svc, "") + compose("logs", "--no-color", "--no-log-prefix", svc, timeout=60)
        (RESULTS / "logs" / f"{svc}.full.log").write_text(out[svc])
    return out


@check("logs: geen geheimen in de logs van MSO-containers")
def c_secret_logs(c: Check) -> None:
    logs = _all_logs()
    db_pw = compose("exec", "-T", "api", "cat", "/run/mso-db/password").strip()
    SENSITIVE.add(db_pw)
    leaked: dict[str, list[str]] = {}
    for svc, text in logs.items():
        hits = [f"{v[:3]}…({len(v)} tekens)" for v in SENSITIVE if v and v in text]
        if hits:
            leaked[svc] = hits
    # Mocks are test doubles that log their own access lines (e.g. SABnzbd's ?apikey= query, like the real
    # service would); they are not part of MSO and are reported, not failed.
    mso_leaks = {k: v for k, v in leaked.items() if not k.startswith("mock-")}
    c.data = {
        "log_bytes": sum(len(t) for t in logs.values()),
        "secrets_checked": len(SENSITIVE),
        "mock_access_logs_with_secret": {k: v for k, v in leaked.items() if k.startswith("mock-")},
    }
    if mso_leaks:
        raise AssertionError(f"geheim(en) in MSO-logs: {mso_leaks}")
    c.detail = (
        f"{len(SENSITIVE)} geheimen (beheer-/viewer-wachtwoord, DB-wachtwoord, koppelingsgeheimen) niet "
        f"gevonden in {c.data['log_bytes']} bytes log van init/postgres/migrate/api/worker/web"
        + (
            f"; mock-toegangslogs met sleutel (geen MSO): {sorted(c.data['mock_access_logs_with_secret'])}"
            if c.data["mock_access_logs_with_secret"]
            else ""
        )
    )


# Lines that are expected consequences of the test itself, not faults:
#  - PostgreSQL logs "FATAL: terminating connection due to administrator command" for every pooled client
#    connection when the test stops/restarts postgres (docker compose restart / down).
ALLOWED_ERROR_LINES = ("terminating connection due to administrator command",)


@check("logs: 0 foutmeldingen (ERROR/CRITICAL/Traceback/FATAL/PANIC)")
def c_error_logs(c: Check) -> None:
    hits: dict[str, list[str]] = {}
    for svc in sorted({r["Service"] for r in ps()}):
        out = LOG_ARCHIVE.get(svc, "") + compose("logs", "--no-color", "--no-log-prefix", svc, timeout=60)
        bad = [
            ln
            for ln in out.splitlines()
            if any(tok in ln for tok in (" ERROR ", "ERROR:", " CRITICAL ", "Traceback", "FATAL:", "PANIC:"))
            and not any(ok in ln for ok in ALLOWED_ERROR_LINES)
        ]
        if bad:
            hits[svc] = bad[:10]
    c.data = {"hits": hits}
    if hits:
        raise AssertionError(json.dumps(hits, ensure_ascii=False)[:1500])
    c.detail = "geen foutregels in de logs van alle containers"


# ---------------------------------------------------------------- report
def write_report(started: str) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    images = {}
    for r in ps():
        images[r["Service"]] = r.get("Image")
    summary = {s: sum(1 for c in CHECKS if c.status == s) for s in ("ok", "failed", "skipped")}
    report = {
        "started": started,
        "finished": datetime.now(UTC).isoformat(),
        "project": PROJECT,
        "base_url": BASE,
        "web": bool(WEB),
        "host": {"platform": platform.platform(), "cpus": os.cpu_count()},
        "images": images,
        "summary": summary,
        "checks": [asdict(c) for c in CHECKS],
        "stats": STATS,
        "features": os.environ.get("MSO_FEATURES", "") or "(alle)",
    }
    (RESULTS / "smoke-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    md = [
        f"# MSO container-stack smoke test — {report['finished'][:19]}Z",
        "",
        f"- Resultaat: **{summary['ok']} ok, {summary['failed']} mislukt, {summary['skipped']} overgeslagen**",
        (
            f"- Basis-URL: `{BASE}` ({'via web-proxy' if WEB else 'direct naar API'}); MSO_FEATURES: "
            f"`{report['features']}`"
        ),
        f"- Host: {report['host']['platform']}, {report['host']['cpus']} CPU's (sandbox/CI — géén TrueNAS-hardware)",
        "",
        "| # | Check | Status | Duur | Detail |",
        "|---|---|---|---|---|",
    ]
    for i, c in enumerate(CHECKS, 1):
        md.append(f"| {i} | {c.name} | {c.status} | {c.seconds}s | {c.detail.replace('|', '/')[:400]} |")
    for label in ("idle", "load"):
        if label in STATS:
            md += [
                "",
                f"## Resources — {'rust' if label == 'idle' else 'onder belasting'} (max van samples)",
                "",
                "| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |",
                "|---|---|---|---|---|",
            ]
            for svc, v in sorted(STATS[label].items()):
                md.append(f"| {svc} | {v['cpu_pct_max']} | {v['mem_mib_max']} | {v['pids_max']} | {v['limit']} |")
    if "load_requests" in STATS:
        lr = STATS["load_requests"]
        md += [
            "",
            (
                f"Belasting: {lr['requests']} API-verzoeken (8 parallel) + 1 back-up-job in {lr['seconds']} s "
                f"({lr['rps']} req/s, p50 {lr['p50_ms']} ms, p95 {lr['p95_ms']} ms, {lr['errors']} fouten)."
            ),
        ]
    md += ["", "## Images", ""] + [f"- {k}: `{v}`" for k, v in sorted(images.items())]
    (RESULTS / "smoke-report.md").write_text("\n".join(md) + "\n")
    print(f"\nrapport: {RESULTS / 'smoke-report.md'}  —  {summary}")
    return 1 if summary["failed"] else 0


def main() -> int:
    if "--wait-only" in sys.argv:  # used by `make e2e`: just wait until the stack is healthy
        print(wait_healthy(300))
        return 0
    started = datetime.now(UTC).isoformat()
    order = [
        c_health,
        c_web,
        c_setup,
        c_auth,
        c_csrf,
        c_roles,
        c_connectors,
        c_ssrf,
        c_reads,
        c_backup,
        c_restore,
        c_restore_corrupt,
        c_job_recovery,
        c_worker_kill,
        c_restart,
        c_down_up,
        c_stats,
        c_secret_logs,
        c_error_logs,
    ]
    for fn in order:
        fn()
        if CHECKS[-1].status == "failed" and CHECKS[-1].name.startswith(("stack:", "setup:")):
            break  # nothing else can work
    return write_report(started)


if __name__ == "__main__":
    sys.exit(main())
