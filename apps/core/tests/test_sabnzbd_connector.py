"""SABnzbd connector over real HTTP against msomocks.sabnzbd: capabilities, contract (add_url/add_nzb/
categories), idempotent sends, API key never in logs/API/audit."""

from __future__ import annotations

import logging
from typing import Any

import pytest

NZB = b"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
 <head><meta type="name">Show.Name.S01E05.1080p.WEB.H264-GRP</meta></head>
 <file poster="someone@example.invalid" date="1700000000" subject="Show.Name.S01E05 [1/2] yEnc">
  <groups><group>alt.binaries.mso.test</group></groups>
  <segments>
   <segment bytes="700000" number="1">seg1@mock</segment>
   <segment bytes="700000" number="2">seg2@mock</segment>
  </segments>
 </file>
 <file poster="someone@example.invalid" date="1700000100" subject="Show.Name.S01E05 [2/2] yEnc">
  <groups><group>alt.binaries.mso.test</group></groups>
  <segments><segment bytes="500000" number="1">seg3@mock</segment></segments>
 </file>
</nzb>"""

SAB_KEY = "SABGEHEIM-0123456789"


@pytest.fixture()
def sab(app: Any, serve: Any, admin: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY)
    url = serve(mock_app)
    r = admin.put(
        "/api/v1/connectors/sabnzbd", json={"url": url, "enabled": True, "secrets": {"api_key": SAB_KEY}}
    )
    assert r.status_code == 200, r.text
    return mock_app.state.sab


def test_connector_test_reports_version_and_capabilities(admin: Any, sab: Any) -> None:
    r = admin.post("/api/v1/connectors/sabnzbd/test")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["ok"] is True and res["status"] == "healthy"
    assert res["version"] == "4.3.3"
    caps = res["capabilities"]
    assert caps["servers"] == 3 and caps["categories"] == 5
    assert all(caps["modes"].values())
    assert SAB_KEY not in r.text


def test_connector_test_bad_key_is_auth_failed(
    admin: Any, sab: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    r = admin.post("/api/v1/connectors/sabnzbd/test", json={"secrets": {"api_key": "VERKEERD-sleutel-xyz"}})
    res = r.json()
    assert res["ok"] is False and res["status"] == "auth_failed"
    assert "VERKEERD-sleutel-xyz" not in r.text
    assert "VERKEERD-sleutel-xyz" not in caplog.text and SAB_KEY not in caplog.text
    assert SAB_KEY not in admin.get("/api/v1/audit").text
    assert SAB_KEY not in admin.get("/api/v1/connectors").text


def test_contract_add_url_add_nzb_categories(
    app: Any, db: Any, sab: Any, caplog: pytest.LogCaptureFixture
) -> None:
    from mso.connectors.sabnzbd import SabClient

    caplog.set_level(logging.DEBUG)
    client = SabClient.from_config()
    assert "SABGEHEIM" not in repr(client)
    assert client.categories() == ["tv", "tv-anime", "movies", "movies-anime", "software"]
    nzo = client.add_url(
        "https://indexer.example/getnzb/123?apikey=INDEXERKEY", category="tv", name="Show.S01E05"
    )
    assert nzo.startswith("SABnzbd_nzo_")
    # idempotent within 24h: same URL → same job, no second SAB queue item
    assert client.add_url("https://indexer.example/getnzb/123?apikey=INDEXERKEY", category="tv") == nzo
    assert len(sab.queue) == 1
    row = db.fetchone("SELECT * FROM usenet_nzbs WHERE nzo_id=%s", (nzo,))
    assert row["origin"] == "send_url" and "INDEXERKEY" not in (row["url_redacted"] or "")
    assert row["segments"] == []  # sent as URL: contents unknown → not testable
    nzo2 = client.add_nzb(
        NZB, "Show.Name.S01E05.nzb", category="tv", release_key="nzb:abc", source_id="direct:x"
    )
    assert client.add_nzb(NZB, "again.nzb", category="tv") == nzo2
    row2 = db.fetchone("SELECT * FROM usenet_nzbs WHERE nzo_id=%s", (nzo2,))
    assert row2["segment_total"] == 3 and row2["files"] == 2 and row2["groups"] == ["alt.binaries.mso.test"]
    assert [s["m"] for s in row2["segments"]] == ["seg1@mock", "seg2@mock", "seg3@mock"]
    assert "poster" not in str(row2["segments"]) and "someone@" not in str(row2)
    assert row2["release_key"] == "nzb:abc" and row2["posted_at"] is not None
    actions = [r["action"] for r in db.fetchall("SELECT action FROM audit_log ORDER BY id")]
    assert "sabnzbd.add_url" in actions and "sabnzbd.add_nzb" in actions
    assert SAB_KEY not in caplog.text and "INDEXERKEY" not in caplog.text
    client.close()


def test_invalid_nzb_rejected_before_sending(app: Any, sab: Any) -> None:
    from mso.connectors.sabnzbd import SabClient
    from mso.features.usenet.nzbs import NzbError

    with SabClient.from_config() as client, pytest.raises(NzbError):
        client.add_nzb(b"<html>no nzb</html>", "x.nzb", category="tv")
    assert sab.queue == []


def test_xml_entity_attack_refused() -> None:
    from mso.features.usenet.nzbs import NzbError, parse_nzb

    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<nzb><file><segments><segment bytes="1" number="1">&b;@x</segment></segments></file></nzb>'
    )
    with pytest.raises(NzbError):
        parse_nzb(bomb)


def test_sab_endpoints_require_connector(admin: Any) -> None:
    r = admin.get("/api/v1/usenet/sab/queue")
    assert r.status_code == 409 and r.json()["code"] == "connector_not_configured"


def test_queue_and_history_views(admin: Any, viewer: Any, sab: Any) -> None:
    from mso.connectors.sabnzbd import SabClient

    with SabClient.from_config() as c:
        c.add_url("https://indexer.example/getnzb/9", category="movies", name="Movie.Title.2020")
    q = viewer.get("/api/v1/usenet/sab/queue").json()
    assert q["items"][0]["media_category"] == "movies" and q["total"] == 1
    h = viewer.get("/api/v1/usenet/sab/history?failed_only=true").json()
    reasons = {i["failure_reason"] for i in h["items"]}
    assert {"account_expired", "auth_failure", "incomplete", "encrypted"} <= reasons
    assert all(i["status"] == "failed" for i in h["items"])
    assert SAB_KEY not in str(h)
