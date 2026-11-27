"""De Hub leest uitsluitend een beperkte meting uit lokale testgegevens."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from test_machine_keys import create
from test_usenet_categories import _diag, _outcome, _sab

from mso import state
from mso.api import ReadAccessError, ReadQueryError
from mso.config import get_settings
from mso.features.usenet import categories

DIAGNOSIS = "/api/v1/usenet/categories/diagnosis/cached"
MAINTENANCE = "/api/v1/system/maintenance/backup-status"


def without_usage(snapshot, used_key_id):
    keys = json.loads(snapshot["machine_keys"])
    for row in keys:
        if row["id"] == used_key_id:
            del row["last_used_at"]
    return {**snapshot, "machine_keys": sorted(keys, key=lambda row: row["id"])}


@pytest.fixture
def leessleutel(admin):
    create(admin, name="Ongebruikte lezer")
    return create(admin)


def test_vergelijking_ziet_ander_veld_en_gebruik_van_andere_sleutel(leessleutel, db, database_bytes):
    key_id = leessleutel["item"]["id"]

    def snapshot():
        return without_usage(database_bytes(db), key_id)

    voor = snapshot()
    db.execute("UPDATE machine_keys SET last_used_at=now() WHERE id=%s", (key_id,))
    assert snapshot() == voor
    db.execute("UPDATE machine_keys SET name='Hernoemde lezer' WHERE id=%s", (key_id,))
    hernoemd = snapshot()
    assert hernoemd != voor
    db.execute("UPDATE machine_keys SET last_used_at=now() WHERE id<>%s", (key_id,))
    assert snapshot() != hernoemd


@pytest.mark.parametrize("age", [None, 5, categories.CACHE_S + 1, -120])
def test_categoriediagnose_leest_uitsluitend_de_bewaarde_meting(age, leessleutel, client, database_bytes, db,
                                                               monkeypatch):
    key = {"X-Api-Key": leessleutel["key"]}
    now = datetime.now(UTC)
    at = now - timedelta(seconds=age) if age is not None else None
    monkeypatch.setattr(categories, "_now", lambda: now)
    if at:
        state.set_state(categories.STATE_KEY, {
            "checked_at": at.isoformat(), "verdict": "geen_ster",
            "sab": {"available": True, "categories": [], "complete_dir": "testmap"},
            "mediaguard": {"available": True, "instances": []},
        })
    monkeypatch.setattr(categories, "_remote", lambda **_: pytest.fail("De leesroute raadpleegt een bron"))
    before = database_bytes(db)
    for _ in range(2):
        response = client.get(DIAGNOSIS, headers=key)
        assert response.status_code == 200
        data = response.json()
        assert set(data) == {"verdict", "checked_at"}
        assert data["verdict"] == ("geen_ster" if age == 5 else "onbekend")
        if age is not None and age >= 0:
            assert datetime.fromisoformat(data["checked_at"]) == at
        else:
            assert data["checked_at"] is None
    assert client.get(DIAGNOSIS + "?refresh=true", headers=key).status_code in {403, 422}
    key_id = leessleutel["item"]["id"]
    assert without_usage(database_bytes(db), key_id) == without_usage(before, key_id)


def test_oordeel_en_meettijd_veranderen_samen(serve, admin, db, monkeypatch):
    _sab(serve, admin)
    nu = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(categories, "_now", lambda: nu)

    def meting():
        response = admin.get(DIAGNOSIS)
        assert response.status_code == 200
        data = response.json()
        return data["verdict"], datetime.fromisoformat(data["checked_at"])

    assert _diag(admin)["verdict"] == "geen_ster"
    eerste = meting()
    assert eerste == ("geen_ster", nu)
    _outcome(db, "lokale-proef", "*")
    nu += timedelta(seconds=1)
    assert _diag(admin, refresh=False)["verdict"] == "onbekend"
    assert meting() == eerste
    assert _diag(admin)["verdict"] == "onbekend"
    assert meting() == ("onbekend", nu)


@pytest.mark.parametrize("extra", [{}, {"verdict": "ongeldig"}])
def test_historische_cache_zonder_geldig_oordeel_blijft_onbekend(extra, monkeypatch):
    at = datetime.now(UTC)
    remote = {"checked_at": at.isoformat(), **extra}
    monkeypatch.setattr(state, "get_state", lambda _: remote)
    monkeypatch.setattr(categories, "star_series", lambda: pytest.fail("De leesroute berekent het oordeel opnieuw"))
    data = categories.cached_diagnosis()
    assert data.verdict == "onbekend" and data.checked_at == at


@pytest.mark.parametrize("age", [None, 1, 4, -2])
def test_onderhoud_leest_ouderdom_en_ruimte_zonder_archiefactie(age, leessleutel, client, db, database_bytes):
    key = {"X-Api-Key": leessleutel["key"]}
    at = datetime.now(UTC) - timedelta(days=age) if age is not None else None
    if at:
        db.execute("INSERT INTO backups(file,created_at) VALUES ('voorbeeld.dump',%s)", (at,))
    directory = get_settings().backup_dir
    files = set(directory.glob("*"))
    existed = directory.exists()
    before = database_bytes(db)
    response = client.get(MAINTENANCE, headers=key)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"observed_at", "last_backup_at", "backup_fresh", "free_bytes", "needed_bytes", "space_ok"}
    assert datetime.fromisoformat(data["observed_at"]) <= datetime.now(UTC)
    assert data["free_bytes"] >= 0 and data["needed_bytes"] > 0
    assert data["space_ok"] == (data["free_bytes"] >= data["needed_bytes"])
    assert data["backup_fresh"] is ({None: None, 1: True, 4: False, -2: None}[age])
    if at and age > 0:
        assert datetime.fromisoformat(data["last_backup_at"]) == at
    else:
        assert data["last_backup_at"] is None
    assert client.post("/api/v1/system/maintenance/run", headers=key).status_code == 403
    assert set(directory.glob("*")) == files and directory.exists() == existed
    key_id = leessleutel["item"]["id"]
    assert without_usage(database_bytes(db), key_id) == without_usage(before, key_id)


@pytest.mark.parametrize("path", [DIAGNOSIS, MAINTENANCE])
def test_hubmetingen_vereisen_leesrecht(admin, client, path, runtime):
    schrijver = create(admin, ["changes:approve"])["key"]
    lezer = create(admin, name="Lezer")["key"]
    reacties = [
        (401, "unauthorized", client.get(path)),
        (403, "forbidden", client.get(path, headers={"X-Api-Key": schrijver})),
        (422, "invalid", client.get(path, params={"refresh": "true"}, headers={"X-Api-Key": lezer})),
        (422, "invalid", admin.get(path, params={"refresh": "true"})),
    ]
    beschreven = runtime.openapi()["paths"][path]["get"]["responses"]
    for status, code, reactie in reacties:
        body = reactie.json()
        assert reactie.status_code == status and body["code"] == code
        schema = beschreven[str(status)]["content"]["application/json"]["schema"]
        model = ReadQueryError if status == 422 else ReadAccessError
        assert schema["$ref"] == f"#/components/schemas/{model.__name__}"
        assert model.model_validate(body).model_dump(mode="json", exclude_unset=True) == body


def test_onmeetbare_backupruimte_blijft_onbekend(admin, client, monkeypatch):
    from mso import backups

    def unavailable(_):
        raise OSError("Geen ruimtemeting beschikbaar")

    key = create(admin)["key"]
    monkeypatch.setattr(backups.shutil, "disk_usage", unavailable)
    response = client.get(MAINTENANCE, headers={"X-Api-Key": key})
    assert response.status_code == 200
    assert response.json()["free_bytes"] is None and response.json()["space_ok"] is None
