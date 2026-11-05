"""Fake qBittorrent WebAPI v2 (test double — never part of the production stack).

Implements the subset Media Source Optimizer uses, with the real wire formats:
``auth/login`` (SID cookie, Referer/Origin CSRF check, "Ok."/"Fails."), ``Authorization: Bearer`` API keys
(only honoured when the faked version is ≥ 5.2), ``app/version|webapiVersion|buildInfo|preferences|
setPreferences|networkInterfaceList``, ``transfer/info|speedLimitsMode|toggleSpeedLimitsMode``,
``torrents/info|properties|trackers|files|categories|add|recheck|reannounce|setUploadLimit|setDownloadLimit|
uploadLimit|downloadLimit|setShareLimits|topPrio|bottomPrio|increasePrio|decreasePrio``, ``sync/maindata``
(incremental ``rid`` diffs), ``torrents/delete`` (removes the torrent; every delete is recorded in
``app.state.qb.deletes`` as ``(hashes, deleteFiles)`` so tests can assert it is only called by an approved
cleanup change). ``torrents/addTrackers``/``removeTrackers`` exist ONLY so tests can assert they are never called
(every call is recorded in ``app.state.qb.calls``).

Scenario torrents (``scenario=full``, default): public seeding, private seeding (passkey in announce URL),
stalled without seeds + false ETA, failing tracker, healthy download, disk-pressure download, missing files,
queued download.

Faults (``create_app(**faults)`` / ``--fault key=value``):
- ``version`` (default 5.1.2), ``webapi`` (default derived), ``old_version=1`` → 4.1.9 / WebAPI 2.2.0 without
  buildInfo, networkInterfaceList, infohash/private fields and ``inactiveSeedingTimeLimit``
- ``username``/``password`` (default admin/adminadmin), ``api_key`` (only accepted when version ≥ 5.2)
- ``auth_fail=1`` → every login fails; ``session_ttl=N`` → a SID expires after N authenticated requests (403)
- ``firewalled=1``, ``free_space=<bytes>``, ``interface=<name>`` (bound interface), ``scenario=empty|full``

Like real qBittorrent, speed limits (``setPreferences`` up/dl/alt limits, ``setUploadLimit``/
``setDownloadLimit``) are stored in whole KiB/s: written values are rounded down to a multiple of 1024.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

GB = 1024**3
MB = 1024**2
RATE_PREFS = ("up_limit", "dl_limit", "alt_up_limit", "alt_dl_limit")


def _kib(v: Any) -> int:
    """Real qBittorrent keeps speed limits in whole KiB/s: a written byte value is rounded down to a multiple of
    1024 (200000000 → 199999488); 0 and negatives (no limit) are kept."""
    n = int(v)
    return (n // 1024) * 1024 if n > 0 else n


def _h(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()  # noqa: S324 (fake infohash)


def _bdecode(data: bytes, i: int = 0) -> tuple[Any, int]:
    c = data[i : i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1 : end]), end + 1
    if c == b"l":
        i, out = i + 1, []
        while data[i : i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i, d = i + 1, {}
        while data[i : i + 1] != b"e":
            k, i = _bdecode(data, i)
            start = i
            v, i = _bdecode(data, i)
            d[k] = v
            if k == b"info":
                d[b"__info_raw__"] = data[start:i]
        return d, i + 1
    colon = data.index(b":", i)
    n = int(data[i:colon])
    return data[colon + 1 : colon + 1 + n], colon + 1 + n


def _torrent_identity(data: bytes) -> tuple[str, str]:
    meta, _ = _bdecode(data)
    raw = meta[b"__info_raw__"]
    return hashlib.sha1(raw).hexdigest(), meta[b"info"][b"name"].decode("utf-8", "replace")  # noqa: S324


async def parse_form(request: Request) -> dict[str, Any]:
    """urlencoded + multipart without python-multipart (file parts come back as bytes)."""
    from email.parser import BytesParser
    from email.policy import HTTP
    from urllib.parse import parse_qsl

    body = await request.body()
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
        out: dict[str, Any] = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True) or b""
            out[str(name)] = payload if part.get_filename() else payload.decode("utf-8", "replace")
        return out
    return dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))


def _vt(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def _torrent(
    name: str,
    *,
    category: str,
    size: int,
    progress: float,
    state: str,
    tracker: str,
    private: bool,
    dlspeed: int = 0,
    upspeed: int = 0,
    eta: int = 8640000,
    num_seeds: int = 0,
    num_leechs: int = 0,
    num_complete: int = 0,
    num_incomplete: int = 0,
    availability: float = -1,
    ratio: float = 0.0,
    seeding_time: int = 0,
    save_path: str = "/data/torrents",
    comment: str = "",
) -> dict[str, Any]:
    h = _h(name)
    downloaded = int(size * progress)
    return {
        "hash": h,
        "infohash_v1": h,
        "infohash_v2": "",
        "name": name,
        "category": category,
        "tags": "",
        "size": size,
        "total_size": size,
        "progress": progress,
        "state": state,
        "dlspeed": dlspeed,
        "upspeed": upspeed,
        "eta": eta,
        "num_seeds": num_seeds,
        "num_leechs": num_leechs,
        "num_complete": num_complete,
        "num_incomplete": num_incomplete,
        "availability": availability,
        "ratio": ratio,
        "seeding_time": seeding_time,
        "time_active": seeding_time + 3600,
        "downloaded": downloaded,
        "uploaded": int(downloaded * ratio),
        "amount_left": size - downloaded,
        "completed": downloaded,
        "added_on": 1_760_000_000,
        "completion_on": 1_760_003_600 if progress >= 1 else -1,
        "last_activity": 1_760_010_000,
        "save_path": f"{save_path}/{category}",
        "content_path": f"{save_path}/{category}/{name}",
        "tracker": tracker if state != "error" else "",
        "comment": comment,
        "up_limit": 0,
        "dl_limit": 0,
        "ratio_limit": -2,
        "seeding_time_limit": -2,
        "inactive_seeding_time_limit": -2,
        "max_ratio": -1,
        "max_seeding_time": -1,
        "priority": 0,
        "force_start": False,
        "private": private,
        "auto_tmm": False,
        "super_seeding": False,
    }


class QbState:
    def __init__(self, faults: dict[str, Any]) -> None:
        self.faults = faults
        self.old = str(faults.get("old_version", "")).lower() in {"1", "true", "yes"}
        self.version = "4.1.9" if self.old else str(faults.get("version", "5.1.2"))
        if faults.get("webapi"):
            self.webapi = str(faults["webapi"])
        elif self.old:
            self.webapi = "2.2.0"
        else:
            self.webapi = (
                "2.11.4"
                if _vt(self.version) >= (5, 1)
                else ("2.11.2" if _vt(self.version) >= (5, 0) else "2.9.3")
            )
        self.username = str(faults.get("username", "admin"))
        self.password = str(faults.get("password", "adminadmin"))
        self.api_key = faults.get("api_key")
        self.auth_fail = str(faults.get("auth_fail", "")).lower() in {"1", "true", "yes"}
        self.session_ttl = int(faults["session_ttl"]) if faults.get("session_ttl") else None
        self.sessions: dict[str, int] = {}
        self.logins = 0
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.deletes: list[tuple[list[str], bool]] = []
        self.speed_limits_mode = 0
        self.rid = 0
        self.snapshots: dict[int, dict[str, Any]] = {}
        self.prefs: dict[str, Any] = {
            "dht": True,
            "pex": True,
            "lsd": True,
            "encryption": 0,
            "anonymous_mode": False,
            "max_connec": 500,
            "max_connec_per_torrent": 100,
            "max_uploads": 20,
            "max_uploads_per_torrent": 4,
            "up_limit": 0,
            "dl_limit": 0,
            "alt_up_limit": 10240,
            "alt_dl_limit": 10240,
            "queueing_enabled": True,
            "max_active_downloads": 3,
            "max_active_uploads": 3,
            "max_active_torrents": 5,
            "dont_count_slow_torrents": False,
            "slow_torrent_dl_rate_threshold": 2,
            "slow_torrent_ul_rate_threshold": 2,
            "slow_torrent_inactive_timer": 60,
            "listen_port": 6881,
            "upnp": False,
            "random_port": False,
            "current_network_interface": str(faults.get("interface", "")),
            "current_interface_address": "",
            "max_ratio_enabled": False,
            "max_ratio": -1,
            "max_seeding_time_enabled": False,
            "max_seeding_time": -1,
            "max_ratio_act": 0,
            "async_io_threads": 10,
            "hashing_threads": 1,
            "disk_queue_size": 1048576,
            "add_trackers_enabled": False,
            "add_trackers": "",
            "web_ui_username": self.username,
            "proxy_password": "proxygeheim",
            "mail_notification_password": "mailgeheim",
            "save_path": "/data/torrents",
        }
        self.server_state: dict[str, Any] = {
            "connection_status": "firewalled"
            if str(faults.get("firewalled", "")).lower() in {"1", "true"}
            else "connected",
            "dht_nodes": 312,
            "dl_info_speed": 0,
            "dl_info_data": 0,
            "up_info_speed": 0,
            "up_info_data": 0,
            "dl_rate_limit": 0,
            "up_rate_limit": 0,
            "free_space_on_disk": int(faults.get("free_space", 900 * GB)),
            "queued_io_jobs": 0,
            "average_time_queue": 0,
            "total_queued_size": 0,
            "read_cache_overload": "0",
            "write_cache_overload": "0",
            "total_peer_connections": 140,
            "use_alt_speed_limits": False,
            "queueing": True,
            "global_ratio": "1.12",
            "refresh_interval": 1500,
        }
        self.categories = {
            c: {"name": c, "savePath": f"/data/torrents/{c}"}
            for c in ("tv", "tv-anime", "movies", "movies-anime")
        }
        self.torrents: dict[str, dict[str, Any]] = {}
        self.trackers: dict[str, list[dict[str, Any]]] = {}
        self.files: dict[str, list[dict[str, Any]]] = {}
        if faults.get("scenario", "full") == "full":
            self._scenario()
        self._recompute()

    # ------------------------------------------------------------------ scenario
    def add(
        self, t: dict[str, Any], trackers: list[dict[str, Any]], files: list[tuple[str, int]] | None = None
    ) -> str:
        h = t["hash"]
        self.torrents[h] = t
        dht_msg = "This torrent is private" if t["private"] else ""
        base = [
            {
                "url": f"** [{n}] **",
                "status": 0 if t["private"] else 2,
                "tier": -1,
                "num_peers": 0,
                "num_seeds": 0,
                "num_leeches": 0,
                "num_downloaded": 0,
                "msg": dht_msg,
            }
            for n in ("DHT", "PeX", "LSD")
        ]
        self.trackers[h] = base + trackers
        files = files or [(f"{t['name']}/{t['name']}.mkv", t["size"])]
        self.files[h] = [
            {
                "index": i,
                "name": n,
                "size": s,
                "progress": t["progress"],
                "priority": 1,
                "is_seed": t["progress"] >= 1,
                "availability": t["availability"],
            }
            for i, (n, s) in enumerate(files)
        ]
        return h

    def _scenario(self) -> None:
        def tr(url: str, status: int = 2, seeds: int = 10, peers: int = 5, msg: str = "") -> dict[str, Any]:
            return {
                "url": url,
                "status": status,
                "tier": 0,
                "num_peers": peers,
                "num_seeds": seeds,
                "num_leeches": peers,
                "num_downloaded": 100,
                "msg": msg,
            }

        pub = "https://tracker.openbt.example/announce"
        priv = "https://tracker.privehd.example/announce/PASSKEY0123456789abcdef"
        self.add(
            _torrent(
                "Publieke.Serie.S01E01.1080p.WEB-DL-GRP",
                category="tv",
                size=2 * GB,
                progress=1.0,
                state="uploading",
                tracker=pub,
                private=False,
                upspeed=300_000,
                num_seeds=12,
                num_leechs=3,
                num_complete=40,
                num_incomplete=3,
                availability=40,
                ratio=1.4,
                seeding_time=3 * 86400,
            ),
            [tr(pub, seeds=40)],
        )
        self.add(
            _torrent(
                "Prive.Film.2024.2160p.BluRay.REMUX-GRP",
                category="movies",
                size=60 * GB,
                progress=1.0,
                state="stalledUP",
                tracker=priv,
                private=True,
                num_complete=25,
                availability=25,
                ratio=0.4,
                seeding_time=20 * 3600,
            ),
            [tr(priv, seeds=25, peers=0)],
        )
        self.add(
            _torrent(
                "Zeldzame.Anime.S01E05.1080p-Grp",
                category="tv-anime",
                size=1 * GB,
                progress=0.42,
                state="stalledDL",
                tracker=pub,
                private=False,
                eta=3600,
                availability=0.42,
            ),
            [tr(pub, seeds=0, peers=1)],
        )
        dead = "https://tracker.dood.example:2710/announce"
        self.add(
            _torrent(
                "Andere.Serie.S02E03.720p.HDTV-GRP",
                category="tv",
                size=1 * GB,
                progress=0.1,
                state="stalledDL",
                tracker="",
                private=False,
                availability=0.1,
            ),
            [tr(dead, status=4, seeds=0, peers=0, msg="Connection timed out")],
        )
        self.add(
            _torrent(
                "Gewone.Film.2023.1080p.WEB-DL-GRP",
                category="movies",
                size=4 * GB,
                progress=0.25,
                state="downloading",
                tracker=pub,
                private=False,
                dlspeed=5 * MB,
                eta=600,
                num_seeds=30,
                num_complete=80,
                availability=30,
            ),
            [tr(pub, seeds=80)],
        )
        self.add(
            _torrent(
                "Grote.Animefilm.Collectie.2160p-GRP",
                category="movies-anime",
                size=120 * GB,
                progress=0.1,
                state="downloading",
                tracker=pub,
                private=False,
                dlspeed=2 * MB,
                eta=54000,
                num_seeds=6,
                num_complete=9,
                availability=6,
            ),
            [tr(pub, seeds=9)],
        )
        self.add(
            _torrent(
                "Kapotte.Serie.S01E02.1080p.WEB-GRP",
                category="tv",
                size=2 * GB,
                progress=1.0,
                state="missingFiles",
                tracker=pub,
                private=False,
                ratio=2.0,
            ),
            [tr(pub)],
        )
        self.add(
            _torrent(
                "Wachtrij.Film.2022.1080p.BluRay-GRP",
                category="movies",
                size=8 * GB,
                progress=0.0,
                state="queuedDL",
                tracker=pub,
                private=False,
                num_complete=50,
            ),
            [tr(pub, seeds=50)],
        )

    def _recompute(self) -> None:
        self.server_state["dl_info_speed"] = sum(t["dlspeed"] for t in self.torrents.values())
        self.server_state["up_info_speed"] = sum(t["upspeed"] for t in self.torrents.values())
        self.server_state["dl_rate_limit"] = self.prefs["dl_limit"]
        self.server_state["up_rate_limit"] = self.prefs["up_limit"]

    # ------------------------------------------------------------------ views
    def info(self, t: dict[str, Any]) -> dict[str, Any]:
        out = dict(t)
        if self.old:
            for k in ("infohash_v1", "infohash_v2", "private", "inactive_seeding_time_limit"):
                out.pop(k, None)
        elif _vt(self.webapi) < (2, 11, 1):
            out.pop("private", None)
        return out

    def snapshot(self) -> dict[str, Any]:
        return {
            "torrents": {h: self.info(t) for h, t in self.torrents.items()},
            "categories": copy.deepcopy(self.categories),
            "server_state": dict(self.server_state),
        }


def create_app(**faults: Any) -> FastAPI:
    app = FastAPI(title="Mock qBittorrent", docs_url=None, redoc_url=None, openapi_url=None)
    qb = QbState(faults)
    app.state.qb = qb

    def authed(request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return bool(qb.api_key) and _vt(qb.version) >= (5, 2) and auth[7:].strip() == str(qb.api_key)
        sid = request.cookies.get("SID")
        if not sid or sid not in qb.sessions:
            return False
        qb.sessions[sid] += 1
        if qb.session_ttl is not None and qb.sessions[sid] > qb.session_ttl:
            qb.sessions.pop(sid, None)
            return False
        return True

    def forbidden() -> Response:
        return PlainTextResponse("Forbidden", status_code=403)

    async def form(request: Request) -> dict[str, Any]:
        return await parse_form(request)

    def hashes(value: str | None) -> list[str]:
        if not value:
            return []
        if value == "all":
            return list(qb.torrents)
        return [h for h in value.split("|") if h in qb.torrents]

    @app.middleware("http")
    async def record(request: Request, call_next):  # type: ignore[no-untyped-def]
        qb.calls.append((request.method, request.url.path, dict(request.query_params)))
        return await call_next(request)

    # ------------------------------------------------------------------ auth
    @app.post("/api/v2/auth/login")
    async def login(request: Request) -> Response:
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        host = request.headers.get("host", "")
        if origin and host not in origin:
            return PlainTextResponse("Unauthorized", status_code=401)
        f = await form(request)
        qb.logins += 1
        if qb.auth_fail or f.get("username") != qb.username or f.get("password") != qb.password:
            return PlainTextResponse("Fails.")
        sid = secrets.token_hex(16)
        qb.sessions[sid] = 0
        resp = PlainTextResponse("Ok.")
        resp.set_cookie("SID", sid, httponly=True, samesite="strict")
        return resp

    @app.post("/api/v2/auth/logout")
    async def logout(request: Request) -> Response:
        qb.sessions.pop(request.cookies.get("SID", ""), None)
        return PlainTextResponse("")

    # ------------------------------------------------------------------ app
    @app.get("/api/v2/app/version")
    async def version(request: Request) -> Response:
        return PlainTextResponse(f"v{qb.version}") if authed(request) else forbidden()

    @app.get("/api/v2/app/webapiVersion")
    async def webapi(request: Request) -> Response:
        return PlainTextResponse(qb.webapi) if authed(request) else forbidden()

    @app.get("/api/v2/app/buildInfo")
    async def build(request: Request) -> Response:
        if qb.old:
            return PlainTextResponse("Not Found", status_code=404)
        if not authed(request):
            return forbidden()
        return JSONResponse(
            {
                "qt": "6.7.3",
                "libtorrent": "2.0.11.0",
                "boost": "1.86.0",
                "openssl": "3.3.2",
                "zlib": "1.3.1",
                "bitness": 64,
            }
        )

    @app.get("/api/v2/app/preferences")
    async def prefs(request: Request) -> Response:
        return JSONResponse(qb.prefs) if authed(request) else forbidden()

    @app.post("/api/v2/app/setPreferences")
    async def set_prefs(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        f = await form(request)
        for k, v in json.loads(f.get("json") or "{}").items():
            if k in qb.prefs:
                qb.prefs[k] = _kib(v) if k in RATE_PREFS else v
        qb._recompute()
        return PlainTextResponse("")

    @app.get("/api/v2/app/networkInterfaceList")
    async def ifaces(request: Request) -> Response:
        if qb.old:
            return PlainTextResponse("Not Found", status_code=404)
        if not authed(request):
            return forbidden()
        return JSONResponse([{"name": "eth0", "value": "eth0"}, {"name": "wg0", "value": "wg0"}])

    # ------------------------------------------------------------------ transfer
    @app.get("/api/v2/transfer/info")
    async def transfer(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        s = qb.server_state
        return JSONResponse(
            {
                k: s[k]
                for k in (
                    "connection_status",
                    "dht_nodes",
                    "dl_info_speed",
                    "dl_info_data",
                    "up_info_speed",
                    "up_info_data",
                    "dl_rate_limit",
                    "up_rate_limit",
                )
            }
        )

    @app.get("/api/v2/transfer/speedLimitsMode")
    async def slm(request: Request) -> Response:
        return PlainTextResponse(str(qb.speed_limits_mode)) if authed(request) else forbidden()

    @app.post("/api/v2/transfer/toggleSpeedLimitsMode")
    async def toggle(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        qb.speed_limits_mode = 0 if qb.speed_limits_mode else 1
        qb.server_state["use_alt_speed_limits"] = bool(qb.speed_limits_mode)
        return PlainTextResponse("")

    # ------------------------------------------------------------------ torrents (read)
    @app.get("/api/v2/torrents/info")
    async def info(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        hs = request.query_params.get("hashes")
        items = [qb.info(qb.torrents[h]) for h in (hashes(hs) if hs else list(qb.torrents))]
        cat = request.query_params.get("category")
        if cat is not None:
            items = [t for t in items if t["category"] == cat]
        return JSONResponse(items)

    @app.get("/api/v2/torrents/properties")
    async def properties(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        t = qb.torrents.get(request.query_params.get("hash", ""))
        if t is None:
            return PlainTextResponse("Not Found", status_code=404)
        props = {
            "save_path": t["save_path"],
            "total_size": t["total_size"],
            "piece_size": 4 * MB,
            "seeding_time": t["seeding_time"],
            "share_ratio": t["ratio"],
            "nb_connections": t["num_seeds"],
            "up_limit": t["up_limit"],
            "dl_limit": t["dl_limit"],
            "comment": "",
            "is_private": t["private"],
        }
        if qb.old:
            props.pop("is_private")
        return JSONResponse(props)

    @app.get("/api/v2/torrents/trackers")
    async def trackers(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        h = request.query_params.get("hash", "")
        if h not in qb.trackers:
            return PlainTextResponse("Not Found", status_code=404)
        return JSONResponse(qb.trackers[h])

    @app.get("/api/v2/torrents/files")
    async def files(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        h = request.query_params.get("hash", "")
        if h not in qb.files:
            return PlainTextResponse("Not Found", status_code=404)
        return JSONResponse(qb.files[h])

    @app.get("/api/v2/torrents/categories")
    async def categories(request: Request) -> Response:
        return JSONResponse(qb.categories) if authed(request) else forbidden()

    @app.get("/api/v2/torrents/uploadLimit")
    async def up_limit(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        return JSONResponse(
            {h: qb.torrents[h]["up_limit"] for h in hashes(request.query_params.get("hashes"))}
        )

    @app.get("/api/v2/torrents/downloadLimit")
    async def dl_limit(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        return JSONResponse(
            {h: qb.torrents[h]["dl_limit"] for h in hashes(request.query_params.get("hashes"))}
        )

    # ------------------------------------------------------------------ torrents (write)
    def _write(path: str, fn: Any) -> None:
        async def endpoint(request: Request) -> Response:
            if not authed(request):
                return forbidden()
            f = await form(request)
            result = fn(f)
            return result if isinstance(result, Response) else PlainTextResponse("")

        app.add_api_route(path, endpoint, methods=["POST"])

    def _set_field(field: str, key: str = "limit", cast: Any = int) -> Any:
        def fn(f: dict[str, Any]) -> None:
            for h in hashes(f.get("hashes")):
                qb.torrents[h][field] = cast(f.get(key))

        return fn

    _write("/api/v2/torrents/setUploadLimit", _set_field("up_limit", cast=_kib))
    _write("/api/v2/torrents/setDownloadLimit", _set_field("dl_limit", cast=_kib))

    def share(f: dict[str, Any]) -> Response | None:
        if qb.old and "inactiveSeedingTimeLimit" in f:
            return PlainTextResponse("Bad Request", status_code=400)
        for h in hashes(f.get("hashes")):
            qb.torrents[h]["ratio_limit"] = float(f["ratioLimit"])
            qb.torrents[h]["seeding_time_limit"] = int(f["seedingTimeLimit"])
            if "inactiveSeedingTimeLimit" in f:
                qb.torrents[h]["inactive_seeding_time_limit"] = int(f["inactiveSeedingTimeLimit"])
        return None

    _write("/api/v2/torrents/setShareLimits", share)

    def recheck(f: dict[str, Any]) -> None:
        for h in hashes(f.get("hashes")):
            t = qb.torrents[h]
            t["state"] = "checkingUP" if t["progress"] >= 1 else "checkingDL"

    _write("/api/v2/torrents/recheck", recheck)
    _write("/api/v2/torrents/reannounce", lambda f: None)
    for name in ("topPrio", "bottomPrio", "increasePrio", "decreasePrio"):
        _write(f"/api/v2/torrents/{name}", lambda f: None)

    def delete(f: dict[str, Any]) -> None:
        hs = hashes(f.get("hashes"))
        qb.deletes.append((hs, str(f.get("deleteFiles", "false")).lower() == "true"))
        for h in hs:
            qb.torrents.pop(h, None)
            qb.trackers.pop(h, None)
            qb.files.pop(h, None)

    _write("/api/v2/torrents/delete", delete)
    # present only so tests can assert MSO never calls them
    _write("/api/v2/torrents/addTrackers", lambda f: None)
    _write("/api/v2/torrents/removeTrackers", lambda f: None)

    @app.post("/api/v2/torrents/add")
    async def add(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        f = await parse_form(request)
        up = f.get("torrents")
        data = up if isinstance(up, bytes) else None
        url = f.get("urls")
        if not data and not url:
            return PlainTextResponse("Fails.")
        ident = hashlib.sha1(data or str(url).encode()).hexdigest()  # noqa: S324
        name = f"toegevoegd-{ident[:8]}"
        if data:
            try:
                ident, name = _torrent_identity(data)
            except Exception:
                return PlainTextResponse("Fails.")
        if ident in qb.torrents:
            return PlainTextResponse("Fails.")
        paused = str(f.get("paused", f.get("stopped", "false"))).lower() == "true"
        t = _torrent(
            name,
            category=str(f.get("category") or ""),
            size=1 * GB,
            progress=0.0,
            state="stoppedDL" if paused else "metaDL",
            tracker="",
            private=False,
        )
        t["hash"] = t["infohash_v1"] = ident
        if f.get("savepath"):
            t["save_path"] = str(f["savepath"])
        t["tags"] = str(f.get("tags") or "")
        t["skip_checking"] = str(f.get("skip_checking", "false")).lower() == "true"
        qb.torrents[ident] = t
        qb.trackers[ident] = []
        qb.files[ident] = []
        return PlainTextResponse("Ok.")

    # ------------------------------------------------------------------ sync
    @app.get("/api/v2/sync/maindata")
    async def maindata(request: Request) -> Response:
        if not authed(request):
            return forbidden()
        qb._recompute()
        rid = int(request.query_params.get("rid", "0") or 0)
        snap = qb.snapshot()
        qb.rid += 1
        prev = qb.snapshots.get(rid)
        qb.snapshots[qb.rid] = copy.deepcopy(snap)
        for old in [r for r in qb.snapshots if r < qb.rid - 20]:
            qb.snapshots.pop(old)
        if rid == 0 or prev is None:
            return JSONResponse({"rid": qb.rid, "full_update": True, **snap, "tags": []})
        out: dict[str, Any] = {"rid": qb.rid, "torrents": {}, "server_state": {}}
        for h, t in snap["torrents"].items():
            old_t = prev["torrents"].get(h)
            diff = t if old_t is None else {k: v for k, v in t.items() if old_t.get(k) != v}
            if diff:
                out["torrents"][h] = diff
        removed = [h for h in prev["torrents"] if h not in snap["torrents"]]
        if removed:
            out["torrents_removed"] = removed
        out["server_state"] = {
            k: v for k, v in snap["server_state"].items() if prev["server_state"].get(k) != v
        }
        cats = {k: v for k, v in snap["categories"].items() if prev["categories"].get(k) != v}
        if cats:
            out["categories"] = cats
        return JSONResponse(out)

    return app
