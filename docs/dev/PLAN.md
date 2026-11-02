# Media Source Optimizer — volledig bouwplan voor Claude Code

**Datum:** 2 november 2026  
**Product:** zelfstandige TrueNAS SCALE-companion-app, met visuele MediaGuard Ultimate-huisstijl  
**Doel:** Usenet- en torrentbronnen beoordelen, beheren en optimaliseren; onnodige abonnementskosten terugdringen; minder mislukte/ontbrekende downloads; naadloze samenwerking met MediaGuard Ultimate **zonder één wijziging aan MediaGuard**.

## 0. /goal — plak dit als opdracht in Claude Code

> Bouw in de HUIDIGE werkmap een volledig werkende, productiegerichte, zelfstandige TrueNAS SCALE-app genaamd **Media Source Optimizer**. Werk autonoom door op basis van dit gehele document; stop niet na scaffolding, plannen of mockups. Gebruik tests en concrete acceptatiecriteria en lever een werkende container-stack, migratie- en rollbackmechanisme, complete UI en handleiding op. Neem alleen toegang of secrets die ontbreken via de installatiewizard in de app op; vraag niet vooraf om accountgeheimen in de chat. Indien een bevoegdheidsstap (bijvoorbeeld sudo) nodig is, vraag deze één keer op de normale veilige manier, en gebruik de toestemming alleen voor de installatiehandeling. Geen onbeheerde privilege-escalatie of persistente sudo-credentials.
>
> De repository `https://github.com/lvos04/media-guard-ultimate` is **uitsluitend referentie/read-only**. Lees eerst zijn `README.md`, `docs/design/DESIGN_SYSTEM.md`, `docs/design/COMPONENT_INVENTORY.md`, `docs/design/SCREEN_SPECIFICATIONS.md`, `apps/web/styles/mg.css`, `apps/web/components/mg/`, `apps/web/components/shell/`, `docs/design/screenshots-app/`, `apps/core/mediaguard/provisioning/prowlarr.py`, `apps/core/mediaguard/provisioning/prowlarr_sync.py`, `apps/core/mediaguard/wanted/`, `apps/core/mediaguard/app.py`, `packages/contracts/fork-hooks.md`, `docs/AV1_SAFETY_DIFF.md` en `deploy/truenas/compose.yaml`. Haal die bron met geautoriseerde read-only GitHub-toegang op als het privé is; is toegang niet mogelijk, maak een expliciete compatibiliteitsmatrix met ontbrekende verificatiepunten en ga verder met standaard API-contracten. **Geen git push, PR of wijzigingen naar MediaGuard.** Lees voor de samenwerking ook `https://github.com/lvos04/av1`, maar wijzig deze niet.
>
> Lever één eigen app met twee onafhankelijke engines: (1) Usenet/Indexer Engine en (2) Torrent Engine. Een gezamenlijke orchestrator biedt bronzoeking, gezondheid, performance tuning, kostenanalyse, downloadherstel en audit. De app functioneert ook zelfstandig wanneer MediaGuard afwezig is. Bestaande SABnzbd, qBittorrent en Prowlarr blijven voorlopig intact; configureer ze via hun ondersteunde API's. MediaGuard blijft exclusief eigenaar van bibliotheekbeheer, kwaliteitsbeslissingen, handmatige media-import, AV1-encode en bestandsvervanging. De nieuwe app mag **niet** rechtstreeks MediaGuard-bestanden, configuratie, secrets, database, of bestaande Sonarr/Radarr-databases wijzigen.
>
> De UI moet eruitzien als dezelfde productfamilie als MediaGuard: gebruik de *daadwerkelijke* MediaGuard-design-tokens, layout, kaarten, statuschips, tabellen, nav, dialogen, typografie, zowel donker als licht, mobiele breakpoints en a11y-staten. Neem benodigde broncode als expliciete, intern geversioneerde afgeleide op in de NIEUWE repository, met behoud van toepasselijke licenties en herkomstvermelding; geen dynamische of runtime-koppeling aan MediaGuard-frontend. Gebruik indien technisch geschikt Next.js + TypeScript en Tailwind zoals MediaGuard. Vergelijk nieuwe schermen via Playwright-screenshots met de bestaande MediaGuard-referentiebeelden op 1440 en 390 pixels. Maak geen generieke admin-template en geen statische mockknoppen.
>
> Verbind Prowlarr voor health, stats, beschikbare indexers en zoeken; bied eigen **Newznab**- en **Torznab**-compatibele endpoints aan, zodat deze als extra indexers in Prowlarr geregistreerd kunnen worden. Voorkom zoeklussen wanneer Prowlarr tegelijk bron en afnemer is: gebruik een expliciete origin graph, bron-id's en cycle-detectie. Houd anime en normale films/series apart; ondersteun de vier MediaGuard-categorieën `tv`, `tv-anime`, `movies`, `movies-anime` en corresponderende indexertags. De app mag de MediaGuard-eigen Prowlarr-reconcile niet bestrijden; detecteer onenigheid en geef een veilige, idempotente configuratiepreview.
>
> Usenet: meet providerstatus, authenticatie, header-/artikelbeschikbaarheid voor daadwerkelijk aangevraagde artikelen, ontbrekende blocks, mislukte downloads en zinvolle fallback. Ondersteun bestaande Eweka en hernieuwde Newshosting en optionele blockaccounts. Meet unieke bijdrage **passief** of via expliciet toegestane tests; maak geen claims over unieke retentie op basis van alleen totaalgedownload. Ondersteun public/free sources, Spotweb en optioneel eigen header-indexering als afzonderlijk geconfigureerde backend. Scan NNTP alleen wanneer gebruiker de scope, opslag/bandbreedte en toegang configureert. Indexeer niet blind miljoenen berichten en suggereer niet dat een eigen indexer de provider geheel vervangt. Detecteer obfuscatie en beperkingen eerlijk. Geen betaling/toegangscontrole van derden omzeilen.
>
> Torrents: ondersteun qBittorrent als primaire downloadclient, met versie- en API-capability-detectie; later Transmission/Deluge als adapters. Gebruik waar ondersteund qBittorrent API key, anders veilige sessie-auth. Bouw tracker-health, peer-/seeder-data, ETA, stalled detectie, valse ETA-herkenning, intelligente recheck, poort-/connectiviteitsdiagnose, veilige snelheidslimieten, queue-beleid, schijf-I/O-beperking, seedverplichtingen, private tracker restrictions, ratio/seedtijdbeleid, VPN-interface binding diagnose en optionele cross-seed-op-databasis van hash en verifieerbaar aanwezige data. Voer geen automatische trackerinjectie, seedstop, bestandverwijdering of publieke DHT voor private torrents uit. Alle wijzigingen starten in advies/dry-run; rollen en goedkeuring bij risicovolle handelingen.
>
> Ontbrekende downloads: integreer als MediaGuard beschikbaar is via de GEDOCUMENTEERDE API's en bestaande zoek/backfill-routes. Maak geen tweede concurrerende wanted-/media-import-agent. De nieuwe engine levert nieuwe zoekbronnen, analyses, status en suggesties, en vraagt herzoeken via de officiële bestaande routes met quota en idempotency; gebruik uitsluitend ondersteunde API's. In zelfstandige modus mag de gebruiker handmatig zoeken en bestanden naar SABnzbd/qBittorrent versturen, zonder automatische wijzigingen aan een mediabibliotheek. Herhaal nooit eindeloos dezelfde gefaalde NZB of infohash. Fallback tussen Usenet en torrent alléén onder behoud van MediaGuard-kwaliteitseisen (juiste aflevering, anime-nummering, audio/ondertiteling, HDR/DV), download- en seedpolicy.
>
> Kies een eenvoudige veilige architectuur, bijvoorbeeld `web` (Next), `core` (FastAPI), `worker`, `postgres` (persistente taken, metingen, index, audit). Geen verplichte externe cloud of betaalde API. Worker kan geïsoleerde indexers zoals Spotweb/NNTmux later aansturen, maar bouw of fork geen complexe Usenet-indexer zonder technische en licentie-evaluatie. Houd containers, datasets, media-bestanden en rechten gescheiden. UI login, eerste-startwizard, secret-vault, least privilege, rollen, API-rate limits, CSRF/CORS/SSRF-afscherming, geen keys in logs, beveiligde webhooks, audittrail, backups/herstel en versie-pin op images. Verzamel geen onnodige persoonsgegevens. Configureer geen externe hosting of publieke toegang standaard.
>
> Maak een volledig bedienbare Nederlandse UI: Overzicht, Zoeken, Usenet-providers, NZB-indexers, Torrent-indexers, Torrentclient, Downloads, Ontbrekend & herstel, MediaGuard-koppeling, Opslag/seeding, Kosten & besparing, Automatisering, Logs/audit, Instellingen en Setup. Alle acties hebben status, uitleg, testknoppen, preview, rollback en duidelijke grens tussen 'advies', 'automatisch veilig' en 'toestemming nodig'. Licht/donker/systeem, responsive 1440/1280/390/360, echte backenddata, geen dummydata. Optionele AI-adviseur alleen na expliciete opt-in, met maandelijks budgetplafond en deterministic fallback; geen AI die autonoom bestanden verwijdert of toegang betaalt.
>
> Maak een TrueNAS SCALE `Install via YAML`-geschikte Compose-stack met configureerbare `APPDATA`, `WORK` en optionele `DATA` mounts, images of reproduceerbare builds, healthchecks, init, persistente secrets, restart policies, niet-root containers en expliciete CPU/RAM/disk-quota. Geen Docker socket; geen host network tenzij aantoonbaar nodig. Bestaande TrueNAS downloadclients kunnen via API gekoppeld worden. Configureer de UI via setupwizard en sla API-credentials niet in de YAML op.
>
> Lever in fasen volledige productiecode: (A) repo-audit en ontwerpcontracten, (B) TrueNAS backend/DB/API/auth, (C) dezelfde MediaGuard UI incl. wizard, (D) Prowlarr/SABnzbd/qBittorrent connectoren, (E) Newznab/Torznab en veilige source scoring, (F) torrent/performance optimizer, (G) metrics/kosten, (H) MediaGuard-koppeling & herstel, (I) back-up, security, tests en release. Maak per fase unit-/contract-/integratietests. Test ook een echte test-stack met containers, fake NNTP server, mock indexers, mock providerfouten, simulatie qBittorrent/SABnzbd, Playwright/browser-tests en fault-injection. Waar geen echte TrueNAS-hardware of credentials beschikbaar zijn: rapporteer dat als niet geverifieerd, en geef een uitgewerkte echte-hardware acceptatiechecklist; claim nooit dat hardwaretests zijn uitgevoerd als dat niet zo is.
>
> **Harde acceptatiecriteria:** (1) MediaGuard branch/repo blijft bit-voor-bit ongewijzigd; (2) nieuwe app draait zelfstandig met werkende login; (3) verbindingstest en uitlezing van 3 bestaande systemen; (4) effectieve en cyclusvrije Newznab/Torznab endpoints; (5) vier bibliotheekcategorieën correct; (6) ingestelde privacy/seeding/AV1-harde grenzen worden gerespecteerd; (7) rollback en restart-safe jobs; (8) visueel en toegankelijk als MediaGuard; (9) bewijs uit CI, testlogs en schermopnamen; (10) één complete TrueNAS-installatiehandleiding; (11) reële, begrensde kosten/benefit-analyse zonder gegarandeerde besparingsclaims. Sluit af met een overzicht van gerealiseerde functies, bewijs van tests, resterende echte-hardware checks en exacte installatie-instructies. Werk door tot dit geleverd is, niet slechts een plan.

## 1. Bestaande situatie en integratiegrenzen

### Bevestigd in huidige MediaGuard-repository

- `README.md` beschrijft 4 Sonarr/Radarr-forks: normale tv, anime tv, normale films, animefilms.
- `apps/core/mediaguard/app.py` toont een FastAPI core met module-routers.
- `apps/core/mediaguard/provisioning/prowlarr.py` ondersteunt Prowlarr read/write met API key en indexers/applications.
- `prowlarr_sync.py` beheert categories/tags (`anime`, `normal`); niet ongecontroleerd tegen dezelfde settings inwerken.
- `apps/core/mediaguard/wanted/backfill.py` bevat gequoteerd backfillbeleid; geen dubbele wanted-worker bouwen.
- `packages/contracts/fork-hooks.md` bevat releaseprotocol `torrent|usenet`, pre-grab en pre-import.
- `docs/AV1_SAFETY_DIFF.md` beschrijft hardlink-/seed-gerelateerde veiligheidsgrenzen.
- `docs/design/DESIGN_SYSTEM.md` + `apps/web/styles/mg.css` zijn canonieke UI-bronnen.

### Ownership-model

| Systeem | Schrijft naar | Nieuwe app doet |
|---|---|---|
| MediaGuard | bibliotheek, gewenste releases, import, AV1-conversie | read-only API, veilige zoekaanvragen via gedocumenteerde API |
| Prowlarr | indexers, sync, aanvraagroute | indexer endpoint aanbieden; configuratie-preview; idempotent gekoppeld |
| SABnzbd | NZB downloadqueue/serverprioriteit | read/write API met preview/rollback voor ondersteunde instellingen |
| qBittorrent | torrents, trackers, queue, ratio, snelheid | read/write API met privacy/seed-policy en preview/rollback |
| Source Optimizer | eigen index, prestaties, kosten, jobs, auth, audit | volledige eigenaar |

**Geen patch, plugin-installatie, SQL-schema wijziging of proxy-injectie in MediaGuard.** Het volledige nieuwe dashboard staat in de companion app. MediaGuard UI toont nieuwe schermen alleen als MediaGuard zelf later optioneel wordt uitgebreid.

## 2. Architectuur en modules

- `web`: Next.js/TypeScript; gekloonde en geversioneerde UI-primitives/tokens uit de MediaGuard-repository, met eigen naam/branding.
- `api`: FastAPI (OpenAPI); auth, connector-config, RBAC, zoeken, scoring, audit, quality-policy, subscriptions, metrics, backfill coordination.
- `worker`: persistente taken, rate limiting en backoff, polling met conditional fetch, provider en tracker health, index backends. Bij uitval hervatten vanuit DB.
- `postgres`: taken, settings, indexer cache, bronnen, resultaten, redactie-logboeken; periodieke backup.
- `connectors`: MediaGuard, Prowlarr, SABnzbd, qBittorrent; uitbreiding naar NZBGet, Transmission, Deluge mogelijk via adapters.
- `source_backends`: externe toegestane Newznab/Torznab; Spotweb; optioneel eigen NNTP header scanner na resource-check.
- `policy`: protocol-agnostische release normalisatie/dedupe; quality gates van MediaGuard blijven leidend.
- `integration`: companion-handshake, capabilities, credential test, webhook-auth, telemetry, conflicts.

### Anti-loop-strategie

Een optimizer-indexer mag **niet** een Prowlarr-query terug naar dezelfde Prowlarr sturen die de optimizer-indexer aanroept. Werk met `source_id`, `origin`, `trace_id`, `hop_count` en een ingestelde gerichte acyclische bron-graaf. Een aanvraag met reeds bezochte node wordt geweigerd (met reason code). Prowlarr mag via normale indexers bronnen zoeken; optimizer mag daar geen cyclisch proxykanaal voor gebruiken. Voeg regressietests toe.

### Release-normering

Vastleggen: protocol, originele bron, id, NZB id/MID of torrent infohash v1/v2, titel, media ids, seizoen/episode/absolute anime numbering, categorie, grootte, codecs/resolutie, audios/subs, HDR, retentie, seeders, trackerstatus, ingeschatte snelheid, indexertest timestamp, confidence, TTL, false-positive flags. **Niet** op titel alleen dedupliceren.

## 3. Essentiële functies en regels

### Zoek/indexers

- Concreet: zoeken, RSS, filters, category map, language constraints, duplicates, bronbeschikbaarheid.
- Newznab/Torznab `caps`, `search`, `tvsearch`, `movie`, `get`/download en correcte XML-antwoordformaten met veilige auth, quota, pagination en foutcodes.
- Prowlarr `indexerstats`, `indexerstatus`, `health`, `history` en beschikbare indexers; categoriefouten en timeouts onderscheiden van lege resultaten.
- `normal` en `anime` per bron expliciet; bronrotatie op betrouwbaarheid, niet enkel snelheid.
- Geen automatische privétracker-scraping, captcha/paywall/anti-bot-bypass, of het omzeilen van toegangsregels.

### Usenet

- Herkende accountfouten (expired/auth/rate-limit/cap/reachability).
- Monitor Eweka, Newshosting en uitbreidbare blockaccounts; geen losse hostnaam/credentials in logs.
- Provider-overlap meten op opgevraagde artikelen/recoveries, met observatiedekking/vertrouwensniveau.
- Prioriteit aanpassen na gebruikersbevestiging; rollback oorspronkelijke serverinstellingen.
- Eigen NNTP-indexering optioneel en begrensd: toegestane groepen, lage retention window, disk/CPU/netwerk budget, indexing opt-in, ratio bruikbare index voor nieuwe releases, alerts voor obfuscatie.
- Niet doen alsof eigen indexer onbeperkte Usenet-toegang of bestaande indexers kan substitueren.

### Torrent

- Niet-destructief standaard: read-only diagnostics en aanbevelingen.
- Bottleneck-classifier: geen peers, tracker down, geblokkeerde poort, rate limits, disk I/O, stale torrents, verkeerd VPN/interface, onvoldoende ruimte, seed policy.
- Tuning met guardrails: connection counts, per-torrent limits, seeding/per-category limits, upload cap nooit 100% van lijncapaciteit, jitter en hysterese om oscillatie te voorkomen.
- Eerst vastleggen baseline, dan één omkeerbare parameter tegelijk, 24–48h meten, rollback op verslechtering.
- Geen publieke tracker/DHT op private torrents; private tracker seed ratio/time respecteren.
- Cross-seed alleen indien toegestaan en bewezen gelijke payload; zonder verplaatsen of hernoemen van seedgegevens.
- ZFS monitoring en AV1 hardlink awareness: actief seedbestanden nooit herschrijven of verwijderen.

### Kosten en inzichten

- Per dienst: handmatig ingevuld maand-/jaar-/blockbedrag, vervaldatum, gebruik, unieke succesvolle hits, latency, succesvolle downloads, faaloorzaken, ontbrekende items teruggevonden.
- Overzicht twee providers vs alternatieve backbone en blockaccount: betrouwbare scenario's, zonder verzonnen backbone-diversiteit of besparingsgarantie.
- Simulatie `Wat gebeurt er als ik deze dienst uitschakel?` met retrospectieve, geobserveerde bronnen; nooit automatisch opzeggen.
- AI opt-in: detectie complexe fouten of suggesties, €0 standaard; model en maandlimiet via UI. Geen besluitvorming buiten harde policy.

### Herstel

- Geautomatiseerde observatie van missing items in MediaGuard via zijn bestaande API; geen duplicatie van zijn planner.
- Source optimizer meldt aanvullende hits, foutoorzaken en fallback-kandidaten.
- Gebruik MediaGuard officiële zoek-API of Sonarr/Radarr-forks via hun normale API uitsluitend na duidelijke bron-ownership afspraken en rate limits.
- Mislukte release blacklist met TTL, op media/protocol/hash basis; exponential backoff, idempotency keys.
- Quality gates nooit omzeilen: 4K/HDR/DV, anime taal/dual audio, juiste aflevering, ondertitels.

## 4. Design en schermen (MediaGuard design system v1)

**Canoniek:** `docs/design/DESIGN_SYSTEM.md`, `apps/web/styles/mg.css` en `apps/web/components/mg/*` uit MediaGuard. Gebruik eveneens `apps/web/components/shell` en `docs/design/screenshots-app/` voor verifiëren.

- Dark tokens: `--mg-bg #0C111B`, `--mg-surface #141C29`, `--mg-raised #1C2735`, `--mg-sunken #0A0E16`, `--mg-accent #4F9CF9`, `--mg-success #35B587`, `--mg-warning #E8A44D`, `--mg-danger #E36B71`, `--mg-text #F0F4FA`.
- Typografie: Inter + JetBrains Mono, met legale pakketdistributie en licentie; geen herpublicatie van fontbestanden in los bijlagebestand.
- Opzet: linkersidebar desktop, 60px topbar, kaarten grid 12 cols, 256/236 sidebar, mobiele bottom-nav + menu, 1440/1280/390/360.
- UI states: loading, empty, error, offline, not available, stale, uncertain, pending, succes; kleur altijd met icon en tekst; `prefers-reduced-motion`, WCAG 2.2 AA.
- Exacte functionaliteit: Dashboard (gezondheid, actieve downloads, foutlocaties, kosten), Zoekbronnen (Usenet/torrent + details), Providers, qBittorrent, SABnzbd, Zoekresultaten (vergelijk kandidaat), Ontbrekend, MediaGuard-koppeling, Opslag & Seeding, Kosten, Automatisering, Audit/Logs, Instellingen, Updates, Setup.
- UI alle instellingen direct bedienbaar, inclusief toggles veilige auto-tuning, licenties, credentials, limieten, categorieën en tests. Geen dode UI-elementen.
- Visuele regressie: Playwright-screenshots per belangrijk scherm dark+light+390, vergelijken met eigen golden baselines en MediaGuard-feel; geen pixel-identieke applicatiebrand vereist, wél dezelfde vormtaal.

## 5. UI flow

1. Start -> administrator aanmaken -> wizard detecteert Prowlarr/SABnzbd/qBittorrent en optioneel MediaGuard via ingevulde URLs; geen geheime data auto-discovery.
2. API credentials met Test + `Bewaar`; laat alleen status `ingesteld` zien.
3. Preview toont welke settings wel/niet aangepast worden. Read-only totdat gebruiker beheermodus selecteert.
4. `Source Baseline` leest statistieken en onderscheidt met zekerheid: geen indexer hit, false positive, rejected on quality, no retention, incomplete, client failure, import failure.
5. De app stelt per bron en download een testbare verbetering voor, inclusief impact en revert.
6. MediaGuard heeft geen verplichting om de nieuwe app te starten: backend valt terug naar standalone modus.

## 6. TrueNAS install en resources

- `deploy/truenas/compose.yaml`, ondersteun `Install via YAML` onder TrueNAS SCALE. Versioneer immutable container images, health checks, dependencies en restart.
- Datasets: `apps/media-source-optimizer` (config + database), `media-source-optimizer-work` (cache, zoekindex; quota), optioneel read-only `/data/media` voor diagnostics maar liever API-only.
- Default beheer op eigen TCP-poort (bijvoorbeeld 30081), alleen LAN, geen host-network, geen `docker.sock`, geen root tenzij gespecificeerde capability echt nodig is.
- Config en API keys via in-app wizard en veilig volume, geen betaalde SaaS nodig. Credentials niet mee in backups/export zonder expliciete toestemming.
- Bewaak opslag: NNTP header index kan groot worden; capaciteit begrenzen, TTL-retentie, metrics. Minimaal service health en disk reserve.
- Documenteer ondergrens voor geheugen/CPU op basis van echte benchmark in CI; geen ongeverifieerde hardwareprestatieclaim.

## 7. Acceptatietests

1. `git status` van MediaGuard voor/na geen mutaties (read-only clone). Geen patches daarheen.
2. Standalone container-stack 0 foutmeldingen, persistente data na restart, login + logout, TLS/reverse-proxy docs.
3. Prowlarr mocked en echt sandbox: stats, test, indexerstatus, tag mapping, no query loop.
4. SABnzbd: expired account, auth denied, article missing, success; per server metingen, prioriteiten read-only/dry-run/apply/revert.
5. qBittorrent: public/private torrent, rate policies, ratio/seedtime, stalled, tracker fail, disk pressure, auto-tune rollback; geen dataverlies.
6. Newznab en Torznab XML/caps/TV/movie/search met cursor/paginering en juiste categorie.
7. MediaGuard online/offline: API capability detection; safe fallback en geen dubbele download/wanted agents.
8. Matrix tv/anime/movies/anime-movies en audio/HDR rules; API geen policy bypass.
9. Playwright responsive desktop/mobile dark/light, keyboard, contrast, no overflow; geen mock-knoppen.
10. Restore uit corrupte/ontbrekende index en database backup; job restart; permissions; API secret redaction, SSRF test, CSRF test, role enforcement.
11. QA report met echt gedraaide tests en beperkingen. Echte TrueNAS-installatie, LiveUsenet, echte trackers blijven een verificatiestap indien geen toegangsgegevens/hardware beschikbaar.

## 8. Verplichte oplevering

- Complete Git-repository en releasebare Docker images of reproduceerbare builds.
- Scherpe grenzen, OpenAPI-spec, connectoren, Newznab/Torznab capability-API, UI, tests.
- Werking zonder MediaGuard en met MediaGuard ongewijzigd.
- `README.md`, `ARCHITECTURE.md`, `INTEGRATION_MEDIAGUARD.md`, `SECURITY.md`, `TRUENAS_INSTALL.md`, `OPERATIONS.md`, `MIGRATION.md`, `TEST_RESULTS.md`, `CHANGELOG.md`.
- Eén installatiecodeblok met specifieke paden/tokens, geen hardcoded secrets.
- Rollback en backupplan en gebruikershandleiding, screenshots beide thema's en mobiel.

## 9. Niet-doen / realistische grenzen

- Niet beloven dat een eigen indexer een NNTP-provider vervangt of alle commerciële indexers evenaart.
- Niet automatisch media verwijderen, herencoden, hernoemen, privétrackers omzeilen of torrents seeden buiten lokale policy.
- Niet Prowlarr en MediaGuard tegelijk dezelfde instellingen laten beheren.
- Niet vergeten dat een 100%-artikel-hitrate van de primaire provider niet betekent dat alternatieve bestanden altijd bestaan.
- Niet een betaalde indexer/Usenet-toegang omzeilen. Respecteer licenties, auteursrecht en toegang tot bronnen.
- Niet claimen dat de app al op de echte TrueNAS van de gebruiker is getest wanneer alleen CI-containers konden draaien.

## 10. Referentie-URL's

- MediaGuard: https://github.com/lvos04/media-guard-ultimate
- AV1: https://github.com/lvos04/av1
- Prowlarr API: https://prowlarr.com/docs/api/
- SABnzbd API: https://sabnzbd.org/wiki/advanced/api
- qBittorrent WebAPI: https://github.com/qbittorrent/qBittorrent/wiki
- TrueNAS Custom Apps: https://apps.truenas.com/managing-apps/installing-custom-apps/
- Optionele NNTmux (alleen na evaluatie): https://github.com/NNTmux/newznab-tmux
