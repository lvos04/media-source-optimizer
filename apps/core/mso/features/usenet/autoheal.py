"""Autopilot for SABnzbd servers: failing servers off, and back on (1.5.0).

In Autopilot (``state.autopilot()``):

1. A server SABnzbd reports with a definitive account problem (``serverstatus.DISABLE_SUGGESTED``: expired account,
   login refused, access denied, block used up) for longer than ``usenet.auto_disable_after_h`` (immediately for
   "account verlopen") is switched off: one change set per server on the ``sabnzbd`` applier, op ``destructive``
   (reversible), verified by reading SABnzbd's config back, audited. The LAST enabled server is never switched off.
2. Back on:
   - when MSO's own NNTP test of the linked provider (with credentials) succeeds after the switch-off → re-enabled;
   - otherwise as a *probe* after ``usenet.auto_probe_interval_h`` (24 h): enable again and watch. If SABnzbd
     reports the error again, the server goes off again and the wait doubles (``auto_probe_max_interval_h`` cap).
     After ``usenet.auto_probe_max`` failed probes MSO stops probing by itself ("given_up").
   - "Opnieuw proberen" (user, any mode with beheermodus) re-enables + watches the same way.
   - a server the user switched on in SABnzbd themselves is watched as a probe as well.
3. A probe without new errors for ``usenet.probe_watch_h`` counts as recovered.

Every server state change re-evaluates failed downloads that were skipped earlier (:mod:`recovery.fallback`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.connectors.sabnzbd import SabClient
from mso.db import get_db
from mso.features.usenet import autoapply, providers, serverstatus

LOGGER = logging.getLogger(__name__)
ACTOR = "autopilot:usenet"
AREA = "usenet"
RECOVERED_MEMORY = timedelta(days=7)  # a server failing again this soon after a probe keeps its backoff


def _now() -> datetime:
    return datetime.now(UTC)


def _h(key: str, default: float) -> float:
    try:
        return float(sr.get(key))
    except Exception:
        return default


def backoff(probes: int) -> timedelta:
    """Wait before the next probe after ``probes`` failed probes: interval · 2^probes, capped."""
    base = _h("usenet.auto_probe_interval_h", 24)
    cap = _h("usenet.auto_probe_max_interval_h", 168)
    return timedelta(hours=min(cap, base * 2 ** max(0, probes)))


# ------------------------------------------------------------------ persistence
def rows() -> dict[str, dict[str, Any]]:
    return {r["server"]: dict(r) for r in get_db().fetchall("SELECT * FROM usenet_server_autoheal")}


def _save(server: str, **fields: Any) -> None:
    cols = ["server", *fields]
    vals = [server, *fields.values()]
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in fields) + ", updated_at=now()"
    get_db().execute(
        f"INSERT INTO usenet_server_autoheal({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT (server) DO UPDATE SET {updates}",
        vals,
    )


def _reevaluate_failed_downloads() -> None:
    try:
        from mso.features.recovery import fallback
    except ImportError:  # recovery module disabled
        return
    try:
        fallback.reevaluate()
    except Exception:  # pragma: no cover - never let this break the server state machine
        LOGGER.info("herbeoordeling mislukte downloads overgeslagen", exc_info=True)


# ------------------------------------------------------------------ helpers
def failing_since(server: str, sab_state: dict[str, Any]) -> datetime:
    """Start of the current failing period: MSO's first observation or SABnzbd's first message of this status."""
    since = sab_state.get("since") or _now()
    first = get_db().scalar(
        "SELECT min(first_at) FROM usenet_sab_server_events WHERE server=%s AND status=%s "
        "AND last_at > now() - interval '7 days'",
        (server, sab_state.get("status")),
    )
    return min(since, first) if first else since


def error_after(server: str, ts: datetime) -> dict[str, Any] | None:
    """A definitive account error SABnzbd reported for ``server`` after ``ts`` (probe failed)."""
    return get_db().fetchone(
        "SELECT status, message, last_at FROM usenet_sab_server_events WHERE server=%s AND status = ANY(%s) "
        "AND last_at > %s ORDER BY last_at DESC LIMIT 1",
        (server, sorted(serverstatus.DISABLE_SUGGESTED), ts),
    )


def _labels() -> dict[str, str]:
    return {
        r["sab_server"]: r["label"]
        for r in get_db().fetchall("SELECT sab_server, label FROM usenet_providers WHERE sab_server IS NOT NULL")
    }


def _name(server: str, sab: dict[str, dict[str, Any]], labels: dict[str, str]) -> str:
    st = sab.get(server) or {"server": server, "displayname": server}
    return serverstatus.display_name(
        {"server": server, "displayname": st.get("displayname") or server, "short": st.get("short")}, labels
    )


def _own_test_ok(server: str, after: datetime | None) -> bool:
    """MSO's own NNTP test (with credentials) of the provider linked to ``server`` succeeded after ``after``."""
    if after is None:
        return False
    for p in providers.list_rows():
        if p.get("sab_server") != server or not providers.has_credentials(p["slug"]):
            continue
        at = providers._ts(p.get("last_check_at"))
        if p.get("status") == "ok" and at is not None and at > after:
            return True
    return False


def _discard_covered_proposals(enabled_now: set[str]) -> None:
    """Open 'kapotte servers uitschakelen' proposals whose servers are all off now are no longer needed."""
    for ch in get_db().fetchall(
        "SELECT * FROM change_sets WHERE target='sabnzbd' AND status IN ('preview','approved') "
        "AND dedupe_key LIKE 'usenet.disable\\_failing:%%'"
    ):
        servers = [o["path"].split("/", 1)[1].rsplit("/", 1)[0] for o in ch["ops"] if o.get("after") == 0]
        if servers and not any(s in enabled_now for s in servers):
            try:
                changes.discard(ch["id"], ACTOR)
            except HTTPException:
                pass


# ------------------------------------------------------------------ actions
def _switch_off(server: str, name: str, st: dict[str, Any], *, probes: int, why: str) -> dict[str, Any]:
    label = serverstatus.LABELS.get(st.get("status") or "", "fout")
    wait = backoff(probes)
    msg = f' SABnzbd meldt: "{st["message"]}".' if st.get("message") else ""
    explanation = (
        f"{name}: {label.lower()}.{msg} {why} Zolang de server aanstaat probeert SABnzbd hem bij elke download, wat "
        "fouten geeft en ontbrekende artikelen niet aanvult. MSO zet 'enable' van 1 op 0 en leest de "
        "SABnzbd-configuratie terug ter controle. Terugdraaien zet de server weer aan. Er wordt niets verwijderd "
        f"en geen abonnement opgezegd. MSO probeert de server over {int(wait.total_seconds() // 3600)} uur zelf "
        "opnieuw (of eerder zodra MSO's eigen test met inloggegevens slaagt)."
    )
    return autoapply.propose_and_apply(
        "sabnzbd",
        f"Server {name} automatisch uitgeschakeld ({label.lower()})",
        [
            {
                "path": f"servers/{server}/enable",
                "label": f"{name}: server uitschakelen",
                "before": 1,
                "after": 0,
                "destructive": True,
            }
        ],
        explanation=explanation,
        impact={
            "Server": name,
            "Reden": label,
            "SABnzbd meldt": st.get("message") or "—",
            "Opnieuw proberen": f"automatisch over {int(wait.total_seconds() // 3600)} uur",
            "Terugdraaien": "enable weer op 1",
            "servers": [server],
        },
        dedupe_key=f"usenet.auto_off:{server}",
        area=AREA,
    )


def _switch_on(server: str, name: str, *, why: str, actor: str | None = None) -> dict[str, Any]:
    """Re-enable one server. ``actor`` set = user action (applied as that user, needs beheermodus);
    otherwise through Autopilot."""
    ops = [{"path": f"servers/{server}/enable", "label": f"{name}: server inschakelen", "before": 0, "after": 1}]
    title = f"Server {name} opnieuw proberen (inschakelen)"
    explanation = (
        f"{why} MSO zet 'enable' van 0 op 1 en leest de SABnzbd-configuratie terug. Daarna let MSO op SABnzbd's "
        "meldingen voor deze server: keert de fout terug, dan gaat hij in Autopilot weer uit (met een langere "
        "wachttijd voor de volgende proef)."
    )
    impact = {"Server": name, "Reden": why, "Terugdraaien": "enable weer op 0", "servers": [server]}
    if actor is None:
        return autoapply.propose_and_apply(
            "sabnzbd", title, ops, explanation=explanation, impact=impact, dedupe_key=f"usenet.auto_on:{server}",
            area=AREA,
        )
    ch = changes.create(
        "sabnzbd",
        title,
        ops,
        risk="automatisch_veilig",
        explanation=explanation,
        impact={"area": AREA, **impact},
        source="usenet.retry_server",
        actor=actor,
        dedupe_key=f"usenet.auto_on:{server}",
    )
    applied = changes.apply(ch["id"], actor)
    return dict(applied) | {"auto_applied": True, "auto_reason": None}


def retry_server(server: str, actor: str) -> dict[str, Any]:
    """'Opnieuw proberen' by a user: switch the server on now and watch it like a probe."""
    state.require_manage_mode()
    with SabClient.from_config() as client:
        servers = {s["name"]: s for s in client.servers()}
    if server not in servers:
        raise HTTPException(404, detail="SABnzbd-server niet gevonden")
    sab, labels = serverstatus.current(), _labels()
    name = _name(server, sab, labels)
    row = rows().get(server) or {}
    if servers[server]["enable"]:
        change = None
    else:
        change = _switch_on(server, name, why="Opnieuw proberen op verzoek van de gebruiker.", actor=actor)
    _save(
        server,
        status="probing",
        reason=row.get("reason") or (sab.get(server) or {}).get("status"),
        probes=int(row.get("probes") or 0),
        probe_started_at=_now(),
        probe_change_id=change["id"] if change else row.get("probe_change_id"),
        probe_trigger="user",
        next_probe_at=None,
        last_result="opnieuw geprobeerd door gebruiker",
    )
    audit(actor, "usenet.server_retry", f"SABnzbd-server {name} opnieuw geprobeerd", target="sabnzbd",
          detail={"server": server, "change_id": change["id"] if change else None})
    _reevaluate_failed_downloads()
    return {"server": server, "name": name, "change": change, "state": public_row(rows().get(server))}


# ------------------------------------------------------------------ the tick
def tick(client: SabClient | None = None) -> dict[str, Any]:
    """One pass of the server state machine. SABnzbd's persisted state (:func:`serverstatus.current`) must be
    fresh (the caller refreshes it). Without Autopilot only watching happens (no writes)."""
    own = client is None
    c = client or SabClient.from_config()
    try:
        servers = c.servers()
    finally:
        if own:
            c.close()
    by_name = {s["name"]: s for s in servers}
    enabled_now = {s["name"] for s in servers if s["enable"]}
    sab, labels, state_rows = serverstatus.current(), _labels(), rows()
    auto = state.autopilot()
    now = _now()
    out: dict[str, Any] = {"autopilot": auto, "switched_off": [], "switched_on": [], "probe_failed": [],
                           "recovered": [], "kept_last": [], "pending": []}
    changed = False
    watch = timedelta(hours=_h("usenet.probe_watch_h", 2))
    max_probes = int(_h("usenet.auto_probe_max", 5))

    # 1) servers being watched after a re-enable
    for server, row in state_rows.items():
        if server not in by_name:
            get_db().execute("DELETE FROM usenet_server_autoheal WHERE server=%s", (server,))
            continue
        name = _name(server, sab, labels)
        if row["status"] == "probing":
            if server not in enabled_now:  # switched off again outside MSO
                _save(server, status="disabled", disabled_at=now, last_result="buiten MSO uitgeschakeld",
                      next_probe_at=now + backoff(int(row["probes"] or 0)))
                continue
            err = error_after(server, row["probe_started_at"])
            if err:
                probes = int(row["probes"] or 0) + 1
                out["probe_failed"].append(server)
                audit(ACTOR, "usenet.server_probe_failed",
                      f"Proef mislukt: {name} geeft opnieuw '{serverstatus.LABELS.get(err['status'], err['status'])}'",
                      target="sabnzbd", detail={"server": server, "probes": probes})
                if not auto:
                    get_db().execute("DELETE FROM usenet_server_autoheal WHERE server=%s", (server,))
                    continue
                if len(enabled_now - {server}) == 0:
                    out["kept_last"].append(server)
                    continue
                st = {**(sab.get(server) or {}), "status": err["status"], "message": err["message"]}
                ch = _switch_off(server, name, st, probes=probes,
                                 why=f"De proef na het opnieuw inschakelen mislukte ({probes}e keer).")
                if ch.get("auto_applied"):
                    enabled_now.discard(server)
                    _save(server, status="given_up" if probes >= max_probes else "disabled", reason=err["status"],
                          message=err["message"], disabled_at=now, disable_change_id=ch["id"], probes=probes,
                          next_probe_at=None if probes >= max_probes else now + backoff(probes),
                          last_result="proef mislukt")
                    out["switched_off"].append(server)
                    changed = True
                else:
                    out["pending"].append({"server": server, "reason": ch.get("auto_reason")})
            elif row["probe_started_at"] <= now - watch and (sab.get(server) or {}).get("status") not in (
                serverstatus.FAILING
            ):
                _save(server, status="recovered", last_result="werkt weer", next_probe_at=None)
                out["recovered"].append(server)
                audit(ACTOR, "usenet.server_recovered", f"{name} werkt weer na opnieuw inschakelen",
                      target="sabnzbd", detail={"server": server, "trigger": row.get("probe_trigger")})
                changed = True
        elif row["status"] in ("disabled", "given_up"):
            if server in enabled_now:  # the user switched it on in SABnzbd (e.g. after fixing credentials)
                _save(server, status="probing", probe_started_at=now, probe_trigger="sabnzbd",
                      last_result="in SABnzbd weer ingeschakeld")
                changed = True
                continue
            own_ok = _own_test_ok(server, row.get("disabled_at"))
            due = row["status"] == "disabled" and row.get("next_probe_at") and row["next_probe_at"] <= now
            if not auto or not (own_ok or due):
                continue
            why = (
                "MSO's eigen test met inloggegevens slaagt weer."
                if own_ok
                else f"Automatische proef {int(row['probes'] or 0) + 1} na een wachttijd."
            )
            ch = _switch_on(server, name, why=why)
            if ch.get("auto_applied"):
                enabled_now.add(server)
                _save(server, status="probing", probe_started_at=now, probe_change_id=ch["id"],
                      probe_trigger="own_test" if own_ok else "autopilot", last_result=why)
                out["switched_on"].append(server)
                changed = True
            else:
                out["pending"].append({"server": server, "reason": ch.get("auto_reason")})

    # 2) failing, enabled servers → off (Autopilot only)
    if auto:
        state_rows = rows()
        cands = []
        for server in sorted(enabled_now):
            st = sab.get(server) or {}
            if st.get("status") not in serverstatus.DISABLE_SUGGESTED:
                continue
            row = state_rows.get(server) or {}
            if row.get("status") == "probing":
                continue  # judged on new errors only (above)
            since = failing_since(server, st)
            limit = timedelta(hours=_h("usenet.auto_disable_after_h", 6))
            if st["status"] != "account_expired" and now - since < limit:
                continue
            cands.append(server)
        # never the last enabled server: keep the best-priority one when every enabled server fails
        if cands and not (enabled_now - set(cands)):
            keep = min(cands, key=lambda s: (by_name[s]["priority"], s))
            cands.remove(keep)
            out["kept_last"].append(keep)
            audit(ACTOR, "usenet.server_kept_last",
                  f"{_name(keep, sab, labels)} blijft aan: het is de laatste ingeschakelde SABnzbd-server",
                  target="sabnzbd", detail={"server": keep})
        for server in sorted(cands, key=lambda s: (-by_name[s]["priority"], s)):
            if len(enabled_now - {server}) == 0:
                out["kept_last"].append(server)
                continue
            st = sab.get(server) or {}
            row = state_rows.get(server) or {}
            recent = row.get("status") == "recovered" and row.get("updated_at") and \
                row["updated_at"] > now - RECOVERED_MEMORY
            probes = int(row.get("probes") or 0) + (1 if recent else 0)
            name = _name(server, sab, labels)
            why = (
                "Het account is verlopen; MSO schakelt de server daarom meteen uit."
                if st["status"] == "account_expired"
                else f"De fout duurt langer dan {int(_h('usenet.auto_disable_after_h', 6))} uur."
            )
            ch = _switch_off(server, name, st, probes=probes, why=why)
            if ch.get("auto_applied"):
                enabled_now.discard(server)
                _save(server, status="given_up" if probes >= max_probes else "disabled", reason=st["status"],
                      message=st.get("message"), disabled_at=now, disable_change_id=ch["id"], probes=probes,
                      next_probe_at=None if probes >= max_probes else now + backoff(probes),
                      probe_started_at=None, last_result="automatisch uitgeschakeld")
                out["switched_off"].append(server)
                changed = True
            else:
                out["pending"].append({"server": server, "reason": ch.get("auto_reason")})
        if out["switched_off"]:
            _discard_covered_proposals(enabled_now)
    if changed:
        _reevaluate_failed_downloads()
    return out


# ------------------------------------------------------------------ reads
def public_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "status": row["status"],
        "reason": row.get("reason"),
        "reason_label": serverstatus.LABELS.get(row.get("reason") or "", None),
        "disabled_at": row.get("disabled_at"),
        "probes": int(row.get("probes") or 0),
        "next_probe_at": row.get("next_probe_at"),
        "probe_started_at": row.get("probe_started_at"),
        "probe_trigger": row.get("probe_trigger"),
        "last_result": row.get("last_result"),
        "disable_change_id": row.get("disable_change_id"),
        "updated_at": row.get("updated_at"),
    }


def overview() -> dict[str, Any]:
    return {
        "autopilot": state.autopilot(),
        "auto_disable_after_h": int(_h("usenet.auto_disable_after_h", 6)),
        "probe_interval_h": int(_h("usenet.auto_probe_interval_h", 24)),
        "servers": {s: public_row(r) for s, r in rows().items()},
    }


def run() -> dict[str, Any]:
    """Autopilot pass after every SABnzbd server-status refresh: server off/on, then retry failed downloads."""
    from mso.features.usenet import retry

    out: dict[str, Any] = {"at": _now().isoformat(timespec="seconds")}
    with SabClient.from_config() as client:
        try:
            out["servers"] = tick(client)
        except Exception as exc:
            LOGGER.warning("autopilot servers: %s", type(exc).__name__)
            out["servers"] = {"error": str(getattr(exc, "detail", exc))[:300]}
        out["retry_sync"] = retry.sync()
        try:
            res = retry.propose(client)
            out["retry"] = {k: v for k, v in res.items() if k != "change"} | (
                {"change_id": res["change"]["id"]} if res.get("change") else {}
            )
        except Exception as exc:
            LOGGER.warning("autopilot retry: %s", type(exc).__name__)
            out["retry"] = {"error": str(getattr(exc, "detail", exc))[:300]}
    state.set_state("usenet.autopilot_last_run", out)
    return out
