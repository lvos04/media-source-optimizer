"""1.6.0 G2 — fake releases are stopped at MSO's own sends (C20).

- ``releasegate.executable_hint``: programs/scripts/shortcuts and double extensions (``.mkv.exe``) in a name or file
  list, same rules as the post-download scan; ``size_problem``: a ≥ 720p episode < 50 MB / film < 200 MB.
- ``torrent.policy.check_release`` (did not exist: candidates fell back to a seeders-only check): seeders + fake.
- Pre-send check (recovery send, approved/automatic change, Zoeken → Verstuur): .torrent file list
  (``crossseed.parse_torrent``, v2/hybrid too), magnet name, NZB file names → "Beleid weigert deze release: bevat
  uitvoerbaar bestand …", audited, a proven fake is blacklisted, nothing reaches the client.
- Zoeken: flag ``uitvoerbaar`` (ranked last); MSO's own Newznab/Torznab feed leaves fakes out.
Over real HTTP: Torznab/Newznab mock (fake payloads served as real .torrent/NZB), qBittorrent and SABnzbd mocks.
"""

from __future__ import annotations

import hashlib
import types
from typing import Any

import pytest
from mg_support import install_clients, install_search, release
from msomocks import newznab as mock_nz
from slice_a_helpers import NZ_KEY, TZ_KEY, add_direct, search, set_settings

from mso.domain import releasegate as g
from mso.domain.release import parse_title
from mso.features.torrent import crossseed

MB = 1024 * 1024
EXE_TITLE = "Criminal Minds S19E06 1080p WEB h264-GRACE.exe"
SCR_TITLE = "House of the Dragon S03E05 1080p HEVC x265-MeGusta.scr"


# ------------------------------------------------------------------ unit: executable_hint / describe
def test_executable_hint_names_and_files() -> None:
    def hint(title: str, files: list[str] | None = None, **kw: Any) -> list[str]:
        return [g.describe(m) for m in g.executable_hint(title, files, **kw)]

    assert hint(EXE_TITLE) == ["releasenaam is een uitvoerbaar bestand (.exe)"]
    assert hint(SCR_TITLE) == ["releasenaam is een uitvoerbaar bestand (.scr)"]
    assert hint("Show.S01E01.1080p.WEB.h264-GRP.mkv.exe") == [
        "releasenaam vermomt een uitvoerbaar bestand als video (.mkv.exe)"
    ]
    assert hint("Show.S01E01.1080p.WEB.h264-GRP", ["Show/Show.S01E01.mkv.exe", "Show/a.nfo"]) == [
        'bevat uitvoerbaar bestand "Show.S01E01.mkv.exe" (dubbele extensie, vermomd als video)'
    ]
    assert hint("Show.S01E01", [{"name": "Show/Watch Now.lnk"}, {"name": "Show/run.ps1"}]) == [
        'bevat uitvoerbaar bestand "Watch Now.lnk"',
        'bevat uitvoerbaar bestand "run.ps1"',
    ]
    # clean releases, site tags, weak signals (not programs) and software outside media are not "executable"
    assert hint("Show.S01E01.1080p.WEB.h264-GRP", ["Show/Show.S01E01.mkv", "Show/RARBG.txt"]) == []
    assert hint("Movie 2020 1080p - site.com") == []
    assert hint("Show S01E01 1080p WEB.zipx") == []
    assert hint("SomeApp 2.0 Portable", ["SomeApp/setup.exe"]) == []  # not a media download
    assert hint("SomeApp 2.0 Portable", ["SomeApp/setup.exe"], media_category="tv") != []  # media category known
    assert g.is_media_release("Some Show S01E02 720p") and not g.is_media_release("SomeApp 2.0")


def test_payload_problems_include_weak_signals_like_the_post_download_scan() -> None:
    reasons, matches, n = g.payload_problems(
        "Movie.2020.1080p", ["Movie/movie.rar", "Movie/Password.txt", "Movie/extra.zipx"], media=True
    )
    assert n == 3 and len(matches) == 2
    assert reasons == [
        'bevat wachtwoordhint "Password.txt" naast een archief (beveiligd archief)',
        'bevat ongebruikelijk archief "extra.zipx"',
    ]
    assert g.payload_problems("Movie.2020.1080p", ["Movie/movie.mkv", "Movie/Password.txt"], media=True)[0] == []


def test_bluray_disc_menu_programs_are_not_a_fake() -> None:
    """A full Blu-ray disc with BD-J menus always has BDMV/JAR/<id>.jar: not a PC program, never a fake."""
    disc = ["Movie.2020.COMPLETE.BLURAY-GRP/BDMV/index.bdmv", "Movie.2020.COMPLETE.BLURAY-GRP/BDMV/STREAM/00001.m2ts",
            "Movie.2020.COMPLETE.BLURAY-GRP/BDMV/JAR/00000.jar",
            "Movie.2020.COMPLETE.BLURAY-GRP/BDMV/JAR/00000/menu.jar"]
    assert g.executable_hint("Movie.2020.COMPLETE.BLURAY-GRP", disc) == []
    assert g.payload_problems("Movie.2020.COMPLETE.BLURAY-GRP", disc, media=True)[0] == []
    data = mock_nz.torrent_bytes("x", "Movie.2020.COMPLETE.BLURAY-GRP",
                                 files=[(f.split("/", 1)[1], 9) for f in disc])
    assert g.check_download("torrent", _fetched(data), title="Movie.2020.COMPLETE.BLURAY-GRP").files_checked == 4
    # outside a disc layout a .jar is still a program, and a disc does not excuse other programs
    assert g.executable_hint("Movie.2020.1080p", ["Movie/BDMV/JAR/00000.jar"]) != []
    assert [g.describe(m) for m in g.executable_hint("Movie.2020.COMPLETE.BLURAY", [*disc, "Movie/Play.exe"])] == [
        'bevat uitvoerbaar bestand "Play.exe"'
    ]


# ------------------------------------------------------------------ unit: size sanity
@pytest.mark.parametrize(
    "title,size,cat,bad",
    [
        ("Show.S01E01.1080p.WEB.h264-GRP", 12 * MB, "tv", "1080p-aflevering (12 MB; minimaal 50 MB verwacht)"),
        ("Show.S01E01.720p.WEB.h264-GRP", 49 * MB, None, "720p-aflevering"),
        ("[Grp] Sky Blade - 13 (1080p)", 4 * MB, "tv-anime", "1080p-aflevering (4.0 MB"),
        ("Show.S01E01.720p.WEB.h264-GRP", 60 * MB, "tv", None),
        ("Show.S01E01.480p.WEB.h264-GRP", 10 * MB, "tv", None),  # below 720p: not judged
        ("Show.S01.1080p.WEB.h264-GRP", 12 * MB, "tv", None),  # season pack: not judged as one episode
        ("Show.S01E01.WEB.h264-GRP", 12 * MB, "tv", None),  # resolution unknown
        ("Show.S01E01.1080p.WEB.h264-GRP", None, "tv", None),  # size unknown
        ("Movie.2020.1080p.WEB.h264-GRP", 120 * MB, None, "1080p-film (120 MB; minimaal 200 MB verwacht)"),
        ("Movie.2020.2160p.WEB.h265-GRP", 150 * MB, "movies", "2160p-film"),
        ("Movie.2020.1080p.WEB.h264-GRP", 700 * MB, "movies", None),
    ],
)
def test_size_problem(title: str, size: int | None, cat: str | None, bad: str | None) -> None:
    out = g.size_problem(parse_title(title), size, cat)
    if bad is None:
        assert out is None, out
    else:
        assert out is not None and bad in out and out.startswith("onwaarschijnlijk klein voor een"), out


# ------------------------------------------------------------------ unit: torrent policy
def test_check_release_keeps_seeders_and_adds_fake_rules() -> None:
    from mso.features.torrent.policy import check_release

    ok = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-TOR", protocol="torrent", seeders=40)
    assert check_release(ok) == []
    dead = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-TOR", protocol="torrent", seeders=0)
    assert check_release(dead) == ["geen seeders: torrent kan niet worden voltooid"]
    unknown = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-TOR", protocol="torrent", seeders=None)
    assert check_release(unknown) == ["aantal seeders onbekend: beschikbaarheid niet aangetoond"]
    fake = release(EXE_TITLE, protocol="torrent", seeders=99, size_gb=0.003, cat="tv")
    reasons = check_release(fake)
    assert reasons[0] == "releasenaam is een uitvoerbaar bestand (.exe)"
    assert any(r.startswith("onwaarschijnlijk klein voor een 1080p-aflevering") for r in reasons)


# ------------------------------------------------------------------ unit: reading what was fetched
def _v2_torrent(name: str, tree: dict[str, Any], *, v1_files: list[tuple[str, int]] | None = None) -> bytes:
    def node(t: dict[str, Any]) -> dict[str, Any]:
        return {k: ({"": {"length": v, "pieces root": b"\x00" * 32}} if isinstance(v, int) else node(v))
                for k, v in t.items()}

    info: dict[str, Any] = {"meta version": 2, "name": name, "piece length": 16384, "file tree": node(tree)}
    if v1_files:
        info["files"] = [{"length": s, "path": p.split("/")} for p, s in v1_files]
        info["pieces"] = b"\x00" * 20
    return crossseed.bencode({"announce": "http://tracker.invalid/announce", "info": info})


def test_torrent_payload_v1_v2_hybrid_and_unreadable() -> None:
    single = g.torrent_payload(mock_nz.torrent_bytes("x", "Show.S01E01.1080p.WEB.h264-GRP"))
    assert single["files"] == ["Show.S01E01.1080p.WEB.h264-GRP"] and single["keys"][0].startswith("btih:")
    multi = g.torrent_payload(
        mock_nz.torrent_bytes("x", "Show.S01E01", files=[("Show.S01E01.mkv.exe", 9), ("sub/a.nfo", 1)])
    )
    assert multi["files"] == ["Show.S01E01/Show.S01E01.mkv.exe", "Show.S01E01/sub/a.nfo"]
    # v2-only: parse_torrent refuses ('file tree'), the gate still reads the tree
    v2 = _v2_torrent("Show.S01E01", {"Show.S01E01.mkv": 5, "Extras": {"Play.lnk": 1}})
    p2 = g.torrent_payload(v2)
    assert sorted(p2["files"]) == ["Show.S01E01/Extras/Play.lnk", "Show.S01E01/Show.S01E01.mkv"]
    assert [k[:5] for k in p2["keys"]] == ["btmh:"]
    meta, raw = crossseed.bdecode(v2)
    assert p2["keys"] == [f"btmh:{hashlib.sha256(raw).hexdigest()}"]
    hybrid = g.torrent_payload(_v2_torrent("H", {"a.mkv": 5}, v1_files=[("a.mkv", 5)]))
    assert [k[:5] for k in hybrid["keys"]] == ["btih:", "btmh:"]
    for bad in (b"", b"<html>login</html>", b"d8:announce3:abce"):
        with pytest.raises(g.Refused) as exc:
            g.torrent_payload(bad)
        assert not exc.value.content and "niet te controleren" in exc.value.message
        assert exc.value.evidence == {"kind": "unreadable"}


def test_nzb_file_names() -> None:
    nzb = mock_nz.nzb_bytes("x", "T", [("Show.S01E01.mkv.exe", 1), ("Show.S01E01.par2", 1)])
    assert g.nzb_file_names(nzb) == ["Show.S01E01.mkv.exe", "Show.S01E01.par2"]
    assert g.nzb_file_names(mock_nz.nzb_bytes("x", "T")) == []  # obfuscated subject: nothing quoted
    assert g.nzb_file_names(b"not xml") == []
    entity = b'<!DOCTYPE nzb [<!ENTITY a "x.exe">]><nzb><file subject="&quot;&a;&quot;"/></nzb>'
    assert g.nzb_file_names(entity) == []  # entities are never expanded


def _fetched(content: bytes = b"", final_url: str | None = None) -> Any:
    return types.SimpleNamespace(content=content, final_url=final_url, filename=None)


def test_check_download_torrent_magnet_nzb() -> None:
    clean = mock_nz.torrent_bytes("x", "Show.S01E01.1080p.WEB.h264-GRP", files=[("Show.S01E01.mkv", 10)])
    ok = g.check_download("torrent", _fetched(clean), title="Show.S01E01.1080p.WEB.h264-GRP")
    assert ok.kind == "files" and ok.files_checked == 1 and ok.keys[0].startswith("btih:")
    fake = mock_nz.torrent_bytes("x", "Show.S01E01.1080p.WEB.h264-GRP", files=[("x.exe", 10), ("a.mkv", 9)])
    with pytest.raises(g.Refused) as exc:
        g.check_download("torrent", _fetched(fake), title="Show.S01E01.1080p.WEB.h264-GRP")
    assert exc.value.message == 'Beleid weigert deze release: bevat uitvoerbaar bestand "x.exe"'
    assert exc.value.content and exc.value.keys and exc.value.evidence["files_checked"] == 2
    locked = mock_nz.torrent_bytes("x", "Movie.2020.1080p", files=[("movie.rar", 10), ("Password.txt", 1)])
    with pytest.raises(g.Refused, match="wachtwoordhint"):
        g.check_download("torrent", _fetched(locked), title="Movie.2020.1080p")
    # software outside media is not judged (manual search of a non-media result)
    app = mock_nz.torrent_bytes("x", "SomeApp 2.0", files=[("setup.exe", 10)])
    assert g.check_download("torrent", _fetched(app), title="SomeApp 2.0", media=False).kind == "files"
    # magnet: no file list yet → title and dn
    magnet = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Show.S01E01.1080p.WEB.mkv.exe"
    with pytest.raises(g.Refused) as exc:
        g.check_download("torrent", _fetched(final_url=magnet), title="Show.S01E01.1080p.WEB.h264-GRP")
    assert "vermomt een uitvoerbaar bestand als video (.mkv.exe)" in exc.value.message and not exc.value.content
    good = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Show.S01E01.1080p.WEB.h264-GRP"
    assert g.check_download("torrent", _fetched(final_url=good), title="Show.S01E01.1080p.WEB.h264-GRP").kind == (
        "magnet"
    )
    # NZB: title + file names in the subjects
    nzb = mock_nz.nzb_bytes("x", "T", [("Show.S01E01.1080p.mkv.exe", 1)])
    with pytest.raises(g.Refused) as exc:
        g.check_download("usenet", _fetched(nzb), title="Show.S01E01.1080p.WEB.h264-GRP")
    assert exc.value.content and '"Show.S01E01.1080p.mkv.exe"' in exc.value.message
    nzb_ok = mock_nz.nzb_bytes("x", "T", [("Show.S01E01.1080p.part01.rar", 1)])
    assert g.check_download("usenet", _fetched(nzb_ok), title="Show.S01E01.1080p.WEB.h264-GRP").files_checked == 1
    # obfuscated title without a category (Zoeken: media=None): release-like file names still make it media
    with pytest.raises(g.Refused) as exc:
        g.check_download("usenet", _fetched(nzb), title="a8f3c1d9e07b", media=None)
    assert exc.value.content and '"Show.S01E01.1080p.mkv.exe"' in exc.value.message
    app_nzb = mock_nz.nzb_bytes("x", "T", [("setup.exe", 1)])
    assert g.check_download("usenet", _fetched(app_nzb), title="a8f3c1d9e07b", media=None).files_checked == 1


# ------------------------------------------------------------------ candidates (recovery)
def _manual(operator: Any, **kw: Any) -> str:
    r = operator.post("/api/v1/recovery/wanted", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["media_ref"]


def _cands(operator: Any, ref: str) -> dict[str, Any]:
    r = operator.post(f"/api/v1/recovery/items/{ref}/candidates", json={})
    assert r.status_code == 200, r.text
    return r.json()


def test_candidates_reject_fakes_with_evidence_row(operator: Any, monkeypatch: Any) -> None:
    install_search(
        monkeypatch,
        [
            release("Criminal.Minds.S19E06.1080p.WEB.h264-NTb", protocol="torrent", seeders=30, key="real", cat="tv"),
            release(EXE_TITLE, protocol="torrent", seeders=900, key="exe", cat="tv"),
            release("Criminal.Minds.S19E06.1080p.WEB.h264-GRACE", protocol="torrent", seeders=50, key="tiny",
                    size_gb=0.004, cat="tv"),
            release("Criminal.Minds.S19E06.1080p.WEB.h264-GRACE.mkv.exe", key="nzbexe", cat="tv"),  # usenet too
        ],
    )
    ref = _manual(operator, media_category="tv", title="Criminal Minds", season_number=19, episode_number=6)
    res = _cands(operator, ref)
    assert [c["title"] for c in res["accepted"]] == ["Criminal.Minds.S19E06.1080p.WEB.h264-NTb"]
    rej = {c["title"]: c for c in res["rejected"]}
    exe = rej[EXE_TITLE]
    assert exe["reasons"] == ["releasenaam is een uitvoerbaar bestand (.exe)"]
    row = next(e for e in exe["evidence"] if e["key"] == "authenticity")
    assert row["label"] == "Echte release" and row["ok"] is False and row["actual"] == "nep-release"
    pol = next(e for e in exe["evidence"] if e["key"] == "torrent_policy")
    assert pol["ok"] is True and pol["required"] == "torrentbeleid (qBittorrent-module)"  # no duplicate reason
    assert any("onwaarschijnlijk klein" in r for r in rej["Criminal.Minds.S19E06.1080p.WEB.h264-GRACE"]["reasons"])
    assert rej["Criminal.Minds.S19E06.1080p.WEB.h264-GRACE.mkv.exe"]["reasons"] == [
        "releasenaam vermomt een uitvoerbaar bestand als video (.mkv.exe)"
    ]
    good = next(e for e in res["accepted"][0]["evidence"] if e["key"] == "authenticity")
    assert good["ok"] and good["actual"] == "geen programma in de naam · grootte plausibel"


def test_recovery_send_refuses_fake_payload_and_blacklists_it(operator: Any, db: Any, monkeypatch: Any) -> None:
    sneaky = release("Criminal.Minds.S19E06.1080p.WEB.h264-GRACE", protocol="torrent", seeders=50, key="sneaky",
                     cat="tv")
    sneaky.extra["torrent_files"] = [("Criminal.Minds.S19E06.1080p.WEB.h264-GRACE.mkv.exe", 2 * MB), ("x.txt", 9)]
    install_search(monkeypatch, [sneaky])
    sent = install_clients(monkeypatch)
    ref = _manual(operator, media_category="tv", title="Criminal Minds", season_number=19, episode_number=6)
    assert [c["title"] for c in _cands(operator, ref)["accepted"]] == [sneaky.title]  # the title looks clean
    from mso.features.recovery import tasks

    tasks.make_recommendations()
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s", (f"recovery:cand:{ref}",))
    assert rec["status"] == "open" and f"'{sneaky.title}'" in rec["explanation"]
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": sneaky.release_key})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        'Beleid weigert deze release: bevat uitvoerbaar bestand "Criminal.Minds.S19E06.1080p.WEB.h264-GRACE.mkv.exe" '
        "(dubbele extensie, vermomd als video)"
    )
    assert not sent["qbit"]
    row = db.fetchone("SELECT * FROM recovery_sends WHERE media_ref=%s", (ref,))
    assert row["status"] == "failed" and "uitvoerbaar bestand" in row["error"]
    a = db.fetchone("SELECT * FROM audit_log WHERE action='recovery.send_refused'")
    assert a["summary"].startswith("Versturen geweigerd, nep-release") and a["detail"]["checked"]["kind"] == "files"
    payload_key = g.torrent_payload(mock_nz.torrent_bytes(sneaky.release_key, sneaky.title,
                                                          files=sneaky.extra["torrent_files"]))["keys"][0]
    assert set(a["detail"]["blacklisted"]) == {sneaky.release_key, payload_key}
    from mso.features.recovery import blacklist

    assert blacklist.is_blacklisted(sneaky.release_key) and blacklist.is_blacklisted(payload_key)
    entry = db.fetchone("SELECT * FROM recovery_blacklist WHERE release_key=%s", (sneaky.release_key,))
    assert entry["reason"].startswith("nep-release: bevat uitvoerbaar bestand") and entry["media_ref"] == ref
    # the stored candidate is no longer offered as "Voldoet" (item panel) ...
    stored = operator.get(f"/api/v1/recovery/items/{ref}").json()["candidates"]
    assert [(c["release_key"], c["passed"], c["score"]) for c in stored] == [(sneaky.release_key, False, None)]
    assert stored[0]["reasons"][-1].startswith('nep-release: bevat uitvoerbaar bestand "Criminal.Minds')
    auth = next(e for e in stored[0]["evidence"] if e["key"] == "authenticity")
    assert auth["ok"] is False and auth["actual"] == "nep-release (bestanden gecontroleerd vóór verzenden)"
    # ... nor recommended: the open recommendation is rewritten now and not recreated by the next analysis
    rec = db.fetchone("SELECT * FROM recommendations WHERE id=%s", (rec["id"],))
    assert rec["status"] == "open" and rec["title"] == f"Fallback-kandidaat bleek nep-release ({ref})"
    assert "bleek bij de controle vóór verzenden een nep-release (bevat uitvoerbaar bestand" in rec["explanation"]
    assert "Verstuur handmatig" not in rec["explanation"]
    tasks.make_recommendations()
    assert db.fetchone("SELECT title FROM recommendations WHERE id=%s", (rec["id"],))["title"] == rec["title"]
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": sneaky.release_key})
    assert r.status_code == 422 and "blacklist" in r.json()["detail"]
    # never picked again: the next candidate search rejects it as blacklisted
    again = _cands(operator, ref)
    assert not again["accepted"] and any("blacklist" in x for x in again["rejected"][0]["reasons"])


def test_recovery_send_magnet_and_unreadable_torrent(operator: Any, db: Any, monkeypatch: Any) -> None:
    mag = release("Criminal.Minds.S19E06.1080p.WEB.h264-MAG", protocol="torrent", seeders=50, key="mag", cat="tv")
    mag.extra["final_url"] = "magnet:?xt=urn:btih:" + "c" * 40 + "&dn=Criminal.Minds.S19E06.1080p.mkv.scr"
    broken = release("Criminal.Minds.S19E06.1080p.WEB.h264-BRK", protocol="torrent", seeders=50, key="brk", cat="tv")
    install_search(monkeypatch, [mag, broken])
    mod = __import__("sys").modules["mso.features.search.service"]
    orig = mod.fetch_download

    def fetch(token: str) -> Any:
        if token == broken.extra["token"]:
            return types.SimpleNamespace(content=b"<html>Inloggen vereist</html>", filename=None, final_url=None)
        return orig(token)

    monkeypatch.setattr(mod, "fetch_download", fetch)
    sent = install_clients(monkeypatch)
    ref = _manual(operator, media_category="tv", title="Criminal Minds", season_number=19, episode_number=6)
    assert len(_cands(operator, ref)["accepted"]) == 2
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": mag.release_key})
    assert r.status_code == 422 and "vermomt een uitvoerbaar bestand als video (.mkv.scr)" in r.json()["detail"]
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": broken.release_key})
    assert r.status_code == 422 and "niet te controleren" in r.json()["detail"]
    assert not sent["qbit"]
    # a magnet name or an unreadable file is not proof from the payload itself: refused, not blacklisted
    from mso.features.recovery import blacklist

    assert not blacklist.is_blacklisted(mag.release_key) and not blacklist.is_blacklisted(broken.release_key)
    summaries = [x["summary"] for x in db.fetchall("SELECT summary FROM audit_log WHERE action='recovery.send_refused' "
                                                     "ORDER BY id")]
    assert summaries[0].startswith("Versturen geweigerd, nep-release")
    assert summaries[1].startswith("Versturen geweigerd, bestanden niet te controleren")


def test_automatic_send_refuses_fake_payload(admin: Any, viewer: Any, monkeypatch: Any, manage_mode: Any) -> None:
    """Opt-in auto-send / Autopilot path (download_send applier → actions.send): the same pre-send check."""
    from datetime import UTC, datetime

    from mso.domain import outcomes
    from mso.features.recovery import fallback, tasks

    sneaky = release("Lost.Signal.S02E05.1080p.WEB-DL.H.264-TGRP", protocol="torrent", seeders=40, key="tfake",
                     cat="tv")
    sneaky.extra["torrent_files"] = [("Lost.Signal.S02E05.1080p.mkv.exe", 3 * MB)]
    install_search(monkeypatch, [sneaky])
    sent = install_clients(monkeypatch)
    r = admin.put(
        "/api/v1/settings",
        json={"values": {"recovery.auto_send_fallback": True, "automation.recovery.auto_apply_safe": True}},
    )
    assert r.status_code == 200, r.text
    outcomes.upsert_outcome(
        {"protocol": "torrent", "client": "qbittorrent", "client_ref": "a" * 40, "release_key": "btih:" + "a" * 40,
         "title": "Lost.Signal.S02E05.1080p.WEB-DL.H.264-MALWARE", "client_category": "tv", "media_category": "tv",
         "status": "failed", "failure_reason": "false_positive", "finished_at": datetime.now(UTC), "meta": {}}
    )
    tasks.process_outcomes()
    fallback.process_failed()
    fb = viewer.get("/api/v1/recovery/fallbacks").json()["items"][0]
    from mso.features.automation import engine

    res = engine.apply_safe()
    assert res["failed"] == [fb["change_id"]], res
    ch = viewer.get(f"/api/v1/changes/{fb['change_id']}").json()
    assert ch["status"] == "failed" and "bevat uitvoerbaar bestand" in (ch.get("error") or "")
    assert not sent["qbit"]
    from mso.db import get_db

    a = get_db().fetchone("SELECT * FROM audit_log WHERE action='recovery.send_refused'")
    assert a["detail"]["via"] == "change" and sneaky.release_key in a["detail"]["blacklisted"]


# ------------------------------------------------------------------ over real HTTP (Torznab/Newznab mocks)
FAKE_FIXTURES = [
    {"id": "cm-exe", "title": EXE_TITLE, "cats": [5000, 5040], "size": 2_500_000, "season": 19, "ep": 6,
     "age_d": 1, "grabs": 900},
    {"id": "cm-sneaky", "title": "Criminal.Minds.S19E06.1080p.WEB.h264-GRACE", "cats": [5000, 5040],
     "size": 1_500_000_000, "season": 19, "ep": 6, "age_d": 1, "grabs": 400,
     "files": [("Criminal.Minds.S19E06.1080p.WEB.h264-GRACE.mkv.exe", 1_500_000_000), ("Read Me.txt", 100)]},
    {"id": "cm-real", "title": "Criminal.Minds.S19E06.1080p.WEB.h264-NTb", "cats": [5000, 5040],
     "size": 1_400_000_000, "season": 19, "ep": 6, "age_d": 2, "grabs": 300,
     "files": [("Criminal.Minds.S19E06.1080p.WEB.h264-NTb.mkv", 1_400_000_000)]},
]


@pytest.fixture()
def torznab(admin: Any, serve: Any) -> Any:
    tz = mock_nz.create_app(apikey=TZ_KEY, protocol="torrent")
    tz.state.fixtures.extend(dict(f) for f in FAKE_FIXTURES)
    add_direct(admin, serve(tz), kind="torznab", key=TZ_KEY, label="Torznab mix")
    return tz


@pytest.fixture()
def qbit(admin: Any, serve: Any) -> Any:
    from msomocks.qbittorrent import create_app

    from mso.connectors.qbittorrent import QbitClient

    QbitClient.reset_cache()
    app = create_app(version="5.1.2", scenario="empty")
    r = admin.put("/api/v1/connectors/qbittorrent",
                  json={"url": serve(app), "secrets": {"username": "admin", "password": "adminadmin"}})
    assert r.status_code == 200, r.text
    yield app.state.qb
    QbitClient.reset_cache()


def _gets(tz: Any) -> list[str]:
    return [c["params"].get("id") for c in tz.state.calls if c["params"].get("t") == "get"]


def test_search_flags_fakes_and_ranks_them_last(viewer: Any, torznab: Any) -> None:
    res = search(viewer, q="Criminal Minds", category="tv", protocol="torrent")
    by_title = {i["title"]: i for i in res["items"]}
    exe = by_title[EXE_TITLE]
    assert "uitvoerbaar" in exe["false_positive_flags"]
    assert exe["extra"]["release_gate"] == ["releasenaam is een uitvoerbaar bestand (.exe)"]
    assert res["items"][-1]["title"] == EXE_TITLE  # despite the most grabs/seeders
    for sort in ("seeders", "age", "size"):  # last in every sort order, not only by relevance
        ordered = search(viewer, q="Criminal Minds", category="tv", protocol="torrent", sort=sort)["items"]
        assert ordered[-1]["title"] == EXE_TITLE, (sort, [i["title"] for i in ordered])
    # a clean title is not flagged (its payload is only known after fetching: checked at send)
    assert "uitvoerbaar" not in by_title["Criminal.Minds.S19E06.1080p.WEB.h264-GRACE"]["false_positive_flags"]
    assert "uitvoerbaar" not in by_title["Criminal.Minds.S19E06.1080p.WEB.h264-NTb"]["false_positive_flags"]


def test_search_send_gate_over_http(viewer: Any, operator: Any, torznab: Any, qbit: Any, db: Any) -> None:
    res = search(viewer, q="Criminal Minds", category="tv", protocol="torrent")
    tok = {i["title"]: i["token"] for i in res["items"]}
    keys = {i["title"]: i["release_key"] for i in res["items"]}
    # 1. fake title: refused before anything is fetched from the indexer
    r = operator.post("/api/v1/search/send", json={"token": tok[EXE_TITLE], "category": "tv"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "Beleid weigert deze release: releasenaam is een uitvoerbaar bestand (.exe)"
    assert "cm-exe" not in _gets(torznab)
    assert db.scalar("SELECT count(*) FROM search_sends") == 0
    # 2. clean title, program inside: the real .torrent is fetched over HTTP, its file list refuses it
    sneaky = "Criminal.Minds.S19E06.1080p.WEB.h264-GRACE"
    r = operator.post("/api/v1/search/send", json={"token": tok[sneaky], "category": "tv"})
    assert r.status_code == 422, r.text
    assert 'bevat uitvoerbaar bestand "Criminal.Minds.S19E06.1080p.WEB.h264-GRACE.mkv.exe"' in r.json()["detail"]
    assert "cm-sneaky" in _gets(torznab)
    assert not qbit.torrents  # nothing reached qBittorrent
    row = db.fetchone("SELECT * FROM search_sends WHERE release_key=%s", (keys[sneaky],))
    assert row["status"] == "failed" and "uitvoerbaar" in row["error"]
    audits = db.fetchall("SELECT * FROM audit_log WHERE action='recovery.send_refused' ORDER BY id")
    assert len(audits) == 2 and audits[0]["detail"]["checked"]["kind"] == "title"
    assert {(a["detail"]["via"], a["detail"]["client"]) for a in audits} == {("search", "qbittorrent")}
    assert audits[1]["detail"]["checked"]["kind"] == "files" and keys[sneaky] in audits[1]["detail"]["blacklisted"]
    assert TZ_KEY not in str(audits)
    # the proven fake is blocked from now on (also as a recovery candidate)
    again = {i["title"]: i for i in search(viewer, q="Criminal Minds", category="tv", protocol="torrent")["items"]}
    assert "geblokkeerd" in again[sneaky]["false_positive_flags"]
    # 3. the real release goes through to qBittorrent (valid .torrent from the mock, parsed by the qBittorrent mock)
    r = operator.post("/api/v1/search/send", json={"token": tok["Criminal.Minds.S19E06.1080p.WEB.h264-NTb"],
                                                   "category": "tv"})
    assert r.status_code == 200, r.text
    assert [t["name"] for t in qbit.torrents.values()] == ["Criminal.Minds.S19E06.1080p.WEB.h264-NTb"]


def test_search_send_nzb_with_program_is_refused(admin: Any, viewer: Any, operator: Any, serve: Any, db: Any) -> None:
    from msomocks import sabnzbd as sabmock

    nz = mock_nz.create_app(apikey=NZ_KEY)
    nz.state.fixtures.append(
        {"id": "exp-fake", "title": "The.Expanse.S02E07.1080p.WEB-DL.DDP5.1.H.264-NTb", "cats": [5000, 5040],
         "size": 2_000_000_000, "season": 2, "ep": 7, "age_d": 1, "grabs": 5,
         "files": [("The.Expanse.S02E07.1080p.WEB-DL.mkv.exe", 2_000_000), ("The.Expanse.S02E07.par2", 10)]}
    )
    add_direct(admin, serve(nz), label="NZB mix")
    sab_app = sabmock.create_app(api_key="SABKEY-gate-0123456789")
    r = admin.put("/api/v1/connectors/sabnzbd",
                  json={"url": serve(sab_app), "enabled": True, "secrets": {"api_key": "SABKEY-gate-0123456789"}})
    assert r.status_code == 200, r.text
    queued = len(sab_app.state.sab.queue)
    item = search(viewer, q="Expanse", category="tv", protocol="usenet", season=2, episode=7)["items"][0]
    r = operator.post("/api/v1/search/send", json={"token": item["token"], "category": "tv"})
    assert r.status_code == 422, r.text
    assert 'bevat uitvoerbaar bestand "The.Expanse.S02E07.1080p.WEB-DL.mkv.exe"' in r.json()["detail"]
    assert len(sab_app.state.sab.queue) == queued
    # a normal NZB (no quoted program names) still goes to SABnzbd
    ok = search(viewer, q="Expanse", category="tv", protocol="usenet", season=2, episode=5)["items"][0]
    r = operator.post("/api/v1/search/send", json={"token": ok["token"], "category": "tv"})
    assert r.status_code == 200, r.text
    assert len(sab_app.state.sab.queue) == queued + 1


def test_recovery_over_http_refuses_fake_and_sends_real(operator: Any, torznab: Any, qbit: Any, db: Any) -> None:
    ref = _manual(operator, media_category="tv", title="Criminal Minds", season_number=19, episode_number=6)
    res = _cands(operator, ref)
    rej = {c["title"]: c["reasons"] for c in res["rejected"]}
    assert rej[EXE_TITLE][0] == "releasenaam is een uitvoerbaar bestand (.exe)"
    assert any("onwaarschijnlijk klein voor een 1080p-aflevering (2.4 MB" in x for x in rej[EXE_TITLE])
    acc = {c["title"]: c for c in res["accepted"]}
    assert set(acc) == {"Criminal.Minds.S19E06.1080p.WEB.h264-GRACE", "Criminal.Minds.S19E06.1080p.WEB.h264-NTb"}
    sneaky = acc["Criminal.Minds.S19E06.1080p.WEB.h264-GRACE"]
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": sneaky["release_key"]})
    assert r.status_code == 422 and "dubbele extensie, vermomd als video" in r.json()["detail"]
    assert not qbit.torrents
    real = acc["Criminal.Minds.S19E06.1080p.WEB.h264-NTb"]
    r = operator.post(f"/api/v1/recovery/items/{ref}/send", json={"release_key": real["release_key"]})
    assert r.status_code == 200, r.text
    t = next(iter(qbit.torrents.values()))
    assert t["name"] == "Criminal.Minds.S19E06.1080p.WEB.h264-NTb" and t["category"] == "mso-tv"
    assert db.scalar("SELECT count(*) FROM audit_log WHERE action='recovery.sent'") == 1


def test_fake_proven_from_zoeken_stops_being_a_recovery_candidate(
    viewer: Any, operator: Any, torznab: Any, qbit: Any, db: Any
) -> None:
    sneaky = "Criminal.Minds.S19E06.1080p.WEB.h264-GRACE"
    ref = _manual(operator, media_category="tv", title="Criminal Minds", season_number=19, episode_number=6)
    acc = {c["title"]: c for c in _cands(operator, ref)["accepted"]}
    assert sneaky in acc and "Criminal.Minds.S19E06.1080p.WEB.h264-NTb" in acc
    from mso.features.recovery import tasks

    tasks.make_recommendations()
    rec = db.fetchone("SELECT * FROM recommendations WHERE dedupe_key=%s", (f"recovery:cand:{ref}",))
    assert f"bv. '{sneaky}'" in rec["explanation"]  # the fake has the most seeders, so it is the example
    item = next(i for i in search(viewer, q="Criminal Minds", category="tv", protocol="torrent")["items"]
                if i["title"] == sneaky)
    r = operator.post("/api/v1/search/send", json={"token": item["token"], "category": "tv"})
    assert r.status_code == 422, r.text
    stored = {c["title"]: c for c in operator.get(f"/api/v1/recovery/items/{ref}").json()["candidates"]}
    assert stored[sneaky]["passed"] is False and stored[sneaky]["score"] is None
    assert stored["Criminal.Minds.S19E06.1080p.WEB.h264-NTb"]["passed"] is True  # the real one stays offered
    real = "1 release(s) voldoen aan alle kwaliteitseisen, bv. 'Criminal.Minds.S19E06.1080p.WEB.h264-NTb'."
    rec = db.fetchone("SELECT * FROM recommendations WHERE id=%s", (rec["id"],))
    assert rec["status"] == "open" and rec["explanation"].startswith(real)  # rewritten with the remaining one
    assert f"'{sneaky}' bleek bij de controle vóór verzenden een nep-release" in rec["explanation"]
    tasks.make_recommendations()  # the next analysis refreshes it as usual
    rec = db.fetchone("SELECT * FROM recommendations WHERE id=%s", (rec["id"],))
    assert rec["explanation"].startswith(real) and sneaky not in rec["explanation"]


def test_own_torznab_feed_leaves_fakes_out(admin: Any, client: Any, torznab: Any) -> None:
    set_settings({"newznab.public_url": "http://mso.lan:8000"})
    info = admin.get("/api/v1/newznab/endpoint").json()
    key = admin.post(f"/api/v1/newznab/consumers/{info['default_consumer']['id']}/reveal").json()["apikey"]
    from defusedxml import ElementTree as DET

    r = client.get("/torznab/api", params={"t": "tvsearch", "q": "Criminal Minds", "apikey": key})
    titles = [i.findtext("title") for i in DET.fromstring(r.content).findall("./channel/item")]
    assert "Criminal.Minds.S19E06.1080p.WEB.h264-NTb" in titles and EXE_TITLE not in titles
    # the interactive search (operator/viewer) still shows it, flagged
    assert any(i["title"] == EXE_TITLE for i in search(admin, q="Criminal Minds", category="tv")["items"])
