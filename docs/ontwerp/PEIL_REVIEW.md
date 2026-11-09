# Onafhankelijke ontwerpreview MSO-web-UI tegen Peil 1.1.0 (stap 5)

**Datum:** 9 november 2026 · **Ontwerptaal:** Peil 1.1.0 (`design/ontwerptaal/`, O-14) · **Rol:** ontwerp-agent
**Beoordeeld:** de echte app, code-HEAD `c3dc933` (eind stap 3). **Gerepareerd tot:** app `8571054`, tests `426b49b`.
**Hoort bij:** `docs/ontwerp/PEIL_MIGRATIE.md` (ontwerp, stap 2/3), ADR-0006/0007, **ADR-0008** (keuzes bij deze
reparaties). Metingen en afdrukken: `docs/screenshots/peil/review/`.

Vaste regels van deze ronde zijn gevolgd: alleen uiterlijk, teksten en toegankelijkheid. Functies, API-routes,
foutformaat, datamodel, poorten en installatie zijn niet veranderd. `git diff c3dc933..HEAD -- apps/core apps/mocks
deploy tests scripts .github Makefile docs/openapi` is leeg; de OpenAPI-export is byte-gelijk (sha256
`bb551ee1…0471b`). `data-testid`'s en rollen zijn gelijk gebleven. Er is geen versie-ophoging; de CHANGELOG staat
onder Unreleased.

---

## 0. Oordeel in het kort

**De omzetting van stap 3 hield stand.** Op de beoordeelde HEAD vond het onafhankelijke meetscript in 410 metingen
(16 schermen × 6 breedtes × 2 thema's, plus tabs, Beheermodus en toestanden):
- **0 axe-overtredingen** op alle impacts op de schermen;
- **geen tekst onder 4,5:1** (de echte laagste waarde was 6,62:1);
- **geen UI-grens onder 3:1** (laagste 5,2:1);
- **geen aanraakdoel onder 44 px** (van 15 186);
- **geen tekst onder 14 px**;
- **een Peil-focusring op elke tabstop** (2 234 stops; ringcontrast ≥ 7,55:1; geen enkele volledig bedekt).

**Wel gevonden:** 23 afwijkingen van Peil, die allemaal zijn gerepareerd (§3).
- De twee zwaarste: horizontale overloop op 320 px (Torrentclient, Instellingen-tabs, tot 66 px) en een torrenttabel
  die op 1440 px de actieknoppen buiten de kaart duwde.
- Amber stond nog op 24 plekken die geen gezondheid zijn, waaronder "Privé" zonder icoon (status alleen in kleur).
  Daarnaast circle-check buiten Gezond, ellips op inhoud (160×), verboden woorden ("OK", "Let op", "Verstoord",
  270×), een offlinepagina zonder h1 en een grafiektabel die niet met het toetsenbord te scrollen was (axe
  `scrollable-region-focusable`).
- Uit de vergelijking met de prototypes: de ondernavigatie brak woorden af ("Downlo-ads") en de grafiek
  "Downloads in '*'" had geen as.

**Na de reparaties (HEAD `e7e88e2` en `8571054`):** 0 bij elke meting in de vier groepen, met twee uitzonderingen:
- de 16 treffers van het oude woord "Verstoord" in het **logboek**: dat is de opgeslagen audittekst uit de core
  (O-3);
- de bewust gedempte laatst-bekende waarde op de arcering. Die haalt 4,56:1 in licht en 5,01:1 in donker, precies
  de Peil-norm (≥ 4,5:1) en dezelfde waarden als in de prototypes.

**Verder getest:**
- Beide toegestane toevoegingen werken in de echte app: 23 van 23 controles, waarvan 6× ongeldig adres → 422 via
  de echte API.
- 200 %-zoom zonder overloop, forced colors met stippelranden en focus, en geen beweging bij reduced motion. Dit is
  alleen in emulatie gemeten.
- e2e strikt **109/109**, nu ook met axe = 0 op alle impacts als eis.
- vitest **55/55**.

**Open:**
- De schermstructuur van het Overzicht zet de status niet eerst. Dat is een bewuste regel van deze ronde (A-1).
- Fase 2d (inline styles) is nog niet gedaan.
- Niet gevalideerd: echte telefoon, schermlezer en een echt contrastthema.

---

## 1. Werkwijze

1. **Echte app met testdata.** Er is een eigen kloon van HEAD gemaakt en daaruit zijn de core-, mocks- en
   web-images gebouwd (onder de machinebrede docker-build-lock). De teststack is gestart met
   `tests/stack/run.sh` (UP_ONLY + E2E_PREPARE, zoals `make e2e`): beheerder, koppelingen naar de mocks, één back-up, wizard afgerond, modus Alleen-lezen. Na elke
   reparatie is het web-image opnieuw gebouwd en is alleen de web-container vervangen. Er is dus altijd tegen de
   productieserver (`server.mjs`) gemeten, niet tegen `next dev`.
2. **Eigen meetscript, los van de bouwstap geschreven:** `docs/screenshots/peil/review/review.mjs`. Het volgt de
   Peil-controle (`definitief/bouw/controle.cjs`). Gebruikt zijn Playwright 1.65 en axe-core 4.14 uit `.lokaal`,
   niet uit de app. Per scherm × breedte × thema wordt gemeten:
   - axe met de tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` en `wcag22aa`, **alle impacts**. De e2e-suite
     telde tot deze stap alleen serious/critical (nu aangescherpt, commit `426b49b`).
   - contrast in de gerenderde DOM: achtergrondlagen, opacity en kleuren in `background-image`. Tekst moet
     ≥ 4,5:1 halen (groot ≥ 3:1); daarnaast is geteld hoeveel tekst onder het Peil-doel van 7:1 blijft.
   - UI-grenzen ≥ 3:1: velden, schakelaars, stippelranden, actieve tab en focusring.
   - horizontale overloop ten opzichte van de apparaatbreedte (ook onder `isMobile`).
   - aanraakdoelen < 44 px, tekst < 14 px, ellips op inhoud, één h1, kopsprongen, dubbele id's en kapotte
     aria-verwijzingen.
   - Peil-semantiek: groen buiten een gemeten Gezond, statusbadges zonder icoon of woord, en verboden woorden uit
     §11.1.

   Toetsenbord: een volledige Tab-doorloop op 390 en 1440 px, waarbij voor elke tabstop gemeten is of er een ring
   is, of die ≥ 3 px is, wat het contrast is en of hij bedekt is.
3. **Matrix.** 16 hoofdschermen × 320/360/390/768/1024/1440 × licht/donker. Daarnaast:
   - 12 tabbladen (320/390/1440);
   - de 16 schermen in Beheermodus (390/1440, met meer actieve bedieningen);
   - 22 toestanden en dialogen, waarvan 8 nagebootst door een API-antwoord te onderscheppen, zoals in stap 2/3;
   - een Tab-doorloop van 16 schermen × 390/1440 × 2 thema's.

   Er zijn afdrukken gemaakt op 360/390/768/1440 (schermen) en 390/1440 (toestanden); 320 en 1024 zijn alleen
   gemeten.
4. **Bekeken en vergeleken met de prototypes** (`definitief/shots/home-*`, `appdetail-*`, `problemen-*`): alle
   schermen op 360 px, plus een selectie op 390/768/1440 in licht en donker. Lange afdrukken zijn daarvoor in kolommen
   naast elkaar gezet.
5. **Toegestane toevoegingen** getest via de echte API, zonder onderschepping: `toevoegingen.mjs`.
6. **In emulatie** (`extra.mjs`): 200 %-zoom (640 CSS-px), forced colors (Chromium `forcedColors: active`) en
   `prefers-reduced-motion`.
7. Daarna is alles opnieuw gemeten op de gerepareerde HEAD. De e2e-suite (strikt, 109) en vitest zijn gedraaid.

De metingen van vóór de reparatie staan in `review/metingen-voor-reparatie.json`. Die zijn met de eerste versie
van het script gemaakt. Twee meetfouten zijn daarna in het script verbeterd:
- legendablokjes van een grafiek telden als "groen buiten Gezond" (36×);
- een verloop in de achtergrond van de inlogzijbalk werd meegeteld, terwijl de merkknop er dekkend overheen ligt
  (4× een vals tekstcontrast).

In de tabel hieronder zijn die vals-positieven apart vermeld.

---

## 2. Uitkomst in cijfers

Alle groepen: vóór = HEAD `c3dc933`, na = HEAD `e7e88e2` (inloggen, eerste start en laden opnieuw gemeten op
`8571054`). Aantallen zijn meldingen opgeteld over alle metingen van de groep. Tussen haakjes staan
vals-positieven van de eerste scriptversie (§1).

| Meting | Schermen (192) | Tabs (72) | Beheermodus (64) | Toestanden (82) |
|---|---|---|---|---|
| axe-overtredingen, alle impacts | 0 → **0** | 0 → **0** | 0 → **0** | 2 → **0** |
| tekst < 4,5:1 (groot < 3:1), gerenderd | 0 → **0** | 0 → **0** | 0 → **0** | (4) → **0** |
| laagste tekstcontrast | 6,62 → **7,61** | 8,63 → **8,63** | 6,62 → **7,61** | 4,56 → **4,56** ¹ |
| tekst onder het Peil-doel 7:1 (excl. verouderd/uitgeschakeld) | 6 → **0** | 0 → **0** | 2 → **0** | 14 → **0** |
| UI-grens < 3:1 (velden, schakelaars, stippels, actieve tab) | 0 → **0** | 0 → **0** | 0 → **0** | 0 → **0** |
| horizontale overloop (> 1 px t.o.v. apparaatbreedte) | 2 → **0** | 6 → **0** | 0 → **0** | 0 → **0** |
| aanraakdoel < 44 px | 0 → **0** | 0 → **0** | 0 → **0** | 0 → **0** |
| tekst < 14 px | 0 → **0** | 0 → **0** | 0 → **0** | 0 → **0** |
| ellips op inhoud | 64 → **0** | 16 → **0** | 32 → **0** | 48 → **0** |
| groen buiten gemeten Gezond | (36) → **0** | 0 → **0** | (12) → **0** | (12) → **0** |
| statusbadge zonder icoon | 24 → **0** | 0 → **0** | 8 → **0** | 24 → **0** |
| verboden woorden (§11.1) | 60 → **12** ² | 132 → **0** | 20 → **4** ² | 58 → **0** |
| h1 ≠ 1 / kopsprong / dubbele id / kapotte aria | 0 / 0 / 0 / 0 → **0** | 0 → **0** | 0 → **0** | 4 / 0 / 0 / 0 → **0** |
| JavaScript-fouten | 0 → **0** | 0 → **0** | 0 → **0** | 0 → **0** |

¹ De laatst-bekende waarde op de arcering (`--stale-ink`), bewust gedempt; Peil-eis ≥ 4,5:1 (donker 5,01:1).
² Alleen het logboek: de opgeslagen audittekst "Test Prowlarr: Verstoord" (O-3).

**Toetsenbord** (16 schermen × 390/1440 × licht/donker): 64 volledige Tab-doorlopen, 2 234 → **2 460** tabstops.
Het aantal stops steeg doordat afgekapte tekst nu volledig in beeld staat en scrollende tabellen een stop
krijgen. Uitkomst na de reparaties:
- **0** tabstops zonder ring of met een ring < 3 px;
- **0** met ringcontrast < 3:1 (laagste 7,55:1);
- **0** volledig bedekt door de kop- of ondernavigatie;
- de eerste tabstop is overal "Naar inhoud".

**Emulatie** (`extra.json`):
- 200 %-zoom (640 CSS-px): 16 van 16 schermen zonder overloop;
- forced colors: alle statusbadges houden een rand, Onbekend en Verouderd blijven gestippeld, de focus is 3 px
  solid;
- reduced motion: geen animatie of transitie > 20 ms, ook niet in de geopende Meer-sheet.

Er zijn nog twee horizontale scrollgebieden over, allebei datatabellen (WCAG 1.4.10 staat dat toe) en allebei met
een tabstop en een naam:
- de tabeltweeling van de oorzaakverdeling (11 kolommen) onder 1024 px;
- de vergelijkingstabel in de dialoog Vergelijken op 390 px.

44 vinkjes zijn native (accent-color primary). Hun rand komt van de browser; die is niet apart gemeten.

---

## 3. Bevindingen en reparaties

Ernst: **H** = hoog (WCAG-fout of Peil-kernregel), **M** = middel (Peil-regel, merkbaar), **L** = laag (afwerking).

| # | Bevinding (vóór, HEAD `c3dc933`) | Waar / gemeten | Norm | Ernst | Reparatie | Commit |
|---|---|---|---|---|---|---|
| R-01 | Horizontale overloop op 320 px: de bevindingskop (diagnose + "Zekerheid" + balk) liep niet om. | Torrentclient 320 licht/donker, 12 px | WCAG 1.4.10, Peil §6 | H | `.evidence-head` loopt om | `5a1f0ae` |
| R-02 | Horizontale overloop op 320 px door instellingssleutels in mono (`usenet.min_articles_for_recommendation`). De vaste ondernavigatie werd mee verbreed: "Meer" stond tot 66 px buiten beeld. | Instellingen → Usenet/Torrent/Automatisering, 24–66 px | WCAG 1.4.10 | H | `.setting-key` breekt waar nodig (Peil §5: codes breken, nooit afkappen) | `5a1f0ae` |
| R-03 | De torrenttabel op 1440 px was 50 px te breed. De actieknoppen ("Opnieuw aankondigen", "Recheck") vielen half buiten de kaart; je moest horizontaal scrollen voor kerninformatie. | Downloads → Torrent, 1134 → 1184 px | Peil §12 A-06 | M | Trackerhost (mono) in tabelcellen breekt waar nodig; de tabel past (1134 px) | `5a1f0ae` |
| R-04 | Ellips op inhoud: uitleg en titels werden op 768–1440 px afgekapt ("De naam wijst op een anime-indexer, maar…", back-upnamen, taken, aanbevelingen). | NZB/Torrent-indexers, Systeem, Overzicht, Automatisering: 160 meldingen (schermen 64, tabs 16, Beheermodus 32, toestanden 48) | Peil §5/§12 "geen ellips op inhoud" | M | `.list-title`/`.list-sub`/`.media-*`/`.job-file` lopen op elke breedte om | `5a1f0ae` |
| R-05 | Ondernavigatie: vijf vaste kolommen van 64 px, waardoor labels midden in een woord afbraken: "Downlo-ads", "Ontbre-kend", "Overzicht" op 320–390 px. | alle schermen < 1024 px (afdrukken) | leesbaarheid, Peil §6 | M | Items op tekstbreedte, alleen afbreken op lettergrepen. Gemeten: op 320 px past alles op één regel | `5a1f0ae` |
| R-06 | Een statuspil die omloopt ("Tracker onbereikbaar of foutmelding") werd door de ronde rand afgesneden. | Downloads → Torrent 1440 | leesbaarheid | L | Radius 1rem (op één regel nog steeds volledig rond) | `5a1f0ae` |
| R-07 | "Privé" stond als amber pil **zonder icoon** (`data-status="degraded"`): status alleen in kleur en op de verkeerde as. | Zoeken, NZB/Torrent-indexers: 24× | Peil §4.1 r3/r9, A-04, WCAG 1.4.1 | H | Info met slot, zoals bij Downloads | `caa240e` |
| R-08 | Amber (de "Beperkt"-vorm) voor zaken die geen gezondheid zijn. Voorbeelden: "Staat nog uit", "Automatisering niet toegestaan", "Prijs ontbreekt", "ETA onbetrouwbaar", "Verouderd", "Beheermodus", "Herstart nodig", "Op pauze", "geen prijs ingesteld", "onvolledig", "Verlopen", "Loopt nog", plus de meldingen voor verouderde gegevens, onbekende diagnose, rol en modus. | 24 plekken in de code (één daarvan, de formulierfout bij Zoeken, werd juist rood: een veldfout) | Peil §4.1 regel 3 en 12 | H | Neutraal of slot (voorwaarde/rol), Onbekend (stippel + circle-help), nieuwe toon **Verouderd** (stippel + arcering + clock-alert), Gepauzeerd, info (ADR-0008 punt 1) | `caa240e`, `c5ff0a8` |
| R-09 | circle-check buiten Gezond (9 plekken: kandidaat voldoet, compat aanwezig, preventie actief, stap klaar, …), CircleX en triangle-alert bij Storing. | code-audit + afdrukken | Peil §7 vaste koppeling | M | Gewoon vinkje; Storing altijd octagon-x; een afgewezen kandidaat is neutraal met x | `caa240e` |
| R-10 | Ernst van afstemmingsbevindingen stond als gezondheidspil met "Conflict", "Let op" of "Info". | NZB/Torrent-indexers → Afstemming met MediaGuard | Peil T-20, §11.1 ("Let op" mag niet) | M | **ErnstLabel** (linkerbalk 6 px): Hoog, Middel, Info | `caa240e` |
| R-11 | De statusstip in de zijbalk (totaalstatus en problemen) en in de regel "Synchronisaties" verschilde per status alleen in kleur. | zijbalk ≥ 1024 px, Overzicht | Peil §1.3 "nooit alleen kleur" | M | Het Peil-icoon in de statuskleur (woord ernaast) | `caa240e` |
| R-12 | Verboden woorden in beeld: "OK" (KPI "Laatste zoekopdracht OK", 22× in de takenlijst), "Verstoord" (Overzicht → Aandachtspunten, Systeem naast de badge), "Let op". | woordscan op 410 metingen: 270 treffers (waarvan 132× "OK" in de takenlijst) | Peil §11.1 | M | "Laatste zoekopdracht geslaagd", "Niets mislukt (24 u)", het Peil-woord uit de statuscode, en geen los statuswoord meer naast een badge. **Logboek: open** (O-3) | `caa240e` |
| R-13 | De gemiddelde betrouwbaarheid zonder metingen toonde "—". | NZB/Torrent-indexers | Peil §4.4 (nooit "—" of 0) | L | Onbekend met reden: "Nog te weinig metingen (…); we tonen geen schatting." | `caa240e` |
| R-14 | Kosten → "Totaal per maand € 0,00" leek compleet, terwijl 3 diensten geen prijs hadden. Het Overzicht zei wél "Deels onbekend". | Kosten | Peil §1.1, §4.3, WCAG 3.2.4 | M | "Deels onbekend" ook op Kosten | `caa240e` |
| R-15 | Tekst in de icoonkleur van een status (`.t-warning` `#8F4B00`, 6,62:1). Dat haalt AA, maar niet het Peil-doel van 7:1. | Downloads → Torrent ("Private torrent zonder seedregel") | Peil §3.2 (`-ink` voor tekst) | L | `.t-warning`/`.t-danger` in de `-ink`-variant | `5a1f0ae` |
| R-16 | De offlinepagina (core onbereikbaar) had geen h1 en geen main. | toestand offline, 4× | Peil A-02, WCAG 1.3.1/2.4.6 | M | StateView `pageLevel`: de titel wordt h1 binnen `<main>` | `5a1f0ae` |
| R-17 | De grafiektabel "Ontbrekend en mislukt per oorzaak" (11 kolommen) scrolt op 390 px, maar was niet met het toetsenbord te bereiken. | axe `scrollable-region-focusable` (serious), 2× | WCAG 2.1.1 | H | Alleen als hij scrolt: tabstop + `role=region` + naam (ADR-0008 punt 8) | `5a1f0ae` |
| R-18 | Grafiek "Downloads in '*' per dag": geen basislijn en geen daglabels, en Overig zonder arcering. | Usenet-providers | Peil §10, dataviz | M | Basislijn (haarlijn), eerste en laatste dag als label, Overig gearceerd (staaf en legenda) | `3089bda` |
| R-19 | Native vinkjes waren 13 px (header-index, kwaliteitseisen). | Usenet-providers, Ontbrekend | Peil §6 (20 px) | L | Elk vinkje en keuzerondje is 20 px | `5a1f0ae` |
| R-20 | In forced colors (emulatie) waren de gekozen periode, het actieve tabblad en het huidige navigatie-item niet van de rest te onderscheiden. | forced-colors-afdrukken | Peil A-18, WCAG 1.4.11 | M | Rand of onderstreping in `Highlight` | `5d54743` |
| R-21 | Een pictogram in lopende tekst stond op een eigen regel ("✓" boven "Geen knelpunten gevonden"). Tailwind-preflight maakt svg block. Op de koppelingskaart stond ook "Gecontroleerd 8 minuten geleden" in mono. | Torrentclient; NZB/Torrent-indexers, Torrentclient | Peil §5 (mono alleen voor codes) | L | `.ico` inline-block; op de koppelingskaart staan alleen id en versie in mono | `e7e88e2` |
| R-22 | Op inloggen en eerste start lag een decoratief verloop (accent- en grafiekkleur) achter de tekst. De secundaire tekst zakte daardoor in donker naar 6,78:1. | toestanden inloggen/eerste-start, donker 1440 | Peil §1.5, §3.2 (twee tekstniveaus ≥ 7:1), §4.1 r8 | L | Zijkolom effen `--card`; laagste tekst nu ≥ 8,34:1 | `8571054` |
| R-23 | Een skelet kondigde "Laden…" aan (sr-only). | toestand laden, 8 treffers per afdruk | Peil §11.1 ("Laden…" mag niet) | L | "Gegevens ophalen…" (patroon uit §11.2) | `8571054` |

Niet als fout geteld, wel bekeken:
- **Gehaald** (doel) en **Werkt** (servertest) zijn groen: dat is gemeten en goed.
- De overgebleven amber meldingen zijn per stuk beoordeeld: buiten de grenzen, weinig back-upruimte, firewalled, server flappert, indexer uitgeschakeld of onbetrouwbaar volgens Prowlarr, nog geen back-up, publiek adres niet ingesteld. Bij de eerste zes is het gemeten gezondheid of een echt risico; zie O-6 voor het laatste geval.
- In de Tab-doorloop lagen tabstops voor een deel onder een andere laag (124 vóór, 128 na; zie §7). WCAG 2.4.11 vraagt alleen "niet volledig bedekt"; volledig bedekt waren 0 tabstops.

---

## 4. Vergelijking met de Peil-prototypes (design-critique)

**Eerste indruk.** MSO leest nu als een Peil-app. Het gaat om de koele neutrale basis, kaarten met een rand in
plaats van een schaduw, het systeemfont, de tweekleurige focusring en de vijf assen met elk een eigen vorm (pil,
hoekige R-tag, ErnstLabel, stippel/arcering). Het donkere thema heeft dezelfde opbouw als `home-donker`.

**Wat klopt** (gezien in de afdrukken en gemeten):
- tokens en kleuren; groen komt alleen voor bij Gezond, Gehaald (gemeten doel) en Werkt (servertest);
- R0/R1/R2 als treden-tag met woord;
- elk bedienbaar element is ≥ 44 px en alle tekst is ≥ 14 px;
- tabellen worden een kaartenlijst zodra hun kaart smal is;
- tabs lopen om;
- verouderd heeft een stippelrand, arcering en "laatst bekend";
- Onbekend heeft altijd een reden.

**Afwijkingen ten opzichte van de prototypes:**

| # | Wat | Waarom (nog) zo | Advies |
|---|---|---|---|
| A-1 | **Status niet eerst op het Overzicht.** Op 360/390 px staat de totaalstatus (kaart Gezondheid) pas na Doelen, Wacht op jou en Wat MSO deed, rond 1 300 px scrollen (Peil §1.2, prototype `home`: SysteemStatusPaneel bovenaan). | Vaste regel van deze ronde: de schermstructuur blijft. | Bij een functionele ronde een SysteemStatusPaneel bovenaan het Overzicht (totaal = slechtste onderdeel, telling, rijen met reden). |
| A-2 | **KPI-tegels zonder Bron + Bijgewerkt per tegel** (StatKaart C-05, Peil §1.6). | Dat is een inhoudelijke toevoeging, geen uiterlijk. | Meenemen met A-1. |
| A-3 | **Zekerheid als getal + inktbalk** ("0,92 ━━") in plaats van een ZekerheidLabel met drie stippen (T-24). | Kleur is wel inkt, nooit groen (fase 1). Het woord ontbreekt; het getal komt uit de API. | ZekerheidLabel met een vaste indeling (zeker/waarschijnlijk/onzeker). Die indeling bestaat in MSO nog niet; daarvoor is een besluit van de API-bouwer nodig. |
| A-4 | **Dichtheid.** Er staat meer tekst per kaart dan in het prototype, bijvoorbeeld Doelen met netto/bruto en uitleg. | Dit zijn domeinteksten van MSO; die blijven. | Geen. De hiërarchie (titel 18 px, uitleg 14–16 px) is goed. |
| A-5 | **Vervolglinks** zijn tekstlinks ("Systeem →", 44 px) in plaats van volle-breedte-secundaire knoppen. | Bestaande componentkeuze in fase 2. | Geen, of samen met A-1. |

---

## 5. Toegestane toevoegingen

Getest met `toevoegingen.mjs` tegen de echte API. Uitkomst in `toevoegingen.json`: **23 van 23 controles
geslaagd**.

**"Terug naar de Hub" (O-11)**
- Zonder instelling staat er geen link in de zijbalk (1440) en geen link in de Meer-sheet (390).
- Zes ongeldige adressen worden door de **core** geweigerd met HTTP 422: `localhost`, `127.0.0.1`, `ftp://`, een
  pad, gebruiker:wachtwoord en "geen-url". Daarna staat er nog steeds geen link.
- Het geldige adres `http://hub.thuis.test:30080` (een gereserveerde `.test`-naam, geen echt adres) geeft HTTP 200.
  Daarna:
  - staat de link "Terug naar de Hub" in de zijbalk, 231 × 44 px;
  - wijst `href` precies naar het adres en opent de link in hetzelfde tabblad (de weg terug, geen deep link);
  - is de link in de Meer-sheet het eerste item, 362 × 44 px.
- Een lege waarde geeft HTTP 200 en de link verdwijnt.
- Aan het eind is de oorspronkelijke (lege) waarde teruggezet.

**Tabelweergave en toetsenbord bij grafieken**
- Oorzaakverdeling (Ontbrekend): "Toon als tabel" wordt "Verberg tabel". De tabel heeft 10 rijen, 11 kolomkoppen
  (`th scope=col`) en het bijschrift "Ontbrekend en mislukt per oorzaak (categorie en bron)", op 390 en 1440. Op
  390 px is de tabel met het toetsenbord te scrollen (R-17).
- Downloads in '*' per dag: tabel met 14 rijen en 4 kolommen. In het plotvak leest Home "27 okt 2026: 0 van 0 in
  '*'", leest End "9 nov 2026: 0 van 6 in '*'", gaat → één dag verder en wist Esc de uitlezing (de `aria-live`-regel).
  Op 390 en 1440.
- De onzekerheidsbalk (Kosten) en de doel-trendlijn (Overzicht) komen in de testdata niet in beeld (§6). Die zijn
  dus niet in de echte app getest; hun kader en tabel delen wel de code met de twee geteste grafieken.

---

## 6. Grafieken (dataviz-procedure, Peil §10)

| Grafiek | Plek | Vorm | Oordeel |
|---|---|---|---|
| Oorzaakverdeling (segmentbalk) | Ontbrekend → Source Baseline | dunne segmentbalk + legenda + tabel | Goed. Kleur volgt de oorzaak (chart-1…5), Overig is grijs met arcering, Onbekend gestippeld en er zit 2 px vlak tussen de segmenten. De tabeltweeling heeft 11 kolommen; op 390 px scrolt die. **Gerepareerd:** de tabel is nu met het toetsenbord te scrollen (R-17). |
| Downloads in '*' per dag (staven) | Usenet-providers → SABnzbd-categorieën | gestapelde staven, één y-as | **Gerepareerd (R-18):** zonder as en daglabels was niet te zien welke staaf bij welke dag hoort. Nu is er een basislijn als haarlijn, de eerste en laatste dag staan als label, en Overig heeft arcering. Toetsenbord ←/→/Home/End/Esc leest per dag voor en de tabel heeft 14 rijen. |
| Onzekerheidsbalk (opzeg-simulatie) | Kosten → Simulatie | segmentbalk met geschat-arcering + tabel | Verschijnt pas na een simulatie met providerdata. In de testdata kwam hij niet in beeld; alleen code en unit-test. **Niet visueel gevalideerd.** |
| Doel-trendlijn | Overzicht → Doelen | lijn, gat bij ontbrekende meting | Verschijnt vanaf twee dagmetingen. De testdata heeft er één. **Niet visueel gevalideerd** (dat gold al in stap 3). |

Palet: Peil chart-1…5. De validator gaf al in stap 2 PASS in licht en donker. In de grafieken komt geen
statuskleur voor; het meetscript vond geen groen buiten Gezond (legendablokjes uitgezonderd, zie §1).

---

## 7. Toetsenbord en focus

- **Skiplink** "Naar inhoud" is overal de eerste tabstop (64 van 64 doorlopen).
- **Ring:** op elke tabstop de Peil-ring van 3 px met een halo van 2 px. Het laagste contrast tegen de achtergrond is
  7,55:1 (eis ≥ 3:1).
- **Bedekt:** geen enkele tabstop valt volledig weg achter de kopbalk of de ondernavigatie; scroll-padding werkt.
  128 tabstops liggen gedeeltelijk onder een andere laag:
  - de skiplink zelf (64×), die bij focus over de kopbalk valt;
  - de menuknop (32×) en de merklink (30×), die direct na de skiplink de focus krijgen terwijl die er nog ligt;
  - 2× het hoge scrollgebied van een tabel op Ontbrekend (390 px): de bovenkant is zichtbaar, het midden ligt achter
    de ondernavigatie.

  WCAG 2.4.11 (AA) is daarmee gehaald.
- **Bedieningen.** Getest in e2e (`peil.spec`, `dialogs.spec`) en in `toevoegingen.mjs`:
  - het plotvak van een grafiek met ←/→/Home/End/Esc;
  - de pauzedialoog: Esc sluit hem en de focus keert terug naar de knop;
  - het veld in de dialoog krijgt de focus.

  Wat in de componenten zit (stap 3) maar in deze review **niet apart** is getest: tabs met ←/→/Home/End, en
  scrollen met de pijltjes in een tabel die de focus heeft.
- **Forced colors (emulatie):** focus `Highlight` 3 px. De selectie (segment, tab en navigatie) is zichtbaar
  gemaakt (R-20).

---

## 8. Wat NIET gevalideerd is

- **Echte Android-telefoon in fel licht**, **schermlezer** (NVDA, TalkBack) en een **echt Windows-contrastthema**:
  hier niet uit te voeren. Forced colors, 200 %-zoom en reduced motion zijn alleen in Chromium-emulatie gemeten.
  Dat is "Sim-getest", geen acceptatie (08_UI).
- De **doel-trendlijn** en de **onzekerheidsbalk** zijn niet in beeld geweest met (test)data (§6).
- De **contrastmetingen** zijn in de browser gedaan, niet in de PNG's: die staan op een palet van 256 kleuren.
- **Echte NAS**: niet van toepassing op deze UI-ronde. De Excel-statussen blijven Bezig.

---

## 9. Open punten en vervolg

| # | Wat | Waarom open | Voorstel |
|---|---|---|---|
| O-1 | Status niet eerst op het Overzicht (A-1). | De schermstructuur blijft in deze ronde. | Een functionele ronde voor een SysteemStatusPaneel. |
| O-2 | KPI-tegels zonder bron en tijd per tegel (A-2). | Inhoud, geen uiterlijk. | Samen met O-1. |
| O-3 | Het logboek toont de opgeslagen audittekst letterlijk, met oude woorden: "Test Prowlarr: Verstoord". | De tekst komt uit de core (`CONNECTOR_STATUS_LABELS`, `_STATUS_NL`). Een vastgelegd verslag herschrijven we in de weergave niet (ADR-0008 punt 7). | In de core de woorden Beperkt/Storing gebruiken. Dat verandert API-teksten en hoort dus in een functionele ronde. |
| O-4 | Fase 2d: 569 inline `style={{…}}`, 24× `var(--mg-*)` in TSX, en `mg.css`/`mso.css`/de aliaslaag staan er nog. | Mechanisch werk zonder zichtbaar verschil (ADR-0007 punt 7). | Vervolg uit stap 3. |
| O-5 | Doel-trendlijn en onzekerheidsbalk niet visueel gevalideerd. | Ze komen niet voor in de testdata. | Een mock met twee dagmetingen en een simulatie met providerprijzen. |
| O-6 | Meldingen die op de grens liggen en nog amber zijn, bijvoorbeeld "Stel de publieke URL van MSO in" (een voorwaarde, geen gezondheid). | Per melding een inhoudelijk oordeel; niet in één keer omgezet. | Bij de volgende ontwerpronde naar neutraal met slot (gate) als het een voorwaarde is. |
| O-7 | Zekerheid als getal + balk in plaats van een ZekerheidLabel (A-3). | Daarvoor is een indeling (zeker/waarschijnlijk/onzeker) uit de API nodig. | Besluit van de API-bouwer. |
| O-8 | DiffViewer, Stepper en Tijdlijn hebben alleen de fase-1-correcties (uit stap 3). | — | Omzetten naar DiffWeergave C-20, Wizard-stepper C-36 en Incidenttijdlijn C-25. |
| O-9 | Tabs (←/→/Home/End) en scrollen met de pijltjes in een gefocuste tabel zijn in deze review niet apart getest. De axe-eis "0 op alle impacts" staat sinds `426b49b` wel in de e2e. | Buiten de tijd van deze stap. | Een e2e-test voor de tabtoetsen en voor scrollen in een tabelregio. |
| O-10 | Echte telefoon, schermlezer en een echt contrastthema (§8). | Niet uit te voeren op deze machine. | Acceptatie volgens 08_UI. |

---

## 10. Opnieuw draaien

Stack zoals `make e2e` (in een kloon; `run.sh` overschrijft getrackte resultaten):

```sh
MSO_STACK_ADMIN_PW=… MSO_STACK_UP_ONLY=1 MSO_STACK_E2E_PREPARE=1 tests/stack/run.sh
export MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD="$MSO_STACK_ADMIN_PW"
node docs/screenshots/peil/review/review.mjs alles      # schermen, tabs, beheer, toestanden, toetsenbord
node docs/screenshots/peil/review/toevoegingen.mjs      # Hub-link via de echte API, tabel + toetsenbord per grafiek
node docs/screenshots/peil/review/extra.mjs             # 200 %-zoom, forced colors, reduced motion (emulatie)
python3 docs/screenshots/peil/review/verklein.py        # PNG's naar 256 kleuren
make down
```
