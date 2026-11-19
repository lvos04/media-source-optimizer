"""Regressies voor gebonden wijzigingen op de lokale PostgreSQL-database en de nepbron."""

from datetime import UTC, datetime, timedelta

import pytest
import test_connector_api
import test_maintenance
from conftest import USER_FIXTURE_PASSWORD
from fastapi import HTTPException
from test_change_safety import HUB_RATE_PATH, _hub_key
from test_connector_api import PREFIX, _connector_headers, _execution, _pin, fout

from mso import backups, changes, machine_keys, state
from mso.app import init_runtime
from mso.change_preview import digest
from mso.db import migrate_down, migrate_up, schema_version

signed_change = test_connector_api.signed_change
old_db = test_maintenance.old_db
swap_db = test_maintenance.swap_db


def _applied_count(db, ch):
    return db.scalar("SELECT count(*) FROM audit_log WHERE change_id=%s AND action='change.applied'", (ch["id"],))


@pytest.mark.parametrize("route", ["apply", "rollback"])
def test_verval_tijdens_broncontrole(route, client, db, database_bytes, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    now = datetime.now(UTC).replace(microsecond=0)
    if route == "rollback":
        applied = client.post(PREFIX + "/apply", json=_execution(ch, key), headers=_connector_headers())
        assert applied.status_code == 200
        ch = applied.json()
    body = _execution(ch, key, rollback=route == "rollback", now=now)
    original = source.applier()

    def read(path):
        monkeypatch.setattr(changes, "_now", lambda: now + timedelta(minutes=16))
        return original.read(path)

    monkeypatch.setitem(changes.APPLIERS, "qbittorrent", lambda: changes.Applier(read, original.write))
    before, writes = database_bytes(db), list(source.writes)
    fout(client.post(PREFIX + "/" + route, json=body, headers=_connector_headers()), 409, "approval_expired")
    assert source.writes == writes
    assert database_bytes(db) == before


@pytest.mark.parametrize("reason", ["alleen_lezen", "afwijking"])
def test_vooraf_geweigerd_zonder_reservering(reason, client, db, database_bytes, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    if reason == "alleen_lezen":
        state.set_state("mode", "read_only")
    else:
        source.values[HUB_RATE_PATH] = 90
    before = database_bytes(db)
    response = client.post(PREFIX + "/apply", json=_execution(ch, key), headers=_connector_headers())
    assert response.status_code == 409
    assert source.writes == []
    assert database_bytes(db) == before


def test_oude_hash_blijft_ongewijzigd_bij_weigering(admin, client, db, database_bytes, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    machine = _hub_key(admin, ["changes:approve"])
    assert migrate_down(db, 609) == [610]
    assert migrate_up(db) == [610]
    base = f"/api/v1/changes/{ch['id']}"
    wrong = digest("andere voorvertoning")
    before = database_bytes(db)
    native = client.post(base + "/approve", json={"expected_preview_hash": wrong},
                         headers={**machine, **_connector_headers()})
    assert (native.status_code, native.json()["code"]) == (409, "preview_changed")
    fout(client.post(PREFIX + "/apply", json=_execution({**ch, "preview_hash": wrong}, key),
                     headers=_connector_headers()), 409, "preview_changed")
    assert source.writes == []
    assert database_bytes(db) == before
    assert admin.get(base).json()["preview_hash"] == ch["preview_hash"]
    assert db.scalar("SELECT preview_hash FROM change_sets WHERE id=%s", (ch["id"],)) == ch["preview_hash"]


def test_foutantwoord_maskeert_bronlabel_en_waarde(admin, db, signed_change, caplog):
    ch, source, _ = signed_change
    label = "password=" + USER_FIXTURE_PASSWORD
    source.values[HUB_RATE_PATH] = label
    ops = [{**ch["ops"][0], "label": label}]
    ch = changes.create(ch["target"], "Meetbron", ops, risk=ch["risk"])
    base = f"/api/v1/changes/{ch['id']}"
    assert admin.post(base + "/approve").status_code == 200
    headers = _connector_headers()
    for _ in range(2):
        refused = admin.post(base + "/apply", headers=headers)
        assert refused.status_code == 409
        assert USER_FIXTURE_PASSWORD not in refused.text
    assert not db.scalar("SELECT count(*) FROM connector_idempotency WHERE response::text LIKE %s",
                         ("%" + USER_FIXTURE_PASSWORD + "%",))
    assert USER_FIXTURE_PASSWORD not in caplog.text
    assert source.writes == []


def test_definitieve_bronfout_bewaart_alleen_geredigeerd_antwoord(admin, db, signed_change, caplog):
    ch, source, _ = signed_change
    label = "password=" + USER_FIXTURE_PASSWORD
    second = "pref:up_limit"
    source.values[second] = 50
    source.fail.update({(second, 40), (HUB_RATE_PATH, 100)})
    ops = [{**ch["ops"][0], "label": label}, {"path": second, "before": 50, "after": 40}]
    ch = changes.create(ch["target"], "Herstelcontrole", ops, risk=ch["risk"])
    base = f"/api/v1/changes/{ch['id']}"
    assert admin.post(base + "/approve").status_code == 200
    headers = _connector_headers()
    first = admin.post(base + "/apply", headers=headers)
    assert first.status_code == 502 and USER_FIXTURE_PASSWORD not in first.text
    saved = db.scalar("SELECT response FROM connector_idempotency WHERE request_id=%s", (headers["Idempotency-Key"],))
    assert saved == {"status": 502, "body": first.json()}
    writes = list(source.writes)
    replay = admin.post(base + "/apply", headers=headers)
    assert replay.json() == first.json() and replay.headers["Idempotent-Replay"] == "true"
    assert source.writes == writes and USER_FIXTURE_PASSWORD not in caplog.text


def test_operator_herhaalt_alleen_met_actueel_recht(admin, operator, db, signed_change):
    ch, source, _ = signed_change
    base = f"/api/v1/changes/{ch['id']}"
    assert admin.post(base + "/approve").status_code == 200
    headers = _connector_headers()
    first = operator.post(base + "/apply", headers=headers)
    assert first.status_code == 200
    replay = operator.post(base + "/apply", headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert replay.headers["Idempotent-Replay"] == "true"
    db.execute("UPDATE users SET role='viewer' WHERE username='operator'")
    assert operator.post(base + "/apply", headers=headers).status_code == 403
    assert source.writes == [(HUB_RATE_PATH, 80)] and _applied_count(db, ch) == 1


def test_verlopen_aanvraag_kan_alleen_antwoord_herhalen(client, db, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    body, headers = _execution(ch, key), _connector_headers()
    first = client.post(PREFIX + "/apply", headers=headers, json=body)
    assert first.status_code == 200
    later = datetime.now(UTC) + timedelta(minutes=16)
    monkeypatch.setattr(changes, "_now", lambda: later)
    replay = client.post(PREFIX + "/apply", headers=headers, json=body)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert replay.headers["Idempotent-Replay"] == "true"
    fresh_headers = _connector_headers()
    expired = _execution(ch, key)
    fout(client.post(PREFIX + "/apply", headers=fresh_headers, json=expired), 409, "approval_expired")
    fout(client.post(PREFIX + "/apply", headers=headers, json=expired), 422, "idempotency_key_reused")
    from mso.config import get_settings

    monkeypatch.setattr(get_settings(), "hub_policy_hash", digest("ander beleid"))
    fout(client.post(PREFIX + "/apply", headers=headers, json=body), 403, "forbidden")
    assert source.writes == [(HUB_RATE_PATH, 80)] and _applied_count(db, ch) == 1


def test_ongebruikte_goedkeuring_vervalt_bij_herstel(client, db, database_bytes, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    body = _execution(ch, key)
    archive = backups.create_backup(actor="proef")
    backups.restore_backup(archive["id"], "proef")
    before = database_bytes(db)
    fout(client.post(PREFIX + "/apply", json=body, headers=_connector_headers()), 409, "approval_expired")
    assert database_bytes(db) == before and source.writes == []
    # Het ondertekende tijdstip heeft een precisie van één seconde.
    later = datetime.now(UTC) + timedelta(seconds=2)
    monkeypatch.setattr(changes, "_now", lambda: later)
    assert client.post(PREFIX + "/apply", json=_execution(ch, key, now=later), headers=_connector_headers()
                       ).status_code == 200
    assert source.writes == [(HUB_RATE_PATH, 80)]


def test_herstel_vraagt_verse_native_goedkeuring(admin, client, db, signed_change):
    ch, source, _ = signed_change
    machine = _hub_key(admin, ["changes:approve", "read"])
    base = f"/api/v1/changes/{ch['id']}"
    body = {"expected_preview_hash": ch["preview_hash"]}
    old = {**machine, **_connector_headers()}
    assert client.post(base + "/approve", json=body, headers=old).status_code == 200
    archive = backups.create_backup(actor="proef")
    backups.restore_backup(archive["id"], "proef")
    assert client.get(base, headers=machine).json()["preview_hash"] == ch["preview_hash"]
    assert client.post(base + "/approve", json=body, headers=old).status_code == 409
    denied = client.post(base + "/apply", json=body, headers={**machine, **_connector_headers()})
    assert denied.status_code == 409 and source.writes == []
    assert client.post(base + "/approve", json=body, headers={**machine, **_connector_headers()}).status_code == 200
    assert client.post(base + "/apply", json=body, headers={**machine, **_connector_headers()}).status_code == 200
    assert source.writes == [(HUB_RATE_PATH, 80)] and _applied_count(db, ch) == 1


def test_herstel_blokkeert_ook_de_achtergrondwerker(db, signed_change):
    ch, source, _ = signed_change
    changes.approve(ch["id"], "beheerder", db)
    with machine_keys.administration_lock(db), pytest.raises(HTTPException) as refused:
        changes.apply(ch["id"], "systeem", db=db)
    assert refused.value.status_code == 503
    assert source.writes == [] and _applied_count(db, ch) == 0


def test_onderbroken_herstel_sluit_het_oude_journaal(db, signed_change, monkeypatch):
    ch, source, _ = signed_change
    db.execute("UPDATE change_sets SET status='applying' WHERE id=%s", (ch["id"],))
    archive = backups.create_backup(actor="proef")
    db.execute("UPDATE change_sets SET status='failed' WHERE id=%s", (ch["id"],))
    settle = backups._settle_applying

    def interrupted(file, db):
        monkeypatch.setattr(backups, "_settle_applying", settle)
        raise RuntimeError("Onderbreking na terugzetten van de database")

    monkeypatch.setattr(backups, "_settle_applying", interrupted)
    with pytest.raises(RuntimeError, match="Onderbreking"):
        backups.restore_backup(archive["id"], "proef")
    assert changes.get(ch["id"], db)["status"] == "failed"
    assert changes.reconcile_interrupted(db)["checked"] == 0
    assert source.writes == [] and _applied_count(db, ch) == 0


def test_gebonden_rollback_controleert_alle_bronwaarden(client, db, database_bytes, signed_change, monkeypatch, caplog):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    other = "pref:up_limit"
    source.values[other] = 50
    ch = changes.create(ch["target"], "Twee limieten", [*ch["ops"], {"path": other, "before": 50, "after": 40}],
                        risk=ch["risk"])
    first = client.post(PREFIX + "/apply", json=_execution(ch, key), headers=_connector_headers())
    assert first.status_code == 200
    source.values[HUB_RATE_PATH] = 25
    before, writes = database_bytes(db), list(source.writes)
    body = _execution(first.json(), key, rollback=True)
    fout(client.post(PREFIX + "/rollback", json=body, headers=_connector_headers()), 412, "precondition_failed")
    assert source.writes == writes and database_bytes(db) == before
    assert "change.rollback_refused" in caplog.text


def test_brontimeout_wordt_geverifieerd_zonder_tweede_uitvoering(client, db, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    original = source.applier()
    readable = True

    def write(path, value):
        nonlocal readable
        original.write(path, value)
        readable = False
        raise TimeoutError("De meetbron antwoordt niet.")

    def read(path):
        if not readable:
            raise TimeoutError("Teruglezen tijdelijk onmogelijk.")
        return original.read(path)

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(read, write))
    body, headers = _execution(ch, key), _connector_headers()
    pending = client.post(PREFIX + "/apply", json=body, headers=headers)
    assert pending.status_code in (502, 503)
    assert changes.get(ch["id"])["status"] == "applying"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='change.auto_restored'") == 0
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "wacht"}
    fout(client.post(PREFIX + "/apply", json=body, headers=headers), 409, "idempotency_in_progress")
    readable = True
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "afgerond"}
    replay = client.post(PREFIX + "/apply", json=body, headers=headers)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replay"] == "true"
    assert source.values[HUB_RATE_PATH] == 80 and source.writes == [(HUB_RATE_PATH, 80)]
    assert _applied_count(db, ch) == 1


def test_bron_bevestigt_waarde_na_verloren_antwoord(client, db, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    original = source.applier()

    def write(path, value):
        original.write(path, value)
        raise TimeoutError("Antwoord van de meetbron verloren.")

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(original.read, write))
    body, headers = _execution(ch, key), _connector_headers()
    first = client.post(PREFIX + "/apply", json=body, headers=headers)
    assert first.status_code == 200 and first.json()["readback"]["verified"]
    replay = client.post(PREFIX + "/apply", json=body, headers=headers)
    assert replay.json() == first.json() and replay.headers["Idempotent-Replay"] == "true"
    assert source.writes == [(HUB_RATE_PATH, 80)] and _applied_count(db, ch) == 1


@pytest.mark.parametrize("damage", ["dumpfout", "ontbreekt", "checksum", "archief"])
def test_migratie_wacht_op_gecontroleerd_archief(damage, app, old_db, swap_db, monkeypatch):
    from mso.config import get_settings

    migrate_up(old_db, 609)
    old_db.execute("INSERT INTO users(username,password_hash,role) VALUES ('upgradeproef','testhash','admin')")
    if damage == "dumpfout":
        def fail(**kwargs):
            raise backups.BackupError("Gesimuleerde dumpfout")
        monkeypatch.setattr(backups, "_dump", fail)
    else:
        made = backups.pre_upgrade_backup(old_db)
        path = get_settings().backup_dir / made["file"]
        if damage == "ontbreekt":
            path.unlink()
        elif damage == "checksum":
            with path.open("ab") as archive:
                archive.write(b"schade")
        else:
            path.write_bytes(b"geen PostgreSQL-archief")
            old_db.execute("UPDATE backups SET sha256=%s WHERE file=%s", (backups.sha256_file(path), path.name))
    with pytest.raises(backups.BackupError):
        init_runtime(old_db)
    assert schema_version(old_db) == 609
    assert old_db.scalar("SELECT count(*) FROM users WHERE username='upgradeproef'") == 1


def test_cli_zonder_nieuwe_dump_vereist_bestaand_archief(app, old_db, swap_db):
    from mso.__main__ import main

    migrate_up(old_db, 609)
    old_db.execute("INSERT INTO users(username,password_hash,role) VALUES ('upgradeproef','testhash','admin')")
    with pytest.raises(SystemExit) as stopped:
        main(["migrate", "--no-backup"])
    assert stopped.value.code == 1 and schema_version(old_db) == 609
    assert old_db.scalar("SELECT count(*) FROM backups") == 0
