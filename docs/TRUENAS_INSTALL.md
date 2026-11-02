# Media Source Optimizer installeren op TrueNAS SCALE

Deze handleiding installeert Media Source Optimizer (MSO) als eigen app op TrueNAS SCALE via
**Apps → Discover Apps → ⋮ → Install via YAML**, met het bestand `deploy/truenas/compose.yaml`.

> **Verificatiestatus.** De compose-stack is gebouwd en getest in een Linux/Docker-sandbox (zie
> `tests/stack/results/smoke-report.md` en docs/OPERATIONS.md). Een installatie op echte TrueNAS-hardware is
> **niet** uitgevoerd. De TrueNAS-specifieke stappen hieronder (menu's, dataset-ACL's, containernamen) volgen
> de gangbare werkwijze van TrueNAS SCALE 24.10+ maar zijn **niet geverifieerd**; zie
> [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md) voor de controlelijst.

## Wat er draait

| Container | Rol | Netwerk | Draait als |
|---|---|---|---|
| `init` | eenmalig per start: mappen + eigenaar 568:568 + databasewachtwoord genereren | geen | root met alleen `CHOWN`, `DAC_OVERRIDE`, `FOWNER` |
| `postgres` | PostgreSQL 16.15 (gepind op tag + digest) | `db` (intern, geen uitgaand verkeer) | 568:568, géén capabilities |
| `migrate` | eenmalig: databaseschema bijwerken | `db` | 568:568 |
| `api` | core-API, Newznab/Torznab-feeds (niet gepubliceerd) | `db`, `app` | 568:568 |
| `worker` | achtergrondtaken (back-ups, polling, herstel) | `db`, `app` | 568:568 |
| `web` | gebruikersinterface + same-origin proxy (`/api/v1`, `/newznab`, `/torznab` → `api`) | `app`, poort **30081** | 568:568 |

Alle containers: `no-new-privileges`, `cap_drop: ALL`, read-only root-filesystem met tmpfs voor `/tmp`, vaste
CPU-/geheugen-/PID-limieten, logrotatie (20 MB × 5). Geen Docker-socket, geen host-netwerk, geen privileged
containers. Alleen `web` publiceert een poort.

**Geheimen.** In de YAML staan géén API-sleutels of wachtwoorden. Het databasewachtwoord genereert `init`
(`APPDATA/db/password`, 64 hex-tekens, modus 0400). API-sleutels en wachtwoorden van Prowlarr, SABnzbd,
qBittorrent, MediaGuard en providers voer je in de setupwizard in; MSO bewaart ze AES-256-GCM-versleuteld in
de database met de sleutel `APPDATA/secrets/vault.key` (wordt bij de eerste start aangemaakt, modus 0600).

## Benodigd

- TrueNAS SCALE 24.10 of nieuwer (Docker-gebaseerde apps met "Install via YAML").
- Een pool (hieronder `tank`; vervang dit overal door de naam van jouw pool).
- Netwerktoegang vanaf TrueNAS naar de images op `ghcr.io` (of zelf bouwen, zie onderaan).
- Resources (gemeten in de sandbox, zie OPERATIONS.md): reken minimaal op 1 GB RAM vrij en 2 CPU-threads
  voor de hele stack; de ingestelde limieten zijn bovengrenzen, geen reservering.

## Installatie in één blok

> Het blok haalt `compose.yaml` van release-tag `v1.0.0` en de YAML verwijst naar
> `ghcr.io/lvos04/media-source-optimizer-{core,web}:1.0.0`. Die tag en images ontstaan pas wanneer de release
> is gepubliceerd (CI-job `images` pusht bij een `v*`-tag). Bestaan ze nog niet, gebruik dan het bestand uit
> je eigen checkout en bouw de images zelf (onderaan).

Voer dit uit in **System → Shell** van TrueNAS (of via SSH) als je admin-gebruiker. `sudo` vraagt één keer om je
wachtwoord, alleen voor het aanmaken van de datasets en het zetten van de eigenaar. Pas de drie variabelen
bovenaan aan.

```sh
# ---- aanpassen --------------------------------------------------------------------------------
POOL=tank                 # naam van je pool
WORK_QUOTA=50G            # bovengrens voor cache/zoekindex (dataset-quota)
TRUENAS_IP=192.168.1.10   # LAN-adres van je TrueNAS (voor Prowlarr-links)
# -----------------------------------------------------------------------------------------------
sudo zfs create -p "$POOL/apps/media-source-optimizer"
sudo zfs create -p -o quota="$WORK_QUOTA" "$POOL/apps/media-source-optimizer-work"
sudo chown 568:568 "/mnt/$POOL/apps/media-source-optimizer" "/mnt/$POOL/apps/media-source-optimizer-work"
sudo chmod 750 "/mnt/$POOL/apps/media-source-optimizer" "/mnt/$POOL/apps/media-source-optimizer-work"
zfs get -H -o name,property,value quota "$POOL/apps/media-source-optimizer-work"
ls -ldn "/mnt/$POOL/apps/media-source-optimizer" "/mnt/$POOL/apps/media-source-optimizer-work"
# Kopieer compose.yaml en zet je pool en IP erin; plak de uitvoer daarna in "Install via YAML":
curl -fsSL https://raw.githubusercontent.com/lvos04/media-source-optimizer/v1.0.0/deploy/truenas/compose.yaml \
  | sed -e "s#/mnt/tank/#/mnt/$POOL/#g" -e "s#MSO_PUBLIC_URL:-}#MSO_PUBLIC_URL:-http://$TRUENAS_IP:30081}#" \
  > /tmp/mso-compose.yaml
cat /tmp/mso-compose.yaml
```

Verwachte uitvoer van de controles: de quota-regel toont `quota 50G` en beide mappen tonen eigenaar `568 568`.

Liever via de webinterface? **Datasets → Add Dataset** onder `tank/apps`: naam `media-source-optimizer`, en
daarna `media-source-optimizer-work`; bij de tweede onder *Advanced Options → Quotas* een quota instellen
(bijv. 50 GiB). Zet via *Edit Permissions* eigenaar en groep op `apps` (uid/gid 568) met lees/schrijf/uitvoer
voor eigenaar. Het preset "Apps" voor het dataset-type is ook goed, mits gebruiker `apps` schrijfrechten heeft.

### Install via YAML

1. **Apps → Discover Apps → ⋮ (rechtsboven) → Install via YAML.**
2. Naam: `media-source-optimizer`.
3. Plak de inhoud van `/tmp/mso-compose.yaml` (of van `deploy/truenas/compose.yaml` waarin je `/mnt/tank/`
   zelf hebt vervangen).
4. **Save.** TrueNAS haalt de images op en start in volgorde: `init` → `postgres` (wacht tot gezond) →
   `migrate` → `api` + `worker` → `web` (wacht tot `api` gezond is). De eerste start duurt ongeveer een
   minuut.
5. Controle in de shell (containernamen krijgen van TrueNAS een voorvoegsel, daarom `grep`):

   ```sh
   sudo docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -E 'media-source-optimizer|mso'
   ```

   Verwacht: `init` en `migrate` → `Exited (0)`; `postgres`, `api`, `worker`, `web` → `Up … (healthy)`.

Optionele variabelen staan bovenaan `compose.yaml` (poort, bind-adres, tijdzone, limieten, image-tags). Zet ze
door de standaardwaarde achter `:-` in het bestand aan te passen.

## Eerste start: setupwizard

1. Open `http://<truenas-ip>:30081`. Zonder beheerder stuurt MSO je naar **/setup**.
2. Maak de beheerder aan (minimaal 10 tekens, met hoofd- en kleine letters of cijfers).
3. Vul per koppeling URL + sleutel in en klik **Test**. Gebruik LAN-adressen of hostnamen die de
   `api`-container kan bereiken, bijvoorbeeld:
   - Prowlarr: `http://<truenas-ip>:9696` + API-sleutel (Prowlarr → Settings → General)
   - SABnzbd: `http://<truenas-ip>:<poort>` + API-sleutel (SABnzbd → Config → General)
   - qBittorrent: `http://<truenas-ip>:<poort>` + gebruiker/wachtwoord (of API-sleutel bij qBittorrent ≥ 5.2)
   - MediaGuard (optioneel): `http://<truenas-ip>:30080` + een MediaGuard-gebruiker met rol *operator*
4. `localhost`/`127.0.0.1` werkt **niet** (dat is de container zelf; loopback is bewust geblokkeerd), en
   link-local/metadata-adressen (169.254.x.x) worden altijd geweigerd (SSRF-bescherming).
5. Rond de wizard af. MSO start in **alleen-lezen**-modus; wijzigingen aan externe systemen worden pas na
   jouw goedkeuring in beheermodus toegepast.

## MSO als indexer in Prowlarr

De `web`-container proxyt `/newznab/*` en `/torznab/*` naar de core (`apps/web/server.mjs`), dus Prowlarr
gebruikt dezelfde poort als de UI.

1. Haal in MSO de Newznab-sleutel op (NZB-indexers → Newznab-endpoint; de sleutel wordt alleen op verzoek
   getoond).
2. Zet de publieke basis-URL: `MSO_PUBLIC_URL` in de YAML (het installatieblok hierboven doet dat) of de
   instelling `newznab.public_url`, bijvoorbeeld `http://192.168.1.10:30081`. **Zonder deze instelling bevatten
   downloadlinks in de feed het interne adres `http://api:8200`**, omdat de web-proxy de Host-header naar de
   core herschrijft. Prowlarr kan dat adres niet bereiken.
3. Prowlarr → **Indexers → Add Indexer → Generic Newznab**:
   - URL: `http://<truenas-ip>:30081/newznab`
   - API Path: `/api`
   - API Key: de sleutel uit stap 1
   - Categories: series 5000-reeks, anime 5070, films 2000-reeks (animefilms: films + 5070); MSO meldt de
     exacte lijst in `t=caps`.
4. Voor torrents: **Generic Torznab** met URL `http://<truenas-ip>:30081/torznab`, API Path `/api`.
5. **Test** in Prowlarr moet slagen. Draait Prowlarr als TrueNAS-app in een eigen Docker-netwerk, dan bereikt
   het MSO via het host-IP en de gepubliceerde poort 30081 (niet verifieerd op echte hardware; zie
   HARDWARE_ACCEPTANCE 3.3).
6. Anti-lus: gebruikt MSO zelf deze Prowlarr als bron, dan weigert MSO cyclische verzoeken (Prowlarr →
   MSO → dezelfde Prowlarr) met een reden-code. Zet in Prowlarr bij de MSO-indexer geen sync naar apps die
   MSO zelf weer bevraagt als je dat niet wilt.

## Reverse proxy en TLS

MSO zelf spreekt HTTP op poort 30081 in je LAN. Voor HTTPS zet je er een reverse proxy voor (bijv. de
TrueNAS-app van Nginx Proxy Manager, Caddy of Traefik):

- Proxy naar `http://<truenas-ip>:30081` en geef de headers `Host`, `X-Forwarded-For` en
  `X-Forwarded-Proto` door. WebSocket/SSE-ondersteuning aan, buffering uit (live-updates).
- Zet in de YAML `MSO_SECURE_COOKIES: true` (cookie alleen over HTTPS) en `MSO_PUBLIC_URL` op de https-URL.
- Draait de proxy op dezelfde TrueNAS, zet dan `MSO_WEB_BIND` op `127.0.0.1` zodat poort 30081 niet meer
  rechtstreeks uit het LAN bereikbaar is.
- Publiceer MSO niet naar internet. Er is geen ingebouwde 2FA; gebruik VPN als je van buitenaf wilt beheren.

## Updaten (gepinde versies)

Images zijn op exacte versie gepind (`…-core:1.0.0`, `…-web:1.0.0`, `postgres:16.15-alpine@sha256:…`).
Een update is dus altijd een bewuste tagwijziging.

1. Lees `CHANGELOG.md` van de nieuwe versie (migraties, vereiste stappen).
2. Maak een back-up in MSO (**Systeem → Back-ups → Nu maken**) én een ZFS-snapshot:
   `sudo zfs snapshot -r tank/apps/media-source-optimizer@voor-update-$(date +%Y%m%d)`.
3. Noteer de schemaversie (Systeem → Info, `schema_version`).
4. **Apps → media-source-optimizer → Edit**, vervang `1.0.0` door de nieuwe versie bij `MSO_CORE_IMAGE` en
   `MSO_WEB_IMAGE`, **Save**. `init` en `migrate` draaien opnieuw; `api` start pas na een geslaagde migratie.
5. Controleer dat `migrate` met `Exited (0)` stopte en Systeem de nieuwe versie toont.

## Rollback

**A. Schema terugdraaien met `mso migrate --down` (data blijft, wijzigingen van de nieuwe versie gaan weg).**
De down-scripts zitten in het *nieuwe* image, dus voer dit uit vóór je de tag terugzet:

```sh
API=$(sudo docker ps --format '{{.Names}}' | grep -E 'media-source-optimizer.*api' | head -1)
WORKER=$(sudo docker ps --format '{{.Names}}' | grep -E 'media-source-optimizer.*worker' | head -1)
sudo docker stop "$WORKER"
sudo docker exec "$API" mso migrate --down <schemaversie-van-vóór-de-update>
```

Zet daarna in **Edit** de oude image-tags terug en **Save**. De oude versie start op het teruggedraaide schema.

**B. Volledige terugval met de ZFS-snapshot** (betrouwbaarst, ook bij een mislukte migratie): stop de app in
TrueNAS, `sudo zfs rollback -r tank/apps/media-source-optimizer@voor-update-…`, zet de oude tags terug en
start de app.

## Back-up en herstel

- **Automatisch:** dagelijks een `pg_dump` (instelling `backup.auto_daily`, bewaard: `backup.keep`, standaard
  14) in `APPDATA/backups/` met een `.sha256`-bestand.
- **Handmatig:** Systeem → Back-ups, of `sudo docker exec "$API" mso backup`.
- **Geheimen:** standaard zonder de tabel met versleutelde koppelingsgeheimen; alleen met expliciete keuze
  ("inclusief geheimen") gaan ze mee. De sleutel `APPDATA/secrets/vault.key` zit nooit in een dump.
- **Herstel:** Systeem → Back-ups → Herstellen. MSO controleert checksum en archief, maakt eerst een
  vangnet-back-up (`pre-restore`) en herstelt in één transactie (alles of niets). Daarna moet iedereen opnieuw
  inloggen.
- **Naar een andere server:** kopieer de héle APPDATA-dataset (bijv. `zfs send | zfs recv`), inclusief
  `secrets/vault.key` en `db/password`. Zonder `vault.key` zijn opgeslagen koppelingsgeheimen onleesbaar en
  moet je ze opnieuw invoeren.
- Neem APPDATA op in je periodieke ZFS-snapshots/replicatie. WORK bevat alleen herbouwbare cache/index.

## Zelf bouwen in plaats van ghcr.io-images

Op een Docker-host met de broncode (bijv. via SSH op TrueNAS of op een andere machine):

```sh
git clone https://github.com/lvos04/media-source-optimizer && cd media-source-optimizer
git checkout v1.0.0
docker compose -f deploy/truenas/compose.yaml -f deploy/truenas/compose.build.yaml build
```

Dit levert `media-source-optimizer-core:local` en `media-source-optimizer-web:local` op. Zet in de YAML van
de app `MSO_CORE_IMAGE`/`MSO_WEB_IMAGE` op die tags. (TrueNAS "Install via YAML" bouwt zelf geen images.)

## Verwijderen

Apps → media-source-optimizer → Delete. De datasets blijven bestaan; verwijder ze pas als je de data echt niet
meer nodig hebt. MSO verwijdert nooit media of downloads en heeft geen toegang tot je mediadatasets (tenzij je
de optionele alleen-lezen `DATA`-mount inschakelt).
