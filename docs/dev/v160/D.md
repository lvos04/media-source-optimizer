# WP-D — Eerlijke doelenkaart en meting (C01, C02) — notities

Branch `wp/D` (basis 48f90dc). Migratie **0604** (`0604_v160_goals.sql`, met werkende `-- migrate:down`).

## Wat er veranderd is

### 1. `download_outcomes.failed_at` en eerlijke vensters
- Nieuwe kolom `failed_at` (migratie 0604) + expressie-index op `COALESCE(finished_at, failed_at)`.
- `domain/outcomes.upsert_outcome` beheert de kolom: gezet bij de overgang naar `failed` (op `row['failed_at']` als de
  schrijver het moment kent, anders `now()`), behouden zolang de uitkomst `failed` blijft, gewist zodra hij `failed`
  verlaat (een torrent die weer voortgang maakt en later opnieuw vastloopt krijgt een nieuw moment).
- `torrent/ingest.py` geeft het moment mee voor een vastgelopen torrent: laatste voortgang + `torrent.stalled_fail_hours`
  (begrensd op nu); foutstatus/ontbrekende bestanden/nep-release: het moment van de eerste waarneming.
- Backfill van bestaande mislukte rijen: `finished_at`; anders (torrents, via `torrent_state`) vastlopen = laatste
  voortgang + drempel, nep-release = eerste waarneming, overig = laatste voortgang/eerste waarneming (nooit later dan
  `observed_at`); anders `started_at`; anders `observed_at`.
- Nieuwe helper `outcomes.outcome_at(alias)` = `COALESCE(finished_at, failed_at)`; alle vensters die
  `COALESCE(finished_at, observed_at)` (of kaal `observed_at`) gebruikten zijn omgezet:
  `overview/api.py` (failures-blok), `automation/evaluators.failure_series`, `recovery/baseline.py`,
  `recovery/fallback.py` (de twee verwerkingsvensters; de vergelijking "opnieuw waargenomen na de laatste beslissing"
  `… > f.updated_at` is bewust ongemoeid gelaten), `ai/explain.py`, `costs/analysis.py` (2×),
  `indexers/reliability.py` (vals-positieven per bron).
- Gevolg: een torrent die al weken vastzit telt niet meer eeuwig als "mislukt in de laatste 24 u"; een torrent die
  zonder `completion_on` als geslaagd staat telt ook niet meer eeuwig mee als recente download (kosten/ratio).

### 2. Faalratio netto (per aflevering/film) naast bruto (per poging)
- `outcomes.item_key(title, media_category)`: identiteit van het gewenste item via de bestaande releasenormalisatie
  (`release.title_part` + `parse_title`): genormaliseerde naam +
  - S01E02 / seizoenspakket (met het jaar als dat vóór de nummering staat: `Doctor.Who.2005.S01E01` ≠
    `Doctor.Who.S01E01`), of de absolute anime-aflevering;
  - een uitzenddatum (dagelijkse shows, nieuws, worstelen, sport per datum: `WWE.Raw.2026.11.03` → `wwe raw|2026-11-03`),
    plus de wedstrijd als de titel die noemt (`NBA.2026.11.03.Lakers.vs.Celtics`);
  - voor een film het jaar — maar alleen als `media_category` film zegt, of (categorie onbekend) als er na het jaar
    niets nummerachtigs staat. `Formula1.2026.Round20`, `NFL.2026.Week.10`, `MotoGP.2026.Brazil.Grand.Prix.Race` en
    een serie (`tv*`) zonder nummering/datum krijgen **geen** identiteit.
  Zonder identiteit (ook geobfusceerd) is elke poging een eigen item: nooit samengevoegd met een andere aflevering,
  dus netto kan nooit "gehaald" zeggen omdat een andere aflevering van dezelfde show lukte.
- `outcomes.item_keys(rows, links)`: koppelt daarnaast SABnzbd-retries die een nieuw nzo_id kregen
  (`usenet_retries.new_nzo_id`).
- `outcomes.failure_ratios(rows, start, end)`: bruto = mislukte pogingen / afgeronde pogingen; netto = items met een
  poging in het venster die tot `end` nooit lukten / items met een poging in het venster. n bij beide,
  `insufficient` bij n < 20 (`MIN_RATIO_N`). Een eerder geslaagde download van hetzelfde item (tot 30 dagen vóór het
  venster) telt als binnengekomen, zodat een mislukte upgrade van een aflevering die er al is niet als "niet
  binnengekomen" telt.
- `GET /api/v1/overview` → `failures.ratio = {net:{value,failed,n,insufficient}, gross:{…}, min_n}` (bestaande velden
  `failure_rate_7d` enz. ongewijzigd) en een compact `goals`-blok (status, waarde nu en doel per doel).

### 3. Doelenkaart
- Nieuw `features/overview/goals.py` met `GET /api/v1/overview/goals` (kijker):
  - **proefperiode**: start = instelling `goals.trial_start` (JJJJ-MM-DD, categorie Automatisering; leeg =
    automatisch), anders de eerste keer dat Autopilot aanging (audit `system.mode` → autopilot of
    `setup.completed` met mode autopilot), anders de setup-datum (audit setup / eerste gebruiker). Lengte 28 dagen;
    `day` (dag X van 28), `ended`, `not_started`, `end_date`; een ongeldige datum wordt genegeerd met een
    waarschuwing in de kaart.
  - **lokale dagen**: `TZ` van de container (geldige IANA-naam), anders Europe/Amsterdam.
  - per doel: `start` (waarde + bron), `now`, `target`, `status` (met / not_met / insufficient / unavailable),
    `direction` (beter/slechter/gelijk t.o.v. de start), `trend` (per lokale dag) en `blocker` (één zin + link).
  - **startwaarde** = de stand bij de start, nooit een latere meting (zie "Na de review" hieronder):
    faalratio over de 7 dagen vóór de start (alleen die rijen worden gelezen); verdachte torrents die op enig moment
    in het **eerste uur** vanaf de start aanwezig waren; servers waarvoor SABnzbd in dat eerste uur een foutstatus gaf.
    Keek MSO bij de start nog niet mee, dan telt het eerste uur vanaf het moment dat het begon (eerste torrent gezien /
    SABnzbd-koppeling voor het eerst opgeslagen). Zolang dat uur loopt is de waarde `provisional` ("voorlopig");
    daarna bevriest de uurlijkse taak hem per proefperiode-start in `system_state['goals.start']` (`frozen`), zodat
    hij nooit meer verandert en opruimen van geschiedenis hem niet raakt. Een andere startdatum = opnieuw bepaald.
  - **trend** = per lokale dag de stand aan het eind van die dag (vandaag: nu): momentopname (laatste uurlijkse run
    van de dag) of anders reconstructie op het einde van de dag; faalratio zoals bekend aan het eind van de dag.
  - **richting** (beter/slechter/gelijk) alleen als beide kanten genoeg data hebben: bij de faalratio niet als de
    start minder dan 20 items had.
  - **Faalratio < 10 %**: status volgt *netto*; bruto staat er altijd naast (met n). Blokkade:
    "Te weinig data: N aflevering(en)/film(s) …" of "Meeste mislukkingen: <oorzaak> (x van y)".
  - **0 verdachte torrents**: dezelfde `suspicious_payload`-bevinding als de Torrentclient-pagina; `new_7d` = alle
    verdachte torrents die deze week binnenkwamen, ook die al opgeruimd zijn (werkt de preventie?); aantal in een open
    opruimvoorstel. Blokkade: "N wachten op jouw goedkeuring (opruimvoorstel) · M nieuw in 7 dagen" (met link naar
    het voorstel) of "N verdachte torrent(s) zonder opruimvoorstel; maak er een op Torrentclient …".
  - **0 falende servers**: ingeschakeld én falend (`usenet_sab_servers.enabled` + `serverstatus.FAILING`). Servers die
    MSO uitzette en die nog uit staan, staan apart met de tekst "N uitgezet omdat ze niet werken; herstellen of
    verwijderen in SABnzbd is jouw keuze": via Autopilot (autoheal-rij) **of** via een goedgekeurde, toegepaste en niet
    teruggedraaide wijziging waarvan de laatste `servers/<naam>/enable`-op `after` 0 is (beheermodus, "N kapotte
    servers uitschakelen"; `via: change` + `change_id`). Een server die de gebruiker tijdens een proef zelf uitzette
    (autoheal `last_result` "buiten MSO uitgeschakeld") telt niet als door MSO uitgezet. Servers in proefronde apart
    geteld. Blokkade: "Ingeschakeld maar falend: xsnews (account verlopen) · …" (+ "— uitschakelen wacht op jouw
    goedkeuring" als er een uitschakelvoorstel openstaat).
  - niet gekoppeld → `unavailable` met reden ("qBittorrent is niet gekoppeld", "SABnzbd is niet gekoppeld",
    "SABnzbd heeft nog geen serverstatus gegeven").
- **Dagelijkse momentopnames**: tabel `goal_snapshots` (dag, metrics), uurlijkse taak `overview.goals_snapshot` die de
  rij van de lokale dag bijwerkt (de laatste run van de dag wint = stand aan het eind van de dag) en daarna de
  startwaarden bevriest waarvan het eerste uur voorbij is. Voor dagen zonder momentopname reconstrueert de kaart de
  stand aan het eind van de dag: verdachte torrents (`torrent_state.first_seen` / verwijdermoment) en falende servers
  (foutperiode in `usenet_sab_server_events` die tot dat moment liep, met 15 min speling voor de 5-minutenpoll), en
  markeert die als `reconstructed`.
- `GET /api/v1/overview`: een fout in de netto/bruto-berekening geeft `failures.ratio = null` met een waarschuwing in
  het log, nooit een 500 voor het hele Overzicht (net als het `goals`-blok).
- Web: nieuw `components/mso/overview/goals-card.tsx` (eerste element op Overzicht, boven de KPI-rij), types in
  `lib/api/goals.ts`, teksten in `lib/i18n/messages/nl/goals.ts`. Per doel een tegel (bestaande `.kpi`-stijl):
  statuschip (icoon + tekst), waarde nu, doel, start + richting, netto/bruto met n, inline-SVG-verloop met doellijn
  (blijft zichtbaar op 390 px; gereconstrueerde punten lichter), "Wat houdt dit tegen" met link, uitgezette servers
  apart, definitie per doel. Laden/fout/verouderd via `ResourceView`; niet-beschikbaar per tegel. Responsief via
  `repeat(auto-fit, minmax(min(100%, 280px), 1fr))` (3 naast elkaar op 1440, onder elkaar op 390); alleen
  bestaande tokens (licht + donker). "Startdatum wijzigen" alleen voor beheerders. Startwaarde met "voorlopig, het
  eerste uur loopt nog" en (faalratio) "te weinig data"; een proefperiode die nog moet beginnen toont "Start (…): nog
  niet begonnen" en in de voetregel "Begint {datum}"; de voetregel legt uit wat de startwaarde is.

## Gemaakte keuzes (waar het plan iets open liet)
- Doelstatus faalratio = **netto** (wat de eigenaar merkt: kwam de aflevering/film binnen); bruto staat er altijd
  naast, zodat niets wordt verbloemd.
- `failed_at` wordt gewist bij het verlaten van `failed` ("eerste overgang" geldt per mislukkingsperiode).
- Doelen (10 %, 0, 0) en lengte (28 dagen) zijn vast zoals in het plan; alleen de start is instelbaar. Geen aparte
  tijdzone-instelling: `TZ` van de container (staat in alle compose-bestanden op Europe/Amsterdam).
- De failure-trend wordt per dag herberekend (exact en reproduceerbaar); momentopnames zijn nodig voor wat niet uit
  de geschiedenis te halen is (verdachte torrents, servers) en worden daarvoor gebruikt.
- Startwaarde = eerste uur (niet het exacte startmoment): zo telt ook mee wat MSO pas bij de eerste poll na de start
  ziet (torrents elke 2 min, SABnzbd elke 5 min) en wist een actie vlak na de start (Autopilot zet een verlopen
  account binnen minuten uit, een opruiming direct na goedkeuring) het startpunt niet uit. Een verdachte torrent die
  pas in dat eerste uur binnenkwam telt dus als "bij de start"; dat is gedocumenteerd en bewust.
- Geen `first_metrics`-kolom in `goal_snapshots` (de reviewer noemde die als eerste optie): de eerste uurlijkse meting
  van de dag kan ná een Autopilot-actie vallen (zelfde probleem in het klein). De gereconstrueerde stand van het eerste
  uur + bevriezen dekt beide gevallen en blijft ook goed na opruimen van geschiedenis.
- De kaart staat boven de KPI-rij in een eigen grid, zodat de herordening van het grid door WP-G1 ("Wacht op jou"
  eerst) niet botst.
- `usenet/retry.py` (eigendom WP-A) is niet aangepast: dat venster betreft alleen SABnzbd-rijen, die altijd
  `finished_at` hebben, dus er is geen gedragsverschil.

## Na de review (tweede commit)
- **Startwaarde werd overschreven door latere momentopnames (major)**: start komt niet meer uit `goal_snapshots`
  (die rij wordt de hele dag overschreven). Nu: eerste uur vanaf de start (of vanaf het moment dat MSO begon mee te
  kijken), `provisional` tot dat uur voorbij is, daarna bevroren in `system_state['goals.start']` door de uurlijkse
  taak. Test: 5 verdachte torrents, opgeruimd, momentopname later die dag → start blijft 5, nu 0, "beter"; servers die
  Autopilot direct uitzet tellen bij de start nog mee (SABnzbd-mocktest: start 4 → nu 0).
- **Trend mengde drie betekenissen (deel van dezelfde major)**: alle punten zijn nu de stand aan het eind van de dag
  (vandaag: nu); de serverreconstructie telt niet meer "ooit falend die dag" maar "falend aan het eind van de dag".
- **`item_key` voegde gedateerde afleveringen samen (major)**: uitzenddatum als identiteit, jaar alleen als
  filmsleutel (categorie film of niets nummerachtigs na het jaar), anders geen identiteit; jaar blijft in
  serie-sleutels. `media_category` gaat mee uit `outcome_rows`. Tests: dagelijkse show 4 mislukt + 1 andere gelukt =
  4 van 5; Formula 1 per ronde = aparte items.
- **Onbegrensde rijen (major)**: `outcome_rows(db, since, until)`; de startratio leest alleen (start − 37 d, start];
  bevroren startwaarden worden niet meer berekend; `lru_cache` 65 536 titels.
- **Door MSO uitgezet via goedgekeurde wijziging (minor)**: telt nu mee (`via: change`); "buiten MSO uitgeschakeld"
  niet. Test via de SABnzbd-mock in beheermodus (incl. terugdraaien → niet meer "uitgezet door MSO").
- **Richting bij te weinig startdata (minor)**: `start.insufficient` + geen richting onder 20 items.
- **`new_7d` (minor)**: telt ook al opgeruimde verdachte torrents van deze week.
- **`failures.ratio` zonder vangnet (minor)**: try/except, `ratio: null` bij een fout; test met een kapotte
  `ratio_now`.
- **Voetregel bij toekomstige start (minor)**: "Begint {datum}".

## Bewust veranderde tests
Geen bestaande tests (van vóór 1.6.0) gewijzigd; alle zijn ongewijzigd groen. In de eigen nieuwe testfile zijn bij de
review de verwachtingen voor de startwaarde (`source: history`, `provisional`, venster) en `new_7d` aangepast aan het
bewust veranderde gedrag. `apps/core/tests/test_overview_goals.py` (20 tests): item-identiteit (incl. uitzenddatum,
ronde/evenement, jaar in serie, categorie), netto/bruto (incl. dagelijkse show), retry-koppeling, lokale
dagen/zomertijd, `failed_at`-levenscyclus, vastgelopen torrent buiten 24 u/evaluatorreeks, migratie-backfill +
down/up, begrensde `outcome_rows` + Overzicht overleeft een fout in de ratio, goals-API (rechten, lege installatie,
startbronnen, ongeldige/toekomstige datum, trend, blokkades, momentopname-taak, reconstructie, bevriezen, startwaarde
= eerste uur en niet een latere momentopname, serverreconstructie = stand aan het eind van de dag) en via echte
HTTP-mocks: qBittorrent (vastgelopen torrent → `failed_at` = laatste voortgang + 72 u; verdachte torrents +
opruimvoorstel) en SABnzbd (falend vs. door MSO uitgezet in Autopilot, startwaarde blijft na uitzetten, uitschakel-
voorstel als blokkade, uitgezet via goedgekeurde wijziging in beheermodus, "buiten MSO uitgeschakeld", geen
geheimen).

## Tekst voor CHANGELOG (Nederlands)
- **Doelenkaart op Overzicht**: de drie doelen van de proefperiode — faalratio onder 10 %, 0 verdachte torrents,
  0 falende servers — met startwaarde (de stand in het eerste uur van de proefperiode, daarna vast), waarde nu, doel,
  "dag X van 28", verloop per dag en één zin wat het doel tegenhoudt (met link naar de ene actie die nodig is).
  Servers die MSO uitzette omdat ze niet werken (Autopilot of een goedgekeurde wijziging) staan apart.
  Start van de proefperiode instelbaar (standaard: de dag dat Autopilot voor het eerst aanging).
- **Eerlijke faalratio**: per aflevering/film (netto — wat later alsnog binnenkwam telt als gelukt) naast per poging
  (bruto), altijd met het aantal en "te weinig data" onder 20. Afleveringen met een datum (dagelijkse shows, nieuws,
  sport) tellen per uitzenddatum; wat niet betrouwbaar te herkennen is telt per poging.
- **Vastgelopen torrents tellen niet meer eeuwig mee**: een mislukking telt op het moment dat hij mislukte (nieuw
  veld `failed_at`), niet bij elke nieuwe waarneming. Geldt voor Overzicht, de controle na wijzigingen, herstel,
  kosten, de AI-uitleg en de betrouwbaarheid van indexers. Bestaande gegevens worden bij de upgrade aangevuld.
- API: `GET /api/v1/overview/goals` (kijker); `GET /api/v1/overview` krijgt `failures.ratio` en `goals`.
- Migratie 0604 (`download_outcomes.failed_at`, tabel `goal_snapshots`); terugdraaien met `migrate --to 501` blijft
  werken.

## Tekst voor USER_GUIDE (Nederlands)
### Doelenkaart (Overzicht)
Bovenaan Overzicht staat de kaart **Doelen (proefperiode)**. Hij beantwoordt één vraag: haalt MSO jouw drie doelen?
- **Faalratio** (doel: onder 10 %) over de laatste 7 dagen. *Netto* telt per aflevering of film: wat eerst mislukte
  maar later alsnog binnenkwam, telt als gelukt. *Bruto* telt elke downloadpoging. Beide staan er met het aantal
  (n); onder de 20 zegt de kaart eerlijk "te weinig data". Het doel volgt netto.
- **Verdachte torrents** (doel: 0): torrents met een programma of script (bijv. `.exe`) in een media-download. Je ziet
  hoeveel er nieuw zijn in 7 dagen (werkt de preventie?) en hoeveel er in een opruimvoorstel op jouw goedkeuring
  wachten. Verwijderen gebeurt nooit automatisch.
- **Falende servers** (doel: 0): SABnzbd-servers die aanstaan maar niet werken. Servers die MSO zelf uitzette omdat ze
  niet werken tellen niet mee; ze staan apart ("4 uitgezet omdat ze niet werken; herstellen of verwijderen in SABnzbd
  is jouw keuze").

Per doel zie je de startwaarde, de waarde nu, of het beter of slechter gaat, het verloop per dag (de stand aan het
eind van elke dag; de stippellijn is het doel; lichtere punten zijn afgeleid uit de geschiedenis omdat er die dag geen
dagmeting was) en onder **Wat houdt dit tegen** de ene zin met de actie die nodig is.

De **startwaarde** is de stand in het eerste uur van de proefperiode (faalratio: de 7 dagen ervoor). Wat MSO daarna
doet — een server uitzetten, een opruiming na jouw goedkeuring — verandert de startwaarde dus niet; zo zie je eerlijk
hoeveel er sindsdien veranderd is. Tijdens dat eerste uur staat er "voorlopig"; daarna ligt de startwaarde vast.

De proefperiode duurt 28 dagen en begint standaard op de dag dat je Autopilot voor het eerst aanzette (anders op de
installatiedag). Een andere startdatum stel je in onder **Instellingen → automatisering → Start proefperiode
(doelenkaart)** (JJJJ-MM-DD; leeg = automatisch). Dagen zijn lokale dagen (tijdzone van de container, standaard
Europe/Amsterdam).

## Open punten (voor de hoofdagent)
- `usenet/retry.py` (WP-A) gebruikt nog `COALESCE(o.finished_at, o.observed_at)` (regels ~126/129); bij de merge
  omzetten naar `outcomes.outcome_at('o')` (geen gedragsverschil: SABnzbd-rijen hebben altijd `finished_at`). Niet in
  WP-D gedaan omdat het bestand van WP-A is (ook de reviewer noemde dit als integratiepunt).
- WP-E1/WP-B: `features.overview.goals._mso_switched_off` leest toegepaste `change_sets` op `servers/<naam>/enable`
  (`after` 0 of false). Als die op-vorm verandert, deze query meenemen.
- WP-E2 (bewaartermijnen/back-ups): `goal_snapshots` is klein (1 rij per dag) en **niet** opnieuw op te halen — niet
  opruimen binnen 400 dagen en niet uitsluiten van back-ups; idem `system_state['goals.start']` (bevroren
  startwaarden). `torrent_state` (verwijderde rijen) en `usenet_sab_server_events` worden gebruikt voor de
  reconstructie van trenddagen zonder momentopname en van een nog niet bevroren startwaarde; opruimen daarvan raakt
  bevroren startwaarden niet.
- WP-B: als `usenet_server_autoheal` nieuwe statussen krijgt (bijv. voor verlopen accounts), telt de kaart een
  server die MSO uitzette en die nog uit staat al mee als "uitgezet door MSO" (alles behalve `probing`/`recovered`).
- WP-G1 (lokale dagen in "Wat MSO deed") kan `features.overview.goals.local_tz()` hergebruiken.
- `messages/index.ts`: één import + één spread (`goals`); bij de merge naast die van andere pakketten zetten.
- De UI is gecontroleerd met tsc/eslint; visuele controle (Playwright 1440/390, licht/donker, axe) bij de integratie.
- Niet gebouwd (buiten het plan): weekrapport/`goal_snapshots`-weergave onder Automatisering, Wilson-intervallen,
  instelbare doelen.
