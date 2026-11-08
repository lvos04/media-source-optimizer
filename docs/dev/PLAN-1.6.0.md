# MSO 1.6.0 — bouwplan (werkpakketten)

Doel: MSO pakt de **juiste oorzaak** aan en laat de eigenaar eerlijk zien of zijn drie doelen gehaald worden
(faalratio < 10 %, 0 verdachte torrents, 0 falende servers, binnen 3–4 weken), plus de betrouwbaarheid die nodig is
om MSO weken onbeheerd te laten draaien. Achtergrond en bewijs per idee (bestand:regel, rapportcitaten, drie
onafhankelijke toetsen): `/tmp/claude-0/-home-user/ff67ae26-da4f-541c-8855-f71c760f58d4/scratchpad/v160/candidates.json`
(ids C01…C37) en `ranking.json` daarnaast. De echte testrapporten staan in dezelfde scratchpad onder `report*/`.

Nagekeken feit (SABnzbd 5.1.3-broncode, `sabnzbd/api.py:988-996` + `bpsmeter.py:401-411`): het veld
`articles_success` in `mode=server_stats` bevat het aantal **mislukte** artikelen (`article_stats_failed`).
Eweka "0,4 % gevonden" is dus in werkelijkheid 99,6 %. SABnzbd-broncode is op te halen via
`https://raw.githubusercontent.com/sabnzbd/sabnzbd/master/sabnzbd/<bestand>.py` (master = 5.1.3).
MediaGuard-broncode (alleen lezen, nooit wijzigen): `/tmp/claude-0/-home-user/ff67ae26-da4f-541c-8855-f71c760f58d4/scratchpad/mg-latest`.

## Harde regels (gelden voor elk pakket)

1. Alles wat iets **verwijdert** is een wijzigingsvoorstel met risico `toestemming_nodig` en wordt nooit
   automatisch uitgevoerd. Al het andere (tuning, servers uit/aan, vervangers, synchronisaties) mag automatisch in
   Autopilot. Iets dat MediaGuard indirect laat opruimen (bijv. een SABnzbd-categorie aanmaken) = `toestemming_nodig`.
2. In Alleen-lezen wordt niets toegepast.
3. Elk voorstel toont vooraf exact wat er gebeurt en wordt na toepassen bij de bron gecontroleerd; terugdraaien waar
   het kan, en het voorbeeld zegt expliciet wanneer het niet kan.
4. Geheimen nooit in logs, audit, API-antwoorden of UI (ook geen SABnzbd-gebruikersnamen/wachtwoorden).
5. Bestaand gedrag, rollen (kijker/operator/beheerder) en tests blijven intact. Wijzig een bestaande test alleen als
   het gedrag bewust verandert, en zeg dat in je notities.
6. Alle UI-tekst Nederlands, huisstijl (bestaande componenten in `apps/web/components/mg/*`), licht + donker,
   bruikbaar op 1440 en 390 px; elke staat (laden, leeg, fout); geen dode knoppen.
7. MediaGuard is alleen-lezen referentie: MSO gebruikt uitsluitend de gedocumenteerde MediaGuard-API; geen nieuwe
   schrijfroutes naar MediaGuard. Geen privétracker-scraping. Meldingen alleen in de app.

## Werkwijze per pakket

- Elk pakket werkt in een eigen git-worktree `/home/user/wt/<WP>` op branch `wp/<WP>` (al aangemaakt), en commit
  daar (meerdere commits mag). Niet pushen, niet mergen, geen andere branches.
- Tests: `cd /home/user/wt/<WP>/apps/core && /home/user/media-source-optimizer/.venv/bin/python -m pytest -q -p no:warnings`
  (de testdatabaseserver draait op 127.0.0.1:55432; elke pytest-sessie maakt zijn eigen database). Draai vanuit
  `apps/core` van je eigen worktree, zodat je eigen `mso` wordt geïmporteerd. Snel deel: `MSO_FEATURES=usenet …`.
  Lint: `cd /home/user/wt/<WP> && /home/user/media-source-optimizer/.venv/bin/python -m ruff check apps/core apps/mocks tests/stack`.
  Web (als je web wijzigt): `cd /home/user/wt/<WP>/apps/web && npx tsc --noEmit && npx eslint <gewijzigde bestanden>`
  (`node_modules` is een symlink naar de hoofdcheckout). Geen docker, geen `npm ci`, geen Playwright.
- Klaar = ruff schoon, **volledige** pytest groen, tsc schoon, nieuwe tests voor elk nieuw gedrag (unit + via de
  echte HTTP-mocks in `apps/mocks/msomocks`), en een notitiebestand `docs/dev/v160/<WP>.md` met: wat er veranderd is,
  bewust veranderde tests, Nederlandse tekst voor CHANGELOG en USER_GUIDE, open punten.
- **Niet** aanpassen: `CHANGELOG.md`, `TEST_RESULTS.md`, `README.md`, `release/VERSION`, versienummers, andere
  docs dan je notitiebestand. Dat integreert de hoofdagent.
- Migraties: alleen het eigen nummer hieronder (versie > 0600, zodat terugdraaien naar 1.5.0 = `migrate --to 501`
  blijft werken). Elke migratie heeft een werkende `-- migrate:down`.
- Nieuwe UI-teksten in een **eigen** i18n-bestand `apps/web/lib/i18n/messages/nl/<naam>.ts` (één import + één spread
  in `messages/index.ts`). Bestaande sleutels alleen wijzigen als ze bij je pakket horen.
- Gedeelde bestanden: blijf binnen je eigen functies/secties; voeg nieuwe code toe op de plek die hieronder staat,
  zodat de merges schoon blijven.
- Gedeelde basis (al in de basiscommit): migratie `0600_v160_base.sql` (`usenet_server_autoheal.recovered_at`,
  `last_test_at`, `last_test_result`; tabel `change_overrides`) en `mso/features/automation/overrides.py`.

## Pakketten

### WP-A — Usenet: echte oorzaak + alleen zinvol opnieuw proberen (C14, C03-stap 0, C17) — migratie 0601
1. **Tellerfout**: sla `articles_success = max(tried − failed, 0)` op (SABnzbd-veld `articles_success` = failed).
   Migratie 0601 corrigeert bestaande rijen in `usenet_server_stats`. Pas de mock (`server_stats`) aan aan het echte
   SABnzbd-gedrag en de fixture in `test_usenet_sab_server_status.py` (eweka wordt 99,6 %). Controleer alle
   gebruikers van de kolom (metrics, api, recommend, UI "Artikelen gevonden").
2. **Verwerkingslogboek**: nieuw `usenet/stagelog.py` dat het SABnzbd-`stage_log` van een historieslot omzet in
   feiten: ontbrekende artikelen, herstel (gelukt/mislukt/niet nodig), precieze uitpakfout (geredigeerd), wachtwoord
   vereist, schijf vol, geen schrijfrechten, uitpakprogramma-fout, beschadigd archief, uitvoerbaar bestand. Gebruik
   de echte SABnzbd-teksten (broncode: `postproc.py`, `newsunpack.py`, `nzbstuff.py`, `api.py` build_history).
   Bewaar de feiten in `download_outcomes.meta` met `classifier_version: 2`.
3. **Classificatie**: `with_server_context` koppelt een server alleen nog als *waarschijnlijke oorzaak* als er
   artikelen ontbraken of herstel mislukte (of, zonder feiten, bij `incomplete`). "Uitpakken mislukt" met complete
   data krijgt bewijs "Data was compleet; uitpakken mislukte: <regel>" en geen servertoeschrijving.
4. **Systeemmelding**: herhaalt dezelfde uitpak-/omgevingsfout zich (≥ 3 keer in 7 dagen), dan één in-app melding
   (aanbeveling, verschijnt in "Wacht op jou") "SABnzbd kan downloads niet uitpakken" met de precieze regel, aantal en
   voorbeelden; ontdubbeld; sluit vanzelf als het 3 dagen niet meer voorkomt.
5. **Herbeoordeling**: eenmalige taak na de upgrade (en op verzoek) die de SABnzbd-historie (ook archief, SAB ≥ 4:
   `archive=1`) opnieuw classificeert; audit `usenet.reclassified` met aantallen; rijen die niet meer in SABnzbd staan
   houden hun oude label maar zijn herkenbaar (`classifier_version` 1/afwezig).
6. **Opnieuw proberen** (`retry.py`): alleen bij een serveroorzaak die SABnzbd zelf noemt, of bij ontbrekende
   artikelen/mislukt herstel — niet bij "uitpakken mislukt" met complete data. Alleen als de dekking is verbeterd
   ná het mislukken: nieuw `usenet/coverage.py` met `last_gain_at(db)` = max(`usenet_server_autoheal.recovered_at`,
   eerste keer gezien van een nieuw ingeschakelde server — bijhouden in een eigen tabel uit 0601). Bestaande
   voorwaarden blijven. **Effectiviteitsrem**: ≥ 10 afgeronde pogingen in 14 dagen en < 30 % gelukt → automatisch
   opnieuw proberen pauzeert zichzelf (audit `usenet.retry_paused`, in-app uitleg), hervat bij een nieuwe
   dekkingswinst of via een knop (operator+). UI (Usenet-providers → Autopilot): "opnieuw geprobeerd N · gelukt X %".
7. **Downloads-scherm**: per mislukte download de feiten ("Data compleet · uitpakken mislukt: …" of "312 artikelen
   ontbraken").
Bezit: `features/usenet/{failures,history,retry,metrics,recommend}.py`, nieuw `stagelog.py`, `unpack.py`,
`coverage.py`; in `features/usenet/api.py` alleen retry/diagnose-endpoints en de artikelberekening;
`connectors/sabnzbd.py`: alleen `history()` (parameters zoals `archive`); `apps/mocks/msomocks/sabnzbd.py`: alleen
history-/server_stats-gedeelte; web `components/mso/usenet/autopilot.tsx` (retry-deel),
`components/mso/downloads/usenet-tab.tsx` (feiten), i18n `nl/usenetdiag.ts`.

### WP-B — Usenet-servers testen i.p.v. blind aanzetten + vervaldata (C10, C11) — migratie 0602
1. `SabClient.test_server(name)` via `mode=config&name=test_server` met de serverinstellingen uit `get_config` en het
   gemaskeerde wachtwoord plus `server=<name>` zodat SABnzbd zijn opgeslagen wachtwoord gebruikt (controleer
   `_api_config_test_server` / `test_nntp_server_dict` in de SABnzbd-broncode). Resultaat: ok / inloggen geweigerd /
   verlopen / te veel verbindingen / onbereikbaar / onbekend. Gebruikersnaam alleen in het geheugen. Plaats nieuwe
   methoden direct na `warnings()` in `connectors/sabnzbd.py`.
2. `autoheal.py`: uitgeschakelde servers worden periodiek getest (instelbaar, standaard 6 u, na herhaald falen
   24 u, nooit opgeven: minimaal wekelijks). Alleen na een geslaagde test weer aan (via `propose_and_apply`), en pas
   "hersteld" (`recovered_at` zetten) als SABnzbd hem echt gebruikte (artikelen/bytes voor die server gestegen) zonder
   nieuwe fouten. Verlopen account: geen proefronde, één rustige melding, wekelijkse test (verlenging herkennen).
   "Te veel verbindingen" = geen dood account (niet als mislukte test tellen). Geen blinde productieproef meer,
   behalve als terugval wanneer SABnzbd de test niet ondersteunt.
3. Vervaldata (C11): lees `expire_date`, `quota`, `usage_at_start` in `servers()`; in-app herinneringen 14/7/1 dag
   vóór verlopen (ontdubbeld), blok-restant met voorspelling; vul een lege verlengdatum onder Kosten voor (alleen
   MSO's eigen gegevens).
Bezit: `features/usenet/{autoheal,serverstatus,providers}.py`, sectie server-tests in `features/usenet/settings.py`
(onderaan), `connectors/sabnzbd.py` (`servers()` + nieuwe methoden na `warnings()`), mock: `test_server` en
`get_config`-servervelden, web serverdelen van `components/mso/usenet/{autopilot,providers,priority}.tsx`, i18n
`nl/usenetserver.ts`. Niet: `automation/*` (de evaluatie van een heringeschakelde server doet WP-E1).

### WP-C — Categorie '*' ophelderen (C34, ingeperkt) — migratie 0603 (alleen als nodig)
1. MediaGuard-leesroutes (bestaan in mg-latest, rol viewer, geheimen gemaskeerd): `GET /api/v1/instances`,
   `/api/v1/instances/{id}/downloadclients`, `/api/v1/instances/{id}/health` (controleer exacte paden in
   `mg-latest/apps/core/mediaguard/instances/manage.py`). Als leesmethoden in de connector + rijen in de
   compatibiliteitsmatrix (`features/mediaguard/compat.py`), optioneel/gracieus bij 403/404. `ALLOWED_WRITES` blijft
   ongewijzigd.
2. Nieuw `features/usenet/categories.py`: diagnose (a) *arr stuurt categorie X die SABnzbd niet kent → SABnzbd zet
   het in '*'; (b) er wordt geen categorie meegestuurd → oplossen in MediaGuard (provisioning), niet in Sonarr;
   (c) categorieën kloppen maar toch '*' → andere bron. Toon de *arr-gezondheidsmeldingen die erover gaan en het
   aantal '*'-downloads per dag.
3. Bij (a): één voorstel "SABnzbd-categorie X aanmaken" (risico `toestemming_nodig`, nooit automatisch) met in het
   voorbeeld de nieuwe map en de waarschuwing dat MediaGuard downloads in die categorie daarna volgens zijn eigen
   regels opruimt (dat deel is niet terug te draaien). Nieuwe change-target `sabnzbd_categories`, pad
   `categories/<naam>`, eigen applier (schrijven via SABnzbd `set_config` section=categories, terugdraaien via
   `del_config`, controle via `get_cats`/`get_config`) — plaats de applier direct na de `RETRY_TARGET`-registratie
   in `connectors/sabnzbd.py`.
4. Vervang de teksten `defaultCatBody`/`defaultCatFix` (nl/usenet.ts) die zeggen "pas Sonarr aan".
Bezit: nieuw `features/usenet/categories.py`, `connectors/mediaguard.py` (alleen leesmethoden),
`features/mediaguard/compat.py`, het categorie-endpoint in `features/usenet/api.py`, `connectors/sabnzbd.py`
(`categories()` + nieuwe applier na retry-registratie), mocks: MediaGuard downloadclients/health, SABnzbd
categorieën-config; web `components/mso/usenet/categories.tsx`, '*'-tekst in downloads, i18n `nl/usenetcat.ts`.

### WP-D — Eerlijke doelenkaart en meting (C01, C02) — migratie 0604
1. `download_outcomes.failed_at`: gezet bij de eerste overgang naar `failed` (bestaande rijen: backfill). Alle
   vensters die nu `COALESCE(finished_at, observed_at)` gebruiken gaan naar `COALESCE(finished_at, failed_at)`, zodat
   een vastgelopen torrent niet eeuwig "mislukt in de laatste 24 u" telt (`overview/api.py`,
   `automation/evaluators.py` alleen `failure_series`, waar nog meer).
2. Faalratio **per aflevering/film** (netto: een item dat uiteindelijk lukte telt als gelukt) naast **per poging**
   (bruto), met n en "te weinig data" bij n < 20. Identiteit via de bestaande releasenormalisatie.
3. Doelenkaart bovenaan Overzicht: per doel startwaarde, nu, doel, "dag X van 28", trend (dagelijkse momentopnames,
   lokale dagen Europe/Amsterdam / ingestelde tijdzone) en één zin wat het doel tegenhoudt (bijv. "41 wachten op jouw
   goedkeuring", "meeste mislukkingen: uitpakken mislukt"). Falende servers = ingeschakeld en falend; servers die MSO
   uitzette apart en eerlijk ("4 uitgezet omdat ze niet werken; herstellen of verwijderen in SABnzbd is jouw keuze").
   Proefperiode-start instelbaar (standaard: eerste keer Autopilot aan, anders setup-datum), lengte 28 dagen.
   API `GET /api/v1/overview/goals` (viewer) + blok in `/overview`.
Bezit: `features/overview/api.py` (failures-blok + nieuw goals-blok; niet connectors/health), nieuw
`features/overview/goals.py`, `domain/outcomes.py`, `features/torrent/ingest.py` (alleen failed_at),
`automation/evaluators.py` (alleen `failure_series`), web `app/(app)/page.tsx` (kaart als eerste element),
nieuw `components/mso/overview/goals-card.tsx`, i18n `nl/goals.ts`.

### WP-E1 — Kernveiligheid van wijzigingen en Autopilot (C12, C25-fix, C26, C28, C36-deel, C35-deel) — migratie 0605
1. C12: `_rollback_disables` herkent ook 0/1 (niet alleen False/True); test met int-waarden. Een heringeschakelde
   SABnzbd-server (enable 0→1) wordt beoordeeld op de eigen serverstatus (via bestaande `serverstatus`-functies), niet
   op de globale faalratio.
2. Geen "terugdraaien aanbevolen" voor `one_way`/`irreversible` wijzigingen.
3. C26: afkoeling voor mislukte automatische wijzigingen (1 u → 4 u → 24 u; na 3× geparkeerd met één "Wacht op
   jou"-item; gewijzigd voorbeeld reset), mislukte pogingen tellen mee voor `automation.max_per_hour`; **noodrem**:
   ≥ 3 automatische terugdraaiingen of ≥ 5 mislukte automatische toepassingen in 24 u → `autopilot_hold` (alleen
   automatische schrijfacties stoppen; synchronisaties en voorbereiden gaan door), audit `autopilot.self_paused`,
   banner met "Hervatten" (operator+). "Autopilot terugdraaien (24 u / 7 d)" voor omkeerbare automatische
   wijzigingen (niet in Alleen-lezen, bevestiging, verwijderingen nooit).
4. C28: terugdraaien met driftcontrole per op (afwijkende huidige waarde → "overgeslagen: buiten MSO gewijzigd"),
   controle na schrijven, geen valse "teruggedraaid"; atomaire statusovergangen (`FOR UPDATE` /
   `UPDATE … WHERE status IN … RETURNING`).
5. C36 crash-veilig toepassen: status `applying` vooraf vastgelegd met observed_before, resultaat per op direct
   opgeslagen; bij start van api/worker een reconciler voor `applying` (afronden of omkeerbare ops terugzetten,
   audit `change.interrupted`, "Wacht op jou"-item).
6. Eigenaar-override: `why_not()` weigert automatisch toepassen als `overrides.active_for_ops(...)` iets vindt; reden
   "Je hebt dit zelf teruggezet; MSO past het niet opnieuw automatisch toe".
Bezit: `changes.py`, `state.py`, `features/automation/{engine,evaluators (niet failure_series),autopilot,api}.py`,
web `components/mso/automation/*` + een nieuwe banner-component, i18n `nl/safety.ts`.

### WP-E2 — Onderhoud: bewaartermijnen, lichtere back-ups, back-up vóór upgrade, koppelingsbewaking (C29, C36-deel, C27) — migratie 0606
1. C29: dagelijkse taak `system.retention` (instellingen onder Systeem): afgeronde jobs > 14 d (laatste 50 per soort
   houden), mislukte > 60 d; ruwe metingen > 30 d → uursamenvatting (400 d); automation_log 180 d; login_attempts
   90 d; audit_log blijft. VACUUM (ANALYZE) in autocommit. Controleer dat evaluaties/grafieken niet verder dan de
   ruwe bewaartermijn terugkijken.
2. Back-ups: tabeldata die opnieuw op te halen is uitsluiten (controleer tabelnamen), na herstel automatisch opnieuw
   synchroniseren; veiligheidsback-ups > 30 d opruimen (nieuwste 2 houden); dump overslaan bij te weinig vrije ruimte
   met een duidelijk "Wacht op jou"-item.
3. C36: back-up vóór migraties als er openstaande migraties zijn en er ruimte is (all-in-one `supervisor.py` én
   api-start in `app.py`), soort `pre-upgrade`, nieuwste 3 houden, audit `backup.pre_upgrade`; mislukt het, dan
   duidelijke logregel en gewoon doorstarten.
4. C27: achtergrondresultaten van SABnzbd/qBittorrent/Prowlarr werken `connectors.status`/`last_error` bij (+
   `status_since`, `last_ok_at`); classificatie sleutel geweigerd / offline / time-out; geheimen nooit in
   `last_error`; Overzicht toont "qBittorrent onbereikbaar sinds 2 d 4 u" i.p.v. aantallen mislukte taken; taken
   voor een onbereikbare koppeling hooguit elke 15 min; bij herstel meteen een volledige ronde; "Wacht op jou" alleen
   bij geweigerde sleutel > 15 min of offline > 24 u.
Bezit: `backups.py`, `jobs.py`, `connectors/base.py`, `deploy/allinone/supervisor.py`, `app.py` (opstarthaak),
`__main__.py`, `features/overview/api.py` alleen `connectors_block`/`health_summary`, nieuw
`mso/retention.py` (of `features/system/`), web systeem-/back-upschermen en de koppelingenkaart op Overzicht, i18n
`nl/maintenance.ts`.

### WP-G1 — Rust in de app: één inbox, "Wat MSO deed" zonder ruis, voetafdruk (C06, C30, C35) — migratie 0607
1. C06: `waiting()` ontdubbelt per-torrent malware-aanbevelingen die al in de open opruimbundel zitten, toont de bundel
   één keer ("41 nep-downloads opruimen (+3 nieuw sinds gisteren)"), groepeert de rest, voegt `decisions` toe (API
   compatibel). Overzicht: "Wacht op jou" eerst (of één regel "Niets wacht op jou ✓"). Shell: teller in de topbalk
   (ook ≤ 860 px), badge op Automatisering, tabtitel "(N) ".
2. C30: `actions_total` zonder synchronisaties/evaluaties (bestaande `total` blijft); synchronisaties als één
   statusregel met link naar Systeem; per actie Wat / Waarom / Effect (uit de evaluatie); inline "Terugdraaien" voor
   omkeerbare toegepaste wijzigingen (bestaand endpoint + DangerDialog); lokale dagen.
3. C35: taak `automation.footprint` (elk uur, alleen lezen, in elke modus): per toegepaste, niet teruggedraaide op op
   een omkeerbare applier de huidige waarde lezen → "nog actief" / "buiten MSO gewijzigd (nu X)" / "bron
   onbereikbaar"; bij buiten-MSO-wijziging `overrides.record(..., days=<instelling, standaard 30>)` + audit
   `footprint.overridden` + één rustig info-item. Pagina Automatisering → Voetafdruk per systeem met origineel / MSO /
   nu / status, en "Alles terugzetten" (operator+, niet in Alleen-lezen, eerst lijst + DangerDialog; overslaan wat niet
   terug kan, met uitleg).
Bezit: `features/overview/autopilot.py`, nieuw `features/automation/footprint.py` (+ eigen router-bestand),
web: Wacht-op-jou/autopilot-overzichtcomponenten, shell (`components/shell/*`), nieuwe pagina onder
`app/(app)/automatisering/`, i18n `nl/calm.ts`.

### WP-G2 — Nep-releases tegenhouden bij MSO's eigen verzendingen (C20) — migratie 0608 (alleen als nodig)
1. Gedeelde helper (`domain/release.py` of vergelijkbaar) `executable_hint(title, files)` die
   `torrent/diagnostics` hergebruikt (EXEC_EXT, dubbele extensies zoals `.mkv.exe`).
2. `torrent/policy.check_release` (bestaat nog niet; kandidaten vallen nu terug op alleen seeders — die controle moet
   erin blijven): weiger uitvoerbare (dubbele) extensies en absurde groottes (≥ 720p-aflevering < 50 MB).
3. Controle vóór verzenden in `recovery/actions` en `search/api` send (ook handmatig): .torrent-bestandslijst
   parseren (`crossseed.parse_torrent`), weigeren met "Beleid weigert deze release: bevat uitvoerbaar bestand
   \"x.exe\"", audit `recovery.send_refused`; magnet → titelcontrole.
4. Zoeken: vlag "uitvoerbaar" met rode badge "Nep-release (uitvoerbaar bestand)", lager gerangschikt.
Bezit: `features/torrent/policy.py`, `features/recovery/{candidates,actions}.py`, `features/search/{api,service}.py`,
nieuwe helper, web zoekresultaat-badge, i18n `nl/releasegate.ts`.

## Integratie (hoofdagent)
Merge alle `wp/*`-branches, los conflicten op, draai alles (pytest, stack, Playwright 1440/390 licht/donker,
all-in-one-test), onafhankelijke review, docs (CHANGELOG, USER_GUIDE, TEST_RESULTS, MIGRATION, OPERATIONS), versie
1.6.0, release.
