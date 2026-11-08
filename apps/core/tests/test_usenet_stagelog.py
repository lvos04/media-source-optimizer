"""1.6.0 WP-A · SABnzbd's processing log (stage_log) → facts; classification with those facts; the article counter
fix (SABnzbd's ``articles_success`` field = failed articles) incl. migration 0601. Texts are SABnzbd's own wording
(sabnzbd/nzb/object.py, postproc.py, newsunpack.py) and its Dutch translation (po/main/nl.po)."""

from __future__ import annotations

import time
from typing import Any

import pytest

SAB_KEY = "SABGEHEIM-stage-0123456789"


def _log(*stages: tuple[str, list[str]]) -> list[dict[str, Any]]:
    return [{"name": n, "actions": a} for n, a in stages]


# ------------------------------------------------------------------ parser
def test_missing_articles_and_failed_repair() -> None:
    from mso.features.usenet import stagelog

    f = stagelog.parse(
        _log(
            ("Download", ["Downloaded in 12 min at an average of 30.2 MB/s<br/>Age: 412d<br/>"
                          "312 articles were missing"]),
            ("Servers", ["Eweka=1.4 GB, XSNews=512 B, Newshosting=3.0 MB"]),
            ("Repair", ["[Show.S01E01] Verified in 3 seconds, repair is required",
                        "[Show.S01E01] Repair failed, not enough repair blocks (87 short)"]),
        ),
        "Repair failed, not enough repair blocks (87 short)",
    )
    assert f is not None
    assert f.missing_articles == 312 and f.age_days == 412 and f.has_download
    assert f.repair == "mislukt" and f.blocks_short == 87
    assert f.repair_line == "Repair failed, not enough repair blocks (87 short)"  # set name removed
    assert f.data_complete is False and f.problem == "repair_failed"
    assert f.servers == [{"name": "Eweka", "bytes": int(1.4 * 1024**3)}, {"name": "XSNews", "bytes": 512},
                         {"name": "Newshosting", "bytes": 3 * 1024**2}]
    assert stagelog.data_missing(f) is True
    assert stagelog.summary_nl(f) == "312 artikelen ontbraken · herstel mislukt (87 blokken tekort)"


def test_repaired_data_is_complete_and_the_unpack_line_is_the_cause() -> None:
    from mso.features.usenet import stagelog

    f = stagelog.parse(
        _log(
            ("Download", ["Downloaded in 3 min<br/>Age: 2d<br/>45 articles were missing"]),
            ("Repair", ["[Rel] Repaired in 12 seconds"]),
            ("Unpack", ["[Rel] Unpacking failed, archive requires a password"]),
        ),
        "Unpacking failed, archive requires a password",
    )
    assert f.repair == "gelukt" and f.data_complete is True and f.problem == "password"
    assert f.unpack == "mislukt" and f.unpack_line == "Unpacking failed, archive requires a password"
    assert stagelog.data_missing(f) is False
    assert stagelog.summary_nl(f) == (
        "Data compleet (45 ontbrekende artikelen hersteld) · uitpakken mislukt: Unpacking failed, archive requires a "
        "password"
    )


def test_seven_zip_see_logfile_only_in_fail_message() -> None:
    """7-Zip's default failure writes no Unpack line (newsunpack.seven_extract_core): the fail message is used."""
    from mso.features.usenet import stagelog

    f = stagelog.parse(_log(("Download", ["Downloaded in 1 min<br/>Age: 3d"]), ("Repair", ["[R] Quick Check OK"])),
                       "Unpacking failed, see logfile")
    assert f.data_complete is True and f.missing_articles == 0 and f.repair == "niet_nodig"
    assert f.unpack == "mislukt" and f.unpack_line == "Unpacking failed, see logfile"
    assert f.problem == "unpacker_error" and "unpacker_error" in stagelog.ENVIRONMENT


@pytest.mark.parametrize(
    "line,problem",
    [
        ("[R] Unpacking failed, write error or disk is full? Permission denied", "no_permission"),
        ("[R] Unpacking failed, write error or disk is full? the file /mnt/tank/complete/R/x.mkv", "disk_full"),
        ("[R] Unpacking failed, disk full", "disk_full"),
        ("[R] Unpacking failed, file too large for filesystem (FAT?)", "file_too_large"),
        ("[R] Unpacking failed, CRC error", "corrupt_archive"),
        ("[R] Corrupt RAR file", "corrupt_archive"),
        ("[R] Unusable RAR file", "corrupt_archive"),
        ("[R] Unpacking failed, unable to find R.part07.rar", "corrupt_archive"),
        ("Failed moving /a/b/c/R to /x/y/z/R", "move_failed"),
        ("[R] ERROR: Unknown error in unrar", "unpacker_error"),
        ("[R] Error \"[Errno 13] Permission denied: '/complete/R'\" while unpacking RAR files", "no_permission"),
        ("[R] Uitpakken mislukt, de schijf is vol", "disk_full"),
        ("[R] Uitpakken mislukt, archief vereist wachtwoord", "password"),
        ("[R] Uitpakken mislukt, CRC-fout", "corrupt_archive"),
        ("[R] Beschadigd RAR-bestand", "corrupt_archive"),
    ],
)
def test_unpack_problem_kinds(line: str, problem: str) -> None:
    from mso.features.usenet import stagelog

    f = stagelog.parse(_log(("Download", ["Downloaded in 1 min"]), ("Unpack", [line])), None)
    assert f.unpack == "mislukt" and f.problem == problem and f.data_complete is True


def test_dutch_sabnzbd_texts() -> None:
    from mso.features.usenet import stagelog

    f = stagelog.parse(
        _log(
            ("Download", ["Gedownload in 3 min met een gemiddelde snelheid van 20 MB/s<br/>Leeftijd: 9d<br/>"
                          "120 artikelen ontbreken<br/>3 artikelen zijn misvormd"]),
            ("Repair", ["[R] Reparatie mislukt, te weinig herstelblokken (14 tekort)"]),
        ),
        "Reparatie mislukt, te weinig herstelblokken (14 tekort)",
    )
    assert f.missing_articles == 120 and f.malformed_articles == 3 and f.age_days == 9
    assert f.repair == "mislukt" and f.blocks_short == 14 and f.data_complete is False
    ok = stagelog.parse(
        _log(("Repair", ["[R] Snelle Controle OK"]), ("Unpack", ["[R] 3 bestanden/mappen uitgepakt in 4 s"]))
    )
    assert ok.repair == "niet_nodig" and ok.unpack == "gelukt" and ok.data_complete is True


def test_verification_stopped_by_password_or_disk_is_not_a_data_failure() -> None:
    from mso.features.usenet import stagelog

    f = stagelog.parse(_log(("Download", ["Downloaded in 1 min"]),
                            ("Repair", ["[R] RAR-based verification failed: Passworded"])), None)
    assert f.repair is None and f.data_complete is True and "password" in f.problems
    g = stagelog.parse(_log(("Download", ["Downloaded in 1 min"]), ("Repair", ["Repairing failed, Disk full"])), None)
    assert g.repair is None and g.data_complete is True and g.problem == "disk_full"


def test_aborted_download_and_unknown_cases() -> None:
    from mso.features.usenet import stagelog

    # the abort message only survives in fail_message (the download report replaces the Download stage)
    f = stagelog.parse(_log(("Download", ["Downloaded in 1 min<br/>Age: 3000d<br/>9000 articles were missing"])),
                       "Aborted, cannot be completed - https://sabnzbd.org/not-complete")
    assert f.aborted and f.data_complete is False and f.problem == "missing_articles"
    nos = stagelog.parse(_log(("Download", ["Download failed - Not on your server(s) - https://sabnzbd.org/not-complete"])))
    assert nos.aborted and nos.data_complete is False
    assert stagelog.parse(None, "x") is None and stagelog.parse([], "x") is None
    src = stagelog.parse(_log(("Source", ["Release.nzb"])), "Something odd")
    assert src.data_complete is None and not stagelog.informative(src) and stagelog.data_missing(src) is None
    exe = stagelog.parse(_log(("Download", ["Downloaded in 1 min"])), "Aborted, unwanted extension detected")
    assert "executable" in exe.problems


def test_lines_are_redacted_and_signatures_ignore_names_and_paths() -> None:
    from mso.features.usenet import stagelog

    a = stagelog.parse(_log(("Download", ["Downloaded"]), ("Unpack", [
        "[Show.A] Unpacking failed, write error or disk is full? the file /home/jan/media/complete/Show.A/a.mkv "
        "apikey=GEHEIM123"])))
    b = stagelog.parse(_log(("Download", ["Downloaded"]), ("Unpack", [
        "[Movie.B.2019] Unpacking failed, write error or disk is full? the file /home/jan/media/complete/Movie.B/b.mkv "
        "apikey=OTHER"])))
    assert "GEHEIM123" not in a.unpack_line and "/home/jan" not in a.unpack_line and "…/a.mkv" in a.unpack_line
    assert "Show.A" not in a.unpack_line
    assert a.signature == b.signature
    c = stagelog.parse(_log(("Download", ["Downloaded"]), ("Unpack", ["[X] Unpacking failed, CRC error"])))
    assert c.signature != a.signature


# ------------------------------------------------------------------ classification with facts
def test_crc_error_with_complete_data_is_a_damaged_archive_not_missing_articles() -> None:
    from mso.features.usenet import failures, stagelog

    log = _log(("Download", ["Downloaded in 2 min<br/>Age: 5d"]), ("Repair", ["[R] Quick Check OK"]),
               ("Unpack", ["[R] Unpacking failed, CRC error"]))
    facts = stagelog.parse(log, "Unpacking failed, CRC error")
    c = failures.classify("Unpacking failed, CRC error", log, facts=facts)
    assert (c.reason, c.certainty) == ("unpack_failed", "zeker")
    assert c.evidence.startswith(
        "Data was compleet; uitpakken mislukte: Unpacking failed, CRC error (beschadigd archief)"
    )
    # without facts the 1.5.0 rule stays: CRC after repair → probably incomplete data
    assert failures.classify("Unpacking failed, CRC error").reason == "incomplete"


def test_repaired_missing_articles_do_not_make_a_job_incomplete() -> None:
    from mso.features.usenet import failures, stagelog

    log = _log(("Download", ["Downloaded<br/>312 articles were missing"]), ("Repair", ["[R] Repaired in 4 seconds"]))
    facts = stagelog.parse(log, "Unpacking failed, see logfile")
    c = failures.classify("Unpacking failed, see logfile", log, facts=facts)
    assert c.reason == "unpack_failed" and c.certainty == "zeker"
    assert c.evidence.startswith("Data was compleet (312 ontbrekende artikelen hersteld); uitpakken mislukte:")
    assert "noemt geen oorzaak" in c.evidence
    # 1.5.0 behaviour without facts: the stage text "articles were missing" made it 'incomplete'
    assert failures.classify("Unpacking failed, see logfile", log).reason == "incomplete"


@pytest.mark.parametrize(
    "msg,reason",
    [
        ("Uitpakken mislukt, archief vereist wachtwoord", "encrypted"),
        ("Afgebroken, kan niet voltooid worden - https://sabnzbd.org/not-complete", "incomplete"),
        ("Download mislukt - Niet meer op je server(s)", "incomplete"),
        ("Reparatie mislukt, te weinig herstelblokken (3 tekort)", "incomplete"),
        ("Uitpakken mislukt, de schijf is vol", "disk_full"),
        ("Uitpakken mislukt, zie logbestand", "unpack_failed"),
        ("Nabewerking is afgebroken", "client_failure"),
    ],
)
def test_dutch_fail_messages(msg: str, reason: str) -> None:
    from mso.features.usenet.failures import classify

    assert classify(msg).reason == reason


def _problems() -> list[dict[str, Any]]:
    return [{"server": "news.x.test", "displayname": "X", "status": "account_expired"}]


def test_server_context_only_for_missing_articles() -> None:
    from mso.features.usenet import failures, stagelog

    complete_log = _log(("Download", ["Downloaded"]), ("Repair", ["[R] Quick Check OK"]))
    facts = stagelog.parse(complete_log, "Unpacking failed, see logfile")
    c = failures.classify("Unpacking failed, see logfile", complete_log, facts=facts)
    assert failures.with_server_context(c, _problems(), facts).likely_cause is None

    missing_log = _log(("Download", ["Downloaded<br/>77 articles were missing"]))
    mf = stagelog.parse(missing_log, "Unpacking failed, see logfile")
    m = failures.with_server_context(failures.classify("Unpacking failed, see logfile", missing_log, facts=mf),
                                     _problems(), mf)
    assert m.likely_cause["reason"] == "account_expired" and m.evidence.startswith("77 artikelen ontbraken terwijl")

    # no stage log at all: only 'incomplete' still gets the server as likely cause
    inc = failures.classify("Aborted, cannot be completed")
    assert failures.with_server_context(inc, _problems(), None).likely_cause is not None
    unp = failures.classify("Unpacking failed, see logfile")
    assert failures.with_server_context(unp, _problems(), None).likely_cause is None


# ------------------------------------------------------------------ article counter (SABnzbd: field = failed)
def test_articles_found_is_tried_minus_failed() -> None:
    from mso.features.usenet.history import articles_found

    assert articles_found(434_000, 1_736) == 432_264
    assert articles_found("10", "3") == 7
    assert articles_found(5, 9) == 0  # never negative
    assert articles_found(None, 3) is None and articles_found(5, None) is None


def test_mock_server_stats_behave_like_sabnzbd(app: Any, serve: Any) -> None:
    """The mock answers like SABnzbd 5.1.3: articles_success carries the failed counts; archive=1 the archive."""
    import httpx
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY)
    url = serve(mock_app)
    body = httpx.get(f"{url}/api", params={"mode": "server_stats", "output": "json", "apikey": SAB_KEY}).json()
    ew = body["servers"]["news.eweka.test"]
    day = next(iter(ew["articles_tried"]))
    assert ew["articles_tried"][day] == 14_000 and ew["articles_success"][day] == 140
    sab = mock_app.state.sab
    sab.archive_job("SABnzbd_nzo_0002")
    hist = httpx.get(f"{url}/api", params={"mode": "history", "output": "json", "apikey": SAB_KEY}).json()
    arch = httpx.get(f"{url}/api", params={"mode": "history", "output": "json", "apikey": SAB_KEY,
                                           "archive": 1}).json()
    assert "SABnzbd_nzo_0002" not in {s["nzo_id"] for s in hist["history"]["slots"]}
    assert [s["nzo_id"] for s in arch["history"]["slots"]] == ["SABnzbd_nzo_0002"]


def test_snapshot_stores_found_articles(app: Any, db: Any, admin: Any, serve: Any) -> None:
    from msomocks import sabnzbd as mock

    from mso.features.usenet import history

    mock_app = mock.create_app(api_key=SAB_KEY)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200
    history.snapshot_server_stats()
    row = db.fetchone("SELECT sum(articles_tried) AS t, sum(articles_success) AS s FROM usenet_server_stats "
                      "WHERE server='news.eweka.test'")
    assert (int(row["t"]), int(row["s"])) == (7 * 14_000, 7 * 13_860)


def test_migration_0601_corrects_stored_counters_and_rolls_back(database_url: str) -> None:
    import psycopg

    from mso.db import Database, migrate_down, migrate_up, schema_version

    name = database_url.rsplit("/", 1)[1] + "_m601"
    base = database_url.rsplit("/", 1)[0]
    with psycopg.connect(base + "/postgres", autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    db = Database(base + "/" + name, max_size=2)
    try:
        migrate_up(db, 600)
        # what 1.5.0 stored: SABnzbd's field as-is (= failed articles)
        db.execute("INSERT INTO usenet_server_stats(server,day,bytes,articles_tried,articles_success) VALUES "
                   "('eweka', current_date, 1, 434000, 1736), ('x', current_date, 1, NULL, 5), "
                   "('y', current_date, 1, 10, 12)")
        migrate_up(db, 601)
        assert schema_version(db) == 601
        got = {r["server"]: r["articles_success"] for r in db.fetchall("SELECT * FROM usenet_server_stats")}
        assert got == {"eweka": 432_264, "x": 5, "y": 0}
        assert db.scalar("SELECT count(*) FROM pg_tables WHERE tablename='usenet_server_coverage'") == 1
        migrate_down(db, 600)
        got = {r["server"]: r["articles_success"] for r in db.fetchall("SELECT * FROM usenet_server_stats")}
        assert got["eweka"] == 1_736 and got["x"] == 5
        assert db.scalar("SELECT count(*) FROM pg_tables WHERE tablename='usenet_server_coverage'") == 0
        migrate_down(db, 501)  # back to 1.5.0 keeps working
        assert schema_version(db) == 501
    finally:
        db.close()
        with psycopg.connect(base + "/postgres", autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_history_view_shows_facts_per_failed_download(app: Any, admin: Any, viewer: Any, serve: Any) -> None:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, scenario="empty")
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200
    now = int(time.time())
    sab = mock_app.state.sab
    sab.add_history(mock._slot(1, "Movie.A.2020.1080p-GRP", "movies", "missing", now - 20))
    sab.add_history(mock._slot(2, "Movie.B.2020.1080p-GRP", "movies", "permission", now - 10))
    sab.add_history(mock._slot(3, "Movie.C.2020.1080p-GRP", "movies", "success", now))
    items = {i["nzo_id"]: i for i in viewer.get("/api/v1/usenet/sab/history").json()["items"]}
    a, b, c = items["SABnzbd_nzo_0001"], items["SABnzbd_nzo_0002"], items["SABnzbd_nzo_0003"]
    assert a["facts"]["summary"] == "1.532 artikelen ontbraken" and a["facts"]["data_complete"] is False
    assert a["failure_reason"] == "incomplete"
    assert b["facts"]["problem"] == "no_permission" and b["facts"]["environment"] is True
    # SABnzbd could not write: a client problem (never a release problem, never "schijf vol")
    assert b["failure_reason"] == "client_failure" and "(geen schrijfrechten)" in b["evidence"]
    assert b["evidence"].startswith("Data was compleet; uitpakken mislukte:")
    assert b["facts"]["problem_label"] == "geen schrijfrechten"
    assert b["facts"]["summary"].startswith("Data compleet · uitpakken mislukt: Unpacking failed, write error")
    assert b["facts"]["servers"][0] == {"name": "Eweka", "bytes": int(1.4 * 1024**3)}
    assert c["facts"] is None


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("permission", "client_failure"),  # SABnzbd may not write: not a release problem, not a full disk
        ("disk", "disk_full"),
        ("unpack", "unpack_failed"),  # 7-Zip "see logfile": cause unknown, data complete
        ("crc", "unpack_failed"),  # damaged archive in complete data
        ("encrypted", "encrypted"),
    ],
)
def test_complete_data_failure_reasons(kind: str, reason: str) -> None:
    from msomocks import sabnzbd as mock

    from mso.features.usenet import failures, stagelog

    log, msg = mock.stage_log(kind, "Rel"), mock.FAIL_MESSAGES[kind]
    c = failures.classify(msg, log, facts=stagelog.parse(log, msg))
    assert c.reason == reason and c.certainty == "zeker"
    if reason != "encrypted":
        assert c.evidence.startswith("Data was compleet; uitpakken mislukte:")
