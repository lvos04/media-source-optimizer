"""Verzendingen mogen alleen met het eigen, eenduidige ontvangstbewijs worden bevestigd."""

from hashlib import sha256

import pytest

from mso.change_preview import digest
from mso.features.usenet import nzbs


def test_tegenstrijdig_ontvangstbewijs_blokkeert_beide_formulieren(db):
    instance, category, ref = "testinstantie", "tv", "testdownload"
    forms = []
    for title in ("Eerste.nzb", "Tweede.nzb"):
        content = sha256(title.encode()).hexdigest()
        form = digest(["addfile", category, title, content])
        forms.append(dict(instance=instance, category=category, sha256=content, request_hash=form))
    nzbs.record_send(nzo_id=ref, origin="send_nzb", title="Eerste.nzb", **forms[0])
    with pytest.raises(ValueError, match="tegenstrijdig"):
        nzbs.record_send(nzo_id=ref, origin="send_nzb", title="Tweede.nzb", **forms[1])
    for form in forms:
        with pytest.raises(ValueError, match="tegenstrijdig"):
            nzbs.recent_send(**form)
    assert db.scalar("SELECT count(*) FROM usenet_nzbs WHERE nzo_id=%s", (ref,)) == 1


def test_samenlopende_formulieren_per_download_id(db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    ready = Barrier(2)

    def claim(title):
        content = sha256(title.encode()).hexdigest()
        ready.wait(timeout=5)
        try:
            nzbs.record_send(instance="testinstantie", nzo_id="een-doel", origin="send_nzb", title=title,
                             category="tv", sha256=content, request_hash=digest([title, content]))
        except ValueError as exc:
            assert "tegenstrijdig" in str(exc)
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, ["een.nzb", "twee.nzb"])) == [False, True]
    assert db.scalar("SELECT count(*) FROM download_receipt_targets WHERE disputed") == 1
    assert db.scalar("SELECT count(*) FROM usenet_nzbs") == 1
