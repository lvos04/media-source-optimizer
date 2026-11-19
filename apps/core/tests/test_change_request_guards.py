"""Regressies voor gebonden wijzigingen op de lokale PostgreSQL-database en de nepbron."""

from datetime import UTC, datetime, timedelta

import httpx
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
from mso.connectors.sabnzbd import SabError
from mso.db import jsonb, migrate_down, migrate_up, schema_version

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


def test_rollback_behoudt_tussentijdse_bronwijziging(client, db, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    upload = "pref:up_limit"
    source.values[upload] = 50
    ch = changes.create(ch["target"], "Twee limieten", [*ch["ops"], {"path": upload, "before": 50, "after": 40}],
                        risk=ch["risk"])
    applied = client.post(PREFIX + "/apply", json=_execution(ch, key), headers=_connector_headers())
    assert applied.status_code == 200
    original = source.applier()
    writes_before = len(source.writes)

    def write(path, value):
        original.write(path, value)
        if (path, value) == (upload, 50):
            source.values[HUB_RATE_PATH] = 37  # Handmatige wijziging vóór de volgende rollbackoperatie.

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(original.read, write))
    body, headers = _execution(applied.json(), key, rollback=True), _connector_headers()
    rollback = client.post(PREFIX + "/rollback", json=body, headers=headers)
    assert source.values[HUB_RATE_PATH] == 37
    assert rollback.status_code == 200
    assert rollback.json()["readback"] == {"verified": False, "results": ["overgeslagen", "teruggedraaid"]}
    assert changes.get(ch["id"])["ops"][0]["current"] == 37
    replay = client.post(PREFIX + "/rollback", json=body, headers=headers)
    assert replay.json() == rollback.json() and replay.headers["Idempotent-Replay"] == "true"
    assert source.writes[writes_before:] == [(upload, 50)]


@pytest.mark.parametrize("reason,status,code", [("afwijking", 412, "precondition_failed"),
                                               ("verval", 409, "approval_expired")])
def test_tussentijdse_bronwijziging_of_verval_voor_eerste_rollbackactie(
    reason, status, code, client, db, database_bytes, signed_change, monkeypatch,
):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    applied = client.post(PREFIX + "/apply", json=_execution(ch, key), headers=_connector_headers())
    assert applied.status_code == 200
    original = source.applier()
    now = datetime.now(UTC).replace(microsecond=0)
    body = _execution(applied.json(), key, rollback=True, now=now)
    reads = 0

    def read(path):
        nonlocal reads
        reads += 1
        if reads == 2:
            if reason == "afwijking":
                source.values[path] = 37
            else:
                monkeypatch.setattr(changes, "_now", lambda: now + timedelta(minutes=16))
        return original.read(path)

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(read, original.write))
    before, writes = database_bytes(db), list(source.writes)
    fout(client.post(PREFIX + "/rollback", json=body, headers=_connector_headers()), status, code)
    assert reads == 2 and source.writes == writes
    assert database_bytes(db) == before


@pytest.mark.parametrize("timeout", [TimeoutError(), httpx.ReadTimeout("test"), SabError("test", "timeout")],
                         ids=["bron", "http", "sabnzbd"])
def test_vertraagde_bronactie_wordt_eenmaal_bevestigd(timeout, client, db, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    original = source.applier()
    received = []

    def write(path, value):
        received.append((path, value))
        raise timeout

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(original.read, write))
    body, headers = _execution(ch, key), _connector_headers()
    assert client.post(PREFIX + "/apply", json=body, headers=headers).status_code == 502
    assert source.values[HUB_RATE_PATH] == 100 and source.writes == []
    assert changes.get(ch["id"])["status"] == "applying"
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "wacht"}
    fout(client.post(PREFIX + "/apply", json=body, headers=headers), 409, "idempotency_in_progress")
    assert _applied_count(db, ch) == 0

    original.write(*received[0])  # De bron verwerkt de eerder ontvangen opdracht.
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "afgerond"}
    replay = client.post(PREFIX + "/apply", json=body, headers=headers)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replay"] == "true"
    assert replay.json()["status"] == "applied" and replay.json()["readback"]["verified"]
    assert received == source.writes == [(HUB_RATE_PATH, 80)]
    assert _applied_count(db, ch) == 1 and changes.reconcile_interrupted()["checked"] == 0


def _vertraagde_limieten(client, signed_change, monkeypatch, extra_ops):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    source.values.update({op["path"]: op["before"] for op in extra_ops})
    ch = changes.create(ch["target"], "Lokale testlimieten", [*ch["ops"], *extra_ops], risk=ch["risk"])
    original = source.applier()
    received = []

    def write(path, value):
        received.append((path, value))
        if (path, value) == (HUB_RATE_PATH, 80):
            raise TimeoutError("De lokale bron verwerkt de eerste opdracht later.")
        original.write(path, value)

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(original.read, write))
    assert client.post(PREFIX + "/apply", json=_execution(ch, key), headers=_connector_headers()).status_code == 502
    assert received == [(HUB_RATE_PATH, 80)] and changes.get(ch["id"])["status"] == "applying"
    original.write(*received[0])
    return ch, source, received


@pytest.mark.parametrize("derde_waarde", [30, 20])
def test_herstel_slaat_onverstuurde_limieten_over(derde_waarde, client, signed_change, monkeypatch):
    upload, alternatief = "pref:up_limit", "pref:alt_dl_limit"
    ch, source, received = _vertraagde_limieten(client, signed_change, monkeypatch, [
        {"path": upload, "before": 50, "after": 40}, {"path": alternatief, "before": 30, "after": 20},
    ])
    source.values.update({upload: 40, alternatief: derde_waarde})
    uitkomst = changes.reconcile_interrupted()
    assert source.values[upload] == 40 and source.values[alternatief] == derde_waarde
    assert received == [(HUB_RATE_PATH, 80), (HUB_RATE_PATH, 100)]
    assert uitkomst["outcomes"] == {str(ch["id"]): "teruggezet"}
    einde = changes.get(ch["id"])
    assert einde["status"] == "failed"
    assert [op["result"] for op in einde["ops"]] == ["teruggedraaid", "niet_uitgevoerd", "niet_uitgevoerd"]


def test_herstel_bewaart_handmatige_waarde_na_eerste_lezing(client, signed_change, monkeypatch):
    ch, source, received = _vertraagde_limieten(client, signed_change, monkeypatch, [
        {"path": "pref:up_limit", "before": 50, "after": 40},
    ])
    applier = changes.APPLIERS[ch["target"]]()
    metingen = []

    def read(path):
        waarde = applier.read(path)
        if path == HUB_RATE_PATH:
            metingen.append(waarde)
            if len(metingen) == 1:
                source.values[path] = 37  # De beheerder schrijft nadat de achtergrondcontrole 80 las.
        return waarde

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(read, applier.write))
    uitkomst = changes.reconcile_interrupted()
    assert source.values[HUB_RATE_PATH] == 37 and received == [(HUB_RATE_PATH, 80)]
    assert metingen[0] == 80 and 37 in metingen[1:]
    assert uitkomst["outcomes"] == {str(ch["id"]): "deels"}
    op = changes.get(ch["id"])["ops"][0]
    assert op["result"] == "overgeslagen" and op["current"] == 37
    assert op["skip_reason"] == "buiten MSO gewijzigd" and changes.reconcile_interrupted()["checked"] == 0


def test_herstel_bewaart_handmatige_limiet_bij_directe_bronfout(client, db, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    upload, alternatief = "pref:up_limit", "pref:alt_dl_limit"
    source.values.update({upload: 50, alternatief: 30})
    ch = changes.create(ch["target"], "Drie testlimieten", [
        *ch["ops"], {"path": upload, "before": 50, "after": 40}, {"path": alternatief, "before": 30, "after": 20},
    ], risk=ch["risk"])
    original = source.applier()

    def write(path, value):
        if path == alternatief:
            source.values[HUB_RATE_PATH] = 37
            raise changes.SourceWriteRefused("De lokale bron weigert de laatste limiet.")
        original.write(path, value)

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(original.read, write))
    assert client.post(PREFIX + "/apply", json=_execution(ch, key), headers=_connector_headers()).status_code == 502
    assert source.values[HUB_RATE_PATH] == 37 and source.values[upload] == 50
    assert source.writes == [(HUB_RATE_PATH, 80), (upload, 40), (upload, 50)]
    op, hersteld, _ = changes.get(ch["id"])["ops"]
    assert op["result"] == "overgeslagen" and op["current"] == 37
    detail = db.scalar("SELECT detail FROM audit_log WHERE change_id=%s AND action='change.auto_restored'", (ch["id"],))
    assert detail["skipped"] == [op["label"]] and detail["values"] == [{"label": hersteld["label"], "back_to": 50}]


@pytest.mark.parametrize("achtergrondmeting", [False, True])
def test_oud_journaal_zonder_schrijfbewijs_laat_handmatige_limiet_staan(
    achtergrondmeting, client, db, signed_change, monkeypatch,
):
    upload = "pref:up_limit"
    ch, source, received = _vertraagde_limieten(client, signed_change, monkeypatch, [
        {"path": upload, "before": 50, "after": 40}, {"path": "pref:alt_dl_limit", "before": 30, "after": 20},
    ])
    ops = changes.get(ch["id"])["ops"]
    for op in ops:
        op.pop("write_started", None)
    if achtergrondmeting:
        # Een oude controle in leesmodus kon beide gelijke bronwaarden als toegepast opslaan.
        for op in ops[:2]:
            op.update(result="toegepast", observed_after=op["after"], checked_at=changes._now_iso(),
                      verified_at=changes._now_iso())
        ops[2].update(result="niet_toegepast", observed_after=30, checked_at=changes._now_iso())
    db.execute("UPDATE change_sets SET ops=%s, apply_started_at=now()-interval '61 minutes' WHERE id=%s",
               (jsonb(ops), ch["id"]))
    source.values[upload] = 40
    uitkomst = changes.reconcile_interrupted()
    assert source.values[upload] == 40 and received == [(HUB_RATE_PATH, 80)]
    assert uitkomst["outcomes"] == {str(ch["id"]): "onduidelijk"} and changes.get(ch["id"])["status"] == "failed"
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key=%s AND status='open'",
                     (f"change.interrupted:{ch['id']}",)) == 1


@pytest.mark.parametrize("resultaat", ["onbevestigd", "toegepast"])
def test_oud_journaal_met_schrijfbewijs_bevestigt_de_bronactie(resultaat, client, db, signed_change, monkeypatch):
    ch, source, received = _vertraagde_limieten(client, signed_change, monkeypatch, [])
    ops = changes.get(ch["id"])["ops"]
    ops[0].pop("write_started", None)
    if resultaat == "toegepast":
        ops[0].update(result=resultaat, observed_after=80, verified_at=changes._now_iso())
    db.execute("UPDATE change_sets SET ops=%s WHERE id=%s", (jsonb(ops), ch["id"]))
    assert changes.reconcile_interrupted()["outcomes"] == {str(ch["id"]): "afgerond"}
    assert changes.get(ch["id"])["status"] == "applied" and _applied_count(db, ch) == 1
    assert source.writes == received == [(HUB_RATE_PATH, 80)]


@pytest.mark.parametrize("unreachable", [True, False], ids=["onbereikbaar", "oude_waarde"])
def test_overdracht_na_een_uur_zonder_bronbevestiging(unreachable, client, db, signed_change, monkeypatch):
    ch, source, key = signed_change
    _pin(key, monkeypatch)
    original = source.applier()
    received = []
    readable = True

    def write(path, value):
        nonlocal readable
        received.append((path, value))
        if unreachable:
            original.write(path, value)
            readable = False
        raise TimeoutError("De bron heeft geen antwoord gegeven.")

    def read(path):
        if not readable:
            raise TimeoutError("De bron is onbereikbaar.")
        return original.read(path)

    monkeypatch.setitem(changes.APPLIERS, ch["target"], lambda: changes.Applier(read, write))
    body, headers = _execution(ch, key), _connector_headers()
    assert client.post(PREFIX + "/apply", json=body, headers=headers).status_code == 502
    number = ch["id"]
    db.execute("UPDATE change_sets SET apply_started_at=now()-interval '59 minutes' WHERE id=%s", (number,))
    assert changes.reconcile_interrupted()["outcomes"] == {str(number): "wacht"}
    assert changes.get(number)["status"] == "applying"
    db.execute("UPDATE change_sets SET apply_started_at=now()-interval '61 minutes' WHERE id=%s", (number,))
    assert changes.reconcile_interrupted()["outcomes"] == {str(number): "onduidelijk"}
    assert changes.get(number)["status"] == "failed"
    assert db.scalar("SELECT count(*) FROM recommendations WHERE dedupe_key=%s AND status='open'",
                     (f"change.interrupted:{number}",)) == 1
    readable = True
    assert changes.reconcile_interrupted()["checked"] == 0
    error = client.post(PREFIX + "/apply", json=body, headers=headers)
    replay = client.post(PREFIX + "/apply", json=body, headers=headers)
    assert error.status_code == replay.status_code == 502
    assert replay.json() == error.json() and replay.headers["Idempotent-Replay"] == "true"
    assert received == [(HUB_RATE_PATH, 80)] and _applied_count(db, ch) == 0
    assert source.writes == (received if unreachable else [])


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
