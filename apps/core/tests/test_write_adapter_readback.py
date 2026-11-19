"""Meet alleen expliciete, geldige bronwaarden via de geregistreerde schrijfadapters."""

from types import SimpleNamespace

import pytest

from mso import changes
from mso.connectors import prowlarr, qbittorrent, sabnzbd
from mso.features.indexers import prowlarr_sync
from mso.features.recovery import fallback
from mso.features.torrent import adapters, cleanup, policy
from mso.features.torrent import applier as torrent_applier
from mso.features.usenet import retry

HASH = "a" * 40
STATUS = ("ontbreekt", "ongeldig", "expliciet")


@pytest.mark.parametrize("actie", ["recheck", "reannounce"])
def test_qbittorrent_actie_vergt_aanwezige_torrent(monkeypatch, actie):
    monkeypatch.setattr(adapters, "get_adapter", lambda _: SimpleNamespace(has_torrent=lambda _: False))
    monkeypatch.setattr(torrent_applier, "_action_done", lambda *_: True)
    with pytest.raises(ValueError):
        torrent_applier.build().read(f"torrent:{HASH}:{actie}")


def bronwaarde(document, veld, geval, geldig, ongeldig):
    if geval == "ontbreekt":
        document.pop(veld, None)
    else:
        document[veld] = ongeldig if geval == "ongeldig" else geldig


def meet(lezen, pad, geval, verwacht):
    if geval == "expliciet":
        assert lezen(pad) == verwacht
    else:
        with pytest.raises((ValueError, sabnzbd.SabError, qbittorrent.QbitError, prowlarr.ProwlarrError),
                           match="(?i)onleesbaar|limiet"):
            lezen(pad)


def sab_bron(monkeypatch, antwoord):
    bron = object.__new__(sabnzbd.SabClient)
    monkeypatch.setattr(bron, "_call", antwoord)
    monkeypatch.setattr(bron, "close", lambda: None)
    monkeypatch.setattr(sabnzbd.SabClient, "from_config", lambda: bron)


@pytest.mark.parametrize("veld", sabnzbd.SERVER_FIELDS)
@pytest.mark.parametrize("geval", STATUS)
def test_sab_servermeting(monkeypatch, veld, geval):
    server = {"name": "proefserver"}
    bronwaarde(server, veld, geval, "0", 0.9)
    sab_bron(monkeypatch, lambda *_: {"config": {"servers": [server]}})
    meet(changes.APPLIERS["sabnzbd"]().read, f"servers/proefserver/{veld}", geval, 0)


@pytest.mark.parametrize("geval", STATUS)
def test_sab_categoriemeting(monkeypatch, geval):
    categorie = {"name": "proef", "order": 0}
    bronwaarde(categorie, "dir", geval, "0", 0)
    sab_bron(monkeypatch, lambda *_: {"config": {"categories": [categorie]}})
    meet(changes.APPLIERS[sabnzbd.CATEGORY_TARGET]().read, "categories/proef", geval, "aanwezig · map 0")


@pytest.mark.parametrize("veld", ["nzo_id", "status"])
@pytest.mark.parametrize("geval", STATUS)
def test_sab_retrymeting(monkeypatch, veld, geval):
    slot = {"nzo_id": "0", "status": "Completed"}
    bronwaarde(slot, veld, geval, slot[veld], 0)
    antwoorden = {"queue": {"queue": {"slots": [], "noofslots": 0}},
                  "history": {"history": {"slots": [slot], "noofslots": 1}}}
    sab_bron(monkeypatch, lambda mode, *_: antwoorden[mode])
    monkeypatch.setattr(retry, "_row", lambda _: {"retried_at": True})
    meet(changes.APPLIERS[sabnzbd.RETRY_TARGET]().read, "retry/0", geval, sabnzbd.RETRY_AFTER)


@pytest.mark.parametrize(
    ("pad", "veld", "geldig", "ongeldig"),
    [
        ("pref:up_limit", "up_limit", 0, True),
        ("pref:queueing_enabled", "queueing_enabled", False, 0),
        ("pref:max_ratio", "max_ratio", 0, float("nan")),
        ("pref:add_trackers", "add_trackers", "0", 0),
        ("pref:excluded_file_names", "excluded_file_names", "0", [0]),
        ("transfer:alt_speed", None, False, "2"),
        (f"torrent:{HASH}:up_limit", "up_limit", 0, 0.9),
        (f"torrent:{HASH}:dl_limit", "dl_limit", 0, True),
        (f"torrent:{HASH}:share_limits", "ratio_limit", 0, "0"),
        (f"torrent:{HASH}:share_limits", "seeding_time_limit", 0, 0.9),
        (f"torrent:{HASH}:share_limits", "inactive_seeding_time_limit", 0, True),
    ],
)
@pytest.mark.parametrize("geval", STATUS)
def test_qbittorrentmeting(monkeypatch, pad, veld, geldig, ongeldig, geval):
    qb = object.__new__(qbittorrent.QbitClient)
    antwoord = {"hash": HASH, "ratio_limit": -2.0, "seeding_time_limit": -2, "inactive_seeding_time_limit": -2}
    verwacht = geldig
    if veld is not None:
        bronwaarde(antwoord, veld, geval, geldig, ongeldig)
        if pad.endswith(":share_limits"):
            verwacht = {k: v for k, v in antwoord.items() if k != "hash"}
    tekst = {"ontbreekt": "", "ongeldig": "2", "expliciet": "0"}[geval]
    monkeypatch.setattr(qb, "_request", lambda *_: SimpleNamespace(text=tekst))
    monkeypatch.setattr(qb, "cap", lambda _: True)
    monkeypatch.setattr(qb, "_get_json", lambda route, **_: [antwoord] if route == "torrents/info" else antwoord)
    monkeypatch.setattr(adapters, "get_adapter", lambda _: adapters.QbitAdapter(qb))
    meet(torrent_applier.build().read, pad, geval, verwacht)


@pytest.mark.parametrize(
    ("veld", "ongeldig"), [("name", 0), ("implementation", 0), ("baseUrl", 0), ("apiPath", 0), ("enable", 0)],
)
@pytest.mark.parametrize("geval", STATUS)
def test_prowlarrmeting(monkeypatch, veld, ongeldig, geval):
    monkeypatch.setattr(prowlarr_sync.keys, "public_base_url", lambda: "https://mso.example")
    reg = prowlarr_sync.desired_registration("newznab") | {"enable": False}
    record = {k: reg[k] for k in ("name", "implementation", "enable")}
    record["id"] = 1
    velden = {k: {"name": k, "value": reg[k]} for k in ("baseUrl", "apiPath")}
    if veld in velden:
        bronwaarde(velden, veld, geval, velden[veld], {"name": veld, "value": ongeldig})
    else:
        bronwaarde(record, veld, geval, reg[veld], ongeldig)
    record["fields"] = list(velden.values())
    bron = object.__new__(prowlarr.ProwlarrClient)
    monkeypatch.setattr(bron, "request", lambda *_: [record])
    monkeypatch.setattr(prowlarr.ProwlarrClient, "from_config", lambda: bron)
    meet(changes.APPLIERS["prowlarr"]().read, "indexer:newznab", geval, prowlarr_sync.summary(reg))


@pytest.mark.parametrize("status", ["sent", "download_ok", "download_failed"])
@pytest.mark.parametrize("geval", STATUS)
def test_herstelverzendingmeting(monkeypatch, geval, status):
    slot = {}
    bronwaarde(slot, "nzo_id", geval, "0", 0)
    antwoorden = {"queue": {"queue": {"slots": [slot], "noofslots": 1}},
                  "history": {"history": {"slots": [], "noofslots": 0}}}
    sab_bron(monkeypatch, lambda mode, *_: antwoorden[mode])
    rij = {"status": status, "client": "sabnzbd", "client_ref": "0"}
    monkeypatch.setattr(fallback, "get_db", lambda: SimpleNamespace(fetchone=lambda *_: rij))
    monkeypatch.setattr(fallback, "parse_path", lambda _: ("proef", "kandidaat"))
    meet(changes.APPLIERS[fallback.TARGET]().read, "proef", geval, fallback.AFTER)


@pytest.mark.parametrize("geval", STATUS)
def test_qbittorrent_delegatie_meet_wachtrijtotaal(monkeypatch, geval):
    wachtrij = {"records": [], "page": 1, "pageSize": 200}
    bronwaarde(wachtrij, "totalRecords", geval, 0, False)
    bron = SimpleNamespace(get=lambda route, *_: [{"id": "proef"}] if route.endswith("instances") else wachtrij)
    token = "a" * 16
    monkeypatch.setattr(cleanup, "_mg_client", lambda: bron)
    meet(torrent_applier.build().read, f"arrblock:{token}:{HASH}", geval, "niet_in_wachtrij")


@pytest.mark.parametrize(("versie", "oud"), [
    ("2.8.3", True), ("2.9.1", True), ("2.9.2", False), ("2.16.2", False),
    ("", False), ("onleesbaar", False), ("2.8.3-fout", False), ("2.8.x", False), (None, False),
])
def test_qbittorrent_oude_webapi_meet_alleen_ondersteunde_deelgrenzen(monkeypatch, versie, oud):
    bron = object.__new__(qbittorrent.QbitClient)
    bron.capabilities = {"inactive_seeding_time_limit": False}  # eerder gezien, kan inmiddels gewijzigd zijn
    deelgrenzen = {"ratio_limit": -2, "seeding_time_limit": 0}
    monkeypatch.setattr(bron, "torrent", lambda _: deelgrenzen)

    def antwoord(_, route):
        assert route == "app/webapiVersion"
        if versie is None:
            raise qbittorrent.QbitUnsupported("versieroute ontbreekt")
        return SimpleNamespace(text=versie)

    monkeypatch.setattr(bron, "_request", antwoord)
    adapter = adapters.QbitAdapter(bron)
    pad = f"torrent:{HASH}:share_limits"
    if oud:
        assert adapter.read_value(pad) == deelgrenzen
    else:
        with pytest.raises(ValueError, match="onleesbaar"):
            adapter.read_value(pad)
    deelgrenzen["inactive_seeding_time_limit"] = 0
    assert adapter.read_value(pad) == deelgrenzen


@pytest.mark.parametrize(("veld", "geldig", "ongeldig"), [
    *((veld, -1, -2) for veld in (*policy.RATE_PREFS, *policy.GUARDED_PREFS,
                                  "max_active_downloads", "max_active_uploads", "max_active_torrents",
                                  "max_seeding_time")),
    ("max_ratio", -1, -0.5), ("max_ratio_act", 3, 4),
    ("async_io_threads", 1, -1), ("hashing_threads", 1, -1), ("disk_queue_size", 65536, -1),
    *((veld, False, 0) for veld, meta in policy.ALLOWED_PREFS.items() if meta["kind"] == "bool"),
    ("add_trackers", "", False), ("excluded_file_names", [], ["*.voorbeeld", None]),
])
def test_qbittorrent_voorkeurgrenzen(veld, geldig, ongeldig):
    prefs = {veld: geldig}
    adapter = adapters.QbitAdapter(SimpleNamespace(preferences=lambda: prefs))
    pad = f"pref:{veld}"
    assert adapter.read_value(pad) == geldig
    prefs[veld] = ongeldig
    with pytest.raises(ValueError, match="onleesbaar"):
        adapter.read_value(pad)
    del prefs[veld]
    with pytest.raises(ValueError, match="onleesbaar"):
        adapter.read_value(pad)


@pytest.mark.parametrize(("veld", "geldig", "ongeldig"), [
    ("up_limit", -1, -2), ("dl_limit", 0, -3), ("ratio_limit", 0.5, -1.5),
    ("seeding_time_limit", -1, -3), ("inactive_seeding_time_limit", -2, -3),
])
def test_qbittorrent_torrentgrenzen(veld, geldig, ongeldig):
    torrent = {"ratio_limit": -2, "seeding_time_limit": 0, "inactive_seeding_time_limit": -1}
    adapter = adapters.QbitAdapter(SimpleNamespace(torrent=lambda _: torrent))
    limiet = veld in ("up_limit", "dl_limit")
    pad = f"torrent:{HASH}:{veld if limiet else 'share_limits'}"
    torrent[veld] = geldig
    assert adapter.read_value(pad) == (geldig if limiet else torrent)
    torrent[veld] = ongeldig
    with pytest.raises(ValueError, match="onleesbaar"):
        adapter.read_value(pad)


@pytest.mark.parametrize(("doel", "bron", "gelijk"), [
    (0, -1, True), (-1, 0, True), (0, -3, False), (-2, -2, False), (0, False, False),
    (1025, 1024, True), (2047, 1024, True), (2048, 1024, False), (4096, 4097, False),
    (4099, 4097, False), (1, 0, False), (4097, 4097, True), (1024, 1024.5, False),
])
def test_qbittorrent_vergelijking_van_limieten(doel, bron, gelijk):
    assert policy.rate_matches(doel, bron) is gelijk


@pytest.mark.parametrize("veld", sabnzbd.SERVER_FIELDS)
def test_sab_servergrenzen(monkeypatch, veld):
    onder, boven = sabnzbd.SERVER_FIELDS[veld]
    server = {"name": "proef"}
    sab_bron(monkeypatch, lambda *_: {"config": {"servers": [server]}})
    lees = changes.APPLIERS["sabnzbd"]().read
    for waarde in (onder, boven):
        server[veld] = str(waarde)
        assert lees(f"servers/proef/{veld}") == waarde
    for waarde in (onder - 1, boven + 1, False, None):
        server[veld] = waarde
        with pytest.raises(sabnzbd.SabError, match="onleesbaar"):
            lees(f"servers/proef/{veld}")


@pytest.mark.parametrize("bronlijst", ["queue", "history"])
@pytest.mark.parametrize(("velden", "actief", "weigeren"), [
    ({"cat": "proef"}, True, True), ({"category": "proef"}, True, True),
    ({"cat": "proef", "category": ""}, True, True), ({"cat": "ander", "category": "proef"}, True, True),
    ({"cat": "ander", "category": "derde"}, True, True), ({"cat": "ander", "category": None}, True, True),
    ({}, True, True), ({"cat": "ander", "category": "ANDER"}, True, False),
    ({"category": "proef"}, False, False),
])
def test_sab_categorie_verwijdering(monkeypatch, bronlijst, velden, actief, weigeren):
    slot = {"nzo_id": "taak", "status": "Extracting" if actief else "Completed", **velden}
    antwoorden = {mode: {mode: {"slots": [], "noofslots": 0}} for mode in ("queue", "history")}
    antwoorden[bronlijst][bronlijst] = {"slots": [slot], "noofslots": 1}
    antwoorden["get_config"] = {"config": {"categories": [{"name": "proef", "dir": "proef", "order": 0}]}}
    sab_bron(monkeypatch, lambda mode, *_: antwoorden[mode])
    verwijderd = []
    monkeypatch.setattr(sabnzbd.SabClient, "delete_category", lambda _, naam: verwijderd.append(naam))
    schrijf = changes.APPLIERS[sabnzbd.CATEGORY_TARGET]().write
    if weigeren or (bronlijst == "queue" and not actief):
        with pytest.raises(sabnzbd.SabError):
            schrijf("categories/proef", sabnzbd.CATEGORY_ABSENT)
        assert verwijderd == []
    else:
        schrijf("categories/proef", sabnzbd.CATEGORY_ABSENT)
        assert verwijderd == ["proef"]


@pytest.mark.parametrize("doel", [sabnzbd.CATEGORY_TARGET, sabnzbd.RETRY_TARGET, fallback.TARGET])
@pytest.mark.parametrize("lijst", ["queue", "history"])
@pytest.mark.parametrize("geval", ["pagina-twee", "onvolledig", "dubbel", "totaal-wijzigt", "geen-totaal"])
def test_sab_paginering_per_schrijfadapter(monkeypatch, doel, lijst, geval):
    verzoeken = []
    verwijderd = []
    slot = {"nzo_id": "ander", "cat": "ander", "status": "Completed"}
    doelstatus = "Extracting" if doel == sabnzbd.CATEGORY_TARGET else "Completed"
    geraakt = {"nzo_id": "taak", "cat": "proef", "status": doelstatus}

    def antwoord(mode, params):
        if mode == "get_config":
            return {"config": {"categories": [{"name": "proef", "dir": "proef", "order": 0}]}}
        if mode != lijst:
            return {mode: {"slots": [], "noofslots": 0}}
        start = params["start"]
        verzoeken.append(start)
        if geval == "geen-totaal":
            return {mode: {"slots": [geraakt]}}
        if start == 0:
            return {mode: {"slots": [slot if geval == "pagina-twee" else geraakt], "noofslots": "2"}}
        return {mode: {"slots": [] if geval == "onvolledig" else [geraakt],
                       "noofslots": 1 if geval == "totaal-wijzigt" else 2}}

    sab_bron(monkeypatch, antwoord)
    monkeypatch.setattr(sabnzbd.SabClient, "delete_category", lambda _, naam: verwijderd.append(naam))
    monkeypatch.setattr(retry, "_row", lambda _: {"retried_at": True})
    verzending = {"status": "sent", "client": "sabnzbd", "client_ref": "taak"}
    monkeypatch.setattr(fallback, "get_db", lambda: SimpleNamespace(fetchone=lambda *_: verzending))
    monkeypatch.setattr(fallback, "parse_path", lambda _: ("proef", "kandidaat"))
    adapter = changes.APPLIERS[doel]()
    if doel == sabnzbd.CATEGORY_TARGET:
        with pytest.raises(sabnzbd.SabError, match="in gebruik" if geval == "pagina-twee" else "onleesbaar"):
            adapter.write("categories/proef", sabnzbd.CATEGORY_ABSENT)
        assert verwijderd == []
    else:
        pad = "retry/taak" if doel == sabnzbd.RETRY_TARGET else "proef"
        if geval == "pagina-twee":
            assert adapter.read(pad) == (sabnzbd.RETRY_AFTER if doel == sabnzbd.RETRY_TARGET else fallback.AFTER)
        else:
            with pytest.raises(sabnzbd.SabError, match="onleesbaar"):
                adapter.read(pad)
    assert verzoeken == ([0] if geval == "geen-totaal" else [0, 1])


@pytest.mark.parametrize("geval", [
    "pagina-twee", "afwezig", "afgekapt", "dubbel", "gewijzigd-totaal", "gewijzigde-grootte",
    "verkeerde-pagina", "geen-totaal", "geen-grootte", "geen-pagina", "ongeldige-id", "losse-lijst",
])
def test_qbittorrent_wachtrij_na_schrijfantwoord(monkeypatch, geval):
    paginas = []
    opdrachten = []
    rijen = [{"id": 1, "downloadId": "b" * 40}, {"id": 2, "downloadId": HASH}]

    def antwoord(route, params=None):
        if route.endswith("/instances"):
            return [{"id": "proef", "archived": False}]
        if not opdrachten:
            return {"page": 1, "pageSize": 200, "totalRecords": 2, "records": rijen}
        pagina = params["page"]
        paginas.append((pagina, params["page_size"]))
        record = dict(rijen[pagina - 1])
        if geval == "afwezig":
            record["downloadId"] = None
        data = {"page": pagina, "pageSize": 1, "totalRecords": 2, "records": [record]}
        if geval == "afgekapt":
            data["pageSize"] = 200
        if geval == "dubbel" and pagina == 2:
            data["records"] = [rijen[0]]
        if geval == "gewijzigd-totaal" and pagina == 2:
            data["totalRecords"] = 1
        if geval == "gewijzigde-grootte" and pagina == 2:
            data["pageSize"] = 2
        if geval == "verkeerde-pagina":
            data["page"] = True
        if geval == "ongeldige-id":
            record["id"] = 0
        for variant, veld in (("geen-totaal", "totalRecords"), ("geen-grootte", "pageSize"), ("geen-pagina", "page")):
            if geval == variant:
                del data[veld]
        return data["records"] if geval == "losse-lijst" else data

    monkeypatch.setattr(cleanup, "_mg_client", lambda: SimpleNamespace(get=antwoord))
    monkeypatch.setattr(cleanup, "mediaguard_block", lambda _, rec: opdrachten.append(rec["queue_id"]))
    pad = f"arrblock:{'c' * 16}:{HASH}"
    adapter = torrent_applier.build()
    adapter.write(pad, "geblokkeerd")
    assert opdrachten == [2]
    if geval in ("pagina-twee", "afwezig"):
        assert adapter.read(pad) == ("in_wachtrij" if geval == "pagina-twee" else "geblokkeerd")
        assert paginas == [(1, 200), (2, 1)]
    else:
        with pytest.raises(policy.PolicyError):
            adapter.read(pad)


@pytest.mark.parametrize("geval", ["leeg", "afgekapt", "dubbele-registratie", "dubbel-veld", "ongeldige-url"])
def test_prowlarr_registratielijst(monkeypatch, geval):
    monkeypatch.setattr(prowlarr_sync.keys, "public_base_url", lambda: "https://mso.example")
    reg = prowlarr_sync.desired_registration("newznab")
    record = {k: reg[k] for k in ("name", "implementation", "enable")}
    record.update(id=1, fields=[{"name": k, "value": reg[k]} for k in ("baseUrl", "apiPath")])
    payload = [record]
    if geval == "leeg":
        payload = []
    elif geval == "afgekapt":
        payload = {"records": [record], "totalRecords": 2}
    elif geval == "dubbele-registratie":
        payload.append(dict(record, id=2))
    elif geval == "dubbel-veld":
        record["fields"].append({"name": "apiPath", "value": "/ander"})
    else:
        record["fields"][0]["value"] = "onleesbaar"
    bron = object.__new__(prowlarr.ProwlarrClient)
    monkeypatch.setattr(bron, "request", lambda *_: payload)
    monkeypatch.setattr(prowlarr.ProwlarrClient, "from_config", lambda: bron)
    verwijderd = []
    monkeypatch.setattr(bron, "delete_indexer", verwijderd.append)
    adapter = changes.APPLIERS["prowlarr"]()
    if geval == "leeg":
        assert adapter.read("indexer:newznab") is None
    else:
        with pytest.raises(prowlarr.ProwlarrError, match="onleesbaar"):
            adapter.read("indexer:newznab")
        with pytest.raises(prowlarr.ProwlarrError, match="onleesbaar"):
            adapter.write("indexer:newznab", None)
    assert verwijderd == []


@pytest.mark.parametrize("document", [None, {}, [None], [{}], [{"hash": False}], [{"hash": "b" * 40}],
                                      [{"hash": HASH}, {"hash": HASH}], [], [{"hash": HASH.upper()}]])
def test_qbittorrent_torrentlijst_voor_herstelverzending(monkeypatch, document):
    bron = object.__new__(qbittorrent.QbitClient)
    monkeypatch.setattr(bron, "close", lambda: None)
    monkeypatch.setattr(bron, "torrents", lambda _: document)
    monkeypatch.setattr(qbittorrent.QbitClient, "from_config", lambda: bron)
    monkeypatch.setattr(fallback.actions.time, "sleep", lambda _: None)
    verzending = {"status": "sent", "client": "qbittorrent", "client_ref": HASH}
    monkeypatch.setattr(fallback, "get_db", lambda: SimpleNamespace(fetchone=lambda *_: verzending))
    monkeypatch.setattr(fallback, "parse_path", lambda _: ("proef", "kandidaat"))
    lees = changes.APPLIERS[fallback.TARGET]().read
    if document == [{"hash": HASH.upper()}]:
        assert lees("proef") == fallback.AFTER
    elif document == []:
        assert lees("proef") == "niet gevonden in qBittorrent"
    else:
        with pytest.raises(qbittorrent.QbitError, match="onleesbaar"):
            lees("proef")


@pytest.mark.parametrize(("doel", "sectie", "pad"), [
    ("sabnzbd", "servers", "servers/proef/priority"), (sabnzbd.CATEGORY_TARGET, "categories", "categories/proef"),
])
@pytest.mark.parametrize("geval", ["ontbrekende-lijst", "dubbele-naam", "tegenstrijdige-naam", "lege-naam"])
def test_sab_configuratielijst(monkeypatch, doel, sectie, pad, geval):
    record = {"name": "proef", "dir": "proef", "order": 0, "priority": 0}
    lijst = {"ontbrekende-lijst": None, "dubbele-naam": [record, record],
             "tegenstrijdige-naam": {"ander": record}, "lege-naam": [dict(record, name="")]}[geval]
    sab_bron(monkeypatch, lambda *_: {"config": {sectie: lijst}})
    with pytest.raises(sabnzbd.SabError, match="onleesbaar"):
        changes.APPLIERS[doel]().read(pad)


@pytest.mark.parametrize(("pad", "waarde"), [
    ("pref:dl_limit", -3), (f"torrent:{HASH}:up_limit", -2), ("transfer:alt_speed", "0"),
    (f"torrent:{HASH}:share_limits", {"ratio_limit": -3, "seeding_time_limit": 0}),
])
def test_qbittorrent_ongeldige_journaalwaarde_geeft_geen_terugzetopdracht(pad, waarde):
    opdrachten = []
    bron = SimpleNamespace(speed_limits_mode=lambda: 0, toggle_speed_limits_mode=lambda: opdrachten.append(True),
                           set_preferences=opdrachten.append, set_upload_limit=lambda *args: opdrachten.append(args),
                           set_share_limits=lambda *args, **kwargs: opdrachten.append(kwargs))
    with pytest.raises(ValueError, match="onleesbaar"):
        adapters.QbitAdapter(bron).write_value(pad, waarde)
    assert opdrachten == []


@pytest.mark.parametrize(("versie", "inactief", "schrijven"), [
    ("", None, False), ("2.8.3", None, True), ("2.8.3", 4, False), ("2.9.2", 4, True),
    ("2.16.2", None, False), ("2.16.2", 0, True),
])
def test_qbittorrent_schrijft_alle_gevraagde_deelgrenzen(monkeypatch, versie, inactief, schrijven):
    bron = object.__new__(qbittorrent.QbitClient)
    bron.capabilities = {"share_limits": True, "inactive_seeding_time_limit": False}
    monkeypatch.setattr(bron, "_request", lambda *_: SimpleNamespace(text=versie))
    opdrachten = []
    monkeypatch.setattr(bron, "_post", lambda _, velden: opdrachten.append(velden))
    waarden = {"ratio_limit": -2, "seeding_time_limit": 0, "inactive_seeding_time_limit": inactief}
    if schrijven:
        bron.set_share_limits([HASH], **waarden)
        verwacht = {"hashes": HASH, "ratioLimit": -2, "seedingTimeLimit": 0}
        if inactief is not None:
            verwacht["inactiveSeedingTimeLimit"] = inactief
        assert opdrachten == [verwacht]
    else:
        with pytest.raises(qbittorrent.QbitUnsupported):
            bron.set_share_limits([HASH], **waarden)
        assert opdrachten == []


@pytest.mark.parametrize("lijst", ["queue", "history"])
def test_sab_wachtrij_en_historie_vereisen_een_paginaobject(monkeypatch, lijst):
    pagina = [["slots", []], ["noofslots", 0]]
    antwoorden = {mode: {mode: {"slots": [], "noofslots": 0}} for mode in ("queue", "history")}
    antwoorden[lijst][lijst] = pagina
    antwoorden["get_config"] = {"config": {"categories": [{"name": "proef", "dir": "proef", "order": 0}]}}
    sab_bron(monkeypatch, lambda mode, *_: antwoorden[mode])
    verwijderd = []
    monkeypatch.setattr(sabnzbd.SabClient, "delete_category", lambda _, naam: verwijderd.append(naam))
    with pytest.raises(sabnzbd.SabError, match="onleesbaar"):
        changes.APPLIERS[sabnzbd.CATEGORY_TARGET]().write("categories/proef", sabnzbd.CATEGORY_ABSENT)
    assert verwijderd == []


@pytest.mark.parametrize("geval", STATUS)
def test_sab_categorievolgorde(monkeypatch, geval):
    record = {"name": "ander", "dir": ""}
    bronwaarde(record, "order", geval, "0", False)
    sab_bron(monkeypatch, lambda *_: {"config": {"categories": [record]}})
    opdrachten = []
    monkeypatch.setattr(sabnzbd.SabClient, "create_category", lambda _, naam, mapnaam, order:
                        opdrachten.append((naam, mapnaam, order)))
    schrijf = changes.APPLIERS[sabnzbd.CATEGORY_TARGET]().write
    if geval == "expliciet":
        schrijf("categories/proef", sabnzbd.category_present("proef"))
        assert opdrachten == [("proef", "proef", 1)]
    else:
        with pytest.raises(sabnzbd.SabError, match="onleesbaar"):
            schrijf("categories/proef", sabnzbd.category_present("proef"))
        assert opdrachten == []
