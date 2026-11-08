"""1.6.0 WP-E2 (C29, C36-part): daily retention (jobs, observations → hourly rollup, logs, VACUUM), lighter
backups (refetchable data excluded + resync after restore), safety-backup pruning, free-space check with a
"Wacht op jou" item, and the backup before pending migrations (also via the CLI)."""

from __future__ import annotations

import subprocess
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mso import backups, jobs, retention
from mso import settings_registry as sr
from mso.config import get_settings

Usage = namedtuple("Usage", "total used free")


def _job(db: Any, kind: str, status: str, age_days: float) -> int:
    return int(db.scalar(
        "INSERT INTO jobs(kind,status,created_at,updated_at) VALUES (%s,%s,now() - make_interval(secs => %s),"
        "now() - make_interval(secs => %s)) RETURNING id", (kind, status, age_days * 86400, age_days * 86400)))


def _obs(db: Any, ts: datetime, value: float | None, subject: str = "qbittorrent",
         metric: str = "torrent.dl_speed_active", labels: str = "{}") -> None:
    db.execute("INSERT INTO observations(subject,metric,value,labels,ts) VALUES (%s,%s,%s,%s::jsonb,%s)",
               (subject, metric, value, labels, ts))


# ------------------------------------------------------------------ retention
def test_jobs_retention_keeps_newest_per_kind_and_recent(app: Any, db: Any) -> None:
    sr.put({"retention.jobs_keep_per_kind": 5}, "test")
    old_a = [_job(db, "test.a", "done", 20 + i / 100) for i in range(8)]  # 8 old finished runs
    old_b = [_job(db, "test.b", "cancelled", 30)]
    recent = _job(db, "test.a", "done", 2)
    failed_old, failed_new = _job(db, "test.a", "failed", 61), _job(db, "test.a", "failed", 59)
    queued = _job(db, "test.a", "queued", 100)
    running = _job(db, "test.c", "running", 100)
    res = retention.purge_jobs(db)
    left = {r["id"] for r in db.fetchall("SELECT id FROM jobs")}
    # newest 5 finished 'test.a' (by id) stay: the recent one + the 4 newest old ones
    assert recent in left and set(old_a[-4:]) <= left and not (set(old_a[:4]) & left)
    assert old_b[0] in left  # fewer than 5 of this kind: kept
    assert failed_new in left and failed_old not in left
    assert queued in left and running in left  # never touches unfinished jobs
    assert res == {"jobs_done": 4, "jobs_failed": 1}


def test_observations_rollup_hourly_and_idempotent(app: Any, db: Any) -> None:
    now = datetime.now(UTC)
    old_hour = (now - timedelta(days=40)).replace(minute=0, second=0, microsecond=0)
    for minute, v in ((1, 10.0), (20, 30.0), (40, 20.0), (50, None)):
        _obs(db, old_hour + timedelta(minutes=minute), v)
    _obs(db, old_hour + timedelta(minutes=5), 1.0, labels='{"server": "eweka"}')
    _obs(db, now - timedelta(days=10), 99.0)
    res = retention.rollup_observations(db)
    assert res["observations_raw"] == 5 and res["observations_hourly_written"] == 2
    assert db.scalar("SELECT count(*) FROM observations") == 1  # the recent one stays raw
    h = db.fetchone("SELECT * FROM observations_hourly WHERE labels='{}'::jsonb")
    assert h["hour"] == old_hour and h["n"] == 4 and h["n_values"] == 3
    assert (h["min"], h["max"], h["avg"], h["median"], h["sum"]) == (10.0, 30.0, 20.0, 20.0, 60.0)
    assert db.scalar("SELECT n FROM observations_hourly WHERE labels->>'server'='eweka'") == 1
    # a straggler in an already rolled-up hour is merged, never duplicated
    _obs(db, old_hour + timedelta(minutes=55), 40.0)
    retention.rollup_observations(db)
    h = db.fetchone("SELECT * FROM observations_hourly WHERE labels='{}'::jsonb")
    assert db.scalar("SELECT count(*) FROM observations_hourly") == 2
    assert (h["n"], h["n_values"], h["max"], h["sum"], h["avg"]) == (5, 4, 40.0, 100.0, 25.0)
    assert retention.rollup_observations(db)["observations_raw"] == 0
    # hourly summaries older than 400 days go
    db.execute("INSERT INTO observations_hourly(hour,subject,metric,n) VALUES (now() - interval '401 days','x','y',1)")
    assert retention.rollup_observations(db)["observations_hourly"] == 1


def test_raw_window_extends_to_the_longest_reader(app: Any, db: Any) -> None:
    if "automation.evaluation_baseline_days" not in sr.REGISTRY:
        pytest.skip("automatiseringsmodule niet geladen")
    raw = retention.raw_retention(db)
    assert raw["configured_days"] == 30 and raw["effective_days"] == 30 and not raw["extended"]
    sr.put({"automation.evaluation_baseline_days": 60}, "test")
    raw = retention.raw_retention(db)
    assert raw["effective_days"] == 68 and raw["extended"]  # 7 d evaluation period + 60 d baseline + 1 d margin
    _obs(db, datetime.now(UTC) - timedelta(days=40), 5.0)
    assert retention.rollup_observations(db)["observations_raw"] == 0  # an evaluation may still need it


def test_retention_job_cleans_logs_vacuums_and_never_touches_audit(app: Any, db: Any, worker: Any) -> None:
    db.execute("INSERT INTO automation_log(kind,result,ts) VALUES ('run','ok',now() - interval '181 days'), "
               "('run','ok',now() - interval '10 days')")
    db.execute("INSERT INTO login_attempts(username,remote,ok,ts) VALUES ('a','1.2.3.4',false,now() - interval "
               "'91 days'), ('a','1.2.3.4',true,now())")
    db.execute("INSERT INTO audit_log(actor,action,summary,ts) VALUES ('x','oud.bewijs','oud',now() - interval "
               "'5 years')")
    _job(db, "test.a", "failed", 90)
    j = jobs.enqueue("system.retention", {}, max_attempts=1)
    worker.drain()
    row = db.fetchone("SELECT * FROM jobs WHERE id=%s", (j["id"],))
    assert row["status"] == "done", row["error"]
    res = row["result"]
    assert res["deleted"]["automation_log"] == 1 and res["deleted"]["login_attempts"] == 1
    assert res["deleted"]["jobs_failed"] == 1
    assert "jobs" in res["vacuumed"] and "automation_log" in res["vacuumed"]
    assert "opgeruimd" in row["summary"]
    assert db.scalar("SELECT count(*) FROM automation_log") == 1
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='oud.bewijs'") == 1
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='system.retention'") == 1
    from mso import state

    assert state.get_state(retention.STATE_KEY)["removed_total"] == res["removed_total"]
    # disabled → the schedule does not run
    sr.put({"retention.enabled": False}, "test")
    sched = next(s for s in jobs.SCHEDULES if s.kind == "system.retention")
    assert sched.enabled() is False


def test_maintenance_api(viewer: Any, operator: Any, admin: Any, db: Any) -> None:
    r = viewer.get("/api/v1/system/maintenance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retention"]["raw"]["effective_days"] >= 30
    assert body["database"]["size_bytes"] > 0 and body["database"]["tables"]
    assert body["backups"]["space"]["needed_bytes"] > 0 and "release_cache" in body["backups"]["excluded_tables"]
    assert viewer.post("/api/v1/system/maintenance/run").status_code == 403
    assert operator.post("/api/v1/system/maintenance/run").status_code == 403
    r = admin.post("/api/v1/system/maintenance/run")
    assert r.status_code == 200 and db.scalar("SELECT kind FROM jobs WHERE id=%s", (r.json()["job_id"],)) == \
        "system.retention"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='system.retention_requested'") == 1
    retention_defs = [d for d in viewer.get("/api/v1/settings").json()["definitions"]
                      if d["key"].startswith("retention.")]
    assert retention_defs and all(d["category"] == "systeem" for d in retention_defs)


# ------------------------------------------------------------------ backups
def test_backup_excludes_refetchable_data_and_resyncs_after_restore(app: Any, db: Any) -> None:
    db.execute("INSERT INTO release_cache(token,release_key,source_id,protocol,title,expires_at) "
               "VALUES ('t1','k','s','usenet','Titel',now() + interval '1 day')")
    db.execute("INSERT INTO mg_snapshots(key,data) VALUES ('probe','{}')")
    db.execute("INSERT INTO torrent_samples(hash) VALUES ('abc')")
    db.execute("INSERT INTO usenet_hi_groups(name,last_article) VALUES ('alt.binaries.test', 12345)")
    db.execute("INSERT INTO settings(key,value) VALUES ('backup.keep','7')")
    b = backups.create_backup(actor="test")
    listing = subprocess.run([backups._bin("pg_restore"), "--list", str(get_settings().backup_dir / b["file"])],
                             capture_output=True, text=True, check=True).stdout
    data_entries = {line.split()[-2] for line in listing.splitlines() if " TABLE DATA " in line}
    assert "settings" in data_entries and "jobs" in data_entries
    assert not data_entries & {"release_cache", "mg_snapshots", "mg_wanted_cache", "torrent_samples",
                               "usenet_hi_headers", "torrent_client_snapshot", "sessions", "secrets"}
    assert backups.AUDIT_KEEP_TABLE not in listing
    res = backups.restore_backup(b["id"], "test")
    assert db.scalar("SELECT count(*) FROM release_cache") == 0 and db.scalar("SELECT count(*) FROM settings") == 1
    assert db.scalar("SELECT last_article FROM usenet_hi_groups") is None  # header index starts fresh
    expected = [k for k, _ in backups.RESYNC_JOBS if k in jobs.HANDLERS]
    assert res["resync"] == expected
    for kind in expected:
        assert db.scalar("SELECT status FROM jobs WHERE kind=%s ORDER BY id DESC LIMIT 1", (kind,)) == "queued"
    assert not db.scalar("SELECT to_regclass('public.mso_restore_audit_keep') IS NOT NULL")


def test_low_disk_space_skips_dump_with_waiting_item(app: Any, db: Any, worker: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from mso.features.overview.autopilot import waiting

    monkeypatch.setattr(backups.shutil, "disk_usage", lambda _p: Usage(10 * 1024**3, 9 * 1024**3, 100 * 1024**2))
    before = set(get_settings().backup_dir.glob("*.dump")) if get_settings().backup_dir.exists() else set()
    with pytest.raises(backups.BackupSkipped, match="Te weinig vrije schijfruimte"):
        backups.create_backup(actor="test")
    assert (set(get_settings().backup_dir.glob("*.dump")) if get_settings().backup_dir.exists() else set()) == before
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key='backup.space'")
    assert rec["status"] == "open" and rec["risk"] == "toestemming_nodig" and "GB" in rec["explanation"]
    assert any(i["id"] == f"rec:{rec['id']}" for i in waiting(db)["items"])
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='backup.skipped'") == 1
    # the daily job: skipped (done) with a clear summary; a manual one fails with the reason
    auto = jobs.enqueue("backup.create", {"kind": "auto", "actor": "scheduler"}, max_attempts=1)
    manual = jobs.enqueue("backup.create", {"actor": "beheerder"}, max_attempts=1)
    worker.drain()
    a = db.fetchone("SELECT * FROM jobs WHERE id=%s", (auto["id"],))
    m = db.fetchone("SELECT * FROM jobs WHERE id=%s", (manual["id"],))
    assert a["status"] == "done" and "Overgeslagen" in a["summary"] and "skipped" in a["result"]
    assert m["status"] == "failed" and "Te weinig vrije schijfruimte" in m["error"]
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key='backup.space'") == 1
    # a restore without room for its safety backup is refused (database untouched)
    db.execute("INSERT INTO backups(file,kind,size_bytes) VALUES ('bestaat.dump','manual',1)")
    monkeypatch.undo()
    b = backups.create_backup(actor="test")  # room again: the item resolves itself
    assert db.scalar("SELECT status FROM recommendations WHERE dedupe_key='backup.space'") == "resolved"
    monkeypatch.setattr(backups.shutil, "disk_usage", lambda _p: Usage(10 * 1024**3, 9 * 1024**3, 100 * 1024**2))
    with pytest.raises(backups.BackupError, match="Herstel geweigerd"):
        backups.restore_backup(b["id"], "test")


def test_space_status_basis(app: Any, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backups.shutil, "disk_usage", lambda _p: Usage(100 * 1024**3, 0, 50 * 1024**3))
    st = backups.space_status(db)
    assert st["reference"] == "databasegrootte" and st["ok"]
    db.execute("INSERT INTO backups(file,kind,size_bytes) VALUES ('x.dump','auto',%s)", (3 * 1024**3,))
    st = backups.space_status(db)
    assert st["reference"] == "vorige back-up" and st["needed_bytes"] == 6 * 1024**3 + st["reserve_bytes"]


def test_safety_backups_are_pruned(app: Any, db: Any) -> None:
    bdir = get_settings().backup_dir
    bdir.mkdir(parents=True, exist_ok=True)

    def add(kind: str, age_days: int, name: str) -> None:
        (bdir / name).write_bytes(b"x")
        (bdir / name).with_suffix(".sha256").write_text("x")
        db.execute("INSERT INTO backups(file,kind,size_bytes,created_at) VALUES (%s,%s,1,now() - make_interval("
                   "days => %s))", (name, kind, age_days))

    for i, age in enumerate((50, 45, 40, 35)):
        add("pre-restore", age, f"pr-{i}.dump")
    for i in range(5):
        add("pre-upgrade", 100 - i, f"pu-{i}.dump")
    assert backups.prune_safety(db) == 2 + 2
    left = {r["file"] for r in db.fetchall("SELECT file FROM backups")}
    # pre-restore: all older than 30 days, but the newest 2 always stay
    assert {"pr-2.dump", "pr-3.dump"} <= left and not ({"pr-0.dump", "pr-1.dump"} & left)
    assert {"pu-2.dump", "pu-3.dump", "pu-4.dump"} <= left and not ({"pu-0.dump", "pu-1.dump"} & left)
    assert not (bdir / "pr-0.dump").exists() and not (bdir / "pr-0.sha256").exists()
    add("pre-restore", 3, "pr-new.dump")
    backups.prune_safety(db)
    assert {"pr-new.dump", "pr-3.dump"} <= {r["file"] for r in db.fetchall("SELECT file FROM backups")}
    assert "pr-2.dump" not in {r["file"] for r in db.fetchall("SELECT file FROM backups")}


# ------------------------------------------------------------------ backup before an upgrade
@pytest.fixture()
def old_db(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A separate database at schema 0600 (1.6.0 base), as if MSO is about to be upgraded."""
    import psycopg

    from mso.db import Database, migrate_up

    name = database_url.rsplit("/", 1)[1] + "_upg"
    base = database_url.rsplit("/", 1)[0]
    with psycopg.connect(base + "/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    url = base + "/" + name
    db = Database(url, max_size=3)
    migrate_up(db, 600)
    db.execute("INSERT INTO settings(key,value) VALUES ('backup.keep','14')")
    monkeypatch.setattr(get_settings(), "database_url", url)
    yield db
    db.close()
    with psycopg.connect(base + "/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def swap_db(app: Any) -> Any:
    from mso.db import get_db, set_db

    original = get_db()
    yield set_db
    set_db(original)


def test_pre_upgrade_backup_before_pending_migrations(app: Any, old_db: Any, swap_db: Any) -> None:
    from mso.db import migrate_up, schema_version

    lines: list[str] = []
    res = backups.pre_upgrade_backup(old_db, log=lines.append)
    assert res["status"] == "created", res
    row = old_db.fetchone("SELECT * FROM backups WHERE kind='pre-upgrade'")
    assert row["file"] == res["file"] and row["schema_version"] == 600
    assert (get_settings().backup_dir / row["file"]).exists()
    assert lines and "back-up vóór upgrade gemaakt" in lines[0]
    # api + worker + `mso migrate` starting together: only one dump per upgrade
    assert backups.pre_upgrade_backup(old_db)["status"] == "exists"
    migrate_up(old_db)
    assert schema_version(old_db) > 600
    swap_db(old_db)
    v = backups.flush_pre_upgrade(old_db)
    assert v["status"] == "created" and v["from"] == 600
    audit_row = old_db.fetchone("SELECT * FROM audit_log WHERE action='backup.pre_upgrade'")
    assert audit_row and row["file"] in audit_row["summary"]
    assert backups.flush_pre_upgrade(old_db) is None  # once
    assert backups.pre_upgrade_backup(old_db)["status"] == "not_needed"


def test_pre_upgrade_backup_skips_or_fails_but_never_blocks(app: Any, old_db: Any, swap_db: Any,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from mso.db import migrate_up

    monkeypatch.setattr(backups.shutil, "disk_usage", lambda _p: Usage(10 * 1024**3, 0, 1024))
    res = backups.pre_upgrade_backup(old_db, log=lambda _m: None)
    assert res["status"] == "skipped" and "Te weinig vrije schijfruimte" in res["reason"]
    assert not old_db.scalar("SELECT count(*) FROM backups")
    migrate_up(old_db)
    swap_db(old_db)
    backups.flush_pre_upgrade(old_db)
    assert old_db.scalar("SELECT count(*) FROM audit_log WHERE action='backup.pre_upgrade_skipped'") == 1
    assert old_db.scalar("SELECT status FROM recommendations WHERE dedupe_key='backup.space'") == "open"


def test_pre_upgrade_backup_failure_is_a_log_line(app: Any, old_db: Any, monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
    monkeypatch.setenv("MSO_PG_BIN", str(tmp_path / "bestaat-niet"))
    lines: list[str] = []
    res = backups.pre_upgrade_backup(old_db, log=lines.append)
    assert res["status"] == "failed" and "MSO start gewoon door" in lines[-1]
    assert not old_db.scalar("SELECT count(*) FROM backups")
    assert old_db.fetchone("SELECT value FROM system_state WHERE key=%s", (backups.PRE_UPGRADE_STATE,))


def test_pre_upgrade_keeps_newest_three(app: Any, old_db: Any) -> None:
    for i in range(4):
        old_db.execute("INSERT INTO backups(file,kind,schema_version,created_at) VALUES (%s,'pre-upgrade',500,"
                       "now() - make_interval(days => %s))", (f"oud-{i}.dump", 10 + i))
    assert backups.pre_upgrade_backup(old_db, log=lambda _m: None)["status"] == "created"
    files = [r["file"] for r in old_db.fetchall("SELECT file FROM backups WHERE kind='pre-upgrade' "
                                                 "ORDER BY created_at DESC")]
    assert len(files) == 3 and files[1:] == ["oud-0.dump", "oud-1.dump"]


def test_fresh_install_needs_no_pre_upgrade_backup(database_url: str, app: Any) -> None:
    import psycopg

    from mso.db import Database

    name = database_url.rsplit("/", 1)[1] + "_fresh"
    base = database_url.rsplit("/", 1)[0]
    with psycopg.connect(base + "/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    db = Database(base + "/" + name, max_size=2)
    try:
        assert backups.pre_upgrade_backup(db)["status"] == "not_needed"
    finally:
        db.close()
        with psycopg.connect(base + "/postgres", autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_cli_pre_upgrade_and_migrate(app: Any, old_db: Any, swap_db: Any, capsys: pytest.CaptureFixture[str]) -> None:
    from mso.__main__ import main

    main(["backup", "--pre-upgrade"])
    out = capsys.readouterr().out
    assert "back-up vóór upgrade gemaakt" in out
    assert old_db.scalar("SELECT count(*) FROM backups WHERE kind='pre-upgrade'") == 1
    main(["migrate", "--no-backup"])
    out = capsys.readouterr().out
    assert "toegepast:" in out
    assert old_db.scalar("SELECT count(*) FROM audit_log WHERE action='backup.pre_upgrade'") == 1
    assert old_db.scalar("SELECT count(*) FROM backups WHERE kind='pre-upgrade'") == 1
    main(["backup", "--pre-upgrade"])
    assert "niet nodig" in capsys.readouterr().out


def test_cli_migrate_makes_its_own_pre_upgrade_backup(app: Any, old_db: Any, swap_db: Any,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    from mso.__main__ import main

    main(["migrate"])  # compose 'migrate' service: backup + migrate in one go
    out = capsys.readouterr().out
    assert "back-up vóór upgrade gemaakt" in out and "toegepast:" in out
    assert old_db.scalar("SELECT count(*) FROM audit_log WHERE action='backup.pre_upgrade'") == 1


def test_api_start_makes_pre_upgrade_backup(app: Any, old_db: Any, swap_db: Any) -> None:
    from mso.app import init_runtime
    from mso.db import schema_version
    from mso.vault import get_vault, set_vault

    vault = get_vault()
    try:
        init_runtime(old_db)
    finally:
        set_vault(vault)
    assert schema_version(old_db) > 600
    assert old_db.scalar("SELECT count(*) FROM backups WHERE kind='pre-upgrade'") == 1
    assert old_db.scalar("SELECT count(*) FROM audit_log WHERE action='backup.pre_upgrade'") == 1
