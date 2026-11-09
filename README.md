# Media Source Optimizer

Zelfstandige TrueNAS SCALE-app die je Usenet- en torrentbronnen beoordeelt, beheert en optimaliseert —
met als doel minder mislukte/ontbrekende downloads en inzicht in wat je abonnementen werkelijk opleveren.
Werkt naast **MediaGuard Ultimate** (zelfde huisstijl, samenwerking via de gedocumenteerde API) zonder
MediaGuard op enige manier te wijzigen, en werkt ook volledig zonder MediaGuard.

> Status 1.6.0 — gebouwd en getest in een Linux/Docker-sandbox met mock-diensten (zie
> [TEST_RESULTS.md](TEST_RESULTS.md)); het SABnzbd-gedrag is nagekeken in de broncode van SABnzbd 5.1.3. **Niet**
> getest op echte TrueNAS-hardware of tegen echte Prowlarr/SABnzbd/qBittorrent/MediaGuard/providers: zie
> [docs/HARDWARE_ACCEPTANCE.md](docs/HARDWARE_ACCEPTANCE.md).

## Wat het doet

| Gebied | Functies |
|---|---|
| **Usenet-engine** | SABnzbd-koppeling (wachtrij, historie, per-server statistiek), faaloorzaken uit SABnzbd's eigen verwerkingslog (ontbrekende artikelen, herstel, precieze uitpakfout, schijf vol, rechten) met zekerheidsniveau, opnieuw proberen alleen bij betere dekking, kapotte servers testen met SABnzbd's eigen servertest in plaats van blind aanzetten, vervaldata en blokken met herinneringen, uitleg en oplossing voor categorie '*', providers (Eweka, Newshosting, blockaccounts) met NNTP-statuscontrole, artikelbeschikbaarheid voor *daadwerkelijk aangevraagde* artikelen, bijdrage/overlap per provider met dekking en betrouwbaarheid, prioriteitsadvies met preview/rollback, optionele begrensde header-index (opt-in) |
| **Torrent-engine** | qBittorrent (API-key of sessie, versie-/capability-detectie), knelpuntdiagnose (peers, tracker, poort, limieten, schijf-I/O, VPN-interface, ruimte), valse-ETA-herkenning, recheck-advies, guardrails (private trackers, seedverplichtingen, uploadplafond), tuning één parameter tegelijk met 24–48 u evaluatie en rollback, cross-seed op payload-identiteit (opt-in), opslag & hardlink-bewustzijn |
| **Zoeken & indexers** | Prowlarr (health, stats, indexers, zoeken, tags), directe Newznab/Torznab/Spotweb-indexers, betrouwbaarheidsscore, eigen **Newznab- en Torznab-endpoints** voor Prowlarr met cyclusdetectie (origin graph), vier MediaGuard-categorieën `tv`/`tv-anime`/`movies`/`movies-anime` |
| **Herstel** | Source Baseline (geen hit, vals-positief, kwaliteit, retentie, onvolledig, client, import), fallback-kandidaten met MediaGuard-kwaliteitspoorten, nep-releasecontrole vóór elke eigen verzending (bestandenlijst van .torrent/NZB), blacklist met TTL en backoff, herzoek via MediaGuard's officiële route met quotum en idempotency |
| **Kosten** | handmatige bedragen per dienst, kosten per succes/GB, *Wat gebeurt er als ik deze dienst uitschakel?* (retrospectief, met onzekerheidsmarge — nooit een garantie, nooit automatisch opzeggen), scenariovergelijking |
| **Automatisering** | advies / automatisch veilig / toestemming nodig, standaard alleen-lezen, Autopilot (nooit verwijderen), doelenkaart (faalratio < 10 %, 0 verdachte torrents, 0 falende servers), één inbox "Wacht op jou", "Wat MSO deed" met effect, noodrem en afkoeling, Autopilot terugdraaien, voetafdruk met "Alles terugzetten" en respect voor je eigen keuze, pauzeknop, audit van alles, optionele AI-adviseur (opt-in, maandbudget, deterministische fallback) |
| **Platform** | login + rollen (admin/operator/viewer), CSRF, rate limits, SSRF-afscherming, versleutelde geheimen, back-up/herstel (ook van oudere versies, automatisch vóór elke upgrade), crash-veilig toepassen, koppelingsbewaking, bewaartermijnen, herstartveilige jobs, omkeerbare migraties |

## Schermen

Overzicht · Zoeken · Usenet-providers · NZB-indexers · Torrent-indexers · Torrentclient · Downloads ·
Ontbrekend & herstel · MediaGuard-koppeling · Opslag & seeding · Kosten & besparing · Automatisering (met Veiligheid en Voetafdruk) ·
Logs & audit · Instellingen · Back-up & updates · Setup-wizard. Donker/licht/systeem, 1440/1280/390/360.
Screenshots: [docs/screenshots/](docs/screenshots/).

## Installeren

**Aanbevolen — gewone Custom App, zonder YAML:** één image `ghcr.io/lvos04/media-source-optimizer:1.6.0`
(database, API, worker en web in één container), één map → `/appdata`, poort `38081`. Geen wachtwoorden of
variabelen nodig; rechten worden automatisch goed gezet. Stap voor stap: **[docs/TRUENAS_CUSTOM_APP.md](docs/TRUENAS_CUSTOM_APP.md)**.

Alternatief met losse containers via *Install via YAML*: [docs/TRUENAS_INSTALL.md](docs/TRUENAS_INSTALL.md).

## Documentatie

| Document | Inhoud |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | componenten, engines, change sets, jobs, anti-loop, data |
| [INTEGRATION_MEDIAGUARD.md](INTEGRATION_MEDIAGUARD.md) | eigenaarschap, compatibiliteitsmatrix, grenzen |
| [SECURITY.md](SECURITY.md) | beveiligingsmodel en bijbehorende tests |
| [docs/TRUENAS_CUSTOM_APP.md](docs/TRUENAS_CUSTOM_APP.md) | installatie als gewone Custom App (alles-in-één) |
| [docs/TRUENAS_INSTALL.md](docs/TRUENAS_INSTALL.md) | installatie met losse containers (YAML), Prowlarr-koppeling, updates, rollback |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | beheer, logs, back-ups, resources (gemeten) |
| [docs/MIGRATION.md](docs/MIGRATION.md) | schema-migraties, upgrade (1.5.0 → 1.6.0), terugdraaien |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | gebruikershandleiding |
| [docs/HARDWARE_ACCEPTANCE.md](docs/HARDWARE_ACCEPTANCE.md) | controlelijst voor de echte installatie |
| [TEST_RESULTS.md](TEST_RESULTS.md) | wat echt is getest, met resultaten |
| [CHANGELOG.md](CHANGELOG.md) | versies |

## Ontwikkelen

```bash
python3 -m venv .venv && .venv/bin/pip install -e 'apps/core[test]'
make lint test            # ruff + pytest (vereist PostgreSQL 16; MSO_TEST_DATABASE_URL;
                          #   meerdere pg-clients naast elkaar: MSO_PG_BIN=/usr/lib/postgresql/16/bin)
make stack                # images bouwen + volledige containerstack met mocks + smoke-test
make e2e                  # verse stack voorbereid (beheerder, koppelingen, back-up, wizard afgerond)
                          #   + alle Playwright-tests; een overgeslagen test laat de run falen
cd apps/web && npm ci && npm run dev
```

## Licenties

Licentie: **GPL-3.0** (zie [`LICENSE`](LICENSE); besluit van de eigenaar, 2026-11-09).

Eigen code van dit project. Het uiterlijk volgt de platform-ontwerptaal **Peil 1.1.0** (versie-gepinde kopie in
[`design/ontwerptaal/`](design/ontwerptaal/HERKOMST.md); omzetting in
[`docs/ontwerp/PEIL_MIGRATIE.md`](docs/ontwerp/PEIL_MIGRATIE.md)). De oorspronkelijke UI-basis was een intern
geversioneerde afgeleide van MediaGuard Ultimate (zelfde eigenaar) — zie
[apps/web/PROVENANCE.md](apps/web/PROVENANCE.md). Lettertype: het systeemfont (geen meegeleverde lettertypen meer).
Componenten: shadcn/ui (MIT) op Radix UI (MIT). Iconen: Lucide (ISC).
