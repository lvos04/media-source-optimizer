"""Mock MediaGuard Ultimate core API (``/api/v1``) — a faithful subset for MSO tests.

Shapes follow the read-only reference ``lvos04/media-guard-ultimate`` at commit ``e92c2ac``:
``mediaguard/auth.py`` (``mg_session`` cookie + ``X-CSRF-Token``; ``X-MediaGuard-Token`` = admin machine
principal), ``api/auth.py`` (login → ``{csrf_token, role, username}``), ``api/system.py`` (``/health``,
``/status``), ``wanted/api.py`` (list/kpis/reasons/backfill quota/detail/diagnosis/search + the write
routes MSO
must never call), ``wanted/diagnosis.py`` (``REASONS``) and ``policy/api.py`` (``/policy/effective``).
It is a FastAPI app, so unknown routes answer ``404 {"detail": "Not Found"}`` exactly like MediaGuard.

Every request is recorded in ``app.state.calls`` as ``(method, path, query)`` so tests can prove MSO
never calls
MediaGuard write routes other than ``POST /api/v1/wanted/{id}/search``.

Faults (``create_app(**faults)`` / ``--fault key=1``):
- ``offline``          every route answers 503 (service down behind its proxy)
- ``auth_fail``        login always 401 and the internal token is rejected
- ``old_version``      older MediaGuard: no ``/wanted/backfill/quota``, ``/policy/effective`` and
                       ``POST /wanted/{id}/search`` (also absent from ``/api/v1/openapi.json``)
- ``no_openapi``       ``/api/v1/openapi.json`` is not served (forces route probing)
- ``quota_exhausted``  backfill quota has ``remaining: 0``; search answers 429 like MediaGuard
- ``emergency_stop``   ``/status`` reports an active emergency stop
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

VERSION = "0.1.0"
INTERNAL_TOKEN = "mock-internal-token-0123456789"  # noqa: S105  (test double)
USERS = {  # username: (password, role)
    "mso-operator": ("Operator-Wachtwoord1", "operator"),
    "mso-viewer": ("Viewer-Wachtwoord1", "viewer"),
    "admin": ("Admin-Wachtwoord1", "admin"),
}
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
MEDIA_TYPES = {
    "sonarr-series": "tv",
    "sonarr-anime": "anime",
    "radarr-films": "movie",
    "radarr-animefilms": "anime_movie",
}
INSTANCE_NAMES = {
    "sonarr-series": "Series",
    "sonarr-anime": "Anime",
    "radarr-films": "Films",
    "radarr-animefilms": "Animefilms",
}

REASONS = {
    "intentional-exception": {
        "label_nl": "Bewuste uitzondering",
        "fix_nl": "Niets te doen. Kies 'Opnieuw volgen' als dit item toch gezocht moet worden.",
    },
    "unmonitored": {
        "label_nl": "Niet gemonitord",
        "fix_nl": "Zet monitoring aan voor serie/seizoen/aflevering als je dit wilt hebben.",
    },
    "not-yet-aired": {
        "label_nl": "Nog niet uitgezonden / nog niet beschikbaar",
        "fix_nl": "Wacht tot de uitzend-/releasedatum. Controleer bij twijfel de metadata (Vernieuwen).",
    },
    "instance-suspended": {
        "label_nl": "Instantie gepauzeerd",
        "fix_nl": "Hervat de instantie (Instellingen → Instanties) om zoeken weer toe te staan.",
    },
    "root-folder-missing": {
        "label_nl": "Rootmap ontbreekt of is niet bereikbaar",
        "fix_nl": "Controleer de dataset/mount en rechten van de rootmap, of wijs een bestaande rootmap toe.",
    },
    "file-not-linked": {
        "label_nl": "Bestand aanwezig maar niet gekoppeld",
        "fix_nl": "Start 'Handmatig importeren' of 'Herscannen' voor deze serie/film.",
    },
    "import-failed": {
        "label_nl": "Download voltooid, import mislukt",
        "fix_nl": "Bekijk de importmelding; controleer remote path mappings, rechten en de "
        "episode-/filmkoppeling.",
    },
    "download-client-failed": {
        "label_nl": "Downloadclient-fout",
        "fix_nl": "Controleer de downloadclient (bereikbaarheid, schijfruimte, "
        "categorie) en zoek daarna opnieuw.",
    },
    "stuck-queue": {
        "label_nl": "Wachtrij vastgelopen",
        "fix_nl": "Verwijder het item uit de wachtrij (eventueel met blocklist) en zoek opnieuw.",
    },
    "downloading": {
        "label_nl": "Wordt gedownload",
        "fix_nl": "Geen actie nodig; wacht op voltooiing en import.",
    },
    "download-client-missing": {
        "label_nl": "Geen (geschikte) downloadclient",
        "fix_nl": "Voeg een downloadclient toe of schakel er een in voor het protocol "
        "van de gevonden releases.",
    },
    "no-indexers": {
        "label_nl": "Geen bruikbare indexer",
        "fix_nl": "Schakel automatisch zoeken in op een indexer, of pas de indexer-tags aan zodat "
        "ze voor deze titel gelden.",
    },
    "indexer-auth": {
        "label_nl": "Indexer-authenticatie mislukt",
        "fix_nl": "Vernieuw de API-sleutel/inloggegevens van de indexer en test opnieuw.",
    },
    "indexer-rate-limit": {
        "label_nl": "Indexer-limiet bereikt",
        "fix_nl": "Wacht tot de limiet reset; verlaag eventueel de backfill-quota voor deze instantie.",
    },
    "indexer-offline": {
        "label_nl": "Indexer offline of tijdelijk uitgeschakeld",
        "fix_nl": "Controleer de indexer (Test). Sonarr/Radarr schakelt hem automatisch weer in "
        "na de wachttijd.",
    },
    "incorrect-mapping": {
        "label_nl": "Verkeerde koppeling/nummering",
        "fix_nl": "Controleer scene-/absolute nummering (anime: TheXEM/AniDB) en de serie-ID; "
        "vernieuw daarna de serie.",
    },
    "rejected-quality": {
        "label_nl": "Afgewezen: kwaliteit",
        "fix_nl": "Releases bestaan maar in een kwaliteit die het profiel niet toestaat. Pas "
        "het kwaliteitsprofiel aan of gebruik 'Opnieuw met versoepelde regels'.",
    },
    "rejected-language": {
        "label_nl": "Afgewezen: taal",
        "fix_nl": "Alleen releases in een niet-toegestane taal. Pas taalbeleid aan of wacht "
        "op een geschikte release.",
    },
    "rejected-custom-format": {
        "label_nl": "Afgewezen: custom format-score",
        "fix_nl": "Releases halen de minimale custom format-score niet. Controleer de "
        "scores (TRaSH) of versoepel expliciet.",
    },
    "rejected-size": {
        "label_nl": "Afgewezen: grootte",
        "fix_nl": "Releases vallen buiten de min/max-grootte van de kwaliteitsdefinitie.",
    },
    "rejected-other": {
        "label_nl": "Afgewezen: overige regel",
        "fix_nl": "Zie de afwijzingsregels (retentie, seeders, release profile, blocklist, "
        "MediaGuard-beleid).",
    },
    "release-available": {
        "label_nl": "Geschikte release beschikbaar",
        "fix_nl": "Er is een goedgekeurde release; 'Zoek nu' zou die moeten grabben "
        "(controleer anders het delay profile).",
    },
    "no-indexer-hit": {
        "label_nl": "Geen release gevonden op indexers",
        "fix_nl": "Er bestaat (nog) geen release. Backfill probeert het later opnieuw met "
        "oplopende tussenpozen; niet alles is te vinden.",
    },
    "searched-no-grab": {
        "label_nl": "Gezocht zonder resultaat",
        "fix_nl": "Automatisch zoeken leverde niets op. Start een diagnose met interactieve "
        "zoekactie om de afwijzingsredenen te zien.",
    },
    "not-searched-yet": {
        "label_nl": "Nog niet gezocht",
        "fix_nl": "Kies 'Zoek nu' of wacht op de volgende backfillronde.",
    },
    "unknown": {
        "label_nl": "Oorzaak onbekend",
        "fix_nl": "Voer een interactieve diagnose uit en bekijk de historie.",
    },
}

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "anime-dual-first": {
        "name_nl": "Anime: Dual Audio eerst",
        "config": {"audio": {"mode": "anime", "anime_preference": "dual_first", "nl_subtitles_wanted": True}},
    },
    "standaard": {
        "name_nl": "Standaard: originele audio + NL ondertitels",
        "config": {"audio": {"mode": "original", "nl_audio": "off", "nl_subtitles_wanted": True}},
    },
}
MEDIA_TYPE_DEFAULTS = {
    "anime": "anime-dual-first",
    "anime_movie": "anime-dual-first",
    "tv": "standaard",
    "movie": "standaard",
}


def _profile_config(overrides: dict[str, Any]) -> dict[str, Any]:
    """``ProfileConfig().model_dump()`` defaults from mediaguard/policy/profiles.py, then overrides."""
    cfg: dict[str, Any] = {
        "ranking_order": ["audio", "source", "resolution", "custom_format", "group"],
        "audio": {
            "mode": "original",
            "anime_preference": "dual_first",
            "nl_audio": "off",
            "nl_subtitles_wanted": True,
            "subtitle_preference": ["nl", "en"],
        },
        "min_resolution": None,
        "max_resolution": None,
        "max_size_gb": None,
        "min_custom_format_score": None,
        "blocked_groups": [],
        "preferred_groups": [],
        "prefer_hdr": False,
        "prefer_smaller": False,
        "reject_false_audio_claims": True,
        "trash_score_set": "default",
    }
    for k, v in overrides.items():
        if k == "audio":
            cfg["audio"].update(v)
        else:
            cfg[k] = v
    return cfg


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _z(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_wanted() -> list[dict[str, Any]]:
    """A small library-shaped set of open wanted items across the four instances."""
    rows: list[dict[str, Any]] = []

    def add(
        iid: str,
        kind: str,
        pid: int,
        item_pid: int,
        title: str,
        item_title: str,
        *,
        season: int | None = None,
        episode: int | None = None,
        absolute: int | None = None,
        reason: str = "missing",
        code: str | None = None,
        attempts: int = 0,
        status: str = "wanted",
    ) -> None:
        rows.append(
            {
                "id": len(rows) + 1,
                "instance_id": iid,
                "kind": kind,
                "provider_id": pid,
                "item_provider_id": item_pid,
                "reason": reason,
                "title": title,
                "item_title": item_title,
                "season_number": season,
                "episode_number": episode,
                "absolute_episode_number": absolute,
                "air_date_utc": _z(20 + len(rows)),
                "status": status,
                "priority": 50 - len(rows),
                "attempts": attempts,
                "last_searched_at": _z(1) if attempts else None,
                "next_search_at": None,
                "last_outcome": "no_candidate" if attempts else None,
                "last_reason_code": code,
                "keep_monitoring": False,
                "ignored_reason": None,
                "first_seen_at": _z(30),
                "updated_at": _z(1),
                "resolved_at": None,
                "resolution": None,
                "media_type": MEDIA_TYPES[iid],
                "instance_name": INSTANCE_NAMES[iid],
            }
        )

    add(
        "sonarr-series",
        "episode",
        1001,
        11,
        "Lost Signal – S02E05 – Static",
        "Lost Signal",
        season=2,
        episode=5,
        code="no-indexer-hit",
        attempts=3,
    )
    add(
        "sonarr-series",
        "episode",
        1002,
        11,
        "Lost Signal – S02E06",
        "Lost Signal",
        season=2,
        episode=6,
        code="rejected-quality",
        attempts=2,
    )
    add(
        "sonarr-series",
        "episode",
        1003,
        12,
        "Harbour Lights – S01E01",
        "Harbour Lights",
        season=1,
        episode=1,
        code="import-failed",
        attempts=1,
    )
    add(
        "sonarr-anime",
        "episode",
        2001,
        21,
        "Sky Blade – S01E13",
        "Sky Blade",
        season=1,
        episode=13,
        absolute=13,
        code="rejected-language",
        attempts=4,
        status="unavailable",
    )
    add(
        "sonarr-anime",
        "episode",
        2002,
        21,
        "Sky Blade – S02E01",
        "Sky Blade",
        season=2,
        episode=1,
        absolute=26,
        code=None,
    )
    add(
        "radarr-films",
        "movie",
        3001,
        3001,
        "The Long Night (2019)",
        "The Long Night",
        code="download-client-failed",
        attempts=1,
    )
    add(
        "radarr-films",
        "movie",
        3002,
        3002,
        "Paper Moon Rising (2021)",
        "Paper Moon Rising",
        reason="cutoff",
        code="rejected-quality",
        attempts=2,
    )
    add(
        "radarr-animefilms",
        "movie",
        4001,
        4001,
        "Garden of Words (2013)",
        "Garden of Words",
        code="no-indexer-hit",
        attempts=5,
        status="unavailable",
    )
    return rows


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SearchBody(BaseModel):
    wait_seconds: float = Field(default=0, ge=0, le=300)


class RelaxedBody(BaseModel):
    relaxations: list[Literal["quality", "custom-format", "size", "language"]] = Field(min_length=1)
    confirm: bool = False
    reason: str = Field(default="", max_length=500)


class BulkAction(BaseModel):
    action: Literal["search", "ignore", "unignore", "keep_monitoring"]
    wanted_ids: list[int] = Field(default_factory=list, max_length=1000)
    reason: str = Field(default="", max_length=500)


class ItemAction(BaseModel):
    reason: str = Field(default="", max_length=500)


class PlanBody(BaseModel):
    instance_id: str | None = None


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def create_app(**faults: Any) -> FastAPI:
    f = {k: _truthy(v) for k, v in faults.items()}
    app = FastAPI(
        title="MediaGuard Ultimate API (mock)",
        version=VERSION,
        openapi_url=None if f.get("no_openapi") else "/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    st = app.state
    st.calls = []  # [(method, path, query)]
    st.sessions: dict[str, dict[str, str]] = {}
    st.wanted = seed_wanted()
    st.searches: list[dict[str, Any]] = []
    st.quota = {
        iid: {"hour_used": 0, "hour_max": 20, "day_used": 0, "day_max": 150, "batch_size": 10}
        for iid in MEDIA_TYPES
    }
    st.policy_overrides: dict[str, dict[str, Any]] = {}  # instance_id -> ProfileConfig overrides
    st.diagnoses: dict[int, dict[str, Any]] = {}
    st.faults = f
    for w in st.wanted:
        if w["last_reason_code"]:
            st.diagnoses[w["id"]] = {
                "id": w["id"],
                "wanted_id": w["id"],
                "instance_id": w["instance_id"],
                "ts": _z(1),
                "primary_code": w["last_reason_code"],
                "interactive": 0,
                "reasons": [
                    {
                        "code": w["last_reason_code"],
                        "label_nl": REASONS[w["last_reason_code"]]["label_nl"],
                        "rule": "mock",
                        "ts": _z(1),
                        "message_nl": "Gesimuleerde diagnose.",
                        "suggested_fix_nl": REASONS[w["last_reason_code"]]["fix_nl"],
                        "evidence": None,
                        "severity": "error",
                        "confidence": "high",
                    }
                ],
                "evidence": {"errors": []},
            }

    class Recorder(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
            st.calls.append((request.method, request.url.path, request.url.query))
            if f.get("offline"):
                return JSONResponse({"detail": "Service Unavailable"}, status_code=503)
            return await call_next(request)

    app.add_middleware(Recorder)

    # ------------------------------------------------------------------ auth (mediaguard/auth.py)
    def principal(request: Request) -> dict[str, Any]:
        internal = request.headers.get("X-MediaGuard-Token")
        if internal and not f.get("auth_fail") and hmac.compare_digest(internal, INTERNAL_TOKEN):
            return {"username": "internal", "role": "admin", "machine": True, "csrf": None}
        token = request.cookies.get("mg_session")
        sess = st.sessions.get(hashlib.sha256(token.encode()).hexdigest()) if token else None
        if sess is None:
            raise HTTPException(status_code=401, detail="Niet ingelogd")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), sess["csrf"]):
                raise HTTPException(status_code=403, detail="CSRF-controle mislukt")
        return {"username": sess["username"], "role": sess["role"], "machine": False, "csrf": sess["csrf"]}

    def require(role: str):  # type: ignore[no-untyped-def]
        def dep(p: dict[str, Any] = Depends(principal)) -> dict[str, Any]:
            if ROLE_RANK.get(p["role"], 0) < ROLE_RANK[role]:
                raise HTTPException(status_code=403, detail=f"Rol '{role}' vereist")
            return p

        return dep

    @app.post("/api/v1/auth/login")
    def login(body: Credentials, response: Response) -> dict[str, str]:
        user = USERS.get(body.username)
        if f.get("auth_fail") or user is None or user[0] != body.password:
            raise HTTPException(401, "Onjuiste gebruikersnaam of wachtwoord")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        st.sessions[hashlib.sha256(token.encode()).hexdigest()] = {
            "username": body.username,
            "role": user[1],
            "csrf": csrf,
        }
        response.set_cookie(
            "mg_session", token, max_age=14 * 86400, httponly=True, samesite="strict", path="/"
        )
        return {"csrf_token": csrf, "role": user[1], "username": body.username}

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        token = request.cookies.get("mg_session")
        if token:
            st.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        response.delete_cookie("mg_session", path="/")
        return {"ok": True}

    @app.get("/api/v1/auth/me")
    def me(p: dict[str, Any] = Depends(principal)) -> dict[str, Any]:
        return {
            "username": p["username"],
            "role": p["role"],
            "csrf_token": p["csrf"],
            "locale": "nl-NL",
            "preferences": {},
        }

    # ------------------------------------------------------------------ system (api/system.py)
    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.0.9" if f.get("old_version") else VERSION}

    @app.get("/api/v1/status")
    def status(_: dict[str, Any] = Depends(require("viewer"))) -> dict[str, Any]:
        stop = (
            {"active": True, "reason": "test", "by": "admin", "since": _z(0)}
            if f.get("emergency_stop")
            else {"active": False}
        )
        return {
            "version": "0.0.9" if f.get("old_version") else VERSION,
            "role": "core",
            "emergency_stop": stop,
            "jobs": {"succeeded": 4},
            "unread_notifications": 0,
            "time": utcnow(),
        }

    # ------------------------------------------------------------------ wanted (wanted/api.py)
    def quota_status(iid: str) -> dict[str, Any]:
        q = dict(st.quota[iid])
        if f.get("quota_exhausted"):
            q["hour_used"] = q["hour_max"]
        q["remaining"] = max(0, min(q["hour_max"] - q["hour_used"], q["day_max"] - q["day_used"]))
        return q

    def get_wanted(wid: int) -> dict[str, Any] | None:
        return next((copy.deepcopy(w) for w in st.wanted if w["id"] == wid), None)

    @app.get("/api/v1/wanted")
    def wanted_list(
        instance_id: str | None = None,
        media_type: str | None = None,
        kind: Literal["episode", "movie"] | None = None,
        reason: Literal["missing", "cutoff"] | None = None,
        status: str | None = "open",
        q: str | None = None,
        reason_code: str | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        sort: str = "priority",
        _: dict[str, Any] = Depends(require("viewer")),
    ) -> dict[str, Any]:
        rows = [w for w in st.wanted]
        if status == "open":
            rows = [w for w in rows if w["status"] in ("wanted", "searching", "unavailable")]
        elif status:
            rows = [w for w in rows if w["status"] == status]
        for col, val in (
            ("instance_id", instance_id),
            ("media_type", media_type),
            ("kind", kind),
            ("reason", reason),
            ("last_reason_code", reason_code),
        ):
            if val:
                rows = [w for w in rows if w[col] == val]
        if q:
            rows = [w for w in rows if q.lower() in w["title"].lower()]
        rows.sort(key=lambda w: (-w["priority"], w["id"]))
        start = (page - 1) * page_size
        return {
            "page": page,
            "page_size": page_size,
            "total": len(rows),
            "items": copy.deepcopy(rows[start : start + page_size]),
        }

    def _kpi_block(iid: str | None) -> dict[str, Any]:
        rows = [w for w in st.wanted if iid in (None, w["instance_id"])]
        open_ = [w for w in rows if w["status"] in ("wanted", "searching", "unavailable")]
        dist: dict[str, int] = {}
        for w in open_:
            k = w["last_reason_code"] or "niet-gediagnosticeerd"
            dist[k] = dist.get(k, 0) + 1
        return {
            "missing": sum(w["reason"] == "missing" for w in open_),
            "cutoff": sum(w["reason"] == "cutoff" for w in open_),
            "unavailable": sum(w["status"] == "unavailable" for w in rows),
            "ignored": sum(w["status"] == "ignored" for w in rows),
            "missing_aired_7d": 0,
            "resolved_7d": 0,
            "missing_aired_30d": len(open_),
            "resolved_30d": 0,
            "missing_aired_90d": len(open_),
            "resolved_90d": 0,
            "reason_distribution": dist,
            "backfill_30d": {"searches": 12, "completed": 10, "grabbed": 3, "errors": 0, "success_rate": 0.3},
            "import_failed": sum(w["last_reason_code"] == "import-failed" for w in open_),
        }

    @app.get("/api/v1/wanted/kpis")
    def kpis(
        instance_id: str | None = None, _: dict[str, Any] = Depends(require("viewer"))
    ) -> dict[str, Any]:
        data = _kpi_block(instance_id)
        data["per_instance"] = {i: _kpi_block(i) for i in MEDIA_TYPES if instance_id in (None, i)}
        return data

    @app.get("/api/v1/wanted/reasons")
    def reasons(_: dict[str, Any] = Depends(require("viewer"))) -> dict[str, Any]:
        return REASONS

    @app.post("/api/v1/wanted/backfill-plan")
    def backfill_plan(
        body: PlanBody | None = None, _: dict[str, Any] = Depends(require("viewer"))
    ) -> dict[str, Any]:
        return {"dry_run": True, "plans": []}

    @app.get("/api/v1/wanted/backfill/runs")
    def backfill_runs(_: dict[str, Any] = Depends(require("viewer"))) -> list[dict[str, Any]]:
        return []

    if not f.get("old_version"):

        @app.get("/api/v1/wanted/backfill/quota")
        def quota(_: dict[str, Any] = Depends(require("viewer"))) -> dict[str, Any]:
            return {iid: quota_status(iid) for iid in MEDIA_TYPES}

    @app.post("/api/v1/wanted/actions")
    def bulk_action(body: BulkAction, _: dict[str, Any] = Depends(require("operator"))) -> dict[str, Any]:
        return {"updated": len(body.wanted_ids), "results": [], "explanation_nl": ""}

    @app.get("/api/v1/wanted/{wanted_id}")
    def wanted_item(wanted_id: int, _: dict[str, Any] = Depends(require("viewer"))) -> dict[str, Any]:
        item = get_wanted(wanted_id)
        if item is None:
            raise HTTPException(404, "Item niet gevonden")
        item.update(
            attempts_log=[],
            diagnoses=[],
            overrides=[],
            settings_scope={},
            latest_diagnosis=st.diagnoses.get(wanted_id),
        )
        return item

    @app.get("/api/v1/wanted/{wanted_id}/diagnosis")
    def diagnosis(
        wanted_id: int,
        interactive: bool = False,
        cached: bool = False,
        p: dict[str, Any] = Depends(require("viewer")),
    ) -> dict[str, Any]:
        if get_wanted(wanted_id) is None:
            raise HTTPException(404, "Item niet gevonden")
        if cached:
            if wanted_id not in st.diagnoses:
                raise HTTPException(404, "Nog geen diagnose opgeslagen")
            return st.diagnoses[wanted_id]
        if interactive and p["role"] not in ("operator", "admin"):
            raise HTTPException(403, "Interactieve diagnose vereist de rol 'operator'")
        raise HTTPException(503, "Live diagnose niet gesimuleerd in de mock")

    if not f.get("old_version"):

        @app.post("/api/v1/wanted/{wanted_id}/search")
        def search_now(
            wanted_id: int, body: SearchBody | None = None, p: dict[str, Any] = Depends(require("operator"))
        ) -> dict[str, Any]:
            item = get_wanted(wanted_id)
            if item is None:
                raise HTTPException(404, "Item niet gevonden")
            q = quota_status(item["instance_id"])
            if q["remaining"] <= 0:
                raise HTTPException(
                    429,
                    {"message": "Zoekquotum voor deze instantie is op; probeer later opnieuw.", "quota": q},
                )
            st.quota[item["instance_id"]]["hour_used"] += 1
            st.quota[item["instance_id"]]["day_used"] += 1
            for w in st.wanted:
                if w["id"] == wanted_id:
                    w.update(status="searching", attempts=w["attempts"] + 1, last_searched_at=utcnow())
            st.searches.append({"wanted_id": wanted_id, "by": p["username"]})
            run_id = len(st.searches)
            return {
                "instance_id": item["instance_id"],
                "run_id": run_id,
                "issued": 1,
                "issued_wanted_ids": [wanted_id],
                "skipped_in_queue": 0,
                "errors": [],
                "evaluation": {},
                "plan": {"quota": q},
                "item": get_wanted(wanted_id),
            }

    @app.post("/api/v1/wanted/{wanted_id}/retry-relaxed")
    def retry_relaxed(
        wanted_id: int, body: RelaxedBody, _: dict[str, Any] = Depends(require("operator"))
    ) -> dict[str, Any]:
        return {"requires_confirmation": True, "explanation_nl": "", "relaxations": body.relaxations}

    @app.post("/api/v1/wanted/{wanted_id}/ignore")
    def ignore(
        wanted_id: int, body: ItemAction | None = None, _: dict[str, Any] = Depends(require("operator"))
    ) -> dict[str, Any]:
        return {"id": wanted_id, "action": "ignore"}

    @app.post("/api/v1/wanted/{wanted_id}/unignore")
    def unignore(wanted_id: int, _: dict[str, Any] = Depends(require("operator"))) -> dict[str, Any]:
        return {"id": wanted_id, "action": "unignore"}

    @app.post("/api/v1/wanted/{wanted_id}/keep-monitoring")
    def keep_monitoring(wanted_id: int, _: dict[str, Any] = Depends(require("operator"))) -> dict[str, Any]:
        return {"id": wanted_id, "action": "keep_monitoring"}

    # ------------------------------------------------------------------ policy (policy/api.py)
    if not f.get("old_version"):

        @app.get("/api/v1/policy/effective")
        def effective(
            instance_id: str | None = None,
            media_type: str | None = None,
            series_id: int | None = None,
            movie_id: int | None = None,
            season: int | None = None,
            episode_id: int | None = None,
            _: dict[str, Any] = Depends(require("viewer")),
        ) -> dict[str, Any]:
            mt = media_type or MEDIA_TYPES.get(instance_id or "", "tv")
            pid = MEDIA_TYPE_DEFAULTS.get(mt, "standaard")
            prof = BUILTIN_PROFILES[pid]
            cfg = _profile_config(prof["config"])
            over = st.policy_overrides.get(instance_id or "", {})
            cfg = _profile_config({**{k: v for k, v in cfg.items()}, **over}) if over else cfg
            chain = {"media_type": mt, **({"instance": instance_id} if instance_id else {})}
            return {
                "profile_id": pid,
                "profile_name_nl": prof["name_nl"],
                "config": cfg,
                "trace": {"profile_id": {"value": pid, "source": f"auto:{mt}"}},
                "chain": chain,
            }

    return app
