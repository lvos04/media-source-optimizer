# WP-B — Usenet-servers testen i.p.v. blind aanzetten + vervaldata (C10, C11)

Branch `wp/B`, migratie **0602** (`0602_v160_server_tests.sql`, met werkende `migrate:down`).

## Nagekeken tegen de echte SABnzbd-broncode (master = 5.1.3)

- `api._api_config_test_server` → `test_nntp_server_dict(kwargs)`, antwoord `{"value": {"result": bool, "message": str}}`
  (toegangsniveau 3 = volledige API-sleutel, zoals `get_config`/`set_config`). Bij een vergrendelde configuratie
  `{"status": false, "error": "Configuration locked"}`, onbekende `name` → `"not implemented"`.
- Een gemaskeerd wachtwoord (alleen sterretjes) wordt alleen vervangen door het opgeslagen wachtwoord als `server`
  een bestaande servernaam is; anders "Password masked in ******, please re-enter". `get_config` geeft het
  wachtwoord als `"*" * 10` (`OptionPassword.get_stars`). MSO stuurt dus altijd `password=**********` + `server=<naam>`
  en nooit een echt wachtwoord. Geen `timeout` meegestuurd → SABnzbd gebruikt `DEF_NETWORKING_TEST_TIMEOUT` (5 s);
  de HTTP-time-out voor deze ene aanroep is 45 s (SABnzbd probeert bij onbereikbaar nog 80/119/563).
- SABnzbd geeft het NNTP-antwoord van de provider ongewijzigd door ("502 Authentication Failed", "482 Account
  expired"); eigen teksten ("Timed out", `Server address "h:563" is not valid.`, "Server requires username and
  password.") worden eerst herkend, zodat een poortnummer nooit als NNTP-code wordt gelezen.
- `ConfigServer`: `expire_date` (ISO-datum of ""), `quota` (K/M/G/T-notatie, 1024-basis, `misc.from_units`),
  `usage_at_start` (SABnzbd's `grand_total` op het moment dat het quotum werd ingesteld). SABnzbd wist
  `expire_date` op de vervaldag zelf (`downloader.check_server_expiration`) en `quota` als het op is
  (`check_server_quota`); resterend = `quota − grand_total + usage_at_start` (`interface.py`).
- De mock (`apps/mocks/msomocks/sabnzbd.py`) volgt dit gedrag: `config&name=test_server` met dezelfde
  maskerregel, de accountstatus per server (blijft bestaan nadat SABnzbd `servererror` wist), `fix_account()`,
  `test_results`-overrides, `test_calls` (wat MSO stuurde), fouten `test_server=unsupported|locked`;
  `get_config` met `keyword`, wachtwoord als sterretjes en de velden `expire_date`/`quota`/`usage_at_start`.

## Wat er veranderd is

### Connector (`connectors/sabnzbd.py`, alleen `servers()` + nieuwe methoden na `warnings()`)
- `SabClient.test_server(name)` → `{supported, ok, message}`; POST (gebruikersnaam in de body, nooit in de URL of
  een log), gebruikersnaam alleen in het geheugen en uit SABnzbd's antwoord vervangen door `<gebruiker>`.
- `servers()` geeft nu ook `expire_date`, `quota_bytes`, `usage_at_start`; met `fingerprint_key` een HMAC van
  host/poort/SSL/gebruikersnaam/vervaldatum/quotum (sleutel willekeurig, in de kluis) zodat MSO ziet dat de eigenaar
  de server in SABnzbd aanpaste zonder de gebruikersnaam op te slaan.

### Classificatie (`serverstatus.py`)
- `classify_test()` → ok / inloggen geweigerd / verlopen / toegang geweigerd / blok op / **te veel verbindingen**
  (geen dood account) / onbereikbaar / TLS-fout / onbekend / niet beschikbaar.
- Een fout die alleen uit SABnzbd's waarschuwingslijst komt telt niet meer als de servertest ná die waarschuwing
  slaagde (achterhaalde melding). Een nieuwe waarschuwing na de test telt wel; SABnzbd's live `servererror` wordt
  nooit overschreven.
- `refresh()` roept na het verversen `accounts.sync(servers)` aan (in een try: breekt de serverstatus nooit).

### Servertest (`servertest.py`, nieuw, eigen router)
- `run()`: test, opslag in `usenet_server_tests` (30 dagen / 100 per server), observatie `sab.server_test_ok`,
  audit `usenet.server_test` (bij een gebruiker altijd, automatisch alleen bij een ander resultaat dan de vorige).
- `flapping()`: binnen 7 dagen een geslaagde test **gevolgd door** een weigering/te veel verbindingen (test of
  SABnzbd-melding > 30 min na de test). Kapot → gerepareerd telt niet. Eén rustig advies
  `usenet.server_flapping:<server>` ("waarschijnlijk te veel verbindingen of een gedeeld account"), lost vanzelf op.
- `POST /api/v1/usenet/sab/servers/{server}/test` (operator, **elke modus**, ook Alleen-lezen: de test verandert
  niets; 10/min) en `GET /api/v1/usenet/sab/server-tests` (kijker).

### Autopilot (`autoheal.py`)
- **Uitzetten** (alleen Autopilot): pas na een bevestigende SABnzbd-test. Geslaagd of "te veel verbindingen" →
  server blijft aan (`kept_on`). Een recente test (≤ 1 u, ≤ 6 u bij wisselende resultaten) wordt hergebruikt.
  Faalt een server opnieuw binnen een week nadat hij weer werkte, dan gaat hij zonder de 6-uurswachttijd uit, maar
  alleen als de test het bevestigt. Nooit de laatste ingeschakelde server (ongewijzigd).
- **Testen**: elke server die MSO uitzette wordt in elke modus getest: `usenet.server_test_interval_h` (6 u), na
  een week zonder geslaagde test `usenet.server_test_slow_interval_h` (24 u), nooit opgeven (maximaal een week);
  verlopen account wekelijks. "Te veel verbindingen" telt niet als mislukte test (opnieuw na 1 u). Een wijziging
  van de server in SABnzbd, een nieuwere geslaagde eigen MSO-test of "Testen" → meteen testen. Max. 6 tests per
  ronde (elke test blokkeert enkele seconden).
- **Weer aan**: alleen na een geslaagde test, via `propose_and_apply` (Autopilot automatisch; anders één voorstel
  `usenet.auto_on:<server>` in "Wacht op jou"; Alleen-lezen past niets toe). Een latere mislukte test verwerpt dat
  voorstel. "Opnieuw proberen" (gebruiker) blijft direct aanzetten.
- **Hersteld**: `recovered_at`/status `recovered` pas als SABnzbd de server echt gebruikte (bytes of artikelen voor
  die server gestegen sinds het aanzetten, uit live `server_stats`) zonder nieuwe accountfout; tot die tijd
  "test geslaagd en server aan; SABnzbd heeft hem nog niet gebruikt". Statistiek-reset in SABnzbd → nieuwe basis.
- **Verlopen account**: geen proefronde, één rustig "Wacht op jou"-item `usenet.server_expired:<server>`
  (aanbeveling, risico `toestemming_nodig`: verlengen of in SABnzbd verwijderen is jouw keuze; wordt niet elke ronde
  ververst; opgelost zodra de server weer aan is of uit SABnzbd verdwijnt), wekelijkse test, nieuwe vervaldatum in
  SABnzbd = meteen testen (verlenging herkennen).
- **Terugval**: biedt SABnzbd de test niet aan (of is de configuratie vergrendeld), dan de 1.5.0-proef met backoff
  en `given_up` na `usenet.auto_probe_max` — nooit voor een verlopen account; elke week kijkt MSO opnieuw of de test
  beschikbaar is. Bestaande `given_up`-rijen uit 1.5.0 worden weer getest en dan `disabled`.
- `public_row()` + `overview()` geven `next_test_at`, `tests_failed`, `last_test_*`, `test_supported`,
  `recovered_at`, `waiting_for_use`, `test_interval_h`.

### Vervaldata en blokken (`accounts.py`, nieuw, eigen router) — C11
- `usenet_server_accounts` onthoudt de laatst geziene vervaldatum (SABnzbd wist hem op de dag zelf), quotum en
  `usage_at_start`.
- Herinneringen 14/7/1 dag vóór verlopen voor ingeschakelde servers (`usenet.server_expiry:<server>:<datum>:<stap>`,
  risico `toestemming_nodig` → "Wacht op jou"); vorige stap wordt opgelost, een weggeklikte stap blijft weg, de
  tekst wordt alleen ververst als hij verandert.
- Blokrestant: SABnzbd-quotum (totaal − `usage_at_start`), anders de blokgrootte van de provider minus SABnzbd's
  totaal op het moment dat die grootte werd ingesteld (nieuw: `usenet_providers.block_baseline_bytes/_at`; zonder
  basis het oude gedrag). Voorspelling uit het gemiddelde dagverbruik van de laatste 14 volle dagen (dagen zonder
  verkeer tellen als 0, minimaal 3 dagen). Herinnering `usenet.block_low:…` bij ≥ 90 % of ≤ 7 dagen.
- Kosten: een **lege** verlengdatum van de gekoppelde dienst wordt ingevuld met SABnzbd's vervaldatum (audit
  `costs.renewal_prefilled`); een datum die MSO zelf invulde schuift mee met SABnzbd, een eigen datum van de
  eigenaar wordt nooit overschreven. De Kosten-herinnering "Verlenging X over N dag(en)" voor dezelfde dienst en
  datum wordt samengevoegd met de Usenet-herinnering (anders twee meldingen voor hetzelfde).
- `GET /api/v1/usenet/sab/accounts` (kijker, geen SABnzbd-aanroep).

### Providers (`providers.py`)
- "Testen" werkt zonder inloggegevens in MSO als de provider aan een SABnzbd-server gekoppeld is (SABnzbd's eigen
  test; de eigen MSO-teststatus blijft "geen inloggegevens"). Een geslaagde test van een door MSO uitgezette server
  volgt dezelfde weg als hierboven.
- Import uit SABnzbd: een server met quotum wordt `kind=block` met blokgrootte en SABnzbd's `usage_at_start` als basis.
- `public()` geeft `block` (restant + voorspelling), `expiry` en `sab_test`; voor `kind=block` komen
  `block_used_bytes`/`block_size_bytes` nu uit het quotum of vanaf de basis.

### Instellingen (`settings.py`, onderaan)
- Nieuw: `usenet.server_test_interval_h` (6 u, 1–48), `usenet.server_test_slow_interval_h` (24 u, 6–168, geavanceerd).
- Helptekst van `usenet.auto_probe_interval_h` en `usenet.auto_probe_max`: alleen nog de terugval zonder servertest.

### Web
- Nieuw `components/mso/usenet/server-test.tsx`: `TestServerButton` ("Testen", operator, elke modus),
  `ServerTestLine`, `ExpiryNote`, `BlockMeter`, `expiryFromDate`.
- `autopilot.tsx` (serverdeel): volgende test i.p.v. volgende proef, laatste test, "wacht tot SABnzbd hem
  gebruikt", knop Testen naast Opnieuw proberen, nieuwe uitleg `usenetserver.apOn`.
- `priority.tsx` (servertabel): laatste test per server (+ wisselende resultaten), vervaldatum, knop Testen.
- `providers.tsx`: Testen ook zonder inloggegevens (via SABnzbd), regel SABnzbd-servertest en vervaldatum,
  blokmeter met restant en voorspelling.
- `lib/api/usenet.ts`: optionele velden + types `ServerTest*`, `BlockInfo`, `ExpiryInfo`, `ServerAccounts`.
- i18n `lib/i18n/messages/nl/usenetserver.ts` (één import + één spread in `messages/index.ts`).

## Keuzes waar het plan iets open liet
- "Na herhaald falen 24 u" = de server staat langer dan een week uit en de test slaagt nog niet (zoals C10: week 1
  elke 6 u, daarna dagelijks). Maximaal een week tussen tests, ook bij verlopen accounts.
- Uitzetten blijft na 6 u (geen strenger gedrag dan 1.5.0), behalve de expliciete C10-regel "opnieuw falen binnen
  een week na herstel"; maar altijd pas na een bevestigende test.
- Een geslaagde test buiten Autopilot geeft een voorstel (ook in Alleen-lezen zichtbaar in "Wacht op jou", niets
  toegepast) — zo hoeft de eigenaar alleen goed te keuren.
- MSO's eigen NNTP-test (met inloggegevens in MSO) zet een server niet meer direct aan: de inloggegevens in MSO
  kunnen afwijken van die in SABnzbd. Hij laat SABnzbd meteen testen; alleen zonder servertest telt hij zoals in 1.5.0.
- Verlopen-melding, vervalherinneringen en blokherinnering staan als aanbeveling met risico `toestemming_nodig`
  (de huidige `waiting()` toont alleen die in "Wacht op jou"); het flapping-advies is `advies`.

## Bewust veranderde bestaande tests (`tests/test_usenet_autopilot_servers.py`)
- `test_autopilot_switches_failing_servers_off_expired_immediately`: verwacht nu `next_probe_at IS NULL`, de
  bevestigende test (`last_test_result = account_expired`) en `next_test_at` over 7 dagen (verlopen: geen proef).
- `test_probe_reenable_with_backoff_then_recovered` → vervangen door
  `test_reenable_only_after_passing_test_then_recovered_on_use` (geen blinde proef; aan na geslaagde test; uit na
  bevestigde fout; hersteld pas na gebruik).
- `test_bounded_probes_give_up` → `test_bounded_probes_give_up_only_without_server_test` (nooit opgeven met
  servertest; de begrensde proef alleen als terugval, nooit voor verlopen).
- `test_own_successful_test_switches_server_back_on`: MSO's eigen test laat nu SABnzbd testen; pas als die slaagt
  gaat de server aan (`probe_trigger = server_test`).
- Overige bestaande tests ongewijzigd. Nieuw: `tests/test_usenet_server_tests.py` (connector + mock over HTTP,
  classificatie, API/rollen/geheimen, bevestigen, testen, verlopen, te veel verbindingen, voorstel buiten Autopilot,
  Testen-knop, legacy given_up, flapping, provider-Testen via SABnzbd, vervalherinneringen, Kosten-invulling,
  blokvoorspelling, import als blok, blokbasis).

## Tekst voor CHANGELOG (Nederlands)

### Usenet-servers: testen in plaats van blind aanzetten
- MSO zet een kapotte SABnzbd-server pas uit als **SABnzbd's eigen servertest** (dezelfde als 'Test server' in
  SABnzbd, met het wachtwoord dat daar staat) het probleem bevestigt. Slaagt de test, of meldt de provider alleen
  "te veel verbindingen", dan blijft de server aan.
- Uitgezette servers zet MSO niet meer blind als proef aan. SABnzbd test ze elke 6 uur (na een week dagelijks,
  verlopen accounts wekelijks; MSO geeft nooit op) en pas na een geslaagde test gaat de server weer aan. Pas je de
  server in SABnzbd aan, of klik je op **Testen**, dan test MSO meteen.
- "Werkt weer" telt pas als SABnzbd de server echt gebruikt zonder nieuwe fouten.
- Verlopen account: geen proeven meer, één rustige melding in "Wacht op jou" (verlengen of verwijderen in SABnzbd),
  en MSO ziet een verlenging vanzelf.
- **Testen** op Usenet-providers werkt nu ook zonder inloggegevens in MSO.
- Wisselende testresultaten → advies "waarschijnlijk te veel verbindingen of een gedeeld account".
- Biedt SABnzbd de test niet aan, dan werkt MSO zoals in 1.5.0 (proef met wachttijd).

### Vervaldata en blokken
- Vult je in SABnzbd een vervaldatum of quotum in bij een server, dan herinnert MSO je 14, 7 en 1 dag vóór het
  verlopen, toont het wat er van een blok over is met een voorspelling wanneer het op is, en vult het een lege
  verlengdatum onder Kosten in. Servers met een quotum worden bij importeren een blokaccount.
- Het blokverbruik telt vanaf het moment dat je de blokgrootte invult (niet meer SABnzbd's totaal sinds de laatste
  reset).

## Tekst voor USER_GUIDE (Nederlands)

**Hoe MSO kapotte Usenet-servers afhandelt.** In Autopilot zet MSO een SABnzbd-server met een accountprobleem
(inloggen geweigerd, toegang geweigerd, blok op) na 6 uur uit, een verlopen account meteen — maar alleen als
SABnzbd's eigen servertest het probleem bevestigt. Die test is dezelfde als de knop 'Test server' in SABnzbd:
SABnzbd logt in met het wachtwoord dat daar staat; MSO ziet dat wachtwoord nooit en bewaart ook je gebruikersnaam
niet. Een uitgezette server laat MSO elke 6 uur testen (na een week dagelijks, een verlopen account wekelijks). Pas
als de test slaagt, zet MSO de server weer aan. Ben je niet in Autopilot, dan staat er een voorstel klaar in
"Wacht op jou". Heb je het wachtwoord in SABnzbd gerepareerd? Klik op **Testen** bij de server (Usenet-providers →
Serverprioriteit of Autopilot); dat werkt in elke modus en verandert niets. Een server telt pas als "werkt weer"
als SABnzbd hem echt gebruikt zonder nieuwe fouten.

**Te veel verbindingen.** Meldt de provider "te veel verbindingen", dan werkt het account wel: MSO zet de server
niet uit. Wisselen de testresultaten, dan krijg je het advies het aantal verbindingen in SABnzbd te verlagen of te
controleren of iemand anders het account gebruikt.

**Vervaldatum en blok.** Vul in SABnzbd (Config → Servers) bij een server de vervaldatum en/of het quotum in. MSO
herinnert je dan 14, 7 en 1 dag vóór het verlopen, toont bij de provider wat er van het blok over is en wanneer het
bij het huidige verbruik op is, en vult een lege verlengdatum onder Kosten in (een datum die je zelf invulde blijft
staan). Zonder quotum in SABnzbd telt het blokverbruik vanaf het moment dat je de blokgrootte bij de provider invult.

## Open punten
- Integratie: `api.py` is niet aangeraakt; de nieuwe endpoints staan in de routers van `servertest.py` en
  `accounts.py` (automatisch gemount). WP-A (`retry.readiness`, `coverage.last_gain_at`) leest `recovered_at`,
  dat nu pas na echt gebruik wordt gezet.
- WP-E1 beoordeelt een heringeschakelde server (`automation/*`), niet aangeraakt.
- WP-G1 herziet `waiting()`: de nieuwe aanbevelingen (`usenet.server_expired:*`, `usenet.server_expiry:*`,
  `usenet.block_low:*`) gebruiken risico `toestemming_nodig` zonder `change_id`, zodat ze in "Wacht op jou" staan.
- De i18n-sleutels `usenet.ap.on` en `usenet.ap.trigger.*` in `nl/usenet.ts` blijven bestaan; `usenet.ap.on` wordt
  niet meer gebruikt (opruimen bij integratie, niet in dit pakket om conflicten met WP-A te vermijden).
- Op de echte instantie nog bevestigen dat de SABnzbd-test van de eigenaar (5.1.3) "502 Authentication Failed"
  met code doorgeeft; de classificatie werkt ook zonder code (op trefwoorden).
- Een wachtwoordwijziging alleen in SABnzbd is niet zichtbaar in `get_config` (gemaskeerd): die ziet MSO bij de
  volgende test (≤ 6 u) of direct via **Testen**.
