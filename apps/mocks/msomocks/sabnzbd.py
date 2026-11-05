"""Fake SABnzbd (``/api?mode=…&output=json&apikey=…``) for tests and the container test stack.

Implements: version, queue, history (incl. fail_message, stage_log, last_history_update conditional polling),
server_stats (per-server daily bytes + articles_tried/articles_success), get_config&section=servers,
set_config&section=servers, retry (failed history job back into the queue),
status/fullstatus (per-server ``servererror``/``serveractive``), warnings
(``[{text, type, time}]``), get_cats, addurl, addfile (multipart parsed without extra dependencies).

Faults (``create_app(**faults)`` / ``--fault key=value``):
- ``api_key``             expected key (default ``sabkey``)
- ``scenario``            history content: ``mixed`` (default: success, expired account, auth denied, article
                          missing, repair failed, encrypted), ``success``, ``expired``, ``auth``, ``missing``,
                          ``empty``
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
from fastapi.responses import JSONResponse

VERSION = "4.3.3"
CATEGORIES = ["*", "tv", "tv-anime", "movies", "movies-anime", "software"]

FAIL_MESSAGES = {
    "expired": "Failed login for server news.eweka.test: 481 Account expired",
    "auth": "Failed login for server news.newshosting.test: 481 Authentication failed",
    "missing": "Aborted, cannot be completed - https://sabnzbd.org/not-complete",
    "repair": "Repair failed, not enough repair blocks (12 short)",
    "encrypted": "Unpacking failed, archive requires a password",
    "unpack": "Unpacking failed, see logfile",
}


def _slot(i: int, name: str, cat: str, kind: str, completed: int) -> dict[str, Any]:
    failed = kind != "success"
    stage_log = [{"name": "Download", "actions": ["Downloaded in 3 min 2 sec at an average of 45.1 MB/s"]}]
    if kind == "repair":
        stage_log.append(
            {"name": "Repair", "actions": ["Repair failed, not enough repair blocks (12 short)"]}
        )
    if kind == "encrypted":
        stage_log.append({"name": "Unpack", "actions": ["Unpacking failed, archive requires a password"]})
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
        "stage_log": stage_log,
        "storage": f"/downloads/{name}",
        "url": "",
        "size": "1.4 GB",
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
    def __init__(self, scenario: str = "mixed") -> None:
        self.lock = threading.Lock()
        self.history: list[dict[str, Any]] = _history_for(scenario)
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
                "password": "*****",
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
                "password": "*****",
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
                "password": "*****",
            },
        ]
        today = date.today()
        days = [(today - timedelta(days=d)).isoformat() for d in range(7)]
        self.stats: dict[str, dict[str, Any]] = {
            "news.eweka.test": {
                "daily": {d: 10_000_000_000 for d in days},
                "articles_tried": {d: 14_000 for d in days},
                "articles_success": {d: 13_860 for d in days},
            },
            "news.newshosting.test": {
                "daily": {d: 120_000_000 for d in days},
                "articles_tried": {d: 140 for d in days},
                "articles_success": {d: 98 for d in days},
            },
            "block.example.test": {
                "daily": {d: 5_000_000 for d in days},
                "articles_tried": {d: 42 for d in days},
                "articles_success": {d: 30 for d in days},
            },
        }

        # SABnzbd's own per-server state: name -> servererror text ("" = fine); warnings list
        self.server_errors: dict[str, str] = {}
        self.server_inactive: set[str] = set()
        self.warnings: list[dict[str, Any]] = []
        self.retried: list[str] = []  # nzo_ids for which mode=retry was called

    def set_server_error(self, name: str, text: str, *, warn: bool = True, at: int | None = None) -> None:
        self.server_errors[name] = text
        if warn and text:
            self.warnings.append({"text": text, "type": "WARNING", "time": at or int(time.time())})

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

    def add_history(self, slot: dict[str, Any]) -> None:
        with self.lock:
            self.history.append(slot)
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
                **s,
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
        "password": "*****",
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


def create_app(**faults: Any) -> FastAPI:
    api_key = str(faults.get("api_key", "sabkey"))
    state = SabState(str(faults.get("scenario", "mixed")))
    ignore_set = str(faults.get("ignore_set_config", "0")) in ("1", "true")
    broken = str(faults.get("broken_mode", ""))
    status_mode = str(faults.get("status_mode", "status"))
    retry_new_id = str(faults.get("retry_new_id", "0")) in ("1", "true")
    retry_noop = str(faults.get("retry_noop", "0")) in ("1", "true")
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
        if not params.get("apikey"):
            return ok({"status": False, "error": "API Key Required"})
        if params.get("apikey") != api_key:
            return ok({"status": False, "error": "API Key Incorrect"})
        if broken and mode == broken:
            return JSONResponse({"error": "internal"}, status_code=500)
        with state.lock:
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
                items = sorted(state.history, key=lambda s: -s["completed"])
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
            if mode == "get_config":
                if params.get("section") != "servers":
                    return ok({"config": {}})
                return ok({"config": {"servers": [dict(s) for s in state.servers]}})
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
                return ok({"categories": CATEGORIES})
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
                        "cat": params.get("cat") or "*",
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
