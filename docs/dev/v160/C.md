# WP-C — Categorie '*' ophelderen (C34, ingeperkt)

Branch `wp/C`. Geen migratie nodig (0603 niet gebruikt): de diagnose bewaart alleen de laatste SABnzbd- en
MediaGuard-lezing in `system_state` (`usenet.categories.remote`, alleen namen en categorieën), het voorstel staat in
`change_sets` en de "Wacht op jou"-melding in `recommendations`.

## Wat er veranderd is

### MediaGuard: alleen-lezen routes (`connectors/mediaguard.py`, `features/mediaguard/compat.py`)
- Nieuwe leesmethoden op `MediaGuardClient` (alle `GET`, rol viewer):
  - `instances()` → `GET /api/v1/instances`: id, app, media_type, naam, gearchiveerd/gepauzeerd,
    `download_category` en de gecachte gezondheidsmeldingen (geredigeerd). URL, sleutelvlaggen en `last_error`
    worden niet overgenomen.
  - `download_clients(id)` → `GET /api/v1/instances/{id}/download-clients`. **Let op:** het echte pad heeft een
    koppelteken (`manage.py`: `PROVIDERS = {"download-clients": "downloadclient"}`), niet `downloadclients` zoals in
    het plan. MSO houdt alleen naam, implementatie, protocol, aan/uit, prioriteit en `tvCategory`/`movieCategory`
    over, plus `mediaguard_owned` (naam begint met `MediaGuard · `, `templates.PREFIX`). Host, poort, gebruikersnaam en
    (gemaskeerde) sleutels verdwijnen al in de connector.
  - `instance_health(id)` → `GET /api/v1/instances/{id}/health`: source, type, message (geredigeerd).
  - `provisioning_categories()` → `GET /api/v1/provisioning/config`, teruggebracht tot downloadclient-naam,
    implementatie, aan/uit, doelinstanties en categorie per instantie; per instantie de inrichtingsmodus en of de
    downloadclient-sectie uitstaat; MediaGuards standaardcategorie per instantie (`defaults[].category`).
    `host`/`username`/`*_set` worden weggelaten (regel 4).
  - Instantie-id's worden gevalideerd (`[A-Za-z0-9_.-]{1,64}`) voordat ze in een pad komen.
- Compatibiliteitsmatrix: vier nieuwe rijen (`instances_list`, `instance_download_clients`, `instance_health`,
  `provisioning_config`), niet verplicht; `never_used` aangevuld (downloadclients wijzigen/testen, inrichting
  wijzigen). `ALLOWED_WRITES` is ongewijzigd.
- Gracieus: 403 → "geen toegang", 404/ontbreekt in OpenAPI → "route ontbreekt"; de koppeling blijft `connected`.
  Zonder de downloadclient-route gebruikt MSO MediaGuards eigen categorie per instantie (`download_category` of de
  standaard per mediatype) en noemt de diagnose dat "waarschijnlijk". Health: eerst de live route, anders de laatst
  bekende meldingen uit de instantielijst.

### SABnzbd (`connectors/sabnzbd.py`)
- Na `categories()`: `category_config()` (`get_config&section=categories`), `complete_dir()`
  (`get_config&section=misc&keyword=complete_dir`, alleen die ene waarde), `create_category()` (`set_config
  &section=categories&keyword=<naam>&dir=<naam>`) en `delete_category()` (`del_config&section=categories`).
  Nagekeken in de SABnzbd-broncode (master = 5.1.3): `api.handle_cat_api` (naam in kleine letters),
  `api.del_from_section`, `api._api_get_cats`, `config.ConfigCat`/`get_ordered_categories`/`get_dconfig`,
  `misc.cat_to_opts` ("Ignore the input category if we don't know it" → '*').
- Nieuwe applier `sabnzbd_categories` direct na de `RETRY_TARGET`-registratie, pad `categories/<naam>`, waarden
  `ontbreekt` ↔ `aanwezig · map <naam>`. Schrijven = `set_config`, controle = `get_config` teruglezen. Terugdraaien
  (`ontbreekt`) verwijdert de categorie alleen als hij nog precies is zoals MSO hem maakte én geen download in de
  SABnzbd-wachtrij of in nabewerking (historie met een niet-eindstatus, zoals Extracting/Moving/Running) hem
  gebruikt; anders een duidelijke fout ("buiten MSO gewijzigd" / "in gebruik door download(s) in SABnzbd (N in de
  wachtrij, M in nabewerking)"). Zonder categorie zou SABnzbd zo'n job in de '*'-map zetten
  (`postproc.get_complete_directory`).
- **Nooit automatisch teruggedraaid**: eigen evaluator `_category_evaluation` voor `sabnzbd_categories`, direct na
  de applier geregistreerd (`not_applicable`, `auto_rollback: False`). Zonder eigen evaluator nam de
  automatisering de generieke (alle downloads); bij "slechter" in Autopilot of met `automation.auto_rollback` zou
  die de goedgekeurde categorie via `del_config` verwijderen (harde regel 1). Terugdraaien kan alleen met de hand.
  Beleid: alleen gewone namen (`[a-z0-9][a-z0-9 ._+-]{0,63}`), nooit `*`, geen andere waarden.

### Diagnose (`features/usenet/categories.py`, nieuw)
- (a) Sonarr/Radarr sturen categorie X die SABnzbd niet kent → SABnzbd zet de download in '*'.
- (b) Er wordt geen categorie meegestuurd → oplossen in MediaGuard (inrichting) als MediaGuard die downloadclient
  maakte/beheert, anders in Sonarr/Radarr zelf.
- (c) Categorieën kloppen maar toch '*' → andere bron; MSO's eigen verzendingen worden apart geteld
  (`usenet_nzbs` ↔ `download_outcomes`).
- Plus: relevante Sonarr/Radarr-meldingen (de bekende downloadclient-/import-/remote-path-checks, of tekst over
  categorie/downloadclient/SABnzbd/remote path; nooit `ImportList*`/`Indexer*`) met bron "melding van
  Sonarr via MediaGuard", '*'-downloads per lokale dag (14 dagen, Europe/Amsterdam) en nu in de wachtrij, en welke
  categorie MSO zelf gebruikt maar SABnzbd niet kent.
- Alleen downloadclients die MSO echt uitlas tellen als "MSO weet wat Sonarr/Radarr sturen"; MediaGuards eigen
  categorie ("waarschijnlijk", bij een ontbrekende of verboden downloadclient-route) is een aanname en geeft dus
  nooit `ok` of `c`, wel `geen_ster`/`onbekend` (of `a` met "waarschijnlijk").
- `c` met alleen '*' in de live wachtrij (de laatste 7 dagen niets afgerond in '*') krijgt een eigen tekst
  (`usenetcat.verdict.cQueue`) in plaats van "0 downloads in '*'".
- Uitkomsten: `a`, `b`, `c`, `ok`, `geen_ster` (geen '*' maar *arr-categorieën onbekend: nooit een valse "alles
  klopt") en `onbekend` (SABnzbd onbereikbaar of '*' zonder MediaGuard-informatie).
- API (`features/usenet/api.py`, na `sab_category_map`): `GET /api/v1/usenet/categories/diagnosis[?refresh=true]`
  (viewer; verversen max 6/min) en `POST /api/v1/usenet/categories/propose` (beheerder, optioneel `names`).
- Voorstel bij (a): één wijziging "SABnzbd-categorie X aanmaken", risico `toestemming_nodig`, nooit automatisch (ook
  niet in Autopilot). Voorbeeld en uitleg noemen de nieuwe map (`<complete_dir>/<naam>`), dat nabewerking '*' volgt,
  dat bestaande '*'-downloads blijven staan, en dat MediaGuard downloads in die categorie daarna volgens zijn eigen
  regels opruimt (niet terug te draaien door MSO). Impact met Nederlandse sleutels zodat het wijzigingsvoorbeeld ze
  leesbaar toont.
- Elke ontbrekende categorie heeft een vlag `creatable`. De knop "Voorstel maken" stuurt alleen die namen; voor
  een naam die MSO niet in SABnzbd zet (bijv. met `/` of niet-ASCII) toont de pagina "maak hem zelf aan in
  SABnzbd" en geeft de API een eigen 422 ("ontbreekt wel, maar ... maak hem zelf aan") in plaats van "ontbreekt
  niet volgens de diagnose". Zijn er alleen zulke namen, dan is er geen knop maar die aanwijzing.
- Periodieke taak `usenet.categories_check` (elke 6 u, alleen als SABnzbd én MediaGuard gekoppeld zijn): houdt
  precies één open voorstel voor de *zekere* ontbrekende categorieën (verschijnt in "Wacht op jou"), vervangt het als
  de set verandert, verwerpt het als het niet meer nodig is en sluit de bijbehorende aanbeveling (die weer opengaat
  als hetzelfde probleem later terugkomt). Namen met tekens die MSO niet in SABnzbd zet, krijgen geen voorstel.
  Er wordt nooit iets toegepast; zonder antwoord van SABnzbd/MediaGuard verandert er niets.
  **De eigenaar beslist:** categorieën waarvan iemand anders dan MSO (`systeem`/`automatisering`) het voorstel
  verwierp of terugdraaide (audit `change.discarded`/`change.rolled_back`), zet de taak niet opnieuw klaar
  (`system_state` `usenet.categories.declined`, met een audit-id-watermerk; de diagnose geeft ze als `declined` en
  de pagina zegt het erbij, de knop werkt nog wel). Zo'n besluit vervalt pas als het probleem weg is (SABnzbd kent
  de categorie, of alle downloadclients zijn uitgelezen en niemand stuurt hem nog), zodat hetzelfde probleem later
  weer een voorstel geeft. Een voorstel dat een beheerder zelf maakte (bijv. een deel van de namen) of dat al is
  goedgekeurd, vervangt de taak nooit; alleen MSO's eigen voorstel wordt vervangen. Verworpen als het zinloos is
  geworden: MSO's eigen voorstel als geen van zijn categorieën nog ontbreekt, dat van een beheerder alleen als
  SABnzbd alle categorieën al kent (toepassen zou dan toch op een afwijking stuiten). Label toegevoegd in
  `SCHEDULE_LABELS` (`features/automation/api.py`, één regel in het Usenet-blok).

### Mocks
- SABnzbd: categorieën als echte config (`get_cats`, `get_config/set_config/del_config&section=categories`,
  `get_config&section=misc&keyword=complete_dir`), namen in kleine letters, '*' met de standaardwaarden van
  SABnzbd, een job met een onbekende categorie komt in '*' (`misc.cat_to_opts`). Nieuwe faults `categories=` en
  `config_locked=1`; `ignore_set_config=1` maakt ook `del_config` een no-op.
- MediaGuard: `GET /instances` in de vorm van `instance_view`, `GET /instances/{id}/download-clients` (gemaskeerd,
  zoals `mask_provider`), `GET /instances/{id}/health`, `GET /provisioning/config` (vorm van `store.public_view`, met
  de host/gebruikersnaam die MSO moet weglaten). Faults `no_category_routes=1` en `category_routes_forbidden=1`.

### Web
- `components/mso/usenet/categories.tsx`: bovenaan de kaart "SABnzbd-categorieën" het blok "Waarom staan downloads
  in '*'?": oordeel met de plek waar je het oplost, het voorstel (knop voor beheerders, daarna het bestaande
  wijzigingsvoorbeeld met goedkeuren/toepassen/terugdraaien), MSO's eigen verzendingen, staafjes '*' per dag
  (met tekstsamenvatting voor schermlezers) en per Sonarr/Radarr-instantie wat hij stuurt, of SABnzbd het kent, wie
  het beheert en de meldingen van Sonarr/Radarr. Laden/fout/leeg via `ResourceView`; `table--stack` voor 390 px.
- Downloads → Usenet: de '*'-uitleg toont nu de vastgestelde oorzaak en linkt naar de oplossing
  (`StarCategoryNote`). De teksten `usenet.dl.defaultCatBody`/`defaultCatFix` zeggen niet meer "pas Sonarr aan".
- Nieuwe teksten in `lib/i18n/messages/nl/usenetcat.ts`; types in `lib/api/usenetcat.ts`.

## Keuzes waar het plan ruimte liet
1. **Map = categorienaam** (`dir=<naam>`): MediaGuards `SabClient.view` zoekt zijn categorieën in
   `complete_dir/<dir of naam>`; een lege map zou MediaGuard niet begrijpen (feasibility-blokker).
2. **Nabewerking/script/prioriteit op "Default"** in plaats van kopiëren van '*': SABnzbd lost "Default" op naar de
   waarden van '*' (`misc.cat_to_opts`), dus nu identiek en het blijft gelijk als de eigenaar '*' later aanpast.
3. **Alleen een voorstel, nooit automatisch** (harde regel 1: MediaGuard gaat daarna opruimen). De periodieke taak
   zet het voorstel alleen klaar bij zekere gevallen (downloadclients uitgelezen), zodat het in "Wacht op jou"
   staat zonder dat de eigenaar de Usenet-pagina hoeft te openen; "waarschijnlijke" gevallen (oudere MediaGuard)
   alleen op verzoek.
4. **Geen voorstel voor MSO's eigen `mso-*`-categorieën**, alleen advies: met zo'n categorie zou MediaGuard MSO's
   bewust niet-geïmporteerde verzendingen na 7 dagen naar zijn prullenbak verplaatsen.
5. **Terugdraaien beschermd en alleen met de hand**: alleen een categorie die MSO maakte (blijkt uit de wijziging),
   ongewijzigd, en zonder download in de wachtrij of in nabewerking; nooit door de automatisering (eigen
   evaluator). Werkt samen met de driftcontrole per op van WP-E1 (die leest dezelfde waarde).
6. **Lokale dagen** met een constante `Europe/Amsterdam` (`categories.LOCAL_TZ`); bij integratie gelijktrekken met
   de tijdzone-instelling van WP-D als die er komt.
7. **Mock realistisch**: een onbekende categorie komt in '*', zoals echte SABnzbd. Daardoor moest één bestaande test
   de categorie `mso-tv` in de mock aanmaken (zie hieronder).

## Bewust veranderde tests
- `tests/test_recovery_fallback.py` (fixture `sab`): de SABnzbd-mock krijgt `categories=…,mso-tv`. De mock zet een
  job met een onbekende categorie nu in '*' (echt SABnzbd-gedrag); de test controleert terecht dat de verzending in
  de aparte MSO-categorie staat, en die moet dan in SABnzbd bestaan.

Nieuwe tests: `tests/test_usenet_categories.py` (19 tests; 18 via echte HTTP tegen beide mocks, 1 unit-test van de regels): leesroutes
zonder geheimen, compat-rijen en `ALLOWED_WRITES` ongewijzigd, SABnzbd-categorieën lezen/schrijven en '*' bij
onbekende categorie, gevallen a/b/c/onbekend/geen_ster, oudere MediaGuard (404) en geen toegang (403), lokale dagen,
volledig voorstel (rollen, idempotent, "Wacht op jou", nooit automatisch, alleen-lezen, toepassen + controle bij de
bron, terugdraaien geweigerd bij gebruik in de wachtrij of in nabewerking en bij wijziging buiten MSO), nooit
automatisch teruggedraaid (Autopilot + "slechter" via `engine.evaluate_due`), de beslissing van de eigenaar
(verworpen/teruggedraaid voorstel komt niet terug, eigen voorstel van de beheerder wordt niet vervangen, besluit
vervalt als het probleem weg is), niet aan te maken namen (eigen 422), drift, vergrendelde
SABnzbd-config (mislukt + automatisch hersteld), beleid van de applier en de periodieke taak.

## Tekst voor CHANGELOG (Nederlands)
- **Categorie '*' opgehelderd.** MSO legt uit waarom SABnzbd downloads in zijn standaardcategorie '*' zet en waar je
  het oplost. Via de alleen-lezen MediaGuard-routes (instanties, downloadclients, gezondheidsmeldingen, inrichting;
  rol viewer volstaat) vergelijkt MSO wat Sonarr/Radarr meesturen met de categorieën van SABnzbd: (a) SABnzbd kent
  de categorie niet, (b) er wordt geen categorie meegestuurd (oplossen in MediaGuard of in Sonarr/Radarr), (c) de
  categorieën kloppen en de downloads komen van een andere bron. Met de meldingen van Sonarr/Radarr zelf en het
  aantal '*'-downloads per dag.
- Bij (a) zet MSO één voorstel klaar in "Wacht op jou": de ontbrekende SABnzbd-categorie aanmaken met een eigen map.
  Altijd met jouw goedkeuring, ook in Autopilot, omdat MediaGuard downloads in die categorie daarna volgens zijn
  eigen regels opruimt. Verwerp je het voorstel, dan zet MSO het niet opnieuw klaar. Gecontroleerd door SABnzbd
  terug te lezen; terugdraaien (alleen met de hand) verwijdert alleen een categorie die
  MSO zelf maakte en die niet in gebruik is.
- De oude tip "pas het veld Category in Sonarr aan" is weg; die botste met MediaGuards inrichting.
- Compatibiliteitsmatrix: vier nieuwe, optionele MediaGuard-leesroutes. MSO schrijft nog steeds niets nieuws naar
  MediaGuard.

## Tekst voor USER_GUIDE (Nederlands)
### Waarom staan downloads in '*'?
SABnzbd zet een download in zijn standaardcategorie '*' als hij geen categorie meekrijgt of de meegestuurde
categorie niet kent. Sonarr/Radarr zien zulke downloads dan niet terug: geen automatische import en geen afhandeling
van mislukte downloads (blokkeren en opnieuw zoeken). MediaGuard levert en ruimt ze ook niet op.

Onder **Usenet-providers → SABnzbd-categorieën** zie je bovenaan wat er aan de hand is:
- **SABnzbd kent een categorie niet** die Sonarr/Radarr meesturen (vaak `tv-anime` of `movies-anime`). Met
  **Voorstel maken** (beheerder) zet MSO een wijziging klaar die de categorie in SABnzbd aanmaakt, met een eigen map
  met dezelfde naam. Je ziet vooraf de nieuwe map en wat er gebeurt; het wordt pas toegepast na goedkeuring en
  nooit automatisch. Daarna ruimt MediaGuard downloads in die categorie op volgens zijn eigen regels; dat deel kan
  MSO niet terugdraaien. MSO zet dit voorstel ook zelf klaar in **Wacht op jou** als het zeker is; verwerp je het (of draai je het terug), dan zet MSO het niet opnieuw klaar, tenzij het probleem eerst opgelost was en later terugkomt. Terugdraaien gebeurt nooit automatisch. Liever niet?
  Kies dan in MediaGuard (Inrichting → Downloadclients) een categorie die SABnzbd al kent.
- **Er wordt geen categorie meegestuurd.** Beheert MediaGuard die downloadclient, los het dan op in MediaGuard
  (Inrichting → Downloadclients, categorie per instantie) en pas de inrichting toe; anders in Sonarr/Radarr
  (Instellingen → Download Clients → SABnzbd → Category). Een wijziging in Sonarr zelf ziet MediaGuard als
  afwijking.
- **De categorieën kloppen** maar er komen toch downloads in '*': die komen van een andere bron (handmatig, RSS in
  SABnzbd, een andere app). MSO's eigen verzendingen met `mso-tv` enz. komen ook in '*' zolang SABnzbd die
  categorie niet kent; dat is onschuldig (ze worden bewust niet automatisch geïmporteerd).

Daaronder: het aantal '*'-downloads per dag (zo zie je of een oplossing werkt) en per Sonarr/Radarr-instantie welke
categorie hij stuurt, of SABnzbd die kent, wie de downloadclient beheert en de meldingen van Sonarr/Radarr zelf. Voor
dit alles leest MSO MediaGuard alleen (rol viewer volstaat); zonder MediaGuard zie je alleen de tellingen en het
advies om het in Sonarr/Radarr na te kijken.

## Reviewronde (onafhankelijke review van 80dd235)
Alle bevindingen verwerkt:
- **Blokker** — automatisch terugdraaien (en dus verwijderen) van een toegepaste categorie door de automatisering:
  eigen evaluator die nooit automatisch terugdraait (zie SABnzbd hierboven). Test bevestigt dat de test zonder de
  fix faalt (`rolled_back == 1`).
- **Major** — de periodieke taak negeerde "Verwerpen" en verving een zelfgemaakt (deel)voorstel: zie "De eigenaar
  beslist". Eén bewuste keuze die iets verder gaat dan de review ("alleen MSO's eigen voorstel verwerpen"): een
  voorstel van een beheerder wordt wél verworpen zodra SABnzbd al zijn categorieën kent, omdat toepassen dan
  toch op een afwijking stuit en het anders eeuwig in "Wacht op jou" blijft staan. Het wordt nooit vervangen.
- Minor — terugdraaien kijkt ook naar nabewerking (historie, niet-eindstatus); `knows_arr` telt alleen uitgelezen
  downloadclients; de knop stuurt alleen aan te maken namen (+ eigen 422 en aanwijzing in de UI); eigen tekst voor
  '*' alleen in de wachtrij; meldingenfilter zonder het losse woord "import" en zonder `ImportList*`/`Indexer*`.
- In het eigen testbestand uitgebreid (geen bestaand gedrag van andere pakketten veranderd): `test_case_a…`
  (ImportList-melding wordt niet getoond), `test_proposal_…_guarded_rollback` (nabewerking), `test_without_…`
  (alleen aangenomen categorieën → `onbekend`/`geen_ster`), `test_evaluate_rules_unit` (wachtrij-`c`, `creatable`,
  meldingenfilter) en drie nieuwe tests.

## Open punten
- Het gedrag "Sonarr/Radarr tonen alleen SABnzbd-jobs in hun eigen categorie (of '*' als hun categorie leeg is)"
  komt uit de Sonarr/Radarr-broncode (`Sabnzbd.cs` `GetItems`) zoals bekend, niet in deze sessie nagelezen (geen
  Sonarr-bron beschikbaar). SABnzbd- en MediaGuard-gedrag zijn wel in de echte bron nagekeken.
- Categorievergelijking is hoofdletterongevoelig (SABnzbd slaat namen in kleine letters op). Stuurt een *arr `TV`,
  dan matcht dat met `tv`; of de *arr daarna zijn eigen jobs terugvindt hangt af van zijn eigen vergelijking.
- Mogelijke (triviale) mergeconflicten: importlijst in `features/usenet/api.py` (WP-A), imports in
  `downloads/usenet-tab.tsx` (WP-A), `SCHEDULE_LABELS` in `features/automation/api.py` (E1/E2/G1), de dispatch
  bovenaan `with state.lock:` en `get_cats` in de SABnzbd-mock (A/B), en de buurt van `categories()` in
  `connectors/sabnzbd.py` (WP-B voegt methoden toe na `warnings()`).
- Tests van andere pakketten die via de SABnzbd-mock versturen met een categorie die de mock niet kent en daarna
  `queue[...]["cat"]` controleren, zien nu '*' (echt gedrag); maak in zo'n test de categorie aan
  (`create_app(categories=…)` of `state.add_category(...)`).
- Lokale dag: zie keuze 6 (constante Europe/Amsterdam).
- UI niet in een browser bekeken (geen Playwright in dit pakket): graag meenemen in de 1440/390 licht/donker-ronde.
