"""Bewaker weigert een nieuwer schema zonder gewone diensten of een herstartlus te starten."""

from __future__ import annotations

import http.client
import importlib.util
import runpy
import signal
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from mso import allinone_privileges
from mso.db import NEWER_DATABASE_EXIT_CODE

DEPLOY = Path(__file__).resolve().parents[3] / "deploy" / "allinone"


@pytest.fixture()
def supervisor(monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location("schema_supervisor", DEPLOY / "supervisor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_fail = module.fail
    trace: list[str] = []
    handlers: dict = {}
    stopped = threading.Event()
    codes = {"backup": 0, "migrate": 0, "api": 0}

    class Child:
        def __init__(self, name: str, *_: Any, **__: Any) -> None:
            self.name = name
            self.proc = SimpleNamespace(returncode=0, poll=lambda: None if self.running else self.proc.returncode)
            self.running = False

        def start(self) -> None:
            trace.append("start " + self.name)
            self.proc.returncode = codes.get(self.name, 0)
            self.running = self.proc.returncode == 0
            if self.name == "web" and codes.get("stop_after_start"):
                module.STOP.set()

        def alive(self) -> bool:
            return self.running

        def stop(self, *_: Any) -> None:
            trace.append("stop " + self.name)
            self.running = False
            if self.name == "database":
                stopped.set()

    def fail(message: str, **_: Any) -> None:
        raise RuntimeError(message)

    def run_once(_name: str, cmd: list[str]) -> int:
        command = cmd[3]
        trace.append(command)
        return codes[command]

    for name in ("prepare_dirs", "demote", "init_database", "wait_for_db", "check_schema_compatibility"):
        monkeypatch.setattr(module, name, lambda *_, **__: None)
    monkeypatch.setattr(module, "Database", lambda *_a, **_k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, "Child", Child)
    monkeypatch.setattr(module, "run_once", run_once)
    monkeypatch.setattr(module, "fail", fail)
    monkeypatch.setattr(module.signal, "signal", lambda signum, fn: handlers.update({signum: fn}))
    yield SimpleNamespace(module=module, trace=trace, handlers=handlers, codes=codes,
                          stopped=stopped, real_fail=real_fail)
    module.STOP.set()


@pytest.mark.parametrize("stage", ["backup", "migrate", "api", "worker", "web"])
@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_supervisor_holds_on_newer_schema_without_restarting(supervisor: Any, capsys: Any,
                                                            stage: str, stop_signal: int) -> None:
    supervisor.codes[stage] = NEWER_DATABASE_EXIT_CODE
    errors: list[Exception] = []
    done = threading.Event()

    def main() -> None:
        try:
            supervisor.module.main()
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=main)
    thread.start()
    try:
        assert supervisor.stopped.wait(2), supervisor.trace
        # Het proces leeft en wacht; de database en gewone diensten zijn gestopt.
        assert not done.wait(0.1), errors
        expected = ["start database", "backup"]
        if stage != "backup":
            expected.append("migrate")
        if stage in ("api", "worker", "web"):
            expected += ["start api", "start worker", "start web", "stop web", "stop worker", "stop api"]
        assert supervisor.trace == [*expected, "stop database"]
        out = capsys.readouterr().out
        assert out.count("database is van een nieuwere MSO-versie; zet de bijpassende snapshot/back-up terug") == 1
        assert "herstart over" not in out and "start gewoon door" not in out
        supervisor.handlers[stop_signal](stop_signal, None)
        assert done.wait(2)
        assert errors == []
    finally:
        supervisor.module.STOP.set()
        thread.join(3)
        assert not thread.is_alive()


def test_supervisor_starts_normally_after_compatible_schema(supervisor: Any) -> None:
    supervisor.codes.update(stop_after_start=1)
    supervisor.module.main()
    assert supervisor.trace == ["start database", "backup", "migrate", "start api", "start worker", "start web",
                                "stop web", "stop worker", "stop api", "stop database"]


def test_supervisor_stopt_voor_migratie_bij_dumpfout(supervisor: Any) -> None:
    supervisor.codes.update(backup=1, stop_after_start=1)
    with pytest.raises(RuntimeError, match="Back-up vóór upgrade mislukt"):
        supervisor.module.main()
    assert supervisor.trace == ["start database", "backup", "stop database"]


def test_supervisor_keeps_other_migration_errors_separate(supervisor: Any) -> None:
    supervisor.codes["migrate"] = 42
    with pytest.raises(RuntimeError, match="databasemigratie mislukt"):
        supervisor.module.main()
    assert supervisor.trace == ["start database", "backup", "migrate", "stop database"]


def test_waiting_supervisor_is_not_application_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    checked = []

    def unavailable(_self: Any, method: str, path: str) -> None:
        checked.append((method, path))
        raise OSError("API en web zijn niet gestart")

    monkeypatch.setattr(http.client.HTTPConnection, "request", unavailable)
    main = runpy.run_path(str(DEPLOY / "healthcheck.py"))["main"]
    monkeypatch.setitem(main.__globals__, "lower_privileges", lambda *_: None)
    assert main([]) == 1
    assert checked == [("GET", "/healthz")]


@pytest.mark.parametrize("identity", ["0", "verkeerd", "568"])
def test_healthcheck_stops_before_http_if_privileges_cannot_be_dropped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], identity: str,
) -> None:
    def unexpected_http(*_: Any, **__: Any) -> Any:
        pytest.fail("HTTP-controle vóór een geslaagde rechtenwissel")

    def denied(*_: Any) -> None:
        assert identity == "568"
        raise PermissionError("synthetische rechtenweigering")

    monkeypatch.setenv("MSO_UID", identity)
    monkeypatch.setattr(http.client, "HTTPConnection", unexpected_http)
    main = runpy.run_path(str(DEPLOY / "healthcheck.py"))["main"]
    monkeypatch.setitem(main.__globals__, "lower_privileges", denied)
    assert main([]) == 1
    assert "rechtenwissel" in capsys.readouterr().err


@pytest.mark.parametrize(("field", "value"), [("Uid", "0 0 0 0"), ("CapEff", "0000000000000001")])
def test_healthcheck_refuses_an_unexpected_privilege_profile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], field: str, value: str,
) -> None:
    def unexpected_http(*_: Any, **__: Any) -> Any:
        pytest.fail("HTTP-controle met een onveilig rechtenprofiel")

    for name in ("MSO_UID", "MSO_GID"):
        monkeypatch.setenv(name, "568")
    status = {
        "Uid": "568 568 568 568",
        "Gid": "568 568 568 568",
        "NoNewPrivs": "1",
        **dict.fromkeys(("CapEff", "CapPrm", "CapInh", "CapAmb"), "0000000000000000"),
    }
    status[field] = value
    monkeypatch.setattr(allinone_privileges.os, "geteuid", lambda: 568)
    monkeypatch.setattr(allinone_privileges, "_set_process_control", lambda *_: None)
    monkeypatch.setattr(allinone_privileges, "process_status", lambda: status)
    monkeypatch.setattr(http.client, "HTTPConnection", unexpected_http)
    main = runpy.run_path(str(DEPLOY / "healthcheck.py"))["main"]
    assert main([]) == 1
    assert "rechtenwissel" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["open", "check", "close"])
@pytest.mark.parametrize("error", [psycopg.OperationalError, RuntimeError])
def test_supervisor_preflight_error_stops_database_and_delays_exit(
    supervisor: Any, monkeypatch: pytest.MonkeyPatch, stage: str, error: type[Exception],
) -> None:
    """Elke andere fout in de voorcontrole volgt de bestaande nette stop met pauze en code 78."""
    module, trace = supervisor.module, supervisor.trace
    monkeypatch.setattr(module.os, "geteuid", lambda: 568)

    def broken() -> None:
        raise error("synthetische databasefout")

    def close() -> None:
        trace.append("db-close")
        if stage == "close":
            broken()

    def database(*_: Any, **__: Any) -> Any:
        if stage == "open":
            broken()
        return SimpleNamespace(close=close)

    def check(_: Any) -> None:
        if stage == "check":
            broken()

    monkeypatch.setattr(module, "Database", database)
    monkeypatch.setattr(module, "check_schema_compatibility", check)
    monkeypatch.setattr(module, "fail", supervisor.real_fail)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: trace.append(f"slaap-{seconds}"))
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 78
    expected = ["start database"]
    if stage != "open":
        expected.append("db-close")
    assert trace == [*expected, "stop database", "slaap-30"]


def test_supervisor_568_weigert_map_pas_na_rechtencontrole(
    supervisor: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, trace = supervisor.module, supervisor.trace
    monkeypatch.setattr(module.os, "geteuid", lambda: 568)
    monkeypatch.setattr(module, "demote", lambda: trace.append("rechten geverifieerd"))

    def deny(**_: Any) -> None:
        assert trace == ["rechten geverifieerd"]
        raise PermissionError("synthetische niet-schrijfbare map")

    monkeypatch.setattr(module, "prepare_dirs", deny)
    with pytest.raises(RuntimeError, match="een start- of stopopdracht is mislukt"):
        module.main()
    assert trace == ["rechten geverifieerd"]


@pytest.mark.parametrize(
    ("stage", "safe"),
    [("laatste rechtenwissel", False), ("subprocess vóór wissel", False), ("subprocess na wissel", True)],
)
def test_supervisor_ruimt_op_zonder_bevoorrechte_foutpauze(
    supervisor: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stage: str, safe: bool,
) -> None:
    module, trace = supervisor.module, supervisor.trace
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "fail", supervisor.real_fail)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: trace.append(f"slaap-{seconds}"))

    def deny_drop() -> None:
        raise PermissionError("synthetische rechtenweigering")

    def deny_subprocess(*_: Any) -> None:
        raise module.subprocess.SubprocessError("synthetische weigering in preexec_fn")

    if stage == "subprocess vóór wissel":
        monkeypatch.setattr(module, "wait_for_db", deny_subprocess)
    elif safe:
        monkeypatch.setattr(module, "run_once", deny_subprocess)
    else:
        monkeypatch.setattr(module, "demote", deny_drop)
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 78
    assert trace == ["start database", "stop database"] + (["slaap-30"] if safe else [])
    assert "[mso] FOUT:" in capsys.readouterr().out
