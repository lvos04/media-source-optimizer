"""SABnzbd's own server test (1.6.0): "kan SABnzbd nu inloggen bij deze provider?" without touching the download path.

:meth:`SabClient.test_server` asks SABnzbd to connect and log in with the credentials it has stored itself (the same
call as the 'Test server' button in SABnzbd). MSO never sees a password and keeps no username. A test changes nothing
in SABnzbd, so it is allowed in every operating mode (read-only included).

Results (:data:`serverstatus.TEST_LABELS`): ``ok``, an account problem (``auth_failed``, ``account_expired``,
``access_denied``, ``block_exhausted``), ``rate_limited`` ("te veel verbindingen": the account is alive, never counted
as a failed test), ``unreachable``, ``tls_error``, ``unknown`` and ``unsupported`` (SABnzbd does not offer the call or
its configuration is locked → Autopilot falls back to the 1.5.0 probe).

Every test is stored in ``usenet_server_tests`` (bounded). :func:`flapping` recognises a server whose results keep
changing (the shared-account / connection-limit pattern) so MSO advises instead of switching it on and off.

Endpoints: ``POST /api/v1/usenet/sab/servers/{server}/test`` (operator, any mode) and
``GET /api/v1/usenet/sab/server-tests`` (viewer).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from mso import settings_registry as sr
from mso.audit import audit
from mso.auth import Principal, require
from mso.connectors.sabnzbd import SabClient, SabError
from mso.db import get_db
from mso.domain import outcomes
from mso.features.usenet import serverstatus
from mso.security import limiter, redact

LOGGER = logging.getLogger(__name__)
ACTOR = "autopilot:usenet"
KEEP_DAYS = 30
KEEP_PER_SERVER = 100
FLAP_WINDOW = timedelta(days=7)
# results that say "SABnzbd can log in" (rate_limited: logged in, but the account has too many connections)
ALIVE = {"ok", "rate_limited"}
ACCOUNT_PROBLEMS = set(serverstatus.DISABLE_SUGGESTED)
# a refusal after a passing test = flapping; an expired account or used-up block after a passing test is a real change
FLAP_RESULTS = {"auth_failed", "access_denied", "rate_limited"}

router = APIRouter(prefix="/api/v1/usenet", tags=["usenet"])


def _now() -> datetime:
    return datetime.now(UTC)


def label(result: str | None) -> str | None:
    return serverstatus.TEST_LABELS.get(result or "") if result else None


def public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "result": row["result"],
        "label": label(row["result"]),
        "ok": row["result"] == "ok",
        "message": row.get("message"),
        "trigger": row.get("trigger"),
        "at": row["at"],
    }


# ------------------------------------------------------------------ run + store
def run(client: SabClient, server: str, *, trigger: str, actor: str | None = None) -> dict[str, Any]:
    """Run SABnzbd's test for ``server``, store and return it (``public`` shape + ``supported``). SABnzbd being
    unreachable raises :class:`SabError` (no test row: nothing about the provider was learned)."""
    raw = client.test_server(server)
    result = serverstatus.classify_test(raw)
    message = redact(str(raw.get("message") or ""))[:300] or None
    db = get_db()
    prev = latest(server)
    row = db.fetchone(
        "INSERT INTO usenet_server_tests(server,result,message,trigger) VALUES (%s,%s,%s,%s) RETURNING *",
        (server, result, message, trigger),
    )
    db.execute(
        "UPDATE usenet_server_autoheal SET last_test_at=%s, last_test_result=%s WHERE server=%s",
        (row["at"], result, server),
    )
    _prune(server)
    outcomes.observe("sabnzbd", "sab.server_test_ok", 1.0 if result in ALIVE else 0.0,
                     {"server": server, "result": result, "trigger": trigger})
    if actor:
        audit(actor, "usenet.server_test", f"SABnzbd-servertest {server}: {label(result)}", target="sabnzbd",
              detail={"server": server, "result": result, "message": message})
    elif prev is None or prev["result"] != result:
        audit(ACTOR, "usenet.server_test", f"SABnzbd-servertest {server}: {label(result)}", target="sabnzbd",
              detail={"server": server, "result": result, "previous": prev["result"] if prev else None,
                      "trigger": trigger})
    out = public(row) or {}
    out["supported"] = result not in ("unsupported", "locked")
    _sync_flapping(server)
    _sync_locked(result)
    return out


LOCKED_KEY = "usenet.sab_locked"


def _sync_locked(result: str) -> None:
    """SABnzbd's configuration lock: one calm 'Wacht op jou' item (MSO can neither test nor switch servers; SABnzbd
    refuses set_config as well), resolved by the first test that is no longer refused."""
    db = get_db()
    if result != "locked":
        db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
                   "AND status='open'", (LOCKED_KEY,))
        return
    existing = db.fetchone("SELECT status FROM recommendations WHERE dedupe_key=%s", (LOCKED_KEY,))
    if existing and existing["status"] in ("open", "dismissed", "accepted"):
        return  # told once (or the owner dismissed it): stay calm
    outcomes.recommend(
        "usenet", "SABnzbd-configuratie is vergrendeld",
        "SABnzbd staat op 'configuratie vergrendeld' (config_lock). Dan weigert SABnzbd ook de servertest en elke "
        "wijziging via de API, dus MSO kan kapotte servers niet testen, uitschakelen of weer aanzetten. MSO probeert "
        "het daarom niet (geen mislukte automatische wijzigingen) en kijkt dagelijks opnieuw. Wil je dat MSO dit "
        "doet, zet de vergrendeling in SABnzbd dan uit; anders hoeft er niets te gebeuren.",
        risk="toestemming_nodig", subject="sabnzbd", impact="Kapotte servers blijven aan tot je zelf ingrijpt",
        dedupe_key=LOCKED_KEY, evidence={"source": "sabnzbd", "result": "locked"}, db=db)
    db.execute("UPDATE recommendations SET status='open', updated_at=now() WHERE dedupe_key=%s AND status='resolved'",
               (LOCKED_KEY,))


def _prune(server: str) -> None:
    db = get_db()
    db.execute("DELETE FROM usenet_server_tests WHERE at < now() - make_interval(days => %s)", (KEEP_DAYS,))
    db.execute(
        "DELETE FROM usenet_server_tests WHERE server=%s AND id NOT IN (SELECT id FROM usenet_server_tests "
        "WHERE server=%s ORDER BY at DESC, id DESC LIMIT %s)",
        (server, server, KEEP_PER_SERVER),
    )


# ------------------------------------------------------------------ reads
def latest(server: str) -> dict[str, Any] | None:
    return get_db().fetchone(
        "SELECT * FROM usenet_server_tests WHERE server=%s ORDER BY at DESC, id DESC LIMIT 1", (server,)
    )


def latest_all() -> dict[str, dict[str, Any]]:
    return {
        r["server"]: dict(r)
        for r in get_db().fetchall(
            "SELECT DISTINCT ON (server) * FROM usenet_server_tests ORDER BY server, at DESC, id DESC"
        )
    }


def history(server: str, limit: int = 10) -> list[dict[str, Any]]:
    return get_db().fetchall(
        "SELECT * FROM usenet_server_tests WHERE server=%s ORDER BY at DESC, id DESC LIMIT %s", (server, limit)
    )


def flapping(server: str, *, now: datetime | None = None) -> bool:
    """The account keeps changing between "werkt" and "geweigerd" (C13: shared account or connection limit): within
    7 days a passing test FOLLOWED by a refusal or "too many connections" — in a later test, or reported by SABnzbd
    itself more than 30 minutes after the passing test (an old warning never counts). Broken → repaired (only
    failures, then passes) is not flapping."""
    now = now or _now()
    rows = get_db().fetchall(
        "SELECT result, at FROM usenet_server_tests WHERE server=%s AND at > %s AND result <> 'unsupported'",
        (server, now - FLAP_WINDOW),
    )
    oks = [r["at"] for r in rows if r["result"] == "ok"]
    if not oks:
        return False
    first_ok = min(oks)
    if any(r["result"] in FLAP_RESULTS and r["at"] > first_ok for r in rows):
        return True
    return bool(
        get_db().scalar(
            "SELECT count(*) FROM usenet_sab_server_events WHERE server=%s AND status = ANY(%s) AND last_at > %s",
            (server, ["access_denied", "auth_failed", "rate_limited"], first_ok + timedelta(minutes=30)),
        )
    )


def sync_open_flapping() -> list[str]:
    """Re-check every server with an open 'wisselende resultaten' advice (cheap, no SABnzbd call): a healthy server is
    rarely tested again, so without this the advice would never resolve. Returns the servers still flapping."""
    still = []
    for r in get_db().fetchall(
        "SELECT dedupe_key FROM recommendations WHERE status='open' AND dedupe_key LIKE 'usenet.server\\_flapping:%%'"
    ):
        server = r["dedupe_key"].split(":", 1)[1]
        _sync_flapping(server)
        if flapping(server):
            still.append(server)
    return still


def _sync_flapping(server: str) -> None:
    """One calm advice per flapping server; resolved by itself when the pattern is gone for 7 days."""
    key = f"usenet.server_flapping:{server}"
    db = get_db()
    if not flapping(server):
        db.execute("UPDATE recommendations SET status='resolved', updated_at=now() WHERE dedupe_key=%s "
                   "AND status='open'", (key,))
        return
    st = serverstatus.current().get(server) or {"server": server, "displayname": server}
    name = serverstatus.display_name(
        {"server": server, "displayname": st.get("displayname") or server, "short": st.get("short")}
    )
    outcomes.recommend(
        "usenet",
        f"Wisselende resultaten bij {name}: waarschijnlijk te veel verbindingen of een gedeeld account",
        f"SABnzbd's eigen servertest voor {name} slaagt soms en wordt soms geweigerd. Dat past niet bij een "
        "verkeerd wachtwoord of een verlopen account, maar wel bij een account dat op meer plekken tegelijk "
        "gebruikt wordt of bij meer verbindingen dan het abonnement toestaat. Verlaag het aantal verbindingen "
        "voor deze server in SABnzbd, of controleer of iemand anders het account gebruikt. MSO laat de server aan "
        "zolang SABnzbd's eigen test slaagt en zet hem alleen uit als de test zelf geweigerd wordt. Bron: SABnzbd.",
        risk="advies",
        subject=f"sabnzbd:{server}",
        impact="Af en toe geweigerde verbindingen; downloads kunnen trager zijn",
        dedupe_key=key,
        evidence={"server": server, "tests": [
            {"result": r["result"], "at": r["at"].isoformat()} for r in history(server, 10)
        ], "source": "sabnzbd"},
    )


# ------------------------------------------------------------------ API
def _limit(bucket: str, actor: str, n: int) -> None:
    ok, wait = limiter.hit(bucket, actor, n, 60)
    if not ok:
        raise HTTPException(429, detail=f"Te veel verzoeken; wacht {int(wait) + 1} s")


@router.post("/sab/servers/{server}/test")
def test_now(server: str, p: Principal = Depends(require("operator"))) -> dict[str, Any]:
    """'Testen': SABnzbd's own server test now (read-only, every mode, no MSO credentials needed). For a server MSO
    switched off, a passing test lets Autopilot switch it back on right away (otherwise a proposal is made)."""
    from mso.features.usenet import autoheal  # lazy: autoheal imports this module

    _limit("usenet-server-test", p.actor, 10)
    try:
        with SabClient.from_config() as client:
            test = run(client, server, trigger="user", actor=p.actor)
            follow = autoheal.after_user_test(client, server, test)
    except SabError as exc:
        if exc.code == "not_found":  # only the unknown server; a broken SABnzbd answer ('invalid') is a 502
            raise HTTPException(404, detail="SABnzbd-server niet gevonden") from exc
        raise HTTPException(502, detail=str(exc)) from exc
    return {"server": server, "test": test, **follow}


@router.get("/sab/server-tests")
def tests_overview(_: Principal = Depends(require("viewer"))) -> dict[str, Any]:
    """Latest SABnzbd server test per server + the last 10, and whether the results keep changing (no SABnzbd call)."""
    items = {}
    for server, row in sorted(latest_all().items()):
        items[server] = {
            "latest": public(row),
            "history": [public(r) for r in history(server, 10)],
            "flapping": flapping(server),
        }
    return {
        "items": items,
        "interval_h": int(sr.get("usenet.server_test_interval_h")),
        "slow_interval_h": int(sr.get("usenet.server_test_slow_interval_h")),
    }
