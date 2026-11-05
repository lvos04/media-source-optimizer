"""1.4.0 · 2b: after every NZB MSO sends, a budgeted STAT sample of exactly those articles runs automatically on
every provider with MSO credentials (1 connection per provider), filling the availability/overlap table; daily
and monthly budgets (connections, commands, bytes) are enforced; opt-out setting; no secrets anywhere."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import pytest

USER, PASSWORD = "u", "p-heel-geheim"
SAB_KEY = "SABGEHEIM-0123456789"


def make_nzb(ids: list[str], title: str = "Show.Name.S01E07.1080p.WEB.H264-GRP") -> bytes:
    segs = "".join(f'<segment bytes="700000" number="{i + 1}">{m}</segment>' for i, m in enumerate(ids))
    return (
        f'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><head>'
        f'<meta type="name">{title}</meta></head><file poster="x@example.invalid" date="1700000000" '
        f'subject="s"><groups><group>alt.binaries.mso.test</group></groups><segments>{segs}</segments>'
        f"</file></nzb>"
    ).encode()


@pytest.fixture()
def setup(app: Any, admin: Any, serve: Any) -> Iterator[tuple[Any, Any, Any]]:
    """Eweka has seg1..seg80, Newshosting seg21..seg100; 'blok' has no credentials in MSO (not sampled)."""
    from msomocks import nntp
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY, scenario="empty")
    admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    a = nntp.start(username=USER, password=PASSWORD, articles=[f"seg{i}@mock" for i in range(1, 81)])
    b = nntp.start(username=USER, password=PASSWORD, articles=[f"seg{i}@mock" for i in range(21, 101)])
    for slug, srv in (("eweka", a), ("newshosting", b)):
        r = admin.post(
            "/api/v1/usenet/providers",
            json={"slug": slug, "label": slug.title(), "host": "127.0.0.1", "port": srv.port, "ssl": False,
                  "connections": 4, "username": USER, "password": PASSWORD},
        )
        assert r.status_code == 200, r.text
    r = admin.post("/api/v1/usenet/providers",
                   json={"slug": "blok", "label": "Blok", "kind": "block", "host": "127.0.0.1", "port": 1119})
    assert r.status_code == 200
    yield mock_app.state.sab, a, b
    a.stop()
    b.stop()


def _send(ids: list[str], name: str = "x.nzb") -> str:
    from mso.connectors.sabnzbd import SabClient

    with SabClient.from_config() as c:
        return c.add_nzb(make_nzb(ids, title=name.removesuffix(".nzb")), name, category="tv", actor="tester")


def test_auto_sample_after_send_fills_availability(
    app: Any, db: Any, admin: Any, viewer: Any, worker: Any, setup: Any
) -> None:
    _, a, b = setup
    _send([f"seg{i}@mock" for i in range(1, 101)])
    test = db.fetchone("SELECT * FROM usenet_article_tests")
    assert test["trigger"] == "auto_send" and sorted(test["providers"]) == ["eweka", "newshosting"]
    assert test["sample_size"] == 25  # usenet.auto_sample_size
    worker.drain()
    test = db.fetchone("SELECT * FROM usenet_article_tests")
    assert test["status"] == "done"
    # exactly one connection per provider, only STAT commands for the articles (plus login/quit)
    for srv in (a, b):
        cmds = srv.state.commands
        assert sum(1 for c in cmds if c.startswith("STAT")) == 25
        assert sum(1 for c in cmds if c.startswith("AUTHINFO USER")) == 1
        assert not any(c.startswith(("ARTICLE", "BODY")) for c in cmds)
    # availability/overlap table has real data now
    items = {c["slug"]: c for c in viewer.get("/api/v1/usenet/contribution").json()["items"]}
    assert items["eweka"]["articles_checked"] == 25 and items["newshosting"]["articles_checked"] == 25
    assert items["eweka"]["unique_available"] is not None and items["eweka"]["compared_articles"] == 25
    assert items["blok"]["articles_checked"] == 0
    # usage counted per provider: connections, commands, bytes
    budget = viewer.get("/api/v1/usenet/stat-budget").json()
    use = {p["slug"]: p["usage"]["daily"] for p in budget["providers"]}
    assert use["eweka"]["connections"] == 1 and use["eweka"]["commands"] >= 25 and use["eweka"]["bytes"] > 0
    assert budget["enabled"] is True and budget["last_auto_test_at"]
    # the same NZB sent again (idempotent send) does not start a second sample
    _send([f"seg{i}@mock" for i in range(1, 101)])
    assert db.scalar("SELECT count(*) FROM usenet_article_tests") == 1
    # no secrets in audit, API or job payloads
    dump = json.dumps(db.fetchall("SELECT * FROM audit_log"), default=str)
    dump += json.dumps(db.fetchall("SELECT * FROM jobs"), default=str)
    dump += viewer.get("/api/v1/usenet/stat-budget").text + viewer.get("/api/v1/usenet/article-tests").text
    dump += viewer.get("/api/v1/usenet/providers").text
    assert PASSWORD not in dump and SAB_KEY not in dump
    assert "usenet.auto_sample" in [r["action"] for r in db.fetchall("SELECT action FROM audit_log")]


def test_budget_limits_connections_and_commands(app: Any, db: Any, worker: Any, setup: Any) -> None:
    from mso import settings_registry as sr

    _, a, _ = setup
    # commands: 4 overhead + 10 STATs fit in 14
    sr.put({"usenet.stat_budget_daily_commands": 14, "usenet.stat_budget_daily_connections": 1}, "test")
    _send([f"seg{i}@mock" for i in range(1, 101)], "one.nzb")
    worker.drain()
    assert sum(1 for c in a.state.commands if c.startswith("STAT")) == 10
    # connection budget for today is used → the next send queues no sample at all
    _send([f"seg{i}@mock" for i in range(1, 50)], "two.nzb")
    assert db.scalar("SELECT count(*) FROM usenet_article_tests") == 1
    # a sample that was queued before the budget ran out stops at the provider (not tested, explained)
    from mso.features.usenet import articles

    nzb2 = db.scalar("SELECT id FROM usenet_nzbs WHERE title='two'")
    db.execute(
        "INSERT INTO usenet_article_tests(nzb_id,trigger,providers,sample_size) VALUES (%s,'auto_send',%s,5)",
        (nzb2, ["eweka"]),
    )
    tid = db.scalar("SELECT max(id) FROM usenet_article_tests")
    summary = articles.run(int(tid))
    assert summary["providers"]["eweka"]["status"] == "budget_exhausted"
    assert summary["providers"]["eweka"]["checked"] == 0
    assert sum(1 for c in a.state.commands if c.startswith("STAT")) == 10  # nothing more was sent


def test_monthly_byte_budget_and_opt_out(app: Any, db: Any, worker: Any, setup: Any) -> None:
    from mso import settings_registry as sr
    from mso.features.usenet import articles

    db.execute(
        "INSERT INTO usenet_stat_usage(day,provider,connections,commands,bytes) VALUES "
        "(date_trunc('month', current_date)::date,'eweka',1,10,%s)",
        (int(sr.get("usenet.stat_budget_monthly_kb")) * 1024,),
    )
    assert articles.budget_fit("eweka") == 0 and articles.budget_fit("newshosting") > 0
    _send([f"seg{i}@mock" for i in range(1, 101)], "m.nzb")
    assert db.fetchone("SELECT providers FROM usenet_article_tests")["providers"] == ["newshosting"]

    sr.put({"usenet.auto_sample_after_send": False}, "test")
    _send([f"seg{i}@mock" for i in range(1, 60)], "off.nzb")
    assert db.scalar("SELECT count(*) FROM usenet_article_tests") == 1


def test_url_send_and_upload_do_not_sample(app: Any, db: Any, operator: Any, setup: Any) -> None:
    from mso.connectors.sabnzbd import SabClient

    with SabClient.from_config() as c:
        c.add_url("https://indexer.example.invalid/getnzb/1.nzb?apikey=abc", category="tv")
    r = operator.post("/api/v1/usenet/nzbs", json={
        "filename": "u.nzb", "content_b64": base64.b64encode(make_nzb(["seg1@mock", "seg2@mock"])).decode()})
    assert r.status_code == 200
    assert db.scalar("SELECT count(*) FROM usenet_article_tests") == 0
