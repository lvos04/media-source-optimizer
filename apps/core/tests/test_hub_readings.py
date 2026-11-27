"""De Hub leest uitsluitend een beperkte meting uit lokale testgegevens."""

from datetime import UTC, datetime, timedelta

import pytest
from test_machine_keys import create

from mso import state
from mso.config import get_settings
from mso.features.usenet import categories

DIAGNOSIS = "/api/v1/usenet/categories/diagnosis/cached"
MAINTENANCE = "/api/v1/system/maintenance/backup-status"


def without_usage(snapshot):
    return {k: v for k, v in snapshot.items() if k != "machine_keys"}


@pytest.mark.parametrize("age", [None, 5, categories.CACHE_S + 1, -120])
def test_categoriediagnose_leest_uitsluitend_de_bewaarde_meting(age, admin, client, database_bytes, db, monkeypatch):
    key = {"X-Api-Key": create(admin)["key"]}
    now = datetime.now(UTC)
    at = now - timedelta(seconds=age) if age is not None else None
    monkeypatch.setattr(categories, "_now", lambda: now)
    if at:
        state.set_state(categories.STATE_KEY, {
            "checked_at": at.isoformat(), "sab": {"available": True, "categories": [], "complete_dir": "testmap"},
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
    assert without_usage(database_bytes(db)) == without_usage(before)


@pytest.mark.parametrize("age", [None, 1, 4, -2])
def test_onderhoud_leest_ouderdom_en_ruimte_zonder_archiefactie(age, admin, client, db, database_bytes):
    key = {"X-Api-Key": create(admin)["key"]}
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
    assert without_usage(database_bytes(db)) == without_usage(before)


@pytest.mark.parametrize("path", [DIAGNOSIS, MAINTENANCE])
def test_hubmetingen_vereisen_leesrecht(admin, client, path):
    key = create(admin, ["changes:approve"])["key"]
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Api-Key": key}).status_code == 403


def test_onmeetbare_backupruimte_blijft_onbekend(admin, client, monkeypatch):
    from mso import backups

    def unavailable(_):
        raise OSError("Geen ruimtemeting beschikbaar")

    key = create(admin)["key"]
    monkeypatch.setattr(backups.shutil, "disk_usage", unavailable)
    response = client.get(MAINTENANCE, headers={"X-Api-Key": key})
    assert response.status_code == 200
    assert response.json()["free_bytes"] is None and response.json()["space_ok"] is None
