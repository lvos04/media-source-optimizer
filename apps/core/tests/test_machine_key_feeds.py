"""Vergelijk de publieke feedauthenticatie met de echte basis vóór machine-sleutels."""

import json
from pathlib import Path

import pytest
from defusedxml import ElementTree as ET

from mso.features.newznab_server import keys
from mso.security import limiter
from tests.conftest import USER_FIXTURE_PASSWORD


def test_consumer_auth_matches_base(admin, client, db):
    consumer = keys.default_key()
    limiter.reset()
    outcomes = {}
    for feed in ("newznab", "torznab"):
        for action in ("caps", "search"):
            for label, query_key, header_key in [
                ("geen", None, None), ("query", consumer, None), ("kop", None, consumer),
                ("foute_query", "ongeldig", None), ("foute_kop", None, "ongeldig"),
                ("beide", consumer, consumer), ("query_geldig", consumer, "ongeldig"),
                ("kop_geldig", "ongeldig", consumer),
            ]:
                response = client.get(
                    f"/{feed}/api", params={"t": action, **({"apikey": query_key} if query_key else {})},
                    headers={"X-Api-Key": header_key} if header_key else {},
                )
                content_type = response.headers["content-type"].split(";")[0]
                root = ET.fromstring(response.content) if content_type == "application/xml" else None
                outcomes[f"{feed}/{action}/{label}"] = [
                    response.status_code, content_type, root.tag if root is not None else None,
                    root.attrib.get("code") if root is not None else None,
                ]
    for n in range(10):
        feed = ("newznab", "torznab")[n % 2]
        client.get(f"/{feed}/api", params={"t": "search"}, headers={"X-Api-Key": consumer})
    login = client.post("/api/v1/auth/login", json={"username": "beheerder", "password": USER_FIXTURE_PASSWORD})
    outcomes["login"] = [login.status_code, login.json().get("code")]
    outcomes["machine_audit"] = db.scalar("SELECT count(*) FROM audit_log WHERE action LIKE 'machine_keys.%'")
    if db.scalar("SELECT to_regclass('machine_auth_limits')"):
        assert db.scalar("SELECT count(*) FROM machine_auth_limits") == 0
    expected = Path(__file__).with_name("fixtures") / "machine_key_feeds_basis.json"
    assert outcomes == json.loads(expected.read_text())


@pytest.mark.parametrize("feed", ["newznab", "torznab"])
def test_machine_header_cannot_use_existing_feeds(admin, client, feed):
    from tests.test_machine_keys import create

    key = create(admin)["key"]
    for params in ({"t": "caps"}, {"t": "search", "apikey": keys.default_key()}):
        assert client.get(f"/{feed}/api", params=params, headers={"X-Api-Key": key}).status_code == 403
