"""Fake NNTP server (plain TCP) for tests and the container test stack.

Commands: CAPABILITIES, AUTHINFO USER/PASS, GROUP, STAT, HEAD, OVER/XOVER, DATE, QUIT.

``serve(host, port, **faults)`` runs forever (``python -m msomocks nntp --port 1119``);
``start(**faults) -> FakeNNTP`` runs it on a free local port in a background thread (tests).

Faults / options:
- ``mode``: ``ok`` (default) | ``expired`` (481 Account expired) | ``auth_denied`` (481 Authentication failed)
  | ``block_exhausted`` (502 block exhausted) | ``too_many`` (greeting 400 Too many connections)
  | ``greeting_502`` (502 Access denied)
- ``username`` / ``password``: accepted credentials (default ``user`` / ``pass``)
- ``articles``: iterable of available message-ids (without <>), default ``seg1..seg1000@mock``
- ``missing_every``: N → every Nth default article is missing (simulates incomplete retention)
- ``groups``: {name: [(subject, date_rfc2822, message_id, bytes)]}; default ``alt.binaries.mso.test`` with a
  mix
  of readable and obfuscated posts
- ``max_connections``: concurrent sessions allowed (default 4); beyond → ``400 Too many connections``
- ``slow``: seconds to sleep before every response (timeouts)
"""

from __future__ import annotations

import socketserver
import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any


def default_group(n: int = 300) -> list[tuple[str, str, str, int]]:
    now = datetime.now(UTC)
    posts = []
    for i in range(n):
        posted = format_datetime(now - timedelta(minutes=n - i))
        if i % 3 == 0:  # obfuscated
            subject = (
                f'[1/40] - "{format(i * 7919 + 104729, "x")}a9f3c2b1e8d7{i:04d}.part01.rar" yEnc (1/120)'
            )
        elif i % 3 == 1:
            subject = (
                f'[3/45] - "Show.Name.S01E{(i % 20) + 1:02d}.1080p.WEB.H264-GRP.part02.rar" yEnc (5/130)'
            )
        else:
            subject = f'"Movie.Title.{2000 + i % 20}.2160p.UHD.BluRay.x265-GRP.vol00+01.par2" yEnc (1/1)'
        posts.append((subject, posted, f"hdr{i}@mock", 700_000))
    return posts


class _State:
    def __init__(self, **faults: Any) -> None:
        self.mode = str(faults.get("mode", "ok"))
        self.username = str(faults.get("username", "user"))
        self.password = str(faults.get("password", "pass"))
        missing_every = int(faults.get("missing_every", 0) or 0)
        arts = faults.get("articles")
        if arts is None:
            arts = [f"seg{i}@mock" for i in range(1, 1001) if not (missing_every and i % missing_every == 0)]
        self.articles: set[str] = {a.strip("<>") for a in arts}
        groups = faults.get("groups")
        self.groups: dict[str, list[tuple[str, str, str, int]]] = (
            groups if groups is not None else {"alt.binaries.mso.test": default_group()}
        )
        self.max_connections = int(faults.get("max_connections", 4))
        self.slow = float(faults.get("slow", 0) or 0)
        self.active = 0
        self.lock = threading.Lock()
        self.commands: list[str] = []  # log of commands (AUTHINFO PASS masked) for assertions
        self.peak = 0


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def send(self, line: str) -> None:
        st = self.server.state
        if st.slow:
            time.sleep(st.slow)
        self.wfile.write(line.encode("utf-8") + b"\r\n")

    def send_long(self, code_line: str, lines: Iterable[str]) -> None:
        st = self.server.state
        if st.slow:
            time.sleep(st.slow)
        out = [code_line] + [("." + ln if ln.startswith(".") else ln) for ln in lines] + ["."]
        self.wfile.write(("\r\n".join(out) + "\r\n").encode("utf-8"))

    def handle(self) -> None:
        st = self.server.state
        with st.lock:
            st.active += 1
            st.peak = max(st.peak, st.active)
            too_many = st.active > st.max_connections
        try:
            if st.mode == "too_many" or too_many:
                self.send("400 Too many connections from your account")
                return
            if st.mode == "greeting_502":
                self.send("502 Access denied")
                return
            self.send("200 MSO mock NNTP ready (posting ok)")
            authed = False
            user = None
            group: str | None = None
            while True:
                raw = self.rfile.readline(4096)
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                parts = line.split()
                if not parts:
                    self.send("500 What?")
                    continue
                cmd = parts[0].upper()
                with st.lock:
                    st.commands.append(
                        "AUTHINFO PASS ***" if line.upper().startswith("AUTHINFO PASS") else line
                    )
                if cmd == "QUIT":
                    self.send("205 Bye")
                    return
                if cmd == "CAPABILITIES":
                    caps = ["VERSION 2", "READER", "OVER"] + ([] if authed else ["AUTHINFO USER"])
                    self.send_long("101 Capability list:", caps)
                    continue
                if cmd == "DATE":
                    self.send("111 " + datetime.now(UTC).strftime("%Y%m%d%H%M%S"))
                    continue
                if cmd == "AUTHINFO" and len(parts) >= 3 and parts[1].upper() == "USER":
                    user = parts[2]
                    self.send("381 Password required")
                    continue
                if cmd == "AUTHINFO" and len(parts) >= 3 and parts[1].upper() == "PASS":
                    if user is None:
                        self.send("482 Authentication commands issued out of sequence")
                        continue
                    if st.mode == "expired":
                        self.send("481 Account expired, please renew your subscription")
                        continue
                    if st.mode == "block_exhausted":
                        self.send("502 Block exhausted - no data remaining on this account")
                        continue
                    if st.mode == "auth_denied" or user != st.username or " ".join(parts[2:]) != st.password:
                        self.send("481 Authentication failed")
                        continue
                    authed = True
                    self.send("281 Authentication accepted")
                    continue
                if not authed:
                    self.send("480 Authentication required")
                    continue
                if cmd == "GROUP" and len(parts) == 2:
                    posts = st.groups.get(parts[1])
                    if posts is None:
                        self.send("411 No such group")
                        continue
                    group = parts[1]
                    n = len(posts)
                    self.send(f"211 {n} {1 if n else 0} {n} {group}")
                    continue
                if cmd in ("STAT", "HEAD") and len(parts) == 2:
                    mid = parts[1].strip("<>")
                    known = mid in st.articles or any(p[2] == mid for ps in st.groups.values() for p in ps)
                    if not known:
                        self.send("430 No such article")
                        continue
                    if cmd == "STAT":
                        self.send(f"223 0 <{mid}>")
                    else:
                        self.send_long(
                            f"221 0 <{mid}>",
                            [f"Message-ID: <{mid}>", "Subject: test", "Newsgroups: alt.binaries.mso.test"],
                        )
                    continue
                if cmd in ("OVER", "XOVER"):
                    if group is None:
                        self.send("412 No newsgroup selected")
                        continue
                    posts = st.groups[group]
                    rng = parts[1] if len(parts) > 1 else "1-"
                    a_s, _, b_s = rng.partition("-")
                    a = int(a_s or 1)
                    b = int(b_s) if b_s else len(posts)
                    sel = [(i, p) for i, p in enumerate(posts, start=1) if a <= i <= b]
                    if not sel:
                        self.send("423 No articles in that range")
                        continue
                    self.send_long(
                        "224 Overview information follows",
                        [
                            f"{i}\t{p[0]}\tposter@example.invalid\t{p[1]}\t<{p[2]}>\t\t{p[3]}\t{p[3] // 128}"
                            for i, p in sel
                        ],
                    )
                    continue
                self.send("500 Unknown command")
        except (ConnectionError, OSError):
            return
        finally:
            with st.lock:
                st.active -= 1


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], state: _State) -> None:
        self.state = state
        super().__init__(addr, _Handler)


class FakeNNTP:
    def __init__(self, server: _Server) -> None:
        self.server = server
        self.host, self.port = server.server_address[0], server.server_address[1]
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def state(self) -> _State:
        return self.server.state

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start(host: str = "127.0.0.1", port: int = 0, **faults: Any) -> FakeNNTP:
    """Start on a free port (port=0) in a background thread."""
    return FakeNNTP(_Server((host, port), _State(**faults)))


def serve(host: str, port: int, **faults: Any) -> None:
    if "articles" in faults and isinstance(faults["articles"], str):
        faults["articles"] = [a for a in faults["articles"].split(",") if a]
    server = _Server((host, port), _State(**faults))
    print(f"mock NNTP op {host}:{port} (mode={server.state.mode})", flush=True)  # noqa: T201
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = ["FakeNNTP", "default_group", "serve", "start"]
