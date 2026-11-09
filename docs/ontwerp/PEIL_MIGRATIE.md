# MSO-web-UI naar Peil — omzettingsontwerp (stap 2)

**Status:** ontwerp (stap 2) **uitgevoerd in stap 3** — zie §12 hieronder, ADR-0007 en
[`docs/screenshots/peil/na/`](../screenshots/peil/na/README.md) · ontwerp 9 november 2026 · design-agent · code-HEAD bij
het ontwerp `b581fbb` (MSO 1.6.0)
**Ontwerptaal:** Peil **1.1.0**, versie-gepinde kopie in [`design/ontwerptaal/`](../../design/ontwerptaal/HERKOMST.md)
(bindend: `ONTWERPTAAL.md`, `COMPONENTEN.md`, `ADR-ONTWERPTAAL.md`; MSO-pad: ONTWERPTAAL §13.3 en §13.4)
**Besluiten:** [`docs/adr/ADR-0006-peil-omzetting-interpretaties.md`](../adr/ADR-0006-peil-omzetting-interpretaties.md)
**Nulmeting:** [`docs/screenshots/peil/voor/`](../screenshots/peil/voor/README.md) (250 afdrukken, `metingen.json`,
`toetsenbord.json`)
**Contract:** 1.2 §13 (reikwijdte: alleen het uiterlijk van MSO; functies, routes, gebruikersreizen en API's blijven).

Gebruikt: `design:design-critique` (§5), `design:accessibility-review` (§4, §5.4), `design:design-system` (§6),
`design:ux-copy` (§6.7), `design:design-handoff` (§6, §8, §9) en `dataviz` (§7; de paletvalidator is gedraaid).

---

## 0. Samenvatting

MSO is een kopie van de MediaGuard-stijl (`mg.css` van commit `e92c2acc`, 794 regels, + `mso.css`) met 736 inline
`style={{…}}` en geen enkele Tailwind-hulpklasse. De UI is informatierijk en eerlijk van toon, maar wijkt op vier
punten hard af van Peil:

1. **Groen betekent alles.** Op de 16 schermen (1440, licht) staan **163 groene chips** tegen 24 amber en 11 rood:
   "Verbonden", "Beschikbaar", "Gelukt", "Seeden", "Geen knelpunt", "Automatisch veilig" (een risiconiveau),
   "Ingeschakeld"-achtige capaciteiten en zelfs gewone getallen ("0"). Peil: groen = alleen *gemeten en goed*.
2. **Onbekend ziet eruit als pauze.** Elke neutrale chip krijgt het `Pause`-icoon (`TONE_ICON.neutral`), ook
   "Onbekend (lijn niet ingesteld)". De KPI-tegels tonen bij laden én bij een fout "— Nog niet beschikbaar" (het
   Peil-woord voor een *fase*). Overzicht toont na een mislukte verversing de oude cijfers **zonder** verouderd-markering;
   een lege lijst zegt "Wachtrij is leeg" terwijl de verversing mislukt is.
3. **Te klein en te smal.** 641 van de 678 bedienbare elementen (95 %) zijn kleiner dan 44 px op 1440; op 360 px
   400 van de 502 (80 %). 2 932 tekstelementen zijn kleiner dan 14 px (11–13 px; op 360 zelfs 10 px). **6 van de 16 schermen
   lopen horizontaal over** op 360/390 px (tot 93 px; de eerste-startwizard 116 px) — de bestaande e2e-test ziet dat
   niet (§10.1). In Instellingen → Bedrijfsmodus wordt een uitleg op 360 px letter voor letter onder elkaar gezet.
4. **Focus en grafieken.** De focusring is één kleur, 2 px; op 390 px verdwijnt de focus tot 4× per scherm achter de
   vaste ondernavigatie (WCAG 2.4.11). De vier grafieken gebruiken statuskleuren als reeks (amber = "in '*'", rood =
   "zeker getroffen", groen = doellijn), het categorische palet faalt de validator, tooltips werken alleen met de muis
   en er is geen tabelweergave.

Wat goed is en blijft: status heeft al icoon + woord, `capability_unavailable` en "verbinding niet ingesteld" zijn
eerlijk (nooit nepdata), de skiplink is de eerste tabstop, dialogen houden de focus vast, axe vindt op de schermen maar
één echte fout (profielknop, WCAG 2.5.3).

**Aanpak in twee fasen** (§8): **fase 1** = alleen CSS en woorden — de gedeelde aliaslaag
`aliaslagen/mediaguard-mso.css` + een kleine MSO-eigen correctielaag, plus de Peil-woorden en -iconen in de bestaande
componenten; **fase 2** = Tailwind 4 + shadcn/ui (new-york, React 19), de componenten één voor één, de twee toegestane
toevoegingen ("Terug naar de Hub", tabelweergave + toetsenbord bij elke grafiek) en tot slot het opruimen van `mg.css`,
inline styles en Inter/JetBrains Mono. OpenAPI blijft byte-gelijk (sha256 `bb551ee1…471b`), `data-testid`'s en rollen
blijven, poorten en installatie veranderen niet.

---

## 1. Vaste regels voor deze omzetting

| Regel | Gevolg voor MSO |
|---|---|
| Alleen uiterlijk (contract 1.2 §13, ONTWERPTAAL §13) | Geen nieuwe routes, geen andere API-aanroepen, geen datamodel- of migratiewijziging. Gebruikersreizen (zelfde schermen, tabs, dialogen, stappen) blijven. |
| OpenAPI byte-gelijk | `docs/openapi/media-source-optimizer.openapi.json` (sha256 `bb551ee1cd14df00c6724038d8564b26748be235bcd5c0121bbadc368ee0471b`) moet na elke stap gelijk zijn. Ook de Hub-link (§9.1) gebruikt alleen het bestaande `GET/PUT /api/v1/settings` (respons zonder schema). |
| `data-testid`'s en rollen blijven | Alle 60 testids (o.a. `mode-chip`, `pause-button`, `pause-confirm`, `theme-toggle`, `sidebar-health`, `waiting-pill(-mobile)`, `connector-<id>`, `goal-<key>`) en de rollen/namen uit de e2e (`alertdialog`, knop "Terugzetten", groep "Bedrijfsmodus kiezen", `#danger-confirm`, kop "Inloggen"). |
| Bestaande integriteit/rollback/monitoring niet aanraken | DangerDialog-bevestigingen, change-set-preview/goedkeuren/terugdraaien, back-up/herstel, noodrem en healthchecks houden hun gedrag; alleen hun uiterlijk wordt Peil. |
| Toegestane toevoegingen (besluiten gebruiker) | O-11 **"Terug naar de Hub"** (adres uit de configuratie, verborgen als onbekend) en **tabelweergave + toetsenbordbediening bij elke grafiek**. Verder niets. De themaknop (O-09) is my_house; MSO heeft al Systeem/Licht/Donker. |
| Taal | MSO is **alleen Nederlands** (`lib/i18n` nl-NL; PROVENANCE: "Dutch-only"). De en-US-eis geldt voor MediaGuard, niet voor MSO. |
| Android | MSO heeft **geen Android-app** (alleen `apps/core`, `apps/mocks`, `apps/web`). De Android-reikwijdte uit de besluiten geldt voor MediaGuard en my_house. |
| Geen versie-ophoging | Geen tag of release; CHANGELOG onder "Unreleased". |

---

## 2. Werkwijze en bewijs

- **Echte app, echte testdata.** Teststack uit `tests/stack/run.sh` (images `core`, `mocks`, `web` uit HEAD gebouwd
  onder `docker-build.lock`; stack onder `stack.lock`), voorbereid zoals `make e2e`. Daarna opgeruimd (`make down`,
  kloon en containers weg).
- **Afdrukken:** `docs/screenshots/peil/voor/maak-afdrukken.mjs` (Playwright 1.65): 16 schermen × 360/390/768/1440 ×
  licht/donker (volledige pagina), 12 tabs × 390/1440 × 2, 19 toestanden × 390/1440 × 2 (+ Meer-lade op 360/390,
  sneltoetsen alleen 1440). Acht toestanden zijn **nagebootst** door één API-antwoord in de browser te onderscheppen
  (fout, offline, laden, niet ingesteld, gepauzeerd, beheermodus, eerste start, verouderd); zie de README.
- **Nulmeting in de browser** per afdruk (`metingen.json`): overloop t.o.v. de apparaatbreedte, doelen < 44 px, tekst
  < 14 px, inline styles, chips per toon, grafiekelementen, axe-core 4.14 met `wcag2a, wcag2aa, wcag21a, wcag21aa,
  wcag22aa` (de Peil-set; de huidige e2e laat `wcag21a` weg).
- **Tab-doorloop** (`toetsenbord.json`): 40 Tab-stops op 4 schermen × 390/1440 × 2 thema's.
- **Palet:** dataviz-validator (`validate_palette.js`) op het huidige `--mg-viz-*` en op Peil `chart-1…5`.
- **Niet gevalideerd:** echte telefoon, echte schermlezer, 200 %-zoom en forced colors op MSO zelf (Peil heeft die op
  de prototypes gemeten, niet op MSO).

---

## 3. Inventaris: schermen, onderdelen en toestanden

### 3.1 Schil (`components/shell/app-shell.tsx`, `nav.ts`, `waiting.tsx`)

| Onderdeel | Nu | Afdruk |
|---|---|---|
| Zijbalk (> 860 px) | logo "MSO" (verloop) + "Media Source Optimizer · Bronnen & downloads"; **15 items in 6 groepen** (Zoeken & bronnen · Usenet · Torrents · Operaties · Integraties · Systeem), groepskoppen in kapitalen 11 px; teller "Wacht op jou" op Automatisering; voet: gezondheid (stip + "Gedeeltelijk verstoord" + max. 3 problemen) en versie | `schermen/*-1440` |
| Kopbalk | zoekveld (`/`), pil "1 wacht op jou" (amber rand), moduschip "Alleen-lezen/Beheermodus/Autopilot" (`mode-chip`), themaknop (cyclus Donker → Licht → Systeem), knop "Automatisering pauzeren" (amber, `pause-button`), profielknop met initialen | idem |
| Telefoon (≤ 860 px) | menuknop, zoekveld, iconen; ondernavigatie **Overzicht · Zoeken · Downloads · Ontbrekend · Meer**; Meer = lade van links met de volledige navigatie | `toestanden/meer-lade-*` |
| Banners | Gepauzeerd (amber, `pause-banner`), noodrem/hold (`hold-banner`) | `toestanden/gepauzeerd-*` |
| Overig | sneltoetsen (`?`, `g o` …) in een dialoog; profielmenu met thema-keuze en Uitloggen | `toestanden/sneltoetsen-*`, `profielmenu-*` |

### 3.2 Schermen

| Route (h1) | Opbouw (kaarten/onderdelen) | Grafieken/meters | Afdrukken |
|---|---|---|---|
| `/` Overzicht | Doelen (3 doelkaarten met status, trend, verklaring) · Wacht op jou · Wat MSO deed (24 uur/7 dagen) · 4 KPI-tegels (Gezondheid, Actieve downloads, Mislukt 7 d, Kosten per maand) · Foutlocaties · Koppelingen · Overige aanbevelingen · Open wijzigingen · Systeem & MediaGuard · Faaloorzaken (tabel) · Recente activiteit | doel-trendlijn (SVG) per doel | `schermen/overzicht-*` |
| `/zoeken` Zoeken | zoekformulier (term, Categorie- en Protocol-segmenten, seizoen/aflevering/absoluut, sortering, media-ID's) · resultaten met bronrapport, vergelijken (lade), versturen (dialoog) | zekerheidsbalkje per resultaat | `schermen/zoeken-*`, `toestanden/zoekresultaten-*`, `vergelijken-*` |
| `/nzb-indexers`, `/torrent-indexers` | Prowlarr-paneel, eigen bronnen/endpoint, indexertabel (betrouwbaarheid, bereik, status), direct toevoegen (dialoog) | betrouwbaarheidsbalkje | `schermen/nzb-indexers-*`, `torrent-indexers-*` |
| `/usenet-providers` | Providers en blokaccounts · Autopilot servers · Beschikbaarheid en overlap (tabel, periode) · Artikeltest · Serverprioriteit in SABnzbd · SABnzbd-categorieën (diagnose '*', staafgrafiek 14 dagen, instantietabel, koppeltabel met selects) · Eigen header-index (formulier) | **staafgrafiek "Downloads in '*' per dag"**, meters (blok, header-index) | `schermen/usenet-providers-*` |
| `/torrentclient` | Preventie uitvoerbare bestanden · qBittorrent (versie, mogelijkheden) · Verbinding, poort & VPN · Knelpunten · Tuning · Veilige snelheidslimieten · Wachtrijbeleid · Seedregels-editor | zekerheidsbalkje | `schermen/torrentclient-*` |
| `/opslag` Opslag & seeding | KPI-tegels, reservemeter, seedverplichtingen per torrent, cross-seed | meters (reserve, ratio/tijd) | `schermen/opslag-*` |
| `/downloads` | tabs Usenet (SABnzbd) / Torrent (qBittorrent): wachtrij + geschiedenis met oorzaak; torrentlijst met filters, voortgang, diagnose | voortgangsbalken | `schermen/downloads-*`, `tabs/downloads-torrent-*` |
| `/ontbrekend` Ontbrekend & herstel | Source Baseline (verdeling per oorzaak; per categorie en per bron) · Ontbrekende items (filters, paginering, detailpaneel) · Mislukte downloads → vervanger · Blacklist | **gestapelde verdelingsbalken** (9 oorzaken), dekkingsbalkje | `schermen/ontbrekend-*` |
| `/mediaguard` MediaGuard-koppeling | Verbinding · Least privilege (authmethode) · Koppeling instellen (connectorformulier) · Zoekquotum · Synchronisatie · Compatibiliteitsmatrix (route × rol × doel × broncode × installatie) · zoekverzoeken | quotummeters | `schermen/mediaguard-*` |
| `/kosten` Kosten & besparing | periode-segment · uitleg · 4 KPI-tegels · Diensten (tabel, prijs invullen, analyseren) · analyse/simulatie · Scenario's · Verlengingen | **onzekerheidsbalk** (simulatie) | `schermen/kosten-*`, `toestanden/fout-*` |
| `/automatisering` | "Drie niveaus" (risicouitleg) · 7 tabs: Aanbevelingen · Wijzigingen (lijst + voorvertoning/diff) · Controle · Veiligheid · Samenvatting · Automatisch toepassen · AI-adviseur | zekerheidsbalkje | `schermen/automatisering-*`, `tabs/automatisering-*`, `toestanden/pauze-dialoog-*` |
| `/automatisering/voetafdruk` | wat MSO veranderde en of het nog actief is; overschrijven | — | `schermen/voetafdruk-*` |
| `/logs` Logs & audit | filter, auditlijst, technische details | — | `schermen/logs-*` |
| `/instellingen` | 14 tabs (12 categorieën uit de backend + Verbindingen + Gebruikers); Bedrijfsmodus-kaart; schema-gestuurde velden (`setting-field.tsx`) met risico, standaard, slot, herstart | — | `schermen/instellingen-*`, `tabs/instellingen-*` |
| `/systeem` Back-up & updates | MSO-core · Onderdelen (gezondheid) · Databasemigraties · Automatische taken · Back-ups (maken, terugzetten met getypte bevestiging) · Onderhoud · Taken | back-upvoortgang, taakvoortgang | `schermen/systeem-*`, `toestanden/terugzet-dialoog-*` |
| `/login`, `/setup` | inlogformulier met verkooppunten links; wizard Beheerder → Verbindingen → Bedrijfsmodus → Samenvatting | — | `toestanden/inloggen-*`, `eerste-start-*` |
| `/<onbekend>` | niet-gevonden-pagina in de schil | — | `toestanden/niet-gevonden-*` |

### 3.3 Gedeelde componenten (bron → wat ze nu doen)

| Bestand | Componenten |
|---|---|
| `components/mg/primitives.tsx` | `StatusChip` (6 tonen; neutraal = `Pause`-icoon), `LockedChip`, `Dot`, `Card` (+ `tone`), `PageHeader` (+ kruimel `›`), `Button` (5 varianten, `sm`, `iconOnly`, `pending`), `LinkButton`, `Switch`, `Segmented`, `Tabs` (links of `role=tablist`), `Field`, `InlineState`, `Progress`, `Skeleton`, `Kbd`, `Modal` (portal, focusval) |
| `components/mg/states.tsx` | `StateView` (loading/empty/error/offline/unavailable/forbidden/not_found), `ErrorState` (correlation-id als tekst), `ResourceView` (laden → fout → leeg → data; stale-markering alleen als er data is en de lijst niet leeg is) |
| `components/mg/domain.tsx` | `KpiCard` (+ `Sparkline`, ongebruikt), `ConnectorStatusChip/Logo/Card`, `Confidence`, `EvidenceCard`, `Trace`, `DiffViewer` (`role=table`), `DistributionBar`, `Stepper`, `Timeline`, `DangerDialog` (getypte bevestiging), `PendingAction` |
| `components/mg/setting-field.tsx`, `toast.tsx` | instellingsveld (risico, slot, herstart, standaard), toasts |
| `components/mso/risk-chip.tsx` | `RiskChip`: advies (info) · automatisch_veilig (**groen**) · toestemming_nodig (amber) |
| `components/mso/unavailable-state.tsx`, `mode-gate.tsx`, `change-preview.tsx`, `mode-settings.tsx` | niet-ingesteld-toestand, schrijfpoort per modus, wijzigingsvoorvertoning (risico, diff, impact, gevaar, goedkeuren/toepassen/terugdraaien), bedrijfsmodus |
| `lib/status.ts` | `healthTone/Label`, `JOB_TONE` (done = groen), `CHANGE_STATUS` (applied = groen), `enumLabel`, Nederlandse taaknamen |

### 3.4 Toestanden

| Toestand | Nu | Afdruk |
|---|---|---|
| Laden | skeletregels; KPI-tegels tonen ondertussen **"— Nog niet beschikbaar"** | `toestanden/laden-*` |
| Leeg | icoon `Inbox` + titel + uitleg ("Wachtrij is leeg") | in de schermen |
| Fout | `CircleX`-icoon, "Er ging iets mis", "Correlatie-ID: …" (niet kopieerbaar, geen code), Opnieuw proberen; KPI-tegels: "— Nog niet beschikbaar" | `toestanden/fout-*` |
| Niet ingesteld | `PlugZap`, "Verbinding niet ingesteld" + link naar Instellingen → Verbindingen | `toestanden/niet-ingesteld-*` |
| Niet beschikbaar (404 op een optionele route) | "Nog niet beschikbaar" + endpoint | (code) |
| Offline (core weg) | volledig scherm "MSO-core is niet bereikbaar" + Opnieuw proberen | `toestanden/offline-*` |
| Verouderd | alleen een grijze regel "Gegevens 30 seconden geleden — laatste verversing mislukt" (11,5 px); **niet** op Overzicht en **niet** bij een lege lijst | `toestanden/verouderd-*`, `verouderd-overzicht-*`, `verouderd-leeg-*` |
| Geen toegang | `Ban` + backendmelding | (code) |
| Gepauzeerd | amber banner + amber pauzeknop ingedrukt + chip "Autopilot gepauzeerd" | `toestanden/gepauzeerd-*` |
| Alleen-lezen / beheer / Autopilot | moduschip (beheer: amber rand; autopilot: blauw); schrijfknoppen `disabled` met `title` | `toestanden/beheermodus-*` |
| Dialogen | pauzeren (`alertdialog`), terugzetten met getypte naam, terugdraaien, versturen, direct toevoegen, dienst bewerken, header-index, seedregels, sneltoetsen | `toestanden/*-dialoog-*` |

### 3.5 Grafieken en meters

| Grafiek | Plaats | Nu |
|---|---|---|
| Doel-trendlijn (`TrendLine`) | Overzicht → Doelen | SVG 280×56; lijn in **statuskleur** van het doel (groen/rood/blauw); doellijn **groen gestippeld**; gereconstrueerde punten op 40 % opacity; tooltip alleen via `<title>`; `role=img` met samenvatting |
| Downloads in '*' per dag (`StarDaysChart`) | Usenet-providers → SABnzbd-categorieën | 14 staven: totaal in `neutral-tint` met daaroverheen "in '*'" in **amber (warning)**; legenda 10 px-blokjes; `<title>` per staaf |
| Verdeling per oorzaak (`DistributionBar`) | Ontbrekend → Source Baseline (totaal, per categorie, per bron) | gestapelde balk, **9 oorzaken** op `viz-1…6` + **danger** + grijs + rand; legenda met aantallen |
| Onzekerheidsbalk (`UncertaintyBar`) | Kosten → simulatie | zeker getroffen = **rood**, onzeker = **amber** gearceerd, rest = vlak; legenda met aantallen |
| Meters (`Progress`, 10×) | Opslag, MediaGuard-quotum, header-index, blok, servertest, downloads, torrents, back-up, taken | vulling in accent of statuskleur; **100 % = groen** |
| Zekerheid/betrouwbaarheid (`Confidence`, 6×) | zoekresultaten, aanbevelingen, torrentbevinding, baseline-dekking, indexers | getal + balkje in **groen/amber/rood** (inline style) |
| `Sparkline` | `KpiCard` | ongebruikt (geen enkele `spark=`) |

---

## 4. Nulmeting (uit `metingen.json` en `toetsenbord.json`)

| Scherm | Overloop 360 / 390 (px) | Doelen < 44 px (360 · 1440) | Tekst < 14 px, elementen (360 · 1440) | Inline styles | Chips groen/amber/rood/info/neutraal (1440) |
|---|---|---|---|---|---|
| automatisering | 0 / 0 | 26/31 · 41/42 | 45 · 72 | 37 | 1/1/0/3/2 |
| downloads | **93 / 63** | 13/18 · 28/29 | 79 · 112 | 60 | 1/0/10/0/0 |
| instellingen | **39 / 9** | 50/55 · 65/66 | 155 · 182 | 49 | 2/0/0/11/6 |
| kosten | 0 / 0 | 26/32 · 41/43 | 69 · 104 | 38 | 0/3/0/0/0 |
| logs | 0 / 0 | 32/38 · 47/49 | 139 · 171 | 58 | 0/0/0/0/0 |
| mediaguard | 0 / 0 | 20/26 · 35/37 | 273 · 305 | 118 | **45**/0/0/0/11 |
| nzb-indexers | 0 / 0 | 28/33 · 43/44 | 115 · 147 | 76 | 1/4/0/1/6 |
| ontbrekend | 0 / 0 | 33/41 · 48/52 | 181 · 218 | 115 | 2/0/0/2/0 |
| opslag | 0 / 0 | 9/16 · 24/27 | 54 · 88 | 34 | 3/0/0/1/2 |
| overzicht | 0 / 0 | 21/33 · 36/44 | 171 · 201 | 59 | 11/3/0/3/8 |
| systeem | 0 / 0 | 20/30 · 35/41 | 509 · 563 | 194 | **68**/1/0/0/10 |
| torrent-indexers | **38 / 8** | 28/33 · 43/44 | 120 · 152 | 78 | 0/6/1/1/7 |
| torrentclient | **56 / 27** | 23/29 · 38/40 | 251 · 281 | 74 | 15/2/0/4/9 |
| usenet-providers | **41 / 10** | 36/42 · 52/53 | 180 · 231 | 94 | 14/4/0/0/6 |
| voetafdruk | 0 / 0 | 10/15 · 25/26 | 21 · 48 | 18 | 0/0/0/0/4 |
| zoeken | **62 / 32** | 25/30 · 40/41 | 30 · 57 | 25 | 0/0/0/0/0 |

- **Overloop elders:** eerste-startwizard 116 px (390), zoekresultaten en vergelijken 32 px, lege SABnzbd-wachtrij
  63 px, Automatisering → Automatisch toepassen 35 px (alle 390). Op 768 en 1440: 0. Oorzaken: chips met
  `white-space: nowrap` (lange oorzaaklabels), `Segmented` met vaste breedte (Categorie, Bedrijfsmodus), horizontale
  stepper, tabellen met `overflow-x: auto` binnen een te brede kaart.
- **Tekstmaten (1440, alle schermen):** 11 px 361× · 11,5 px 489× · 12 px 817× · 12,5 px 258× · 13 px 1 007×. Op 360
  ook 10 px (16×). Bronnen: `mg.css` 89 declaraties < 14 px, inline `fontSize` 257× (12: 95, 12,5: 62, 13: 60, 11,5: 32,
  11: 5).
- **axe-core** (Peil-tags): op elk scherm 1× `label-content-name-mismatch` (serious): de profielknop toont "ST" maar
  heet "Profiel" (WCAG 2.5.3). In zoekresultaten en vergelijken daarnaast 3× `aria-prohibited-attr` (`aria-label` op de
  `span.qribbon`). Inloggen, eerste start en offline: 0. Geen JavaScript-fouten.
- **Toetsenbord:** skiplink "Naar inhoud" is overal de eerste stop. Focusstijl: `outline 2px` in één kleur
  (`--mg-focus`); invoervelden krijgen een 3 px schaduw zonder outline. Op 390 px valt de focus **achter de vaste
  ondernavigatie**: Overzicht 1×, Ontbrekend 3× (o.a. een "Tabel (horizontaal scrollbaar)"-regio), Instellingen 4×
  (o.a. twee invoervelden). Op 1440: 0×.
- **Palet (dataviz-validator):** huidig `--mg-viz-1…6` licht: **FAIL** chroma (`#0E7F8C`), CVD **WARN** 7,6;
  donker: **FAIL** lichtheidsband (alle 6 te licht), CVD **WARN** 7,2. Peil `chart-1…5`: licht en donker **PASS** op
  alle vijf controles (CVD aangrenzend 8,8 / 8,4; normaal zicht 17,7 / 19,3; alle ≥ 3:1 op de kaart).

---

## 5. Kritiek op de huidige stand (design-critique)

**Eerste indruk.** Een rustige, professionele beheer-UI met veel uitleg in gewone taal. Het oog gaat op Overzicht
naar het grote "83,3 %" en naar de vele kleine chips; groen domineert, waardoor de enige echte afwijking (Prowlarr
beperkt, 5 mislukt) niet opvalt. Op de telefoon is alles een lange kolom kleine tekst.

### 5.1 Bruikbaarheid

| Bevinding | Ernst | Aanbeveling (Peil) |
|---|---|---|
| Groen voor "Verbonden", "Beschikbaar", "Gelukt", "Seeden", capaciteiten, "Automatisch veilig" en gewone getallen | 🔴 | groen alleen voor gemeten-en-goed (StatusBadge `healthy`); de rest neutraal met icoon + woord (§6.2) |
| Onbekend/uit/stil met het pauze-icoon; fase-woord "Nog niet beschikbaar" bij laden en fouten | 🔴 | `circle-help` + stippelpil + reden; laden = skelet; fout = Foutpaneel; "Nog niet beschikbaar" alleen voor `hourglass`-fase |
| Verouderde data stil op Overzicht en bij lege lijsten | 🔴 | Versheid-patroon: kaartrand gestippeld, "Laatst bekend · 16:19", status Onbekend, bediening uit (gebruikt `resource.stale`/`fetchedAt`, geen API-wijziging) |
| 6 schermen lopen over op de telefoon; Bedrijfsmodus-uitleg letter voor letter | 🔴 | chips en segmenten laten omlopen, tabellen → kaartenlijst < 768, `dl` één kolom < 640 |
| Focus achter de ondernavigatie | 🟡 | `scroll-padding` (Peil-basislaag) en de Peil-ring |
| Fout zonder code en zonder kopieerbare trace | 🟡 | Foutpaneel: wat + waarom + wat je kunt doen, `code`, KopieerVeld "Trace-ID" (waarde = `correlation_id`) |
| "Kosten per maand: € 0,00" terwijl 3 diensten geen prijs hebben | 🟡 | "Deels onbekend — 3 diensten zonder prijs" naast het bekende bedrag; nooit een bedrag dat volledig lijkt |
| Lange titels afgekapt met ellips (wijzigingslijst, aanbevelingen) | 🟢 | omlopen; geen ellips op inhoud |
| Uitgeschakelde knoppen met opacity .5 en de reden alleen in `title` | 🟡 | Peil: `aria-disabled`, gestippelde rand, reden zichtbaar ernaast (`aria-describedby`) |

### 5.2 Visuele hiërarchie

- **Wat eerst opvalt:** het getal 83,3 % en de groene chips; het zou de totaalstatus moeten zijn ("Beperkt — Prowlarr")
  en "Wacht op jou". Peil: PageHeader met bijgewerkt + Vernieuwen → SysteemStatusPaneel → "Wat actie nodig heeft" →
  kaartraster.
- **Leesvolgorde:** goed op desktop (raster), te lang op de telefoon (alles uitgeklapt, 5 000–17 000 px). Uitleg mag
  achter `details` ("Hoe lees je dit?") zonder informatie weg te halen.
- **Nadruk:** kapitalen-labels (11 px, letterspatiëring) en drie grijsniveaus maken de hiërarchie fijnmazig maar
  zwak. Peil: twee tekstniveaus, kleinste tekst 14 px, sectiekop `text-xl`.

### 5.3 Consistentie

| Element | Probleem | Peil |
|---|---|---|
| Statuswoorden | "Verstoord" (MSO), "Offline", "Gedeeltelijk verstoord", "Core offline", "Status onbekend" naast "Onbekend" | vaste woorden §11.1: Gezond · Beperkt · Storing · Onbekend (+ reden) · Verouderd · Gepauzeerd; totaal: Alles gezond · Beperkt · Storing · Deels onbekend |
| Risico | drie MSO-niveaus in drie statuskleuren | RisicoBadge R0/R1/R2 (hoekig, treden-glyph, vaste woorden) |
| Typografie | 11–13 px in 9 maten, inline | Tailwind-schaal; `text-sm` (14) is het minimum |
| Kleur in code | 59× `var(--mg-…)` inline, 1 hex (`themeColor`) | alleen tokens via utilities; lintregel tegen hex |
| Breekpunt | 860 px (MG) | `lg` = 1024 px (platform) |

### 5.4 Toegankelijkheid (WCAG 2.1 AA, accessibility-review)

| # | Bevinding | Criterium | Ernst | Maatregel |
|---|---|---|---|---|
| 1 | Horizontale overloop op 360/390 (6 schermen + wizard) | 1.4.10 Reflow | 🔴 | §6.6 per scherm; test meet t.o.v. de apparaatbreedte (§10) |
| 2 | 95 % (1440) / 80 % (360) van de doelen < 44 px | 2.5.5 (platformeis 44 px) | 🟡 | fase 1 alleen telefoon (aliaslaag), fase 2 overal `min-h-touch` |
| 3 | Focus bedekt door vaste ondernavigatie | 2.4.11 (2.2) | 🟡 | `scroll-padding` in fase 1 |
| 4 | Profielknop: zichtbare "ST" niet in de naam | 2.5.3 | 🟡 | initialen + sr-only "profiel en thema" in de knoptekst (geen afwijkende `aria-label`) |
| 5 | `aria-label` op `span.qribbon` | 4.1.2 | 🟢 | sr-only tekst in plaats van `aria-label` |
| 6 | Grafiektooltip alleen met de muis, geen tabel | 2.1.1, 1.1.1 | 🟡 | §7 |
| 7 | Status als kleur-alleen in grafieken en balkjes (amber/rood als reeks) | 1.4.1 | 🟡 | §7 (vorm + woord + tabel) |
| 8 | Wit op `danger` in donker (3,18:1) bij `.btn--danger-solid` | 1.4.3 | 🟡 | aliaslaag (`destructive`/`-foreground`: 9,7 / 7,6:1) |
| 9 | Tekst 10–13 px | (Peil ≥ 14 px; leesbaarheid) | 🟡 | fase 2 |
| 10 | Uitgeschakelde knop: reden alleen in `title`, opacity | 1.4.3 / 3.3.2 | 🟢 | Peil `aria-disabled` + zichtbare reden |

**Wat al goed werkt:** skiplink, landmarks (`aside`, `header`, `main`), één h1 per scherm, dialogen met focusval en
Escape, status nooit alleen kleur in chips (icoon + woord), `role=img` + samenvatting bij grafieken, "geen
voorbeelddata"-belofte in elke onbeschikbare toestand, het bronrapport bij zoeken.

### 5.5 Wat goed is en blijft

- De eerlijke toon ("geen besparingsbelofte", "MSO wijzigt niets zelf", "alleen gemeten op artikelen die jij echt
  hebt aangevraagd") — dat is precies Peil-principe 1. **Teksten met domeinbetekenis blijven.**
- De informatiedichtheid op desktop (tabellen) — Peil haalt dichtheid uit compacte tabellen, niet uit kleine tekst.
- Change-set-voorvertoning met diff, risico, impact en terugdraaien — wordt ActiePreview/DiffWeergave-stijl.

---

## 6. Vertaling naar Peil

### 6.1 Tokens

**Fase 1** gebruikt de gedeelde, gegenereerde aliaslaag `design/ontwerptaal/aliaslagen/mediaguard-mso.css` (niet met de
hand wijzigen). Die zet alle `--mg-*` op Peil (`--mg-bg → --background`, `--mg-surface → --card`, `--mg-text-2/3 →
--muted-foreground`, `--mg-accent → --primary`, `--mg-success/warning/danger/info → healthy/degraded/down/info`,
`--mg-viz-1…6 → chart-1…5 + chart-other`, `--mg-lane-* → domain-lane-*`, `--mg-font/mono → font-sans/mono`,
radii, schaduwen, `--mg-fs-11…13 → 14 px`) en bevat correcties voor gevaarknop, tellers, stepper, 44 px op de
telefoon, noodstop/pauze, diff, zekerheid, kwaliteitslabels, lanes en fasebalken.

**MSO-eigen correcties** komen in een nieuw, repo-eigen bestand `apps/web/styles/peil-mso.css` (ná de aliaslaag), omdat
ze in de gedeelde laag niet thuishoren of daar niet werken:

| # | Correctie | Waarom |
|---|---|---|
| a | `:focus-visible { outline: var(--focus-width) solid var(--ring); outline-offset: var(--focus-offset); box-shadow: 0 0 0 var(--focus-offset) var(--ring-halo) }` | Peils ring staat in `@layer base`; de ongelaagde `mg.css`-regel (2 px, één kleur) wint daar altijd van. Geldt ook voor MediaGuard → melden bij de ontwerptaal (ADR-0006 punt 6). |
| b | `html { scroll-padding-top: calc(var(--mg-topbar-h) + .75rem) }` en < 861 px `scroll-padding-bottom: calc(var(--mg-bottomnav-h) + env(safe-area-inset-bottom) + .75rem)` | focus niet achter kop/ondernavigatie (2.4.11); Peils eigen regel rekent met 64 px en `lg` |
| c | `.chip, .tag { white-space: normal; height: auto; min-height: 1.5rem }`; `.segmented { flex-wrap: wrap }`; < 48rem: `.tabs { flex-wrap: wrap; overflow: visible }` | overloop op 360/390 en tabs die buiten beeld vallen |
| d | < 40rem: `.kv { grid-template-columns: minmax(0,1fr) }` | Bedrijfsmodus letter voor letter |
| e | `.confidence .bar > span { background: var(--foreground) !important }` | de aliasregel verliest van de inline `style.background` (groen/amber/rood); zekerheid is inkt (T-24) |
| f | `.kpi--success .kpi-value, .kpi--warning .kpi-value, .kpi--danger .kpi-value { color: var(--foreground) }` | een getal is nooit in statuskleur; de status komt als badge (fase 1b) |
| g | `.btn:disabled, .btn[aria-disabled="true"], .estop:disabled { opacity: 1; border-style: dashed; background: var(--muted); color: var(--disabled-foreground) }` | geen opacity op tekst (Peil §4.1 regel 10) |
| h | `.stale-mark { color: var(--stale-ink); font-size: .875rem; border: 2px dashed var(--stale); background-image: repeating-linear-gradient(135deg, var(--stale-hatch) 0 2px, transparent 2px 6px); border-radius: 999px; padding: .125rem .5rem }` | verouderd herkenbaar zonder componentwijziging |
| i | `.estop--pause` en `.banner--warning[data-testid="pause-banner"]` → `paused`-tokens | pauze = Gepauzeerd (info), niet amber; de aliaslaag dekt de knop, niet de banner |
| j | `globals.css`: het eigen `:root { --mg-font… }`-blok weg | systeemfont (O-01); Inter/JetBrains Mono blijven tot fase 2d geladen maar ongebruikt |

**Fase 2** importeert `ontwerptaal.css` als Tailwind-bron; componenten gebruiken alleen Peil-utilities
(`bg-card`, `text-muted-foreground`, `border-border-strong`, `text-degraded-ink bg-degraded-bg border-degraded`,
`bg-risk-2-solid`, `arcering`, `cijfers`, `min-h-touch`, `z-nav` …). `--color-*: initial` voorkomt losse kleuren.

### 6.2 Statusvocabulaire (bron → Peil)

| Bron in MSO | Waarde | Nu | Peil (vorm · icoon · woord) |
|---|---|---|---|
| `healthTone` (gezondheid, connectoren, onderdelen) | healthy/ok/online/connected | groen "Gezond" | `healthy` · `circle-check` · **Gezond** (alleen voor gemeten gezondheid) |
| | degraded/warning/slow | amber "Verstoord" | `degraded` · `triangle-alert` · **Beperkt** |
| | offline/error/failed/unreachable/auth_failed | rood "Offline" | `down` · `octagon-x` · **Storing** (+ reden, bv. "Authenticatie mislukt") |
| | onbekend/leeg | grijs + **Pause** "Onbekend" | `unknown` · `circle-help` · stippelpil · **Onbekend** + reden |
| | not_configured / disabled | grijs + Pause | `unlinked` · `link-2-off` · **Niet gekoppeld** / neutraal label zonder statusicoon **Uitgeschakeld** (geen gezondheid) |
| Zijbalkvoet totaal | healthy/degraded/offline/fout | "Alle systemen gezond", "Gedeeltelijk verstoord", "Core offline", "Status onbekend" | **Alles gezond** · **Beperkt** · **Storing** · **Deels onbekend** (slechtste onderdeel; fetchfout = Onbekend — "MSO-core reageert niet", nooit "Storing" zonder meting) |
| Backend-tekst in `message` ("Gezond", "Verstoord", "Offline") | — | getoond als reden | UI toont het Peil-woord uit `status`; `message` alleen als reden als het geen kaal statuswoord is (backend blijft ongewijzigd, ADR-0006 punt 3) |
| `JOB_TONE` (taken) | queued / running / done / failed / cancelled / retry_scheduled | grijs+Pause / blauw draaiend / **groen** "Geslaagd" / rood / grijs / amber | neutraal `clock` **In wachtrij** · neutraal `loader-circle` (statisch bij reduced motion) **Bezig** · neutraal `check` **Afgerond** · `down` `octagon-x` **Mislukt** · neutraal `x` **Geannuleerd** · neutraal `rotate-ccw` **Opnieuw gepland** |
| Taak "Staat stil" (schema) | — | grijs + Pause | neutraal `hourglass` **Wacht op voorwaarde** + reden (bv. "Wacht op koppeling of staat uit") — geen pauze-icoon voor iets dat niet bewust gepauzeerd is |
| `CHANGE_STATUS` | preview / approved / applying / applied / rolled_back / failed | info / accent / amber / **groen** / grijs / rood | info `file-diff` **Voorvertoning** · `approval` `clipboard-check` **Goedgekeurd** · neutraal `loader-circle` **Wordt toegepast** · neutraal `check` **Toegepast** (Peil-woord "Uitgevoerd en gecontroleerd" pas als de MSO-controle "Behouden" is) · neutraal `rotate-ccw` **Teruggedraaid** · `down` **Mislukt** |
| `RiskChip` | advies / automatisch_veilig / toestemming_nodig | info / **groen** / amber | RisicoBadge **R0 · Alleen lezen** / **R1 · Omkeerbaar** / **R2 · Goedkeuring nodig** (ONTWERPTAAL §13.4); de MSO-naam blijft als uitleg ("MSO-niveau: automatisch veilig — mag automatisch binnen limiet, controle en terugdraaien") |
| `change.irreversible` / `disables` | — | rood | RisicoBadge-tekst in de preview: **Onomkeerbaar** (`risk-3`-rand, `octagon-alert`) — MSO voert dit nooit automatisch uit (bestaande tekst) |
| Doelstatus (Overzicht, `goals-card.tsx`) | met / not_met / insufficient / unavailable | groen / rood / grijs `circle-help` / **amber** `triangle-alert` | **Gehaald** = `healthy` (gemeten en goed) · **Niet gehaald** = `down` · **Te weinig data** = `unknown` + reden · **Niet beschikbaar** = `circle-slash`, onbekend-vorm + bron (niet amber: er is niets beperkt) |
| Doelrichting | better / worse / same | groen / rood / grijs `=` | neutraal `trending-up` **Beter dan bij de start** · `trending-down` **Slechter dan bij de start** · `equal` **Gelijk aan de start** — een richting is geen gezondheid |
| Downloadgeschiedenis | Gelukt / Mislukt | groen / rood | neutraal `check` **Voltooid** · `down` **Mislukt**; oorzaak als neutraal label (`tag`), niet als rode chip |
| Torrentstatus | Downloaden / Seeden / Vastgelopen / Bestanden ontbreken / In wachtrij | blauw / **groen** / amber / rood / grijs+Pause | neutraal `arrow-down` **Downloaden** · neutraal `arrow-up` **Seeden** · `degraded` **Vastgelopen** · `down` **Bestanden ontbreken** · neutraal `clock` **In wachtrij** |
| Diagnose "Geen knelpunt", capaciteiten, "Bereikbaar: Ja", "Noodstop: Uit", "Geverifieerd/Aanwezig" | — | groen | DekkingsMatrix-cel: `check` **ja** / `x` **nee** / `circle-help` **onbekend** / `minus` **n.v.t.** — neutraal, geen statuskleur |
| "Verbonden" (qBittorrent, MediaGuard) | — | groen | neutrale pil `link-2` **Gekoppeld** (verbonden is geen gezondheid, Peil §4.3) |
| `Confidence` / betrouwbaarheid | 0..1 | groen/amber/rood balkje | getal blijft (domeindata) + balkje in `foreground`; onder de bestaande drempel een korte uitleg met `triangle-alert` **Lage betrouwbaarheid** (woord, niet alleen kleur) |
| "Laag vertrouwen" (beschikbaarheid) | — | amber chip | ZekerheidLabel **Onzeker** (stippen) — zekerheid, geen gezondheid |
| Modus | read_only / manage / autopilot / onbekend | grijs / amber rand / blauw | neutrale chip `eye` **Alleen-lezen** · neutraal `shield-check` **Beheermodus** · neutraal `bot` **Autopilot**; **Modus onbekend** = `unknown` (schrijven uit) |
| Gepauzeerd | paused | amber knop + banner | PausedBar (`paused-bg`, `circle-pause`): **Gepauzeerd** — "Automatisering gepauzeerd. Er worden geen nieuwe zoekopdrachten of automatische wijzigingen gestart." (bestaande tekst) |
| Tellers | "1" op Automatisering, "1 wacht op jou" | amber/rood | inktpil (neutraal), naam in de toegankelijke tekst |

### 6.3 Componenten → shadcn/ui (fase 2) met behoud van API

| MSO-component | shadcn / Peil-component | Behouden (API, testids, hookklassen) | Opmerkingen |
|---|---|---|---|
| `StatusChip` | Badge-basis → **StatusBadge** (C-03) en neutraal **Label** | props `tone/icon/pending/size`; klasse `.chip` blijft op het element (e2e en CSS-hooks) | `tone` wordt via §6.2 vertaald; `success` alleen nog voor gezondheid; `pending` = `loader-circle` |
| `RiskChip` | **RisicoBadge** (C-08) | export en props gelijk; `data-risico` toegevoegd | "Waarom R2?" als `details` in de wijzigingsvoorvertoning (niet als tooltip) |
| `Button`, `LinkButton` | Button (cva `primair/secundair/stil/gevaar/link`, `standaard/klein/icoon`) | klasse `.btn`, `pending` → `aria-busy`, label blijft | alle maten ≥ 44 px; uitgeschakeld = `aria-disabled` + reden |
| `Card`, `PageHeader` | Card / PageHeader (C-02) | `aria-labelledby`, `id` | kruimel met `chevron-right` (44 px) |
| `KpiCard` | **StatKaart** (C-05) | `href/drill` → één vervolglink | + Bron + Bijgewerkt (`fetchedAt`) + meetkwaliteit; toestanden laden/verouderd/onbekend/fout |
| `StateView`, `ErrorState`, `ResourceView` | Leeg/Onbekend/Laden (C-17/18), **Foutpaneel** (C-14), **KopieerVeld** (C-15), CapabilityUnavailable (C-12) | klasse `.state--error` en `data-state` blijven (screens.spec); `role` blijft | stale ook bij lege lijsten; Overzicht krijgt `ResourceView`-gedrag voor verouderd |
| `UnavailableState` | NietGekoppeld (T-12a) | link naar Instellingen → Verbindingen | `link-2-off` |
| `Modal` | Dialog / **AlertDialog** | portal, focusval, `data-autofocus`, `role="alertdialog"` | `z-overlay`; mobiel als volledige-hoogte sheet |
| `DangerDialog` | AlertDialog + getypte bevestiging (stijl GoedkeuringsPagina stap 2) | `#danger-confirm`, knopnaam (bv. "Terugzetten") | blijft een dialoog (ADR-0006 punt 2); knop `gevaar` `aria-disabled` tot de naam klopt |
| `DiffViewer` | **DiffWeergave** (C-20) | `role=table`, kolommen | tekens `~ + −`, Was/Wordt, geen rood/groen |
| `Stepper` (wizard, back-up) | Wizard-stepper (C-36) | `aria-current="step"` | < lg alleen bollen + "Stap n van 4" |
| `Timeline`, `Trace` | Incidenttijdlijn (C-25) | — | tijd in `cijfers` |
| `Tabs` | Tabs (§21 COMPONENTEN) | `role=tablist/tab`, pijltjes; link-tabs met `aria-current` | < 768 omlopende knoppen; Home/End toevoegen |
| `Segmented` | ToggleGroup (`aria-pressed`) | `role=group` + `aria-label` (e2e: "Bedrijfsmodus kiezen") | omlopen, 44 px |
| `Switch` | Switch | `role=switch`, `aria-checked`, label | 44 px raakvlak |
| `Field`, inputs, `setting-field` | Label/Input/Select/Textarea + veldfout | `htmlFor`/id's | veldfout `octagon-x` + `aria-invalid` + `aria-describedby`; bron-tag (standaard/ingesteld) |
| `Progress` | Progress / **Meter** (C-32) | `role=progressbar` (voortgang) of `role=meter` (verhouding tot grens) | §7 |
| `Skeleton` | Skeleton | klasse `.skeleton` blijft (screens.spec wacht op 0) | geen puls bij reduced motion |
| `Toast` | Sonner | — | `z-toast`; succes zegt wat er gebeurde |
| Zijbalk, kopbalk, ondernavigatie, lade | AppSchil (C-01) zonder Hub-specifieke items | `aside[aria-label]`, `sidebar-health`, `waiting-pill(-mobile)`, `mode-chip`, `theme-toggle`, `pause-button` | §6.6 Schil |
| `ConnectorCard/StatusChip/Logo` | Card + StatusBadge + logo | `connector-<id>` | logo blijft merkuitzondering (`aria-hidden`) |
| `Confidence` | Meter-klein in inkt | — | §6.2 |
| `Kbd` | Kbd | — | `text-sm` |

### 6.4 Iconen (lucide-react, al aanwezig)

`Pause` als neutraal standaardicoon verdwijnt; vaste koppeling volgens ONTWERPTAAL §7: Gezond `circle-check` · Beperkt
`triangle-alert` · Storing `octagon-x` (was `CircleX`) · Onbekend `circle-help` · Verouderd `clock-alert` ·
Gepauzeerd `circle-pause` · Niet gekoppeld `link-2-off` · Gekoppeld `link-2` · Niet beschikbaar `circle-slash` · Nog
niet beschikbaar `hourglass` · Back-up `database-backup` · Terugzetten `rotate-ccw` (was `Undo2`) · Diff `file-diff` ·
Kopiëren `copy` · Deep link `square-arrow-out-up-right`. Navigatie-iconen van MSO blijven (eigen schermen). Maat 20 px
in knoppen/titels, 18 px in badges, 24 px in de ondernavigatie.

### 6.5 Typografie, ruimte en vorm

- Systeemfont en systeem-mono (O-01); `next/font/local` Inter/JetBrains Mono en de fontsource-pakketten verdwijnen in
  fase 2d.
- Kleinste tekst 14 px; tabelcel, badge en meta `text-sm`; body 16 px; h1 `text-2xl`; kaartwaarde `text-3xl`
  proportioneel; getallen in kolommen `cijfers`.
- Kapitalen-labels ("SERVERS DIE MSO AUTOMATISCH BEHEERT") worden gewone `text-sm font-semibold`-koppen (h3).
- Radius 8 px (kaart/knop/veld), statuspil rond, risicotag 4 px. Randen in plaats van schaduwen.
- Breekpunten Tailwind: zijbalk ≥ 1024 (was 861); ondernavigatie < 1024.

### 6.6 Per scherm (handoff)

Algemeen voor elk scherm: PageHeader (h1 + omschrijving `max-w-prose` + meta "Bijgewerkt 16:19 (zojuist)" waar de
pagina één hoofdbron heeft) → kaarten in een raster van 1 (< 768) / 2 (`md`) / 3 kolommen (`xl`); tabellen ≥ 768 als
echte `table`, < 768 als kaartenlijst (ResponsieveLijst C-34); nooit horizontale scroll voor kerninformatie.

| Scherm | Peil-patroon | Wat er verandert | Let op |
|---|---|---|---|
| **Schil** | AppSchil C-01 (zonder Hub-items) | zijbalk 15 items in 6 groepen blijft (eigen IA), groepskop `text-sm font-semibold` (geen kapitalen), actief = `bg-accent` + 4 px balk + `aria-current`; voet: StatusBadge totaal + max. 3 rijen als links + versie; **"Terug naar de Hub"** bovenaan (§9.1); kopbalk: zoeken, "Wacht op jou" (inktteller), moduschip, ThemaKeuze (de bestaande cyclusknop blijft, profielmenu krijgt de ToggleGroup Systeem/Licht/Donker; standaard **Systeem**), "Automatisering pauzeren" (secundair, `circle-pause`), profielknop (initialen + sr-only "profiel en thema"); ondernavigatie 5 items blijft (Overzicht · Zoeken · Downloads · Ontbrekend · Meer), Meer = Sheet van onder met "Terug naar de Hub" als eerste item | `layout.tsx`: `data-theme="dark"` vast en `applyTheme(local ?? "dark")` worden "system" (Peil O-02); `themeColor` volgt het thema; skiplink blijft eerste tabstop |
| **Overzicht** | Overzicht/Home (§9) | PageHeader met "Bijgewerkt" + Vernieuwen; KPI-tegels → StatKaart (Gezondheid = StatusBadge groot "Beperkt — 1 aandachtspunt", Kosten = "Deels onbekend" als diensten zonder prijs); Doelen: doelstatus volgens §6.2, trend als Lijngrafiek (§7); "Wacht op jou" en "Overige aanbevelingen" met RisicoBadge; Koppelingen: StatusBadge per connector met reden; "Systeem & MediaGuard": `dl` met neutrale waarden ("Laatste back-up 3m 37s" zonder chip, "Mislukte taken 0" zonder chip); Faaloorzaken = tabel; Recente activiteit = Incidenttijdlijn | verouderd-gedrag (nu stil); geen nieuwe API-aanroep |
| **Zoeken** | Formulier + Lijst | segmenten omlopend; resultaten ≥ 768 tabel, < 768 kaart per release (titel in mono omlopend, kwaliteitslabels neutraal, zekerheid in inkt); bronrapport als lijst met StatusBadge per bron ("Werkt niet volgens Prowlarr" = Storing + reden; "leeg" = neutraal "0 resultaten"); vergelijken = Sheet van rechts (desktop) / volledig scherm (mobiel), verschillen met `info`-vlak + tekst "Verschilt" (niet alleen kleur) | placeholder "Titel, bijv. The Expanse of Frieren" is een zoekhint, geen NAS-waarde: mag blijven |
| **NZB-/Torrent-indexers** | Lijst + detail | betrouwbaarheid = getal + inktbalk; bereik/scope als neutrale tags; statuskolom StatusBadge; tabel < 768 → kaarten | zelfde component voor beide routes |
| **Usenet-providers** | Kaarten + tabellen | "Laag vertrouwen" = ZekerheidLabel Onzeker; "passief uit SABnzbd" neutraal; serverprioriteit: "Werkt" = Gezond (gemeten), "ja/nee" neutraal; "Toestemming nodig" = R2; SABnzbd-categorieën: diagnose met `melding-info`/Gezond, staafgrafiek §7, koppeltabel < 768 → kaarten (select volle breedte); header-index "Uit" = neutraal label (geen pauze-icoon), waarschuwing = `melding-beperkt` | 41 px overloop op 360 |
| **Torrentclient** | Kaarten | "Staat nog uit" (preventie) = neutraal + uitleg (geen amber: niets is kapot); mogelijkheden = DekkingsMatrix-cellen; "Verbonden" = Gekoppeld; VPN-bevinding met ZekerheidLabel + BewijsPaneel; knelpunten-tellers neutraal; tuning: "Automatisering niet toegestaan (alleen-lezen of gepauzeerd)" = GateBlokkade-stijl (slot, "Dit is geen storing; er wacht een voorwaarde.") | 56 px overloop op 360 |
| **Opslag & seeding** | KPI + Meter | reservemeter: vulling `chart-1`, boven de drempel Beperkt met icoon + tekst, kritiek = Storing; ratio/tijd-voortgang neutraal (100 % ≠ groen) | — |
| **Downloads** | Tabs + ResponsieveLijst | tabs omlopend; geschiedenis < 768 als kaart (naam omlopend, status, oorzaak als tag + uitleg); "Mislukt" = Storing, "Voltooid" neutraal; torrentlijst: actieknoppen ("Opnieuw aankondigen") nooit buiten beeld (nu afgekapt op 1440); voortgang = Progress neutraal | 93 px overloop op 360 |
| **Ontbrekend & herstel** | Grafiek + Lijst + Master-detail | Source Baseline §7 (Segmentbalk + matrix-tabel); "Betrouwbaarheid: middel" = ZekerheidLabel; "Verbonden" = Gekoppeld; Ontbrekende items: filters in één FilterBalk (mobiel Sheet), paginering 44 px; "Vervanger zoeken: aan" neutraal | "Tabel (horizontaal scrollbaar)" verdwijnt < 768 |
| **MediaGuard-koppeling** | Detail + matrix | Verbinding: "Gekoppeld" neutraal, rol en bereikbaarheid als `dl` (geen groene chips), "Noodstop MediaGuard: Uit" neutraal (aan = PausedBar-stijl "Gepauzeerd door de MediaGuard-noodstop"); Compatibiliteitsmatrix = DekkingsMatrix (cel = symbool + woord), < 768 kaart per route; quotum = Meter | 45 groene chips → 0–4 (alleen gemeten gezondheid) |
| **Kosten & besparing** | KPI + Lijst + simulatie | StatKaart "Totaal per maand" met **Deels onbekend** als prijzen ontbreken; "Prijs ontbreekt" = neutraal open-label (BesluitStatusBadge "Open", nooit amber/rood); onzekerheidsbalk §7; bedragen altijd met "≈ … · geschat" waar het een schatting is | B05-008: "geen gegarandeerde besparing" blijft zichtbaar |
| **Automatisering** | Tabs + ActiePreview | "Drie niveaus" → RisicoBadge R0/R1/R2 + vaste woorden + MSO-uitleg; 7 tabs omlopend (< 768); wijzigingsvoorvertoning in ActiePreview-stijl (samenvatting, "Waarom R2?", DiffWeergave, zijkolom veiligheid: terugdraaien, controle, limiet); "Veilige ronde nu uitvoeren" uitgeschakeld = `aria-disabled` + zichtbare reden | pauze-dialoog behoudt `alertdialog` en `pause-confirm` |
| **Voetafdruk** | Lijst | "onomkeerbaar" als RisicoBadge-tekst; overschrijven = AlertDialog | — |
| **Logs & audit** | AuditTijdlijn (C-25) + LogWeergave (C-52) | filters één rij; actor als ActorLabel; actiecode mono omlopend | `audit-filter` blijft |
| **Instellingen** | Tabs + SchemaFormulier (C-37) | 14 tabs omlopend (nu verborgen horizontale scroll); per veld: label, hulp, bron-tag ("standaard"/"ingesteld"), RisicoBadge, slot (`lock`) of "herstart nodig"; Bedrijfsmodus: ToggleGroup (`Bedrijfsmodus kiezen`) + uitleg als `dl` die < 640 onder elkaar staat; Verbindingen: connectorformulier met GeheimVeld (nooit vooringevuld, "••••1a2b"); Gebruikers: ResponsieveLijst | geheimen nooit tonen; placeholder "Bijv. http://192.168.1.10:30080" in de MediaGuard-URL-hulptekst wordt "Vorm: http(s)://<NAS-adres>:<poort>. Geen localhost." (Peil §11.2; geen voorbeeld-IP) |
| **Back-up & updates** | Kaarten + tabellen | Onderdelen: StatusBadge per onderdeel (Gezond blijft groen: gemeten); migraties en taken ≥ 768 tabel, < 768 kaarten; taakstatus §6.2 ("Gelukt" → Afgerond neutraal, "Staat stil" → Wacht op voorwaarde); back-up maken met Progress; terugzetten: AlertDialog met getypte naam (bestaand) | `backup-create`, `restore-list`, `#danger-confirm` blijven |
| **Inloggen** | Formulier | knop "Inloggen" uitgeschakeld = Peil-stijl (geen opacity); verkooppunten links blijven (≥ lg), mobiel eronder | `smoke.spec` (kop + knop "Inloggen") |
| **Eerste start** | Wizard (C-36) | stepper < lg alleen bollen + h2 "Stap 1 van 4: Beheerder"; foutsamenvatting `role=alert`; actiebalk | 116 px overloop op 390 |
| **Niet gevonden / geen toegang** | Foutpaneel paginaniveau | h1 + uitleg + "Naar Overzicht" | `not-found` testid |
| **Offline** | VerbindingsBanner / Foutpaneel | "MSO-core is niet bereikbaar" (bestaande tekst) + Opnieuw proberen; `wifi-off` op `unknown`-vlak (nu amber) | — |

### 6.7 Microcopy (NL, ux-copy)

Domeinteksten blijven. Alleen deze woorden veranderen (vaste woordenlijst ONTWERPTAAL §11.1):

| Nu | Wordt | Waar |
|---|---|---|
| Verstoord | **Beperkt** | `status.degraded`, KPI Gezondheid, connectoren |
| Offline (gezondheid) | **Storing** | `status.offline` |
| Gedeeltelijk verstoord / Alle systemen gezond / Core offline / Status onbekend | **Beperkt** / **Alles gezond** / **Onbekend — MSO-core reageert niet** / **Onbekend** | `health.*` (zijbalkvoet) |
| Nog niet beschikbaar (bij laden/fout) | laden: skelet + sr-only "Laden…"; fout: Foutpaneel; 404-route: **Niet beschikbaar — MSO-core biedt dit niet aan** + `capability_unavailable` | `KpiCard`, `state.unavailableTitle` |
| Er ging iets mis | **<Wat mislukte> is mislukt.** + waarom + wat je kunt doen (bv. "Kosten ophalen is mislukt. MSO-core gaf een interne fout. Je configuratie is niet gewijzigd. Probeer het opnieuw; blijft het mislukken, kopieer dan de trace-ID voor het logboek.") | `state.errorTitle/Body` |
| Correlatie-ID: … | **Trace-ID** + Kopiëren ("Gekopieerd." / zonder secure context: "Tekst geselecteerd. Kopieer via het menu van je browser.") | `state.correlation` |
| Gegevens … — laatste verversing mislukt | **Verouderd · laatst bekend 16:19** + "Verversen mislukt om 16:20. Bediening staat uit tot de bron weer antwoordt." | `state.stale` |
| Advies / Automatisch veilig / Toestemming nodig | **R0 · Alleen lezen** / **R1 · Omkeerbaar** / **R2 · Goedkeuring nodig** (+ MSO-uitleg) | `risk.*` |
| Geslaagd / Gelukt (taak, download) | **Afgerond** (taak) / **Voltooid** (download) | `status.succeeded`, geschiedenis |
| Staat stil | **Wacht op voorwaarde** + reden | taken |
| Verbonden | **Gekoppeld** | qBittorrent, MediaGuard |
| Laag vertrouwen | **Onzeker** | beschikbaarheid |
| Automatisering gepauzeerd. | blijft; vorm = Gepauzeerd (info) | banner |
| Profiel (knopnaam) | initialen + sr-only "profiel en thema" | kopbalk |
| — (nieuw) | **Terug naar de Hub** · **Toon als tabel** / **Verberg tabel** · grafiekhulp "Gebruik de pijltjestoetsen om per dag te lezen." · "Geen meting" | §9 |

Toon: rustig, eerst wat er is, dan waarom, dan wat je kunt doen; knoppen beginnen met een werkwoord; geen "OK",
"Doorgaan" of "Weet je het zeker?". MSO heeft geen en-US-teksten; nieuwe teksten zijn alleen NL.

---

## 7. Grafieken (dataviz-procedure)

Volgorde: vorm → kleur → validatie → markeringen → interactie → toegankelijkheid → zelf bekijken. Palet: Peil
`chart-1…5` + `chart-other`, gevalideerd (§4). **Statuskleuren zijn nooit een reeks.** Elke grafiek: `figure` +
`figcaption` (bron · bijgewerkt · "een gat betekent dat er geen meting was (nooit 0)"), plotvak `role=img` +
`tabindex=0` + samenvatting, pijltjes/Home/End/Esc met voorlezen via `aria-live="polite"`, tooltip via aanwijzer én
toetsenbord, `details` **"Toon als tabel"** (de WCAG-tweeling). MSO houdt eigen SVG (O-13), labels als HTML (14 px).

| Grafiek | Vorm (job) | Kleur | Tabel (kolommen) | Toetsenbord |
|---|---|---|---|---|
| **Doel-trendlijn** | Trend t.o.v. doel → **lijn met grenslijn**; één reeks, geen legenda | lijn `chart-1` 2 px; doel = `chart-limit` 1,5 px met direct label "Doel: onder 10 %"; ontbrekende dag = gat met `arcering-gat` + "Geen meting"; **gereconstrueerd = geschat → stippellijn** + "≈" (nu: opacity 0,4); status van het doel staat als StatusBadge **naast** de grafiek, niet in de lijnkleur | Dag · Waarde (met eenheid) · Meetkwaliteit (gemeten/geschat) · Doel | ←/→ per dag, Home/End, Esc sluit tooltip; voorlezen "8 nov: 83,3 % (geschat)" |
| **Downloads in '*' per dag** | Deel van een totaal per dag → **gestapelde kolommen**, *emphasis*: de reeks die ertoe doet in kleur, de rest grijs | "in '*'" = `chart-1` onderaan (op de basislijn), "overige downloads" = `chart-other` erboven, 2 px vlakgat; legenda met beide namen; staaf ≤ 24 px, 4 px afgerond aan de datakant | Dag · In '*' · Totaal · Aandeel | ←/→ per dag; voorlezen "9 nov: 0 van 6 in '*'" |
| **Verdeling per oorzaak (Source Baseline)** | Deel-van-geheel per rij **naast de cijfers** → **dunne segmentbalk zonder tekst** + **matrix-tabel**; meer dan 6 oorzaken → vouwen | vaste slot per oorzaak (kleur volgt de oorzaak, nooit de rang): Geen indexerresultaat 1 · Afgewezen op kwaliteit 2 · Onvolledig 3 · Fout in downloadclient 4 · Importfout 5 · **Overig** (`chart-other` + arcering) = andere bekende oorzaak + niet gevonden + vals-positief; **Onbekend** krijgt geen kleur maar een eigen kolom en het woord (nooit grijs-als-reeks verwarren met status); 2 px gat | Categorie/Bron · Totaal · één kolom per oorzaak (incl. Onbekend) · Betrouwbaarheid | rij-voor-rij via de tabel (de balk is `aria-hidden`; de tabel draagt de cijfers) |
| **Onzekerheidsbalk (kostensimulatie)** | Bereik in een geheel → **dunne segmentbalk** met de getallen ernaast | zeker getroffen = `chart-1`; onzeker = `chart-1` met `arcering-geschat` (geschat, Peil); niet getroffen = `chart-meter-track`; **geen rood/amber**; legenda met aantallen (bestaat) | Deel · Aantal · Aandeel | via de tabel; balk `aria-hidden` |
| **Meters** (`Progress` → Meter) | Verhouding tot een grens → **meter** | vulling `chart-1` op `chart-meter-track`; ≥ drempel (bestaande drempels: 70/80/90 %) Beperkt met `triangle-alert` + tekst "92 % van de blokruimte — boven de drempel van 90 %"; kritiek/gemeten falen = Storing; voortgang (download, back-up, taak, ratio/tijd) = Progress neutraal, 100 % blijft `chart-1` | n.v.t. (één waarde, altijd als tekst ernaast) | `role=meter` + `aria-valuetext` / `role=progressbar` |
| **Zekerheid/betrouwbaarheid** | één getal → **getal + mini-meter in inkt** | `foreground`, geen statuskleur | n.v.t. | — |

Niet toegestaan (anti-patronen): tweede y-as, kleuren laten verspringen bij filteren, een 6e gegenereerde tint, een
tooltip als enige manier om een waarde te lezen, skeletflits bij verversen (vorige weergave blijft staan).

---

## 8. De twee fasen

### Fase 1 — aliaslaag en woorden (geen componentstructuur, geen nieuwe afhankelijkheid)

**1a. CSS (alleen uiterlijk)**

1. Kopieer bij de bouw `design/ontwerptaal/ontwerptaal.css` en `aliaslagen/mediaguard-mso.css` naar
   `apps/web/styles/peil/` (met herkomstkop, ongewijzigd).
2. `app/globals.css` wordt: `@import "tailwindcss"; @import "../styles/peil/ontwerptaal.css"; @import
   "../styles/mg.css"; @import "../styles/mso.css"; @import "../styles/peil/mediaguard-mso.css"; @import
   "../styles/peil-mso.css";` — en het eigen `:root { --mg-font… }`-blok vervalt.
3. Nieuw `styles/peil-mso.css` met de correcties a–j uit §6.1.
4. `app/layout.tsx`: standaardthema **Systeem** (`data-theme` uit het script; zonder keuze het opgeloste
   `prefers-color-scheme`), `themeColor` per schema. De opslagsleutel `mso.theme` blijft.
5. Verwachte uitkomst: alle kleuren, radii en het systeemfont volgen Peil; overloop 0 op 360/390; focus Peil-ring en
   nooit bedekt; geen opacity op uitgeschakelde tekst. **Nog niet:** tekst ≥ 14 px (inline maten), 44 px op desktop,
   iconen.

**1b. Woorden en iconen (alleen in bestaande componenten, geen nieuwe structuur)**

1. `lib/status.ts`: tonen volgens §6.2 (groen alleen voor gezondheid; `JOB_TONE.done`, `CHANGE_STATUS.applied` →
   neutraal); `primitives.tsx`: `TONE_ICON.neutral` = geen icoon (of `circle-help` alleen voor *onbekend*), `danger` =
   `octagon-x`.
2. `lib/i18n/messages/nl/*`: de woorden uit §6.7 (statuswoorden, health, risico, taken, fout, verouderd, trace-ID).
3. `RiskChip`: R-klasse-weergave (code + treden-glyph via CSS + vast woord), MSO-uitleg als `title` **en** als tekst
   in de "Drie niveaus"-kaart.
4. `KpiCard`: laden → skelet in plaats van "— Nog niet beschikbaar"; fout → "Onbekend — ophalen mislukt" (geen
   fase-woord); waarde altijd in `foreground`.
5. `ResourceView`: verouderd ook tonen als de (laatst bekende) lijst leeg is; Overzicht gebruikt dezelfde markering bij
   `ov.stale`.
6. Profielknop: initialen + sr-only tekst (axe 2.5.3); `span.qribbon`: sr-only tekst in plaats van `aria-label`.
7. Tests: §10.

### Fase 2 — Tailwind 4 + shadcn/ui (React 19, new-york)

**2a. Basis en schil**
1. Afhankelijkheden: `class-variance-authority`, `clsx`, `tailwind-merge`, Radix via shadcn (exacte versies vastzetten
   in `package-lock.json`; geen `latest`). `components.json` uit `design/ontwerptaal/` met MSO-paden
   (`css: "app/globals.css"`, aliassen `@/components/ui`, `@/lib/utils`). `shadcn init` mag `globals.css` niet
   overschrijven (codemod COMPONENTEN §1, ook `@custom-variant dark` en het variabelenblok weg).
2. AppSchil: zijbalk ≥ 1024 met de 15 MSO-items, kopbalk, ondernavigatie < 1024, Meer als Sheet van onder, ThemaKeuze
   in het profielmenu, **"Terug naar de Hub"** (§9.1). Testids en `aria`-namen blijven.

**2b. Componenten (één tegelijk, elk met eigen commit en groene e2e)**
Volgorde: Button → StatusBadge/Label → RisicoBadge → Card/StatKaart → Foutpaneel/KopieerVeld/Leeg/Laden → Tabs →
ToggleGroup → Switch/Input/Select/Field → Dialog/AlertDialog (Modal, DangerDialog) → DiffWeergave → Stepper/Tijdlijn →
Progress/Meter → ResponsieveLijst (tabellen → kaarten < 768) → Sonner. Per component: codemod COMPONENTEN §1 (44 px,
`text-sm` minimum, Peil-ring, `destructive-foreground`, geen opacity-trucs, `z-overlay/z-toast`).

**2c. Toegestane toevoegingen**
Tabelweergave + toetsenbord bij de vier grafieken (§7, §9.2) en de Hub-link (als die niet al in 2a zat).

**2d. Opruimen (ONTWERPTAAL §13.3 "fase 3")**
Inline `style={{…}}` → utilities (736×, waarvan 257 lettermaten); `mg.css`/`mso.css` en de aliaslagen weg zodra niets
ze meer gebruikt; `app/fonts/*`, `next/font/local` en de fontsource-pakketten weg; `recharts` (dode afhankelijkheid)
mag weg; `PROVENANCE.md`: MSO kopieert niet meer uit MediaGuard maar uit `ai-server-manager/design/ontwerptaal` (versie +
sha256). Hookklassen die tests gebruiken (`.skeleton`, `.state--error`, `.btn`, `.chip`) blijven op de elementen staan.

**Per stap terugdraaibaar:** elke stap is één commit; fase 1a is één CSS-import (terugzetten = regel weghalen).

---

## 9. Toegestane toevoegingen — specificatie

### 9.1 "Terug naar de Hub" (O-11)

- **Bron van het adres:** een nieuwe, optionele instelling **`systeem.hub_url`** in het bestaande
  instellingenregister (categorie "systeem", type string, standaard leeg, risico "advies", geen herstart). Lezen en
  schrijven via het bestaande `GET/PUT /api/v1/settings`: geen nieuwe route, geen migratie (waarden staan in de
  bestaande `settings`-tabel), OpenAPI byte-gelijk, Custom App-formulier ongewijzigd (ADR-0006 punt 1).
- **Geldig adres** (zelfde regels als `HUB_URL`, contract 1.2 §3.2): `http(s)://<host>[:<poort>]`, zonder pad, query,
  fragment of inloggegevens; geen loopback, link-local of `0.0.0.0`/`::`. De UI toont de link **alleen** als de waarde
  zo te parsen is (`new URL`, protocol `http:`/`https:`); anders verborgen. Server-side dezelfde controle bij opslaan
  (422 "Hub-adres: gebruik http(s)://<NAS-adres>:<poort>, zonder pad"). **Nooit** een voorbeeld-IP of -poort als
  standaard of placeholder.
- **Plaats:** zijbalk direct onder het logo (≥ 1024) en als eerste item van de Meer-sheet (< 1024). Niet op de
  inlogpagina (het adres is pas na inloggen te lezen).
- **Vorm:** secundaire link-knop, `min-h-touch`, icoon `arrow-left` (20 px, `aria-hidden`) + tekst **"Terug naar de
  Hub"**; zelfde tabblad (het is een terugweg, geen deep link); `rel="noopener"`. Tooltip/`title` niet nodig.
- **Toestanden:** onbekend/leeg/ongeldig → niet in de DOM (geen lege plek, geen uitgeschakelde knop); geen leesrecht op
  instellingen → verborgen.
- **Instellingenveld:** label "Hub-adres", hulp "Vorm: http(s)://<NAS-adres>:<poort>. Geen localhost. Leeg = geen
  link 'Terug naar de Hub'.", bron-tag "standaard"/"ingesteld".
- **Tests:** e2e — leeg ⇒ geen link; geldig ⇒ link met `href` gelijk aan de waarde; `javascript:`/pad/loopback ⇒ geen
  link en 422 bij opslaan; unit-test op de URL-validator.

### 9.2 Tabelweergave en toetsenbord bij elke grafiek

- Geldt voor de vier grafieken uit §7 (doel-trendlijn, '*' per dag, oorzaakverdeling, onzekerheidsbalk). Meters en
  zekerheidsbalkjes tonen hun waarde al als tekst en krijgen geen aparte tabel.
- `details` **"Toon als tabel"** onder de grafiek (dicht bij openen van de pagina); `table` met `caption` (= titel van de
  grafiek), `th scope`, getallen `cijfers` rechts uitgelijnd, "Geen meting" als tekst (nooit 0 of "—"); bij > 4 kolommen
  op < 768 een kaart per rij.
- Plotvak `tabindex=0`, `aria-label` = samenvatting + "Gebruik de pijltjestoetsen om per dag te lezen."; ←/→ per punt,
  Home/End, Esc sluit; zichtbare focus = Peil-ring om het plotvak + draadkruis; voorlezen via één
  `p[aria-live=polite]` per grafiek.
- Testhaak: `data-testid="grafiek-<naam>"` en `data-testid="grafiek-<naam>-tabel"` (nieuw, kebab-case NL).

---

## 10. Welke tests geraakt worden

### 10.1 Bestaande tests

| Test | Raakt | Wat er moet gebeuren |
|---|---|---|
| `e2e/screens.spec.ts` (16 schermen × 2 thema's × 1440/390) | h1-teksten, `.skeleton`, `.state--error`, `data-theme`, knopnamen, uitgeschakelde knoppen met uitleg, axe, afdrukken | blijft groen als testids/klassen blijven. **Aanpassen:** (1) overloop meten t.o.v. de ingestelde breedte (`scrollWidth <= viewport.width`) — met `isMobile` is `scrollWidth − innerWidth` altijd 0, dus de huidige check is blind; (2) axe-tags uitbreiden met `wcag21a` (Peil-set) — faalt nu op de profielknop; (3) uitgeschakelde knoppen: `aria-disabled` naast `[disabled]` meetellen; (4) afdrukken naar `docs/screenshots/` opnieuw (64) |
| `e2e/dialogs.spec.ts` | `alertdialog`, "Terugzetten", `#danger-confirm` met focus, `pause-button` (enabled), `mode-chip` → `/instellingen?tab=systeem#modus`, groep "Bedrijfsmodus kiezen" | rollen, namen en id's behouden; AlertDialog van shadcn mag de focus niet verplaatsen bij typen (test P5) |
| `e2e/smoke.spec.ts` | titel, kop en knop "Inloggen", labels Gebruikersnaam/Wachtwoord | behouden |
| `e2e/strict-reporter.ts` + `E2E_MIN_TESTS=78` | aantal tests | nieuwe tests verhogen het minimum in de Makefile |
| `apps/core` pytest (789) | alleen §9.1 (nieuwe instelling + validator) | registertests (aantal definities/categorieën) bijwerken; anders niets |
| OpenAPI-snapshot | — | `create_app().openapi()` opnieuw exporteren (zelfde commando als INVENTARIS §15) en byte-vergelijken met sha256 `bb551ee1…471b` |
| `tests/stack/allinone_test.py`, stack-smoke | — | ongewijzigd (geen installatie- of API-wijziging) |

### 10.2 Nieuwe tests (Peil-testmatrix, ONTWERPTAAL §12)

- Breedtes **320, 360, 390, 768, 1024, 1440** × licht/donker: overloop 0 t.o.v. de apparaatbreedte; geen doel < 44 px
  (radio/checkbox: label telt; inline links uitgezonderd); geen tekst < 14 px (na fase 2d; in fase 1 als rapport);
  geen ellips op inhoud.
- axe `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` = 0 (ook in zoekresultaten, vergelijken, dialogen en lades).
- Tab-doorloop op 390 en 1440: elke stop heeft de Peil-ring en is **niet bedekt** (zie `toetsenbord.mjs`).
- Eén h1, geen kopsprongen, geen dubbele id's; `prefers-reduced-motion` = geen beweging; forced colors toont
  stippelranden.
- Status: geen `.chip--success`/groene statusvorm buiten gezondheid (snapshot van tonen per scherm); Onbekend heeft
  nooit het pauze-icoon.
- Grafieken: tabel aanwezig en gelijk aan de data; pijltjes lezen "Geen meting" voor bij een gat.
- Hub-link: §9.1.
- Afdrukken "na" in `docs/screenshots/peil/na/` met hetzelfde script (360/390/768/1440 × licht/donker).

---

## 11. Risico's, open punten en wat niet gevalideerd is

- **Gedeelde aliaslaag mist de focusring en scroll-padding voor `mg.css`-apps** (laagvolgorde). Opgelost in
  `peil-mso.css`; gemeld als vervolgpunt voor de ontwerptaal (MediaGuard heeft hetzelfde).
- **Inline styles winnen van CSS.** Zekerheidsbalkjes en een deel van de chips/kleuren volgen in fase 1 alleen met
  `!important` of pas in fase 2. Lettermaten < 14 px blijven tot fase 2d (ONTWERPTAAL §13.3 zegt dat eerlijk).
- **Dichtheid.** 44 px en 14 px maken tabellen langer; compensatie via ResponsieveLijst en `details` voor lange uitleg.
- **DangerDialog blijft een dialoog** (geen nieuwe route; ADR-0006 punt 2).
- **Backendteksten** ("Verstoord", "Offline" in `message`) blijven in de API; de UI vertaalt (ADR-0006 punt 3).
- **Niet gevalideerd:** echte telefoon, schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors op MSO; echte NAS
  (Excel B05-*: blijft "NIET GEVALIDEERD (echte NAS)"). Deze ontwerpstap verandert geen Excel-status.
- **Vervolg:** na "OVERDRACHT VOLTOOID" de herkomst in `design/ontwerptaal/HERKOMST.md` omzetten naar
  `ai-server-manager` (commit + sha256) en een CI-driftcontrole toevoegen (ONTWERPTAAL §14).

---

## 12. Uitvoering (stap 3, 9 november 2026)

| Onderdeel | Stand | Waar |
|---|---|---|
| 1a aliaslaag + correctielaag + thema Systeem | gedaan | `apps/web/styles/peil/`, `styles/peil-mso.css`, `app/layout.tsx` |
| 1b woorden en iconen | gedaan | `lib/status.ts`, `components/mg/*`, `lib/i18n/messages/nl/*` |
| 2a basis (cva, clsx, tailwind-merge, Radix), `components.json`, AppSchil, "Terug naar de Hub" | gedaan | `components/ui`, `components/shell/*`, `lib/hub.ts`; core: `systeem.hub_url` |
| 2b componenten (Button, StatusBadge, RisicoBadge, Card, Foutpaneel/KopieerVeld/Leeg/Laden, Tabs, ToggleGroup, Switch/Field, Dialog/AlertDialog, Progress, Skeleton, Toast) | gedaan, met behoud van API | `components/mg/primitives.tsx`, `states.tsx`, `domain.tsx`, `toast.tsx` |
| 2b ResponsieveLijst | gedaan als container query op `.table--stack` (kaart < 56 rem) | `styles/peil-mso.css` |
| 2b DiffWeergave, Stepper, Tijdlijn | alleen de aliaslaag/correcties uit fase 1 (geen eigen shadcn-versie) | — |
| 2c tabelweergave + toetsenbord bij de vier grafieken | gedaan | `components/mso/charts/chart-frame.tsx` |
| 2d fonts en `recharts` weg, tekst ≥ 14 px, geen kapitalen | gedaan | — |
| 2d overige inline styles → utilities; `mg.css`/`mso.css`/aliaslaag weg | **niet gedaan** (569 inline styles over) | vervolg, CHANGELOG |

Gemeten na de omzetting (teststack, HEAD van stap 3): e2e 109/109 (strikt), vitest 45/45, OpenAPI byte-gelijk. De
Peil-e2e controleert per hoofdscherm op 320/768/1024 overloop, axe (Peil-set), één h1, 44 px in `main`, geen
pauze-icoon bij Onbekend en geen groen buiten gezondheid; de afdrukken en metingen staan in `docs/screenshots/peil/na/`.

