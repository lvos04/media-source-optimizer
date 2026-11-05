# Release blokkeren in Sonarr/Radarr bij malware-opruimen (MSO 1.4.0)

## Bevinding (MediaGuard `main`, commit `aec9e64`, 5 nov 2026, alleen gelezen)

MediaGuard heeft al een geschikte route:

| Route | Rol | Werking |
|---|---|---|
| `GET /api/v1/instances` | viewer | lijst Sonarr/Radarr-instanties (`id`, `name`, `archived`) |
| `GET /api/v1/instances/{id}/queue?page=&page_size=` | viewer | wachtrij van de *arr (records met `id` en `downloadId` = infohash in hoofdletters) |
| `DELETE /api/v1/instances/{id}/queue/{queue_id}?remove_from_client=false&blocklist=true` | operator | roept in de fork `DELETE /api/v3/queue/{id}?removeFromClient=false&blocklist=true` aan: de release komt op de blocklist, de download blijft staan, MediaGuard schrijft een auditregel (`queue.remove`) |

## Wat MSO doet

Het opruimvoorstel ("Alle N verdachte torrents opruimen") heeft per torrent, in deze volgorde:

1. **Blokkeren in Sonarr/Radarr via MediaGuard** (`arrblock:…`), alleen als de torrent nog in een wachtrij staat:
   MSO zoekt het wachtrij-item op `downloadId` en roept de DELETE-route hierboven aan met
   `remove_from_client=false&blocklist=true`. Controle achteraf: het item staat niet meer in de wachtrij.
2. **MSO-blacklist** (`blacklist:…`, altijd): de infohash (`btih:`/`btmh:`) komt ~permanent op de blacklist van
   *Ontbrekend & herstel*, zodat MSO deze release nooit meer zoekt of verstuurt.
3. **Verwijderen mét bestanden** in qBittorrent (`torrents/delete … deleteFiles=true`).

Blokkeren gebeurt dus vóór verwijderen; anders verdwijnt het wachtrij-item en kan de *arr de release niet meer
op de blocklist zetten.

## Wat nog nodig is (alleen aan MSO-kant)

`mso/connectors/mediaguard.py` laat bewust alleen login en "zoek opnieuw" toe (`ALLOWED_WRITES`). Zolang de
blokkeerroute daar niet in staat, laat MSO stap 1 weg en zegt het voorbeeld dat expliciet ("Sonarr/Radarr-blocklist
niet mogelijk: … staat de blokkeerroute nog niet toe"). Toe te voegen regel:

```python
("DELETE", re.compile(r"^/api/v1/instances/[A-Za-z0-9_.-]+/queue/\d+$")),
```

De MediaGuard-koppeling heeft daarvoor rol **operator** nodig (API-sleutel met rol operator volstaat).
In `docs/MEDIAGUARD_API_KEYS.md` staat onder "Wat MSO aanroept" dan ook deze route.

## Optionele uitbreiding in MediaGuard (niet nodig voor 1.4.0)

Staat een nep-release niet meer in de wachtrij (bijv. al door de *arr afgehandeld), dan kan MSO hem nu niet in
Sonarr/Radarr blokkeren. Een route die dat wel kan zou helpen:

> **Opdracht voor MediaGuard Ultimate:** voeg `POST /api/v1/instances/{id}/blocklist/release` toe (rol operator,
> audit `blocklist.add`). Payload `{"download_id": "<infohash>", "reason": "<tekst>"}`. Werking: zoek in de
> historie van de instantie (`GET /api/v3/history?downloadId=…`) het grab-record en roep
> `POST /api/v3/history/failed/{historyId}` aan (markeert als mislukt → blocklist, zoekt eventueel opnieuw volgens
> de instellingen van de *arr). Antwoord `{"blocked": true, "history_id": …}`; `404` met Nederlandse `detail` als
> er geen grab-record is. Nooit bestanden verwijderen (`removeFromClient` blijft uit). Voeg tests toe voor: gevonden,
> niet gevonden, viewer-rol geweigerd, audit zonder geheimen.
