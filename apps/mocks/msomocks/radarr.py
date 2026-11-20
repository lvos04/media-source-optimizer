"""Kleine Radarr-fixture voor de MediaGuard-contractproef."""

from __future__ import annotations

from fastapi import FastAPI, Query, Response

MOVIE = {
    "id": 41,
    "title": "Contractfilm",
    "year": 2000,
    "tmdbId": 41,
    "monitored": True,
    "isAvailable": True,
    "minimumAvailability": "released",
    "status": "released",
    "digitalRelease": "2000-01-01T00:00:00Z",
    "hasFile": False,
}
QUEUE_ITEM = {"id": 71, "movieId": 42, "downloadId": "fixture-download", "title": "Andere contractfilm"}
CLIENT = {
    "id": 51,
    "name": "Contractclient",
    "implementation": "Sabnzbd",
    "protocol": "usenet",
    "enable": True,
    "priority": 1,
    "fields": [{"name": "movieCategory", "value": "fixture-films"}],
}


def create_app() -> FastAPI:
    app = FastAPI()
    queue = [QUEUE_ITEM]
    app.get("/api/v3/system/status")(lambda: {"appName": "Radarr", "version": "5.0.0"})
    app.get("/api/v3/movie")(lambda: [MOVIE])
    app.get(f"/api/v3/movie/{MOVIE['id']}")(lambda: MOVIE)
    app.get("/api/v3/downloadclient")(lambda: [CLIENT])
    app.get("/api/v3/health")(lambda: [{"source": "Fixture", "type": "warning", "message": "Testmelding"}])
    app.get("/api/v3/command/61")(lambda: {"id": 61, "status": "completed"})
    app.get("/api/v3/history/movie")(lambda: [])
    for route in (
        "rootfolder", "qualityprofile", "customformat", "indexer", "indexerstatus", "tag", "remotepathmapping",
        "release",
    ):
        app.get(f"/api/v3/{route}")(lambda: [])

    @app.get("/api/v3/queue")
    def get_queue() -> dict:
        return {"page": 1, "pageSize": 50, "totalRecords": len(queue), "records": queue}

    @app.post("/api/v3/command")
    def command(body: dict) -> dict:
        assert body["name"] == "MoviesSearch" and body["movieIds"] == [MOVIE["id"]]
        return {"id": 61, "status": "completed"}

    @app.delete(f"/api/v3/queue/{QUEUE_ITEM['id']}")
    def blocklist(blocklist: bool, remove_from_client: bool = Query(alias="removeFromClient")) -> Response:
        assert remove_from_client is False and blocklist is True
        queue.clear()
        return Response(status_code=200)

    return app
