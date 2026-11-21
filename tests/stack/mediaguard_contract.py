"""Toets de MSO-client tegen de gepinde MediaGuard-core met een nep-Radarr."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import httpx
from gen_mocks import mock_services
from mso.connectors import mediaguard as mg
from mso.connectors.base import ConnectorConfig
from msomocks.radarr import CLIENT, MOVIE, QUEUE_ITEM

VERSION = "1.2.2"
IMAGE = (
    f"ghcr.io/lvos04/mediaguard-core:{VERSION}"
    "@sha256:a8daf269e93285cf71b6e9a18e2f75f7b52fa07da230c679dd71b14bc85bbc2b"
)
INSTANCE = "fixture-films"
API = "/api/v1"
ROOT = Path(__file__).resolve().parents[2]


def stack(fixture_image: str | None = None) -> dict:
    services = mock_services(IMAGE, ("radarr",))
    radarr = services["mock-radarr"]
    radarr.update(
        entrypoint=["python", "-m", "msomocks"],
        environment={"PYTHONPATH": "/fixture", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if fixture_image:
        # Een afzonderlijke buildengine ontvangt de bron via de bouwcontext van de job.
        radarr.update(image=fixture_image, build={
            "context": str(ROOT / "apps" / "mocks"),
            "dockerfile_inline": f"FROM {IMAGE}\nCOPY msomocks /fixture/msomocks\n",
        })
    else:
        radarr["volumes"] = [f"{ROOT / 'apps' / 'mocks'}:/fixture:ro"]
    services["mediaguard"] = {
        "image": IMAGE,
        "environment": {"MG_START_BACKGROUND": "0", "MG_DEV_AUTH_BYPASS": "0"},
        "tmpfs": ["/config:uid=568,gid=568,mode=0700", "/work:uid=568,gid=568,mode=0700"],
        "networks": ["app"],
    }
    return {"services": services, "networks": {"app": {"internal": True}}}


def response_json(response: httpx.Response, status: int = 200):
    assert response.status_code == status, (
        f"Contractafwijking: {response.request.method} {response.request.url.path} "
        f"geeft HTTP {response.status_code}; verwacht HTTP {status}."
    )
    return response.json() if response.content else None


def wait_for_health(client: httpx.Client) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            health = client.get(f"{API}/health")
        except httpx.TransportError:
            time.sleep(1)
            continue
        body = response_json(health)
        assert body["status"] == "ok" and body["version"] == VERSION
        print(f"MediaGuard-core {VERSION} gestart met een lege database.", flush=True)
        return
    raise RuntimeError("De MediaGuard-wegwerpcontainer werd niet bereikbaar.")


def seed(admin: httpx.Client) -> dict[str, str]:
    setup = response_json(admin.get(f"{API}/auth/setup-status"))
    assert setup["needs_setup"] is True, "De contractproef vereist een lege database."
    credentials = {"username": "contract-beheer", "password": token_urlsafe(32)}
    response_json(admin.post(f"{API}/auth/setup", json=credentials))
    login = response_json(admin.post(f"{API}/auth/login", json=credentials))
    assert login["role"] == "admin" and login["csrf_token"] and "mg_session" in admin.cookies
    admin.headers["X-CSRF-Token"] = login["csrf_token"]
    keys = {}
    for role in ("viewer", "operator"):
        body = response_json(admin.post(f"{API}/auth/api-keys", json={"name": f"contract-{role}", "role": role}))
        assert body["role"] == role and body["key"]
        keys[role] = body["key"]
    response_json(admin.post(f"{API}/instances", json={
        "id": INSTANCE,
        "app": "radarr",
        "media_type": "movie",
        "name": "Contractfilms",
        "url": "http://mock-radarr:8080",
        "api_key": token_urlsafe(32),
        "download_category": INSTANCE,
    }), 201)
    synced = response_json(admin.post(f"{API}/instances/{INSTANCE}/sync", params={"mode": "inline"}))
    assert synced["status"] == "succeeded" and synced["movies"] == 1
    page = response_json(admin.get(f"{API}/wanted", params={"instance_id": INSTANCE}))
    assert len(page["items"]) == 1, "De nep-Radarr moet precies één ontbrekende film opleveren."
    path = f"{API}/wanted/{page['items'][0]['id']}/diagnosis"
    assert response_json(admin.get(path, params={"cached": "true"}), 404)["detail"] == "Nog geen diagnose opgeslagen"
    response_json(admin.get(path, params={"interactive": "false"}))  # Maak de cachefixture via MediaGuard zelf.
    return keys


def read_routes(client: mg.MediaGuardClient, role: str) -> int:
    def get(path: str, params: dict | None = None):
        response = client.request("GET", API + path, params=params)
        return response_json(response)

    me = get("/auth/me")
    assert me["role"] == role and me["csrf_token"] is None
    assert get("/status")["emergency_stop"]["active"] is False
    page = get("/wanted", {"status": "open", "page": 1, "page_size": 1, "sort": "priority"})
    assert page["total"] == 1 and len(page["items"]) == 1
    item = page["items"][0]
    assert type(item["id"]) is int
    assert (item["instance_id"], item["media_type"], item["kind"], item["reason"]) == (
        INSTANCE, "movie", "movie", "missing"
    )
    assert item["title"] == f"{MOVIE['title']} ({MOVIE['year']})" and item["item_provider_id"] == MOVIE["id"]
    assert item["status"] == "wanted" and item["attempts"] == 0
    assert {"item_title", "provider_id", "season_number", "episode_number", "air_date_utc",
            "last_searched_at", "last_reason_code"} <= item.keys()
    mid = item["id"]
    detail = get(f"/wanted/{mid}")
    diagnosis = get(f"/wanted/{mid}/diagnosis", {"cached": "true"})
    assert detail["id"] == mid and detail["latest_diagnosis"] == diagnosis
    assert diagnosis["wanted_id"] == mid and diagnosis["primary_code"] == item["last_reason_code"]
    assert diagnosis["reasons"] and all(
        {"label_nl", "message_nl", "suggested_fix_nl", "ts"} <= reason.keys() for reason in diagnosis["reasons"]
    )
    assert get("/wanted/kpis")["missing"] == 1
    reasons = get("/wanted/reasons")
    assert reasons and all({"label_nl", "fix_nl"} <= r.keys() for r in reasons.values())
    quota = get("/wanted/backfill/quota")[INSTANCE]
    assert quota["remaining"] > 0 and {"hour_used", "hour_max", "day_used", "day_max"} <= quota.keys()
    policy = get("/policy/effective", {"instance_id": INSTANCE, "media_type": "movie", "movie_id": MOVIE["id"]})
    assert isinstance(policy["config"], dict) and policy["profile_id"]
    queue = get(f"/instances/{INSTANCE}/queue")["records"]
    assert [(row["id"], row["downloadId"]) for row in queue] == [(QUEUE_ITEM["id"], QUEUE_ITEM["downloadId"])]
    instance = next(row for row in client.instances() if row["id"] == INSTANCE)
    assert (instance["app"], instance["media_type"], instance["download_category"]) == ("radarr", "movie", INSTANCE)
    rows = client.download_clients(INSTANCE)
    assert [(row["id"], row["name"], row["category"]) for row in rows] == [(CLIENT["id"], CLIENT["name"], INSTANCE)]
    assert client.instance_health(INSTANCE) == [{"source": "Fixture", "type": "warning", "message": "Testmelding"}]
    categories = client.provisioning_categories()
    assert categories["default_categories"][INSTANCE] == INSTANCE
    probe = mg.probe(client.cfg)
    assert probe["role"] == role and probe["version"] == VERSION and mg.result_from_probe(probe).ok
    assert probe["capability_source"] == "probe"
    assert probe["capabilities"]["wanted_search"] is None and probe["capabilities"]["openapi"] is None
    assert probe["capabilities"]["instance_queue_block"] is None
    return mid


def verify(url: str) -> ConnectorConfig:
    with httpx.Client(base_url=url, timeout=10, trust_env=False) as admin:
        wait_for_health(admin)
        keys = seed(admin)
        assert response_json(admin.get(f"{API}/openapi.json"))["paths"]
    clients = {
        role: mg.MediaGuardClient(ConnectorConfig(mg.CID, url, {"api_key": key}, {"auth_method": "apikey"}))
        for role, key in keys.items()
    }
    viewer, operator = clients["viewer"], clients["operator"]
    response_json(viewer.request("GET", f"{API}/openapi.json", auth=False), 401)
    for role, client in clients.items():
        response_json(client.request("GET", f"{API}/openapi.json"), 403)
        mid = read_routes(client, role)
    try:
        viewer.request_search(mid)
    except mg.MediaGuardError as exc:
        assert (exc.code, exc.status, str(exc)) == ("forbidden", 403, "MediaGuard-rol heeft onvoldoende rechten")
    else:
        raise AssertionError("MediaGuard liet een kijkerssleutel een zoekopdracht starten.")
    assert mg.result_from_probe(mg.probe(viewer.cfg)).status == "healthy"
    response_json(operator.request("GET", f"{API}/wanted/0"), 404)
    response_json(operator.request("GET", f"{API}/wanted", auth=False), 401)
    # Diepgaande contractproef op het testitem. De capabilityprobe doet nooit een zoekaanroep.
    search = operator.request_search(mid)
    assert search["issued"] == 1 and search["issued_wanted_ids"] == [mid] and search["item"]["id"] == mid
    assert operator.get(f"{API}/wanted/backfill/quota")[INSTANCE]["hour_used"] == 1
    for method, path in (
        ("POST", f"{API}/wanted/{mid}/retry-relaxed"),
        ("DELETE", f"{API}/instances/{INSTANCE}/queue/{QUEUE_ITEM['id']}"),
    ):
        try:
            operator.request(method, path)
        except mg.WriteRefused:
            pass
        else:
            raise AssertionError(f"MSO liet een verboden verzoek toe: {method} {path}.")
    queue_path = f"{API}/instances/{INSTANCE}/queue"
    block_path = f"{queue_path}/{QUEUE_ITEM['id']}"
    block_params = {"blocklist": "true", "remove_from_client": "false"}
    response_json(viewer.request("DELETE", block_path, params=block_params), 403)
    assert response_json(operator.request("DELETE", block_path, params=block_params))["removed"] is True
    assert operator.get(queue_path)["records"] == []
    return operator.cfg


def main() -> int:
    try:
        pulled = subprocess.run(["docker", "pull", IMAGE], check=False, timeout=180).returncode == 0
    except subprocess.TimeoutExpired:
        pulled = False
    if not pulled:
        prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else ""
        print(f"{prefix}OVERGESLAGEN: MediaGuard-core {VERSION} is niet op te halen (T-01).", flush=True)
        return 77
    # De MSO-client gebruikt de eigen Docker-stack ook als er op de host een HTTP-proxy is ingesteld.
    os.environ.update(NO_PROXY="*", no_proxy="*")
    with tempfile.TemporaryDirectory(prefix="mso-mediaguard-", dir=os.environ.get("TMPDIR") or ROOT) as directory:
        compose_file = Path(directory) / "compose.json"
        fixture_image = os.environ.get("MSO_CONTRACT_FIXTURE_IMAGE")
        compose_file.write_text(json.dumps(stack(fixture_image)), encoding="utf-8")
        # CI geeft een vaste projectnaam per job mee, zodat de afsluitende stap ook na afbreken de stack opruimt.
        project = os.environ.get("MSO_CONTRACT_PROJECT") or f"mso-mg-contract-{uuid4().hex[:10]}"
        compose = ["docker", "compose", "-f", str(compose_file), "-p", project]
        try:
            if fixture_image:
                subprocess.run([*compose, "build", "mock-radarr"], check=True, timeout=180)
            subprocess.run([*compose, "up", "-d", "--pull", "never"], check=True, timeout=90)
            container = subprocess.check_output([*compose, "ps", "-q", "mediaguard"], text=True, timeout=15).strip()
            address = subprocess.check_output([
                "docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container
            ], text=True, timeout=15).strip()
            cfg = verify(f"http://{address}:8100")
            subprocess.run([*compose, "stop", "mediaguard"], check=True, timeout=30)
            result = mg.result_from_probe(mg.probe(cfg))
            assert not result.ok and result.status == "offline" and "niet bereikbaar" in result.message
            try:
                mg.MediaGuardClient(cfg).get(f"{API}/wanted")
            except mg.MediaGuardError as exc:
                assert exc.code == "offline" and "niet bereikbaar" in str(exc)
            else:
                raise AssertionError("MSO behandelde de onbereikbare wanted-route als een leesresultaat.")
        finally:
            subprocess.run([*compose, "down", "--volumes"], check=True, timeout=60)
            if fixture_image:
                subprocess.run(["docker", "image", "rm", fixture_image], check=False, timeout=60)
    print(f"MediaGuard-contractproef {VERSION}: leesroutes, rollen en degradatie bevestigd.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
