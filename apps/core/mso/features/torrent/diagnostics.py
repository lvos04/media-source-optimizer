"""Read-only bottleneck classifier for torrents and the client.

Pure functions: input = client data (torrents/info row, normalised trackers, server_state, preferences, recent
samples), output = findings ``{code, title, explanation, severity, confidence, evidence, suggestion,
action}``.
Confidence reflects how direct the evidence is (e.g. "0 seeds" reported by a *working* tracker is strong; the
same number while all trackers fail is weak). Nothing here writes anything.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

INFINITE_ETA = 8640000
SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}
DL_STATES = {
    "downloading",
    "forcedDL",
    "metaDL",
    "forcedMetaDL",
    "stalledDL",
    "queuedDL",
    "checkingDL",
    "allocating",
    "pausedDL",
    "stoppedDL",
}
ACTIVE_DL = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "stalledDL"}
DISK_BUSY_STATES = {"checkingDL", "checkingUP", "allocating", "moving", "checkingResumeData"}
STOPPED_UP = {"pausedUP", "stoppedUP"}
BAD_TRACKER = {"not_working", "tracker_error", "unreachable"}
GB = 1024**3

# ------------------------------------------------------------------ payload integrity (malware-like releases)
# Media downloads never contain programs, scripts or shortcuts. Fake releases ("Show S01E01 1080p.exe",
# "... x265-MeGusta.scr") are a known malware vector on public trackers.
EXEC_EXT = {
    "exe", "scr", "com", "bat", "cmd", "msi", "lnk", "js", "jse", "vbs", "vbe", "ps1", "jar", "apk",
    "pif", "hta", "wsf", "cpl", "msp",
}  # fmt: skip
# extensions that are only suspicious inside a media download (legitimate elsewhere)
MEDIA_ONLY_EXT = {"zipx"}
MEDIA_EXT = {
    "mkv", "mp4", "avi", "m4v", "mov", "wmv", "ts", "m2ts", "webm", "mpg", "mpeg", "flv", "srt", "ass",
    "sub", "idx", "nfo", "jpg", "png", "mp3", "flac", "aac",
}  # fmt: skip
ARCHIVE_EXT = {"rar", "zip", "7z", "zipx"}
RELEASE_RE = re.compile(
    r"(?i)\b(s\d{1,2}e\d{1,3}|\d{3,4}p|x26[45]|h\.?26[45]|hevc|web-?dl|webrip|bluray|bdrip|hdtv|remux)\b"
)
PASSWORD_HINT_RE = re.compile(r"(?i)(pass(word)?|wachtwoord|unlock)[^/\\]*\.(txt|url|html?|lnk|exe)$")
PAYLOAD_MAX_FILES = 2000


def _exts(name: str) -> list[str]:
    """Lower-case extensions of the last path segment, outermost last ('a.mkv.exe' → ['mkv', 'exe'])."""
    base = re.split(r"[/\\]", name.strip())[-1].strip()
    parts = [p.strip().lower() for p in base.split(".")[1:]]
    return [p for p in parts if p and len(p) <= 5 and p.isalnum()]


def looks_like_media(name: str, media_category: str | None) -> bool:
    return media_category is not None or bool(RELEASE_RE.search(name or ""))


def payload_scan(
    name: str, files: list[dict[str, Any]] | None, *, media: bool
) -> dict[str, Any]:
    """Pure check of a torrent's name and file list for executable/script/shortcut payloads.

    Returns ``{matches: [{where, name, ext, reason}], files_checked}``. Only flags programs when the torrent is
    a media download (``media``): a software torrent with an .exe is not suspicious by itself."""
    matches: list[dict[str, Any]] = []
    if not media:
        return {"matches": [], "files_checked": len(files or [])}

    def check(where: str, n: str, is_name: bool) -> None:
        ex = _exts(n)
        if not ex:
            return
        last = ex[-1]
        double = len(ex) >= 2 and ex[-2] in MEDIA_EXT
        # '.com' at the end of a torrent *name* is usually a site tag ('... - site.com'); only a double
        # extension ('.mkv.com') counts there
        if last in EXEC_EXT and not (is_name and last == "com" and not double):
            reason = "dubbele extensie (vermomd als video)" if double else "uitvoerbaar bestand/script/snelkoppeling"
            matches.append({"where": where, "name": n[-200:], "ext": last, "reason": reason})
        elif last in MEDIA_ONLY_EXT:
            matches.append(
                {"where": where, "name": n[-200:], "ext": last, "reason": "ongebruikelijk archief in media-download"}
            )
        elif not is_name and PASSWORD_HINT_RE.search(n):
            matches.append(
                {"where": where, "name": n[-200:], "ext": last, "reason": "wachtwoordhint (beveiligd archief)"}
            )

    check("naam", name or "", True)
    file_names = [str(f.get("name") or "") for f in (files or [])[:PAYLOAD_MAX_FILES]]
    for fn in file_names:
        check("bestand", fn, False)
    has_archive = any((_exts(fn) or [""])[-1] in ARCHIVE_EXT for fn in file_names)
    has_hint = any(m["reason"].startswith("wachtwoordhint") for m in matches)
    if has_hint and not has_archive:
        # a "password" text file without an archive is odd but not the classic locked-archive scam
        matches = [m for m in matches if not m["reason"].startswith("wachtwoordhint")]
    # de-duplicate and keep the evidence small
    seen, uniq = set(), []
    for m in matches:
        key = (m["where"], m["name"])
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    return {"matches": uniq[:20], "match_count": len(uniq), "files_checked": len(file_names)}


def payload_finding(scan: dict[str, Any] | None, source: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not scan or not scan.get("matches"):
        return None
    m = scan["matches"]
    strong = any(x["reason"] != "wachtwoordhint (beveiligd archief)" and x["ext"] != "zipx" for x in m)
    src = source or {}
    blame = ", ".join(
        x
        for x in (
            f"tracker {src['tracker']}" if src.get("tracker") else "",
            f"indexer {src['indexer']}" if src.get("indexer") else "",
            f"bronpagina {src['comment_host']}" if src.get("comment_host") else "",
            f"categorie {src['category']}" if src.get("category") else "",
        )
        if x
    )
    first = m[0]
    return finding(
        "suspicious_payload",
        "Verdacht bestand (uitvoerbaar) in media-download",
        "Media-downloads (series, films, anime) bevatten nooit programma's, scripts of snelkoppelingen. "
        f"Deze torrent bevat '{first['name']}' ({first['reason']})"
        + (f" en nog {scan.get('match_count', len(m)) - 1} verdacht(e) bestand(en)" if len(m) > 1 else "")
        + ". Dit is vrijwel zeker een nep-release met malware, geen echte aflevering of film."
        + (f" Herkomst: {blame}." if blame else ""),
        severity="critical",
        confidence=0.95 if strong else 0.75,
        evidence={"matches": m, "files_checked": scan.get("files_checked"), "source": src},
        suggestion="Open niets uit deze download. Stop of verwijder de torrent zelf in qBittorrent (MSO "
        "verwijdert nooit iets), controleer de indexer/tracker waar hij vandaan kwam en blokkeer de release of "
        "indexer in Sonarr/Radarr (bijv. via de blocklist en een release-profiel dat deze extensies weigert).",
    )


def finding(
    code: str,
    title: str,
    explanation: str,
    *,
    severity: str = "warning",
    confidence: float = 0.7,
    evidence: dict[str, Any] | None = None,
    suggestion: str | None = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "explanation": explanation,
        "severity": severity,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "evidence": evidence or {},
        "suggestion": suggestion,
        "action": action,
    }


def _pct(v: Any) -> float:
    try:
        return float(str(v).rstrip("%") or 0)
    except ValueError:
        return 0.0


def human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def human_rate(bps: float | None) -> str:
    return "?" if bps is None else f"{human_bytes(bps)}/s"


def eta_assessment(
    t: dict[str, Any], samples: list[dict[str, Any]], *, factor: float = 3.0
) -> dict[str, Any]:
    """Is the client's ETA believable? Uses the recent average speed (samples, newest first) and
    availability."""
    eta = int(t.get("eta") or INFINITE_ETA)
    left = int(t.get("amount_left") or 0)
    avail = t.get("availability")
    speeds = [int(s.get("dlspeed") or 0) for s in samples[:30]]
    avg = statistics.fmean(speeds) if speeds else None
    realistic = int(left / avg) if avg and avg > 0 and left > 0 else None
    out: dict[str, Any] = {
        "eta_s": None if eta >= INFINITE_ETA else eta,
        "realistic_eta_s": realistic,
        "avg_speed_bps": int(avg) if avg is not None else None,
        "samples": len(speeds),
        "unreliable": False,
        "reasons": [],
    }
    if float(t.get("progress") or 0) >= 1 or eta >= INFINITE_ETA or left <= 0:
        return out
    try:
        av = float(avail) if avail is not None else -1.0
    except (TypeError, ValueError):
        av = -1.0
    if 0 <= av < 1:
        out["unreliable"] = True
        out["reasons"].append(f"beschikbaarheid {av:.2f} < 1: niet alle stukken zijn in de swarm aanwezig")
    if t.get("state") in ("stalledDL", "metaDL") and int(t.get("dlspeed") or 0) == 0:
        out["unreliable"] = True
        out["reasons"].append("geen voortgang op dit moment terwijl er wel een ETA wordt getoond")
    if avg is not None and len(speeds) >= 3 and eta > 0:
        implied = left / eta
        if avg == 0 or implied > factor * avg:
            out["unreliable"] = True
            out["reasons"].append(
                f"ETA veronderstelt {human_rate(implied)}, gemeten gemiddelde is {human_rate(avg)}"
            )
    return out


def diagnose_torrent(
    t: dict[str, Any],
    trackers: list[dict[str, Any]] | None,
    *,
    server_state: dict[str, Any],
    prefs: dict[str, Any],
    samples: list[dict[str, Any]] | None = None,
    obligation: dict[str, Any] | None = None,
    reserve_bytes: int = 0,
    false_eta_factor: float = 3.0,
    stalled_for_h: float | None = None,
    payload: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    samples = samples or []
    st = str(t.get("state") or "unknown")
    progress = float(t.get("progress") or 0)
    complete = progress >= 1
    left = int(t.get("amount_left") or 0)
    seeds = int(t.get("num_seeds") or 0)
    swarm_seeds = int(t.get("num_complete") if t.get("num_complete") not in (None, -1) else -1)
    avail = float(t.get("availability") if t.get("availability") is not None else -1)
    dlspeed = int(t.get("dlspeed") or 0)
    real = [tr for tr in (trackers or []) if tr.get("kind") == "tracker"]
    working = [tr for tr in real if tr.get("status") == "working"]
    bad = [tr for tr in real if tr.get("status") in BAD_TRACKER]
    firewalled = server_state.get("connection_status") == "firewalled"
    free = server_state.get("free_space_on_disk")
    f: list[dict[str, Any]] = []
    recheck_reason = None

    # --- integrity: executable payload in a media download (never deleted by MSO)
    sus = payload_finding(payload, source)
    if sus:
        f.append(sus)

    # --- error states (recheck only on evidence)
    if st == "missingFiles":
        recheck_reason = "qBittorrent meldt ontbrekende bestanden"
        f.append(
            finding(
                "missing_files",
                "Bestanden ontbreken",
                "qBittorrent vindt (een deel van) de bestanden niet meer op de opslaglocatie. Een recheck "
                "verifieert wat er nog aanwezig is; MSO verplaatst of herstelt zelf niets.",
                severity="critical",
                confidence=0.95,
                evidence={"state": st},
                suggestion="Controleer of de opslag gekoppeld is; daarna een recheck.",
                action={"kind": "recheck", "label": "Recheck voorstellen"},
            )
        )
    elif st == "error":
        recheck_reason = "torrent staat in foutstatus"
        disk = free is not None and left > int(free)
        f.append(
            finding(
                "client_error",
                "Fout in qBittorrent",
                "De torrent staat in foutstatus"
                + (
                    " — waarschijnlijk door onvoldoende schijfruimte."
                    if disk
                    else "; zie de qBittorrent-log voor details."
                ),
                severity="critical",
                confidence=0.8 if disk else 0.6,
                evidence={"state": st, "free_space": free, "amount_left": left},
                suggestion="Los de oorzaak op; een recheck verifieert daarna de data.",
                action={"kind": "recheck", "label": "Recheck voorstellen"},
            )
        )

    # --- disk space (incomplete only)
    if not complete and free is not None:
        free_i = int(free)
        if left > free_i:
            f.append(
                finding(
                    "disk_full",
                    "Onvoldoende schijfruimte",
                    f"Nog {human_bytes(left)} te downloaden, maar slechts {human_bytes(free_i)} vrij.",
                    severity="critical",
                    confidence=0.95,
                    evidence={"amount_left": left, "free_space_on_disk": free_i},
                    suggestion="Maak ruimte vrij of verplaats de downloadmap (MSO verwijdert niets).",
                )
            )
        elif reserve_bytes and left > free_i - reserve_bytes:
            f.append(
                finding(
                    "disk_reserve",
                    "Schijfreserve in gevaar",
                    f"Na afronden blijft {human_bytes(free_i - left)} vrij, minder dan de reserve van "
                    f"{human_bytes(reserve_bytes)}.",
                    severity="warning",
                    confidence=0.85,
                    evidence={"amount_left": left, "free_space_on_disk": free_i, "reserve": reserve_bytes},
                )
            )

    # --- trackers
    if real and len(bad) == len(real):
        msgs = [{"host": tr.get("host"), "status": tr.get("status"), "msg": tr.get("msg")} for tr in bad]
        f.append(
            finding(
                "tracker_error",
                "Tracker onbereikbaar of foutmelding",
                "Geen enkele tracker werkt: "
                + "; ".join(f"{m['host'] or '?'}: {m['msg'] or m['status']}" for m in msgs),
                severity="critical" if (not complete and seeds == 0) else "warning",
                confidence=0.9,
                evidence={"trackers": msgs},
                suggestion="Opnieuw aankondigen kan helpen bij een tijdelijke storing; bij een "
                "private tracker: controleer je account/passkey op de site.",
                action={"kind": "reannounce", "label": "Opnieuw aankondigen voorstellen"},
            )
        )
    elif bad:
        f.append(
            finding(
                "tracker_partial",
                "Eén of meer trackers falen",
                f"{len(bad)} van {len(real)} trackers werken niet; de overige werken wel.",
                severity="info",
                confidence=0.6,
                evidence={"trackers": [{"host": tr.get("host"), "msg": tr.get("msg")} for tr in bad]},
            )
        )

    if not complete and st in DL_STATES:
        # --- queue
        if st == "queuedDL":
            f.append(
                finding(
                    "queued",
                    "Wacht in de wachtrij",
                    f"qBittorrent laat maximaal {prefs.get('max_active_downloads', '?')} downloads "
                    "tegelijk lopen; deze wacht op een vrije plek.",
                    severity="info",
                    confidence=0.9,
                    evidence={
                        "max_active_downloads": prefs.get("max_active_downloads"),
                        "dont_count_slow_torrents": prefs.get("dont_count_slow_torrents"),
                    },
                )
            )
        # --- metadata
        if st in ("metaDL", "forcedMetaDL"):
            f.append(
                finding(
                    "no_metadata",
                    "Metadata nog niet ontvangen",
                    "De torrent heeft nog geen metadata (bestandenlijst) van peers gekregen.",
                    severity="warning",
                    confidence=0.7 if seeds == 0 else 0.5,
                    evidence={"num_seeds": seeds},
                )
            )
        # --- no seeds
        if st in ACTIVE_DL and seeds == 0 and swarm_seeds <= 0 and avail < 1:
            conf = 0.85 if working else 0.45
            f.append(
                finding(
                    "no_seeds",
                    "Geen seeders",
                    "Er is niemand met een complete kopie"
                    + (f"; beschikbaarheid in de swarm is {avail:.2f}." if avail >= 0 else ".")
                    + ("" if working else " (onzeker: trackers geven geen antwoord)"),
                    severity="warning",
                    confidence=conf,
                    evidence={
                        "num_seeds": seeds,
                        "num_complete": swarm_seeds,
                        "availability": avail,
                        "working_trackers": len(working),
                    },
                    suggestion="Wachten of een andere release zoeken; MSO stopt deze torrent niet.",
                )
            )
        # --- stalled with seeds present
        if st == "stalledDL" and (seeds > 0 or swarm_seeds > 0 or avail >= 1):
            if firewalled:
                f.append(
                    finding(
                        "not_connectable",
                        "Niet verbindbaar (poort dicht)",
                        "Er zijn seeders, maar er komt geen data binnen en qBittorrent meldt "
                        "'firewalled': inkomende verbindingen bereiken de client niet.",
                        severity="warning",
                        confidence=0.7,
                        evidence={
                            "connection_status": "firewalled",
                            "listen_port": prefs.get("listen_port"),
                            "num_complete": swarm_seeds,
                        },
                    )
                )
            else:
                f.append(
                    finding(
                        "stalled",
                        "Vastgelopen",
                        "Er zijn seeders in de swarm, maar er komt geen data binnen.",
                        severity="warning",
                        confidence=0.5,
                        evidence={"num_seeds": seeds, "num_complete": swarm_seeds, "availability": avail},
                    )
                )
        elif st == "stalledDL" and not any(x["code"] in ("no_seeds", "tracker_error") for x in f):
            f.append(
                finding(
                    "stalled",
                    "Vastgelopen",
                    "Geen voortgang en geen bekende oorzaak.",
                    severity="warning",
                    confidence=0.4,
                    evidence={"state": st},
                )
            )
        if stalled_for_h is not None and stalled_for_h >= 1 and st in ACTIVE_DL:
            for x in f:
                if x["code"] in ("stalled", "no_seeds", "not_connectable"):
                    x["evidence"]["stalled_for_h"] = round(stalled_for_h, 1)
        # --- rate limits
        dl_lim_global = int(server_state.get("dl_rate_limit") or prefs.get("dl_limit") or 0)
        if server_state.get("use_alt_speed_limits") and st in ACTIVE_DL:
            f.append(
                finding(
                    "alt_speed",
                    "Alternatieve snelheidslimiet actief",
                    "De 'schildpad'-limiet van qBittorrent staat aan.",
                    severity="info",
                    confidence=0.9,
                    evidence={"alt_dl_limit": prefs.get("alt_dl_limit")},
                )
            )
        elif (
            dl_lim_global > 0
            and int(server_state.get("dl_info_speed") or 0) >= 0.9 * dl_lim_global
            and st in ACTIVE_DL
        ):
            f.append(
                finding(
                    "rate_limited_global",
                    "Globale downloadlimiet bereikt",
                    f"Totale downloadsnelheid zit tegen de limiet van {human_rate(dl_lim_global)}.",
                    severity="info",
                    confidence=0.8,
                    evidence={
                        "dl_rate_limit": dl_lim_global,
                        "dl_info_speed": server_state.get("dl_info_speed"),
                    },
                )
            )
        t_lim = int(t.get("dl_limit") or 0)
        if t_lim > 0 and dlspeed >= 0.9 * t_lim:
            f.append(
                finding(
                    "rate_limited_torrent",
                    "Downloadlimiet van deze torrent bereikt",
                    f"Per-torrentlimiet {human_rate(t_lim)}.",
                    severity="info",
                    confidence=0.9,
                    evidence={"dl_limit": t_lim, "dlspeed": dlspeed},
                )
            )
        # --- disk I/O
        if st in ACTIVE_DL and _disk_io_pressure(server_state):
            f.append(
                finding(
                    "disk_io",
                    "Schijf-I/O remt",
                    "qBittorrent heeft een volle schrijfwachtrij of overbelaste cache; de opslag kan de "
                    "binnenkomende data niet bijhouden.",
                    severity="warning",
                    confidence=0.6,
                    evidence=_io_evidence(server_state),
                    suggestion="Minder gelijktijdige downloads of I/O-threads aanpassen (tuningvoorstel).",
                )
            )
        # --- false ETA
        eta = eta_assessment(t, samples, factor=false_eta_factor)
    else:
        eta = eta_assessment(t, [], factor=false_eta_factor)
    if eta["unreliable"]:
        f.append(
            finding(
                "false_eta",
                "ETA onbetrouwbaar",
                "; ".join(eta["reasons"]),
                severity="warning",
                confidence=0.9 if any("beschikbaarheid" in r for r in eta["reasons"]) else 0.7,
                evidence={k: eta[k] for k in ("eta_s", "realistic_eta_s", "avg_speed_bps", "samples")},
            )
        )

    if st in DISK_BUSY_STATES:
        f.append(
            finding(
                "disk_busy",
                "Bezig met controleren/alloceren",
                "De torrent wordt gecontroleerd of verplaatst; dit belast de schijf tijdelijk.",
                severity="info",
                confidence=0.9,
                evidence={"state": st},
            )
        )

    # --- seed policy (complete torrents)
    if complete and obligation:
        if st in STOPPED_UP and obligation.get("status") == "loopt":
            f.append(
                finding(
                    "seed_obligation_unmet",
                    "Gestopt vóór seedverplichting",
                    "Deze torrent seedt niet meer, terwijl de ingestelde ratio/seedtijd nog niet gehaald "
                    "is. Dat kan je account bij een private tracker schaden.",
                    severity="warning",
                    confidence=0.9,
                    evidence=obligation,
                    suggestion="Hervat seeden in qBittorrent (MSO hervat of stopt torrents nooit zelf).",
                )
            )
        elif obligation.get("warning"):
            f.append(
                finding(
                    "seed_rule_missing",
                    "Geen seedregel voor private tracker",
                    "Deze private torrent valt onder geen enkele trackerregel; MSO kan seedverplichtingen "
                    "dan niet bewaken en weigert deelgrenzen.",
                    severity="info",
                    confidence=0.9,
                    evidence={"tracker": obligation.get("tracker")},
                )
            )

    f.sort(key=lambda x: (SEVERITY_RANK[x["severity"]], x["confidence"]), reverse=True)
    return {
        "primary": f[0] if f else None,
        "findings": f,
        "eta": eta,
        "recheck_suggested": recheck_reason is not None,
        "recheck_reason": recheck_reason,
    }


def _disk_io_pressure(s: dict[str, Any]) -> bool:
    return (
        int(s.get("queued_io_jobs") or 0) > 128
        or int(s.get("average_time_queue") or 0) > 1500
        or _pct(s.get("write_cache_overload")) > 0
    )


def _io_evidence(s: dict[str, Any]) -> dict[str, Any]:
    return {
        k: s.get(k)
        for k in (
            "queued_io_jobs",
            "average_time_queue",
            "total_queued_size",
            "write_cache_overload",
            "read_cache_overload",
        )
    }


def diagnose_client(
    server_state: dict[str, Any],
    prefs: dict[str, Any],
    *,
    expected_interface: str = "",
    interfaces: list[dict[str, Any]] | None = None,
    torrents: list[dict[str, Any]] | None = None,
    upload_cap_bps: int | None = None,
    reserve_bytes: int = 0,
    suspicious: list[dict[str, Any]] | None = None,
    pref_violations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    torrents = torrents or []
    f: list[dict[str, Any]] = []
    if suspicious:
        sources = sorted({s.get("tracker") or s.get("indexer") or "onbekend" for s in suspicious})
        f.append(
            finding(
                "suspicious_torrents",
                f"{len(suspicious)} verdachte torrent(s) met uitvoerbare bestanden",
                f"{len(suspicious)} media-download(s) bevatten programma's/scripts (bijv. .exe of .scr) in "
                "plaats van video. Dit zijn vrijwel zeker nep-releases met malware. Herkomst: "
                + ", ".join(sources[:5])
                + ("…" if len(sources) > 5 else "")
                + ".",
                severity="critical",
                confidence=0.9,
                evidence={"torrents": [s.get("name") for s in suspicious[:20]], "sources": sources},
                suggestion="Filter de torrentlijst op 'Problemen', verwijder ze zelf in qBittorrent en blokkeer "
                "de bron in Sonarr/Radarr. MSO verwijdert nooit iets.",
            )
        )
    if pref_violations:
        f.append(
            finding(
                "prefs_out_of_bounds",
                "Instellingen buiten de veilige grens",
                "; ".join(
                    f"{v['label']}: {'onbeperkt' if v['value'] <= 0 else v['value']} (veilig "
                    f"{v['min'] if v['min'] is not None else '…'}–{v['max'] if v['max'] is not None else '…'})"
                    for v in pref_violations
                )
                + ". Extreme waarden kosten geheugen/CPU en overbelasten router en VPN zonder sneller te worden.",
                severity="warning",
                confidence=0.9,
                evidence={v["key"]: v["value"] for v in pref_violations},
                suggestion="Bereken een tuningvoorstel: MSO brengt de grootste afwijking als eerste terug "
                "(met jouw toestemming).",
            )
        )
    cs = server_state.get("connection_status")
    port = prefs.get("listen_port")
    if cs == "disconnected":
        f.append(
            finding(
                "disconnected",
                "Geen netwerkverbinding",
                "qBittorrent meldt dat er geen verbinding is (interface weg of VPN uit?).",
                severity="critical",
                confidence=0.9,
                evidence={"connection_status": cs},
            )
        )
    elif cs == "firewalled":
        f.append(
            finding(
                "firewalled",
                "Poort niet bereikbaar",
                f"Inkomende verbindingen op poort {port} komen niet aan. Je kunt dan alleen met "
                "'verbindbare' peers praten; bij weinig seeders betekent dat trage of vastgelopen "
                "downloads. qBittorrent leidt dit af uit het uitblijven van inkomende verbindingen, dus "
                "bij weinig activiteit kan dit een vals alarm zijn.",
                severity="warning",
                confidence=0.7,
                evidence={
                    "connection_status": cs,
                    "listen_port": port,
                    "upnp": prefs.get("upnp"),
                    "random_port": prefs.get("random_port"),
                },
                suggestion="Zet port forwarding aan op je VPN-provider/router voor deze poort.",
            )
        )
    if prefs.get("dht") and int(server_state.get("dht_nodes") or 0) == 0 and cs != "disconnected":
        f.append(
            finding(
                "dht_no_nodes",
                "DHT zonder nodes",
                "DHT staat aan maar heeft 0 nodes; UDP-verkeer wordt mogelijk geblokkeerd.",
                severity="info",
                confidence=0.5,
                evidence={"dht_nodes": server_state.get("dht_nodes")},
            )
        )
    bound = str(prefs.get("current_network_interface") or "")
    if expected_interface:
        names = {str(i.get("value") or i.get("name")) for i in interfaces or []}
        if bound != expected_interface:
            f.append(
                finding(
                    "vpn_binding_mismatch",
                    "Niet gebonden aan de VPN-interface",
                    (
                        f"qBittorrent is gebonden aan '{bound}'"
                        if bound
                        else "qBittorrent is aan geen specifieke interface gebonden (alle interfaces)"
                    )
                    + f", verwacht '{expected_interface}'. Verkeer kan dan buiten de VPN om lopen als "
                    "de VPN wegvalt.",
                    severity="critical",
                    confidence=0.95,
                    evidence={
                        "current_network_interface": bound,
                        "expected": expected_interface,
                        "current_interface_address": prefs.get("current_interface_address"),
                    },
                    suggestion="Stel in qBittorrent → Geavanceerd → Netwerkinterface de VPN-interface in. "
                    "MSO wijzigt deze binding bewust niet zelf.",
                )
            )
        if names and expected_interface not in names:
            f.append(
                finding(
                    "vpn_interface_missing",
                    "VPN-interface niet gevonden",
                    f"De interface '{expected_interface}' bestaat niet in de qBittorrent-container "
                    f"(beschikbaar: {', '.join(sorted(names))}).",
                    severity="warning",
                    confidence=0.8,
                    evidence={"interfaces": sorted(names)},
                )
            )
    elif not bound:
        f.append(
            finding(
                "binding_unknown",
                "VPN-binding niet bewaakt",
                "Stel bij Instellingen → Torrent een verwachte VPN-interface in om de binding te bewaken.",
                severity="info",
                confidence=1.0,
                evidence={"current_network_interface": bound},
            )
        )
    up = prefs.get("up_limit")
    if upload_cap_bps is not None and up is not None and (int(up) == 0 or int(up) > upload_cap_bps):
        f.append(
            finding(
                "upload_over_cap",
                "Uploadlimiet boven de veilige grens",
                f"Upload is {'onbeperkt' if int(up) == 0 else human_rate(int(up))}; de grens is "
                f"{human_rate(upload_cap_bps)} (percentage van je lijncapaciteit). Een volle uplink "
                "vertraagt ook downloads en ander verkeer.",
                severity="warning",
                confidence=0.9,
                evidence={"up_limit": up, "max_upload_bps": upload_cap_bps},
                suggestion="Maak een voorstel voor de uploadlimiet.",
            )
        )
    if server_state.get("use_alt_speed_limits"):
        f.append(
            finding(
                "alt_speed",
                "Alternatieve snelheidslimiet actief",
                "De 'schildpad'-limiet staat aan.",
                severity="info",
                confidence=0.95,
                evidence={
                    "alt_dl_limit": prefs.get("alt_dl_limit"),
                    "alt_up_limit": prefs.get("alt_up_limit"),
                },
            )
        )
    if _disk_io_pressure(server_state):
        f.append(
            finding(
                "disk_io_pressure",
                "Schijf-I/O overbelast",
                "Schrijfwachtrij of cache is overbelast.",
                severity="warning",
                confidence=0.6,
                evidence=_io_evidence(server_state),
            )
        )
    free = server_state.get("free_space_on_disk")
    if free is not None:
        left = sum(int(t.get("amount_left") or 0) for t in torrents if float(t.get("progress") or 0) < 1)
        after = int(free) - left
        if after < 0:
            f.append(
                finding(
                    "disk_insufficient",
                    "Onvoldoende ruimte voor alle downloads",
                    f"Nog {human_bytes(left)} te downloaden, {human_bytes(int(free))} vrij.",
                    severity="critical",
                    confidence=0.9,
                    evidence={"free": free, "remaining": left},
                )
            )
        elif reserve_bytes and after < reserve_bytes:
            f.append(
                finding(
                    "disk_reserve",
                    "Schijfreserve in gevaar",
                    f"Na alle downloads blijft {human_bytes(after)} vrij (reserve "
                    f"{human_bytes(reserve_bytes)}).",
                    severity="warning",
                    confidence=0.85,
                    evidence={"free": free, "remaining": left, "reserve": reserve_bytes},
                )
            )
    f.sort(key=lambda x: (SEVERITY_RANK[x["severity"]], x["confidence"]), reverse=True)
    return f


def connectivity(
    server_state: dict[str, Any],
    prefs: dict[str, Any],
    *,
    expected_interface: str,
    interfaces: list[dict[str, Any]],
) -> dict[str, Any]:
    """Port / connectivity / interface summary for the UI card (no external probing, no IP addresses
    stored)."""
    cs = server_state.get("connection_status")
    return {
        "connection_status": cs,
        "listen_port": prefs.get("listen_port"),
        "upnp": prefs.get("upnp"),
        "random_port": prefs.get("random_port"),
        "dht": prefs.get("dht"),
        "pex": prefs.get("pex"),
        "lsd": prefs.get("lsd"),
        "dht_nodes": server_state.get("dht_nodes"),
        "total_peer_connections": server_state.get("total_peer_connections"),
        "bound_interface": prefs.get("current_network_interface") or "",
        "bound_address": prefs.get("current_interface_address") or "",
        "expected_interface": expected_interface,
        "interfaces": [i.get("value") or i.get("name") for i in interfaces],
        "binding_ok": (
            None
            if not expected_interface
            else (prefs.get("current_network_interface") or "") == expected_interface
        ),
        "port_ok": None if cs is None else cs == "connected",
    }
