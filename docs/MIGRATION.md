# Schemamigraties, upgrades en rollback

Dit document beschrijft hoe het databaseschema van Media Source Optimizer (MSO) wordt beheerd, hoe je
upgradet en terugdraait, en welke compatibiliteitsregels gelden. Het betreft alleen de eigen
PostgreSQL-database van MSO; MSO migreert of wijzigt nooit databases of configuratie van MediaGuard,
Sonarr/Radarr, Prowlarr, SABnzbd of qBittorrent.

## Hoe migraties werken (`apps/core/mso/db.py`)

- Bestanden: `apps/core/mso/migrations/NNNN_naam.sql`, elk met een `-- migrate:up`- en een
  `-- migrate:down`-sectie. De down-sectie moet de up-sectie volledig terugdraaien.
- Nummering per onderdeel (voorkomt botsingen): 0001–0099 fundament, 0100–0199 indexers/zoeken,
  0200–0299 Usenet, 0300–0399 torrents, 0400–0499 MediaGuard/herstel, 0500–0599 kosten/overzicht/automatisering,
  0600–0699 versie 1.6.0 (alle onderdelen). Migraties worden in numerieke volgorde toegepast.
- Elke migratie draait in een eigen transactie. Mislukt er één, dan blijft het schema op de vorige versie
  staan en stopt de `migrate`-container met een foutcode; `api` en `worker` starten dan niet
  (`depends_on: service_completed_successfully`).
- Tabel `schema_migrations` legt versie, naam, checksum en tijdstip vast. Een al toegepaste migratie waarvan
  het bestand later is gewijzigd, levert een waarschuwing in het log op (checksum wijkt af) in plaats van
  stilletjes te divergeren.
- Een PostgreSQL advisory lock zorgt dat `migrate`, `api` en `worker` nooit tegelijk migreren.
- **Schemaversie** = hoogste toegepaste migratienummer. Zichtbaar in Systeem (in het menu: Back-up & updates) →
  kaart MSO-core → Schemaversie, en in de uitvoer van `mso migrate`.

## Wanneer draaien migraties?

1. De `migrate`-container (eenmalig bij elke start van de app) voert `mso migrate` uit zodra PostgreSQL gezond
   is. In de alles-in-één-image (Custom App) doet de supervisor hetzelfde vóórdat API, worker en web starten.
2. `api` en `worker` roepen bij het opstarten ook `migrate_up` aan (no-op als alles al is toegepast). Dat
   maakt een handmatige `docker run` zonder `migrate`-container veilig.

**Back-up vóór upgrade (sinds 1.6.0).** Zijn er openstaande migraties en bestaat er al een database, dan maakt MSO
eerst een back-up van soort `pre-upgrade`. In Systeem → Back-ups staan de drie meest recente onder "Vóór upgrade".
De startprocessen delen een upgradeslot. Onder dat slot controleert MSO de SHA-256 en de leesbaarheid van het
PostgreSQL-archief. Bij ruimtegebrek, een dumpfout of een onbruikbaar archief stopt de upgrade vóór de volgende
migratie. De oorzaak staat in het app-log. Een nieuwe installatie kan direct starten.
`mso migrate --no-backup` controleert het bestaande archief voor deze upgrade.

## Commando's

> **Voor ontwikkelaars, niet voor de gebruiker.** De commando's hieronder vragen een terminal op de NAS. Een gebruiker
> werkt zonder terminal: updaten en terugdraaien gaan via het Custom App-formulier en een ZFS-snapshot
> ([TRUENAS_CUSTOM_APP.md](TRUENAS_CUSTOM_APP.md) → *Updaten en terugdraaien*). Een knop om het schema vanuit de
> MSO-UI terug te zetten is gepland (P3-13). De varianten voor "losse containers (YAML)" gelden alleen voor de
> ontwikkelstack (TRUENAS_INSTALL.md); dat is geen installatiemethode.

In een draaiende core-container (de wrapper `mso` leest het databasewachtwoord uit
`/run/mso-db/password`):

```sh
API=$(sudo docker ps --format '{{.Names}}' | grep -E 'media-source-optimizer.*api' | head -1)
sudo docker exec "$API" mso migrate                 # back-up vóór upgrade + alle openstaande migraties toepassen
sudo docker exec "$API" mso migrate --to 0105       # toepassen tot en met versie 105
sudo docker exec "$API" mso migrate --down 0002     # alles BOVEN versie 2 terugdraaien (nieuwste eerst)
```

In de alles-in-één-image (Custom App) is er één container; daar draai je hetzelfde als gebruiker 568:

```sh
C=$(sudo docker ps --format '{{.Names}}' | grep media-source-optimizer | head -1)
sudo docker exec -u 568 "$C" mso migrate --down 501
```

`--down N` draait elke toegepaste migratie met een versie groter dan `N` terug, in aflopende volgorde, elk in
een eigen transactie. Ontbreekt het down-script van een toegepaste migratie (bijvoorbeeld omdat je een
ouder image gebruikt), dan stopt het commando met een fout en blijft de rest ongewijzigd.

## Updaten via TrueNAS

1. Stop MSO en maak in **Datasets → Snapshots** een ZFS-snapshot van de volledige APPDATA-dataset. Bewaar
   de naam van de snapshot en de vorige imageversie met digest.
2. Open onder **Apps** het bestaande Custom App-formulier met **Edit**. Kies de nieuwe versie met de
   bijbehorende image-digest, sla op met **Update** en start de app.
3. Tijdens een schema-update zie je in het app-log eerst de bevestiging van de pre-upgrade-back-up en daarna
   de toegepaste migraties. Na de start staat het archief als **Vóór upgrade** onder **Systeem → Back-ups**.
   Controleer de schemaversie onder **Systeem → MSO-core**. Bij een onbruikbaar terugvalarchief stopt de start
   vóór de migratie. Het app-log geeft de oorzaak.

## 1.5.0 → 1.6.0

Volg het updatepad hierboven. De release 1.6.0 maakt eerst de back-up **Vóór upgrade** op schema 501 en past
daarna de migraties 0600–0607 toe. Latere images passen ook hun volgende migraties toe.

**Wat de migraties doen:**

| Migratie | Wat | Bij terugdraaien (`--down 501`) |
|---|---|---|
| `0600_v160_base` | Serverbewaking: wanneer een server echt hersteld is en wanneer hij voor het laatst getest werd. Nieuwe tabel `change_overrides` (waarden die jij zelf terugzette en die MSO een tijd met rust laat). | Kolommen en tabel weg; MSO vergeet je eigen keuzes. |
| `0601_v160_usenet_cause` | Corrigeert de opgeslagen artikeltellers in `usenet_server_stats`: SABnzbd leverde het aantal *mislukte* artikelen, nu staat er het aantal *gevonden* (Eweka 0,4 % → 99,6 %). Nieuwe tabel `usenet_server_coverage` (wanneer een server voor het eerst aan stond: dekkingswinst voor opnieuw proberen). | De tellers krijgen weer de 1.5.0-betekenis (dezelfde formule werkt beide kanten op); tabel weg. |
| `0602_v160_server_tests` | Volgende test, aantal mislukte tests en serverinstellingen-vingerafdruk per bewaakte server; tabellen `usenet_server_tests` (testresultaten, 30 dagen) en `usenet_server_accounts` (vervaldatum, quotum); beginstand van een blok bij de provider. | Testgeschiedenis, vervaldata en blokbasis weg. |
| `0603` | Bestaat niet: de categoriediagnose had geen schemawijziging nodig. | — |
| `0604_v160_goals` | Kolom `download_outcomes.failed_at` (het moment van mislukken), voor bestaande mislukte downloads aangevuld uit de geschiedenis, plus een index; tabel `goal_snapshots` (één stand per dag voor de doelenkaart). | Kolom en dagstanden weg. |
| `0605_v160_change_safety` | Wijzigingsstatus `applying` ("Bezig met toepassen"), starttijd van het toepassen en `applied_auto` (wie onbeheerd toepaste; 1.5.0-wijzigingen van Autopilot worden zo gemarkeerd); tabel `automation_failures` (afkoeling). | Een wijziging die nog op `applying` staat wordt `failed` (met uitleg); kolommen en tabel weg. |
| `0606_v160_maintenance` | Tabel `observations_hourly` (uursamenvattingen van metingen), indexen voor het opruimen, en voor de koppelingsbewaking `connectors.status_since`, `last_ok_at`, `failure_kind` en `jobs.connector_id`. | Uursamenvattingen weg: metingen ouder dan het ruwe venster (standaard 30 dagen) zijn dan niet meer terug te zien. |
| `0607_footprint` | Tabel `change_footprint` (de Voetafdruk; MSO bouwt hem elk uur opnieuw op). | Tabel weg. |

**Wat er na de start vanzelf gebeurt:**
- Een eenmalige taak beoordeelt de mislukte downloads in de SABnzbd-historie (en bij SABnzbd ≥ 4 het archief)
  opnieuw met het verwerkingslog (audit `usenet.reclassified`). Downloads die niet meer in SABnzbd staan, houden hun oude
  oorzaak.
- Servers die 1.5.0 had opgegeven (`given_up`), worden weer met SABnzbd's servertest getest.
- De rem op opnieuw proberen telt alleen pogingen van ná de upgrade.
- De doelenkaart verschijnt. Ging Autopilot al in 1.5.0 aan, dan begint de proefperiode op die dag en leidt MSO de
  startwaarde af uit de geschiedenis. Wil je 1.6.0 vanaf de upgradedag beoordelen, vul die datum dan in bij
  Instellingen → Automatisering → Start proefperiode (doelenkaart).
- Had je bij een blokprovider al een blokgrootte ingevuld, sla die dan één keer opnieuw op (bij een deels gebruikt blok:
  het restant). Tot dan toont MSO het verbruik zonder voorspelling of herinnering.
- In "Wacht op jou" kunnen nieuwe items verschijnen, bijvoorbeeld "SABnzbd kan downloads niet uitpakken", een
  voorstel voor een ontbrekende SABnzbd-categorie of een vervalherinnering.

## Terug naar 1.5.0 via TrueNAS

**Zet altijd de back-up van vóór de upgrade terug voordat je 1.5.0 start. Start de 1.5.0-image nooit op
het nieuwere schema.** 1.5.0 heeft geen schemaweigering en kan op de bijgewerkte database starten.
Een image met schemabewaking meldt in het app-log: „database is van een nieuwere MSO-versie; zet de
bijpassende snapshot/back-up terug”.

1. Stop MSO onder **Apps**.
2. Open **Datasets → Snapshots** en kies **Rollback** bij de APPDATA-snapshot van vóór de upgrade.
   Die snapshot zet ook de lokale bestanden terug die nodig zijn om versleutelde koppelingsgeheimen te lezen.
   Wijzigingen in MSO sinds de snapshot gaan verloren.
3. Open het Custom App-formulier met **Edit**, kies de vorige imageversie met de bijbehorende digest en
   sla op met **Update**. Start de app pas nadat de snapshot is teruggezet.
4. Log opnieuw in en controleer onder **Systeem → MSO-core** dat de schemaversie voor 1.5.0 weer 501 is.

De automatische databaseback-up **Vóór upgrade** kan ook dienen voor herstel naar een lege,
afzonderlijke 1.5.0-installatie. De containerproef herstelt dat archief met `pg_restore` vóór de eerste
appstart. Voor terugval van de bestaande Custom App gebruik je de volledige dataset-snapshot.

## Back-ups en schemaversies

- Elke back-up legt de schemaversie vast (kolom `schema_version`, zichtbaar in de back-uplijst).
- Herstel vervangt de complete database, inclusief `schema_migrations`. Herstel je in de app (Systeem →
  Back-ups) een back-up met een **oudere** schemaversie, bijvoorbeeld de `pre-upgrade`-back-up, dan zet MSO
  eerst het schema terug naar de versie van de back-up (met de down-scripts van het draaiende image), herstelt
  de back-up en werkt het schema daarna meteen weer bij. Een herstart is niet nodig. Gegevens in tabellen van
  nieuwere functies gaan daarbij verloren; ze staan in de vangnet-back-up (`pre-restore`) die MSO vlak vóór het
  herstel maakt.
- Een back-up met een **nieuwere** schemaversie dan het draaiende image kent, weigert MSO (vanaf 1.6.0) te
  herstellen; upgrade eerst het image. **Let op:** 1.5.0 en ouder kennen die controle nog niet. Herstel in 1.5.0 dus
  nooit een back-up die met 1.6.0 is gemaakt (schemaversie hoger dan 501): de oude versie draait dan op een schema dat
  ze niet kent. Gebruik in 1.5.0 alleen de back-up "pre-upgrade" of een back-up uit 1.5.0 zelf.
- Herstel je een oudere dump **met de hand** (`pg_restore`, zie OPERATIONS.md), draai dan eerst met het huidige
  image `mso migrate --down <schemaversie van de dump>`. `pg_restore --clean` verwijdert alleen wat in de dump
  staat; tabellen van nieuwere migraties blijven anders staan en laten de volgende `migrate` vastlopen.
- Wil je de gegevens van vóór de upgrade terug en de huidige image houden, herstel de `pre-upgrade`-back-up in
  de huidige app. Voor terugval naar 1.5.0 volg je de stappen onder *Terug naar 1.5.0 via TrueNAS*.
- De bedrijfsstand hoort niet bij een herstel: modus (alleen-lezen/beheer/Autopilot), pauze en noodrem blijven
  zoals ze vlak vóór het herstel waren. Een wijziging die werd toegepast op het moment van de dump, krijgt de
  status "mislukt" zonder dat MSO iets terugzet; je ziet er één item voor bij "Wacht op jou".
- Koppelingsgeheimen zitten standaard niet in een back-up. Bij herstel van zo'n back-up houdt MSO de huidige
  (versleutelde) geheimen aan. De vault-sleutel (`APPDATA/secrets/vault.key`) zit nooit in een back-up.
- Sessies worden nooit geback-upt; na een herstel log je opnieuw in.

## Compatibiliteitsregels voor nieuwe migraties (voor ontwikkelaars)

1. **Nooit** een toegepaste migratie wijzigen; voeg een nieuwe toe.
2. Elke `up` heeft een `down` die de `up` volledig terugdraait. De testsuite controleert up → down → up.
3. Bij voorkeur additief (expand/contract): eerst nieuwe kolom/tabel toevoegen en vullen, pas in een latere
   release de oude verwijderen. Dit beperkt het aantal gegevens dat een migratie moet herschrijven.
4. Nieuwe `NOT NULL`-kolommen krijgen een default, zodat bestaande rijen geldig blijven.
5. Geen geheimen in migraties; geheimen horen in de vault (`secrets`-tabel, versleuteld).
6. Langdurige datamigraties (grote tabellen) horen in een worker-job, niet in een SQL-migratie die de start
   blokkeert.
