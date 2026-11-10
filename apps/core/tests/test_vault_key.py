"""Vault key file: created exactly once (0600 in a 0700 directory), never overwritten, and safe when api and worker
start at the same moment on a fresh /appdata (race found in the stack smoke test: the losing process crashed with
FileExistsError). No database needed."""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from mso import vault


def test_creates_once_with_strict_modes_and_reuses(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "vault.key"
    key = vault._load_key(path)
    assert len(key) == 32 and path.read_bytes() == key
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert vault._load_key(path) == key  # never regenerated


def test_loser_of_the_create_race_reads_the_winners_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic version of the race: exists() said 'no', but another process created the file before open()."""
    path = tmp_path / "secrets" / "vault.key"
    winner = vault._load_key(path)
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self, *a, **k: False if self == path else real_exists(self, *a, **k))
    assert vault._load_key(path) == winner
    assert path.read_bytes() == winner


def test_waits_while_the_winner_is_still_writing(tmp_path: Path) -> None:
    path = tmp_path / "vault.key"
    path.touch(mode=0o600)  # created by the winner, not yet written
    late = os.urandom(32)

    def write_later() -> None:
        time.sleep(0.2)
        path.write_bytes(late)

    t = threading.Thread(target=write_later)
    t.start()
    try:
        assert vault._load_key(path, wait_s=3.0) == late
    finally:
        t.join()


def test_a_damaged_key_file_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "vault.key"
    path.write_bytes(b"kort")
    with pytest.raises(RuntimeError, match="ongeldige lengte"):
        vault._load_key(path, wait_s=0.2)
    assert path.read_bytes() == b"kort"  # never overwritten


def test_many_concurrent_starts_agree_on_one_key(tmp_path: Path) -> None:
    for ronde in range(20):
        path = tmp_path / f"r{ronde}" / "secrets" / "vault.key"
        barrier = threading.Barrier(8)
        keys: list[bytes] = []
        errors: list[BaseException] = []

        def start(
            barrier: threading.Barrier = barrier, path: Path = path,
            keys: list[bytes] = keys, errors: list[BaseException] = errors,
        ) -> None:
            barrier.wait()
            try:
                keys.append(vault._load_key(path))
            except BaseException as exc:  # collected and asserted below
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(set(keys)) == 1 and keys[0] == path.read_bytes()
