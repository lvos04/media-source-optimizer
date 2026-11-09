# Samenwerking met MediaGuard Ultimate

Media Source Optimizer (MSO) is een **zelfstandige companion-app**. MediaGuard wordt op geen enkele manier
aangepast: geen patch, plugin, SQL-wijziging, proxy-injectie, gedeelde database of gedeelde bestanden.
MSO werkt volledig zonder MediaGuard (standalone modus) en schakelt automatisch terug naar standalone als
MediaGuard onbereikbaar is.

Referentie: `lvos04/media-guard-ultimate@e92c2acc7f24cbf19289f0d5617175f6ff32c13e` (alleen-lezen gebruikt;
aan het eind van de bouw bit-voor-bit ongewijzigd geverifieerd, zie `TEST_RESULTS.md` §1). Routes die later zijn
toegevoegd, zijn nagekeken tegen een nieuwere commit; die staat per route in `compat.py` (1.4.0: `aec9e64`, 1.6.0:
`9bed266`).

## 1. Eigenaarschap

| Systeem | Is eigenaar van | MSO doet |
|---|---|---|
| MediaGuard | bibliotheek, gewenste releases (wanted), kwaliteitsbeslissingen, import, AV1-encode, bestandsvervanging, eigen Prowlarr-reconcile (tags `anime`/`normal`, Applications), inrichting van downloadclients en hun categorieën | leest via de gedocumenteerde API (sinds 1.6.0 ook instanties, downloadclients, gezondheidsmeldingen en inrichting, alleen lezen); vraagt alleen een herzoek aan via `POST /api/v1/wanted/{id}/search` en zet binnen een goedgekeurde malware-opruiming een release op de blokkeerlijst |
| Prowlarr | indexers, sync naar de forks | leest health/stats/indexers/zoeken; biedt eigen Newznab/Torznab-feeds aan; stelt alleen een *preview* voor om MSO als indexer te registreren; detecteert conflicten met MediaGuard's reconcile maar past die nooit aan |
| SABnzbd | NZB-wachtrij, serverprioriteit, categorieën | leest (ook het verwerkingslog per download en de serverinstellingen zonder wachtwoord); laat SABnzbd zijn eigen servertest doen; schrijft alleen serverprioriteit/enable/optional en — na goedkeuring — een ontbrekende categorie, via change sets met rollback; laat mislukte downloads opnieuw proberen (`mode=retry`, niet terug te draaien); handmatig versturen |
| qBittorrent | torrents, trackers, wachtrij, ratio, snelheid | leest; schrijft voorkeuren/limieten via change sets met beleid + rollback; nooit verwijderen |
| MSO | eigen index, metingen, kosten, jobs, gebruikers, audit | volledig eigenaar |

## 2. Authenticatie richting MediaGuard

In volgorde van voorkeur:

1. **API-sleutel** (aanbevolen, vanaf MSO 1.2.0): header `X-Api-Key: mgk_…`, met een eigen rol (`operator`, of `viewer`
   voor alleen-lezen). Geen sessie of CSRF, in MediaGuard per sleutel in te trekken. MediaGuard moet hiervoor
   API-sleutels ondersteunen: contract en bouwprompt in [docs/MEDIAGUARD_API_KEYS.md](docs/MEDIAGUARD_API_KEYS.md).
   Een MediaGuard zonder die ondersteuning antwoordt 401; MSO meldt dat dan duidelijk.
2. **Eigen gebruiker** met rol `operator`/`viewer`: MSO logt in via `POST /api/v1/auth/login`, houdt cookie
   `mg_session` + CSRF-token alleen in het geheugen en logt opnieuw in bij 401.
3. **`X-MediaGuard-Token`** (interne token): dat is een admin-principal in MediaGuard en dus breder dan nodig;
   de UI waarschuwt hiervoor. Alleen als noodoplossing.

## 3. Compatibiliteitsmatrix

Elke route is geverifieerd tegen de referentiebron (bestand::functie in `apps/core/mso/features/mediaguard/compat.py`).
De kolom *live* wordt bij elke verbindingstest gevuld uit MediaGuard's `/api/v1/openapi.json` (of door
GET-probing als OpenAPI niet beschikbaar is). In de UI: **MediaGuard-koppeling → Compatibiliteit**.

| Route | Methode | Min. rol in MediaGuard | Gebruik door MSO |
|---|---|---|---|
| `/api/v1/health` | GET | — | bereikbaarheid, versie |
| `/api/v1/auth/login` | POST | — | sessie voor de MSO-gebruiker |
| `/api/v1/auth/me` | GET | viewer | rolcontrole (vereist) |
| `/api/v1/status` | GET | viewer | versie, noodstop (bij noodstop geen zoekaanvragen) |
| `/api/v1/openapi.json` | GET | — | capability-detectie |
| `/api/v1/wanted` | GET | viewer | ontbrekende items (vereist; cache met TTL, nooit leidend) |
| `/api/v1/wanted/kpis`, `/wanted/reasons` | GET | viewer | baseline/oorzaken |
| `/api/v1/wanted/backfill/quota` | GET | viewer | MediaGuard's eigen quotum — MSO blijft eronder |
| `/api/v1/wanted/{id}` | GET | viewer | detail |
| `/api/v1/wanted/{id}/diagnosis?cached=true` | GET | viewer | alleen gecachte diagnose (geen nieuwe interactieve diagnose) |
| `/api/v1/policy/effective` | GET | viewer | kwaliteitseisen → `QualityRequirements` |
| `/api/v1/wanted/{id}/search` | POST | operator | herzoek via de officiële route |
| `/api/v1/instances/{id}/queue` | GET | viewer | wachtrij-item van een verdachte torrent opzoeken (1.4.0) |
| `/api/v1/instances/{id}/queue/{queue_id}` | DELETE | operator | **alleen** met `blocklist=true&remove_from_client=false`, en alleen binnen een goedgekeurde malware-opruiming: release op de blokkeerlijst van Sonarr/Radarr. De client weigert elke andere parametercombinatie vóór er netwerkverkeer is. |
| `/api/v1/instances` | GET | viewer | Sonarr/Radarr-instanties (id, type, naam, MediaGuard-downloadcategorie, laatst bekende gezondheidsmeldingen) voor de diagnose van categorie '*' (1.6.0, optioneel). URL's, sleutelvlaggen en foutteksten neemt MSO niet over. |
| `/api/v1/instances/{id}/download-clients` | GET | viewer | Welke SABnzbd-categorie Sonarr/Radarr meesturen (`tvCategory`/`movieCategory`) en of MediaGuard die downloadclient beheert (1.6.0, optioneel). MSO houdt alleen naam, type, protocol, aan/uit, prioriteit en categorie over; host, poort, gebruikersnaam en (gemaskeerde) sleutels vallen al in de connector weg. |
| `/api/v1/instances/{id}/health` | GET | viewer | Meldingen van Sonarr/Radarr zelf over downloadclient, categorie, import en paden (geredigeerd) (1.6.0, optioneel). |
| `/api/v1/provisioning/config` | GET | viewer | Alleen de categorie per instantie, de inrichtingsmodus en of MediaGuard de downloadclients inricht (1.6.0, optioneel). Adressen, gebruikersnamen en geheimen laat MSO weg. |

De vier leesroutes van 1.6.0 zijn optioneel: geeft MediaGuard 403 ("geen toegang") of 404 ("route ontbreekt", oudere
MediaGuard), dan blijft de koppeling `connected` en valt alleen dat deel van de diagnose weg (zonder de
downloadclient-route gebruikt MSO MediaGuards eigen categorie per instantie en zegt het "waarschijnlijk"). Instantie-id's
worden gecontroleerd (`[A-Za-z0-9_.-]{1,64}`) voordat ze in een pad komen. **Er zijn in 1.6.0 geen nieuwe
schrijfroutes naar MediaGuard bijgekomen**; `ALLOWED_WRITES` is ongewijzigd.

Bewust **nooit** gebruikt: `retry-relaxed` (versoepelt kwaliteit), `wanted/actions`, `ignore`/`unignore`/
`keep-monitoring`, `backfill-plan`, interactieve diagnose, hooks, bibliotheek/import/encoding, en elke schrijfactie op
instanties: downloadclients aanmaken, wijzigen of testen (`POST|PUT|PATCH|DELETE /instances/{id}/download-clients…`) en
de inrichting wijzigen of toepassen (`PUT|POST /provisioning/config|plan|apply|drift-check`). De MSO-client weigert
elke andere niet-GET-aanroep vóór er netwerkverkeer is (getest).

## 4. Herzoeken: begrenzing

1. Alleen in modus *connected*, met capability, MediaGuard-rol ≥ operator en zonder MediaGuard-noodstop.
2. Idempotency-sleutel per item en tijdvenster → geen dubbele aanvragen.
3. Exponentiële backoff per item na mislukkingen.
4. Eigen MSO-quotum (standaard 10/uur, 40/dag).
5. MediaGuard's live `backfill/quota` moet meer dan een reserve (standaard 2) over hebben; anders geen aanroep.
6. Automatisch alleen als `mediaguard.auto_search_requests` aan staat (standaard **uit**) én automatisering
   is toegestaan (beheermodus, niet gepauzeerd).

MSO bouwt **geen** tweede wanted-/import-agent: het levert extra bronnen, analyses, kandidaten en suggesties.

## 4a. Categorie '*' (1.6.0): grenzen

MSO legt uit waarom SABnzbd downloads in '*' zet en wijst de plek aan waar je het oplost, maar verandert niets in
MediaGuard of Sonarr/Radarr:
- Stuurt Sonarr/Radarr een categorie die SABnzbd niet kent, dan kan MSO (na goedkeuring, nooit automatisch) die
  categorie **in SABnzbd** aanmaken, met een eigen map met dezelfde naam (MediaGuard zoekt zijn categorieën in
  `complete_dir/<naam>`). Het voorbeeld waarschuwt dat MediaGuard downloads in die categorie daarna volgens zijn eigen
  regels opruimt; dat deel kan MSO niet terugdraaien. Voor MSO's eigen `mso-*`-categorieën stelt MSO dit bewust niet
  voor (MediaGuard zou MSO's niet-geïmporteerde verzendingen dan opruimen).
- Wordt er geen categorie meegestuurd, dan is de oplossing de inrichting in MediaGuard (als MediaGuard die
  downloadclient beheert) of Sonarr/Radarr zelf. De oude tip "pas het veld Category in Sonarr aan" is weg: een
  wijziging in Sonarr ziet MediaGuard als afwijking.

## 5. Kwaliteitseisen blijven leidend

Fallback-kandidaten (ook Usenet ↔ torrent) worden gefilterd met `check_quality` tegen eisen uit MediaGuard
(resolutie, HDR/DV, audiotalen, dual audio, ondertitels, anime-absolute nummering, juiste aflevering, jaar).
Eisen kunnen in MSO alleen **strenger** worden gemaakt, nooit soepeler (vergrendelde instelling
`integrity.respect_mediaguard_quality`). Versturen van een kandidaat die niet door de poort komt wordt door de
API geweigerd (getest in `test_recovery_quality.py`). In connected modus gaan handmatige verzendingen naar een
aparte categorie `mso-<categorie>` zodat er niets automatisch in de bibliotheek wordt geïmporteerd.

## 6. AV1- en seedveiligheid

MSO herschrijft, verplaatst of hernoemt nooit bestanden en verwijdert nooit zelfstandig iets. De enige uitzondering
is een door jou goedgekeurde opruiming van verdachte (uitvoerbare) torrents, en die raakt de bibliotheek niet. Met een optionele read-only mediamount
rapporteert MSO hardlinks (`st_nlink > 1`) tussen seedende torrents en bibliotheek ("MediaGuard AV1 mag dit niet
herschrijven"), conform `docs/AV1_SAFETY_DIFF.md` D2 van MediaGuard. Er is geen schrijfpad naar die mount.

## 7. Prowlarr: samen, niet tegen elkaar

MediaGuard beheert tags `anime`/`normal` en registreert de forks als Applications. MSO:
- leest die tags als bron-scope (anime en normaal blijven gescheiden);
- maakt nooit tags aan, hernoemt niets en raakt Applications niet aan;
- detecteert onenigheid (ontbrekende tag, anime-indexer alleen als normal getagd, MSO dubbel geregistreerd …)
  en toont een idempotente configuratiepreview die je zelf in Prowlarr/MediaGuard uitvoert;
- registreert zichzelf alleen na goedkeuring (`toestemming nodig`) als Newznab/Torznab-indexer; rollback
  verwijdert alleen wat MSO zelf aanmaakte.

## 8. Niet geverifieerd

Getest tegen een mock die op de referentiebron is gebaseerd (exacte routes en responsvormen), niet tegen een
draaiende MediaGuard-installatie. Dat geldt ook voor de vier leesroutes van 1.6.0 (mock met de vorm van
`instance_view`, `mask_provider` en `store.public_view`, inclusief 403/404). Het gedrag van Sonarr/Radarr bij
SABnzbd-categorieën is niet in hun broncode nagelezen. Zie `docs/HARDWARE_ACCEPTANCE.md` (§6 en §10) voor de controle op
de echte installatie.
