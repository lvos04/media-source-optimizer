# Architectuur — Media Source Optimizer

## 1. Overzicht

```
                 LAN (poort 38081)
                        │
                ┌───────▼────────┐  same-origin proxy: /api/v1 · /newznab · /torznab
                │ web (Next.js)  │  MediaGuard-design system (afgeleide kopie, zie apps/web/PROVENANCE.md)
                └───────┬────────┘
             netwerk app│
        ┌───────────────▼───────────────┐        ┌──────────────────────────┐
        │ api (FastAPI, uvicorn)        │        │ worker (zelfde codebase) │
        │ auth/RBAC/CSRF · REST /api/v1 │        │ persistente jobs, polling│
        │ Newznab/Torznab-server        │        │ health, tuning, back-ups │
        └───────────────┬───────────────┘        └────────────┬─────────────┘
                 netwerk db (intern)                          │
                        └──────────────┬──────────────────────┘
                               ┌───────▼────────┐
                               │ PostgreSQL 16  │ taken, metingen, index, audit, versleutelde geheimen
                               └────────────────┘
  Uitgaand (SSRF-gecontroleerd, alleen via API's): Prowlarr · SABnzbd · qBittorrent · MediaGuard (optioneel)
  · directe Newznab/Torznab/Spotweb-indexers · NNTP-providers (alleen voor tests/opt-in header-index)
```

Geen Docker-socket, geen host-network, geen bestandstoegang tot MediaGuard. Optioneel een read-only
mediamount (`MSO_MEDIA_DIR`) alleen voor hardlink-/vrije-ruimte-diagnose.

## 2. Codeorganisatie

| Pad | Inhoud |
|---|---|
| `apps/core/mso/` | foundation: `config`, `db` (pool + omkeerbare migraties), `auth`, `security` (redactie, SSRF, rate limit), `vault`, `audit`, `jobs`, `changes` (journaal `applying` + reconciler), `state` (modus, pauze, noodrem), `settings_registry`, `backups` (ook vóór upgrade en herstel van oudere dumps), `retention` (bewaartermijnen), `app` (auto-discovery) |
| `apps/core/mso/domain/` | gedeelde vocabulaire: 4 MediaGuard-categorieën, `NormalizedRelease` + titelparser + identiteit/dedupe + kwaliteitspoorten, download-uitkomsten (`outcomes`: `failed_at`, item-identiteit, netto/bruto faalratio), `releasegate` (nep-releasecontrole) |
| `apps/core/mso/connectors/` | `prowlarr`, `sabnzbd`, `qbittorrent`, `mediaguard` (+ `base` registry, `monitor` koppelingsbewaking) |
| `apps/core/mso/features/indexers`, `search`, `newznab_server` | **Usenet/Indexer- en zoekzijde**: Prowlarr-sync, directe indexers/Spotweb, aggregator, origin graph, eigen Newznab/Torznab |
| `apps/core/mso/features/usenet` | **Usenet-engine**: SABnzbd-historie, providers, NNTP-client, artikelbeschikbaarheid, bijdrage-metingen, opt-in header-index; sinds 1.6.0 `stagelog` + `unpack` (oorzaak uit het verwerkingslog), `retry` + `coverage` (alleen zinvol opnieuw proberen), `servertest` + `autoheal` + `accounts` (serverbewaking, vervaldata, blokken), `categories` (categorie '*') |
| `apps/core/mso/features/torrent` | **Torrent-engine**: diagnose, beleid/guardrails, tuning met evaluatie+rollback, cross-seed, opslag |
| `apps/core/mso/features/mediaguard`, `recovery` | MediaGuard-koppeling (read-only + officiële zoekroute) en herstel (baseline, kandidaten, blacklist) |
| `apps/core/mso/features/costs`, `overview`, `automation`, `ai` | orchestrator-laag: kosten/simulatie, dashboard (`overview/goals` doelenkaart, `overview/autopilot` Wacht op jou + Wat MSO deed), automatisering (`autopilot` afkoeling/noodrem, `footprint` + `overrides` voetafdruk en eigen keuze), opt-in AI-adviseur |
| `apps/web/` | Next.js-UI, 15 schermen (plus de subpagina Voetafdruk) + login + setup |
| `apps/mocks/msomocks/` | testdubbels (Prowlarr, SABnzbd, qBittorrent, MediaGuard, Newznab/Torznab, Spotweb, NNTP) — nooit in productie |
| `deploy/truenas/` | `compose.yaml` (Install via YAML) + build-override |
| `tests/stack/` | containerstack + mocks + smoke/e2e |

De twee engines zijn onafhankelijk: Usenet (`features/usenet` + SABnzbd) en Torrent (`features/torrent` +
qBittorrent) delen alleen de `domain`-laag en de gedeelde tabellen. De orchestrator-laag (zoeken, herstel,
kosten, automatisering, audit) gebruikt beide via de tabellen en via vaste Python-contracten
(`SabClient.add_url/add_nzb`, `QbitClient.add_torrent`, `search.service.search`, `recovery.blacklist`).
Ontbreekt een engine of koppeling, dan toont de UI een eerlijke `niet beschikbaar`-staat.

## 3. Kernmechanismen

### 3.1 Change sets (preview → goedkeuring → toepassen → rollback)
Elke schrijfactie naar een extern systeem is een `change_sets`-record met per operatie `before`/`after`.
Risicoklassen: `advies` (alleen tonen), `automatisch_veilig` (mag door automatisering in beheermodus als het
gebied is ingeschakeld), `toestemming_nodig` (eerst goedkeuring door een beheerder). Bij toepassen: eerst
afwijkingsdetectie (is de huidige waarde nog wat de preview zag?), dan per operatie schrijven + teruglezen;
een fout herstelt automatisch wat al geschreven was. Rollback schrijft de geobserveerde `before` terug.
Appliers bestaan voor `sabnzbd` (serverprioriteit/enable/optional), `sabnzbd_retry` (een mislukte download opnieuw
laten proberen; niet terug te draaien), `sabnzbd_categories` (een ontbrekende categorie aanmaken, sinds 1.6.0),
`qbittorrent` (voorkeuren, limieten, recheck/reannounce, cross-seed toevoegen), `download_send` (een vervanger
versturen; niet terug te draaien) en `prowlarr` (alleen MSO zelf als indexer registreren).

Sinds 1.6.0 (`mso/changes.py`):
- **Atomair**: per wijziging een PostgreSQL-advisory-lock voor het hele toepassen/terugdraaien plus
  `UPDATE … WHERE status = ANY(…) RETURNING`; bezet → 409. Een wijziging wordt nooit door twee processen tegelijk
  behandeld.
- **Journaal**: na de driftcontrole gaat de wijziging naar `applying` met `observed_before` per op, vóór de eerste
  externe schrijfactie; elk op-resultaat (geschreven + teruggelezen) wordt meteen opgeslagen.
- **Reconciler** (`reconcile_interrupted`, taak `changes.reconcile` elke 2 min en direct bij workerstart): een
  `applying`-wijziging waarvan het lock vrij is (het proces leeft niet meer) wordt bij de bron nagelezen en afgerond
  (`applied`) of de omkeerbare geschreven ops worden teruggezet (`failed`); onduidelijk → "Wacht op jou"-item; in
  alleen-lezen schrijft hij niets. Audit `change.interrupted`.
- **Terugdraaien met driftcontrole per op**: huidige waarde = MSO's waarde → terugschrijven + teruglezen; al de oude
  waarde → klaar; anders → `overgeslagen` ("buiten MSO gewijzigd") en een eigenaar-override. Mislukt teruglezen →
  status blijft `applied` (geen valse "teruggedraaid").
- **Nooit onbeheerd verwijderen**: een applier kan markeren dat terugzetten iets verwijdert (`rollback_deletes`, bijv.
  een SABnzbd-categorie). Zo'n op zet de reconciler, het herstel na een mislukte toepassing of een automatisch
  terugdraaien nooit terug; handmatig alleen door een beheerder.

### 3.2 Persistente, herstartveilige jobs
`jobs`-tabel met `FOR UPDATE SKIP LOCKED`, heartbeats en lease-verloop: na een crash of herstart worden
onderbroken jobs opnieuw ingepland en hervat. Idempotency-keys voorkomen dubbele uitvoering; periodieke
schema's gebruiken een sleutel per tijdslot. Fouten krijgen exponentiële backoff met jitter.

### 3.3 Anti-loop (origin graph)
Iedere zoekopdracht draagt `trace_id`, `hop_count` en de bezochte knopen (`X-MSO-Trace`). Een geconfigureerde
gerichte bron-graaf plus aanroeperdetectie (consumer-sleutel, Prowlarr-User-Agent, lopende uitgaande
Prowlarr-zoekopdracht) zorgt dat een verzoek dat van Prowlarr komt nooit terug naar Prowlarr gaat en dat MSO's
eigen registratie in Prowlarr wordt uitgesloten. Weigering met reden `cycle_detected` / `hop_limit`.
Regressietest: zonder bewaking 7 geneste zoekopdrachten, met bewaking precies 1.

### 3.4 Release-normalisatie
`NormalizedRelease` bevat protocol, bron, guid, NZB-id/message-id, infohash v1/v2, media-id's,
seizoen/aflevering/absolute (anime) nummering, categorie, grootte, codec/resolutie/HDR/DV, audio/ondertitels,
retentie, seeders, trackerstatus, confidence, TTL en false-positive-vlaggen. Dedupe gebeurt op
protocol-identiteit (`btih:`, `btmh:`, `mid:`, `nzb:`, of guid per bron) — nooit op titel.

### 3.5 Vier bibliotheekcategorieën
`tv`, `tv-anime`, `movies`, `movies-anime`, met Newznab-mapping gelijk aan MediaGuard
(`provisioning/templates.py`: anime = 5070, animefilms = films + 5070) en bron-scopes `normal`/`anime` die
overeenkomen met MediaGuard's Prowlarr-tags. Een bron met alleen `anime` bedient nooit `tv`/`movies`.

### 3.6 Modi
Standaard **alleen-lezen**. **Beheermodus** staat handmatig toepassen toe. **Automatisering** past alleen
`automatisch_veilig` toe, per gebied aan te zetten, met uurlimiet, en kan altijd gepauzeerd worden
(topbar-knop). **Autopilot** (sinds 1.5.0) past alles toe wat terug te draaien is, nooit verwijderen.
Vergrendelde integriteitsgrenzen gelden altijd.

Sinds 1.6.0 bewaakt de automatisering zichzelf (`features/automation/autopilot.py`, `state.py`):
- **Afkoeling** (`automation_failures`): een mislukte automatische poging wacht 1 u, dan 4 u; vanaf de derde keer
  geparkeerd (één "Wacht op jou"-item, hooguit één poging per dag). Een gewijzigd voorbeeld begint opnieuw. Mislukte
  pogingen tellen mee voor `automation.max_per_hour`. Dezelfde poort geldt voor elke onbeheerde toepassing (ook
  qBittorrent-tuning in beheermodus via `apply_unattended`).
- **Noodrem** (`autopilot_hold` in `system_state`): ≥ `automation.brake_rollbacks` (3) automatische terugdraaiingen of
  ≥ `automation.brake_failures` (5) mislukte automatische toepassingen in 24 u → `automation_allowed()` en `autopilot()`
  worden onwaar. Alleen automatische schrijfacties stoppen; synchronisaties, controles en voorstellen gaan door.
  Opheffen alleen met "Hervatten".
- **Eigen keuze** (`change_overrides`, `overrides.py`): `why_not()` weigert automatisch toepassen op een pad dat de
  eigenaar terugzette (standaard 30 dagen, `automation.override_days`); het blijft een voorstel.

### 3.7 Usenet: oorzaak uit het verwerkingslog
```
SABnzbd history (+ archive) ──► stagelog.parse(stage_log) ──► feiten in download_outcomes.meta.stage
                                                                (classifier_version 2)
                                 failures.classify(facts) ──► oorzaak + bewijs; server alleen bij ontbrekende artikelen
                                 unpack.check() ──► één "SABnzbd kan downloads niet uitpakken" bij herhaling
```
`stagelog` leest SABnzbd's eigen regels (Engels en Nederlands, nagekeken in SABnzbd 5.1.3) en zet ze om in feiten:
ontbrekende artikelen, herstel, de geredigeerde uitpakregel, het probleem (wachtwoord, schijf vol, rechten,
uitpakprogramma, beschadigd archief, uitvoerbaar bestand) en `data_complete`. `history.reclassify` herbeoordeelt
bestaande rijen eenmalig na de upgrade en op verzoek. De artikelteller (`history.snapshot_server_stats`) slaat
`articles_success = max(tried − failed, 0)` op, omdat SABnzbd's veld het aantal mislukte artikelen bevat.

### 3.8 Opnieuw proberen alleen bij dekkingswinst
`retry.basis()` laat alleen ontbrekende artikelen / mislukt herstel of een serverfout die SABnzbd noemt toe;
`coverage.last_gain_at()` = max(`usenet_server_autoheal.recovered_at`, eerste keer dat een nieuwe server aan stond).
Alleen downloads die vóór die winst mislukten, komen in aanmerking. Effectiviteitsrem: ≥ 10 afgeronde pogingen in
14 dagen met < 30 % succes → pauze (`system_state.usenet.retry_brake`) tot een nieuwe dekkingswinst of "Hervatten".

### 3.9 Serverbewaking met SABnzbd's servertest
`SabClient.test_server()` roept `mode=config&name=test_server` aan met de instellingen uit `get_config`, het gemaskeerde
wachtwoord en `server=<naam>`, zodat SABnzbd zijn eigen opgeslagen wachtwoord gebruikt. `servertest` classificeert
(ok / inloggen geweigerd / verlopen / toegang geweigerd / blok op / te veel verbindingen / onbereikbaar / TLS / onbekend /
vergrendeld) en bewaart resultaten (`usenet_server_tests`). `autoheal`: uit pas na een bevestigende test; uitgezette
servers elke 6 u testen (na een week 24 u, verlopen wekelijks); weer aan via `propose_and_apply` alleen na een
geslaagde test; `recovered_at` pas als SABnzbd's tellers voor die server stegen. Zonder servertest: de 1.5.0-proef als
terugval. `accounts`: vervaldatum/quotum uit `get_config`, herinneringen 14/7/1 dag, blokrestant + voorspelling.

### 3.10 Categorie '*'
`categories.diagnose()` vergelijkt wat Sonarr/Radarr meesturen (MediaGuard-leesroutes: instanties, downloadclients,
gezondheid, inrichting) met `get_config&section=categories` van SABnzbd: (a) onbekende categorie, (b) geen categorie,
(c) andere bron, plus `ok`/`geen_ster`/`onbekend`. Bij (a) houdt de taak `usenet.categories_check` (elke 6 u) precies
één voorstel open op de applier `sabnzbd_categories` (`toestemming_nodig`, nooit automatisch, eigen evaluator die nooit
automatisch terugdraait). Een besluit van de eigenaar (verworpen/teruggedraaid) wordt onthouden tot het probleem weg is.

### 3.11 Doelenkaart en meting
`domain/outcomes`: `failed_at` (eerste overgang naar `failed`) en `outcome_at = COALESCE(finished_at, failed_at)` voor
alle vensters; `item_key()` bepaalt de identiteit van een aflevering/film (S/E, uitzenddatum, filmjaar) voor de netto
faalratio naast de bruto. `overview/goals`: proefperiode (instelling of eerste keer Autopilot, 28 dagen, lokale dagen
uit `TZ`), startwaarde = eerste uur (bevroren in `system_state['goals.start']`), trend uit `goal_snapshots` (uurlijkse
taak `overview.goals_snapshot`) of reconstructie, en één blokkadezin per doel.

### 3.12 Rust: Wacht op jou, Wat MSO deed, voetafdruk
`overview/autopilot.waiting()` ontdubbelt aanbevelingen die al in een open opruimbundel zitten, groepeert de rest en
telt `decisions` (één beslissing = één regel); dezelfde bron voedt Overzicht, de topbalk, de badge en de tabtitel
(`GET /api/v1/overview/waiting`). `summary()` telt `actions_total` zonder synchronisaties/evaluaties, met per actie
wat/waarom/effect. `automation/footprint`: taak `automation.footprint` (elk uur, alleen lezen, elke modus) leest per
pad de huidige waarde met de applier van het doel → actief / buiten MSO gewijzigd (→ override + info-item) /
onbereikbaar / verdwenen (`change_footprint`). "Alles terugzetten" maakt per systeem een gewone wijzigingsset
(driftcontrole, teruglezen, audit); verwijderen alleen met goedkeuring, categorieën alleen met de hand.

### 3.13 Nep-releasecontrole vóór verzenden
`domain/releasegate` hergebruikt de regels van de scan ná het downloaden (`torrent/diagnostics.payload_scan`).
`check_download()` draait in `recovery/actions._dispatch` (handmatig, voorstel én automatisch via de
`download_send`-applier) en in `search/api.send`: .torrent → bestandenlijst, magnet → titel + `dn`, NZB → bestandsnamen
uit de subjects (defusedxml). Weigering → 422, audit `recovery.send_refused`, bewezen nep op de blacklist en de
opgeslagen kandidaat afgekeurd. `torrent/policy.check_release()` en de kandidaten-bewijsrij "Echte release" weren
uitvoerbare namen en absurde groottes; de eigen Newznab/Torznab-feed laat ze weg.

### 3.14 Onderhoud en koppelingsbewaking
- `retention` (taak `system.retention`, dagelijks): jobs, ruwe `observations` → `observations_hourly`, automation_log,
  login_attempts; daarna `VACUUM (ANALYZE)`. Het ruwe venster groeit vanzelf mee met de langste lezer
  (`raw_retention()`). `audit_log` blijft.
- `backups`: refetchbare tabellen zonder data in de dump + resync na herstel; vrije-ruimtecontrole; `pre_upgrade_backup()`
  vóór openstaande migraties (supervisor, `mso migrate`, `init_runtime`); herstel van een oudere dump = `migrate_down`
  naar de dumpversie → `pg_restore` → `migrate_up`; een nieuwere dump wordt geweigerd; modus/pauze/noodrem blijven.
- `connectors/monitor`: elke job heeft een meet-scope; `base.http_client` geeft voor SABnzbd/qBittorrent/Prowlarr een
  `MonitoredClient`. Aan het eind van de job één uitspraak per koppeling (`status`, `status_since`, `last_ok_at`,
  `failure_kind`, geredigeerde `last_error`); schema's van een koppeling die down is hooguit elke 15 min, bij herstel
  meteen een volledige ronde.

## 4. Data

Migraties `0001`–`0607` (elk met volledige `down`; `0603` bestaat niet). Belangrijkste tabellen: `users`, `sessions`,
`secrets`, `connectors`, `audit_log`, `jobs`, `change_sets`, `backups`, `settings`, `system_state`; gedeeld:
`download_outcomes`, `sources`, `services`, `observations`, `recommendations`; per engine eigen tabellen
(`usenet_*`, `torrent_*`, `direct_indexers`, `newznab_consumers`, `release_cache`, `search_sends`,
`origin_edges`, `mg_*`, `recovery_*`, `automation_log`, `ai_*`).

Nieuw in 1.6.0: `change_overrides` (eigen keuze), `usenet_server_coverage`, `usenet_server_tests`,
`usenet_server_accounts`, `goal_snapshots`, `automation_failures`, `observations_hourly`, `change_footprint`; kolommen
`download_outcomes.failed_at`, `change_sets.apply_started_at`/`applied_auto` (+ status `applying`),
`connectors.status_since`/`last_ok_at`/`failure_kind`, `jobs.connector_id`.

Opslaggrenzen: header-index met harde MB-limiet + TTL-purge; samples/observaties met retentie-instellingen en de
dagelijkse ronde `system.retention`; back-ups met rotatie (`backup.keep`; `pre-restore` 30 dagen/nieuwste 2;
`pre-upgrade` nieuwste 3).

## 5. API

OpenAPI op `/api/openapi.json` (UI: `/api/docs`, alleen via de API-container). Conventies: prefix
`/api/v1/<gebied>`, fouten `{detail, code, correlation_id}`, lijsten `{items: […]}`, rollen viewer < operator < admin.
Newznab/Torznab: `/newznab/api`, `/torznab/api` (`t=caps|search|tvsearch|movie|get`).

Nieuwe routes in 1.6.0 (rol tussen haakjes): `GET /overview/goals`, `GET /overview/waiting` (viewer);
`GET /automation/safety`, `GET /automation/hold`, `GET /automation/undo` (viewer), `POST /automation/hold/resume`,
`POST /automation/undo`, `POST /automation/failures/reset` (operator); `GET /automation/footprint`,
`GET /automation/footprint/restore` (viewer), `POST /automation/footprint/refresh|restore` (operator);
`POST /usenet/sab/servers/{server}/test` (operator, elke modus), `GET /usenet/sab/server-tests`,
`GET /usenet/sab/accounts` (viewer); `GET /usenet/categories/diagnosis` (viewer), `POST /usenet/categories/propose`
(admin); `GET|POST /usenet/sab/reclassify` (viewer|operator), `POST /usenet/retries/resume` (operator);
`GET /system/maintenance` (viewer), `POST /system/maintenance/run` (admin).
