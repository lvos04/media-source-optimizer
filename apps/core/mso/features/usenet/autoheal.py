"""Autopilot for SABnzbd servers: failing servers off, back on only after SABnzbd's own server test passes (1.6.0).

1. Off (Autopilot only): a server SABnzbd reports with a definitive account problem
   (``serverstatus.DISABLE_SUGGESTED``: expired account, login refused, access denied, block used up) for longer than
   ``usenet.auto_disable_after_h`` — immediately for "account verlopen" and when it fails again within a week after it
   recovered — is switched off, but only after SABnzbd's own server test (:mod:`servertest`) confirms the problem. A
   passing test or "te veel verbindingen" means the account is alive (old warning or a connection limit): the server
   stays on. One change set per server on the ``sabnzbd`` applier, op ``destructive`` (reversible), verified by reading
   SABnzbd's config back, audited. The LAST enabled server is never switched off.
2. Testing (every mode; a test changes nothing): every server MSO switched off is tested every
   ``usenet.server_test_interval_h`` (6 h); after a week without a passing test every
   ``usenet.server_test_slow_interval_h`` (24 h); never given up (at least weekly). "Account verlopen": weekly (renewal)
   and one calm 'Wacht op jou' item, never a probe. A change of the server in SABnzbd (host/port/SSL/username/expiry/
   quota) → test right away; "Testen" on the Usenet page → test now. "Te veel verbindingen" is not a failed test.
3. Back on only after a passing test, through ``propose_and_apply`` (automatic in Autopilot, otherwise a proposal), or
   when the user switches it on in SABnzbd / uses "Opnieuw proberen". A proposal the owner discarded is not made
   again by the next scheduled passing test — only after a failing test, a change of the server in SABnzbd, a newer
   own MSO test or "Testen". The server is then watched: a new definitive error → confirming test → off again (an
   inconclusive test — unreachable/unknown/TLS — waits an hour and decides only after the usual 6 h). "Werkt weer"
   (``recovered_at``) only once SABnzbd really used the server (bytes or articles for that server rose since the
   re-enable) without new errors.
4. Fallback when SABnzbd does not offer the test: the 1.5.0 probe (enable again after ``usenet.auto_probe_interval_h``
   with backoff, given up after ``usenet.auto_probe_max``) — never for an expired account.

Every server state change re-evaluates failed downloads that were skipped earlier (:mod:`recovery.fallback`).
"""

from __future__ import annotations

import logging
import secrets as pysecrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from mso import changes, state
from mso import settings_registry as sr
from mso.audit import audit
from mso.connectors.sabnzbd import SabClient, SabError
from mso.db import get_db, jsonb
from mso.domain import outcomes
from mso.features.usenet import autoapply, providers, serverstatus, servertest

LOGGER = logging.getLogger(__name__)
ACTOR = "autopilot:usenet"
AREA = "usenet"
RECOVERED_MEMORY = timedelta(days=7)  # failing again this soon after recovering: switched off without the 6 h wait
EXPIRED = "account_expired"
WEEK = timedelta(days=7)
RATE_LIMITED_RETRY = timedelta(hours=1)  # "te veel verbindingen": the account is alive, test again soon
CONFIRM_MAX_AGE = timedelta(hours=1)  # a confirming test younger than this is reused (≤ 1 test per hour per server)
LOCKED_RECHECK = timedelta(days=1)  # SABnzbd's config lock: no switch-over is possible; look again once a day
LOCKED_TEXT = ("SABnzbd-configuratie is vergrendeld: MSO kan deze server niet testen of omschakelen (SABnzbd weigert "
               "dan ook set_config); MSO kijkt dagelijks opnieuw")
CONFIRM_MAX_AGE_FLAPPING = timedelta(hours=6)
TESTS_PER_TICK = 6  # each SABnzbd test blocks a few seconds; the rest follows in the next round (5 min)
ACCOUNT_PROBLEMS = set(serverstatus.DISABLE_SUGGESTED)
NOT_USED_YET = "test geslaagd en server aan; SABnzbd heeft hem nog niet gebruikt"
DECLINED = ("test geslaagd; je verwierp het voorstel om de server weer in te schakelen. MSO stelt het opnieuw voor na "
            "een wijziging in SABnzbd, een mislukte test of met 'Testen'")
FP_SECRET = "usenet.server_config_fp_key"  # noqa: S105 - vault entry name, not a secret


def _now() -> datetime:
    return datetime.now(UTC)


def _h(key: str, default: float) -> float:
    try:
        return float(sr.get(key))
    except Exception:
        return default


def backoff(probes: int) -> timedelta:
    """Fallback without SABnzbd's test: wait before the next probe after ``probes`` failed probes (capped)."""
    base = _h("usenet.auto_probe_interval_h", 24)
    cap = _h("usenet.auto_probe_max_interval_h", 168)
    return timedelta(hours=min(cap, base * 2 ** max(0, probes)))


def test_interval(reason: str | None, disabled_at: datetime | None, now: datetime | None = None) -> timedelta:
    """Wait until the next SABnzbd test of a switched-off server: 6 h, 24 h after a week off, weekly when expired;
    never longer than a week."""
    now = now or _now()
    if reason == EXPIRED:
        return WEEK
    hours = _h("usenet.server_test_interval_h", 6)
    if disabled_at is not None and now - disabled_at >= WEEK:
        hours = max(hours, _h("usenet.server_test_slow_interval_h", 24))
    return min(timedelta(hours=hours), WEEK)


# ------------------------------------------------------------------ persistence
def rows() -> dict[str, dict[str, Any]]:
    return {r["server"]: dict(r) for r in get_db().fetchall("SELECT * FROM usenet_server_autoheal")}


def _save(server: str, **fields: Any) -> None:
    if "probe_usage" in fields and fields["probe_usage"] is not None:
        fields["probe_usage"] = jsonb(fields["probe_usage"])
    if "status" not in fields:  # partial update of an existing row (an insert would need a status)
        if fields:
            get_db().execute(
                f"UPDATE usenet_server_autoheal SET {', '.join(f'{c}=%s' for c in fields)}, updated_at=now() "
                "WHERE server=%s",
                [*fields.values(), server],
            )
        return
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


def _fp_key() -> bytes | None:
    """Key for the SABnzbd server-config fingerprint (random, kept in the vault). Without it no change detection."""
    try:
        from mso.vault import get_vault

        vault = get_vault()
        key = vault.get(FP_SECRET)
        if not key:
            key = pysecrets.token_hex(32)
            vault.set(FP_SECRET, key)
        return key.encode()
    except Exception:  # vault unavailable: the periodic test still runs
        return None


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
    """A definitive account error SABnzbd reported for ``server`` after ``ts`` (re-enabled server fails again)."""
    return get_db().fetchone(
        "SELECT status, message, last_at FROM usenet_sab_server_events WHERE server=%s AND status = ANY(%s) "
        "AND last_at > %s ORDER BY last_at DESC LIMIT 1",
        (server, sorted(serverstatus.DISABLE_SUGGESTED), ts),
    )


def _first_error_after(server: str, ts: datetime) -> datetime | None:
    """When the definitive account errors SABnzbd reported for ``server`` after ``ts`` started (not before ``ts``)."""
    first = get_db().scalar(
        "SELECT min(GREATEST(first_at, %s)) FROM usenet_sab_server_events WHERE server=%s AND status = ANY(%s) "
        "AND last_at > %s",
        (ts, server, sorted(serverstatus.DISABLE_SUGGESTED), ts),
    )
    return first


def _declined(server: str, since: datetime | None) -> bool:
    """The owner (not MSO) discarded the 'weer inschakelen' proposal for ``server`` after ``since`` (the switch-off)
    and no failing test followed: a scheduled passing test does not propose it again."""
    ids = [r["id"] for r in get_db().fetchall(
        "SELECT id FROM change_sets WHERE target='sabnzbd' AND dedupe_key=%s AND status='discarded'"
        + (" AND created_at > %s" if since else ""),
        (f"usenet.auto_on:{server}", since) if since else (f"usenet.auto_on:{server}",),
    )]
    if not ids:
        return False
    at = get_db().scalar(
        "SELECT max(ts) FROM audit_log WHERE action='change.discarded' AND change_id = ANY(%s) AND actor <> %s",
        (ids, ACTOR),
    )
    if at is None:
        return False
    return not get_db().scalar(
        "SELECT count(*) FROM usenet_server_tests WHERE server=%s AND at > %s AND NOT (result = ANY(%s))",
        (server, at, sorted(servertest.ALIVE | {"unsupported"})),
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


def _own_test_at(server: str, after: datetime | None) -> datetime | None:
    """When MSO's own NNTP test (with credentials) of the provider linked to ``server`` last passed after
    ``after``, or None."""
    if after is None:
        return None
    best = None
    for p in providers.list_rows():
        if p.get("sab_server") != server or not providers.has_credentials(p["slug"]):
            continue
        at = providers._ts(p.get("last_check_at"))
        if p.get("status") == "ok" and at is not None and at > after and (best is None or at > best):
            best = at
    return best


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


def _discard_switch_on_proposal(server: str) -> None:
    """A later failing test makes an open 'weer inschakelen' proposal for ``server`` wrong."""
    for ch in get_db().fetchall(
        "SELECT id FROM change_sets WHERE target='sabnzbd' AND status IN ('preview','approved') AND dedupe_key=%s",
        (f"usenet.auto_on:{server}",),
    ):
        try:
            changes.discard(ch["id"], ACTOR)
        except HTTPException:
            pass


def _mso_enable_change(server: str, after: int, since: datetime | None) -> int | None:
    """The newest applied MSO change (an autoheal proposal a person applied, an approved 'kapotte servers
    uitschakelen', …) that set ``enable`` of ``server`` to ``after`` since ``since``, or None."""
    if since is None:
        return None
    return get_db().scalar(
        "SELECT id FROM change_sets WHERE target='sabnzbd' AND status='applied' AND applied_at >= %s "
        "AND ops @> %s::jsonb ORDER BY applied_at DESC, id DESC LIMIT 1",
        (since, jsonb([{"path": f"servers/{server}/enable", "after": after}])),
    )


def _owner_choice(server: str, *, mso_value: int, current_value: int, change_id: int | None) -> None:
    """The owner switched the server on/off outside MSO's own flow (in SABnzbd, or by rolling MSO's change back):
    their choice. Recorded as an owner override, so Autopilot does not reverse it on its next round (the same rule
    the footprint applies an hour later at most; here at once, so the timing does not decide)."""
    try:
        from mso.features.automation import autopilot, overrides
    except ImportError:  # automation module disabled: nothing applies automatically anyway
        return
    overrides.record("sabnzbd", f"servers/{server}/enable", mso_value=mso_value, current_value=current_value,
                     change_id=change_id, days=autopilot.override_days())


def _override_until(server: str) -> datetime | None:
    try:
        from mso.features.automation import overrides
    except ImportError:
        return None
    row = overrides.active("sabnzbd", f"servers/{server}/enable")
    return row["until"] if row else None


def _recent_recovery(row: dict[str, Any], now: datetime) -> bool:
    at = row.get("recovered_at") or row.get("updated_at")
    return row.get("status") == "recovered" and at is not None and at > now - RECOVERED_MEMORY


# ------------------------------------------------------------------ usage check ("werkt weer")
def _server_stats(client: SabClient, cache: dict[str, Any], server: str) -> dict[str, Any] | None:
    """SABnzbd's live counters for ``server`` (``server_stats``; one call per round), or None if unavailable."""
    if "stats" not in cache:
        try:
            cache["stats"] = client.server_stats().get("servers") or {}
        except SabError:
            cache["stats"] = None
    stats = cache["stats"]
    if stats is None:
        return None
    return dict(stats.get(server) or {})


def usage_marker(stats: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any] | None:
    """SABnzbd's usage counters of one server at this moment: all-time bytes + articles tried today."""
    if stats is None:
        return None
    day = (now or _now()).date().isoformat()
    tried = stats.get("articles_tried") or {}
    return {
        "bytes": int(stats.get("total") or 0),
        "day": day,
        "tried_day": int(tried.get(day) or 0) if isinstance(tried, dict) else 0,
    }


def used_since(base: dict[str, Any], stats: dict[str, Any]) -> bool | None:
    """Did SABnzbd use the server since ``base`` (:func:`usage_marker`)? None = SABnzbd's statistics were reset."""
    total = int(stats.get("total") or 0)
    if total < int(base.get("bytes") or 0):
        return None
    if total > int(base.get("bytes") or 0):
        return True
    tried = stats.get("articles_tried") or {}
    day = str(base.get("day") or "")
    if not isinstance(tried, dict) or not day:
        return False
    if any(d > day and int(v or 0) > 0 for d, v in tried.items()):
        return True
    return int(tried.get(day) or 0) > int(base.get("tried_day") or 0)


# ------------------------------------------------------------------ SABnzbd's test
def _run_test(client: SabClient, server: str, trigger: str, budget: list[int]) -> dict[str, Any] | None:
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    try:
        return servertest.run(client, server, trigger=trigger)
    except SabError as exc:
        if exc.code == "auth":
            raise
        LOGGER.info("SABnzbd-servertest %s overgeslagen: %s", server, exc.code)
        return None


def _confirm(
    client: SabClient, server: str, budget: list[int], *, after: datetime | None = None
) -> tuple[str, dict[str, Any] | None]:
    """Before switching a server off: ``off`` (SABnzbd's test confirms an account problem), ``keep`` (test passes or
    too many connections: the account is alive), ``unsupported``, ``inconclusive`` (unreachable/unknown) or ``later``
    (no test possible this round). A recent test (newer than ``after``) is reused."""
    now = _now()
    age = CONFIRM_MAX_AGE_FLAPPING if servertest.flapping(server) else CONFIRM_MAX_AGE
    floor = now - age if after is None else max(now - age, after)
    last = servertest.latest(server)
    if last and last["result"] == "locked" and last["at"] > now - LOCKED_RECHECK:
        return "locked", (servertest.public(last) or {}) | {"supported": False}
    if last and last["at"] > floor:
        test = (servertest.public(last) or {}) | {"supported": last["result"] != "unsupported"}
    else:
        fresh = _run_test(client, server, "confirm", budget)
        if fresh is None:
            return "later", None
        test = fresh
    result = test["result"]
    if result in servertest.ALIVE:
        return "keep", test
    if result in ACCOUNT_PROBLEMS:
        return "off", test
    if result == "unsupported":
        return "unsupported", test
    if result == "locked":
        return "locked", test
    return "inconclusive", test


def _after_test(server: str, row: dict[str, Any], test: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Schedule the next test of a switched-off server from a test result. Returns the updated row."""
    result = test["result"]
    reason = row.get("reason")
    f: dict[str, Any] = {}
    if result in ACCOUNT_PROBLEMS and result != reason:
        reason = f["reason"] = result
        f["message"] = test.get("message")
    if result == "ok":
        f["tests_failed"] = 0
        f["next_test_at"] = now + test_interval(reason, row.get("disabled_at"), now)
    elif result == "rate_limited":  # logged in, but too many connections: not a dead account, not a failed test
        f["next_test_at"] = now + RATE_LIMITED_RETRY
        f["last_result"] = "SABnzbd-test: te veel verbindingen (het account werkt wel); opnieuw testen over 1 uur"
    elif result == "locked":  # SABnzbd's config lock refuses set_config too: no blind probe either
        f["next_test_at"] = now + LOCKED_RECHECK
        f["last_result"] = LOCKED_TEXT
    elif result == "unsupported":
        f["next_test_at"] = now + WEEK  # look again whether SABnzbd offers the test (e.g. after an update)
        if reason != EXPIRED and row.get("status") == "disabled" and not row.get("next_probe_at"):
            f["next_probe_at"] = now + backoff(int(row.get("probes") or 0))
    else:
        f["tests_failed"] = int(row.get("tests_failed") or 0) + 1
        f["next_test_at"] = now + test_interval(reason, row.get("disabled_at"), now)
        msg = f' ("{test["message"]}")' if test.get("message") else ""
        f["last_result"] = f"SABnzbd-test: {(servertest.label(result) or result).lower()}{msg}"[:300]
        _discard_switch_on_proposal(server)
    if result != "unsupported":
        f["next_probe_at"] = None  # SABnzbd can test: never a blind probe
        if row.get("status") == "given_up":
            f["status"] = "disabled"  # never given up while SABnzbd can test it
    _save(server, **f)
    # servertest.run already stored these on the row; keep the returned dict in step with the database
    return {**row, **f, "last_test_result": result, "last_test_at": test.get("at") or row.get("last_test_at")}


def _sync_expired_notice(server: str, name: str, row: dict[str, Any] | None) -> None:
    """One calm 'Wacht op jou' item per expired account MSO switched off; resolved when it is back on or gone, or
    once SABnzbd's test passes again (renewal recognised; the server may still wait for a proposal)."""
    key = f"usenet.server_expired:{server}"
    db = get_db()
    if not (row and row.get("status") in ("disabled", "given_up") and row.get("reason") == EXPIRED
            and row.get("last_test_result") not in servertest.ALIVE):
        db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
                   "AND status='open'", (key,))
        return
    existing = db.fetchone("SELECT status FROM recommendations WHERE dedupe_key=%s", (key,))
    if existing and existing["status"] in ("open", "dismissed", "accepted"):
        return  # already told once (or the owner dismissed it): stay calm
    msg = f' ("{row["message"]}")' if row.get("message") else ""
    outcomes.recommend(
        "usenet",
        f"Account bij {name} verlopen — verlengen of de server in SABnzbd verwijderen",
        f"SABnzbd meldt dat het account bij {name} verlopen is{msg}. MSO heeft de server in SABnzbd uitgezet, zodat "
        "downloads er geen last meer van hebben, en zet hem niet blind weer aan. Verleng je het abonnement, dan ziet "
        "MSO dat bij de wekelijkse SABnzbd-servertest (meteen als je de server in SABnzbd aanpast, of met 'Testen' op "
        "Usenet-providers) en zet de server zelf weer aan. Gebruik je het account niet meer, verwijder de server dan "
        "in SABnzbd. MSO zegt nooit iets op en verwijdert niets.",
        risk="toestemming_nodig",
        subject=f"sabnzbd:{server}",
        impact="Geen: de server staat al uit; alleen jouw keuze (verlengen of verwijderen)",
        dedupe_key=key,
        evidence={"server": server, "reason": EXPIRED, "disabled_at": str(row.get("disabled_at") or ""),
                  "source": "sabnzbd"},
    )
    db.execute("UPDATE recommendations SET status='open', updated_at=now() WHERE dedupe_key=%s AND status='resolved'",
               (key,))


# ------------------------------------------------------------------ actions
def _switch_off(
    server: str, name: str, st: dict[str, Any], *, probes: int, why: str, test: dict[str, Any] | None = None
) -> dict[str, Any]:
    label = serverstatus.LABELS.get(st.get("status") or "", "fout")
    expired = st.get("status") == EXPIRED
    fallback = bool(test and test.get("result") == "unsupported")
    msg = f' SABnzbd meldt: "{st["message"]}".' if st.get("message") else ""
    confirmed = ""
    if test and test.get("result") in ACCOUNT_PROBLEMS:
        said = f': "{test["message"]}".' if test.get("message") else "."
        confirmed = f" SABnzbd's eigen servertest bevestigt dit{said}"
    if fallback and not expired:
        wait = backoff(probes)
        hours = int(wait.total_seconds() // 3600)
        retry_text = f"MSO probeert de server over {hours} uur zelf opnieuw (SABnzbd biedt geen servertest aan)."
        retry_impact = f"automatisch over {hours} uur"
    elif expired:
        retry_text = ("MSO zet hem niet blind weer aan: SABnzbd test het account elke week (en meteen als je de server "
                      "in SABnzbd aanpast) en pas als die test slaagt gaat de server weer aan.")
        retry_impact = "na een geslaagde SABnzbd-servertest (wekelijks)"
    else:
        hours = int(test_interval(st.get("status"), _now()).total_seconds() // 3600)
        retry_text = (f"MSO laat SABnzbd de server elke {hours} uur testen (SABnzbd's eigen servertest, met het "
                      "wachtwoord dat in SABnzbd staat) en zet hem pas weer aan als die test slaagt.")
        retry_impact = f"na een geslaagde SABnzbd-servertest (elke {hours} uur)"
    explanation = (
        f"{name}: {label.lower()}.{msg}{confirmed} {why} Zolang de server aanstaat probeert SABnzbd hem bij elke "
        "download, wat fouten geeft en ontbrekende artikelen niet aanvult. MSO zet 'enable' van 1 op 0 en leest de "
        "SABnzbd-configuratie terug ter controle. Terugdraaien zet de server weer aan. Er wordt niets verwijderd en "
        f"geen abonnement opgezegd. {retry_text}"
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
            "SABnzbd-servertest": (servertest.label(test.get("result")) if test else None) or "—",
            "Opnieuw proberen": retry_impact,
            "Terugdraaien": "enable weer op 1",
            "servers": [server],
        },
        dedupe_key=f"usenet.auto_off:{server}",
        area=AREA,
    )


def _switch_on(server: str, name: str, *, why: str, actor: str | None = None) -> dict[str, Any]:
    """Re-enable one server. ``actor`` set = user action (applied as that user, needs beheermodus);
    otherwise through Autopilot (a proposal when MSO may not apply it by itself)."""
    ops = [{"path": f"servers/{server}/enable", "label": f"{name}: server inschakelen", "before": 0, "after": 1}]
    title = f"Server {name} weer inschakelen" if actor is None else f"Server {name} opnieuw proberen (inschakelen)"
    explanation = (
        f"{why} MSO zet 'enable' van 0 op 1 en leest de SABnzbd-configuratie terug. Daarna let MSO op SABnzbd's "
        "meldingen voor deze server: keert een accountfout terug en bevestigt SABnzbd's eigen servertest die, dan gaat "
        "hij in Autopilot weer uit. 'Werkt weer' telt pas als SABnzbd de server echt gebruikt zonder nieuwe fouten."
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


def _start_watch(client: SabClient, server: str, cache: dict[str, Any], **fields: Any) -> None:
    """The server is on again: watch it, with SABnzbd's usage counters as the baseline for 'werkt weer'."""
    _save(server, status="probing", probe_started_at=_now(), next_test_at=None, next_probe_at=None,
          probe_usage=usage_marker(_server_stats(client, cache, server)), **fields)


def retry_server(server: str, actor: str) -> dict[str, Any]:
    """'Opnieuw proberen' by a user: switch the server on now and watch it (no test needed: the user decides)."""
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
        _start_watch(
            client,
            server,
            {},
            reason=row.get("reason") or (sab.get(server) or {}).get("status"),
            probes=int(row.get("probes") or 0),
            probe_change_id=change["id"] if change else row.get("probe_change_id"),
            probe_trigger="user",
            last_result="opnieuw geprobeerd door gebruiker",
        )
    _sync_expired_notice(server, name, None)
    audit(actor, "usenet.server_retry", f"SABnzbd-server {name} opnieuw geprobeerd", target="sabnzbd",
          detail={"server": server, "change_id": change["id"] if change else None})
    _reevaluate_failed_downloads()
    return {"server": server, "name": name, "change": change, "state": public_row(rows().get(server))}


def after_user_test(client: SabClient, server: str, test: dict[str, Any]) -> dict[str, Any]:
    """'Testen' on a server MSO switched off: schedule from the result; a passing test switches it back on (Autopilot)
    or leaves one proposal."""
    row = rows().get(server)
    if not row or row["status"] not in ("disabled", "given_up"):
        return {"change": None, "state": public_row(row)}
    now = _now()
    row = _after_test(server, row, test, now)
    name = _name(server, serverstatus.current(), _labels())
    change = None
    if test["result"] == "ok":
        why = "SABnzbd's eigen servertest slaagt weer (getest op verzoek)."
        ch = _switch_on(server, name, why=why)
        change = {k: ch.get(k) for k in ("id", "status", "title", "auto_applied", "auto_reason")}
        if ch.get("auto_applied"):
            _start_watch(client, server, {}, probe_change_id=ch["id"], probe_trigger="server_test", last_result=why)
            _reevaluate_failed_downloads()
        else:
            _save(server, last_result=f"{why} Weer inschakelen wacht: {ch.get('auto_reason')}"[:300])
    row = rows().get(server)
    _sync_expired_notice(server, name, row)
    return {"change": change, "state": public_row(row)}


# ------------------------------------------------------------------ the tick
def tick(client: SabClient | None = None) -> dict[str, Any]:
    """One pass of the server state machine. SABnzbd's persisted state (:func:`serverstatus.current`) must be fresh
    (the caller refreshes it). Tests run in every mode; servers go off only in Autopilot; back on automatically in
    Autopilot, otherwise as a proposal."""
    own = client is None
    c = client or SabClient.from_config()
    try:
        return _tick(c)
    finally:
        if own:
            c.close()


def _tick(c: SabClient) -> dict[str, Any]:
    servers = c.servers(fingerprint_key=_fp_key())
    by_name = {s["name"]: s for s in servers}
    enabled_now = {s["name"] for s in servers if s["enable"]}
    sab, labels, state_rows = serverstatus.current(), _labels(), rows()
    auto = state.autopilot()
    now = _now()
    out: dict[str, Any] = {"autopilot": auto, "switched_off": [], "switched_on": [], "probe_failed": [],
                           "recovered": [], "kept_last": [], "kept_on": [], "tested": [], "pending": [], "locked": []}
    changed = False
    watch = timedelta(hours=_h("usenet.probe_watch_h", 2))
    limit = timedelta(hours=_h("usenet.auto_disable_after_h", 6))
    max_probes = int(_h("usenet.auto_probe_max", 5))
    budget = [TESTS_PER_TICK]
    cache: dict[str, Any] = {}

    # 1) servers MSO switched off or is watching
    for server, row in state_rows.items():
        if server not in by_name:
            get_db().execute("DELETE FROM usenet_server_autoheal WHERE server=%s", (server,))
            _sync_expired_notice(server, server, None)
            continue
        name = _name(server, sab, labels)
        if row["status"] == "probing":
            if server not in enabled_now:  # switched off again during the watch
                fallback = row.get("last_test_result") == "unsupported"
                off_id = _mso_enable_change(server, 0, row.get("probe_started_at"))
                if off_id is None:  # the owner (in SABnzbd, or rolling MSO's switch-on back): their own choice
                    _owner_choice(server, mso_value=1, current_value=0, change_id=row.get("probe_change_id"))
                _save(server, status="disabled", disabled_at=now,
                      last_result="buiten MSO uitgeschakeld" if off_id is None
                      else "uitgeschakeld (voorstel van MSO toegepast)",
                      disable_change_id=off_id or row.get("disable_change_id"),
                      next_test_at=now + test_interval(row.get("reason"), now, now),
                      next_probe_at=now + backoff(int(row["probes"] or 0)) if fallback else None)
                continue
            err = error_after(server, row["probe_started_at"])
            if err and row.get("next_test_at") and row["next_test_at"] > now:
                continue  # a passing test kept it on less than an hour ago, or switching off waits (see below)
            if err and not auto and state.mode() == "autopilot":
                continue  # noodrem or pause: keep the watch (recovered_at, probes); decided after 'Hervatten'
            if err:
                verdict, test = _confirm(c, server, budget, after=row["probe_started_at"])
                if verdict == "later":
                    continue
                if verdict == "locked":  # a switch-off would only fail (and count towards the noodrem)
                    _save(server, next_test_at=now + LOCKED_RECHECK, last_result=LOCKED_TEXT)
                    out["locked"].append(server)
                    continue
                err_label = serverstatus.LABELS.get(err["status"], err["status"])
                if verdict == "keep" and test:
                    _save(server, probe_started_at=now, next_test_at=now + CONFIRM_MAX_AGE,
                          last_result=f"SABnzbd meldt '{err_label.lower()}', maar SABnzbd's eigen servertest: "
                          f"{(servertest.label(test['result']) or '').lower()}; de server blijft aan")
                    out["kept_on"].append(server)
                    continue
                if verdict == "inconclusive" and test:
                    # like step 2: switching off before the usual wait needs a confirming test
                    started = _first_error_after(server, row["probe_started_at"]) or now
                    if now - started < limit:
                        _save(server, next_test_at=now + CONFIRM_MAX_AGE,
                              last_result=f"SABnzbd meldt '{err_label.lower()}'; SABnzbd's eigen servertest: "
                              f"{(servertest.label(test['result']) or '').lower()}; MSO test over een uur opnieuw")
                        out["kept_on"].append(server)
                        continue
                probes = int(row["probes"] or 0) + 1

                def _probe_failed(probes: int = probes, test: dict[str, Any] | None = test,
                                  err_label: str = err_label, server: str = server, name: str = name) -> None:
                    out["probe_failed"].append(server)
                    audit(ACTOR, "usenet.server_probe_failed", f"{name} geeft opnieuw '{err_label}'",
                          target="sabnzbd", detail={"server": server, "probes": probes,
                                                    "test": (test or {}).get("result")})

                if not auto:  # the owner left Autopilot (state.mode(): a hold or pause continued above)
                    _probe_failed()
                    get_db().execute("DELETE FROM usenet_server_autoheal WHERE server=%s", (server,))
                    continue
                if len(enabled_now - {server}) == 0:
                    _probe_failed()
                    out["kept_last"].append(server)
                    continue
                confirmed = bool(test and test["result"] in ACCOUNT_PROBLEMS)
                reason = test["result"] if confirmed and test else err["status"]
                message = (test or {}).get("message") if confirmed else err["message"]
                st = {**(sab.get(server) or {}), "status": reason, "message": message}
                fallback = verdict == "unsupported"
                ch = _switch_off(server, name, st, probes=probes, test=test,
                                 why="De server faalde opnieuw nadat hij weer was ingeschakeld.")
                if ch.get("auto_applied"):
                    _probe_failed()
                    enabled_now.discard(server)
                    give_up = fallback and probes >= max_probes
                    _save(server, status="given_up" if give_up else "disabled", reason=reason, message=message,
                          disabled_at=now, disable_change_id=ch["id"], probes=probes,
                          next_probe_at=now + backoff(probes) if fallback and not give_up and reason != EXPIRED
                          else None,
                          next_test_at=now + (WEEK if fallback else test_interval(reason, now, now)),
                          tests_failed=1 if test and not fallback else 0, probe_usage=None,
                          last_result="opnieuw mislukt na inschakelen")
                    out["switched_off"].append(server)
                    changed = True
                    _sync_expired_notice(server, name, rows().get(server))
                else:
                    # not applied (the owner's own choice, cooldown, hourly cap …): one audit line and one proposal,
                    # then wait — not a new audit line and a new SABnzbd test every 5-minute round
                    reason_txt = str(ch.get("auto_reason") or "")
                    waiting = f"uitschakelen wacht: {reason_txt}"[:300]
                    if row.get("last_result") != waiting:
                        _probe_failed()
                    until = _override_until(server)
                    wait_until = until if until is not None and until > now else now + CONFIRM_MAX_AGE
                    _save(server, next_test_at=wait_until, last_result=waiting)
                    out["pending"].append({"server": server, "reason": ch.get("auto_reason")})
            elif row["probe_started_at"] <= now - watch and (sab.get(server) or {}).get("status") not in (
                serverstatus.FAILING
            ):
                stats = _server_stats(c, cache, server)
                if stats is None:
                    continue  # SABnzbd's counters unavailable: keep watching
                base = row.get("probe_usage")
                used = used_since(base, stats) if base else None
                if used is None:  # no baseline yet (1.5.0 probe) or SABnzbd's statistics were reset
                    _save(server, probe_usage=usage_marker(stats, now))
                    continue
                if not used:
                    if row.get("last_result") != NOT_USED_YET:
                        _save(server, last_result=NOT_USED_YET)
                    continue
                _save(server, status="recovered", recovered_at=now, next_probe_at=None, next_test_at=None,
                      tests_failed=0, last_result="werkt weer: SABnzbd gebruikt de server zonder fouten")
                out["recovered"].append(server)
                audit(ACTOR, "usenet.server_recovered", f"{name} werkt weer: SABnzbd gebruikt de server zonder fouten",
                      target="sabnzbd", detail={"server": server, "trigger": row.get("probe_trigger")})
                changed = True
        elif row["status"] in ("disabled", "given_up"):
            if server in enabled_now:  # switched on again outside MSO's own test flow
                on_id = _mso_enable_change(server, 1, row.get("disabled_at"))
                if on_id is None:
                    # the owner switched it on in SABnzbd (e.g. after fixing credentials) or rolled MSO's switch-off
                    # back: their choice — a later error leaves a proposal, Autopilot does not switch it off again
                    _owner_choice(server, mso_value=0, current_value=1, change_id=row.get("disable_change_id"))
                    _start_watch(c, server, cache, probe_trigger="sabnzbd", last_result="in SABnzbd weer ingeschakeld")
                else:  # MSO's own 'weer inschakelen' proposal, applied by a person
                    _start_watch(c, server, cache, probe_trigger="server_test", probe_change_id=on_id,
                                 last_result="voorstel 'weer inschakelen' toegepast")
                _sync_expired_notice(server, name, None)
                changed = True
                continue
            fp = by_name[server].get("fingerprint")
            old_fp = row.get("config_fp")
            cfg_changed = bool(fp and old_fp and fp != old_fp)
            own_at = _own_test_at(server, row.get("disabled_at"))
            own_new = own_at is not None and (row.get("last_test_at") is None or own_at > row["last_test_at"])
            due = cfg_changed or own_new or row.get("next_test_at") is None or row["next_test_at"] <= now
            test = None
            trigger = "config_changed" if cfg_changed else "own_test" if own_new else "schedule"
            if due:
                test = _run_test(c, server, trigger, budget)
                if test:
                    out["tested"].append(server)
                    row = _after_test(server, row, test, now)
            # a changed fingerprint is stored only once a test actually ran (budget used up or a transient SABnzbd
            # error this round: the "test right away" trigger stays for the next round)
            if fp and fp != old_fp and (test is not None or not cfg_changed):
                _save(server, config_fp=fp)
            result = test["result"] if test else None
            unsupported = (result or row.get("last_test_result")) == "unsupported"
            passed = result == "ok" or (unsupported and own_at is not None)
            probe_due = bool(
                auto and unsupported and row["status"] == "disabled" and row.get("reason") != EXPIRED
                and row.get("next_probe_at") and row["next_probe_at"] <= now
            )
            if not (passed or probe_due):
                _sync_expired_notice(server, name, row)
                continue
            if result == "ok" and trigger == "schedule" and _declined(server, row.get("disabled_at")):
                if row.get("last_result") != DECLINED:
                    _save(server, last_result=DECLINED)
                _sync_expired_notice(server, name, row)
                continue
            if result == "ok":
                why = "SABnzbd's eigen servertest slaagt weer" + (
                    " na je wijziging in SABnzbd." if trigger == "config_changed" else "."
                )
                how = "server_test"
            elif passed:
                why = "MSO's eigen test met inloggegevens slaagt weer (SABnzbd biedt geen servertest aan)."
                how = "own_test"
            else:
                why = (f"Automatische proef {int(row['probes'] or 0) + 1} na een wachttijd (SABnzbd biedt geen "
                       "servertest aan).")
                how = "autopilot"
            ch = _switch_on(server, name, why=why)
            if ch.get("auto_applied"):
                enabled_now.add(server)
                _start_watch(c, server, cache, probe_change_id=ch["id"], probe_trigger=how, last_result=why)
                _sync_expired_notice(server, name, None)
                out["switched_on"].append(server)
                changed = True
            else:
                _save(server, last_result=f"{why} Weer inschakelen wacht: {ch.get('auto_reason')}"[:300])
                _sync_expired_notice(server, name, row)
                out["pending"].append({"server": server, "reason": ch.get("auto_reason")})

    # 2) failing, enabled servers → off (Autopilot only), after SABnzbd's own test confirms the problem
    if auto:
        state_rows = rows()
        confirmed: dict[str, tuple[dict[str, Any] | None, bool]] = {}
        for server in sorted(enabled_now):
            st = sab.get(server) or {}
            if st.get("status") not in serverstatus.DISABLE_SUGGESTED:
                continue
            row = state_rows.get(server) or {}
            if row.get("status") == "probing":
                continue  # judged on new errors only (above)
            waited = st["status"] == EXPIRED or now - failing_since(server, st) >= limit
            recent = _recent_recovery(row, now)
            if not (waited or recent):
                continue
            verdict, test = _confirm(c, server, budget)
            if verdict == "later":
                continue
            if verdict == "locked":  # SABnzbd would refuse the switch-off: no failed automatic apply, one notice
                out["locked"].append(server)
                continue
            if verdict == "keep":
                out["kept_on"].append(server)
                continue
            if not waited and verdict not in ("off", "unsupported"):
                continue  # switching off before the usual wait needs a confirming test
            confirmed[server] = (test, recent)
        cands = sorted(confirmed)
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
            test, recent = confirmed[server]
            st = dict(sab.get(server) or {})
            if test and test["result"] in ACCOUNT_PROBLEMS:
                st["status"], st["message"] = test["result"], test.get("message") or st.get("message")
            row = state_rows.get(server) or {}
            probes = int(row.get("probes") or 0) + (1 if recent else 0)
            name = _name(server, sab, labels)
            fallback = bool(test and test["result"] == "unsupported")
            if st["status"] == EXPIRED:
                why = "Het account is verlopen; MSO schakelt de server daarom meteen uit."
            elif recent:
                why = "De server faalt opnieuw binnen een week nadat hij weer werkte; MSO schakelt hem meteen uit."
            else:
                why = f"De fout duurt langer dan {int(limit.total_seconds() // 3600)} uur."
            ch = _switch_off(server, name, st, probes=probes, why=why, test=test)
            if ch.get("auto_applied"):
                enabled_now.discard(server)
                give_up = fallback and probes >= max_probes
                _save(server, status="given_up" if give_up else "disabled", reason=st["status"],
                      message=st.get("message"), disabled_at=now, disable_change_id=ch["id"], probes=probes,
                      next_probe_at=now + backoff(probes) if fallback and not give_up and st["status"] != EXPIRED
                      else None,
                      next_test_at=now + (WEEK if fallback else test_interval(st["status"], now, now)),
                      tests_failed=1 if test and not fallback else 0, probe_started_at=None, probe_usage=None,
                      config_fp=by_name[server].get("fingerprint"), last_result="automatisch uitgeschakeld",
                      last_test_at=(test or {}).get("at"), last_test_result=(test or {}).get("result"))
                out["switched_off"].append(server)
                changed = True
                _sync_expired_notice(server, name, rows().get(server))
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
        # 1.6.0: SABnzbd's own server test instead of blind probes
        "next_test_at": row.get("next_test_at") if row["status"] in ("disabled", "given_up") else None,
        "tests_failed": int(row.get("tests_failed") or 0),
        "last_test_at": row.get("last_test_at"),
        "last_test_result": row.get("last_test_result"),
        "last_test_label": servertest.label(row.get("last_test_result")),
        "test_supported": row.get("last_test_result") not in ("unsupported", "locked"),
        "recovered_at": row.get("recovered_at"),
        "waiting_for_use": row["status"] == "probing" and row.get("last_result") == NOT_USED_YET,
    }


def overview() -> dict[str, Any]:
    return {
        "autopilot": state.autopilot(),
        "auto_disable_after_h": int(_h("usenet.auto_disable_after_h", 6)),
        "probe_interval_h": int(_h("usenet.auto_probe_interval_h", 24)),
        "test_interval_h": int(_h("usenet.server_test_interval_h", 6)),
        "slow_test_interval_h": int(_h("usenet.server_test_slow_interval_h", 24)),
        "servers": {s: public_row(r) for s, r in rows().items()},
    }


def run() -> dict[str, Any]:
    """Autopilot pass after every SABnzbd server-status refresh: server tests and off/on, then retry failed
    downloads."""
    from mso.features.usenet import retry

    out: dict[str, Any] = {"at": _now().isoformat(timespec="seconds")}
    with SabClient.from_config() as client:
        try:
            out["servers"] = tick(client)
        except Exception as exc:
            LOGGER.warning("autopilot servers: %s", type(exc).__name__)
            out["servers"] = {"error": str(getattr(exc, "detail", exc))[:300]}
        try:  # an open 'wisselende resultaten' advice resolves even when the server is not tested again
            servertest.sync_open_flapping()
        except Exception as exc:  # pragma: no cover - advice only
            LOGGER.info("flapping-advies overgeslagen: %s", type(exc).__name__)
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
