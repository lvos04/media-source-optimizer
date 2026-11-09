"""SABnzbd's per-job processing log (``stage_log`` of a history slot) → facts (1.6.0).

SABnzbd records what happened to a job per stage (``mode=history`` → ``sabnzbd/api.py`` build_history /
``database.unpack_history_info``): ``[{name: "Download"|"Servers"|"Repair"|"Filejoin"|"Unpack"|"Script"|…,
actions: [str, …]}]``. The wording comes from SABnzbd's own source:

- ``Download`` (``nzb/object.py`` set_download_report, written once when the download ends, lines joined with
  ``<br/>``): "Downloaded in … at an average of …", "Age: 12d", "N articles were malformed", "N articles were
  missing"; ``postproc.py`` appends "Download failed - Not on your server(s)" / "Download might fail, only X of
  required Y available"; an abort during the download ("Aborted, cannot be completed") only survives in the job's
  ``fail_message`` because the report replaces the stage;
- ``Servers``: "Eweka=1.4 GB, Newshosting=12.0 MB" (bytes per server);
- ``Repair`` (``newsunpack.py`` par2 / ``postproc.py`` SFV and RAR checks): "[set] Quick Check OK", "[set] Verified
  in …, all files correct", "[set] Repaired in …", "[set] Repair failed, not enough repair blocks (N short)",
  "Repairing failed, …", "Invalid par2 files …", "Some files failed to verify against …", "[set] RAR-based
  verification failed: Passworded";
- ``Unpack``/``Filejoin`` (``newsunpack.py`` rar/7z/file join): "[set] Unpacked N files/folders in …", "Unpacking
  failed, archive requires a password", "Unpacking failed, CRC error", "Unpacking failed, write error or disk is
  full? <unrar line>", "Unpacking failed, disk full", "Unpacking failed, file too large for filesystem (FAT?)",
  "Unpacking failed, unable to find <volume>", "Corrupt RAR file", "Unusable RAR file", "ERROR: <unrar>",
  "Failed moving <a> to <b>", "Unpack nesting too deep", "Removed N files with unwanted extensions". 7-Zip's
  "Unpacking failed, see logfile" is only in ``fail_message``.

SABnzbd writes these lines in the language that was active at the time, so each rule also knows SABnzbd's Dutch
translation (``po/main/nl.po``). Nothing is guessed: a fact is only set when SABnzbd's own line says so.
:func:`parse` returns ``None`` without a stage log (no facts), and the result is stored in
``download_outcomes.meta.stage`` with ``meta.classifier_version = 2``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from mso.security import redact

CLASSIFIER_VERSION = 2

# Problems SABnzbd's lines name. ENVIRONMENT = SABnzbd/the storage cannot process the job (the same for every
# release); the others are properties of one release (password, damaged archive, unwanted executable) or of the data
# (missing articles, failed repair).
PROBLEM_LABELS = {
    "missing_articles": "artikelen ontbraken",
    "repair_failed": "herstel mislukt",
    "password": "wachtwoord vereist",
    "disk_full": "schijf vol",
    "no_permission": "geen schrijfrechten",
    "file_too_large": "bestand te groot voor het bestandssysteem",
    "move_failed": "verplaatsen naar de eindmap mislukt",
    "unpacker_error": "fout in het uitpakprogramma",
    "corrupt_archive": "beschadigd archief",
    "executable": "uitvoerbaar bestand (ongewenste extensie)",
}
ENVIRONMENT = {"disk_full", "no_permission", "file_too_large", "move_failed", "unpacker_error"}
# The line itself names an environment cause. ``unpacker_error`` (e.g. 7-Zip's "Unpacking failed, see logfile") names
# none: it only counts as SABnzbd/the storage once the same line repeats across different releases
# (:mod:`mso.features.usenet.unpack`, ``meta.classification.repeated``).
CERTAIN_ENVIRONMENT = ENVIRONMENT - {"unpacker_error"}
REPAIR_LABELS = {"gelukt": "gelukt", "mislukt": "mislukt", "niet_nodig": "niet nodig"}

_I = re.IGNORECASE
_SET_PREFIX = re.compile(r"^(?:\[[^\]]{0,300}\]\s*)+")
_TAG = re.compile(r"<[^>]{0,500}>")

# Download stage
_MISSING = re.compile(r"(\d+)\s+(?:articles were missing|artikelen ontbreken)", _I)
_MALFORMED = re.compile(r"(\d+)\s+(?:articles were malformed|artikelen zijn misvormd)", _I)
_AGE = re.compile(r"(?:^|\b)(?:Age|Leeftijd):\s*(\d+)\s*([dhm])\b", _I)
_ABORTED = re.compile(
    r"Aborted, cannot be completed|Afgebroken, kan niet voltooid worden|Not on your server|"
    r"Niet meer op je server|Download might fail, only|Download mislukt waarschijnlijk, slechts|"
    r"cannot be completed|not-complete",
    _I,
)
# Servers stage: "<displayname>=<to_units>B"
_SERVER_BYTES = re.compile(r"\s*([^=,]{1,120}?)=([\d.]+)\s*([KMGTP]?)B\b", _I)
_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}

# Repair stage
_REPAIR_FAILED = re.compile(
    r"Repair failed|Repairing failed|Reparatie mislukt|Invalid par2 files|Ongeldige par2|"
    r"failed to verify|niet geverifieerd|niet verifieerbaar|RAR-based verification failed|RAR verificatie niet gelukt|"
    r"cannot be renamed to|No details available for recoverable file",
    _I,
)
_REPAIRED = re.compile(r"Repaired in|Gerepareerd in", _I)
_REPAIR_OK = re.compile(
    r"Quick Check OK|Snelle Controle OK|all files correct|alle bestanden zijn goed|"
    r"Verified successfully using SFV|Verificatie m\.b\.v\. SFV-bestanden is gelukt|"
    r"RAR files verified successfully|RAR bestanden zijn succesvol geverifieerd",
    _I,
)
_SHORT = re.compile(r"\((\d+)\s+(?:short|tekort)\)", _I)
_PAR2_OPTIONS = re.compile(r"PAR2 received incorrect options|incorrect options", _I)

# Unpack / Filejoin stage
_UNPACK_OK = re.compile(
    r"Unpacked \d+ files/folders|bestanden/mappen uitgepakt|Joined \d+ files|bestanden samengevoegd", _I
)
_UNPACK_FAILED = re.compile(
    r"Unpacking failed|Uitpakken mislukt|Uitplakken mislukt|while unpacking|Unusable RAR|Onbruikbaar RAR|"
    r"Corrupt RAR|Beschadigd RAR|^ERROR: |Failed moving|Verplaatsen van .{0,400} mislukt|Failed to move files|"
    r"nesting too deep|Teveel niveaus|while joining files|bij samenvoegen|File join of .{0,300} failed",
    _I,
)
_UNWANTED_REMOVED = re.compile(r"Removed \d+ files with unwanted extensions", _I)

# What one SABnzbd line says is the problem (first match wins; specific before generic).
_PROBLEM_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("password", re.compile(r"requires a password|vereist wachtwoord|passworded|versleuteld|wrong password|"
                            r"password incorrect|incorrect password|encrypted", _I)),
    ("no_permission", re.compile(r"permission denied|access is denied|operation not permitted|read-only file system|"
                                 r"\[Errno (?:1|13|30)\]|toegang geweigerd|geen toestemming", _I)),
    ("disk_full", re.compile(r"disk (?:is )?full|schijf (?:is )?vol|no space left|not enough space|"
                             r"schrijffout|write error|\[Errno 28\]|quota exceeded", _I)),
    ("file_too_large", re.compile(r"file too large|bestand te groot", _I)),
    ("executable", re.compile(r"unwanted extension|ongewenste extensie|"
                              r"\.(?:exe|scr|bat|cmd|com|pif|msi|vbs|js|jar|lnk|ps1)(?:[\s\"'\])]|$)", _I)),
    ("corrupt_archive", re.compile(r"\bcrc\b|corrupt|beschadigd|unusable rar|onbruikbaar rar|checksum|"
                                   r"unexpected end of archive|unable to find|kan .{1,300} niet vinden|"
                                   r"failed to verify|niet geverifieerd|niet verifieerbaar|"
                                   r"while joining files|bij samenvoegen|file join of", _I)),
    ("move_failed", re.compile(r"failed moving|failed to move|verplaatsen .{0,400}mislukt", _I)),
]
_GENERIC_UNPACK = re.compile(r"see logfile|zie logbestand", _I)


@dataclass
class StageFacts:
    has_download: bool = False  # Download report present → the missing-article count is reliable
    missing_articles: int | None = None  # 0 when the report says nothing about missing articles
    malformed_articles: int | None = None
    age_days: int | None = None
    servers: list[dict[str, Any]] = field(default_factory=list)  # [{name, bytes}] (display names)
    repair: str | None = None  # gelukt | mislukt | niet_nodig | None (no repair info)
    repair_line: str | None = None
    blocks_short: int | None = None
    unpack: str | None = None  # gelukt | mislukt | None
    unpack_line: str | None = None  # SABnzbd's exact failure line (redacted, set name removed)
    aborted: bool = False  # SABnzbd gave up during the download (articles not available)
    problem: str | None = None  # the main problem (PROBLEM_LABELS key)
    problems: list[str] = field(default_factory=list)
    data_complete: bool | None = None  # True: every article arrived (or was repaired); None: unknown
    signature: str | None = None  # stable id of the failure line (same error across releases)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(line: str) -> str:
    text = _TAG.sub(" ", str(line or "")).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _strip_set(line: str) -> str:
    return _SET_PREFIX.sub("", line).strip()


def _lines(actions: Any) -> list[str]:
    out: list[str] = []
    for action in actions or []:
        if not isinstance(action, str):
            continue
        for part in re.split(r"<br\s*/?>", action, flags=_I):
            text = _clean(part)
            if text:
                out.append(text)
    return out


def redact_line(line: str, limit: int = 300) -> str:
    """SABnzbd's line as shown in MSO: secrets masked (:func:`mso.security.redact`), the set name prefix dropped,
    absolute paths shortened to their last part (a home directory or user name in a path never leaves MSO)."""
    text = redact(_strip_set(_clean(line)))
    text = re.sub(r"(?<![\w.])(?:[A-Za-z]:)?(?:[\\/][^\\/\s\"']+){2,}[\\/]([^\\/\s\"']+)", r"…/\1", text)
    return text[:limit]


def problem_of(line: str | None) -> str | None:
    """The problem one SABnzbd failure line names, or ``unpacker_error`` for an unpack failure without a cause."""
    if not line:
        return None
    text = _strip_set(_clean(line))  # a set name ("[The.Encrypted.Files.2019]") never names the problem
    for key, pattern in _PROBLEM_RULES:
        if pattern.search(text):
            return key
    return "unpacker_error" if _UNPACK_FAILED.search(text) else None


def signature(problem: str | None, line: str | None) -> str | None:
    """Stable id for 'the same error': the problem plus the line without set names, paths, quotes and numbers."""
    if not line:
        return None
    norm = _strip_set(_clean(line)).lower()
    norm = re.sub(r"(?:[a-z]:)?(?:[\\/][^\\/\s\"']+)+", "<pad>", norm)
    norm = re.sub(r"\"[^\"]*\"|'[^']*'", "<x>", norm)
    norm = re.sub(r"\d+", "#", norm)
    norm = re.sub(r"\s+", " ", norm).strip()[:200]
    return hashlib.sha256(f"{problem}|{norm}".encode()).hexdigest()[:16]


def _size(num: str, unit: str) -> int | None:
    try:
        return int(float(num) * _UNITS.get(unit.upper(), 1))
    except ValueError:
        return None


def parse(stage_log: list[dict[str, Any]] | None, fail_message: str | None = None) -> StageFacts | None:
    """Facts from one history slot. ``None`` without a stage log (then MSO knows only SABnzbd's message)."""
    stages = [s for s in stage_log or [] if isinstance(s, dict)]
    if not stages:
        return None
    facts = StageFacts()
    msg = _clean(fail_message or "")
    repair_states: list[str] = []
    unpack_fail: str | None = None
    unpack_ok = False
    unwanted_removed = False  # "Removed N files with unwanted extensions": informational, runs after a failed unpack
    problems: list[str] = []

    for stage in stages:
        name = str(stage.get("name") or "")
        lines = _lines(stage.get("actions"))
        if name == "Download":
            facts.has_download = True
            for line in lines:
                if m := _MISSING.search(line):
                    facts.missing_articles = (facts.missing_articles or 0) + int(m.group(1))
                if m := _MALFORMED.search(line):
                    facts.malformed_articles = (facts.malformed_articles or 0) + int(m.group(1))
                if (m := _AGE.search(line)) and facts.age_days is None:
                    facts.age_days = int(m.group(1)) if m.group(2).lower() == "d" else 0
                if _ABORTED.search(line):
                    facts.aborted = True
                if problem_of(line) == "executable":
                    problems.append("executable")
        elif name == "Servers":
            for line in lines:
                for m in _SERVER_BYTES.finditer(line):
                    size = _size(m.group(2), m.group(3))
                    if size is not None:
                        facts.servers.append({"name": m.group(1).strip()[:120], "bytes": size})
        elif name == "Repair":
            for line in lines:
                plain = _strip_set(line)
                if _REPAIR_FAILED.search(plain):
                    kind = problem_of(plain)
                    if kind in ("password", "disk_full", "no_permission"):
                        problems.append(kind)  # verification stopped by the archive/storage, not by the data
                        continue
                    repair_states.append("mislukt")
                    facts.repair_line = facts.repair_line or redact_line(line)
                    if m := _SHORT.search(plain):
                        facts.blocks_short = int(m.group(1))
                elif _PAR2_OPTIONS.search(plain):
                    problems.append("unpacker_error")
                    facts.repair_line = facts.repair_line or redact_line(line)
                elif _REPAIRED.search(plain):
                    repair_states.append("gelukt")
                elif _REPAIR_OK.search(plain):
                    repair_states.append("niet_nodig")
        elif name in ("Unpack", "Filejoin"):
            for line in lines:
                plain = _strip_set(line)
                if _UNPACK_FAILED.search(plain):
                    unpack_fail = unpack_fail or line
                elif _UNPACK_OK.search(plain):
                    unpack_ok = True
                elif _UNWANTED_REMOVED.search(plain):
                    unwanted_removed = True

    if msg and _ABORTED.search(msg):
        facts.aborted = True
    if facts.has_download and facts.missing_articles is None:
        facts.missing_articles = 0  # SABnzbd only writes the line when articles were missing
    if not unpack_fail and msg and _UNPACK_FAILED.search(msg):
        unpack_fail = msg  # e.g. 7-Zip's "Unpacking failed, see logfile" has no Unpack line
    if msg and problem_of(msg) == "executable":
        problems.append("executable")

    for state in ("mislukt", "gelukt", "niet_nodig"):  # worst set wins
        if state in repair_states:
            facts.repair = state
            break
    if unpack_fail:
        facts.unpack = "mislukt"
        facts.unpack_line = redact_line(unpack_fail)
        kind = problem_of(unpack_fail) or "unpacker_error"
        if kind == "unpacker_error":
            problems.append(kind)  # names no cause: a specific problem from another stage says more
        else:
            problems.insert(0, kind)  # what stopped the unpack is the problem
    elif unpack_ok:
        facts.unpack = "gelukt"
    if unwanted_removed and not unpack_fail:
        problems.append("executable")

    incomplete_articles = (facts.missing_articles or 0) + (facts.malformed_articles or 0)
    if facts.repair == "mislukt":
        problems.insert(0, "repair_failed")
    if facts.aborted or incomplete_articles > 0:
        problems.insert(0, "missing_articles")
    facts.problems = list(dict.fromkeys(problems))

    # Data completeness, only from SABnzbd's own lines
    if facts.aborted or facts.repair == "mislukt":
        facts.data_complete = False
    elif incomplete_articles > 0:
        facts.data_complete = facts.repair in ("gelukt", "niet_nodig")
    elif facts.has_download or facts.repair in ("gelukt", "niet_nodig"):
        facts.data_complete = True

    # The main problem: incomplete data first (then nothing after it matters), then what stopped the unpack
    if facts.data_complete is False:
        facts.problem = "repair_failed" if facts.repair == "mislukt" else "missing_articles"
    else:
        facts.problem = next((p for p in facts.problems if p not in ("missing_articles", "repair_failed")), None)
    line = facts.unpack_line if facts.problem not in ("missing_articles", "repair_failed") else facts.repair_line
    facts.signature = signature(facts.problem, line or (redact_line(msg) if msg else None))
    return facts


# ------------------------------------------------------------------ helpers on stored meta
def facts_of(meta: Any) -> dict[str, Any] | None:
    """The stored facts of an outcome (``meta.stage``), or ``None`` (classifier version 1 / no stage log)."""
    if not isinstance(meta, dict):
        return None
    stage = meta.get("stage")
    return stage if isinstance(stage, dict) else None


def informative(facts: dict[str, Any] | StageFacts | None) -> bool:
    """Facts that say something about the data (download report, repair result or an abort)."""
    if facts is None:
        return False
    f = facts.as_dict() if isinstance(facts, StageFacts) else facts
    return bool(f.get("has_download") or f.get("repair") or f.get("aborted"))


def data_missing(facts: dict[str, Any] | StageFacts | None) -> bool | None:
    """True: articles were missing / repair failed / SABnzbd aborted; False: the data was complete; None: unknown."""
    if not informative(facts):
        return None
    f = facts.as_dict() if isinstance(facts, StageFacts) else facts  # type: ignore[union-attr]
    if f.get("data_complete") is False:
        return True
    if f.get("data_complete") is True:
        return False
    return None


def summary_nl(facts: dict[str, Any] | StageFacts | None) -> str | None:
    """One Dutch line with the facts, e.g. "Data compleet · uitpakken mislukt: …" or "312 artikelen ontbraken"."""
    if facts is None:
        return None
    f = facts.as_dict() if isinstance(facts, StageFacts) else facts
    parts: list[str] = []
    missing = int(f.get("missing_articles") or 0)
    malformed = int(f.get("malformed_articles") or 0)
    if f.get("data_complete") is True:
        parts.append("Data compleet" + (f" ({_n(missing)} ontbrekende artikelen hersteld)" if missing else ""))
    elif f.get("data_complete") is False:
        if missing:
            parts.append(f"{_n(missing)} artikelen ontbraken")
        if malformed:
            parts.append(f"{_n(malformed)} artikelen beschadigd")
        if not missing and not malformed and f.get("aborted"):
            parts.append("SABnzbd vond de artikelen niet (download afgebroken)")
        if f.get("repair") == "mislukt":
            short = f.get("blocks_short")
            parts.append("herstel mislukt" + (f" ({_n(int(short))} blokken tekort)" if short else ""))
    if f.get("unpack") == "mislukt" and f.get("data_complete") is not False:
        parts.append(f"uitpakken mislukt: {f.get('unpack_line') or '—'}")
    return " · ".join(parts) or None


def _n(v: int) -> str:
    return f"{v:,}".replace(",", ".")


__all__ = [
    "CERTAIN_ENVIRONMENT",
    "CLASSIFIER_VERSION",
    "ENVIRONMENT",
    "PROBLEM_LABELS",
    "StageFacts",
    "data_missing",
    "facts_of",
    "informative",
    "parse",
    "problem_of",
    "redact_line",
    "signature",
    "summary_nl",
]
