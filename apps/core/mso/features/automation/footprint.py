"""Voetafdruk (1.6.0, C35): what MSO changed in your systems right now — and respecting the owner's own choice.

- :func:`collect` (MSO's own tables only): every applied op on a reversible applier (result ``toegepast``, not
  ``irreversible``/``one_way``, not a one-shot action such as a recheck) gives one entry per ``(target, path)``.
  Several MSO changes on one path form a chain (each change started from the value the previous one set): the
  latest change holds *MSO's value*, the first one's ``observed_before`` is the *original*. A path whose net effect
  is nil (MSO switched a server off and later on again) is no footprint.
- :func:`refresh` — the hourly job ``automation.footprint`` (read-only towards your systems, runs in every mode):
  reads the current value of every entry with the target's own applier (and its normalisation: qBittorrent keeps
  speed limits in whole KiB/s, the exclude list as trimmed lines) → ``actief`` / ``gewijzigd`` (outside MSO, now X)
  / ``onbereikbaar`` (last known value kept) / ``verdwenen`` (object removed). A change outside MSO records an
  owner override (:func:`overrides.record`, ``automation.override_days`` days, default 30), audit
  ``footprint.overridden`` and ONE calm info item (an ``advies`` recommendation, never a decision). When MSO's value
  is back (the owner set it again, or applied a proposal), the override is cleared.
- :func:`restore_plan` / :func:`restore_all` — "Alles terugzetten": one change set per system through the normal
  create → apply (drift check, write, read-back) → audit flow. Skipped, with the reason: values changed outside MSO,
  unreachable or removed objects, and values outside MSO's policy limits. Removing something (MSO's own indexer in
  Prowlarr) is ``toestemming_nodig``: applied at once only when an admin confirms, otherwise it waits for approval.
  Restored paths get an override too, so Autopilot does not put them back on its next round.

No secrets: values are the applier's own (non-secret) setting values, shown as text and redacted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from mso import changes, jobs, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.db import Database, get_db, jsonb
from mso.domain.outcomes import recommend
from mso.features.automation import TARGET_AREA, overrides
from mso.security import redact

LOGGER = logging.getLogger(__name__)
ACTOR = "automatisering"
JOB = "automation.footprint"
RESTORE_SOURCE = "footprint:restore"
REC_PREFIX = "footprint.override:"
STATE_KEY = "automation.footprint_last_run"
STATUSES = ("actief", "gewijzigd", "onbereikbaar", "verdwenen")
STATUS_LABEL = {
    "actief": "Nog actief",
    "gewijzigd": "Buiten MSO gewijzigd",
    "onbereikbaar": "Bron onbereikbaar",
    "verdwenen": "Niet meer aanwezig",
}
# targets whose ops are one-way by nature: a retried download or a sent release cannot be "un-done"
ONE_WAY_TARGETS = ("sabnzbd_retry", "download_send")
# one-shot actions and removals without a lasting value to compare (recheck/reannounce, cross-seed add, cleanup)
ONE_SHOT_RE = re.compile(r"(^crossseed:|:(recheck|reannounce)$|^cleanup:|^arrblock:|^blacklist:|:delete_with_files:)")
TARGET_LABEL = {"sabnzbd": "SABnzbd", "qbittorrent": "qBittorrent", "prowlarr": "Prowlarr",
                "mediaguard": "MediaGuard"}
SAB_FIELD = {"enable": "aan/uit", "priority": "prioriteit", "optional": "optioneel"}
NOT_MEASURED = {
    "result": "niet_van_toepassing",
    "label": "Niet van toepassing",
    "action": "geen",
    "message": "Terugzetten via Voetafdruk wordt niet gemeten en nooit automatisch teruggedraaid.",
    "measures": [],
}
IRREVERSIBLE_LABEL = {
    "torrents_deleted": "verdachte torrents verwijderd (met bestanden)",
    "arr_blocked": "releases geblokkeerd in Sonarr/Radarr via MediaGuard",
    "retried": "mislukte downloads opnieuw geprobeerd in SABnzbd",
    "sent": "vervangers verstuurd naar een downloadclient",
    "crossseed_added": "cross-seeds toegevoegd in qBittorrent (MSO verwijdert nooit torrents)",
    "other": "overige eenmalige acties",
}

sr.define(
    "automation.override_days",
    "Eigen keuze respecteren",
    "int",
    30,
    min=1,
    max=365,
    unit="dagen",
    category="automatisering",
    help="Zet je een waarde die MSO had ingesteld zelf terug (buiten MSO, of met 'Alles terugzetten'), dan past "
    "MSO die zo lang niet opnieuw automatisch toe. Het mag hooguit één voorstel klaarzetten dat op jou wacht.",
)

Matcher = Callable[[str, Any, Any, Any], "bool | None"]  # (path, original, mso_value, current) → still MSO's?
Restorer = Callable[[str, Any, Any, Any], Any]  # (path, original, mso_value, current) → value to write back
MATCHERS: dict[str, Matcher] = {}
RESTORERS: dict[str, Restorer] = {}


def register_normaliser(target: str, *, matches: Matcher | None = None, restore: Restorer | None = None) -> None:
    """Target-specific comparison (``matches`` returns None to fall back to plain equality) and restore value."""
    if matches is not None:
        MATCHERS[target] = matches
    if restore is not None:
        RESTORERS[target] = restore


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, dict | list) or isinstance(b, dict | list):
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    return bool(a == b)


def matches(target: str, path: str, original: Any, mso_value: Any, current: Any) -> bool:
    fn = MATCHERS.get(target)
    if fn is not None:
        verdict = fn(path, original, mso_value, current)
        if verdict is not None:
            return bool(verdict)
    return _same(mso_value, current)


def restore_value(target: str, path: str, original: Any, mso_value: Any, current: Any) -> Any:
    fn = RESTORERS.get(target)
    return fn(path, original, mso_value, current) if fn is not None else original


# ------------------------------------------------------------------ qBittorrent normalisation
def _qb_added(original: Any, mso_value: Any) -> list[str]:
    from mso.features.torrent import policy

    before = {p.lower() for p in policy.split_patterns(original)}
    return [p for p in policy.split_patterns(mso_value) if p.lower() not in before]


def _qb_matches(path: str, original: Any, mso_value: Any, current: Any) -> bool | None:
    from mso.features.torrent import policy

    if policy.is_rate_path(path):
        return policy.rate_matches(mso_value, current)
    if path == "pref:excluded_file_names":
        if current is None:
            return False
        have = {p.lower() for p in policy.split_patterns(current)}
        return all(p.lower() in have for p in _qb_added(original, mso_value))  # the owner's own extra lines are fine
    return None


def _qb_restore(path: str, original: Any, mso_value: Any, current: Any) -> Any:
    from mso.features.torrent import policy

    if path == "pref:excluded_file_names":  # take out only what MSO added; keep what the owner added since
        added = {p.lower() for p in _qb_added(original, mso_value)}
        return policy.join_patterns([p for p in policy.split_patterns(current) if p.lower() not in added])
    return original


register_normaliser("qbittorrent", matches=_qb_matches, restore=_qb_restore)


# ------------------------------------------------------------------ labels and value texts
def _nl(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def value_text(target: str, path: str, value: Any) -> str:
    """Plain Dutch text for a setting value (as the source shows it)."""
    if value is None:
        return "niet aanwezig" if path.startswith("indexer:") else "—"
    if target == "sabnzbd" and path.endswith(("/enable", "/optional")) and value in (0, 1):
        on, off = ("aan", "uit") if path.endswith("/enable") else ("ja", "nee")
        return on if value else off
    if isinstance(value, bool):
        return "aan" if value else "uit"
    if target == "qbittorrent":
        from mso.features.torrent import policy

        if policy.is_rate_path(path) and isinstance(value, int | float):
            return "geen limiet" if value <= 0 else f"{_nl(value / 1_048_576)} MiB/s"
        if path == "pref:excluded_file_names":
            pats = policy.split_patterns(value)
            if not pats:
                return "leeg"
            return f"{len(pats)} patronen: " + ", ".join(pats[:5]) + (" …" if len(pats) > 5 else "")
    if isinstance(value, dict):
        if {"ratio_limit", "seeding_time_limit"} <= set(value):
            return f"ratio {value.get('ratio_limit')} · seedtijd {value.get('seeding_time_limit')} min"
        return redact(json.dumps(value, ensure_ascii=False, default=str))[:160]
    if isinstance(value, str):
        return redact(" · ".join(x.strip() for x in value.splitlines() if x.strip()))[:160]
    return str(value)


def setting_label(target: str, path: str, op_label: str, torrent_names: dict[str, str] | None = None) -> str:
    """Name of the setting (not of the action that changed it)."""
    if target == "sabnzbd" and path.startswith("servers/") and path.count("/") >= 2:
        server, field_ = path[len("servers/"):].rsplit("/", 1)
        shown = op_label.split(":", 1)[0].strip() if ":" in op_label else server
        return f"{shown or server} · {SAB_FIELD.get(field_, field_)}"
    if target == "qbittorrent":
        from mso.features.torrent import policy

        kind, _, rest = path.partition(":")
        if kind == "pref":
            meta = policy.ALLOWED_PREFS.get(rest) or {}
            if rest == "excluded_file_names":
                return "Uitgesloten bestandsnamen"
            if rest == "excluded_file_names_enabled":
                return "Bestandsnamen uitsluiten"
            return str(meta.get("label") or rest)
        if path == "transfer:alt_speed":
            return "Alternatieve snelheidslimieten"
        if kind == "torrent":
            h, _, field_ = rest.partition(":")
            what = {"up_limit": "Uploadlimiet", "dl_limit": "Downloadlimiet", "share_limits": "Deellimieten"}.get(
                field_, field_)
            name = (torrent_names or {}).get(h.lower())
            return f"{what} · {name[:80] if name else h[:10]}"
    if target == "prowlarr" and path.startswith("indexer:"):
        return f"MSO-indexer in Prowlarr ({path.split(':', 1)[1]})"
    return op_label or path


# ------------------------------------------------------------------ collect (MSO's own tables)
def footprint_op(target: str, op: dict[str, Any]) -> bool:
    return (target not in ONE_WAY_TARGETS and op.get("result") == "toegepast" and not op.get("irreversible")
            and not op.get("one_way") and not ONE_SHOT_RE.search(str(op.get("path") or "")))


def collect(db: Database | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """Every path MSO changed and did not roll back, with original and MSO value (no external calls)."""
    db = db or get_db()
    rows = db.fetchall(
        "SELECT id, target, title, ops, applied_at, applied_by FROM change_sets WHERE status='applied' "
        "AND applied_at IS NOT NULL AND NOT (target = ANY(%s)) AND COALESCE(source, '') <> %s "
        "ORDER BY applied_at, id",
        (list(ONE_WAY_TARGETS), RESTORE_SOURCE),
    )
    per: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ch in rows:
        if ch["target"] not in changes.APPLIERS:
            continue  # no applier (feature not loaded): cannot be read nor restored
        for op in ch["ops"] or []:
            if not footprint_op(ch["target"], op):
                continue
            per.setdefault((ch["target"], str(op["path"])), []).append({
                "change_id": ch["id"], "title": ch["title"], "label": str(op.get("label") or op["path"]),
                "before": op.get("observed_before", op.get("before")), "after": op.get("after"),
                "applied_at": ch["applied_at"], "applied_by": ch["applied_by"],
            })
    hashes = sorted({p.split(":")[1].lower() for t, p in per if t == "qbittorrent" and p.startswith("torrent:")})
    names = {r["hash"]: r["name"] for r in db.fetchall(
        "SELECT hash, name FROM torrent_state WHERE hash = ANY(%s)", (hashes,))} if hashes else {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for (target, path), ops in per.items():
        chain = [ops[-1]]
        for prev in reversed(ops[:-1]):
            if not _same(prev["after"], chain[0]["before"]):
                break  # the owner (or something else) set another value in between: the chain starts after it
            chain.insert(0, prev)
        latest = chain[-1]
        if _same(chain[0]["before"], latest["after"]):
            continue  # net effect nil
        out[(target, path)] = {
            "target": target, "path": path, "label": setting_label(target, path, latest["label"], names),
            "change_id": latest["change_id"], "change_title": latest["title"],
            "change_ids": [c["change_id"] for c in chain], "all_change_ids": sorted({o["change_id"] for o in ops}),
            "applied_at": latest["applied_at"], "applied_by": latest["applied_by"],
            "original_value": chain[0]["before"], "mso_value": latest["after"],
        }
    return out


# ------------------------------------------------------------------ live reads
def _gone(exc: Exception) -> bool:
    """The object no longer exists (torrent removed, SABnzbd server deleted) — not a connection problem."""
    if isinstance(exc, json.JSONDecodeError | UnicodeError):
        return False
    return isinstance(exc, LookupError | ValueError) or getattr(exc, "code", None) in ("invalid", "not_found")


def read_current(entries: dict[tuple[str, str], dict[str, Any]]) -> dict[tuple[str, str], tuple[str, Any, str | None]]:
    """(status, current value, detail) per entry, one applier per system. After the first connection failure of a
    system its other entries are not tried again (one hourly time-out, not one per setting)."""
    out: dict[tuple[str, str], tuple[str, Any, str | None]] = {}
    by_target: dict[str, list[dict[str, Any]]] = {}
    for e in entries.values():
        by_target.setdefault(e["target"], []).append(e)
    for target, items in by_target.items():
        try:
            applier = changes.APPLIERS[target]()
        except Exception as exc:
            msg = redact(str(exc))[:200]
            for e in items:
                out[(target, e["path"])] = ("onbereikbaar", None, msg)
            continue
        down: str | None = None
        for e in items:
            key = (target, e["path"])
            if down is not None:
                out[key] = ("onbereikbaar", None, down)
                continue
            try:
                current = applier.read(e["path"])
            except Exception as exc:
                msg = redact(str(exc))[:200]
                if _gone(exc):
                    out[key] = ("verdwenen", None, msg)
                else:
                    down = msg
                    out[key] = ("onbereikbaar", None, msg)
                continue
            ok = matches(target, e["path"], e["original_value"], e["mso_value"], current)
            out[key] = ("actief" if ok else "gewijzigd", current, None)
    return out


# ------------------------------------------------------------------ the hourly job
def override_days() -> int:
    return int(sr.get("automation.override_days"))


def _rec_key(target: str, path: str) -> str:
    return f"{REC_PREFIX}{target}:{path}"


def _resolve_rec(target: str, path: str, db: Database) -> None:
    db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s AND status='open'",
               (_rec_key(target, path),))


def _record_override(e: dict[str, Any], current: Any, days: int, db: Database) -> None:
    target, path = e["target"], e["path"]
    tl = TARGET_LABEL.get(target, target)
    overrides.record(target, path, mso_value=e["mso_value"], current_value=current, change_id=e["change_id"],
                     days=days, db=db)
    row = overrides.active(target, path, db)
    until = row["until"] if row else None
    until_txt = until.astimezone(UTC).strftime("%d-%m-%Y") if isinstance(until, datetime) else f"{days} dagen"
    mso_txt, cur_txt = value_text(target, path, e["mso_value"]), value_text(target, path, current)
    audit(ACTOR, "footprint.overridden",
          f"Buiten MSO gewijzigd: {e['label']} in {tl} — nu {cur_txt} (MSO had {mso_txt}); MSO past dit "
          f"{days} dagen niet opnieuw automatisch toe",
          target=target, change_id=e["change_id"],
          detail={"path": path, "mso_value": e["mso_value"], "current_value": current, "days": days,
                  "until": until.isoformat() if isinstance(until, datetime) else None})
    rec_id = recommend(
        TARGET_AREA.get(target, "automation"),
        f"Je hebt '{e['label']}' zelf aangepast in {tl}",
        f"MSO had dit op {mso_txt} gezet (wijziging #{e['change_id']}); nu staat het op {cur_txt}. MSO respecteert "
        f"jouw keuze en past dit tot {until_txt} niet opnieuw automatisch toe. Wil je MSO's waarde toch terug, pas "
        "het voorstel dan zelf toe; er hoeft niets te gebeuren.",
        risk="advies",
        subject=target,
        impact="Ter info: geen actie nodig.",
        dedupe_key=_rec_key(target, path),
        evidence={"target": target, "path": path, "change_id": e["change_id"], "mso_value": e["mso_value"],
                  "current_value": current, "until": until.isoformat() if isinstance(until, datetime) else None},
        db=db,
    )
    db.execute("UPDATE recommendations SET status='open', updated_at=now() WHERE id=%s AND status <> 'open'",
               (rec_id,))


def refresh(db: Database | None = None) -> dict[str, Any]:
    """Re-read every footprint entry at its source and store the result (see module docstring)."""
    db = db or get_db()
    days = override_days()
    entries = collect(db)
    prev = {(r["target"], r["path"]): dict(r) for r in db.fetchall("SELECT * FROM change_footprint")}
    reads = read_current(entries)
    counts = {s: 0 for s in STATUSES}
    overridden: list[str] = []
    for key, e in entries.items():
        status, current, detail = reads[key]
        old = prev.get(key)
        if status in ("onbereikbaar", "verdwenen") and old is not None and current is None:
            current = old["current_value"]  # keep the last known value
        counts[status] += 1
        db.execute(
            "INSERT INTO change_footprint(target,path,label,change_id,change_ids,applied_at,applied_by,original_value,"
            "mso_value,current_value,status,detail,checked_at,status_since) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now()) ON CONFLICT (target,path) DO UPDATE SET "
            "label=EXCLUDED.label, change_id=EXCLUDED.change_id, change_ids=EXCLUDED.change_ids, "
            "applied_at=EXCLUDED.applied_at, applied_by=EXCLUDED.applied_by, original_value=EXCLUDED.original_value, "
            "mso_value=EXCLUDED.mso_value, current_value=EXCLUDED.current_value, detail=EXCLUDED.detail, "
            "checked_at=now(), status_since=CASE WHEN change_footprint.status = EXCLUDED.status "
            "THEN change_footprint.status_since ELSE now() END, status=EXCLUDED.status",
            (e["target"], e["path"], e["label"], e["change_id"], jsonb(e["change_ids"]), e["applied_at"],
             e["applied_by"], jsonb(e["original_value"]), jsonb(e["mso_value"]), jsonb(current), status, detail),
        )
        if status == "gewijzigd":
            new_event = (old is None or old["status"] != "gewijzigd" or old["change_id"] != e["change_id"]
                         or not _same(old["current_value"], current))
            if new_event:
                _record_override(e, current, days, db)
                overridden.append(e["label"])
        elif status == "actief":
            if overrides.active(e["target"], e["path"], db):
                overrides.clear(e["target"], e["path"], db)
                audit(ACTOR, "footprint.override_cleared",
                      f"MSO-waarde weer actief: {e['label']} in {TARGET_LABEL.get(e['target'], e['target'])}; "
                      "eigen keuze opgeheven", target=e["target"], change_id=e["change_id"],
                      detail={"path": e["path"]})
            _resolve_rec(e["target"], e["path"], db)
    for target, path in set(prev) - set(entries):
        db.execute("DELETE FROM change_footprint WHERE target=%s AND path=%s", (target, path))
        _resolve_rec(target, path, db)
    summary = {"at": datetime.now(UTC).isoformat(), "checked": len(entries), **counts, "overridden": overridden}
    state.set_state(STATE_KEY, summary, db)
    return summary


@jobs.handler(JOB)
def _job(ctx: jobs.JobContext) -> dict[str, Any]:
    return refresh(ctx.db)


jobs.every(JOB, 3600)  # read-only towards your systems: runs in every mode, also when paused


# ------------------------------------------------------------------ view
def _entry_view(r: dict[str, Any], active_overrides: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    t, p = r["target"], r["path"]
    ov = active_overrides.get((t, p))
    return {
        "target": t, "path": p, "label": r["label"], "change_id": r["change_id"], "change_ids": r["change_ids"],
        "applied_at": r["applied_at"], "applied_by": r["applied_by"],
        "automatic": r["applied_by"] in (ACTOR, "autopilot", "systeem", "system"),
        "original_text": value_text(t, p, r["original_value"]), "mso_text": value_text(t, p, r["mso_value"]),
        "current_text": value_text(t, p, r["current_value"]),
        "status": r["status"], "status_label": STATUS_LABEL[r["status"]], "detail": r["detail"],
        "checked_at": r["checked_at"], "status_since": r["status_since"],
        "override": {"until": ov["until"], "detected_at": ov["detected_at"]} if ov else None,
    }


def irreversible_summary(db: Database | None = None) -> list[dict[str, Any]]:
    """What MSO did that cannot be put back (counts), for the footprint page and 'Alles terugzetten'."""
    from mso.features.torrent.cleanup import ARRBLOCK_RE, DELETE_RE

    db = db or get_db()
    rows = db.fetchall(
        "SELECT target, ops FROM change_sets WHERE status IN ('applied','rolled_back','failed') AND ("
        "target = ANY(%s) OR ops @> '[{\"irreversible\": true}]'::jsonb OR ops @> '[{\"one_way\": true}]'::jsonb "
        "OR ops::text LIKE '%%\"crossseed:%%')",
        (list(ONE_WAY_TARGETS),),
    )
    counts: dict[str, int] = {}
    for r in rows:
        for op in r["ops"] or []:
            if op.get("result") not in ("toegepast", "niet_terug_te_draaien"):
                continue
            path = str(op.get("path") or "")
            if DELETE_RE.match(path):
                key = "torrents_deleted"
            elif ARRBLOCK_RE.match(path):
                key = "arr_blocked"
            elif r["target"] == "sabnzbd_retry":
                key = "retried"
            elif r["target"] == "download_send":
                key = "sent"
            elif path.startswith("crossseed:"):
                key = "crossseed_added"
            elif re.search(r":(recheck|reannounce)$|^blacklist:|^cleanup:", path):
                continue  # no lasting change in your systems (a check, or MSO's own blacklist)
            elif op.get("irreversible") or op.get("one_way"):
                key = "other"
            else:
                continue
            counts[key] = counts.get(key, 0) + 1
    return [{"key": k, "label": IRREVERSIBLE_LABEL[k], "count": counts[k]} for k in IRREVERSIBLE_LABEL if counts.get(k)]


def view(db: Database | None = None) -> dict[str, Any]:
    db = db or get_db()
    rows = db.fetchall("SELECT * FROM change_footprint ORDER BY target, label, path")
    active = {(o["target"], o["path"]): dict(o) for o in overrides.list_active(db)}
    systems: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = systems.setdefault(r["target"], {"target": r["target"], "label": TARGET_LABEL.get(r["target"], r["target"]),
                                             "entries": [], "counts": {k: 0 for k in STATUSES}})
        s["entries"].append(_entry_view(dict(r), active))
        s["counts"][r["status"]] += 1
    totals = {k: sum(s["counts"][k] for s in systems.values()) for k in STATUSES}
    labels = {(r["target"], r["path"]): r["label"] for r in rows}
    own = [
        {"target": o["target"], "target_label": TARGET_LABEL.get(o["target"], o["target"]), "path": o["path"],
         "label": labels.get((o["target"], o["path"])) or setting_label(o["target"], o["path"], o["path"]),
         "mso_text": value_text(o["target"], o["path"], o["mso_value"]),
         "current_text": value_text(o["target"], o["path"], o["current_value"]),
         "change_id": o["change_id"], "detected_at": o["detected_at"], "until": o["until"]}
        for o in active.values()
    ]
    return {
        "last_run": state.get_state(STATE_KEY, None, db),
        "systems": list(systems.values()),
        "totals": totals,
        "overrides": own,
        "override_days": override_days(),
        "irreversible": irreversible_summary(db),
        "mode": state.mode(),
        "restore_allowed": state.mode() in ("manage", "autopilot"),
    }


# ------------------------------------------------------------------ Alles terugzetten
def _is_off(path: str, value: Any) -> bool:
    return value is False or (value == 0 and not isinstance(value, bool) and path.endswith("/enable"))


def _validator(target: str) -> Callable[[str, Any], None] | None:
    try:
        return changes.APPLIERS[target]().validate
    except Exception:
        return None


def restore_plan(db: Database | None = None) -> dict[str, Any]:
    """Live: what 'Alles terugzetten' would write (``restore``) and what it leaves alone (``skipped``, with reason).
    ``fingerprint`` identifies exactly this list; :func:`restore_all` refuses when the list changed since."""
    db = db or get_db()
    entries = collect(db)
    reads = read_current(entries)
    validators: dict[str, Callable[[str, Any], None] | None] = {}
    restore: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key in sorted(entries):
        e = entries[key]
        target, path = key
        tl = TARGET_LABEL.get(target, target)
        status, current, detail = reads[key]
        base = {"target": target, "target_label": tl, "path": path, "label": e["label"], "change_id": e["change_id"],
                "change_ids": e["change_ids"], "original_text": value_text(target, path, e["original_value"]),
                "mso_text": value_text(target, path, e["mso_value"]),
                "current_text": value_text(target, path, current) if status in ("actief", "gewijzigd") else None}
        if status == "onbereikbaar":
            skipped.append(base | {"reason_code": "onbereikbaar",
                                   "reason": f"{tl} is niet bereikbaar; er wordt niets veranderd ({detail})"})
            continue
        if status == "verdwenen":
            skipped.append(base | {"reason_code": "verdwenen",
                                   "reason": f"Bestaat niet meer in {tl}; er valt niets terug te zetten"})
            continue
        if status == "gewijzigd":
            skipped.append(base | {"reason_code": "gewijzigd",
                                   "reason": f"Buiten MSO gewijzigd (nu {base['current_text']}); MSO laat jouw "
                                             "waarde staan"})
            continue
        value = restore_value(target, path, e["original_value"], e["mso_value"], current)
        if _same(value, current):
            skipped.append(base | {"reason_code": "al_terug", "reason": "Staat al op de oorspronkelijke waarde"})
            continue
        if target not in validators:
            validators[target] = _validator(target)
        validate = validators[target]
        if validate is not None and value is not None:
            try:
                validate(path, value)
            except ValueError as exc:
                skipped.append(base | {"reason_code": "beleid",
                                       "reason": f"De oorspronkelijke waarde valt buiten MSO's veiligheidsgrenzen "
                                                 f"({redact(str(exc))[:160]}); zet dit zo nodig zelf terug in {tl}"})
                continue
        removes = value is None
        restore.append(base | {
            "before": current, "after": value, "after_text": value_text(target, path, value),
            "removes": removes, "disables": _is_off(path, value) and not _is_off(path, current),
            "needs_approval": removes,
        })
    raw = json.dumps([(r["target"], r["path"], r["before"], r["after"]) for r in restore], sort_keys=True,
                     default=str)
    return {
        "restore": restore,
        "skipped": skipped,
        "irreversible": irreversible_summary(db),
        "fingerprint": hashlib.sha256(raw.encode()).hexdigest()[:16],
        "override_days": override_days(),
        "mode": state.mode(),
        "allowed": state.mode() in ("manage", "autopilot"),
    }


def _discard_if_open(change_id: int, actor: str) -> None:
    try:
        if changes.get(change_id)["status"] in ("preview", "approved"):
            changes.discard(change_id, actor)  # never left behind for the automatic round
    except HTTPException:
        pass


def _mark_restored(target: str, paths: set[str], restore_id: int, actor: str, db: Database) -> list[int]:
    """MSO's earlier changes on the restored paths: ops → 'teruggedraaid'; a change with nothing reversible left
    becomes 'rolled_back' (exactly like a rollback). Returns the fully rolled back change ids."""
    done: list[int] = []
    for row in db.fetchall(
        "SELECT id FROM change_sets WHERE status='applied' AND target=%s AND COALESCE(source, '') <> %s ORDER BY id",
        (target, RESTORE_SOURCE),
    ):
        title = None
        with db.tx() as conn:
            ch = conn.execute("SELECT ops, status, title FROM change_sets WHERE id=%s FOR UPDATE",
                              (row["id"],)).fetchone()
            if ch is None or ch["status"] != "applied":
                continue
            ops = [dict(o) for o in ch["ops"] or []]
            hit = False
            for o in ops:
                if str(o.get("path")) in paths and o.get("result") == "toegepast" and footprint_op(target, o):
                    o["result"] = "teruggedraaid"
                    o["restored_by"] = restore_id
                    hit = True
            if not hit:
                continue
            if any(o.get("result") == "toegepast" and footprint_op(target, o) for o in ops):
                conn.execute("UPDATE change_sets SET ops=%s WHERE id=%s", (jsonb(ops), row["id"]))
                continue
            for o in ops:
                if o.get("result") == "toegepast":
                    o["result"] = "niet_terug_te_draaien"
            conn.execute("UPDATE change_sets SET status='rolled_back', rolled_back_at=now(), ops=%s "
                         "WHERE id=%s AND status='applied'", (jsonb(ops), row["id"]))
            title = ch["title"]
        if title is not None:
            done.append(row["id"])
            audit(actor, "change.rolled_back", f"Teruggedraaid: {title} (via Voetafdruk → Alles terugzetten)",
                  target=target, change_id=row["id"], detail={"restore_change_id": restore_id})
    return done


def restore_all(actor: str, *, fingerprint: str | None = None, is_admin: bool = False,
                db: Database | None = None) -> dict[str, Any]:
    """'Alles terugzetten' (operator+, never in Alleen-lezen). See module docstring."""
    db = db or get_db()
    state.require_manage_mode()
    plan = restore_plan(db)
    if fingerprint is not None and fingerprint != plan["fingerprint"]:
        raise HTTPException(409, detail="De lijst is veranderd sinds je hem bekeek (een waarde is intussen gewijzigd "
                                        "of een bron is (on)bereikbaar geworden). Bekijk de lijst opnieuw.")
    if not plan["restore"]:
        raise HTTPException(409, detail="Er is niets (meer) terug te zetten")
    days = override_days()
    entries = collect(db)
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for item in plan["restore"]:
        groups.setdefault((item["target"], item["needs_approval"]), []).append(item)
    results: list[dict[str, Any]] = []
    for (target, approval), items in groups.items():
        tl = TARGET_LABEL.get(target, target)
        n = len(items)
        key = f"footprint.restore:{target}:{'goedkeuring' if approval else 'direct'}:{plan['fingerprint']}"
        for old in db.fetchall(
            "SELECT id FROM change_sets WHERE source=%s AND target=%s AND status IN ('preview','approved') "
            "AND dedupe_key LIKE %s AND dedupe_key <> %s",
            (RESTORE_SOURCE, target, f"footprint.restore:{target}:{'goedkeuring' if approval else 'direct'}:%", key),
        ):
            _discard_if_open(old["id"], actor)  # an older 'terugzetten' proposal is superseded by this list
        ops = [{"path": i["path"], "label": f"{i['label']}: terug naar {i['after_text']}", "before": i["before"],
                "after": i["after"], **({"destructive": True} if i["removes"] or i["disables"] else {})}
               for i in items]
        source_ids = sorted({cid for i in items for cid in entries.get((target, i["path"]), {}).get(
            "all_change_ids", i["change_ids"])})
        title = (f"MSO-registratie verwijderen uit {tl} (Alles terugzetten)" if approval
                 else f"Voetafdruk terugzetten in {tl}: {n} {'instelling' if n == 1 else 'instellingen'}")
        explanation = (
            "Alles terugzetten (Automatisering → Voetafdruk): MSO zet de oorspronkelijke waarden terug die het zelf "
            "had veranderd. Elke waarde wordt vlak voor het schrijven gecontroleerd (staat er nog wat MSO instelde? "
            "Anders wordt niets geschreven) en daarna teruggelezen bij de bron. "
            + ("Dit verwijdert MSO's eigen registratie; daarvoor is altijd goedkeuring door een beheerder nodig. "
               if approval else "")
            + f"MSO past deze instellingen daarna {days} dagen niet opnieuw automatisch toe."
        )
        impact = {
            "Soort": "terugzetten (Voetafdruk)",
            "Instellingen": str(n),
            "Wijzigingen van MSO": ", ".join(f"#{c}" for c in source_ids),
            "Daarna": f"{days} dagen geen automatische herhaling",
            "area": TARGET_AREA.get(target),
            "evaluation": dict(NOT_MEASURED, at=datetime.now(UTC).isoformat()),
        }
        try:
            ch = changes.create(target, title, ops, risk="toestemming_nodig" if approval else "automatisch_veilig",
                                explanation=explanation, impact=impact, source=RESTORE_SOURCE, actor=actor,
                                dedupe_key=key, db=db)
        except (HTTPException, ValueError) as exc:
            results.append({"target": target, "target_label": tl, "status": "mislukt", "change_id": None, "count": n,
                            "message": str(getattr(exc, "detail", exc))})
            continue
        if approval and not is_admin:
            results.append({"target": target, "target_label": tl, "status": "wacht_op_goedkeuring",
                            "change_id": ch["id"], "count": n,
                            "message": "Verwijderen vraagt goedkeuring door een beheerder; het voorstel staat klaar "
                                       "bij Wacht op jou"})
            continue
        try:
            if approval and ch["status"] == "preview":
                changes.approve(ch["id"], actor, db)
            done = changes.apply(ch["id"], actor, db=db)
        except HTTPException as exc:
            _discard_if_open(ch["id"], actor)
            results.append({"target": target, "target_label": tl, "status": "mislukt", "change_id": ch["id"],
                            "count": n, "message": str(exc.detail)})
            continue
        restored = {str(o["path"]) for o in done["ops"] if o.get("result") in ("toegepast", "al_ingesteld")}
        rolled_back = _mark_restored(target, restored, done["id"], actor, db)
        for path in restored:
            e = entries.get((target, path))
            after = next((o["after"] for o in done["ops"] if o["path"] == path), None)
            overrides.record(target, path, mso_value=e["mso_value"] if e else None, current_value=after,
                             change_id=e["change_id"] if e else None, days=days, db=db)
            db.execute("DELETE FROM change_footprint WHERE target=%s AND path=%s", (target, path))
            _resolve_rec(target, path, db)
        audit(actor, "footprint.restored", f"Voetafdruk teruggezet in {tl}: {len(restored)} van {n}",
              target=target, change_id=done["id"],
              detail={"paths": sorted(restored), "changes": source_ids, "rolled_back": rolled_back,
                      "override_days": days})
        results.append({"target": target, "target_label": tl, "status": "teruggezet", "change_id": done["id"],
                        "count": len(restored), "message": None})
    try:
        refresh(db)  # the page shows what is left right away (not only after the next hourly run)
    except Exception:  # the restore itself is done and audited; the hourly job catches up
        LOGGER.warning("voetafdruk verversen na terugzetten mislukt", exc_info=True)
    return {"results": results, "skipped": plan["skipped"], "irreversible": plan["irreversible"],
            "override_days": days}


__all__ = ["JOB", "REC_PREFIX", "RESTORE_SOURCE", "collect", "irreversible_summary", "matches", "read_current",
           "refresh", "register_normaliser", "restore_all", "restore_plan", "value_text", "view"]
