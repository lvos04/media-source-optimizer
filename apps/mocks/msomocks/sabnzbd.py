"""Fake SABnzbd (``/api?mode=…&output=json&apikey=…``) for tests and the container test stack.

Implements: version, queue, history (incl. fail_message, stage_log in SABnzbd's real wording, last_history_update
conditional polling, ``archive=1`` for jobs removed from the history as SABnzbd ≥ 4 keeps them),
server_stats (per-server daily bytes + articles_tried/articles_success — like the real SABnzbd, whose
``articles_success`` field holds the number of FAILED articles: ``sabnzbd/api.py`` ``_api_server_stats`` →
``BPSMeter.amounts`` → ``article_stats_failed``), get_config&section=servers (password masked,
``expire_date``/``quota``/``usage_at_start``), set_config&section=servers, config&name=test_server,
retry (failed history job back into the queue), status/fullstatus (per-server ``servererror``/``serveractive``),
warnings (``[{text, type, time}]``), get_cats, addurl, addfile (multipart parsed without extra dependencies).

Faults (``create_app(**faults)`` / ``--fault key=value``):
- ``api_key``             expected key (default ``sabkey``)
- ``scenario``            history content: ``mixed`` (default: success, expired account, auth denied, article
                          missing, repair failed, encrypted), ``success``, ``expired``, ``auth``, ``missing``,
                          ``unpack`` (field report: "Unpacking failed, see logfile" with complete data), ``empty``
- ``ignore_set_config=1`` accept set_config but don't change anything (verification failure)
- ``broken_mode=<mode>``  that mode answers HTTP 500
- ``server_errors=report`` like the field report: newshosting "502 Authentication Failed", block server
                          "482 Account expired" (in ``servererror`` and as warnings); eweka works
- ``server_errors=field`` the 1.4.0 field situation with 5 servers: newshosting + usenetbucket "502 Authentication
                          Failed", thundernews "502 Access Denied", xsnews "482 Account expired", eweka works. Only
                          in SABnzbd's warning list (``servererror`` already cleared, as SABnzbd does after its
                          retry pause) — the case where the 1.3.1 connector test showed no failing servers.
- ``retry_new_id=1``      ``mode=retry`` gives the retried job a new nzo_id (as newer SABnzbd versions can)
- ``retry_noop=1``        ``mode=retry`` answers ``status: true`` but nothing reaches the queue (verification)
- ``status_mode=fullstatus|none``  only answer the old ``fullstatus`` mode / neither (old SABnzbd)
- ``test_server=unsupported|locked``  ``mode=config&name=test_server`` answers "not implemented" / "Configuration
                          locked" (otherwise like SABnzbd 5.1: masked password + ``server`` → stored password)
- ``categories=tv,movies``  the categories besides '*' (default: tv, tv-anime, movies, movies-anime, software)
- ``config_locked=1``     like SABnzbd's ``cfg.configlock`` (api.py ``_api_config``/``_api_set_config``/
                          ``_api_del_config``): ``mode=config`` (so also the server test), ``set_config`` and
                          ``del_config`` for every section answer "Configuration locked"; reads keep working

Categories (1.6.0) behave like SABnzbd 4.x/5.x: ``get_cats`` lists '*' first, ``get_config&section=categories``
returns ``[{name, order, pp, script, dir, newzbin, priority}]`` (''keyword'' = one category), ``set_config
&section=categories&keyword=<name>`` creates/updates a category with a lower-case name (``api.handle_cat_api``),
``del_config&section=categories&keyword=<name>`` removes it, ``get_config&section=misc&keyword=complete_dir``
returns that folder. A job sent with a category SABnzbd does not know lands in '*' (``misc.cat_to_opts``:
"Ignore the input category if we don't know it"); ``ignore_set_config=1`` also makes del_config a no-op.
Tests can reach the live state through ``app.state.sab`` (a ``SabState``).
"""

from __future__ import annotations

import email.parser
import email.policy
import itertools
import threading
import time
from datetime import date, timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

VERSION = "4.3.3"
CATEGORIES = ["*", "tv", "tv-anime", "movies", "movies-anime", "software"]
COMPLETE_DIR = "/downloads/complete"
DEFAULT_PRIORITY = -100  # SABnzbd: category priority "Default" (= that of '*')


def _category(name: str, order: int) -> dict[str, Any]:
    """SABnzbd ``ConfigCat.get_dict``; '*' gets the defaults of ``config.get_categories``."""
    if name == "*":
        return {"name": "*", "order": 0, "pp": "3", "script": "None", "dir": "", "newzbin": "", "priority": 0}
    return {"name": name, "order": order, "pp": "", "script": "Default", "dir": "", "newzbin": "",
            "priority": DEFAULT_PRIORITY}

FAIL_MESSAGES = {
    "expired": "Failed login for server news.eweka.test: 481 Account expired",
    "auth": "Failed login for server news.newshosting.test: 481 Authentication failed",
    "missing": "Aborted, cannot be completed - https://sabnzbd.org/not-complete",
    "repair": "Repair failed, not enough repair blocks (12 short)",
    "encrypted": "Unpacking failed, archive requires a password",
    "unpack": "Unpacking failed, see logfile",
    "crc": "Unpacking failed, CRC error",
    "disk": "Unpacking failed, write error or disk is full?",
    "permission": "Unpacking failed, write error or disk is full? Permission denied",
}


def stage_log(kind: str, name: str = "Release") -> list[dict[str, Any]]:
    """SABnzbd's per-job processing log as ``mode=history`` returns it (``sabnzbd/api.py`` build_history →
    ``database.unpack_history_info``): stages in STAGES order, the Download report joined with ``<br/>``
    (``nzb/object.py`` set_download_report), set-specific lines prefixed with ``[setname]``."""
    report = ["Downloaded in 3 min 2 sec at an average of 45.1 MB/s", "Age: 12d"]
    if kind in ("missing", "repair"):
        report.append(f"{1532 if kind == 'missing' else 312} articles were missing")
    log: list[dict[str, Any]] = [
        {"name": "Source", "actions": [f"{name}.nzb"]},
        {"name": "Download", "actions": ["<br/>".join(report)]},
        {"name": "Servers", "actions": ["Eweka=1.4 GB, Newshosting=12.0 MB"]},
    ]
    if kind == "missing":  # aborted during the download: no post-processing
        return log
    if kind == "repair":
        log.append({"name": "Repair", "actions": [f"[{name}] Repair failed, not enough repair blocks (12 short)"]})
        return log
    if kind in ("success", "encrypted", "unpack", "crc", "disk", "permission", "expired", "auth"):
        log.append({"name": "Repair", "actions": [f"[{name}] Quick Check OK"]})
    unpack = {
        "success": f"[{name}] Unpacked 3 files/folders in 4 seconds",
        "encrypted": f"[{name}] Unpacking failed, archive requires a password",
        "crc": f"[{name}] Unpacking failed, CRC error",
        "disk": f"[{name}] Unpacking failed, write error or disk is full? the file /downloads/complete/{name}.mkv",
        "permission": f"[{name}] Unpacking failed, write error or disk is full? Permission denied",
    }.get(kind)
    if unpack:  # 7-Zip's "see logfile" failure writes no Unpack line (newsunpack.seven_extract_core)
        log.append({"name": "Unpack", "actions": [unpack]})
    return log


def _slot(i: int, name: str, cat: str, kind: str, completed: int) -> dict[str, Any]:
    failed = kind != "success"
    return {
        "nzo_id": f"SABnzbd_nzo_{i:04d}",
        "name": name,
        "nzb_name": f"{name}.nzb",
        "category": cat,
        "status": "Failed" if failed else "Completed",
        "fail_message": FAIL_MESSAGES.get(kind, "") if failed else "",
        "bytes": 1_500_000_000 + i,
        "downloaded": 1_500_000_000 + i if not failed else 400_000_000,
        "download_time": 180,
        "completed": completed,
        "stage_log": stage_log(kind, name),
        "storage": f"/downloads/{name}",
        "url": "",
        "size": "1.4 GB",
        "archive": False,
    }


def _history_for(scenario: str) -> list[dict[str, Any]]:
    now = int(time.time())
    plans = {
        "mixed": [
            ("Show.Name.S01E01.1080p.WEB.H264-GRP", "tv", "success"),
            ("Show.Name.S01E02.1080p.WEB.H264-GRP", "tv", "expired"),
            ("Anime.Title.S01E03.1080p.WEB.H264-GRP", "tv-anime", "auth"),
            ("Movie.Title.2020.2160p.UHD.BluRay.x265-GRP", "movies", "missing"),
            ("Movie.Other.2019.1080p.BluRay.x264-GRP", "movies", "repair"),
            ("Anime.Movie.2021.1080p.BluRay.x264-GRP", "movies-anime", "encrypted"),
        ],
        "success": [("Show.Name.S01E01.1080p.WEB.H264-GRP", "tv", "success")],
        "expired": [("Show.Name.S01E02.1080p.WEB.H264-GRP", "tv", "expired")],
        "auth": [("Show.Name.S01E03.1080p.WEB.H264-GRP", "tv", "auth")],
        "missing": [("Movie.Title.2020.2160p.UHD.BluRay.x265-GRP", "movies", "missing")],
        "unpack": [
            ("Paranoia.Agent.S01E01.2004.1080p.BluRay.x265.10bit.FLAC.2.0-ADE", "*", "unpack"),
            ("Slay 2024 1080p WEBRip-SMILEY", "*", "unpack"),
        ],
        "empty": [],
    }[scenario]
    return [_slot(i + 1, n, c, k, now - (len(plans) - i) * 600) for i, (n, c, k) in enumerate(plans)]


class SabState:
    def __init__(self, scenario: str = "mixed", categories: list[str] | None = None) -> None:
        self.lock = threading.Lock()
        names = ["*", *(c for c in (categories if categories is not None else CATEGORIES) if c != "*")]
        self.categories: list[dict[str, Any]] = [_category(n, i) for i, n in enumerate(names)]
        self.complete_dir = COMPLETE_DIR
        self.history: list[dict[str, Any]] = _history_for(scenario)
        self.archive: list[dict[str, Any]] = []  # jobs removed from the history (SABnzbd ≥ 4 archives them)
        self.queue: list[dict[str, Any]] = []
        self.last_history_update = 1
        self.counter = itertools.count(1000)
        self.servers: list[dict[str, Any]] = [
            {
                "name": "news.eweka.test",
                "displayname": "Eweka",
                "host": "news.eweka.test",
                "port": 563,
                "ssl": 1,
                "connections": 20,
                "priority": 0,
                "enable": 1,
                "optional": 0,
                "retention": 0,
                "username": "user1",
                "password": "**********",
                "expire_date": "",
                "quota": "",
                "usage_at_start": 0,
            },
            {
                "name": "news.newshosting.test",
                "displayname": "Newshosting",
                "host": "news.newshosting.test",
                "port": 563,
                "ssl": 1,
                "connections": 30,
                "priority": 1,
                "enable": 1,
                "optional": 0,
                "retention": 0,
                "username": "user2",
                "password": "**********",
                "expire_date": "",
                "quota": "",
                "usage_at_start": 0,
            },
            {
                "name": "block.example.test",
                "displayname": "Blok",
                "host": "block.example.test",
                "port": 563,
                "ssl": 1,
                "connections": 10,
                "priority": 0,
                "enable": 1,
                "optional": 1,
                "retention": 0,
                "username": "user3",
                "password": "**********",
                "expire_date": "",
                "quota": "",
                "usage_at_start": 0,
            },
        ]
        today = date.today()
        days = [(today - timedelta(days=d)).isoformat() for d in range(7)]
        # per server and day: bytes, articles tried and articles FAILED (rendered as SABnzbd's misnamed
        # ``articles_success`` field by :meth:`server_stats`)
        self.stats: dict[str, dict[str, Any]] = {
            "news.eweka.test": {
                "daily": {d: 10_000_000_000 for d in days},
                "articles_tried": {d: 14_000 for d in days},
                "articles_failed": {d: 140 for d in days},
            },
            "news.newshosting.test": {
                "daily": {d: 120_000_000 for d in days},
                "articles_tried": {d: 140 for d in days},
                "articles_failed": {d: 42 for d in days},
            },
            "block.example.test": {
                "daily": {d: 5_000_000 for d in days},
                "articles_tried": {d: 42 for d in days},
                "articles_failed": {d: 12 for d in days},
            },
        }

        # SABnzbd's own per-server state: name -> servererror text ("" = fine); warnings list
        self.server_errors: dict[str, str] = {}
        self.server_inactive: set[str] = set()
        self.warnings: list[dict[str, Any]] = []
        self.retried: list[str] = []  # nzo_ids for which mode=retry was called

        # Server test (mode=config&name=test_server): the stored passwords (never returned by get_config), the
        # account problem a login gets (set by set_server_error, kept after SABnzbd clears ``servererror``),
        # explicit overrides per server name and every call as received (tests check what MSO sent).
        self.passwords: dict[str, str] = {}
        self.account_errors: dict[str, str] = {}
        self.test_results: dict[str, tuple[bool, str]] = {}
        self.test_calls: list[dict[str, Any]] = []

    def set_server_error(self, name: str, text: str, *, warn: bool = True, at: int | None = None) -> None:
        self.server_errors[name] = text
        if text:
            self.account_errors[name] = text
        if warn and text:
            self.warnings.append({"text": text, "type": "WARNING", "time": at or int(time.time())})

    def fix_account(self, name: str) -> None:
        """The owner repaired the account: logins work again, SABnzbd's error and warnings for it are gone."""
        self.account_errors.pop(name, None)
        self.test_results.pop(name, None)
        self.server_errors[name] = ""
        self.warnings = [w for w in self.warnings if name not in w["text"]]

    def public_servers(self, keyword: str | None = None) -> list[dict[str, Any]]:
        """``get_config&section=servers[&keyword=<name>]``: like SABnzbd, the password only as asterisks."""
        return [dict(s) for s in self.servers if not keyword or s["name"] == keyword]

    def test_server(self, kw: dict[str, Any]) -> tuple[bool, str]:
        """``api.test_nntp_server_dict`` (SABnzbd 5.1): a masked password is replaced by the stored one only when
        ``server`` names a configured server; the result follows the account state of the server at ``host``."""
        self.test_calls.append({k: v for k, v in kw.items() if k not in ("apikey", "mode", "output", "name")})
        host = str(kw.get("host", "")).strip()
        port = str(kw.get("port", "0")).strip()
        username = str(kw.get("username", "")).strip()
        password = str(kw.get("password", "")).strip()
        server = str(kw.get("server", "")).strip()
        if not host:
            return False, "The hostname is not set."
        if not str(kw.get("connections", "1")).strip("0 "):
            return False, "There are no connections set. Please set at least one connection."
        if "*" in password and not password.strip("*"):
            stored = next((s for s in self.servers if s["name"] == server), None)
            if stored is None:
                return False, "Password masked in ******, please re-enter"
            password = self.passwords.get(server, "secret")
        target = next((s for s in self.servers if s["host"] == host), None)
        if target is None:
            return False, f'Server address "{host}:{port}" is not valid.'
        if target["name"] in self.test_results:
            return self.test_results[target["name"]]
        if not username or not password:
            return False, "Server requires username and password."
        if username != target["username"] or password != self.passwords.get(target["name"], "secret"):
            return False, "481 Authentication failed"
        err = self.account_errors.get(target["name"])
        if err:
            return False, err.split("reported: ", 1)[-1].rstrip(".") if "reported: " in err else err
        # SABnzbd ≥ 5.1: after a passing test slower than 50 ms with fewer than 5 articles per request, a "helpful
        # warning" lands in the warning list (the mock's provider always answers in 481 ms)
        try:
            pipelining = int(str(kw.get("pipelining_requests", "2")).strip() or 2)
        except ValueError:
            pipelining = 2
        if pipelining < 5:
            self.warnings.append({
                "text": f"Server {host} took 481 ms to respond. On high-latency connections, increasing 'Articles per "
                "request' to a value between 5 and 10 can improve download speed. See: "
                "https://sabnzbd.org/wiki/advanced/nntp-pipelining\nTo prevent all helpful warnings, disable "
                "Special setting 'helpful_warnings'.",
                "type": "WARNING", "time": int(time.time()),
            })
        return True, "Connection Successful!"

    def status(self) -> dict[str, Any]:
        servers = []
        for s in sorted(self.servers, key=lambda x: (x["priority"], x["name"])):
            if not s["enable"]:
                continue
            servers.append(
                {
                    "servername": s["displayname"],
                    "servertotalconn": s["connections"],
                    "serverssl": s["ssl"],
                    "serveractiveconn": 0,
                    "serveroptional": bool(s["optional"]),
                    "serveractive": s["name"] not in self.server_inactive,
                    "servererror": self.server_errors.get(s["name"], ""),
                    "serverpriority": s["priority"],
                    "serverbps": "0 ",
                    "serverconnections": [],
                }
            )
        return {"status": {"paused": False, "servers": servers, "warnings": list(self.warnings)}}

    # ---- categories (config.get_ordered_categories / misc.cat_to_opts)
    def ordered_categories(self) -> list[dict[str, Any]]:
        star = [c for c in self.categories if c["name"] == "*"]
        rest = sorted((c for c in self.categories if c["name"] != "*"), key=lambda c: c["order"])
        return [dict(c) for c in star + rest]

    def category(self, name: str) -> dict[str, Any] | None:
        return next((c for c in self.categories if c["name"] == name), None)

    def resolve_cat(self, cat: str | None) -> str:
        """The category SABnzbd really files a new job under: unknown → '*'."""
        name = (cat or "").strip().lower()
        if name in ("", "none", "default"):
            return "*"
        return name if self.category(name) is not None else "*"

    def add_category(self, name: str, **fields: Any) -> dict[str, Any]:
        name = name.lower()
        cat = self.category(name)
        if cat is None:
            cat = _category(name, 0)
            self.categories.append(cat)
        for k in ("order", "pp", "script", "dir", "newzbin", "priority"):
            if k in fields and fields[k] is not None:
                cat[k] = int(fields[k]) if k in ("order", "priority") else str(fields[k])
        return cat

    def add_history(self, slot: dict[str, Any]) -> None:
        with self.lock:
            self.history.append(slot)
            self.last_history_update += 1

    def archive_job(self, nzo_id: str) -> None:
        """``mode=history&name=delete`` with archiving (SABnzbd ≥ 4 default): the job leaves the history and is
        only returned with ``archive=1``."""
        with self.lock:
            for slot in list(self.history):
                if slot["nzo_id"] == nzo_id:
                    self.history.remove(slot)
                    self.archive.append({**slot, "archive": True})
                    self.last_history_update += 1

    def server_stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {"total": 0, "month": 0, "week": 0, "day": 0, "servers": {}}
        today = date.today().isoformat()
        for name, s in self.stats.items():
            total = sum(s["daily"].values())
            out["servers"][name] = {
                "total": total,
                "month": total,
                "week": total,
                "day": s["daily"].get(today, 0),
                "daily": dict(s["daily"]),
                "articles_tried": dict(s["articles_tried"]),
                # the real SABnzbd puts article_stats_failed in this field (api.py _api_server_stats)
                "articles_success": dict(s.get("articles_failed") or {}),
            }
            for k in ("total", "month", "week"):
                out[k] += total
            out["day"] += s["daily"].get(today, 0)
        return out


def _server(name: str, display: str, priority: int, connections: int) -> dict[str, Any]:
    return {
        "name": name,
        "displayname": display,
        "host": name,
        "port": 563,
        "ssl": 1,
        "connections": connections,
        "priority": priority,
        "enable": 1,
        "optional": 0,
        "retention": 0,
        "username": "user",
        "password": "**********",
        "expire_date": "",
        "quota": "",
        "usage_at_start": 0,
    }


def _parse_multipart(content_type: str, body: bytes) -> dict[str, tuple[str | None, bytes]]:
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
    )
    out: dict[str, tuple[str | None, bytes]] = {}
    if not msg.is_multipart():
        return out
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            out[str(name)] = (part.get_filename(), part.get_payload(decode=True) or b"")
    return out


def _category_config(state: SabState, mode: str, params: dict[str, Any], *, ignore: bool,
                     locked: bool) -> dict[str, Any]:
    """get/set/del_config for ``section=categories`` (and ``misc`` reads), as ``sabnzbd/api.py`` answers them."""
    section, keyword = params.get("section"), params.get("keyword")
    if section == "misc":
        if mode != "get_config":
            return {"status": False, "error": "not implemented"}
        misc = {"complete_dir": state.complete_dir, "download_dir": "/downloads/incomplete"}
        return {"config": {"misc": {keyword: misc[keyword]} if keyword in misc else ({} if keyword else misc)}}
    if mode in ("set_config", "del_config") and locked:
        return {"status": False, "error": "Configuration locked"}
    if mode == "get_config":
        if keyword:
            cat = state.category(str(keyword))
            return {"config": {"categories": [dict(cat)]}} if cat else {"config": {}}
        return {"config": {"categories": state.ordered_categories()}}
    if mode == "set_config":
        name = str(keyword or params.get("name") or "").lower()
        if not name:
            return {"config": {"categories": state.ordered_categories()}}
        if ignore:
            cat = state.category(name)
            return {"config": {"categories": [dict(cat)]}} if cat else {"config": {}}
        fields = {k: params[k] for k in ("order", "pp", "script", "dir", "newzbin", "priority") if k in params}
        return {"config": {"categories": [dict(state.add_category(name, **fields))]}}
    # del_config: SABnzbd answers status true, also for a name that does not exist
    cat = state.category(str(keyword or ""))
    if cat is not None and cat["name"] != "*" and not ignore:
        state.categories.remove(cat)
    return {"status": True}


def create_app(**faults: Any) -> FastAPI:
    api_key = str(faults.get("api_key", "sabkey"))
    cat_fault = faults.get("categories")
    state = SabState(
        str(faults.get("scenario", "mixed")),
        [c.strip() for c in str(cat_fault).split(",") if c.strip()] if cat_fault is not None else None,
    )
    config_locked = str(faults.get("config_locked", "0")) in ("1", "true")
    ignore_set = str(faults.get("ignore_set_config", "0")) in ("1", "true")
    broken = str(faults.get("broken_mode", ""))
    status_mode = str(faults.get("status_mode", "status"))
    retry_new_id = str(faults.get("retry_new_id", "0")) in ("1", "true")
    retry_noop = str(faults.get("retry_noop", "0")) in ("1", "true")
    test_mode = str(faults.get("test_server", ""))
    if str(faults.get("server_errors", "")) == "report":
        state.set_server_error(
            "news.newshosting.test", "Server news.newshosting.test reported: 502 Authentication Failed"
        )
        state.set_server_error("block.example.test", "Server block.example.test reported: 482 Account expired.")
    if str(faults.get("server_errors", "")) == "field":
        state.servers = [
            _server("news.eweka.test", "Eweka", 0, 20),
            _server("news.newshosting.test", "Newshosting", 1, 30),
            _server("reader.usenetbucket.test", "UsenetBucket", 1, 20),
            _server("nl.thundernews.test", "Thundernews", 2, 20),
            _server("reader.xsnews.test", "XSNews", 2, 10),
        ]
        for name, text in (
            ("news.newshosting.test", "Server news.newshosting.test reported: 502 Authentication Failed"),
            ("reader.usenetbucket.test", "Server reader.usenetbucket.test reported: 502 Authentication Failed"),
            ("nl.thundernews.test", "Server nl.thundernews.test reported: 502 Access Denied"),
            ("reader.xsnews.test", "Server reader.xsnews.test reported: 482 Account expired."),
        ):
            state.set_server_error(name, text)
            state.server_errors[name] = ""  # cleared after SABnzbd's retry pause; the warning stays
    app = FastAPI(title="Mock SABnzbd")
    app.state.sab = state
    app.state.api_key = api_key

    def ok(data: dict[str, Any]) -> JSONResponse:
        return JSONResponse(data)

    @app.api_route("/api", methods=["GET", "POST"])
    async def api(request: Request) -> JSONResponse:
        params: dict[str, Any] = dict(request.query_params)
        files: dict[str, tuple[str | None, bytes]] = {}
        if request.method == "POST":
            ctype = request.headers.get("content-type", "")
            body = await request.body()
            if ctype.startswith("multipart/form-data"):
                files = _parse_multipart(ctype, body)
            elif ctype.startswith("application/x-www-form-urlencoded"):
                from urllib.parse import parse_qsl

                params.update(dict(parse_qsl(body.decode())))
        mode = params.get("mode", "")
        if mode == "version":  # SABnzbd answers version without a key
            return ok({"version": VERSION})
        # like SABnzbd (interface.py secured_expose/check_apikey): HTTP 403 with a plain-text message.
        # app.state.api_key can be changed at runtime (a key replaced in SABnzbd).
        if not params.get("apikey"):
            return PlainTextResponse("API Key Required", status_code=403)
        if params.get("apikey") != app.state.api_key:
            return PlainTextResponse("API Key Incorrect", status_code=403)
        if broken and mode == broken:
            return JSONResponse({"error": "internal"}, status_code=500)
        with state.lock:
            if mode in ("get_config", "set_config", "del_config") and params.get("section") in ("categories", "misc"):
                return ok(_category_config(state, mode, params, ignore=ignore_set, locked=config_locked))
            if mode == "queue":
                slots = state.queue[int(params.get("start", 0)) :][: int(params.get("limit", 100))]
                return ok(
                    {
                        "queue": {
                            "status": "Downloading" if state.queue else "Idle",
                            "paused": False,
                            "kbpersec": "45120.5" if state.queue else "0",
                            "mbleft": "1024.0",
                            "noofslots": len(state.queue),
                            "slots": slots,
                        }
                    }
                )
            if mode == "history":
                luh = params.get("last_history_update")
                if luh and luh.isdigit() and int(luh) == state.last_history_update:
                    return ok({"history": False})
                archive = params.get("archive") in ("1", "true")
                items = sorted(state.archive if archive else state.history, key=lambda s: -s["completed"])
                if params.get("nzo_ids"):
                    wanted = set(params["nzo_ids"].split(","))
                    items = [s for s in items if s["nzo_id"] in wanted]
                if params.get("failed_only") in ("1", "true"):
                    items = [s for s in items if s["status"] == "Failed"]
                start, limit = int(params.get("start", 0)), int(params.get("limit", 100))
                return ok(
                    {
                        "history": {
                            "slots": items[start : start + limit],
                            "noofslots": len(items),
                            "last_history_update": state.last_history_update,
                        }
                    }
                )
            if mode == "server_stats":
                return ok(state.server_stats())
            if mode in ("status", "fullstatus"):
                if status_mode == "none" or (status_mode == "fullstatus" and mode == "status"):
                    return ok({"status": False, "error": "not implemented"})
                return ok(state.status())
            if mode == "warnings":
                return ok({"warnings": list(state.warnings)})
            if mode in ("set_config", "del_config", "set_config_default") and config_locked:
                return ok({"status": False, "error": "Configuration locked"})
            if mode == "config":
                if test_mode == "locked" or config_locked:
                    return ok({"status": False, "error": "Configuration locked"})
                if params.get("name") != "test_server" or test_mode == "unsupported":
                    return ok({"status": False, "error": "not implemented"})
                result, message = state.test_server(params)
                return ok({"value": {"result": result, "message": message}})
            if mode == "get_config":
                if params.get("section") != "servers":
                    return ok({"config": {}})
                return ok({"config": {"servers": state.public_servers(params.get("keyword"))}})
            if mode == "set_config":
                if params.get("section") != "servers":
                    return ok({"status": False, "error": "not supported by mock"})
                name = params.get("keyword")
                srv = next((s for s in state.servers if s["name"] == name), None)
                if srv is None:
                    return ok({"status": False, "error": "server not found"})
                if not ignore_set:
                    for field in ("priority", "enable", "optional"):
                        if field in params:
                            srv[field] = int(params[field])
                return ok({"config": {"servers": [dict(srv)]}})
            if mode == "retry":
                nzo = params.get("value", "")
                slot = next((h for h in state.history if h["nzo_id"] == nzo and h["status"] == "Failed"), None)
                if slot is None:
                    return ok({"status": False, "error": "No such item"})
                state.retried.append(nzo)
                if retry_noop:
                    return ok({"status": True, "nzo_id": nzo})
                state.history.remove(slot)
                state.last_history_update += 1
                new_id = f"SABnzbd_nzo_{next(state.counter)}" if retry_new_id else nzo
                state.queue.append(
                    {
                        "nzo_id": new_id,
                        "filename": slot["name"],
                        "cat": slot["category"],
                        "status": "Queued",
                        "percentage": "0",
                        "mb": "1024.0",
                        "mbleft": "1024.0",
                        "timeleft": "0:05:00",
                        "priority": "Normal",
                    }
                )
                return ok({"status": True, "nzo_id": new_id})
            if mode == "get_cats":
                return ok({"categories": [c["name"] for c in state.ordered_categories()]})
            if mode in ("addurl", "addfile"):
                if mode == "addurl":
                    url = params.get("name", "")
                    if not url.startswith(("http://", "https://")):
                        return ok({"status": False, "error": "expects one parameter"})
                    title = params.get("nzbname") or url.rsplit("/", 1)[-1]
                else:
                    f = files.get("name") or files.get("nzbfile")
                    if not f or b"<nzb" not in f[1]:
                        return ok({"status": False, "error": "No file"})
                    title = (f[0] or "upload.nzb").removesuffix(".nzb")
                nzo = f"SABnzbd_nzo_{next(state.counter)}"
                state.queue.append(
                    {
                        "nzo_id": nzo,
                        "filename": title,
                        "cat": state.resolve_cat(params.get("cat")),
                        "status": "Queued",
                        "percentage": "0",
                        "mb": "1024.0",
                        "mbleft": "1024.0",
                        "timeleft": "0:05:00",
                        "priority": "Normal",
                    }
                )
                return ok({"status": True, "nzo_ids": [nzo]})
        return ok({"status": False, "error": "not implemented"})

    return app
