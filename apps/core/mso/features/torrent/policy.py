"""Torrent policies & guardrails.

Hard rules (enforced in ``validate`` of the ``qbittorrent`` change-set applier — every write passes here,
both at
preview and again at apply):

- Private torrents never get public trackers/DHT/PEX/LSD: there is no tracker-add path at all; enabling global
  DHT/PeX/LSD or the "automatically add trackers" preference is refused while any private torrent exists
  (add_trackers is refused always).
- MSO never deletes torrents or files through this validator: any path or value that would remove
  (``max_ratio_act`` 1/3 = remove (+files), any "delete"/"remove" path) is refused. The single exception is the
  malware cleanup (``cleanup.py``), whose paths the applier validates separately against a fresh scan.
- Seed obligations: share limits (per torrent or global) that would stop seeding before the obligation of a
  matching tracker/category rule is met are refused.
- Upload cap never above ``torrent.upload_cap_pct`` of the declared line capacity (unlimited counts as above).
- Connection counts within the configured caps; ``-1`` (unlimited) is refused.
"""

from __future__ import annotations

import re
from typing import Any

from mso.db import Database, get_db
from mso.features.torrent import settings as ts

PRIVATE_SENSITIVE_PREFS = ("dht", "pex", "lsd")
REMOVE_ACTIONS = {
    1,
    3,
}  # qBittorrent max_ratio_act: 0 pause/stop, 1 remove, 2 super seeding, 3 remove + files
ALLOWED_PREFS: dict[str, dict[str, Any]] = {
    # key: {label, kind, min, max}
    "max_connec": {"label": "Maximum verbindingen (globaal)", "kind": "int", "min": 20},
    "max_connec_per_torrent": {"label": "Maximum verbindingen per torrent", "kind": "int", "min": 5},
    "max_uploads": {"label": "Maximum uploadslots (globaal)", "kind": "int", "min": 1, "max": 500},
    "max_uploads_per_torrent": {
        "label": "Maximum uploadslots per torrent",
        "kind": "int",
        "min": 1,
        "max": 100,
    },
    "up_limit": {"label": "Globale uploadlimiet", "kind": "rate"},
    "dl_limit": {"label": "Globale downloadlimiet", "kind": "rate"},
    "alt_up_limit": {"label": "Alternatieve uploadlimiet", "kind": "rate"},
    "alt_dl_limit": {"label": "Alternatieve downloadlimiet", "kind": "rate"},
    "queueing_enabled": {"label": "Wachtrij actief", "kind": "bool"},
    "max_active_downloads": {"label": "Maximum actieve downloads", "kind": "int", "min": 1, "max": 100},
    "max_active_uploads": {"label": "Maximum actieve uploads", "kind": "int", "min": 1, "max": 1000},
    "max_active_torrents": {"label": "Maximum actieve torrents", "kind": "int", "min": 1, "max": 2000},
    "dont_count_slow_torrents": {"label": "Trage torrents niet meetellen in wachtrij", "kind": "bool"},
    "max_ratio_enabled": {"label": "Globale ratiogrens actief", "kind": "bool"},
    "max_ratio": {"label": "Globale ratiogrens", "kind": "float"},
    "max_seeding_time_enabled": {"label": "Globale seedtijdgrens actief", "kind": "bool"},
    "max_seeding_time": {"label": "Globale seedtijdgrens (min)", "kind": "int"},
    "max_ratio_act": {"label": "Actie bij bereiken deelgrens", "kind": "int"},
    "async_io_threads": {"label": "Asynchrone I/O-threads", "kind": "int", "min": 1, "max": 64},
    "hashing_threads": {"label": "Hashing-threads", "kind": "int", "min": 1, "max": 16},
    "disk_queue_size": {"label": "Schijfwachtrij (bytes)", "kind": "int", "min": 65536, "max": 1073741824},
    "dht": {"label": "DHT", "kind": "bool"},
    "pex": {"label": "Peer exchange (PeX)", "kind": "bool"},
    "lsd": {"label": "Lokale peer-ontdekking (LSD)", "kind": "bool"},
    "add_trackers_enabled": {"label": "Automatisch trackers toevoegen", "kind": "bool"},
    "add_trackers": {"label": "Automatisch toe te voegen trackers", "kind": "string"},
}
PATH_RE = re.compile(
    r"^(pref:[a-z_]+|transfer:alt_speed|torrent:[0-9a-f]{32,64}:(up_limit|dl_limit|share_limits|"
    r"recheck|reannounce)|crossseed:\d+)$"
)


RATE_PREFS = tuple(k for k, m in ALLOWED_PREFS.items() if m["kind"] == "rate")
KIB_TOLERANCE = 1024  # qBittorrent keeps speed limits in whole KiB/s


class PolicyError(ValueError):
    pass


def is_rate_path(path: str) -> bool:
    """Applier paths whose value is a speed limit in bytes/s (stored by qBittorrent in whole KiB/s)."""
    kind, _, rest = path.partition(":")
    if kind == "pref":
        return rest in RATE_PREFS
    if kind == "torrent":
        return rest.rpartition(":")[2] in ("up_limit", "dl_limit")
    return False


def rate_matches(requested: Any, actual: Any) -> bool:
    """True when ``actual`` is what qBittorrent makes of ``requested``: equal, or the same limit rounded to
    whole KiB/s (difference < 1024 bytes/s). 0 and -1 both mean "no limit" in qBittorrent and match each
    other, never a real limit."""
    if isinstance(requested, bool) or isinstance(actual, bool):
        return False
    try:
        r, a = int(requested), int(actual)
    except (TypeError, ValueError):
        return False
    if r == a or (r <= 0 and a <= 0):
        return True
    if r <= 0 or a <= 0:
        return False
    return abs(r - a) < KIB_TOLERANCE


# ------------------------------------------------------------------ guardrail bounds (connections / slots)
def pref_bounds(key: str) -> tuple[int | None, int | None]:
    """Safe (min, max) for a numeric preference: configured caps for connection counts, else ALLOWED_PREFS."""
    meta = ALLOWED_PREFS.get(key) or {}
    lo, hi = meta.get("min"), meta.get("max")
    if key == "max_connec":
        hi = int(ts.get("max_connections_cap"))
    elif key == "max_connec_per_torrent":
        hi = int(ts.get("max_connections_per_torrent_cap"))
    return lo, hi


# preferences tuning brings back within bounds when qBittorrent holds an extreme value (-1 = unlimited)
GUARDED_PREFS = ("max_connec", "max_connec_per_torrent", "max_uploads", "max_uploads_per_torrent")


def pref_violations(prefs: dict[str, Any]) -> list[dict[str, Any]]:
    """Connection/upload-slot preferences outside the safe bounds, largest violation first.

    ``severity`` = value / max (``inf`` for unlimited ``-1``/``0``); ``target`` = the nearest safe value."""
    out = []
    for key in GUARDED_PREFS:
        v = prefs.get(key)
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        lo, hi = pref_bounds(key)
        if v <= 0:  # -1 (and 0 for these keys) = unlimited in qBittorrent
            if hi is None:
                continue
            sev, target = float("inf"), hi
        elif hi is not None and v > hi:
            sev, target = v / hi, hi
        elif lo is not None and v < lo:
            sev, target = lo / max(v, 1), lo
        else:
            continue
        out.append(
            {
                "key": key,
                "label": ALLOWED_PREFS[key]["label"],
                "value": v,
                "min": lo,
                "max": hi,
                "target": target,
                "severity": sev,
            }
        )
    out.sort(key=lambda x: x["severity"], reverse=True)
    return out


# ------------------------------------------------------------------ tracker / category rules
def list_rules(db: Database | None = None) -> list[dict[str, Any]]:
    return (db or get_db()).fetchall(
        "SELECT * FROM torrent_tracker_rules ORDER BY tracker_host NULLS LAST, category"
    )


def matching_rules(
    tracker_host: str | None, category: str | None, rules: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    rules = list_rules() if rules is None else rules
    out = []
    for r in rules:
        if r["tracker_host"] and (not tracker_host or r["tracker_host"].lower() != tracker_host.lower()):
            continue
        if r["category"] and (not category or r["category"].lower() != (category or "").lower()):
            continue
        out.append(r)
    return out


def obligation_for(
    info: dict[str, Any],
    tracker_host: str | None,
    private: bool | None,
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Seed obligation status of one torrent. Every matching rule must be satisfied (conservative).
    Rule mode 'either': ratio OR seed time met; 'both': both met."""
    matches = matching_rules(tracker_host, info.get("category"), rules)
    ratio = float(info.get("ratio") or 0)
    seed_h = float(info.get("seeding_time") or 0) / 3600
    complete = float(info.get("progress") or 0) >= 1
    parts = []
    all_met = True
    for r in matches:
        need_ratio, need_h = r["min_ratio"], r["min_seed_time_h"]
        ratio_ok = need_ratio is None or ratio >= need_ratio
        time_ok = need_h is None or seed_h >= need_h
        if need_ratio is None and need_h is None:
            met = True
        elif r["mode"] == "both":
            met = ratio_ok and time_ok
        else:
            met = (need_ratio is not None and ratio_ok) or (need_h is not None and time_ok)
        all_met = all_met and met
        parts.append(
            {
                "rule_id": r["id"],
                "label": r["label"] or r["tracker_host"] or r["category"],
                "min_ratio": need_ratio,
                "min_seed_time_h": need_h,
                "mode": r["mode"],
                "met": met,
                "ratio_progress": None if not need_ratio else min(1.0, ratio / need_ratio),
                "time_progress": None if not need_h else min(1.0, seed_h / need_h),
            }
        )
    status = "geen_regel" if not matches else ("gehaald" if all_met else "loopt")
    if matches and not complete:
        status = "nog_downloaden"
    return {
        "status": status,
        "met": all_met,
        "rules": parts,
        "ratio": round(ratio, 3),
        "seeding_time_h": round(seed_h, 2),
        "private": private,
        "warning": bool(private and not matches),
    }


def required_minimums(
    tracker_host: str | None, category: str | None, rules: list[dict[str, Any]] | None = None
) -> tuple[float | None, float | None]:
    """Strictest (ratio, hours) over matching rules — used to refuse share limits below them."""
    ratio = hours = None
    for r in matching_rules(tracker_host, category, rules):
        if r["min_ratio"] is not None:
            ratio = max(ratio or 0, r["min_ratio"])
        if r["min_seed_time_h"] is not None:
            hours = max(hours or 0, r["min_seed_time_h"])
    return ratio, hours


def global_minimums(rules: list[dict[str, Any]] | None = None) -> tuple[float | None, float | None]:
    ratio = hours = None
    for r in list_rules() if rules is None else rules:
        if r["min_ratio"] is not None:
            ratio = max(ratio or 0, r["min_ratio"])
        if r["min_seed_time_h"] is not None:
            hours = max(hours or 0, r["min_seed_time_h"])
    return ratio, hours


def has_private_torrents(db: Database | None = None) -> bool:
    return bool((db or get_db()).scalar("SELECT count(*) FROM torrent_state WHERE private AND NOT removed"))


# ------------------------------------------------------------------ guardrail validation
def validate(path: str, value: Any) -> None:
    """Raise ``PolicyError`` (a ValueError) when a change-set operation violates a guardrail."""
    low = path.lower()
    if (
        "delete" in low
        or "remove" in low
        or ("tracker" in low.split(":")[-1] and not low.startswith("pref:"))
    ):
        raise PolicyError("MSO verwijdert nooit torrents/bestanden en voegt nooit trackers toe")
    if not PATH_RE.match(path):
        raise PolicyError(f"pad '{path}' is niet toegestaan")
    kind, _, rest = path.partition(":")
    if kind == "pref":
        _validate_pref(rest, value)
    elif kind == "torrent":
        h, _, field_ = rest.partition(":")
        _validate_torrent(h, field_, value)
    elif kind == "transfer":
        if not isinstance(value, bool):
            raise PolicyError("alternatieve snelheid is aan/uit")
    elif kind == "crossseed":
        _validate_crossseed(int(rest), value)


def _validate_pref(key: str, value: Any) -> None:
    meta = ALLOWED_PREFS.get(key)
    if meta is None:
        raise PolicyError(f"instelling '{key}' wordt door MSO niet beheerd")
    k = meta["kind"]
    if k == "bool" and not isinstance(value, bool):
        raise PolicyError("verwacht aan/uit")
    if k in ("int", "rate") and (isinstance(value, bool) or not isinstance(value, int)):
        raise PolicyError("verwacht een geheel getal")
    if k == "float" and (isinstance(value, bool) or not isinstance(value, int | float)):
        raise PolicyError("verwacht een getal")
    if "min" in meta and value < meta["min"]:
        raise PolicyError(f"minimaal {meta['min']}")
    if "max" in meta and value > meta["max"]:
        raise PolicyError(f"maximaal {meta['max']}")
    if key in PRIVATE_SENSITIVE_PREFS and value is True and has_private_torrents():
        raise PolicyError(
            f"{meta['label']} aanzetten is geweigerd: er zijn private torrents (geen publieke "
            "DHT/PeX/LSD voor private trackers)"
        )
    if key == "add_trackers_enabled" and value is True:
        raise PolicyError("automatisch trackers toevoegen is geweigerd (trackerinjectie)")
    if key == "add_trackers" and str(value).strip():
        raise PolicyError("trackers toevoegen is geweigerd (trackerinjectie)")
    if key == "max_ratio_act" and int(value) in REMOVE_ACTIONS:
        raise PolicyError(
            "verwijderen bij deelgrens is geweigerd: MSO verwijdert nooit torrents of bestanden"
        )
    if key == "max_connec":
        cap = int(ts.get("max_connections_cap"))
        if value < 0 or value > cap:
            raise PolicyError(f"globaal maximaal {cap} verbindingen (onbeperkt is niet toegestaan)")
    if key == "max_connec_per_torrent":
        cap = int(ts.get("max_connections_per_torrent_cap"))
        if value < 0 or value > cap:
            raise PolicyError(f"maximaal {cap} verbindingen per torrent (onbeperkt is niet toegestaan)")
    if key in ("up_limit", "alt_up_limit"):
        _check_upload(int(value))
    if key in ("dl_limit", "alt_dl_limit") and value < 0:
        raise PolicyError("ongeldige limiet")
    if key == "max_ratio" and value is not None and float(value) >= 0:
        need_ratio, _ = global_minimums()
        if need_ratio is not None and float(value) < need_ratio:
            raise PolicyError(
                f"globale ratiogrens {value} ligt onder een seedverplichting (min. ratio "
                f"{need_ratio}); seeden mag niet eerder stoppen"
            )
    if key == "max_seeding_time" and value is not None and int(value) >= 0:
        _, need_h = global_minimums()
        if need_h is not None and int(value) < need_h * 60:
            raise PolicyError(
                f"globale seedtijdgrens {value} min ligt onder een seedverplichting ({need_h:g} uur)"
            )


def _check_upload(limit_bps: int) -> None:
    cap = ts.upload_cap_bps()
    if limit_bps < 0:
        raise PolicyError("ongeldige limiet")
    if cap is None:
        return  # line capacity unknown → cannot enforce; tuning never proposes upload changes then
    if limit_bps == 0 or limit_bps > cap:
        raise PolicyError(
            f"upload mag maximaal {ts.get('upload_cap_pct')}% van de lijncapaciteit zijn "
            f"({cap * 8 / 1_000_000:.1f} Mbit/s); onbeperkt is niet toegestaan"
        )


def _torrent_row(h: str) -> dict[str, Any] | None:
    return get_db().fetchone("SELECT * FROM torrent_state WHERE hash=%s", (h,))


def _validate_torrent(h: str, field_: str, value: Any) -> None:
    if field_ == "up_limit":
        if int(value) < 0:
            raise PolicyError("ongeldige limiet")
        if int(value) > 0:  # 0 = no per-torrent limit (the global limit applies)
            _check_upload(int(value))
        return
    if field_ == "dl_limit":
        if int(value) < 0:
            raise PolicyError("ongeldige limiet")
        return
    if field_ in ("recheck", "reannounce"):
        if value not in ("uitgevoerd", "niet_uitgevoerd"):
            raise PolicyError("ongeldige actie")
        return
    if field_ == "share_limits":
        if not isinstance(value, dict) or "ratio_limit" not in value or "seeding_time_limit" not in value:
            raise PolicyError("deelgrenzen vereisen ratio_limit en seeding_time_limit")
        row = _torrent_row(h)
        host = row["tracker_host"] if row else None
        cat = row["category"] if row else None
        need_ratio, need_h = required_minimums(host, cat)
        ratio_limit = float(value["ratio_limit"])
        time_limit = int(value["seeding_time_limit"])
        # -2 = use global, -1 = unlimited; any value ≥ 0 stops seeding when reached
        if need_ratio is not None and 0 <= ratio_limit < need_ratio:
            raise PolicyError(f"ratiogrens {ratio_limit} ligt onder de seedverplichting ({need_ratio})")
        if need_h is not None and 0 <= time_limit < need_h * 60:
            raise PolicyError(
                f"seedtijdgrens {time_limit} min ligt onder de seedverplichting ({need_h:g} uur)"
            )
        if (
            row
            and row["private"]
            and (need_ratio is None and need_h is None)
            and (ratio_limit >= 0 or time_limit >= 0)
        ):
            raise PolicyError(
                "private torrent zonder ingestelde seedregel: stel eerst de trackerregels in "
                "voordat deelgrenzen worden gezet"
            )
        return
    raise PolicyError(f"onbekend veld {field_}")


def _validate_crossseed(cid: int, value: Any) -> None:
    if value != "toegevoegd_gepauzeerd":
        raise PolicyError("cross-seed kan alleen gepauzeerd worden toegevoegd; MSO verwijdert nooit torrents")
    row = get_db().fetchone("SELECT * FROM torrent_crossseed_candidates WHERE id=%s", (cid,))
    if row is None:
        raise PolicyError("cross-seed-kandidaat bestaat niet")
    if row["status"] in ("rejected",):
        raise PolicyError("kandidaat is afgewezen")
    if not ts.get("cross_seed_enabled"):
        raise PolicyError("cross-seed staat uit in de instellingen")
    if not crossseed_allowed(row["tracker_host"]):
        raise PolicyError(f"cross-seeden is niet toegestaan voor tracker {row['tracker_host'] or 'onbekend'}")
    src = _torrent_row(row["source_hash"])
    if src and not crossseed_allowed(src["tracker_host"]):
        raise PolicyError(f"cross-seeden vanaf tracker {src['tracker_host']} is niet toegestaan")


def crossseed_allowed(host: str | None) -> bool:
    """Only when a rule for exactly this tracker host explicitly allows it (default: not allowed)."""
    if not host:
        return False
    row = get_db().fetchone(
        "SELECT allow_cross_seed FROM torrent_tracker_rules WHERE lower(tracker_host)=%s "
        "AND category IS NULL",
        (host.lower(),),
    )
    return bool(row and row["allow_cross_seed"])


# ------------------------------------------------------------------ queue / speed policy view
def speed_policy(prefs: dict[str, Any]) -> dict[str, Any]:
    cap = ts.upload_cap_bps()
    up = prefs.get("up_limit")
    status = "onbekend"
    if cap is not None and up is not None:
        status = "ok" if 0 < int(up) <= cap else "boven_grens"
    return {
        "line_up_mbit": ts.get("line_up_mbit"),
        "line_down_mbit": ts.get("line_down_mbit"),
        "upload_cap_pct": ts.get("upload_cap_pct"),
        "max_upload_bps": cap,
        "current_up_limit": up,
        "current_dl_limit": prefs.get("dl_limit"),
        "status": status,
        "max_connections_cap": ts.get("max_connections_cap"),
        "max_connections_per_torrent_cap": ts.get("max_connections_per_torrent_cap"),
        "current_max_connec": prefs.get("max_connec"),
        "current_max_connec_per_torrent": prefs.get("max_connec_per_torrent"),
        "current_max_uploads": prefs.get("max_uploads"),
        "current_max_uploads_per_torrent": prefs.get("max_uploads_per_torrent"),
        "max_uploads_cap": ALLOWED_PREFS["max_uploads"]["max"],
        "max_uploads_per_torrent_cap": ALLOWED_PREFS["max_uploads_per_torrent"]["max"],
        "out_of_bounds": [
            {k: v for k, v in x.items() if k != "severity"} for x in pref_violations(prefs)
        ],
    }


def queue_policy(prefs: dict[str, Any], torrents: list[dict[str, Any]]) -> dict[str, Any]:
    """Current queue settings + a deterministic suggestion based on observed queue pressure."""
    queued = sum(1 for t in torrents if t.get("state") == "queuedDL")
    active_dl = sum(
        1 for t in torrents if t.get("state") in ("downloading", "forcedDL", "metaDL", "stalledDL")
    )
    stalled = sum(1 for t in torrents if t.get("state") == "stalledDL")
    suggestions = []
    if (
        prefs.get("queueing_enabled")
        and queued
        and stalled
        and stalled >= active_dl > 0
        and not prefs.get("dont_count_slow_torrents")
    ):
        suggestions.append(
            {
                "key": "dont_count_slow_torrents",
                "after": True,
                "why": f"{queued} torrent(s) wachten terwijl alle {active_dl} actieve downloads "
                "vastzitten; trage torrents niet meetellen laat de wachtrij doorlopen.",
            }
        )
    return {
        "queueing_enabled": prefs.get("queueing_enabled"),
        "max_active_downloads": prefs.get("max_active_downloads"),
        "max_active_uploads": prefs.get("max_active_uploads"),
        "max_active_torrents": prefs.get("max_active_torrents"),
        "dont_count_slow_torrents": prefs.get("dont_count_slow_torrents"),
        "queued_downloads": queued,
        "active_downloads": active_dl,
        "stalled_downloads": stalled,
        "suggestions": suggestions,
    }
