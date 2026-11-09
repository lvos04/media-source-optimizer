# Changelog

Alle noemenswaardige wijzigingen. Versies volgen SemVer; databaseschema-versies staan in `docs/MIGRATION.md`.

## [Unreleased]

### Uiterlijk: web-UI omgezet naar de platform-ontwerptaal Peil 1.1.0 (stap 3)
Alleen het uiterlijk en de twee toegestane toevoegingen; functies, routes, API-aanroepen, foutformaat, datamodel,
poorten en installatie zijn gelijk gebleven. OpenAPI-export byte-gelijk (sha256 `bb551ee1…0471b`). Plan:
`docs/ontwerp/PEIL_MIGRATIE.md`; besluiten: ADR-0006 en ADR-0007.
- **Fase 1a — aliaslaag.** Peil-tokens (`styles/peil/ontwerptaal.css`) en de gedeelde MediaGuard/MSO-aliaslaag als
  versie-gepinde kopie met herkomstkop (`npm run check:peil`, ook in CI); MSO-correctielaag `styles/peil-mso.css`
  (Peil-focusring, scroll-padding, omlopen op 320–390 px, uitgeschakeld zonder opacity, verouderd met stippel en
  arcering, pauze = Gepauzeerd). Standaardthema **Systeem** (het themascript zet altijd het opgeloste thema).
- **Fase 1b — woorden en iconen.** Groen alleen voor gemeten gezondheid; vaste woorden Gezond/Beperkt/Storing/Onbekend,
  Alles gezond, Niet gekoppeld/Gekoppeld, Afgerond, Voltooid, Wacht op voorwaarde, Onzeker; Onbekend nooit meer met
  het pauze-icoon; MSO-risico als RisicoBadge R0/R1/R2 met het MSO-niveau als uitleg; KPI-tegels laden met een skelet
  en tonen bij een fout Onbekend met reden; kosten met ontbrekende prijzen = "Deels onbekend"; verouderd ook bij een
  lege lijst en op het Overzicht; foutpaneel met code en kopieerbare trace-ID; profielknop (WCAG 2.5.3) en
  `span.qribbon` (4.1.2) toegankelijk.
- **Fase 2 — Tailwind 4 + shadcn/ui + lucide.** `components/ui` (Button, Badge/RisicoBadge), primitives met dezelfde
  API op shadcn/Radix (Dialog/AlertDialog met focusval en focus terug, Tabs met Home/End, ToggleGroup-vorm, Switch,
  Field met aria-describedby), AppSchil (zijbalk ≥ 1024 px, ondernavigatie, Meer als sheet, ThemaKeuze), elke
  bediening ≥ 44 px, tekst ≥ 14 px, lopende tekst 16 px, systeemfont; tabellen worden een kaartenlijst zodra hun kaart
  smal is (container query). Inter/JetBrains Mono, fontsource en de ongebruikte `recharts` zijn verwijderd.
- **Toegestaan toegevoegd:** "Terug naar de Hub" (alleen bij een geldig `systeem.hub_url`; nieuwe optionele
  instelling in het bestaande register, 422 bij een ongeldig adres) en bij elke grafiek een tabelweergave ("Toon als
  tabel") plus toetsenbordbediening; grafieken gebruiken het Peil-palet (geen statuskleuren als reeks).
- **Tests:** e2e 78 → 109 (nieuw `e2e/peil.spec.ts`: 320/768/1024, axe met wcag21a op schermen en dialogen,
  Tab-doorloop, grafieken, Hub-link; overloop gemeten t.o.v. de apparaatbreedte), 45 unit-tests (vitest) in CI,
  pytest voor de Hub-adresvalidatie. Schermafdrukken na de omzetting in `docs/screenshots/peil/na/`.
- **Nog open (vervolg):** 569 inline `style={{…}}` (layout) naar utilities en daarna `mg.css`, `mso.css` en de
  aliaslaag weg; de doel-trendlijn met meerdaagse echte data bekijken. Niet gevalideerd: echte telefoon,
  schermlezer, forced colors, 200 %-zoom.

### Ontwerp (alleen documentatie; de app is niet gewijzigd)
- **Omzettingsontwerp naar de platform-ontwerptaal Peil 1.1.0** (contract 1.2 §13): inventaris van alle schermen,
  tabs en toestanden, nulmeting, kritiek, vertaling per scherm/component (tokens, shadcn/ui, lucide, statusvorm,
  Nederlandse woorden), de twee fasen (aliaslaag; Tailwind 4 + shadcn/ui), de toegestane toevoegingen ("Terug naar de
  Hub", tabelweergave + toetsenbord bij elke grafiek) en de geraakte tests: `docs/ontwerp/PEIL_MIGRATIE.md`.
- **Schermafdrukken vóór de omzetting**: 250 afdrukken van de echte app met testdata (16 schermen × 360/390/768/1440 ×
  licht/donker, tabs en toestanden) met nulmeting (`metingen.json`, `toetsenbord.json`) in `docs/screenshots/peil/voor/`.
  Gevonden o.a.: 6 schermen lopen horizontaal over op 360/390 px (de huidige e2e-meting ziet dat niet), focus achter de
  ondernavigatie, 163 groene chips voor zaken die geen gezondheid zijn.
- **Versie-gepinde kopie van Peil 1.1.0** met herkomstkop en sha256 in `design/ontwerptaal/` (alleen referentie).
- Besluiten: `docs/adr/ADR-0006-peil-omzetting-interpretaties.md`.

### Tests en CI (geen wijziging aan app, API of installatie)
- **E2E-job is nu eerlijk.** `make e2e` (en de e2e-job in CI) bereidt de verse stack eerst voor: beheerder,
  koppelingen naar de mocks, één back-up en een afgeronde eerste-startwizard (`smoke.py --e2e-prepare`, inclusief
  `POST /api/v1/setup/complete`). Daarna draaien alle 78 Playwright-tests met inloggegevens. Een overgeslagen test of
  minder dan 78 tests laat de run falen (`apps/web/e2e/strict-reporter.ts`, `MSO_E2E_STRICT=1`). Voorheen werden 76
  van de 78 tests stil overgeslagen.
- **Race in de inlogpagina-test opgelost.** `smoke.spec.ts` wacht op het antwoord van `/api/v1/setup/status` en
  beslist pas dan of hij de setup-wizard of het inlogformulier moet zien.
- **Logcontrole van de stacktest niet meer afhankelijk van de schijf.** De PostgreSQL-meldingen "the database system
  is shutting down / starting up" zijn alleen nog toegestaan binnen de bewuste herstartvensters van de test (compose
  restart en down/up), op basis van de Docker-tijdstempels. Daarbuiten blijven ze een fout. Met unit-test
  (`tests/stack/test_smoke_logcheck.py`).
- **python-job:** pg_dump/pg_restore 16 via `MSO_PG_BIN`, net als `release.yaml`.
- Besluiten: `docs/adr/ADR-0005-eerlijke-ci-e2e-en-logcontrole.md`.

## [1.6.0] — 2026-11-09

De echte oorzaak, eerlijk gemeten. MSO pakt nu aan waaróm downloads mislukken, in plaats van kapotte servers de schuld
te geven, en laat bovenaan Overzicht eerlijk zien of je drie doelen gehaald worden: faalratio onder 10 %, 0 verdachte
torrents, 0 falende servers. Daarbij alles wat nodig is om MSO weken onbeheerd te laten draaien: een noodrem, één inbox
en onderhoud zonder omkijken.

### Usenet: de echte oorzaak
- **Tellerfout opgelost.** SABnzbd's veld `articles_success` bevat het aantal *mislukte* artikelen. MSO las het als
  gevonden; daardoor stond Eweka op "0,4 % gevonden", terwijl het 99,6 % is. De opgeslagen cijfers worden bij de
  upgrade ook gecorrigeerd.
- **Oorzaak uit het verwerkingslog van SABnzbd.** Per mislukte download leest MSO wat SABnzbd zelf vastlegde:
  ontbrekende artikelen, herstel gelukt of mislukt, de precieze uitpakfout, wachtwoord, schijf vol, geen schrijfrechten
  (ook als SABnzbd in het Nederlands schrijft). Downloads → Usenet toont het per download, bijvoorbeeld
  "Data compleet · uitpakken mislukt: …" of "312 artikelen ontbraken".
- **Geen valse serveroorzaak meer.** Een server die niet kan inloggen, levert ook geen data. Was de data compleet, dan
  noemt MSO geen kapotte server meer als oorzaak van "uitpakken mislukt".
- **"SABnzbd kan downloads niet uitpakken".** Komt dezelfde fout van SABnzbd of de opslag terug (3 keer in 7 dagen; bij
  een fout van het uitpakprogramma zelf: bij 3 verschillende releases), dan staat er één melding in "Wacht op jou" met
  de precieze regel en voorbeelden. Hij verdwijnt vanzelf na 3 rustige dagen. Voor zulke downloads zoekt MSO geen andere
  release, want die zou net zo mislukken.
- **Herbeoordeling.** Direct na de upgrade beoordeelt MSO de mislukte downloads in de SABnzbd-historie (ook het archief)
  opnieuw; later kan dat met de knop **Opnieuw beoordelen**. Vastgelegd in de audit.
- **Alleen zinvol opnieuw proberen.** MSO laat SABnzbd een download alleen nog opnieuw proberen als er artikelen
  ontbraken (of SABnzbd zelf een serverfout noemt) én de dekking daarna beter werd: een server werkt weer of er kwam een
  nieuwe bij. Servers uitzetten is geen reden meer. Lukt minder dan 30 % van minstens 10 pogingen in 14 dagen, dan
  pauzeert het zichzelf, met uitleg en de knop **Hervatten**. Usenet-providers → Autopilot toont
  "opnieuw geprobeerd N · gelukt X %".
- **Servers testen in plaats van blind aanzetten.** MSO zet een kapotte server pas uit als SABnzbd's eigen servertest
  (dezelfde als 'Test server' in SABnzbd) het probleem bevestigt. Slaagt de test, of meldt de provider alleen "te veel
  verbindingen", dan blijft de server aan. Een uitgezette server laat MSO elke 6 uur testen (na een week dagelijks, een
  verlopen account wekelijks; MSO geeft nooit op) en zet hem pas na een geslaagde test weer aan. "Werkt weer" telt pas
  als SABnzbd hem echt gebruikt. De blinde proef-aanzet uit 1.5.0 blijft alleen als terugval voor een SABnzbd zonder
  servertest.
  - **Testen** bij een server werkt nu ook zonder inloggegevens in MSO, en in elke modus (het verandert niets).
  - Verlopen account: geen proeven meer, één rustige melding; MSO ziet een verlenging vanzelf.
  - Wisselende testresultaten geven één advies: "waarschijnlijk te veel verbindingen of een gedeeld account".
  - Verwerp je het voorstel om een server weer aan te zetten, dan komt het niet bij elke geslaagde test terug.
  - Een vergrendelde SABnzbd-configuratie geeft één rustige melding in plaats van mislukte pogingen.
- **Vervaldata en blokken.** Staat in SABnzbd bij een server een vervaldatum of quotum, dan herinnert MSO je 14, 7 en
  1 dag vóór het verlopen, toont het wat er van een blok over is en wanneer het op is, en vult het een lege verlengdatum
  onder Kosten in (een datum die je zelf invulde blijft staan). Servers met een quotum worden bij importeren een
  blokaccount. SABnzbd's vooraankondiging "is expiring in N day(s)" en zijn snelheidstip na een test gelden niet meer
  als serverfout. Had je al een blokgrootte ingevuld vóór 1.6.0, sla die dan één keer opnieuw op; tot dan geen
  voorspelling of herinnering.
- **Categorie '*' opgehelderd.** MSO legt uit waarom SABnzbd downloads in zijn standaardcategorie '*' zet en waar je het
  oplost: (a) SABnzbd kent de categorie niet die Sonarr/Radarr meesturen, (b) er wordt geen categorie meegestuurd
  (oplossen in MediaGuard of in Sonarr/Radarr), (c) de categorieën kloppen en de downloads komen van een andere bron.
  Daarvoor leest MSO vier MediaGuard-routes (alleen lezen; rol viewer volstaat). Bij (a) staat er één voorstel in
  "Wacht op jou" om de categorie in SABnzbd aan te maken: altijd met jouw goedkeuring, ook in Autopilot, omdat
  MediaGuard downloads in die categorie daarna volgens zijn eigen regels opruimt. Verwerp je het, dan zet MSO het niet
  opnieuw klaar. De oude tip "pas het veld Category in Sonarr aan" is weg.

### Doelenkaart en eerlijke meting
- **Doelenkaart** bovenaan Overzicht: per doel de startwaarde, de waarde nu, het doel, "dag X van 28", het verloop per
  dag en één zin over wat het doel tegenhoudt, met een link naar de actie die nodig is. Servers die MSO uitzette omdat
  ze niet werken, staan apart en tellen niet als falend. De start van de proefperiode is instelbaar (standaard: de dag
  dat Autopilot voor het eerst aanging).
- **De startwaarde ligt vast**: de stand in het eerste uur van de proefperiode (faalratio: de 7 dagen ervoor). Wat MSO
  daarna doet, verandert hem niet; zo zie je eerlijk wat er sindsdien beter werd.
- **Eerlijke faalratio**: per aflevering/film (netto: wat later alsnog binnenkwam telt als gelukt) naast per poging
  (bruto), altijd met het aantal, en "te weinig data" onder de 20. Afleveringen met een datum (dagelijkse shows,
  sport) tellen per uitzenddatum; wat niet betrouwbaar te herkennen is, telt per poging.
- **Vastgelopen torrents tellen niet meer eeuwig mee**: een mislukking telt op het moment dat hij mislukte, niet bij
  elke nieuwe waarneming. Dat geldt voor Overzicht, de controle na wijzigingen, herstel, kosten, de AI-uitleg en de
  betrouwbaarheid van indexers. Bestaande gegevens worden bij de upgrade aangevuld.

### Rust in de app
- **Eén inbox "Wacht op jou"**: elke beslissing staat er één keer. De losse "Verdachte torrent verwijderen"-meldingen vallen
  weg zodra ze in het opruimvoorstel zitten; dat staat er één keer ("41 nep-downloads opruimen (+3 nieuw sinds
  gisteren)"). Het getal is overal gelijk: op Overzicht (direct onder de doelenkaart, of één regel "Niets wacht op
  jou ✓"), in de topbalk (ook op de telefoon), als badge bij Automatisering en in de tabtitel. De kaarten Aanbevelingen en Wijzigingen
  tonen alleen nog wat daar niet al staat.
- **"Wat MSO deed" zonder ruis**: synchronisaties en controles tellen niet meer als actie (in Alleen-lezen staat er dus
  gewoon 0) en staan samen op één statusregel. Per wijziging zie je wat MSO deed, waarom, en wat het effect was
  ("faalratio 31 % → 12 % (n=140) · behouden"), met een knop **Terugdraaien**. Dagen zijn lokale dagen.
- **Voetafdruk** (Automatisering → Voetafdruk): per systeem wat MSO nu veranderd heeft (origineel → MSO → nu), elk uur
  bij de bron gecontroleerd (alleen lezen, in elke modus). **Alles terugzetten** zet de oorspronkelijke waarden terug
  met de gewone controle; wat jij zelf veranderde of wat niet terug kan, wordt overgeslagen met uitleg.
- **Eigen keuze respecteren**: zet je een waarde die MSO had ingesteld zelf terug (in SABnzbd, qBittorrent of Prowlarr,
  of met Terugdraaien), dan past MSO die 30 dagen (instelbaar) niet opnieuw automatisch toe. Je krijgt één melding ter
  info; voorstellen blijven mogelijk.

### Veiligheid
- **Noodrem**: na 3 automatische terugdraaiingen of 5 mislukte automatische wijzigingen in 24 uur (instelbaar)
  pauzeert MSO zijn eigen automatische schrijfacties. Bovenaan elke pagina staat dan een banner met **Hervatten**.
  Synchronisaties, controles en het klaarzetten van voorstellen gaan door.
- **Afkoelen**: mislukt een automatische wijziging, dan wacht MSO 1 uur, daarna 4 uur; na 3 keer staat er één melding in
  "Wacht op jou" en probeert MSO het hooguit één keer per dag. Mislukte pogingen tellen mee voor de uurlimiet.
- **Autopilot terugdraaien** (laatste 24 uur of 7 dagen) onder Automatisering → Veiligheid: eerst de exacte lijst, dan
  bevestigen. Verwijderingen staan er nooit in.
- **Terugdraaien met driftcontrole**: is een waarde intussen buiten MSO gewijzigd, dan laat MSO die staan
  ("overgeslagen") en behandelt het dat als jouw keuze. Elke teruggezette waarde wordt teruggelezen; lukt dat niet, dan
  meldt MSO geen "teruggedraaid" maar een fout.
- **Crash-veilig toepassen**: MSO legt vóór de eerste schrijfactie vast wat de oude waarden waren en slaat elk resultaat
  meteen op. Wordt MSO midden in een wijziging herstart, dan rondt het die binnen enkele minuten af of zet het de al
  geschreven waarden terug, gecontroleerd bij de bron. In Alleen-lezen schrijft MSO ook dan niets. Een wijziging wordt
  nooit door twee processen tegelijk toegepast of teruggedraaid.
- **Nep-releases tegenhouden bij MSO's eigen verzendingen**: voordat MSO iets naar qBittorrent of SABnzbd stuurt (vanuit
  Zoeken, Ontbrekend & herstel, een voorstel of automatisch) controleert het de echte inhoud: de bestandenlijst van het
  .torrent-bestand, de naam in een magnetlink of de bestandsnamen in de NZB. Zit er een programma, script of vermomde
  extensie (.mkv.exe) in, dan weigert MSO ("Beleid weigert deze release: …"); bewijst de bestandenlijst zelf dat het nep
  is, dan komt de release ook op de blacklist. Zoeken toont zulke resultaten met een rode badge "Nep-release
  (uitvoerbaar bestand)", onderaan en met een uitgeschakelde Verstuur-knop; MSO's eigen Newznab/Torznab-feed laat ze
  weg. Vervangers met een onwaarschijnlijk kleine grootte (≥ 720p-aflevering onder
  50 MB, ≥ 720p-film onder 200 MB) worden afgewezen.
- Kleiner: een weer ingeschakelde SABnzbd-server wordt beoordeeld op zijn eigen status, niet op de faalratio van alle
  downloads; MSO herkent SABnzbd's 0/1-waarden als aan/uit; geen "Terugdraaien aanbevolen" meer voor wijzigingen die
  niet terug kunnen; een SABnzbd-categorie die MSO aanmaakte, wordt nooit automatisch verwijderd (terugdraaien alleen
  met de hand, door een beheerder).

### Onderhoud
- **Bewaartermijnen**: elke dag ruimt MSO zijn eigen oude achtergrondtaken, metingen (na 30 dagen samengevat per uur,
  400 dagen bewaard), automatiseringslog (180 dagen) en inlogpogingen (90 dagen) op en schoont het daarna de database
  op. Het auditlogboek blijft altijd. De termijnen staan onder Instellingen → Systeem; Systeem → Onderhoud toont wat er
  is opgeruimd en hoe groot de database is.
- **Lichtere back-ups**: gegevens die MSO zelf opnieuw ophaalt (zoekcache, MediaGuard-cache, header-index,
  torrent-metingen) gaan niet meer mee; na terugzetten haalt MSO ze zelf op. Vóór elke back-up controleert MSO de vrije
  ruimte (te weinig → overslaan, met een melding in "Wacht op jou"). Veiligheidsback-ups van vóór een herstel verdwijnen
  na 30 dagen (de nieuwste 2 blijven).
- **Back-up vóór upgrade**: zijn er databasewijzigingen, dan maakt MSO eerst automatisch een back-up (soort "Vóór
  upgrade"; de nieuwste 3 blijven). Lukt dat niet, dan start MSO gewoon door met een duidelijke logregel.
- **Herstel van oudere back-ups**: een back-up van een oudere versie (bijvoorbeeld de "Vóór upgrade"-back-up) herstel je
  nu gewoon in de app; MSO zet het schema eerst terug en werkt het daarna meteen weer bij. Een back-up van een nieuwere
  versie weigert MSO. Modus, pauze en noodrem blijven bij een herstel zoals ze waren.
- **Koppelingsbewaking**: de achtergrondtaken houden zelf bij of SABnzbd, qBittorrent en Prowlarr bereikbaar zijn.
  Overzicht zegt "qBittorrent onbereikbaar sinds 2 d 4 u" of "Sleutel geweigerd" in plaats van honderden mislukte taken.
  Taken voor een onbereikbare koppeling draaien hooguit elk kwartier; bij herstel haalt MSO meteen alles bij. Een
  melding in "Wacht op jou" komt alleen bij een sleutel die langer dan 15 minuten geweigerd wordt, of een koppeling die
  langer dan 24 uur onbereikbaar is.

### Migraties
Automatisch bij de start, direct na de back-up vóór upgrade. Terug naar 1.5.0: `mso migrate --down 501` met het
1.6.0-image, daarna de 1.5.0-tag (stappen in `docs/MIGRATION.md`).
- `0600` — basis: wanneer een server echt hersteld is en wanneer hij voor het laatst getest werd; tabel voor jouw eigen
  keuzes (`change_overrides`).
- `0601` — corrigeert de opgeslagen artikeltellers (gevonden in plaats van mislukt) en onthoudt per server wanneer hij
  voor het eerst aan stond (dekkingswinst).
- `0602` — servertests, vervaldatum en quotum per server, en de beginstand van een blok.
- `0603` — bestaat niet: de categoriediagnose had geen schemawijziging nodig.
- `0604` — `failed_at` (het moment van mislukken; bestaande rijen aangevuld) en de dagelijkse momentopnames van de
  doelenkaart.
- `0605` — status "bezig met toepassen", het afkoelregister en welke wijzigingen automatisch zijn toegepast.
- `0606` — uursamenvattingen van metingen, extra indexen en de koppelingsbewaking (sinds wanneer, laatst gelukt, soort
  storing).
- `0607` — de voetafdruk (`change_footprint`).

### Bewust niet gebouwd
Uit de review vielen deze ideeën af, omdat ze geen van de drie doelen verschuiven of nieuw risico geven:
- een status "levert niet" voor servers: met de gecorrigeerde teller zou die nooit afgaan;
- indexers automatisch lager zetten of uitzetten in Prowlarr: een nieuwe schrijfroute met doorwerking naar
  Sonarr/Radarr, terwijl MSO dode indexers al overslaat;
- elke download aan een Prowlarr-indexer koppelen via MediaGuard;
- kostenadvies per abonnement (houden, opzeggen, verlengen) en dekkingsadvies per Usenet-account;
- een dekkingstest op je mislukte downloads (vraagt inloggegevens die je bewust niet invulde);
- "haperende" servers aanhouden en verbindingen bijstellen;
- nep- en dode torrents vrijgeven in Sonarr/Radarr en controle na de opruiming (MediaGuard doet dit al);
- bredere nep-herkenning en veiligheidsinstellingen in SABnzbd;
- elk herstel volgen tot de import, een weekrapport, een startplan voor Autopilot en een zelfcontrole na de upgrade;
- MSO-meldingen in MediaGuards eigen "Wacht op jou" (moet in MediaGuard gebouwd worden).

### Niet getest
Gebouwd en getest in een Linux/Docker-sandbox met mock-diensten (zie `TEST_RESULTS.md`). Het SABnzbd-gedrag
(artikelteller, verwerkingslog, servertest, vervaldata, categorieën) is nagekeken in de broncode van SABnzbd 5.1.3, de
nieuwe MediaGuard-routes in de broncode van MediaGuard. Het gedrag van Sonarr/Radarr bij categorie '*' is niet in hun
broncode nagelezen. **Niet** getest op je echte TrueNAS en niet tegen echte Prowlarr, SABnzbd, qBittorrent, MediaGuard of
providers: controleer dat met `docs/HARDWARE_ACCEPTANCE.md` §10.

## [1.5.0] — 2026-11-05

Set it and forget it. Verwerkt ronde 4 van de echte TrueNAS-test (1.4.0).

### Nieuw
- **Autopilot**, een derde bedrijfsmodus naast Alleen-lezen en Beheermodus, ook te kiezen in de setupwizard.
  - MSO past alles wat terug te draaien is zelf toe, controleert het bij de bron, meet het effect en draait terug bij
    verslechtering.
  - Uitschakelen mag automatisch alleen in Autopilot.
  - **Verwijderen nooit automatisch**, in geen enkele modus.
- **Preventie**: qBittorrent downloadt geen uitvoerbare bestanden meer (`excluded_file_names`). Je eigen patronen blijven
  staan, de wijziging is terug te draaien, en patronen die video of mappen zouden raken worden geweigerd.
- **Malware**: één opruimvoorstel staat altijd klaar en blijft actueel ("Wacht op jou"); nooit automatisch.
- **Usenet**:
  - kapotte servers automatisch uit (nooit de laatste werkende), met proef-heraanzet en oplopende wachttijd;
  - mislukte downloads door serverproblemen worden automatisch opnieuw geprobeerd (begrensd).
- **Herstel**: in Autopilot vraagt MSO MediaGuard automatisch opnieuw te zoeken (MediaGuard blijft eigenaar, quota
  gelden). Items die MediaGuard niet meer wil, worden stil overgeslagen.
- **Tuning**: in Autopilot gaan de extreme waarden automatisch naar de veilige grens; daarna volgt tuning per waarde met
  meting en terugdraaien.
- **Overzicht**:
  - "Wat MSO deed" (24 uur en 7 dagen) en "Wacht op jou";
  - Automatisering → Samenvatting per dag;
  - Systeem → Automatische taken.

### Opgelost (ronde 4)
- De beschikbaarheidstabel is nooit meer leeg: zonder inloggegevens gebruikt MSO de serverstatistiek van SABnzbd,
  gemarkeerd als passief.
- Gratis publieke torrent-indexers vragen geen prijs meer en staan standaard verborgen bij Kosten.
- De MediaGuard-quotumkaart toont actuele cijfers.
- Teruggedraaide wijzigingen tonen "n.v.t. (teruggedraaid)" bij Controle.
- De verdachte bestandsnaam staat niet meer dubbel in het opruimvoorstel.
- Uitleg bij de categorie "standaard (*)" in Downloads.
- Nederlandse 404-pagina.

### Bekend
- Sonarr/Radarr-blokkade voor releases die niet meer in de wachtrij staan, vereist een nieuwe MediaGuard-route (zie
  `docs/dev/MEDIAGUARD_BLOCKLIST_REQUEST.md`). Tot die tijd voorkomt de preventie-instelling dat de bestanden opnieuw
  binnenkomen.

## [1.4.0] — 2026-11-05

Van melden naar oplossen. Elk probleem wordt een voorstel dat je met één goedkeuring uitvoert en dat MSO na het
toepassen controleert bij de bron.

### Harde grenzen
- Verwijderen of uitschakelen kan alleen als voorstel met **Toestemming nodig**: voorbeeld → goedkeuren → toepassen →
  controle → audit. Het wordt **nooit** automatisch toegepast. Wat niet terug te draaien is, staat zo in het voorbeeld en
  wordt bij terugdraaien overgeslagen.
- De vergrendelde regel heet nu "Nooit **zelfstandig** bestanden verwijderen". De enige uitzondering zijn verdachte
  downloads na expliciete goedkeuring.

### Nieuw
- **Malware opruimen** per torrent of als bundel:
  - de release op de blokkeerlijst van Sonarr/Radarr via MediaGuard (`DELETE …/queue/{id}`, alleen met
    `blocklist=true&remove_from_client=false`, alleen binnen deze opruiming);
  - de release op MSO's eigen blokkeerlijst;
  - torrent + bestanden verwijderen in qBittorrent, met controle.
  Wijzigt de lijst tussen voorbeeld en toepassen, dan weigert MSO de hele bundel.
- **Kapotte Usenet-servers uitzetten** met één goedkeuring (terug te draaien). Herinneringen bij verlopen accounts of
  aanhoudende fouten. De faaloorzaak noemt per server de werkelijke status.
- **Mislukte download → andere kandidaat**: voorstel *Versturen naar SABnzbd/qBittorrent* met de beste kandidaat die aan
  de MediaGuard-eisen voldoet. De mislukte release gaat op de blokkeerlijst. Automatisch versturen kan als opt-in
  (standaard uit).
- **Tuning**: één bundelvoorstel brengt alle extreme qBittorrent-waarden naar de veilige grens. Daarna volgt tuning per
  waarde met meting.
- **Controle na een wijziging** (evaluators):
  - gemeten wordt faalratio, tijd tot voltooiing en downloadsnelheid;
  - uitkomst beter/gelijk/slechter/onvoldoende per wijziging;
  - automatisch terugdraaien bij verslechtering (`change.auto_rolled_back`).
- **Beschikbaarheid**: automatische STAT-steekproef na elke verstuurde NZB, binnen een dag- en maandbudget.
  Inloggegevens per provider worden eenmalig gevraagd.
- **Eigen bron**: de header-index levert RSS-items en NZB's via het Newznab-endpoint. Met een eigen bron (header-index,
  Spotweb of directe indexer) accepteert Prowlarr MSO als indexer.
- **Kosten**: providers en indexers worden automatisch als dienst overgenomen. Per dienst zie je de kosten per geslaagde
  GB en per geslaagde download, plus het unieke aandeel.

### Opgelost (testrapport 1.3.1)
- Trage Prowlarr: één automatische herhaalpoging met een ruimere time-out, een knop "opnieuw proberen", maximaal 2
  gelijktijdige zoekopdrachten, en samenvoegen van dubbele zoekopdrachten.
- De SABnzbd-verbindingstest toont weer "x server(s) melden fouten".
- Betrouwbaarheid wordt als percentage getoond (was "1 %").
- Wachtrijstatussen van SABnzbd en de audit-samenvattingen zijn in het Nederlands.
- De blokken bij Torrentclient verversen direct na toepassen of terugdraaien.

## [1.3.1] — 2026-11-05

Verwerkt de hertest op de echte TrueNAS (Claude Cowork, 5 nov 2026: 25 geslaagd, 1 mislukt).

### Opgelost
- **Registreren in Prowlarr**: MSO controleert per type (Newznab/Torznab) of er eigen bronnen achter het endpoint
  zitten. Zonder eigen bronnen weigert MSO met uitleg in plaats van een HTTP 400 van Prowlarr; met eigen bronnen
  beantwoordt het de testzoekopdracht van Prowlarr met echte resultaten.
  - De registratie gebruikt de generieke Newznab/Torznab-template (niet langer de AnimeTosho-preset) en geen `forceSave`.
  - Het voorbeeld toont een Nederlandse samenvatting.
- **Back-up terugzetten** bewaart auditregels van na de back-up. Openstaande taken uit de back-up worden geannuleerd
  (geen extra "Handmatige" back-up meer na een herstel).
- **Mislukte inlogpogingen** staan in de audit (zonder wachtwoord).
- **Usenet**: verouderde MSO-tests (vooral die zonder inloggegevens) overschrijven de actuele SABnzbd-status niet
  meer; geen onterecht "Inloggen geweigerd bij eweka" meer.
- **Automatisering**: een foutmelding van het ene voorstel verschijnt niet meer bij een ander.
- **MediaGuard-wachtlijst**: grote lijsten (8000+) worden in rondes gesynchroniseerd en verdwenen items opgeruimd.
  Het overzicht en Ontbrekend & herstel tonen duidelijk wat er gesynchroniseerd is en wanneer.
- Indexerstatus is eenduidig. De zekerheidsscore toont waarop hij is gebaseerd.
- Laatste Engelse resten vertaald ("run", "Group", "degraded", "discarded").
- In Alleen-lezen wordt uitgelegd dat zoekverzoeken (via MediaGuard of handmatig versturen) wel kunnen.

## [1.3.0] — 2026-11-05

Verwerkt het testrapport van de eerste echte TrueNAS-test (Claude Cowork, 4 nov 2026).

### Opgelost
- **Bedrijfsmodus** omschakelbaar na de setup (Instellingen → Systeem → Bedrijfsmodus; de topbar-badge linkt erheen).
  Pauzestatus overal live.
- **Snelheidslimiet toepassen** mislukte door KiB-afronding van qBittorrent: limieten worden nu op hele KiB berekend
  en gecontroleerd. Een mislukte wijziging toont geen "Toepassen" meer. Het automatische herstel staat apart in de audit.
- **Verdachte torrents** (`.exe`, `.scr`, `.zipx`, dubbele extensies in media-downloads) krijgen een waarschuwing en
  een advies, met de bron erbij. Er wordt niets verwijderd.
- **Extreme qBittorrent-waarden** (verbindingen/uploadslots buiten de veilige grenzen) worden gemeld, met een
  voorstel per parameter.
- **SABnzbd-serverfouten** (inloggen geweigerd, account verlopen, toegang geweigerd) zijn zichtbaar per server, met
  advies, en het prioriteitsadvies houdt er rekening mee.
- **Providers zonder inloggegevens** krijgen de status "Gegevens ontbreken" in plaats van "Inloggen geweigerd".
  Geen massale statuswijziging meer.
- Uitpakfouten krijgen een Nederlandse oorzaak, met de waarschijnlijke serveroorzaak erbij.
  SABnzbd-categorieën worden automatisch gekoppeld (aanpasbaar).
- **Zoeken**: geen serie-afleveringen meer in filmcategorieën; de titel weegt zwaarder; jaar-match.
  Indexers zonder series/films (XXX/Console/Books) tellen niet mee. Indexers die volgens Prowlarr niet werken worden
  gemarkeerd en overgeslagen. Het bronrapport toont de status per indexer. Freeleech staat alleen nog bij expliciete
  private trackers.
- **Dialogen** blijven volledig in beeld; het bevestigingsveld verliest geen focus meer tijdens het typen.
- Nederlandse statussen (Geslaagd/Bezig/Mislukt/Verstuurd/Gezond/Verstoord) in plaats van "Onbekend"/Engels.
- De back-uplijst en verbindingskaarten verversen vanzelf; de impact van wijzigingen staat leesbaar.
- **Ontbrekend & herstel** heeft paginering (50/100/200) en details in een dialoog. Het MediaGuard-quotum werkt na
  een zoekverzoek direct bij.
- MediaGuard: melding als naast de API-sleutel ook gebruikersnaam en wachtwoord zijn ingevuld, met een knop om die te wissen.

## [1.2.0] — 2026-11-04

### Toegevoegd
- **MediaGuard-API-sleutels** als aanbevolen inlogmethode (header `X-Api-Key`, eigen rol, geen sessie of CSRF):
  - nieuw veld bij de koppeling en keuze *API-sleutel* op het MediaGuard-scherm;
  - rolcontrole via `/auth/me`;
  - duidelijke melding bij een ongeldige of ingetrokken sleutel, of bij een MediaGuard zonder sleutels;
  - `mgk_`-sleutels worden overal in logs gemaskeerd.
- `docs/MEDIAGUARD_API_KEYS.md`: contract en kant-en-klare bouwprompt om API-sleutels aan MediaGuard toe te voegen.
- Mock-MediaGuard ondersteunt API-sleutels (operator, viewer, ingetrokken, oude versie). Er zijn 8 nieuwe tests.

## [1.1.1] — 2026-11-04

### Gewijzigd
- Standaardpoort van **30081** naar **38081** (image, Custom App-handleiding, beide compose-bestanden, docs).
  30000–31000 is het bereik dat TrueNAS-catalogus-apps standaard gebruiken; 30081 kon daardoor botsen.
  Bestaande installaties: alleen de poort in de app-instellingen aanpassen; data blijft behouden.

## [1.1.0] — 2026-11-03

### Toegevoegd
- **Alles-in-één image** `ghcr.io/lvos04/media-source-optimizer` (database, API, worker, web in één container),
  installeerbaar als gewone TrueNAS **Custom App** zonder YAML, wachtwoorden of variabelen
  (`docs/TRUENAS_CUSTOM_APP.md`, ook als `deploy/truenas/compose.allinone.yaml`).
- Supervisor: zet bij start als root eenmalig de eigenaar van `/appdata` op 568 en draait daarna alles als 568;
  duidelijke Nederlandse foutmelding bij verkeerde maprechten; ruimt een achtergebleven `postmaster.pid` op na een
  harde stop; herstart gecrashte onderdelen met backoff; stopt netjes (< 1 s gemeten).
- PostgreSQL alleen via een unix-socket in de container: geen databasepoort en geen databasewachtwoord.
- End-to-end test `tests/stack/allinone_test.py` (9 scenario's), draait in de release-workflow vóórdat er iets
  wordt gepubliceerd.

### Opgelost
- Back-up/herstel werkt nu ook met een unix-socket-databaseadres (`?host=/pad`).

## [1.0.0] — 2026-11-02

Eerste volledige versie.

### Platform
- FastAPI-core + worker op één codebase, PostgreSQL 16, Next.js-UI; TrueNAS SCALE *Install via YAML*-stack
  (`deploy/truenas/compose.yaml`) met init/migrate-containers, non-root (568), read-only rootfs,
  `cap_drop: ALL`, limieten, healthchecks, gepinde images.
- Login, rollen admin/operator/viewer, CSRF, brute-force- en API-rate-limits, SSRF-afscherming,
  AES-256-GCM-geheimenkluis, redactie van geheimen in logs, audittrail.
- Change sets met preview, goedkeuring, afwijkingsdetectie, automatische terugrol bij gedeeltelijke fout en
  rollback; standaard alleen-lezen; pauzeknop voor automatisering; vergrendelde integriteitsgrenzen.
- Herstartveilige jobs (lease + heartbeat), idempotente schema's, back-up/herstel met checksum en vangnet.
- Omkeerbare migraties 0001, 0002, 0100, 0200, 0300, 0400, 0500.

### Usenet-engine
- SABnzbd-connector, historie-ingest met faaloorzaak + zekerheid, per-server statistiek.
- Providers (Eweka, Newshosting, blockaccounts), eigen NNTP-client, statuscontrole met foutclassificatie.
- Artikelbeschikbaarheid op daadwerkelijk aangevraagde artikelen, bijdrage/overlap met dekking.
- Prioriteitsadvies via change set; opt-in begrensde header-index met obfuscatiedetectie.

### Torrent-engine
- qBittorrent-connector (API-key/sessie, capability-detectie), knelpuntdiagnose, valse ETA, recheck-advies.
- Guardrails voor private trackers en seedverplichtingen, uploadplafond, wachtrijbeleid.
- Tuning één parameter tegelijk met evaluatievenster, hysterese en rollback; opt-in cross-seed op payload.
- Opslag, reserve, hardlink-bewustzijn (read-only).

### Zoeken & indexers
- Prowlarr-connector + sync naar bronnen met anime/normal-scopes; directe Newznab/Torznab/Spotweb-indexers.
- Zoekaggregator met bronrapport, dedupe op identiteit, betrouwbaarheidsscore.
- Eigen Newznab- en Torznab-endpoints met sleutelbeheer, quota, paginering en foutcodes.
- Origin graph + aanroeperdetectie tegen zoeklussen; conflictdetectie met MediaGuard's Prowlarr-reconcile.

### MediaGuard & herstel
- Optionele MediaGuard-koppeling met capability-detectie en compatibiliteitsmatrix; standalone fallback.
- Herzoek uitsluitend via `POST /api/v1/wanted/{id}/search` met quotum, idempotency en backoff.
- Source Baseline, fallback-kandidaten met kwaliteitspoorten, blacklist met TTL.

### Kosten, overzicht, automatisering, AI
- Kosten per dienst, analyse, *wat als ik dit uitschakel*-simulatie met onzekerheid, scenario's.
- Overzichtsdashboard; automatisering per gebied; evaluatiehaken.
- Opt-in AI-adviseur met maandbudget en deterministische fallback (standaard uit).

### UI
- 15 schermen + login + setup-wizard in MediaGuard-huisstijl (afgeleide kopie, zie `apps/web/PROVENANCE.md`),
  donker/licht/systeem, desktop en mobiel, WCAG 2.2 AA (axe) gecontroleerd.
