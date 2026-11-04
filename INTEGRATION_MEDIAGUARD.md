# Samenwerking met MediaGuard Ultimate

Media Source Optimizer (MSO) is een **zelfstandige companion-app**. MediaGuard wordt op geen enkele manier
aangepast: geen patch, plugin, SQL-wijziging, proxy-injectie, gedeelde database of gedeelde bestanden.
MSO werkt volledig zonder MediaGuard (standalone modus) en schakelt automatisch terug naar standalone als
MediaGuard onbereikbaar is.

Referentie: `lvos04/media-guard-ultimate@e92c2acc7f24cbf19289f0d5617175f6ff32c13e` (alleen-lezen gebruikt;
aan het eind van de bouw bit-voor-bit ongewijzigd geverifieerd, zie `TEST_RESULTS.md` §1).

## 1. Eigenaarschap

| Systeem | Is eigenaar van | MSO doet |
|---|---|---|
| MediaGuard | bibliotheek, gewenste releases (wanted), kwaliteitsbeslissingen, import, AV1-encode, bestandsvervanging, eigen Prowlarr-reconcile (tags `anime`/`normal`, Applications) | leest via de gedocumenteerde API; vraagt alleen een herzoek aan via `POST /api/v1/wanted/{id}/search` |
| Prowlarr | indexers, sync naar de forks | leest health/stats/indexers/zoeken; biedt eigen Newznab/Torznab-feeds aan; stelt alleen een *preview* voor om MSO als indexer te registreren; detecteert conflicten met MediaGuard's reconcile maar past die nooit aan |
| SABnzbd | NZB-wachtrij, serverprioriteit | leest; schrijft alleen serverprioriteit/enable/optional via change sets met rollback; handmatig versturen |
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
| `/api/v1/wanted/{id}/search` | POST | operator | **enige schrijfactie**: herzoek via de officiële route |

Bewust **nooit** gebruikt: `retry-relaxed` (versoepelt kwaliteit), `wanted/actions`, `ignore`/`unignore`/
`keep-monitoring`, `backfill-plan`, interactieve diagnose, hooks, instances/bibliotheek. De MSO-client weigert
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

## 5. Kwaliteitseisen blijven leidend

Fallback-kandidaten (ook Usenet ↔ torrent) worden gefilterd met `check_quality` tegen eisen uit MediaGuard
(resolutie, HDR/DV, audiotalen, dual audio, ondertitels, anime-absolute nummering, juiste aflevering, jaar).
Eisen kunnen in MSO alleen **strenger** worden gemaakt, nooit soepeler (vergrendelde instelling
`integrity.respect_mediaguard_quality`). Versturen van een kandidaat die niet door de poort komt wordt door de
API geweigerd (getest in `test_recovery_quality.py`). In connected modus gaan handmatige verzendingen naar een
aparte categorie `mso-<categorie>` zodat er niets automatisch in de bibliotheek wordt geïmporteerd.

## 6. AV1- en seedveiligheid

MSO herschrijft, verplaatst, hernoemt of verwijdert nooit bestanden. Met een optionele read-only mediamount
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
draaiende MediaGuard-installatie. Zie `docs/HARDWARE_ACCEPTANCE.md` voor de controle op de echte installatie.
