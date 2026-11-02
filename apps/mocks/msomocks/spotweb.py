"""Mock Spotweb (test double). Spotweb exposes a Newznab-compatible API at
``/index.php?page=newznabapi&t=...`` (usenet only); otherwise identical to the generic Newznab mock.

``create_app(apikey='spot-test-key', **faults)`` — faults as in :mod:`msomocks.newznab`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from msomocks import newznab


def create_app(apikey: str = "spot-test-key", **faults: Any) -> FastAPI:
    return newznab.create_app(apikey=apikey, protocol="usenet", flavor="spotweb", **faults)
