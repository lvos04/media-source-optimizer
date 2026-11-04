"""Classify SABnzbd failures into ``mso.domain.outcomes.FAILURE_REASONS`` — only as far as the evidence goes.

Input is SABnzbd's ``fail_message`` (and the ``stage_log`` lines). Each rule carries a certainty:

- ``zeker``         the message states the cause literally (e.g. "archive requires a password")
- ``waarschijnlijk`` the message strongly implies it (e.g. a CRC error after repair → damaged/incomplete data)
- ``onbekend``      nothing matched → ``unknown`` (MSO never guesses beyond the evidence)

``no_retention`` is only used when the message says so, or when MSO knows the post date of the NZB and it is
older than the retention of every enabled SABnzbd server (``retention`` > 0). "Articles missing" alone is
``incomplete``: articles can also be gone through takedowns or bad posts.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from mso.security import redact


@dataclass
class Classification:
    reason: str
    certainty: str  # zeker|waarschijnlijk|onbekend
    evidence: str  # short Dutch description of the matched rule
    detail: str  # redacted original message
    likely_cause: dict[str, Any] | None = None  # e.g. server login problems active at that time

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# (reason, certainty, evidence, pattern) — first match wins; order matters (specific before generic).
RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    (
        "account_expired",
        "zeker",
        "provider meldt verlopen account",
        re.compile(
            r"(account|subscription|abonnement)[^.]{0,40}expired|expired[^.]{0,20}(account|subscription)"
            r"|account (is )?(verlopen|inactive|suspended)",
            re.I,
        ),
    ),
    (
        "quota_exceeded",
        "zeker",
        "provider meldt quotum/blok op",
        re.compile(
            r"quota|download limit|limit (reached|exceeded)|block[^.]{0,20}(exhausted|used up|empty)"
            r"|no (more )?(data|credit)s? (left|remaining)",
            re.I,
        ),
    ),
    (
        "auth_failure",
        "zeker",
        "inloggen bij provider mislukt",
        re.compile(
            r"authenticat\w* (failed|error|denied)|failed login|login (failed|incorrect)|"
            r"requires user/password|\b48[12]\b[^.]{0,30}(auth|password|user|denied|login)|"
            r"invalid (user|username|password)",
            re.I,
        ),
    ),
    (
        "encrypted",
        "zeker",
        "archief is versleuteld/wachtwoord vereist",
        re.compile(
            r"requires a password|password[- ]protected|encrypt(ed|ion)|wrong password|"
            r"unpacking failed.{0,20}password",
            re.I,
        ),
    ),
    (
        "disk_full",
        "zeker",
        "schijf vol of schrijffout",
        re.compile(r"disk (is )?full|no space left|too little (disk)?space|out of disk|write error", re.I),
    ),
    (
        "no_retention",
        "zeker",
        "melding noemt retentie",
        re.compile(r"retention|older than.{0,30}(days|retention)", re.I),
    ),
    (
        "incomplete",
        "zeker",
        "artikelen ontbreken (download niet compleet)",
        re.compile(
            r"cannot be completed|not-complete|not on your server|articles? (were )?missing|"
            r"missing articles|only [\d.]+% of required|too much missing data|"
            r"no such article",
            re.I,
        ),
    ),
    (
        "incomplete",
        "zeker",
        "repair faalde: te weinig herstelblokken",
        re.compile(
            r"repair failed|not enough repair blocks|(\d+ )?blocks? short|par2.{0,30}(failed|missing)", re.I
        ),
    ),
    (
        "incomplete",
        "waarschijnlijk",
        "CRC-fout na herstel (beschadigde/ontbrekende data)",
        re.compile(r"\bcrc\b", re.I),
    ),
    (
        "unpack_failed",
        "zeker",
        "SABnzbd meldt dat uitpakken mislukt is; de melding zelf noemt geen oorzaak",
        re.compile(r"unpacking failed|unpack(ing)? error|error (while|during) unpacking|failed to unpack", re.I),
    ),
    (
        "client_failure",
        "zeker",
        "fout in SABnzbd-verwerking",
        re.compile(
            r"post-processing|script (returned|exit|failed)|invalid nzb|empty nzb|"
            r"nzb is empty|duplicate nzb|unwanted extension|aborted, (unwanted|duplicate)|"
            r"unable to (find|move|create)|permission denied|failed to (move|rename)",
            re.I,
        ),
    ),
]


def classify(
    fail_message: str | None,
    stage_log: list[dict[str, Any]] | None = None,
    *,
    posted_at: datetime | None = None,
    server_retention_days: list[int] | None = None,
    now: datetime | None = None,
) -> Classification | None:
    """Return ``None`` when there is no failure message at all (not a failure)."""
    msg = (fail_message or "").strip()
    stage_text = _stage_text(stage_log)
    if not msg and not stage_text:
        return None
    detail = redact(msg or stage_text)[:1000]
    hit = _first_rule(msg) if msg else None
    if hit is not None and hit[0] in GENERIC and stage_text:
        # "Unpacking failed, see logfile" says nothing about the cause; the stage log often does
        specific = _first_rule(stage_text)
        if specific is not None and specific[0] not in GENERIC:
            hit = (specific[0], specific[1], f"{specific[2]} (uit het SABnzbd-verwerkingslog)")
    if hit is None and stage_text:
        hit = _first_rule(stage_text)
    if hit is not None:
        reason, certainty, evidence = hit
        if reason == "incomplete" and _beyond_retention(posted_at, server_retention_days, now):
            return Classification(
                "no_retention",
                "zeker",
                "artikelen ontbreken en de post is ouder dan de retentie van elke ingeschakelde server",
                detail,
            )
        return Classification(reason, certainty, evidence, detail)
    if not msg:  # stage log without failure message and without a known pattern: not a failure
        return None
    return Classification("unknown", "onbekend", "melding niet herkend", detail)


GENERIC = {"unpack_failed", "client_failure"}


def _first_rule(text: str) -> tuple[str, str, str] | None:
    for reason, certainty, evidence, pattern in RULES:
        if pattern.search(text):
            return reason, certainty, evidence
    return None


def with_server_context(cls: Classification, problems: list[dict[str, Any]]) -> Classification:
    """Relate an unspecific failure to SABnzbd server login/account problems that were active at that time.

    Only for failures whose message doesn't name a cause (``unpack_failed``, ``incomplete``, ``unknown``):
    the failure stays what SABnzbd reported, the server problem is added as the *likely* cause."""
    if not problems or cls.reason not in ("unpack_failed", "incomplete", "unknown"):
        return cls
    from mso.features.usenet import serverstatus  # lazy: avoids an import cycle

    parts = []
    for p in problems[:6]:
        name = serverstatus.short_name(p.get("displayname") or p["server"])
        parts.append(f"{name}: {serverstatus.LABELS.get(p['status'], p['status']).lower()}")
    servers = "; ".join(parts)
    what = {
        "unpack_failed": "Uitpakken mislukt",
        "incomplete": "Artikelen ontbraken",
        "unknown": "Download mislukt",
    }[cls.reason]
    evidence = (
        f"{what} terwijl SABnzbd rond dat moment serverfouten meldde ({servers}). Waarschijnlijk konden "
        "ontbrekende artikelen daardoor niet worden aangevuld; de SABnzbd-melding zelf noemt geen oorzaak."
    )
    certainty = cls.certainty if cls.reason == "incomplete" else "waarschijnlijk"
    out = Classification(cls.reason, certainty, evidence, cls.detail)
    expired = any(p["status"] == "account_expired" for p in problems)
    out.likely_cause = {
        "reason": "account_expired" if expired else "auth_failure",
        "certainty": "waarschijnlijk",
        "servers": [
            {"server": p["server"], "name": serverstatus.short_name(p.get("displayname") or p["server"]),
             "status": p["status"]}
            for p in problems
        ],
    }
    return out


def _stage_text(stage_log: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for stage in stage_log or []:
        for action in stage.get("actions") or []:
            if isinstance(action, str):
                lines.append(action)
    return " | ".join(lines)[:4000]


def _beyond_retention(
    posted_at: datetime | None, retention_days: list[int] | None, now: datetime | None
) -> bool:
    if posted_at is None or not retention_days or any(d <= 0 for d in retention_days):
        return False  # 0 = unknown/unlimited in SABnzbd → no evidence
    now = now or datetime.now(UTC)
    pd = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=UTC)
    age_days = (now - pd).total_seconds() / 86400
    return age_days > max(retention_days)
