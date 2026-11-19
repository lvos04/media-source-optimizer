# Beheer en operatie

Dagelijks beheer van de standaard TrueNAS Custom App van Media Source Optimizer (MSO).
Gebruik het [Custom App-formulier](../deploy/truenas/CUSTOM_APP.md) voor installatie en
[het migratiepad](MIGRATION.md#updaten-via-truenas) voor upgrades.

## Gezondheid

| Waar | Controle |
|---|---|
| TrueNAS → Apps → MSO | De container is **Running**. De ingebouwde healthcheck controleert de interne API en webpagina. |
| Webpoort → `/readyz` | HTTP 200 als de controles van MSO slagen. Gebruik het monitorplan uit het manifest. |
| MSO → Systeem | Bekijk de database, worker, vrije ruimte en status van ingeschakelde koppelingen. |
| MSO → Overzicht → Koppelingen | Bekijk of een koppeling onbereikbaar is, een sleutel weigert of een time-out meldt. |
| MSO → Automatisering → Veiligheid | Controleer pauze en noodrem voordat je automatisch beheer hervat. |

Bij starten controleert de bewaker PostgreSQL en het schema. Als een migratie nodig is, controleert hij
eerst het pre-upgrade-archief. Daarna starten API, worker en web. De app-log toont de voortgang.

## Logs

- Bekijk via **TrueNAS → Apps → MSO → Logs** de uitvoer van de alles-in-één-container.
- Alle Python-logregels lopen door een redactiefilter: API-sleutels, wachtwoorden, passkeys, cookies, tokens en
  inloggegevens in URL's worden vervangen door `***`. In de container-stacktest
  (`tests/stack/smoke.py`) wordt gecontroleerd dat het beheerderswachtwoord, het databasewachtwoord en alle
  koppelingsgeheimen in géén enkele MSO-containerlog voorkomen.
- Elke API-fout heeft een `correlation_id`, ook zichtbaar in de UI; zoek daarop in de app-log.
- Functionele gebeurtenissen (inloggen, wijzigingen, back-ups, koppelingstests) staan in het auditlogboek:
  UI → Logs, of `GET /api/v1/audit`.
- Normaal bij herstart/stop van de app: PostgreSQL logt per open verbinding
  `FATAL: terminating connection due to administrator command`. Dat is geen fout.

## Back-ups

- **Automatisch:** worker-job `backup.create` elke 24 uur (instelling `backup.auto_daily`, standaard aan);
  bewaard: de nieuwste `backup.keep` (standaard 14) automatische + handmatige back-ups.
- **Handmatig:** **MSO → Systeem → Back-ups**.
- **Formaat:** `APPDATA/backups/mso-<UTC-tijd>-<soort>.dump` (`pg_dump -Fc`, PostgreSQL 16-client in het
  alles-in-één-image) plus `<naam>.sha256`. Sessies zitten er nooit in; versleutelde koppelingsgeheimen alleen bij de
  expliciete keuze "inclusief geheimen". De vault-sleutel zit er nooit in.
- **Lichter (sinds 1.6.0):** van tabellen die MSO zelf opnieuw ophaalt, gaat alleen de structuur mee, niet de inhoud
  (`--exclude-table-data`): `release_cache` (zoekcache), `mg_wanted_cache` en `mg_snapshots` (MediaGuard-cache),
  `usenet_hi_headers` (header-index), `torrent_samples` en `torrent_client_snapshot` (korte torrent-metingen). Na een
  herstel zet MSO meteen `mediaguard.sync` en `torrent.ingest` in de wachtrij (alleen als die koppeling is ingesteld;
  beide lezen alleen, dus ook veilig in Alleen-lezen) en begint de header-index opnieuw bij de nieuwste headers. Het
  herstelantwoord en de audit noemen wat er opnieuw wordt opgehaald (`resync`).
- **Vrije ruimte:** vóór elke dump controleert MSO of er genoeg ruimte is in de back-upmap: 2× de vorige dump (of de
  databasegrootte als er nog geen is) plus `MSO_DISK_RESERVE_GB`. Te weinig → er wordt niets weggeschreven, audit
  `backup.skipped` en één item in "Wacht op jou" met de getallen; het verdwijnt bij de volgende geslaagde back-up. De
  dagelijkse taak eindigt dan als *done* met "Overgeslagen: …"; een handmatige back-up mislukt met de reden; een herstel
  wordt geweigerd als de vangnet-back-up niet kan (de database blijft ongewijzigd).
- **Opruimen van vangnet-back-ups:** `pre-restore` ouder dan 30 dagen wordt verwijderd, de nieuwste 2 blijven altijd;
  van `pre-upgrade` blijven de nieuwste 3. De dump van een upgrade die nog niet gelukt is (een migratie die blijft
  mislukken), wordt nooit opgeruimd.
- **Buiten de server bewaren:** neem de volledige APPDATA-dataset op in je ZFS-snapshot- en replicatietaken,
  met afgeschermde toegang. Back-ups in dezelfde dataset beschermen niet tegen verlies van de pool.

## Herstel

1. UI → Systeem → Back-ups → **Herstellen**; typ ter bevestiging de bestandsnaam.
2. MSO controleert: bestand aanwezig, niet leeg, SHA-256 klopt, geldig `pg_restore`-archief. Bij een fout
   (beschadigd/ontbrekend) wordt níéts gewijzigd en zie je de reden.
3. Daarna maakt MSO eerst een vangnet-back-up (`…-pre-restore.dump`) en herstelt in één transactie
   (`pg_restore --clean --single-transaction`): alles of niets.
4. Iedereen moet opnieuw inloggen (sessies worden niet hersteld). Koppelingsgeheimen blijven behouden als de
   back-up ze niet bevat.
5. Komt de back-up van een oudere MSO-versie (bijv. de `pre-upgrade`-back-up), dan zet MSO het schema eerst
   terug naar die versie en werkt het na het herstel meteen weer bij; een herstart is niet nodig. Een back-up van
   een nieuwere versie wordt geweigerd. Modus, pauze en noodrem blijven zoals ze vóór het herstel waren.

Getest in de container-stack: herstel van een geldige back-up (gebruikers terug naar de back-upstand, sessie
ongeldig, geheimen behouden), en weigering van een beschadigde (bytes overschreven → "Checksum klopt niet") en
een ontbrekende back-up (→ "Back-upbestand ontbreekt") zonder wijziging van de database.

**Noodherstel zonder werkende MSO-interface:** stop de app via TrueNAS en herstel de volledige APPDATA-snapshot
met de bijpassende imageversie via [het terugvalpad](MIGRATION.md#terugdraaien-via-truenas).

## Back-up vóór upgrade

Bewaar vóór elke upgrade de herstelbare snapshot van de volledige APPDATA-dataset van de gestopte app,
de huidige formulierwaarden en de vorige imagepin. Zie [updaten via TrueNAS](MIGRATION.md#updaten-via-truenas).
De huidige bronstand weigert een database waarvan het hoogste migratienummer nieuwer is dan deze image kent, vóór de
back-up en vóór API of worker iets schrijven. In de log verschijnt: "database is van een nieuwere MSO-versie;
zet de bijpassende snapshot/back-up terug". De opdrachtregel eindigt dan met code 79. Er volgt geen migratie,
back-up, retentie-opruiming of automatische terugzetting. Kies in het standaardformulier van de TrueNAS Custom App
de image die bij de database hoort, of stop de app in de TrueNAS-web-UI en zet daar de snapshot van vóór de update
terug met de daarbij horende image. Alleen een oudere image kiezen maakt een nieuwere database niet compatibel.
De bewaker zet bij deze weigering één duidelijke foutregel in het containerlog, stopt zijn eigen database en
wacht op stoppen van de app, zonder opnieuw te migreren of API, worker of web te starten. Een nog levend
bewakerproces betekent dus geen gereedheid: de bestaande applicatie-healthcheck faalt zolang deze diensten uit staan.
Na het wachten op de back-upvergrendeling controleert MSO het schema opnieuw, met ook de migratievergrendeling
vast. Die vergrendelingen blijven vast tijdens de beslissing over back-up en retentie en tijdens het migreren.
Zo weigeren ook gelijktijdig startende processen zonder tabelwijzigingen of nieuwe of verwijderde herstelbestanden;
ook de aparte back-upstap eindigt dan met code 79. De bewaker bereidt vóór zijn eerste alleen-lezen schemacontrole
uitsluitend de databaseopslag en databasesocket voor. De mappen voor back-ups, geheimen en werkbestanden worden pas
na een geslaagde controle voorbereid. Een lege installatie en een gewone update blijven normaal starten.

### Gedrag per imageversie

**Gepubliceerde 1.6.0.** Bij ruimtegebrek of een dumpfout gaat de migratie door zonder nieuwe pre-upgradeback-up.
MSO logt dat het de back-up heeft overgeslagen bij ruimtegebrek of dat de dump is mislukt.

**Huidige bronstand.** Bij ruimtegebrek, een dumpfout of een onbruikbaar archief stopt de migratie.
Bij openstaande migraties op een bestaande database controleert MSO de SHA-256 en de leesbaarheid van het
PostgreSQL-archief. In de alles-in-één-image maakt de bewaker het archief met `mso backup --pre-upgrade` en
controleert `mso migrate --no-backup` het archief voor dezelfde upgrade opnieuw. In de ontwikkelstack maakt
`mso migrate` het archief. API en worker bewaken de voorwaarde ook tijdens hun start. De oorzaak van een stop
staat in het app-log. Een nieuwe installatie of een database op de huidige schemaversie kan direct starten.

Maak en verifieer bij beide imageversies vóór elke upgrade een snapshot van de volledige APPDATA-dataset van de gestopte app.

Controleer bij schemamigraties het log onder **TrueNAS → Apps → MSO → Logs**. Onder **Systeem → Back-ups**
staan de drie nieuwste gemaakte archieven als **Vóór upgrade**. Controleer daarna aanmelding, koppelingen en
gegevens zoals beschreven in het migratiepad.

## Taken (jobs) en herstel na uitval

- Taken staan in PostgreSQL (`jobs`), niet in het geheugen. De worker claimt met `FOR UPDATE SKIP LOCKED` en
  schrijft tijdens het werk een hartslag.
- Crasht of herstart de worker midden in een taak, dan zet de (nieuwe) worker bij het starten en daarna elke
  15 s taken terug in de wachtrij waarvan de lease (120 s zonder hartslag) verlopen is; de samenvatting krijgt
  `· hervat na herstart`.
- Fouten worden opnieuw geprobeerd met exponentiële backoff (+ jitter) tot `max_attempts`; daarna `failed`.
  In de UI (Systeem/Logs → Taken) kun je een mislukte of geannuleerde taak opnieuw starten of een lopende
  annuleren (`POST /api/v1/jobs/{id}/retry|cancel`).
- Geplande taken gebruiken een idempotentiesleutel per tijdslot: een herstart levert geen dubbele runs op.
- Is een koppeling onbereikbaar, dan draaien de taken die ervan afhangen hooguit elk kwartier (zie
  *Koppelingsbewaking*); mislukkingen door zo'n storing tellen niet mee in "N mislukte taken (24 u)".

### Onderbroken wijzigingen (reconciler)

Vóór de eerste schrijfactie naar SABnzbd, qBittorrent of Prowlarr krijgt een wijziging de status `applying`
("Bezig met toepassen"). MSO bewaart de oude waarde per operatie en markeert een schrijfpoging vlak vóór verzending.
Zodra de bron het schrijfverzoek bevestigt, legt MSO die bevestiging vast in het journaal. Daarna leest het de waarde
terug. Tijdens de uitvoering houdt MSO een PostgreSQL-advisory-lock vast.
Als het schrijfverzoek een time-out krijgt en de bron de uitkomst nog niet bevestigt, geeft de API 502 en blijft
de wijziging `applying`. Na vrijgave van het slot, ook na
een crash of herstart, controleert `changes.reconcile` de bron. Deze taak draait bij de start van de worker en daarna
elke twee minuten.

- MSO leest bij de eindcontrole alle limieten opnieuw, ook als een limiet bij de start al goed stond.
  Pas als alle gemeten waarden overeenkomen met het voorstel, volgt `applied` met één uitvoeringsaudit.
  De connector berekent de `after_hash` uit die eindmetingen. Een afwijkende handmatige waarde blijft staan.
- deels geschreven → MSO leest de bronwaarde vlak vóór elke herstelopdracht. MSO zet een omkeerbare operatie
  alleen terug als het schrijfantwoord van de bron is vastgelegd en de bronwaarde nog overeenkomt met de ingestelde waarde.
  MSO leest het resultaat terug en noteert een handmatige afwijking als `overgeslagen`. De wijziging krijgt `failed`
  met uitleg.
- bij uitval tussen de startmarkering en het vastleggen van het schrijfantwoord laat MSO de waarden staan.
  De beheerder krijgt één item in "Wacht op jou", ook als de bron inmiddels op de doelwaarde staat.
  Bij een ouder journaal zonder informatie over de schrijfpoging volgt die overdracht na één uur.
- bronwaarde onleesbaar of een schrijfactie na een time-out nog onbevestigd → MSO leest de bron periodiek zonder de
  oorspronkelijke opdracht opnieuw te versturen. Blijft bevestiging uit bij een controle meer dan één uur na de start,
  dan volgt `failed` met de uitkomst `onduidelijk` en één item in "Wacht op jou". De achtergrondcontrole stopt.
  De beheerder controleert de bron en maakt zo nodig een nieuw voorstel. Herhaling met dezelfde idempotentiesleutel
  geeft dan 502;
- een waarde die buiten MSO is veranderd, raakt MSO niet aan;
- iets terugzetten dat zou verwijderen (een SABnzbd-categorie die MSO aanmaakte), doet de reconciler nooit;
- in Alleen-lezen schrijft de reconciler niets: is er al een omkeerbare waarde geschreven, dan wacht hij (één item in
  "Wacht op jou") tot beheermodus of Autopilot; anders rondt hij af zonder te schrijven.

De afhandeling staat in de audit als `change.interrupted`. Een wijziging die nog `applying` was in een back-up die
je herstelt, wordt `failed` zonder dat MSO iets bij de bron terugzet.

### Noodrem

Gaan er in 24 uur te veel automatische wijzigingen mis (`automation.brake_rollbacks`, standaard 3 automatische
terugdraaiingen, of `automation.brake_failures`, standaard 5 mislukte automatische toepassingen; geteld vanaf de laatste
"Hervatten"), dan zet MSO `autopilot_hold` in `system_state`: alle automatische schrijfacties stoppen (Autopilot én
automatisering in beheermodus). Synchronisaties, controles, de voetafdruk en het klaarzetten van voorstellen gaan door;
handmatig toepassen blijft mogelijk. Audit `autopilot.self_paused`, een logregel, een item in "Wacht op jou" en een
banner bovenaan elke pagina.

- **Opheffen:** banner of Automatisering → Veiligheid → **Hervatten** (operator of beheerder; audit `autopilot.resumed`),
  of `POST /api/v1/automation/hold/resume`. Een andere modus kiezen of een back-up herstellen heft de noodrem niet op.
- **Afkoeling** (los van de noodrem): een mislukte automatische wijziging wacht 1 uur, dan 4 uur; vanaf de derde keer
  geparkeerd met één item in "Wacht op jou" en hooguit één stille poging per dag (tabel `automation_failures`).
  Mislukte pogingen tellen mee voor `automation.max_per_hour`.

## Onderhoud (bewaartermijnen)

De dagelijkse taak `system.retention` houdt MSO's eigen database klein. Instellingen onder Instellingen → Systeem:

| Instelling | Standaard | Wat |
|---|---|---|
| `retention.enabled` | aan | de dagelijkse ronde aan/uit |
| `retention.jobs_done_days` | 14 dagen | afgeronde (en geannuleerde) achtergrondtaken; wachtende of lopende nooit |
| `retention.jobs_keep_per_kind` | 50 | van elke taaksoort blijven altijd de nieuwste 50 staan |
| `retention.jobs_failed_days` | 60 dagen | mislukte achtergrondtaken |
| `retention.observations_raw_days` | 30 dagen | losse metingen; ouder wordt eerst per uur samengevat (`observations_hourly`: aantal, min, max, gemiddelde, mediaan, som) en dan verwijderd |
| `retention.observations_hourly_days` | 400 dagen | uursamenvattingen |
| `retention.automation_log_days` | 180 dagen | automatiseringslog |
| `retention.login_attempts_days` | 90 dagen | inlogpogingen |
| `retention.vacuum` | aan | daarna `VACUUM (ANALYZE)` op de opgeschoonde tabellen (nooit `VACUUM FULL`, dus geen tabelvergrendeling) |

- Het **auditlogboek wordt nooit opgeruimd**. Er wordt niets in SABnzbd, qBittorrent, Prowlarr of MediaGuard en geen
  bestand aangeraakt.
- **Terugkijkcontrole:** kijkt een evaluatie van een automatische wijziging of de indexer-betrouwbaarheid verder terug
  dan de ruwe termijn, dan verlengt MSO die termijn vanzelf (standaard is 15 dagen nodig, dus blijft het 30).
- Een ronde stopt na 20 minuten en gaat de volgende dag verder. Dezelfde ronde ruimt ook vangnet-back-ups op en sluit
  verouderde koppelingsmeldingen.
- **Zien:** Systeem → Onderhoud (laatste ronde, wat er is opgeruimd, termijnen) en Systeem → Database (grootte,
  grootste tabellen); API `GET /api/v1/system/maintenance`. Een beheerder start een ronde met **Nu opruimen**
  (`POST /api/v1/system/maintenance/run`). Audit `system.retention` alleen als er iets is opgeruimd.

## Koppelingsbewaking

Zie bij bronuitval het [runbook MSO provider outage](runbooks/MSO_PROVIDER_OUTAGE.md).

De achtergrondtaken houden zelf de status van SABnzbd, qBittorrent en Prowlarr bij (niet alleen de knop *Test*):

- Per taak kijkt MSO naar de antwoorden van die koppelingen. Een geweigerde sleutel (HTTP 401/403), een geweigerde
  verbinding of time-out, of een doorverwijzing in plaats van een API-antwoord (bijv. een inlogpagina van een proxy) →
  `auth_failed` of `offline`; minstens één geslaagd antwoord → gezond. Een trage zoekopdracht in Prowlarr (die op elke
  indexer wacht) telt niet als storing.
- Opgeslagen per koppeling: `status_since` (sinds wanneer), `last_ok_at` (laatst gelukt), `failure_kind`
  (auth | offline | timeout) en `last_error` (altijd geredigeerd: nooit een sleutel, wachtwoord of gebruikersnaam).
- **Rem:** zolang een koppeling down is, draaien de taken die ervan afhangen hooguit elk kwartier. **Bij herstel** zet
  MSO meteen een volledige ronde in de wachtrij.
- **Wacht op jou** alleen bij een geweigerde sleutel langer dan 15 minuten of een koppeling die langer dan 24 uur
  onbereikbaar is (audit `connector.down`); het item sluit vanzelf bij herstel (audit `connector.recovered` na een
  storing van minstens 15 minuten). Wegklikken of accepteren geldt voor de hele storing.
- Overzicht → Koppelingen toont "qBittorrent onbereikbaar sinds 2 d 4 u", "SABnzbd: API-sleutel of inloggegevens
  geweigerd sinds 20 min" of "… reageert niet (time-out) sinds …", met de laatste geslaagde keer en hoeveel taken
  daardoor mislukten.
- MediaGuard houdt via zijn eigen controle wel een status bij, maar krijgt geen melding in "Wacht op jou".

Getest in de container-stack: (1) een taak die als `running` met verlopen hartslag achterbleef ("gecrashte
worker") werd na het starten van de worker hervat en afgerond; (2) het worker-proces werd met `SIGKILL`
gedood, Docker herstartte de container (RestartCount 0 → 1), de worker werd weer gezond en voerde een nieuwe
taak uit.

## Resources

### Limieten en opslag van de Custom App

Het formulier stelt 2 CPU en 1536 MiB voor. Controleer het verbruik in TrueNAS. APPDATA bevat
PostgreSQL, de kluis, back-ups, werkbestanden en logs. De database en het auditlogboek groeien met
het gebruik. MSO geeft een waarschuwing als de vrije ruimte onder `MSO_DISK_RESERVE_GB` zakt.

### Opslag en groei

Op Systeem → Opslag en groei staat de laatste dagmeting, met herkomst en eenheid. MSO registreert één meting
per UTC-kalenderdag. De reeksen omvatten de database en haar tabellen, databaseverbindingen, geregistreerde back-ups,
vrije ruimte, de header-index en de laatste bruikbare momentopname van de torrentclient. Het containerlogvolume staat
op Onbekend, omdat dit buiten MSO valt.

`GET /api/v1/system/maintenance` levert de afgelopen 30 UTC-dagen in `growth.days`. Een ontbrekende dag heeft
`observed_at: null`, `values: null` en `provenance: null`. Elke dagmeting bevat in `provenance` de bron, kwaliteit
en bronmeettijd per reeks. De herkomst bovenaan het API-antwoord hoort bij `growth.latest`.
Een trend gebruikt per reeks het eerste en laatste bruikbare meetpunt over die periode. Eén meetpunt levert een
onbekende trend op. Een meting van vrije bytes vereist een niet-negatief geheel getal, inclusief nul. Een onbruikbare
waarde verschijnt als onbekend met een reden en telt niet mee voor de trend. Voor de torrentclient geldt bovendien
dat de momentopname tijdens de dagmeting maximaal tien minuten oud, beschikbaar en foutvrij moet zijn.

De dagelijkse opslagmeting draait ook als dagelijks opruimen uitstaat. Met opruimen aan bewaart het
bestaande onderhoud de laatste 400 UTC-dagen. De bestaande geschiedenis en het bewijs in de twaalf groeitabellen
behouden hun bewaarbeleid. Een genoteerde bewaartermijn voor een oudere tabel vereist een apart besluit.

Migratie `0611_resource_samples` voegt de meetreeks toe na de automatische back-up vóór upgrade.
Zie [het stappenplan voor upgrade en terugval](MIGRATION.md) voor volledig herstel met een gestopte APPDATA-snapshot.

### Gemeten benchmark (sandbox, géén TrueNAS)

Gemeten met `docker stats --no-stream` door `tests/stack/smoke.py` (zie
`tests/stack/results/smoke-report.md`), op 2026-11-02 in een Linux-sandbox: 4 vCPU (Intel Xeon 2,1 GHz),
15 GB RAM, Docker 29.9, lege installatie met vier gekoppelde mock-diensten. "Belasting" = 480 API-verzoeken
via de web-proxy met 8 parallelle clients plus één back-up-job (147,5 req/s, p50 48 ms, p95 91 ms, 0 fouten;
de CPU-pieken zijn de maxima van `docker stats`-samples tijdens die ±3 s). Een eerdere run gaf vrijwel
dezelfde waarden (123,7 req/s, p95 120 ms).

| Container | Geheugen rust | Geheugen belasting | CPU rust (max) | CPU belasting (max) | PIDs max |
|---|---|---|---|---|---|
| postgres | 31 MiB | 48 MiB | 3,7 % | 30,7 % | 16 |
| api | 61 MiB | 61 MiB | 1,4 % | 93,0 % | 18 |
| worker | 55 MiB | 55 MiB | 0,1 % | 0,2 % | 7 |
| web | 37 MiB | 42 MiB | 0,0 % | 37,1 % | 11 |
| **totaal** | **≈ 183 MiB** | **≈ 207 MiB** | | | |

(CPU-% is per kern; 100 % = één volledige kern.)

De cijfers gelden voor de lokale ontwikkelstack. Vergelijk voor de Custom App het gemeten verbruik
in de TrueNAS-interface met de limieten in het formulier.

## Netwerk en beveiliging in bedrijf

De alles-in-één-image publiceert alleen de webpoort. PostgreSQL gebruikt een interne Unix-socket
en de API luistert intern. De webserver geeft verzoeken aan de API door. Gebruik het vertrouwde
lokale netwerk en beheer eventuele TLS-terminatie via de eigen reverse proxy. De koppelingen
controleren het ingevoerde adres en weigeren onder meer loopback en link-local bestemmingen.

## Probleemoplossing

| Symptoom | Oorzaak / actie |
|---|---|
| Ontbrekende mount of schrijfrechten | Controleer de bestaande APPDATA-dataset en de mount op `/appdata` in **Apps → Edit**. |
| MSO meldt een nieuwer databaseschema | Start de bijpassende image of volg het [terugvalpad](MIGRATION.md#terugdraaien-via-truenas). |
| Koppeling weigert een loopback-adres | Gebruik het adres waarop de dienst vanaf MSO bereikbaar is. |
| Prowlarr-downloadlinks gebruiken een onbereikbaar adres | Stel het bereikbare adres in via `newznab.public_url` in MSO. |
| Opgeslagen geheimen onleesbaar na verhuizing | Controleer of de volledige APPDATA-snapshot met de bijpassende kluissleutel is teruggezet. |
| De back-up vóór upgrade mislukt | Lees de oorzaak in het app-log, controleer de vrije ruimte en herstel de back-upvoorwaarde vóór een nieuwe poging. |
| "Wacht op jou": *Back-up overgeslagen: te weinig vrije schijfruimte* | Er is niets weggeschreven. Ruimte vrijmaken of `backup.keep` verlagen; het item verdwijnt bij de volgende geslaagde back-up. |
| Herstel geweigerd: *komt van een nieuwere MSO-versie* | De back-up is gemaakt met een nieuwere versie. Herstel hem met die versie (of nieuwer). |
| Rode banner *Autopilot heeft zichzelf gepauzeerd* | Noodrem (zie hierboven). Bekijk Automatisering → Controle en Wijzigingen en klik **Hervatten**. |
| Wijziging blijft op *Bezig met toepassen* | Normaal hooguit een paar minuten. Staat MSO op Alleen-lezen, dan wacht de reconciler op beheermodus (item in "Wacht op jou"). |
| Overzicht: *Sleutel geweigerd sinds …* | API-sleutel van die dienst is veranderd: vervang hem onder Instellingen → Verbindingen. |
| Overzicht: *Onbereikbaar sinds …* | Dienst gestopt of adres veranderd; controleer de app in TrueNAS en de URL onder Instellingen → Verbindingen. |
