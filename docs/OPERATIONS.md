# Beheer en operatie

Dagelijks beheer van een draaiende Media Source Optimizer-installatie (MSO): gezondheid, logs, back-ups,
herstel, taakherstel en resources. Installatie: [TRUENAS_INSTALL.md](TRUENAS_INSTALL.md); schema-upgrades:
[MIGRATION.md](MIGRATION.md).

Containernamen krijgen van TrueNAS een voorvoegsel. In de voorbeelden hieronder:

```sh
c() { sudo docker ps -a --format '{{.Names}}' | grep -E "media-source-optimizer.*-$1-" | head -1; }
API=$(c api); WORKER=$(c worker); PG=$(c postgres); WEB=$(c web)
```

## Gezondheid

| Waar | Wat | Gezond |
|---|---|---|
| `docker ps` | container-healthchecks | `postgres`, `api`, `worker`, `web`: `(healthy)`; `init`, `migrate`: `Exited (0)` |
| `postgres` | `pg_isready` elke 10 s | accepting connections |
| `api` | `GET /healthz` (pingt de database) elke 30 s | HTTP 200 `{"status":"ok"}` |
| `worker` | hartslag in `system_state` jonger dan 90 s (de worker schrijft elke 20 s) | exit 0 |
| `web` | `GET /login` elke 30 s | HTTP 200 |
| UI → Systeem | `GET /api/v1/system/health`: database, worker, vrije ruimte APPDATA/WORK, elke ingeschakelde koppeling | `healthy`; `degraded` als bijv. een koppeling een waarschuwing heeft of de vrije ruimte onder `MSO_DISK_RESERVE_GB` (standaard 2 GB) zakt |

Handmatig: `sudo docker exec "$API" mso-healthcheck api; echo $?` en idem met `"$WORKER" mso-healthcheck worker`.

Afhankelijkheden bij het starten: `init` → `postgres` (gezond) → `migrate` (exit 0) → `api` + `worker` → `web`
(na gezonde `api`). Bij een herstart van alleen PostgreSQL wachten `api`/`worker` maximaal 60 s
(`MSO_DB_WAIT_S`) op de database bij hun eigen start. Herstart je alleen `postgres` terwijl `api`/`worker`
doorlopen, controleer dan Systeem → Gezondheid; herstart bij databasefouten ook `api` en `worker` (het
herstel van bestaande pool-verbindingen na een losse PostgreSQL-herstart is niet apart getest; de test dekt
`docker compose restart` en `down`/`up` van de hele stack). Alle diensten hebben `restart: unless-stopped`: een gecrasht proces wordt door Docker herstart.

## Logs

- `sudo docker logs --tail 200 -f "$API"` (idem `$WORKER`, `$WEB`, `$PG`). Of in TrueNAS: Apps → app → Logs.
- Rotatie: json-file, max. 20 MB × 5 bestanden per container (`logging` in compose.yaml).
- Alle Python-logregels lopen door een redactiefilter: API-sleutels, wachtwoorden, passkeys, cookies, tokens en
  inloggegevens in URL's worden vervangen door `***`. In de container-stacktest
  (`tests/stack/smoke.py`) wordt gecontroleerd dat het beheerderswachtwoord, het databasewachtwoord en alle
  koppelingsgeheimen in géén enkele MSO-containerlog voorkomen.
- Elke API-fout heeft een `correlation_id`, ook zichtbaar in de UI; zoek daarop in de `api`-log.
- Functionele gebeurtenissen (inloggen, wijzigingen, back-ups, koppelingstests) staan in het auditlogboek:
  UI → Logs, of `GET /api/v1/audit`.
- Normaal bij herstart/stop van de app: PostgreSQL logt per open verbinding
  `FATAL: terminating connection due to administrator command`. Dat is geen fout.

## Back-ups

- **Automatisch:** worker-job `backup.create` elke 24 uur (instelling `backup.auto_daily`, standaard aan);
  bewaard: de nieuwste `backup.keep` (standaard 14) automatische + handmatige back-ups. Vangnet-back-ups
  (`pre-restore`) worden niet automatisch opgeruimd.
- **Handmatig:** UI → Systeem → Back-ups, of `sudo docker exec "$API" mso backup` (drukt de bestandsnaam af).
- **Formaat:** `APPDATA/backups/mso-<UTC-tijd>-<soort>.dump` (`pg_dump -Fc`, PostgreSQL 16-client in het
  core-image) plus `<naam>.sha256`. Sessies zitten er nooit in; versleutelde koppelingsgeheimen alleen bij de
  expliciete keuze "inclusief geheimen". De vault-sleutel zit er nooit in.
- **Buiten de server bewaren:** neem de dataset `apps/media-source-optimizer` (inclusief `secrets/vault.key`
  en `db/password`) op in je ZFS-snapshot- en replicatietaken. Back-ups in dezelfde dataset beschermen niet
  tegen verlies van de pool.

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

**Noodherstel zonder werkende UI** (bijv. database corrupt): stop de app, zet de APPDATA-dataset terug met
`zfs rollback` naar een snapshot, start de app. Of, met draaiende `postgres` en gestopte `api`/`worker`:

```sh
sudo docker exec -i "$PG" pg_restore -U mso -d mso --clean --if-exists --no-owner --single-transaction \
  < /mnt/tank/apps/media-source-optimizer/backups/<bestand>.dump
```

Is de dump van een oudere schemaversie (kolom `schema_version` in de back-uplijst, of de naam
`…-pre-upgrade.dump`), draai dan vóór de `pg_restore` met het huidige image
`mso migrate --down <schemaversie van de dump>`. `--clean` verwijdert alleen wat in de dump staat; tabellen van
nieuwere migraties blijven anders staan en de volgende start (`migrate`) loopt erop vast.

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

Getest in de container-stack: (1) een taak die als `running` met verlopen hartslag achterbleef ("gecrashte
worker") werd na het starten van de worker hervat en afgerond; (2) het worker-proces werd met `SIGKILL`
gedood, Docker herstartte de container (RestartCount 0 → 1), de worker werd weer gezond en voerde een nieuwe
taak uit.

## Resources

### Ingestelde limieten (compose.yaml, aanpasbaar via variabelen)

| Container | CPU | Geheugen | PIDs |
|---|---|---|---|
| postgres | 2 (`MSO_PG_CPUS`) | 1 GB (`MSO_PG_MEM`) | 256 |
| api | 2 (`MSO_API_CPUS`) | 768 MB (`MSO_API_MEM`) | 256 |
| worker | 2 (`MSO_WORKER_CPUS`) | 768 MB (`MSO_WORKER_MEM`) | 256 |
| web | 1 (`MSO_WEB_CPUS`) | 512 MB (`MSO_WEB_MEM`) | 128 |
| migrate / init | 1 / 0,5 | 256 / 128 MB | 64 / 32 |

Schijf: APPDATA groeit met de database en de back-ups (één dump was in de test ±125 kB met een lege
installatie; reken op groei met indexer-cache, uitkomsten en audit). WORK is voor cache en zoek-/header-index en
moet een **ZFS-dataset-quota** hebben (installatie: `zfs create -o quota=50G …`; aanpassen:
`sudo zfs set quota=100G tank/apps/media-source-optimizer-work`). MSO meldt `degraded` zodra de vrije ruimte op
APPDATA of WORK onder `MSO_DISK_RESERVE_GB` zakt.

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

**Afgeleide ondergrens** (alleen op basis van deze meting): de stack draait met ruim onder 512 MiB RAM en minder
dan anderhalve CPU-kern bij deze belasting. Aanbevolen minimum voor een echte installatie: **1 GiB RAM vrij en
2 CPU-threads**, omdat echte zoekopdrachten over vele indexers, een gevulde database en een eventuele
NNTP-header-index meer geheugen en CPU vragen dan deze lege testinstallatie. Dit is **niet** gemeten op
TrueNAS-hardware of met echte data; herhaal de meting op je eigen systeem (HARDWARE_ACCEPTANCE 8.1):

```sh
sudo docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}' | grep media-source-optimizer
```

## Netwerk en beveiliging in bedrijf

- Alleen `web` publiceert een poort (standaard `0.0.0.0:38081`). `api` is alleen bereikbaar voor `web` en
  `worker`; PostgreSQL alleen op het interne `db`-netwerk zonder uitgaand verkeer.
- `api` vertrouwt `X-Forwarded-For` van elke afzender in het interne netwerk (`MSO_FORWARDED_ALLOW_IPS=*`),
  zodat rate limits per echte client gelden. Omdat `api` niet gepubliceerd is, kan alleen `web` die header
  zetten; `web` geeft een door de client meegestuurde `X-Forwarded-For` wel door, dus een LAN-client kan zijn
  IP-adres voor de IP-rate-limit vervalsen (bekend aandachtspunt; de limiet per gebruikersnaam blijft gelden).
- Uitgaande verbindingen van koppelingen gaan door een SSRF-controle: alleen http(s), geen inloggegevens in
  de URL, nooit link-local/metadata (169.254.0.0/16 e.d.), geen loopback. Getest in de container-stack
  (6 onveilige URL's → 422).

## Probleemoplossing

| Symptoom | Oorzaak / actie |
|---|---|
| `init` stopt met fout "is niet gemount" | Pad in de YAML bestaat niet of is verkeerd gespeld; controleer `APPDATA`/`WORK`. |
| `init`-log: `LET OP: … controleer de ACL` | Map is van een andere eigenaar dan 568; zet eigenaar `apps` (568) op de dataset. |
| `postgres` start niet: "data directory has wrong ownership" | `APPDATA/postgres` niet van 568:568 of niet modus 0700; `init` herstelt dit bij lege of root-eigen mappen, anders handmatig `chown -R 568:568`. |
| `migrate` `Exited (1)` | Zie `docker logs` van migrate; schema bleef op de vorige versie. Bij een update: terug naar de vorige tag (MIGRATION.md). |
| `api` unhealthy, log "databasewachtwoordbestand … ontbreekt" | `APPDATA/db/password` weg; herstel uit snapshot. Een nieuw wachtwoord genereren werkt alleen met een nieuwe (lege) database. |
| Koppelingstest "Loopback-adressen zijn uitgeschakeld" | Gebruik het LAN-IP van TrueNAS i.p.v. `localhost`. |
| Prowlarr-downloadlinks naar `http://api:8200/…` | Zet `MSO_PUBLIC_URL` of instelling `newznab.public_url` (TRUENAS_INSTALL.md). |
| Opgeslagen geheimen onleesbaar na verhuizing | `APPDATA/secrets/vault.key` niet meegekopieerd; kopieer de sleutel of voer de geheimen opnieuw in. |
