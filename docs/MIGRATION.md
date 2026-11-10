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
eerst een back-up van soort `pre-upgrade` (zichtbaar als "Vóór upgrade" in Systeem → Back-ups; de nieuwste 3 blijven
bewaard). Dat gebeurt één keer per upgrade, ook als `migrate`, `api` en `worker` tegelijk starten. Is er te weinig
vrije ruimte of mislukt de dump, dan schrijft MSO een duidelijke logregel ("… MSO start gewoon door") en migreert het
toch; na de start staat het in de audit (`backup.pre_upgrade_skipped` / `backup.pre_upgrade_failed`) en bij te weinig
ruimte ook in "Wacht op jou". Een nieuwe installatie krijgt geen back-up (er is nog niets te bewaren).
`mso migrate --no-backup` slaat deze stap over.

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

## Upgradepad

1. ZFS-snapshot van de APPDATA-dataset (aanbevolen). Een back-up maakt MSO sinds 1.6.0 zelf vlak vóór de migraties
   (zie hierboven); een eigen back-up (Systeem → Back-ups) mag natuurlijk ook.
2. Schemaversie noteren.
3. Image-tag(s) naar de nieuwe versie zetten en opslaan (Custom App: TRUENAS_CUSTOM_APP.md → Updaten; YAML:
   TRUENAS_INSTALL.md → Updaten).
4. MSO maakt de back-up vóór upgrade, `migrate` past de nieuwe migraties toe; daarna starten `api`, `worker` en `web`.

Versies overslaan (bijv. 1.0 → 1.3) is toegestaan: alle tussenliggende migraties zitten in het nieuwe image en
worden op volgorde toegepast. Tenzij de CHANGELOG van een versie anders vermeldt.

## 1.5.0 → 1.6.0

**Wat je doet:** alleen de tag naar `1.6.0` zetten. Er is geen handmatige stap nodig. MSO maakt eerst de back-up
"Vóór upgrade" (schemaversie 501) en past daarna de migraties 0600–0607 toe. De schemaversie wordt 607.

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

## Rollback

De down-scripts van nieuwe migraties zitten alleen in het **nieuwe** image. Daarom:

1. Stop `worker` (en bij voorkeur `web`), zodat er niets schrijft. In de alles-in-één-image kan dat niet apart: doe
   stap 2 en 3 dan direct na elkaar.
2. Draai met het nieuwe image: `mso migrate --down <schemaversie van vóór de upgrade>`.
3. Zet de oude image-tags terug en start de app.

Let op:

- Een down-migratie verwijdert kolommen/tabellen die de nieuwe versie heeft toegevoegd, **inclusief de data
  daarin**. Wat je na de upgrade in nieuwe functies hebt vastgelegd, gaat daarbij verloren.
- Start je het oude image zonder eerst `--down` te draaien, dan ziet de oude versie een nieuwer schema. Het
  oude image past dan niets toe (alle bekende migraties zijn al toegepast), maar kan vastlopen op gewijzigde
  structuren. Doe dit niet.
- De betrouwbaarste terugval bij twijfel (of na een mislukte migratie) is de ZFS-snapshot-rollback van de
  APPDATA-dataset met de app gestopt, gevolgd door het terugzetten van de oude tags.

### Terug naar 1.5.0

Kies één van twee manieren.

**A. Schema terugzetten (je gegevens blijven).** Doe dit met het 1.6.0-image, vóór je de tag terugzet.

Alles-in-één (Custom App):

```sh
C=$(sudo docker ps --format '{{.Names}}' | grep media-source-optimizer | head -1)
sudo docker exec -u 568 "$C" mso migrate --down 501
```

Zet daarna meteen de tag terug: Apps → media-source-optimizer → **Edit** → Tag `1.5.0` → **Update**. Herstart MSO
tussendoor niet met het 1.6.0-image: bij elke start past 1.6.0 zijn migraties gewoon opnieuw toe.

Losse containers (YAML):

```sh
API=$(sudo docker ps --format '{{.Names}}' | grep -E 'media-source-optimizer.*api' | head -1)
WORKER=$(sudo docker ps --format '{{.Names}}' | grep -E 'media-source-optimizer.*worker' | head -1)
sudo docker stop "$WORKER"
sudo docker exec "$API" mso migrate --down 501
```

Zet daarna in **Edit** de tags `MSO_CORE_IMAGE` en `MSO_WEB_IMAGE` terug op `1.5.0` en **Save**.

Controleer in Systeem (Back-up & updates) → MSO-core dat de schemaversie 501 is. `--down 501` draait 0607 tot en met
0600 terug; wat daarbij verloren gaat, staat in de tabel hierboven. Wil je precies de stand van vóór de upgrade terug,
herstel dan daarna in 1.5.0 de back-up "pre-upgrade" (Systeem → Back-ups); alles wat sinds de upgrade gebeurde, gaat dan
verloren.

**B. ZFS-snapshot** (betrouwbaarst, ook na een mislukte migratie): stop de app, zet de APPDATA-dataset terug naar de
snapshot van vóór de upgrade, zet de tag op `1.5.0` en start de app.

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
- Terug naar 1.5.0 met de `pre-upgrade`-back-up: eerst `mso migrate --down 501` met het 1.6.0-image en de
  1.5.0-tag (zie *Terug naar 1.5.0* hierboven), daarna de `pre-upgrade`-dump herstellen in 1.5.0. Wil je alleen de
  gegevens van vóór de upgrade terug en 1.6.0 houden, herstel de `pre-upgrade`-back-up dan gewoon in 1.6.0.
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
   release de oude verwijderen. Zo kan de vorige release tijdens een rollback nog met het schema werken.
4. Nieuwe `NOT NULL`-kolommen krijgen een default, zodat bestaande rijen geldig blijven.
5. Geen geheimen in migraties; geheimen horen in de vault (`secrets`-tabel, versleuteld).
6. Langdurige datamigraties (grote tabellen) horen in een worker-job, niet in een SQL-migratie die de start
   blokkeert.
