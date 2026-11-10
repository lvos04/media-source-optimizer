# Installeren als standaard TrueNAS Custom App (de enige installatiemanier)

Sinds versie 1.1.0 (poort 38081 sinds 1.1.1) is Media Source Optimizer (MSO) ook één enkele image waarin alles zit: database,
API, worker en webinterface. Je installeert die met het normale **Custom App**-formulier van TrueNAS SCALE:
geen YAML, geen databasewachtwoord, geen omgevingsvariabelen, geen tweede of derde container.

| Wat | Waarde |
|---|---|
| Image | `ghcr.io/lvos04/media-source-optimizer` |
| Tag | `1.6.0@sha256:9bcdec69f7af0bc1747c42326d65f70eda28e6d00d2559d884e00a58ebfbf3da` (vaste versie plus digest, nooit `latest`; zie stap 2) |
| Poort | `38081` → `38081` |
| Opslag | één map (dataset) → `/appdata` |
| Gebruiker | niets invullen: MSO regelt de rechten zelf |

> **Verificatiestatus.** De image is in een Linux/Docker-omgeving end-to-end getest (zie
> `docs/test-evidence/allinone-report.md`): starten als root én als gebruiker 568, herstart, harde kill, back-up/herstel en
> verkeerde maprechten. Het TrueNAS-formulier zelf is **niet** op echte TrueNAS-hardware doorlopen; de veldnamen
> hieronder volgen TrueNAS SCALE 24.10/25.04 en kunnen per versie iets afwijken.

> **Onderhoudsnotitie (niet voor de installatie).** Het image-pakket is openbaar: op 2026-11-10 gaf een anonieme
> pull van het manifest van `ghcr.io/lvos04/media-source-optimizer:1.6.0` `200` (zonder inloggen). Je hebt dus geen
> registry-login nodig (contract §2.1). Alleen als een **nieuw** pakket ooit privé blijkt (GitHub maakt een nieuw pakket
> eerst privé), zet de eigenaar het op GitHub om: *Packages* → **media-source-optimizer** → **Package settings** →
> **Change visibility** → **Public**. Een registry-login in TrueNAS met een token dat alleen pakketten mag lezen is
> alleen een noodoplossing (NIET GEVALIDEERD op een echte NAS).

## 1. Dataset maken

**Datasets** → kies je pool → **Add Dataset**:
- Name: `media-source-optimizer` (bijv. onder een eigen dataset `apps` op jouw pool)
- Dataset Preset: **Apps**

Rechten hoef je niet aan te passen: bij de eerste start maakt MSO gebruiker `568` (apps) eigenaar van zijn eigen
submappen (`postgres`, `secrets`, `backups`, `work`) en verder niets.

## 2. Custom App aanmaken

**Apps** → **Discover Apps** → **Custom App**. Vul in:

| Sectie | Veld | Waarde |
|---|---|---|
| Application Name | | `media-source-optimizer` |
| Image Configuration | Repository | `ghcr.io/lvos04/media-source-optimizer` |
| | Tag | `1.6.0@sha256:9bcdec69f7af0bc1747c42326d65f70eda28e6d00d2559d884e00a58ebfbf3da` (versie + digest, contract §2.2). Weigert het formulier het `@`-deel, vul dan alleen `1.6.0` in (de digest staat dan alleen hier ter controle). Of het formulier de digest accepteert, is **NIET GEVALIDEERD** op een echte NAS. |
| | Pull Policy | *Pull the image if it is not already present on host* |
| Container Configuration | Timezone | jouw tijdzone (bijv. `Europe/Amsterdam`; de doelenkaart en "Wat MSO deed" tellen in lokale dagen) |
| | Restart Policy | *Unless Stopped* |
| | Environment Variables | leeg laten |
| Security Context | Privileged | **uit** |
| | Custom User | **uit** (niet aanvinken) |
| Network Configuration | Host Network | **uit** |
| | Ports → Add | Container Port `38081`, Host Port `38081`, Protocol TCP |
| Storage Configuration | Add → Type | **Host Path** |
| | Mount Path | `/appdata` |
| | Host Path | het pad van de dataset uit stap 1: `/mnt/<jouw pool>/…/media-source-optimizer` (kies hem met de mapkiezer) |
| Resources (optioneel) | CPUs / Memory | 2 / 1536 MiB is ruim (gemeten gebruik in rust ± 200 MiB) |

**Install**. De eerste start duurt ongeveer 10–20 seconden (database aanmaken). Daarna staat de app op *Running* en
na de healthcheck op *Healthy*.

## 3. Openen en instellen

Open `http://<ip-van-truenas>:38081`. De setup-wizard vraagt om:
1. een beheerdersaccount;
2. de URL en sleutel van Prowlarr, SABnzbd, qBittorrent en optioneel MediaGuard. Gebruik het **LAN-IP van TrueNAS**
   met de poort van die app: `http://<LAN-IP van TrueNAS>:<poort van die app>` (de poort staat bij die app in
   TrueNAS → Apps). Gebruik geen `localhost`: dat wijst naar de MSO-container zelf;
3. de modus: **Alleen-lezen** om eerst rond te kijken, of meteen **Autopilot** voor set it and forget it (zie USER_GUIDE §4).

## Optioneel

| Wens | Hoe |
|---|---|
| Hardlink- en vrije-ruimtediagnose van je media | extra Host Path: je mediamap → `/media`, **Read Only** aan, en Environment Variable `MSO_MEDIA_DIR=/media` |
| Prowlarr bereikt MSO via een ander adres | Environment Variable `MSO_PUBLIC_URL=http://<ip>:38081` |
| Andere poort | Host Port aanpassen (Container Port blijft `38081`) |

## Updaten en terugdraaien

Alles hieronder gaat via de TrueNAS-web-UI, zonder terminal. De menunamen volgen TrueNAS SCALE 24.10/25.04 en zijn
**NIET GEVALIDEERD** op een echte NAS.

- **Updaten**:
  1. Maak eerst een ZFS-snapshot van de dataset uit stap 1 (**Datasets** → de dataset → **Snapshots** → maak een
     snapshot). Dat is je terugval.
  2. App → **Edit** → Tag naar de nieuwe versie (met digest, zoals in stap 2) → **Update**.

  Sinds 1.6.0 maakt MSO bij de start eerst automatisch een back-up ("Vóór upgrade" in *Back-up & updates*; de nieuwste
  3 blijven) als er databasewijzigingen zijn, en voert daarna de migraties uit. Is er te weinig ruimte voor die
  back-up, dan staat dat in de log en start MSO gewoon door. Mislukt een migratie, dan blijft de oude database
  onaangeroerd en meldt de log dat.
- **Terugdraaien** (bijv. 1.6.0 → 1.5.0): terugzetten = de vorige image **én** de snapshot samen, nooit alleen de
  image. Een oudere versie werkt niet betrouwbaar met het nieuwere databaseschema.
  1. App → **Stop**.
  2. **Datasets** → de dataset → **Snapshots** → de snapshot van vóór de update → **Rollback**.
  3. App → **Edit** → Tag terug naar de vorige versie → **Update** en start de app.

  Wat je sinds de update in MSO hebt gedaan, gaat daarbij verloren. Een terugweg die je gegevens van na de update
  bewaart (het schema eerst met de nieuwe versie terugzetten), vraagt nu nog een commando in de container en is
  daarom geen gebruikersroute; die staat voor ontwikkelaars in `docs/MIGRATION.md` → *Terug naar 1.5.0*. Een knop
  daarvoor in de MSO-UI is gepland (P3-13, `docs/platform-v2/DELTA_MATRIX.md`).

## Problemen

| Melding in de app-log | Oplossing |
|---|---|
| `FOUT: /appdata bestaat niet` | Storage-regel ontbreekt of Mount Path is niet precies `/appdata`. |
| `FOUT: geen schrijfrechten op /appdata/...` | Je hebt *Custom User* aangezet met een andere gebruiker. Zet het uit (MSO regelt de rechten), of geef gebruiker 568 schrijfrechten op de dataset. |
| `oude postmaster.pid ... verwijderd` | Geen fout: de vorige stop was onverwacht (stroom/kill); de database start gewoon. |
| Poort bezet / "port is already in use" bij opslaan | Een andere app gebruikt die host-poort. TrueNAS-catalogus-apps zitten meestal tussen 30000 en 31000; MSO gebruikt daarom 38081. Kies anders een andere vrije **Host Port** (Container Port blijft `38081`). |
| Image kan niet worden opgehaald (`denied`/`unauthorized`) | Controleer repository en tag (met digest) letter voor letter. Het pakket is openbaar; blijkt het toch privé, zie de onderhoudsnotitie bovenaan. |
| Koppeling: "Loopback-adressen zijn uitgeschakeld" | Gebruik het LAN-IP van TrueNAS in plaats van `localhost`/`127.0.0.1`. |

## Geen andere installatiemanieren

De compose-bestanden in `deploy/truenas/` (`compose.yaml`, `compose.allinone.yaml`, `compose.build.yaml`) en
[TRUENAS_INSTALL.md](TRUENAS_INSTALL.md) zijn **alleen voor ontwikkeling, CI en de end-to-end-tests**. Ze zijn geen
installatiemethode: installeer MSO nooit via *Install via YAML* of met `docker compose` op de NAS (contract §2.3
regel 4). Het formulier hierboven is de enige manier.
