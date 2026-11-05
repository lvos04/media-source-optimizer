"""Torrent engine (Slice C): qBittorrent ingestion, diagnostics, guardrails, tuning loop, cross-seed, storage.

Non-destructive by default: everything is read-only diagnostics and advice; every write to qBittorrent goes
through a change set on the ``qbittorrent`` applier (preview → approve → apply → rollback). The only delete is
the malware cleanup (``cleanup.py``): suspicious torrents only, ``toestemming_nodig``, re-checked live at apply.
"""
