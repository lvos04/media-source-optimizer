# Installeren als gewone TrueNAS Custom App (aanbevolen)

Sinds versie 1.1.0 (poort 38081 sinds 1.1.1) is Media Source Optimizer (MSO) ook één enkele image waarin alles zit: database,
API, worker en webinterface. Je installeert die met het normale **Custom App**-formulier van TrueNAS SCALE:
geen YAML, geen databasewachtwoord, geen omgevingsvariabelen, geen tweede of derde container.

| Wat | Waarde |
|---|---|
| Image | `ghcr.io/lvos04/media-source-optimizer` |
| Tag | `1.4.0` (altijd een vaste versie, nooit `latest`) |
| Poort | `38081` → `38081` |
| Opslag | één map (dataset) → `/appdata` |
| Gebruiker | niets invullen: MSO regelt de rechten zelf |

> **Verificatiestatus.** De image is in een Linux/Docker-omgeving end-to-end getest (zie
> `docs/test-evidence/allinone-report.md`): starten als root én als gebruiker 568, herstart, harde kill, back-up/herstel en
> verkeerde maprechten. Het TrueNAS-formulier zelf is **niet** op echte TrueNAS-hardware doorlopen; de veldnamen
> hieronder volgen TrueNAS SCALE 24.10/25.04 en kunnen per versie iets afwijken.

## 0. Eenmalig: de image openbaar maken op GitHub

GitHub maakt een nieuw image-pakket altijd eerst **privé**, ook als de repository openbaar is. Dat is een instelling
van GitHub zelf en kan door geen enkele automatische workflow worden omgezet. Doe dit één keer; latere versies van
hetzelfde pakket blijven daarna openbaar.

1. Ga naar `https://github.com/lvos04?tab=packages` → **media-source-optimizer**.
2. Rechts: **Package settings** → onderaan **Change visibility** → **Public** → bevestig met de pakketnaam.

Controle (moet `200` geven): open `https://ghcr.io/token?scope=repository:lvos04/media-source-optimizer:pull`.

## 1. Dataset maken

**Datasets** → kies je pool → **Add Dataset**:
- Name: `media-source-optimizer` (bijv. onder `tank/apps`)
- Dataset Preset: **Apps**

Rechten hoef je niet aan te passen: bij de eerste start maakt MSO gebruiker `568` (apps) eigenaar van zijn eigen
submappen (`postgres`, `secrets`, `backups`, `work`) en verder niets.

## 2. Custom App aanmaken

**Apps** → **Discover Apps** → **Custom App**. Vul in:

| Sectie | Veld | Waarde |
|---|---|---|
| Application Name | | `media-source-optimizer` |
| Image Configuration | Repository | `ghcr.io/lvos04/media-source-optimizer` |
| | Tag | `1.4.0` |
| | Pull Policy | *Pull the image if it is not already present on host* |
| Container Configuration | Timezone | jouw tijdzone (bijv. `Europe/Amsterdam`) |
| | Restart Policy | *Unless Stopped* |
| | Environment Variables | leeg laten |
| Security Context | Privileged | **uit** |
| | Custom User | **uit** (niet aanvinken) |
| Network Configuration | Host Network | **uit** |
| | Ports → Add | Container Port `38081`, Host Port `38081`, Protocol TCP |
| Storage Configuration | Add → Type | **Host Path** |
| | Mount Path | `/appdata` |
| | Host Path | `/mnt/tank/apps/media-source-optimizer` (jouw dataset) |
| Resources (optioneel) | CPUs / Memory | 2 / 1536 MiB is ruim (gemeten gebruik in rust ± 200 MiB) |

**Install**. De eerste start duurt ongeveer 10–20 seconden (database aanmaken). Daarna staat de app op *Running* en
na de healthcheck op *Healthy*.

## 3. Openen en instellen

Open `http://<ip-van-truenas>:38081`. De setup-wizard vraagt om:
1. een beheerdersaccount;
2. de URL en sleutel van Prowlarr, SABnzbd, qBittorrent en optioneel MediaGuard. Gebruik het **LAN-IP van TrueNAS**
   met de poort van die app, bijv. `http://192.168.1.10:9696`. Gebruik geen `localhost`: dat wijst naar de MSO-container zelf;
3. de modus (laat **Alleen-lezen** staan tot je de adviezen hebt bekeken).

## Optioneel

| Wens | Hoe |
|---|---|
| Hardlink- en vrije-ruimtediagnose van je media | extra Host Path: je mediamap → `/media`, **Read Only** aan, en Environment Variable `MSO_MEDIA_DIR=/media` |
| Prowlarr bereikt MSO via een ander adres | Environment Variable `MSO_PUBLIC_URL=http://<ip>:38081` |
| Andere poort | Host Port aanpassen (Container Port blijft `38081`) |

## Updaten en terugdraaien

- **Updaten**: app → **Edit** → Tag naar de nieuwe versie → **Update**. Databasemigraties lopen automatisch bij de start;
  mislukt er een, dan blijft de oude database onaangeroerd en meldt de log dat.
- **Terugdraaien**: maak eerst in MSO (*Back-up & updates*) een back-up of een ZFS-snapshot van de dataset, en zet daarna
  de Tag terug. Een oudere versie op een nieuwer schema: zie `docs/MIGRATION.md`.

## Problemen

| Melding in de app-log | Oplossing |
|---|---|
| `FOUT: /appdata bestaat niet` | Storage-regel ontbreekt of Mount Path is niet precies `/appdata`. |
| `FOUT: geen schrijfrechten op /appdata/...` | Je hebt *Custom User* aangezet met een andere gebruiker. Zet het uit (MSO regelt de rechten), of geef gebruiker 568 schrijfrechten op de dataset. |
| `oude postmaster.pid ... verwijderd` | Geen fout: de vorige stop was onverwacht (stroom/kill); de database start gewoon. |
| Poort bezet / "port is already in use" bij opslaan | Een andere app gebruikt die host-poort. TrueNAS-catalogus-apps zitten meestal tussen 30000 en 31000; MSO gebruikt daarom 38081. Kies anders een andere vrije **Host Port** (Container Port blijft `38081`). |
| Image kan niet worden opgehaald (`denied`/`unauthorized`) | Stap 0 is nog niet gedaan: het pakket staat nog op privé. |
| Koppeling: "Loopback-adressen zijn uitgeschakeld" | Gebruik het LAN-IP van TrueNAS in plaats van `localhost`/`127.0.0.1`. |

## Alternatieven

- Dezelfde alles-in-één app via **Install via YAML**: `deploy/truenas/compose.allinone.yaml`.
- Losse containers (database apart, strengere scheiding): `deploy/truenas/compose.yaml` met
  [TRUENAS_INSTALL.md](TRUENAS_INSTALL.md).
