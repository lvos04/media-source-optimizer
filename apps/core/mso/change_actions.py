"""De begrensde actielijst voor goedgekeurde wijzigingen vanuit de Hub."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from mso.features.torrent import policy


@dataclass(frozen=True)
class Action:
    action_id: str
    target: str
    description: str
    accepts_path: Callable[[str], bool]

    def public(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id, "target": self.target, "description": self.description,
            "rollback": True, "min_risk": "R2", "requires_backup": False,
        }


def _torrent_limit(path: str) -> bool:
    return bool(policy.PATH_RE.fullmatch(path)) and policy.is_rate_path(path)


ACTIONS = (
    Action("mso.sabnzbd.server_limits", "sabnzbd", "Prioriteit en optional-vlag van een bestaande nieuwsserver",
           lambda path: bool(re.fullmatch(r"servers/.+/(priority|optional)", path))),
    Action("mso.qbittorrent.speed_limits", "qbittorrent", "Globale of per-torrent ingestelde snelheidslimieten",
           _torrent_limit),
)


def require_action(change: dict[str, Any], action_id: str | None = None) -> Action:
    for action in ACTIONS:
        if change["target"] != action.target or action_id not in (None, action.action_id):
            continue
        ops = change["ops"]
        if ops and all(action.accepts_path(op["path"]) and type(op.get("before")) is int
                       and type(op.get("after")) is int
                       and not any(op.get(flag) for flag in ("destructive", "irreversible", "one_way"))
                       for op in ops):
            return action
    raise HTTPException(403, detail="Deze wijziging valt buiten de actielijst voor de Hub.")
