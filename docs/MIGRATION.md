# Schemamigraties, upgrades en rollback

Dit document beschrijft hoe het databaseschema van Media Source Optimizer (MSO) wordt beheerd, hoe je
upgradet en terugdraait, en welke compatibiliteitsregels gelden. Het betreft alleen de eigen
PostgreSQL-database van MSO; MSO migreert of wijzigt nooit databases of configuratie van MediaGuard,
Sonarr/Radarr, Prowlarr, SABnzbd of qBittorrent.

## Hoe migraties werken (`apps/core/mso/db.py`)

- Bestanden: `apps/core/mso/migrations/NNNN_naam.sql`, elk met een `-- migrate:up`- en een
  `-- migrate:down`-sectie. De down-sectie moet de up-sectie volledig terugdraaien.
- Nummering per onderdeel (voorkomt botsingen): 0001–0099 fundament, 0100–0199 indexers/zoeken,
  0200–0299 Usenet, 0300–0399 torrents, 0400–0499 MediaGuard/herstel, 0500–0599 kosten/overzicht/automatisering.
  Migraties worden in numerieke volgorde toegepast.
- Elke migratie draait in een eigen transactie. Mislukt er één, dan blijft het schema op de vorige versie
  staan en stopt de `migrate`-container met een foutcode; `api` en `worker` starten dan niet
  (`depends_on: service_completed_successfully`).
- Tabel `schema_migrations` legt versie, naam, checksum en tijdstip vast. Een al toegepaste migratie waarvan
  het bestand later is gewijzigd, levert een waarschuwing in het log op (checksum wijkt af) in plaats van
  stilletjes te divergeren.
- Een PostgreSQL advisory lock zorgt dat `migrate`, `api` en `worker` nooit tegelijk migreren.
- **Schemaversie** = hoogste toegepaste migratienummer. Zichtbaar in Systeem → Info (`schema_version`) en in
  de uitvoer van `mso migrate`.

## Wanneer draaien migraties?

1. De `migrate`-container (eenmalig bij elke start van de app) voert `mso migrate` uit zodra PostgreSQL gezond
   is.
2. `api` en `worker` roepen bij het opstarten ook `migrate_up` aan (no-op als alles al is toegepast). Dat
   maakt een handmatige `docker run` zonder `migrate`-container veilig.

## Commando's

In een draaiende core-container (de wrapper `mso` leest het databasewachtwoord uit
`/run/mso-db/password`):

```sh
API=$(sudo docker ps --format '{{.Names}}' | grep -E 'media-source-optimizer.*api' | head -1)
sudo docker exec "$API" mso migrate                 # alle openstaande migraties toepassen
sudo docker exec "$API" mso migrate --to 0105       # toepassen tot en met versie 105
sudo docker exec "$API" mso migrate --down 0002     # alles BOVEN versie 2 terugdraaien (nieuwste eerst)
```

`--down N` draait elke toegepaste migratie met een versie groter dan `N` terug, in aflopende volgorde, elk in
een eigen transactie. Ontbreekt het down-script van een toegepaste migratie (bijvoorbeeld omdat je een
ouder image gebruikt), dan stopt het commando met een fout en blijft de rest ongewijzigd.

## Upgradepad

1. Back-up maken (Systeem → Back-ups) en ZFS-snapshot van de APPDATA-dataset.
2. Schemaversie noteren.
3. Image-tags in de app-YAML naar de nieuwe versie zetten en opslaan (zie TRUENAS_INSTALL.md → Updaten).
4. `migrate` past de nieuwe migraties toe; daarna starten `api`, `worker` en `web`.

Versies overslaan (bijv. 1.0 → 1.3) is toegestaan: alle tussenliggende migraties zitten in het nieuwe image en
worden op volgorde toegepast. Tenzij de CHANGELOG van een versie anders vermeldt.

## Rollback

De down-scripts van nieuwe migraties zitten alleen in het **nieuwe** image. Daarom:

1. Stop `worker` (en bij voorkeur `web`), zodat er niets schrijft.
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

## Back-ups en schemaversies

- Elke back-up legt de schemaversie vast (kolom `schema_version`, zichtbaar in de back-uplijst).
- Herstel vervangt de complete database, inclusief `schema_migrations`. Herstel je een back-up met een
  **oudere** schemaversie in een nieuwere MSO-versie, herstart dan de app: `migrate` brengt het schema weer
  naar de huidige versie.
- Een back-up met een **nieuwere** schemaversie dan het draaiende image kent, herstel je niet in een oudere
  versie; upgrade eerst het image.
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
