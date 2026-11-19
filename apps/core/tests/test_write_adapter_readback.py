"""Meet alleen expliciete, geldige bronwaarden via de geregistreerde schrijfadapters."""

from types import SimpleNamespace

import pytest

from mso import changes
from mso.connectors import prowlarr, qbittorrent, sabnzbd
from mso.features.indexers import prowlarr_sync
from mso.features.recovery import fallback
from mso.features.torrent import adapters, cleanup
from mso.features.torrent import applier as torrent_applier
from mso.features.usenet import retry

HASH = "a" * 40
STATUS = ("ontbreekt", "ongeldig", "expliciet")


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
    antwoorden = {"queue": {"queue": {"slots": []}}, "history": {"history": {"slots": [slot]}}}
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


@pytest.mark.parametrize("geval", STATUS)
def test_herstelverzendingmeting(monkeypatch, geval):
    slot = {}
    bronwaarde(slot, "nzo_id", geval, "0", 0)
    antwoorden = {"queue": {"queue": {"slots": [slot]}}, "history": {"history": {"slots": []}}}
    sab_bron(monkeypatch, lambda mode, *_: antwoorden[mode])
    rij = {"status": "sent", "client": "sabnzbd", "client_ref": "0"}
    monkeypatch.setattr(fallback, "get_db", lambda: SimpleNamespace(fetchone=lambda *_: rij))
    monkeypatch.setattr(fallback, "parse_path", lambda _: ("proef", "kandidaat"))
    meet(changes.APPLIERS[fallback.TARGET]().read, "proef", geval, fallback.AFTER)


@pytest.mark.parametrize("geval", STATUS)
def test_qbittorrent_delegatie_meet_wachtrijtotaal(monkeypatch, geval):
    wachtrij = {"records": []}
    bronwaarde(wachtrij, "totalRecords", geval, 0, False)
    bron = SimpleNamespace(get=lambda route, *_: [{"id": "proef"}] if route.endswith("instances") else wachtrij)
    token = "a" * 16
    monkeypatch.setattr(cleanup, "_mg_client", lambda: bron)
    meet(torrent_applier.build().read, f"arrblock:{token}:{HASH}", geval, "niet_in_wachtrij")


def test_qbittorrent_oude_webapi_meet_alleen_ondersteunde_deelgrenzen():
    bron = SimpleNamespace(torrent=lambda _: {"ratio_limit": -2, "seeding_time_limit": 0}, cap=lambda _: False)
    waarden = adapters.QbitAdapter(bron).read_value(f"torrent:{HASH}:share_limits")
    assert waarden == {"ratio_limit": -2, "seeding_time_limit": 0}
