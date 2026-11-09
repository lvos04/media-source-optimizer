# WP-A — Usenet: echte oorzaak + alleen zinvol opnieuw proberen (C14, C03-stap 0, C17)

Branch `wp/A`, migratie `0601_v160_usenet_cause.sql`.

## Wat er veranderd is

### 1. Tellerfout (C03 stap 0)
- Nagekeken in de SABnzbd-broncode (master = 5.1.3): `sabnzbd/api.py` `_api_server_stats` vult `articles_success` met
  `BPSMeter.amounts()` → `article_stats_failed` (`bpsmeter.py`, opgehoogd in `nzb/article.py` bij een mislukt
  artikel). Het veld bevat dus het aantal **mislukte** artikelen.
- `history.snapshot_server_stats()` slaat nu `articles_success = max(tried − failed, 0)` op (`history.articles_found`).
- Migratie 0601 corrigeert bestaande rijen in `usenet_server_stats` met dezelfde formule; die is zijn eigen inverse, dus
  `-- migrate:down` zet de 1.5.0-waarden terug (`migrate --to 501` blijft werken; getest).
- Alle gebruikers van de kolom lezen hem als "gevonden" en kloppen daarmee zonder verdere wijziging: `metrics.py`
  (passieve beschikbaarheid, `article_missing_passive = tried − success`), `api.py` (`/sab/servers`, `success_ratio`,
  `low_article_success`), `recommend.py` (`low_success`-inzicht) en de UI ("Artikelen gevonden", beschikbaarheid).
- Mock: `server_stats` geeft nu net als SABnzbd de mislukte aantallen in `articles_success` (intern `articles_failed`).
  Eweka in de fixture: 434.000 geprobeerd / 1.736 mislukt = 99,6 % gevonden.

### 2. Verwerkingslogboek (`usenet/stagelog.py`, nieuw)
- Zet het SABnzbd-`stage_log` van een historieslot om in feiten: ontbrekende/beschadigde artikelen, leeftijd van de
  post, bytes per server, herstel (gelukt / mislukt / niet nodig, blokken tekort), uitpakken (gelukt / mislukt + de
  precieze, geredigeerde regel), afgebroken download, en het probleem: wachtwoord vereist, schijf vol, geen
  schrijfrechten, bestand te groot voor het bestandssysteem, verplaatsen mislukt, uitpakprogramma-fout, beschadigd
  archief, uitvoerbaar bestand (ongewenste extensie). Plus `data_complete` en een `signature` (dezelfde fout over
  releases heen: zonder setnaam, paden en getallen).
- Teksten letterlijk uit de SABnzbd-broncode (`nzb/object.py` set_download_report, `postproc.py`, `newsunpack.py`,
  `api.py` build_history / `database.unpack_history_info`) én de Nederlandse vertaling uit `po/main/nl.po` (SABnzbd
  schrijft het log in de taal die toen actief was).
- Feiten staan in `download_outcomes.meta.stage` met `meta.classifier_version = 2`. Regels: geheimen gemaskeerd
  (`redact`), setnaam weg, absolute paden ingekort tot `…/<laatste deel>`.
- Wat het uitpakken stopte is het probleem: een uitpakfout met oorzaak gaat vóór andere problemen; SABnzbd's
  informatieve "Removed N files with unwanted extensions" (draait ná een mislukt uitpakken) telt alleen als er geen
  uitpakfout was; een uitpakfout zonder oorzaak ("see logfile") laat een specifiek probleem uit een andere fase
  (bijv. wachtwoord bij de RAR-controle) voorgaan. `problem_of` haalt zelf de `[setnaam]` weg, zodat een releasenaam
  met "Encrypted"/"CRC"/"Corrupt" nooit het probleem bepaalt.

### 3. Classificatie (`failures.py`)
- `classify(..., facts=)`: bij complete data (alles binnen of door par2 hersteld) maken "articles were missing" of een
  CRC-fout de download niet meer `incomplete`; het wordt `unpack_failed` (zeker) met bewijs
  "Data was compleet; uitpakken mislukte: <regel> (<probleem>). De servers zijn hier niet de oorzaak."
- `with_server_context(cls, problems, facts)`: koppelt een server alleen nog als *waarschijnlijke oorzaak* als er
  artikelen ontbraken / herstel mislukte / SABnzbd afbrak; zonder feiten alleen bij `incomplete`.
- Een duidelijke omgevingsfout bij complete data wordt nooit een releaseprobleem: schijf vol → `disk_full`; geen
  schrijfrechten, bestand te groot, verplaatsen mislukt → `client_failure` (beide in herstel's "geen
  release-oorzaak", dus geen zinloze nieuwe zoekopdracht). "write error or disk is full? Permission denied" telt als
  rechtenprobleem, niet als volle schijf.
- Nederlandse SABnzbd-meldingen worden herkend (o.a. "Afgebroken, kan niet voltooid worden", "Uitpakken mislukt,
  archief vereist wachtwoord", "Reparatie mislukt, te weinig herstelblokken").

### 4. Systeemmelding (`usenet/unpack.py`, nieuw)
- Zelfde omgevingsfout (schijf vol, geen schrijfrechten, bestand te groot, verplaatsen mislukt, uitpakprogramma-fout —
  bijv. 7-Zip's "Unpacking failed, see logfile") ≥ 3 keer in 7 dagen → één aanbeveling "SABnzbd kan downloads niet
  uitpakken" met de precieze regel, het aantal, of de data compleet was, de waarschijnlijke oorzaak en voorbeelden.
- Risico `toestemming_nodig` zonder change → verschijnt in "Wacht op jou" (bestaand patroon in `waiting()`).
  Ontdubbeld per fout (`usenet.unpack:<signature>`), een afwijzing blijft staan. ≥ 3 in 7 dagen beslist alleen
  wanneer de melding opengaat: eenmaal getoond (open of geaccepteerd) blijft hij staan zolang de fout binnen 3 dagen
  terugkomt, ongeacht het aantal in 7 dagen; na 3 rustige dagen → `resolved` (ook als hij geaccepteerd was) en hij gaat
  weer open als de fout terugkomt. Draait na elke ingest met nieuwe mislukkingen (vóór de artikeltests) en elk uur
  (`usenet.unpack_check`).
- **Uitpakprogramma-fout** ("Unpacking failed, see logfile") noemt zelf geen oorzaak: die telt pas als dezelfde regel
  bij 3 *verschillende* releases terugkwam (dezelfde release 3× bewijst niets). Vanaf dan krijgen de downloads van een
  actieve fout `client_failure` (`disk_full` bij een volle schijf) i.p.v. `unpack_failed`, met
  `meta.classification.repeated` en bewijs "… Dezelfde melding kwam de afgelopen 7 dagen bij N verschillende releases
  voor, dus ligt het aan SABnzbd of de opslag, niet aan de release." Herstel slaat ze daardoor over (geen blacklist,
  geen nieuwe zoekopdracht), Downloads → Usenet zegt hetzelfde, en de melding zegt terecht dat MSO er geen andere
  release voor zoekt. Nieuwe downloads met een actieve fout krijgen dat label al bij de ingest (`map_slot(active_env=)`),
  dus vóór herstel ze ziet. Zonder herhaling toont de UI neutraal "Oorzaak niet bekend; zie het SABnzbd-logbestand"
  (geen "ligt aan SABnzbd").

### 5. Herbeoordeling (`history.reclassify`)
- Eenmalige taak `usenet.reclassify` na de upgrade (schema loopt tot hij één keer slaagde, status in
  `system_state.usenet.reclassify_v2`) en op verzoek: `POST /api/v1/usenet/sab/reclassify` (operator+, begrensd) en
  de knop **Opnieuw beoordelen** op Downloads → Usenet. `GET /api/v1/usenet/sab/reclassify` (viewer) toont het laatste
  resultaat.
- Leest de mislukte jobs uit de SABnzbd-historie én, bij SABnzbd ≥ 4, het archief (`archive=1`; `SabClient.history`
  kreeg de parameter `archive`), ververst eerst de serverstatus, classificeert opnieuw en werkt alleen
  `failure_reason`, `failure_detail` en de classificatiedelen van `meta` bij (andere meta blijft).
- Audit `usenet.reclassified` met aantallen (beoordeeld, gewijzigd, niet meer in SABnzbd, complete data,
  serveroorzaak weg/behouden, oorzaken voor/na). Rijen die niet meer in SABnzbd staan houden hun oude label en zijn
  herkenbaar aan het ontbreken van `classifier_version`.
- Daarna draaien de uitpakmelding (vóór de audit; de aantallen en `reasons_after` zijn ná het herlabelen geteld,
  plus `environment_relabeled`) en `recovery.fallback.reevaluate()` (zodat herstel de nieuwe labels gebruikt).
- Downloads → Usenet toont onder de kop van de historie "Laatst opnieuw beoordeeld … : X van Y kregen een andere
  oorzaak" (ook na de automatische run na de upgrade).

### 6. Opnieuw proberen (`retry.py`, `usenet/coverage.py` nieuw)
- `basis(o)`: alleen bij een serveroorzaak die SABnzbd zelf noemt (`auth_failure`, `account_expired`,
  `quota_exceeded`) of bij ontbrekende artikelen / mislukt herstel (feiten; zonder feiten `incomplete`). Nooit bij
  "uitpakken mislukt" met complete data.
- Alleen als de dekking verbeterde ná het mislukken: `coverage.last_gain_at()` =
  max(`usenet_server_autoheal.recovered_at`, `usenet_server_coverage.gain_at`). `coverage.observe(servers)` (bij elke
  retry-ronde) houdt per server bij wanneer MSO hem voor het eerst zag en voor het eerst ingeschakeld zag; de eerste
  waarneming is de basislijn, een server die MSO zelf uit- en weer aanzette telt alleen via `recovered_at`. Een nieuwe
  dekkingswinst laat ook herstel (`fallback.reevaluate`) opnieuw kijken.
- Bestaande voorwaarden blijven (geen kapotte server meer aan, minstens één werkende, één keer per job, limieten per
  uur/dag, maximale leeftijd, nog mislukt in SABnzbd). Zonder dekkingswinst: status `waiting` met `kind: coverage`
  ("Wacht op betere dekking …").
- `status_for` (gebruikt door herstel) geeft alleen nog `candidate` als opnieuw proberen echt gaat gebeuren
  (oorzaak + dekkingswinst + niet gepauzeerd); een `proposed`-rij telt alleen als `retried` als hij in de open bundel
  zit.
- **Effectiviteitsrem**: ≥ 10 afgeronde pogingen in 14 dagen en < 30 % gelukt → pauze (`system_state.usenet.retry_brake`),
  open bundelvoorstel verworpen, audit `usenet.retry_paused`, een advies "Automatisch opnieuw proberen gepauzeerd".
  Hervat vanzelf bij een nieuwe dekkingswinst of via **Hervatten** (`POST /api/v1/usenet/retries/resume`, operator+;
  audit `usenet.retry_resumed`); pogingen van vóór het hervatten tellen dan niet meer mee.
- De rem (en het getoonde percentage) beoordeelt alleen pogingen die onder de 1.6.0-regels gekozen zijn:
  `usenet_retries`-rijen aangemaakt na het toepassen van migratie 0601 (`schema_migrations.applied_at`). 1.5.0's
  pogingen (vooral uitpakfouten met complete data) laten 1.6.0 dus niet direct na de upgrade pauzeren. Direct na de
  upgrade toont de UI "Sinds de nieuwe regels (…): …" i.p.v. "Laatste 14 dagen".
- Een verworpen bundelvoorstel (rem, of vervangen door een nieuw) laat geen "in voorstel"-rijen achter; `counts.proposed`
  telt alleen rijen in de open bundel. Een geaccepteerd pauze-advies wordt bij hervatten ook `resolved`, zodat een
  latere pauze weer zichtbaar wordt.
- Herstel (`recovery/fallback.py`, status `retry`) zegt nu waarom SABnzbd het opnieuw probeert (`retry.recovery_text`):
  "Artikelen ontbraken; sinds het mislukken is de dekking verbeterd …" of, bij een serveroorzaak, "… nu de dekking
  verbeterd is (server hersteld of nieuwe server)".
- `GET /api/v1/usenet/autopilot` → `retry.efficacy`, `retry.brake`, `retry.coverage`.

### 7. UI
- Usenet-providers → Autopilot (retry-deel): "Laatste 14 dagen: opnieuw geprobeerd N · gelukt X %", pauzemelding met
  **Hervatten**, laatste dekkingswinst en de regel in één zin; wachten op dekking is informatief (geen waarschuwing).
- Downloads → Usenet: per mislukte download de feiten ("Data compleet · uitpakken mislukt: …", "312 artikelen
  ontbraken · herstel mislukt (12 blokken tekort)"), met een hint of het aan SABnzbd/de opslag of aan de release ligt
  (bij een uitpakprogramma-fout zonder herhaling: "Oorzaak niet bekend …"); leeftijd van de post en bytes per server
  als zichtbare kleine regel (ook op touch/toetsenbord, niet alleen als tooltip). Knop **Opnieuw beoordelen**
  (operator+) en de regel met de laatste herbeoordeling.
- Nieuwe teksten in `apps/web/lib/i18n/messages/nl/usenetdiag.ts`, types in `apps/web/lib/api/usenetdiag.ts`.
  Bijgewerkte bestaande sleutels van het retry-deel: `usenet.ap.retry`, `usenet.ap.retryOff`,
  `usenet.ap.checked.nothing`. Ook label/uitleg van de instelling `usenet.retry_failed_downloads` aangepast (oude tekst
  "zodra de kapotte servers uit staan" klopte niet meer).

## Gemaakte keuzes (waar het plan iets openliet)
- Rem en drempels (10 / 14 d / 30 %) zijn constanten in `retry.py`, geen instellingen (minder knoppen, set-and-forget).
- De rem pauzeert ook handmatige voorstellen ("Nu controleren"); hervatten is één knop, schrijft niets naar SABnzbd en
  mag daarom ook in Alleen-lezen.
- De uitpakmelding telt alleen omgevingsfouten. Wachtwoord, beschadigd archief en uitvoerbaar bestand horen bij één
  release; ontbrekende artikelen zijn een dekkingsvraag. Zo noemt MSO nooit "SABnzbd kan niet uitpakken" bij nep-releases.
  Een uitpakprogramma-fout telt per *verschillende* release (titel); een fout waarvan de regel zelf de oorzaak noemt
  (schijf vol, rechten, …) per download.
- "Nieuw ingeschakelde server" = MSO ziet een server voor het eerst ingeschakeld (nieuwe server of een slapend account
  dat de eigenaar aanzet). Uit- en weer aanzetten van een server die MSO al ingeschakeld zag telt niet; herstel van een
  kapotte server loopt via `recovered_at` (WP-B).
- Herbeoordeling werkt alleen bestaande rijen bij (geen nieuwe rijen uit het archief), zodat de faalratio-historie niet
  verschuift.
- `data_complete` komt uitsluitend uit SABnzbd's eigen regels: een Download-rapport zonder "N articles were missing"
  betekent 0 ontbrekend (SABnzbd schrijft de regel alleen als er iets ontbrak).

## Buiten de bezitlijst aangeraakt
- `apps/core/mso/features/usenet/settings.py`: alleen label en uitleg van de bestaande instelling
  `usenet.retry_failed_downloads` (gedrag veranderde; de oude tekst beloofde opnieuw proberen "zodra de kapotte servers
  uit staan"). Geen nieuwe instellingen; WP-B's sectie onderaan is niet geraakt.
- `apps/core/mso/features/recovery/fallback.py` (review): alleen het `retry`-blok in `handle_outcome` gebruikt nu
  `retry.recovery_text(o, rs)` en `detail.server_related = basis == "server"`. Geen ander pakket wijzigt die regels
  (WP-D raakt alleen de import en `process_failed`/`reevaluate`).
- `apps/core/mso/features/recovery/settings.py` (review): alleen de laatste zin van de uitleg van
  `recovery.autopilot_fallback` (zelfde verouderde belofte "zodra de kapotte servers uit staan").

## Bewust veranderde tests
- `tests/test_usenet_sab_server_status.py`
  - `test_unpack_failed_related_to_server_problems` → vervangen door
    `test_unpack_failed_with_complete_data_is_not_blamed_on_servers` (complete data krijgt geen serveroorzaak meer) en
    nieuw `test_missing_articles_related_to_server_problems` (ontbrekende artikelen houden de serveroorzaak per server).
  - `test_low_article_success_is_flagged`: fixture eweka 434.000/1.736 is nu 99,6 % en niet gemarkeerd; een server die
    echt weinig vindt (blok, 0,4 %) wel.
- `tests/test_usenet_retry.py`: de 1.5.0-tests voegen een dekkingswinst toe (`_gain`) voordat ze opnieuw proberen
  verwachten; `test_autopilot_retries_automatically_within_caps_once_per_job` controleert nu dat uitschakelen alleen
  `waiting`/`coverage` geeft.
- `tests/test_usenet_disable_failing.py::test_failed_download_names_state_per_server`: slot "unpack" → "missing"
  (alleen ontbrekende artikelen krijgen nog een serveroorzaak; de naamgeving per server wordt nog steeds getest).
- `tests/test_recovery_fallback.py::test_unmappable_or_client_caused_failures_are_skipped_with_reason`: `auth_failure`
  zonder dekkingswinst is nu `skipped` (met dezelfde uitleg "andere release lost dit niet op") i.p.v. `retry`.

Review-ronde: alleen tests uit dit pakket zelf aangepast/uitgebreid (`test_usenet_cause.py`: de remtests zetten via
de fixture `rules_since` het moment van migratie 0601 terug, zodat hun pogingen van "1 dag geleden" onder de nieuwe
regels vallen; extra asserts voor opgeruimde voorstelrijen, geaccepteerd pauze-advies en de hersteltekst). Nieuwe tests:
`test_unpack_program_error_across_releases_is_not_a_release_problem` (zelfde release 3× → niets; 3 releases → melding,
herlabel, UI, herstel slaat over, nieuwe download direct gelabeld), `test_unpack_advisory_stays_while_the_error_continues_and_accepted_comes_back`,
`test_brake_judges_only_retries_chosen_under_the_new_rules`, en in `test_usenet_stagelog.py`
`test_unpack_failure_wins_over_removed_unwanted_files` en `test_set_names_never_name_the_problem`.

Nieuwe tests: `tests/test_usenet_stagelog.py` (parser op echte SABnzbd-teksten EN/NL, classificatie met feiten,
tellerformule, mock zoals SABnzbd, migratie 0601 heen/terug, feiten in de history-API over HTTP) en
`tests/test_usenet_cause.py` (herbeoordeling incl. archief en eenmalige taak, uitpakmelding incl. ontdubbelen /
vanzelf sluiten / heropenen / afwijzing, dekking, alleen zinvol opnieuw proberen, `status_for`, rem pauzeren /
hervatten met knop / hervatten bij dekkingswinst).

## Tekst voor CHANGELOG (Nederlands)
### Nieuw / gewijzigd (Usenet: echte oorzaak)
- **Eerlijke artikeltelling**: SABnzbd's veld `articles_success` bevat het aantal *mislukte* artikelen. MSO rekende het
  als gevonden ("Eweka 0,4 %"); dat is nu gecorrigeerd, ook voor de opgeslagen cijfers (Eweka: 99,6 %).
- **Oorzaak uit het verwerkingslog van SABnzbd**: per mislukte download leest MSO wat SABnzbd zelf vastlegde
  (ontbrekende artikelen, herstel, de precieze uitpakfout, wachtwoord, schijf vol, rechten …), ook in het Nederlands.
  Downloads → Usenet toont dat per download ("Data compleet · uitpakken mislukt: …" of "312 artikelen ontbraken").
- **Geen valse serveroorzaak meer**: een server die niet kan inloggen levert geen data. Bij complete data noemt MSO
  daarom geen kapotte server meer als oorzaak van "Uitpakken mislukt".
- **"SABnzbd kan downloads niet uitpakken"**: herhaalt dezelfde omgevingsfout zich (3× in 7 dagen; bij een fout van
  het uitpakprogramma zelf: bij 3 verschillende releases), dan staat er één melding in "Wacht op jou" met de precieze
  regel en voorbeelden; hij verdwijnt vanzelf na 3 rustige dagen. Zulke downloads gelden dan niet meer als
  releaseprobleem: MSO zoekt er geen andere release voor.
- **Herbeoordeling** van de SABnzbd-historie (ook het archief) direct na de upgrade en met de knop *Opnieuw
  beoordelen*; vastgelegd in de audit.
- **Opnieuw proberen alleen als het kan helpen**: alleen bij ontbrekende artikelen (of een serverfout die SABnzbd zelf
  noemt) én pas nadat de dekking verbeterde (server hersteld of nieuwe server). Servers uitzetten is geen reden meer.
  Lukt minder dan 30 % van minstens 10 pogingen in 14 dagen, dan pauzeert het zichzelf (met uitleg en knop
  *Hervatten*); het Autopilot-blok toont "opnieuw geprobeerd N · gelukt X %".
- Migratie 0601 (terugdraaien met `migrate --to 600` of `--to 501` zet de oude tellerwaarden terug).

## Tekst voor USER_GUIDE (Nederlands)
In de Autopilot-tabel (§4) de regel voor SABnzbd vervangen door:

| SABnzbd: mislukte downloads opnieuw proberen | alleen bij ontbrekende artikelen (of een serverfout die SABnzbd noemt) én nadat de dekking verbeterde: een uitgezette server werkt weer of er kwam een server bij (max 20/u, 100/dag; pauzeert zichzelf als < 30 % lukt) |

Nieuwe alinea (bijv. onder §6 *Providers en beschikbaarheid* of een nieuwe paragraaf *Waarom mislukt een download?*):

> **Waarom mislukt een download?** MSO leest per mislukte download het verwerkingslog van SABnzbd. Onder Downloads →
> Usenet staat per download wat er gebeurde: *Data compleet · uitpakken mislukt: …* (de servers leverden alles; het
> ligt aan de release of aan SABnzbd/de opslag) of *312 artikelen ontbraken* (een dekkingsvraag: een andere server of
> backbone kan helpen). Een kapotte server wordt alleen nog als waarschijnlijke oorzaak genoemd als er echt artikelen
> ontbraken. Mislukt het uitpakken steeds op dezelfde manier (bijv. schijf vol of geen schrijfrechten), dan krijg je
> één melding **SABnzbd kan downloads niet uitpakken** in "Wacht op jou"; MSO verandert daar zelf niets aan. Een fout
> van het uitpakprogramma zelf ("see logfile") noemt geen oorzaak; die telt pas als hij bij drie verschillende
> releases voorkomt, en MSO zoekt voor die downloads dan ook geen andere release (die zou net zo mislukken). Met
> **Opnieuw beoordelen** (Downloads → Usenet) beoordeelt MSO alle mislukte downloads opnieuw.
>
> **Opnieuw proberen** helpt alleen als SABnzbd nu meer artikelen kan bereiken dan toen de download mislukte. MSO
> probeert daarom alleen downloads met ontbrekende artikelen opnieuw, en pas nadat een server hersteld is of er een
> nieuwe bij kwam. Op Usenet-providers → Autopilot zie je hoeveel pogingen lukten; lukt minder dan 30 % van minstens 10
> pogingen in 14 dagen, dan pauzeert MSO dit zelf. Het hervat vanzelf bij betere dekking, of met **Hervatten**.

De kolom "Artikelen gevonden" (Serverprioriteit) en de passieve beschikbaarheid tonen nu het echte percentage
gevonden artikelen (eerder stond daar het percentage *mislukte* artikelen).

## Review-ronde (verwerkt)
- **Major — uitpakprogramma-fout tegenstrijdig**: opgelost zoals hierboven (§2/§4): `environment` alleen voor
  oorzaken die de regel zelf noemt of na herhaling over ≥ 3 releases; dan herlabelen naar `client_failure`, zodat
  herstel niet blacklist/opnieuw zoekt en melding, UI en herstel hetzelfde zeggen. Nieuwe sleutel
  `usenetdiag.facts.unknown` (neutraal) en `usenetdiag.facts.environmentRepeated`.
- "Removed N files with unwanted extensions" na een mislukte uitpak → was `executable`; nu wint de uitpakfout.
- `[setnaam]` in `problem_of` → nu altijd weggehaald (ook voor de uitpakregel).
- Melding sloot terwijl de fout nog terugkwam → `MIN_COUNT` alleen voor openen; open/geaccepteerd blijft tot 3 rustige dagen.
- Geaccepteerde meldingen (uitpakken, pauze-advies) kwamen nooit terug → `open` én `accepted` worden `resolved`.
- Rem beoordeelde 1.5.0-pogingen → alleen rijen aangemaakt na migratie 0601. Gekozen boven een basislijn in
  `system_state`: deterministisch, geen eerste-aanroep-effect, en een 1.5.0-voorstel dat pas na de upgrade wordt
  goedgekeurd telt ook niet mee.
- Verworpen bundel liet "proposed"-rijen achter → opgeruimd + `counts.proposed` alleen voor de open bundel.
- Herstel-tekst bij `retry` → `retry.recovery_text` (zie "Buiten de bezitlijst").
- `GET /usenet/sab/reclassify` ongebruikt → getoond op Downloads → Usenet; `usenetdiag.retry.checked.paused` wordt nu
  gebruikt als "Nu controleren" pauze zonder reden teruggeeft.
- Leeftijd/servers alleen in tooltip → zichtbare kleine regel.

## Open punten
- WP-B moet `usenet_server_autoheal.recovered_at` zetten wanneer een uitgezette server echt weer werkt; tot de merge
  telt alleen een nieuw ingeschakelde server als dekkingswinst.
- C03 voorbij stap 0 ("levert niet"-status) is bewust niet gebouwd: met de gecorrigeerde teller vervalt de premisse.
- WP-D kan voor "wat het doel tegenhoudt" `download_outcomes.meta.stage.problem` gebruiken (bijv. "meeste
  mislukkingen: uitpakken mislukt (data compleet)").
- Herstel kijkt na de herbeoordeling alleen naar mislukkingen van de laatste 72 uur opnieuw (`fallback.MAX_AGE_H`).
- SABnzbd slaat stage-regels op gescheiden door ";" (`database.build_history_info`); een regel die zelf een ";" bevat
  (bijv. een lijst SFV-bestanden) komt in delen binnen. De parser is daar tolerant voor; het precieze citaat kan dan
  korter zijn.
- Gedeelde sleutel `usenet.ap.on` noemt "laat SABnzbd mislukte downloads opnieuw proberen" (klopt nog), niet aangepast.
