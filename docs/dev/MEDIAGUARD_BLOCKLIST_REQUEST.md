# Release blokkeren in Sonarr/Radarr bij malware-opruimen

## Bevinding 1.5.0 (MediaGuard `main`, commit `9bed266`, 5 nov 2026, alleen gelezen)

Testrapport ronde 4, P1: het opruimvoorstel blokkeerde in Sonarr/Radarr 0 van de 41 nep-releases, omdat MSO alleen
items kan blokkeren die nog in de *arr-wachtrij staan, en deze 41 waren al afgerond.

MediaGuard heeft **geen** route die een afgerond grab-record als mislukt markeert of een release op de blocklist zet:

| Route in MediaGuard | Bruikbaar? |
|---|---|
| `GET /api/v1/instances/{id}/history?page=&page_size=&event_type=&episode_id=&series_id=&movie_id=` (viewer) | alleen lezen; **geen** filter op `downloadId` |
| `DELETE /api/v1/instances/{id}/queue/{queue_id}?remove_from_client=false&blocklist=true` (operator) | ja, maar alleen zolang het item in de wachtrij staat (MSO gebruikt dit al) |
| `POST history/failed/{historyId}` naar Sonarr/Radarr | wordt **alleen intern** aangeroepen (`downloads/failures.py`), niet als API-route aangeboden |
| `POST /api/v1/instances/{id}/commands` | alleen een vaste lijst UI-commando's; geen blocklist |

Gevolg in MSO 1.5.0:

- Voor releases die nog in de wachtrij staan blijft de bestaande blokkade via MediaGuard werken.
- Voor afgeronde releases zegt het voorbeeld van het opruimvoorstel (kopje *Blokkeren*) nu letterlijk:
  "Sonarr kan deze releases opnieuw grijpen; de preventie-instelling 'Uitvoerbare bestanden nooit downloaden'
  voorkomt dat de bestanden opnieuw binnenkomen" — met erbij of die instelling aan staat.
- De preventie-instelling (qBittorrent ≥ 4.6, `excluded_file_names`) wordt in Autopilot automatisch aangezet; daarmee
  komt het `.exe`/`.scr`-bestand van een opnieuw gegrepen nep-release nooit op schijf. MSO's eigen blacklist voorkomt
  daarnaast dat MSO de release zelf ooit nog zoekt of verstuurt.

Zodra MediaGuard de route hieronder heeft, voegt MSO één strikt begrensde regel toe aan `ALLOWED_WRITES`
(`mso/connectors/mediaguard.py`), een rij in `features/mediaguard/compat.py`, en een `arrblock`-variant voor
afgeronde releases in `features/torrent/cleanup.py`.

## Opdracht voor de MediaGuard-sessie (klaar om te plakken)

> **Opdracht voor MediaGuard Ultimate — "afgeronde release als mislukt markeren (blocklist)"**
>
> Context: Media Source Optimizer (MSO) ruimt nep-releases met malware op (bijv. `Serie S01E01 1080p WEB-GRP.exe`).
> Die zijn door Sonarr/Radarr al gegrepen en afgerond, dus ze staan niet meer in de wachtrij en de bestaande route
> `DELETE /api/v1/instances/{id}/queue/{queue_id}?blocklist=true` helpt niet. Sonarr grijpt ze daardoor opnieuw.
>
> Voeg in `apps/core/mediaguard/instances/manage.py` (router `/instances/{instance_id}`) deze route toe:
>
> `POST /api/v1/instances/{instance_id}/history/failed`
>
> - **Rol:** `operator` (`Depends(require_operator)`); `viewer` krijgt 403. Werkt met API-sleutels (`X-Api-Key`,
>   rol operator) en met sessie + CSRF zoals de andere operator-routes. `require_instance(ctx, instance_id,
>   mutable=True)` (gearchiveerde instantie → zelfde fout als bij `queue_remove`).
> - **Body (Pydantic, extra velden verboden):** `{"download_id": str, "reason": str}`.
>   `download_id`: infohash, 40 of 64 hex-tekens (hoofd-/kleine letters toegestaan, vergelijk hoofdletterongevoelig),
>   of een SABnzbd `nzo_id` (`^SABnzbd_nzo_[A-Za-z0-9_]+$`); anders 422. `reason`: max. 300 tekens.
> - **Werking:**
>   1. Zoek in de historie van de instantie het **grab**-record (`eventType` = `grabbed`) met dat `downloadId`
>      (Sonarr/Radarr: `GET /api/v3/history?downloadId=<id>&eventType=1`, of pagineer `history` en filter;
>      hoofdletterongevoelig). Meerdere grabs → de nieuwste.
>   2. Geen grab-record → `404` met Nederlandse `detail`: "Geen grab-record met dit downloadId in deze instantie".
>   3. Anders `POST /api/v3/history/failed/{historyId}` (markeert als mislukt → release op de blocklist; de *arr
>      zoekt daarna opnieuw volgens zijn eigen instellingen). Raak nooit bestanden aan; geen `removeFromClient`.
>   4. Idempotent: is er voor dit `downloadId` al een `downloadFailed`-record, antwoord `200` met
>      `{"blocked": true, "history_id": …, "already": true}` zonder opnieuw te posten.
> - **Antwoord:** `200 {"blocked": true, "history_id": <int>, "already": false, "title": "<releasetitel>"}`.
>   Fouten van de *arr via `arr_call` (502 met Nederlandse `detail`).
> - **Audit:** `_audit(ctx, principal, "history.failed", instance_id, "history", history_id,
>   new={"download_id": <id>, "reason": <reason>})` — geen geheimen (geen API-sleutels, geen URL's met passkeys).
> - **Noodstop:** bij actieve noodstop `409` ("MediaGuard-noodstop is actief"), net als andere schrijvende acties.
> - **OpenAPI:** de route moet in `/api/v1/openapi.json` staan (MSO detecteert routes daarmee).
> - **Docs:** voeg de route toe aan `docs/MEDIAGUARD_API_KEYS.md` onder "Wat MSO aanroept".
> - **Tests** (`apps/core/tests/`, met de nep-*arr):
>   1. grab gevonden → `history/failed/{id}` precies één keer aangeroepen, antwoord `blocked: true`, auditregel
>      `history.failed` zonder geheimen;
>   2. tweede aanroep → `already: true`, geen tweede POST;
>   3. geen grab-record → 404 met Nederlandse detail, niets gepost;
>   4. rol viewer (sessie én API-sleutel) → 403, niets gepost;
>   5. ongeldig `download_id` → 422;
>   6. gearchiveerde instantie / noodstop → weigering, niets gepost.

## Wat MSO daarna doet (1.5.x, na de MediaGuard-route)

- `mso/connectors/mediaguard.py` `ALLOWED_WRITES`: alleen
  `("POST", re.compile(r"^/api/v1/instances/[A-Za-z0-9_.-]+/history/failed$"))`, met in `request()` een body-controle
  (precies de sleutels `download_id` + `reason`, `download_id` = infohash) — net zo streng als de bestaande
  DELETE-wachtrijregel (`blocklist=true&remove_from_client=false`).
- `apps/core/tests/test_recovery_blacklist.py`: vastgepinde whitelist bijwerken; `features/mediaguard/compat.py`: rij
  voor de nieuwe route (operator, optioneel).
- Opruimvoorstel: per afgeronde release een op `arrblock-hist:<token>:<hash>` (irreversibel, vóór het verwijderen),
  controle achteraf via de historie (`downloadFailed` aanwezig).

## Eerdere bevinding (1.4.0)

`DELETE /api/v1/instances/{id}/queue/{queue_id}?remove_from_client=false&blocklist=true` (operator) roept in de *arr
`DELETE /api/v3/queue/{id}?removeFromClient=false&blocklist=true` aan: release op de blocklist, download blijft
staan, auditregel `queue.remove`. MSO gebruikt deze route sinds 1.4.0 voor releases die nog in de wachtrij staan
(op `arrblock:<token>:<hash>`, vóór het verwijderen), via de whitelist-regel
`("DELETE", re.compile(r"^/api/v1/instances/[A-Za-z0-9_.-]+/queue/\d+$"))`.
