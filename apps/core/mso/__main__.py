"""Entrypoints: ``python -m mso api|worker|migrate|backup|restore-check``."""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
import time

from mso.config import get_settings
from mso.security import configure_logging

LOGGER = logging.getLogger("mso")


def _api(args: argparse.Namespace) -> None:
    import uvicorn

    from mso.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True,
                forwarded_allow_ips=args.forwarded_allow_ips, log_level="info", access_log=False)


def _worker(_: argparse.Namespace) -> None:
    from mso import jobs, state
    from mso.app import init_runtime

    db = init_runtime()
    worker = jobs.Worker(f"{get_settings().node_id}-{socket.gethostname()}", db, get_settings().worker_poll_s)

    def heartbeat() -> None:
        while not worker.stop_event.is_set():
            try:
                state.set_state("worker_heartbeat", {"worker": worker.worker_id, "ts": time.time()}, db)
            except Exception:
                LOGGER.warning("hartslag schrijven mislukt")
            worker.stop_event.wait(20)

    def stop(*_: object) -> None:
        LOGGER.info("worker stopt (signaal ontvangen); lopende job wordt na herstart hervat")
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=heartbeat, daemon=True).start()
    LOGGER.info("worker %s gestart", worker.worker_id)
    worker.run_forever()


def _say(msg: str) -> None:
    print(f"[mso] {msg}", flush=True)


def _migrate(args: argparse.Namespace) -> None:
    from mso import backups
    from mso.db import Database, migrate_down, migrate_up, schema_version, set_db

    db = Database(get_settings().database_url, max_size=2)
    set_db(db)
    if args.down is not None:
        print("teruggedraaid:", migrate_down(db, args.down))
    else:
        if not args.no_backup:
            # safety net before the schema changes (compose 'migrate' service; the all-in-one supervisor makes it
            # as a separate step and passes --no-backup). Never blocks the upgrade.
            backups.pre_upgrade_backup(db, target=args.to, log=_say)
        print("toegepast:", migrate_up(db, args.to))
        try:
            backups.flush_pre_upgrade(db)
        except Exception as exc:  # the audit row is a nicety; the migration itself succeeded
            LOGGER.warning("audit van de back-up vóór de upgrade niet geschreven: %s", type(exc).__name__)
    print("schemaversie:", schema_version(db))


def _backup(args: argparse.Namespace) -> None:
    from mso import backups

    if args.pre_upgrade:
        # runs BEFORE the migrations, against the old schema: no init_runtime (that would migrate first)
        from mso.db import Database, set_db

        db = Database(get_settings().database_url, max_size=2)
        set_db(db)
        try:
            res = backups.pre_upgrade_backup(db, log=_say)
            if res["status"] in ("not_needed", "exists"):
                _say(f"back-up vóór upgrade: niet nodig ({res.get('reason')})")
        finally:
            db.close()
        return  # never a non-zero exit: a missing pre-upgrade backup must not block the start
    from mso.app import init_runtime

    init_runtime()
    row = backups.create_backup(include_secrets=args.include_secrets, kind="manual", actor="cli")
    print(row["file"])


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    p = argparse.ArgumentParser(prog="mso")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("api")
    a.add_argument("--host", default="0.0.0.0")
    a.add_argument("--port", type=int, default=8200)
    a.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    a.set_defaults(fn=_api)
    sub.add_parser("worker").set_defaults(fn=_worker)
    m = sub.add_parser("migrate")
    m.add_argument("--to", type=int, default=None)
    m.add_argument("--down", type=int, default=None, help="draai terug tot en met deze versie")
    m.add_argument("--no-backup", action="store_true", help="geen automatische back-up vóór openstaande migraties")
    m.set_defaults(fn=_migrate)
    b = sub.add_parser("backup")
    b.add_argument("--include-secrets", action="store_true")
    b.add_argument("--pre-upgrade", action="store_true",
                   help="alleen als er openstaande migraties zijn: back-up vóór de upgrade (faalt nooit hard)")
    b.set_defaults(fn=_backup)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
