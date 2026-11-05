"""1.5.0 · P3: without provider credentials the availability table is never empty: SABnzbd's own per-server article
counters are the passive source ("passief uit SABnzbd"); STAT samples stay the source for providers with
credentials; one gentle hint "Inloggegevens toevoegen voor betere meting"."""

from __future__ import annotations

from typing import Any

SAB_KEY = "SABGEHEIM-passive-0123456789"


def _start(serve: Any, admin: Any) -> Any:
    from msomocks import sabnzbd as mock

    mock_app = mock.create_app(api_key=SAB_KEY)
    r = admin.put("/api/v1/connectors/sabnzbd", json={"url": serve(mock_app), "secrets": {"api_key": SAB_KEY}})
    assert r.status_code == 200, r.text
    return mock_app.state.sab


def test_table_never_empty_passive_from_sabnzbd(app: Any, db: Any, admin: Any, viewer: Any, serve: Any) -> None:
    _start(serve, admin)
    # no providers, no stored statistics yet: SABnzbd's counters are read on demand
    body = viewer.get("/api/v1/usenet/contribution").json()
    rows = {i["passive"]["server"]: i for i in body["items"]}
    assert set(rows) == {"news.eweka.test", "news.newshosting.test", "block.example.test"}
    ew = rows["news.eweka.test"]
    assert ew["availability"]["source"] == "passive" and ew["availability"]["label"] == "passief uit SABnzbd"
    assert abs(ew["availability"]["ratio"] - 0.99) < 0.001
    assert body["sources"]["passive"] == 3 and body["credentials_hint"] is None

    # providers imported from SABnzbd, without credentials → same passive figure + one gentle hint
    assert admin.post("/api/v1/usenet/providers/import-sab").status_code == 200
    body = viewer.get("/api/v1/usenet/contribution").json()
    assert len(body["items"]) == 3 and all(i["slug"] for i in body["items"])
    assert all(i["availability"]["source"] == "passive" for i in body["items"])
    hint = body["credentials_hint"]
    assert hint and "Inloggegevens toevoegen voor betere meting" in hint["text"] and len(hint["providers"]) == 3
    assert SAB_KEY not in str(body)


def test_stat_sample_wins_for_providers_with_results(app: Any, db: Any, admin: Any, viewer: Any, serve: Any) -> None:
    _start(serve, admin)
    assert admin.post("/api/v1/usenet/providers/import-sab").status_code == 200
    slug = db.scalar("SELECT slug FROM usenet_providers WHERE sab_server='news.eweka.test'")
    nzb = db.scalar(
        "INSERT INTO usenet_nzbs(sha256,title,origin,segment_total) VALUES ('x','t','upload',4) RETURNING id"
    )
    tid = db.scalar(
        "INSERT INTO usenet_article_tests(nzb_id,status,trigger,sample_size,providers) "
        "VALUES (%s,'done','manual',4,%s) RETURNING id",
        (nzb, [slug]),
    )
    for i, ok in enumerate([True, True, True, False]):
        db.execute(
            "INSERT INTO usenet_article_results(test_id,provider,message_id,available,checked_at) "
            "VALUES (%s,%s,%s,%s,now())",
            (tid, slug, f"m{i}@x", ok),
        )
    rows = {i["slug"]: i for i in viewer.get("/api/v1/usenet/contribution").json()["items"]}
    assert rows[slug]["availability"] == {"ratio": 0.75, "source": "stat", "label": "STAT-steekproef", "articles": 4}
