<!-- HERKOMST — Peil 1.1.1 · bron: lvos04/ai-server-manager@0a7143bc0a94ed5317a9655fa785e73a1fb07ba1 docs/adr/ADR-0015-ontwerptaal-peil.md · sha256 van het bronbestand (zonder deze kop): af350a471658a22669b18058b21314d731226a23245f8af4874534f6e2808ac8 · versie-gepinde kopie (O-14, ONTWERPTAAL §14), niet met de hand wijzigen (scripts/bronkopie.py). Relatieve links naar bestanden die hier niet zijn gekopieerd (prototypes/, shots/, bouw/, REVIEW.md, andere ADR's) werken alleen in de bron. -->

# ADR — Eén ontwerptaal "Peil" voor het TrueNAS Home Platform V2

- **Status:** Aangenomen door de ontwerp-agent (mandaat van de gebruiker: "de ontwerp-agent beslist zelfstandig binnen
  deze kaders en legt keuzes vast"). In te nemen in `ai-server-manager/docs/adr/` als volgend ADR-nummer zodra
  ai-server-manager na "OVERDRACHT VOLTOOID" weer beschrijfbaar is.
- **Datum:** 9 november 2026, aangevuld 10 november 2026 · **Versie ontwerptaal:** 1.1.1 (1.0.0 + herstellingen uit de
  ontwerpreview = 1.1.0; + verbeterpunten uit de reviews van de apps en statuspagina's = 1.1.1, zie onderaan)
- **Vervangt:** ADR-0009 punt 1 van de Hub ("geen CSS-framework, geen iconenbibliotheek"). De overige punten van
  ADR-0009 (navigatie, Apps onder Server, service worker alleen voor de schil, "zero empty screens") blijven.
- **Raakt:** ai-server-manager (Hub-UI en executor-statuspagina), home-control-hub, appstore-en-fork,
  jelly-leon-platform, zoeken-en-persoonlijke-ai (statuspagina's), media-guard-ultimate, media-source-optimizer,
  my_house (alleen uiterlijk). **Niet:** Jelly Leon web/clients, UI's van derden, ai-factory.

## 1. Context

De vier bestaande UI's hebben elk een eigen ontwerp (MediaGuard/MSO: donker controlecentrum met `mg.css` en 800 inline
styles; my_house: warm, Space Grotesk, geen Tailwind; Hub P0: eigen CSS zonder framework). Er zijn vier
statusvocabulaires, drie woorden voor *degraded*, groen voor "actueel" en "automatisch veilig", rood voor een gate die op
invulwerk wacht, veldranden van 1,42:1, wit op rood van 3,18:1 en grafiekpaletten die de dataviz-validator afkeurt
(`onderzoek/BESTAANDE_UI.md`). De platformeisen (`PLATFORM_CONTRACTEN.md` §1/§6/§8, `08_UI_EN_INTERACTIES.md`,
`onderzoek/EISEN_EN_SCHERMEN.md`) vragen één eerlijke, Nederlandse, toegankelijke taal over alle oppervlakken.

De gebruiker heeft vastgelegd: één eigen ontwerptaal op **Tailwind CSS 4 + shadcn/ui (new-york, CSS-variabelen) +
lucide-react**; reikwijdte Hub + statuspagina's + uiterlijk van MediaGuard, MSO en my_house; Jelly Leon niet.

## 2. Alternatieven en juryoordeel

Drie richtingen zijn volledig uitgewerkt (tokens, contrast, prototypes, schermafdrukken) en door drie onafhankelijke
jury's beoordeeld:

| Richting | Toegankelijkheid | Platformeisen en eerlijkheid | Consistentie en haalbaarheid | **Som** |
|---|---|---|---|---|
| **A — Rustig controlecentrum** | 6 | 6,5 | **8** | 20,5 |
| **B — Warm thuis** | 6,5 | 7 | 5 | 18,5 |
| **C — Helder en toegankelijk eerst** | **8,5** | **7,5** | 6,5 | **22,5** |

Noot: het meegegeven object `SOMSCORES` telde de derde jury onder andere sleutels (`a 12,5 · b 13,5 · c 16` zijn de som
van twee jury's; `"A — …": 8` enz. is de derde). Met alle drie de jury's geteld wint C ook (22,5 tegen 20,5 en 18,5).

**Winnaar: C.** Geen van de blokkerende fouten van C is principieel; alle zijn herstelbaar zonder het kernidee los te
laten. Herstel:

| Blokkerend (jury) | Bij C | Herstel in Peil | Bewijs |
|---|---|---|---|
| Toegankelijkheid | 10 px overloop op 320 px (knop met `nowrap`) | geen `whitespace-nowrap` op inhoud; codes breken; 320 en 1024 in de testmatrix | `controle/samenvatting.txt`: 0 overloop, 10 schermen × 6 breedtes × 2 thema's |
| Toegankelijkheid | focus 6× volledig achter de sticky ondernavigatie | `scroll-padding-top/bottom` = kop/ondernavigatie (+ actiebalk) + safe area, in de basislaag | 0 bedekte focus bij volledige Tab-doorloop op 390 en 1440 |
| Platform | R1 in de kleur "menselijke beslissing" (violet), terwijl R1 automatisch loopt | R0 neutraal, **R1 neutraal-sterk**, alleen **R2 violet** (= goedkeuring), R3 rood | `tokens.json` `risk-*`, `approval*` |
| Platform | grafiekpalet bevat groen, rood (status) en violet (R1) | 5 slots: blauw · oranje · aqua · geel · magenta (+ grijs Overig); divergerend blauw ↔ oranje | dataviz-validator PASS in beide thema's; ΔE grafiek ↔ status ≥ 16,1 (eis 15) |
| Consistentie | my_house-alias `--light-on/--light-ink` → één kleur (1:1); `heat-soft/cool-soft` zonder doel | gegenereerde aliaslaag: `--light-on` → `domain-light-on-bg`, `--light-ink` → `domain-light-on-ink`; `-soft` per domein | `contrast.json` → `aliaslagen.my_house`: 50 paren, 0 fouten; `.light-tile.on` 8,5:1 |

Daarnaast zijn de zwakke punten van C opgelost: verschoven typeschaal → Tailwind-standaard (ondergrens 14 px); 48 px →
44 px (dichtheid); asymmetrische namen → regelmatig drieluik met connectorcontract-namen; afgekapte trace-ID → breekt
altijd; kruimelpad 45×20 → 44×44; tooltip zonder live-regio → `aria-live`; domeinkleuren dicht bij status → domein =
grafiekslot, ΔE ≥ 15; lage dichtheid → master-detail en twee kolommen bovenaan op ≥ 1280. Ook de blokkerende punten van
A en B komen niet terug: geen groen live-icoon, geen rood/groene diff, geen domeinkleur op ΔE 4 van een status, geen
omgekeerde `--light-on`.

**Overgenomen uit A:** statusnamen = connectorcontract; *Gezond* zonder vlak; `--color-*: initial`; intacte
Tailwind-schaal; palet zonder status-tinten en domeinkleur = grafiekslot; statuspagina-CSS uit dezelfde tokens; eerlijke
statuspagina-kop; preview met vervaltijd en stappen; meetkwaliteit inline.
**Overgenomen uit B:** 44 px overal; 320 px in de matrix; `dl`-metadata met `<time>`; kinderschil; "Ik weet het niet" als
kaart; "Dit is geen storing; er wacht een voorwaarde"; zichtbaar afwezige R3-actie; kopieer-terugval zonder Ctrl+C.

## 3. Besluit

We voeren **Peil 1.1.0** in, zoals beschreven in `ONTWERPTAAL.md` en `COMPONENTEN.md`, met als bron
`bouw/tokens.mjs` → `tokens.json` (DTCG) → `ontwerptaal.css`, `statuspagina.css`/`.html` en `aliaslagen/*.css`.

Vastgelegde keuzes (de open ontwerpbesluiten uit EISEN §11 en eigen keuzes):

| # | Besluit | Keuze | Waarom |
|---|---|---|---|
| O-01 | Lettertype | **Systeemfont + systeem-mono**; Inter, Space Grotesk, JetBrains Mono vervallen | 0 KB, CSP-veilig, gelijk op de statuspagina's (die geen fonts mogen laden); Roboto op Android |
| O-02 | Standaardthema | **Systeem** overal, keuze Systeem/Licht/Donker per gebruiker; het themascript zet altijd het opgeloste thema | MG/MSO stonden op donker; eenheid; `.dark` én `[data-theme="dark"]` zodat bestaande tests blijven werken |
| O-03 | Verouderde gegevens | **laatst bekend gedempt** (`--stale-ink` ≈ 6:1, arcering, "Laatst bekend · 10:12") + status **Onbekend** + bediening uit + gat in grafieken | bruikbaarder dan wissen; sluit aan op B06-006/EX24; vraagt een Hub-codewijziging |
| O-04 | Desktop-ingangen | **kopbalk**: Zoeken, live, Problemen, Goedkeuringen, AI, gebruiker; Apps onder Server | zijbalk blijft exact 08_UI |
| O-05 | Rechten kind | voorstel kindschil **Home · Kijken · Zoeken · Ik** | kleinste set; **open**: bevestigen met de API-bouwer |
| O-06 | Wie mag R2/R3 goedkeuren | **server-gestuurd** (`magGoedkeuren`), niets hardgecodeerd | beleid hoort in de policy |
| O-07 | Productnaam | **open** (gebruiker); plaatshouder "Hub · Thuisplatform" | merkkeuze |
| O-08 | Kleur dichte gate | **slot + neutraal-sterk** (`gate*`); rood alleen bij gemeten falen | K2 |
| O-09 | Themaknop my_house | **open** (functionele toevoeging); aliaslaag ondersteunt `data-theme` al | alleen uiterlijk |
| O-10 | my_house naar Tailwind | **nee in fase 1**: zelfstandige aliaslaag met eigen variabelenamen | minste risico; geen React 19-upgrade nodig |
| O-11 | "Terug naar de Hub" | **open** (functionele toevoeging) | alleen uiterlijk |
| O-12 | Validatiestatus zichtbaar | **"Over deze Hub", zijbalkvoet en bij uitvoerders/functies zonder hardwarevalidatie** (bijv. in de preview) | relevant waar het ertoe doet, geen ruis |
| O-13 | Grafiekbibliotheek | **recharts via shadcn Chart in de Hub**; MediaGuard/MSO houden hun eigen SVG (alleen tokens) | MG verving recharts al; MSO importeert het niet |
| O-14 | Verspreiding | **versie-gepinde kopie met herkomstkop + sha256**, CI-driftcontrole | repos blijven zelfstandig en offline |
| O-15 | Statussymbolen | **lucide-iconen** (vaste koppeling, ONTWERPTAAL §7); statuspagina's met dezelfde paden inline | consistentie (WCAG 3.2.4) |
| P-1 | Aanraakdoelen | **44 px overal**, ook desktop met muis | platformeis; Hub-e2e controleert 44 px op 1440 |
| P-2 | Tekstcontrast | **twee niveaus, beide ≥ 7:1** op elk vlak; geen derde grijslaag | leesbaar in fel licht (C) |
| P-3 | Typeschaal | **Tailwind-standaard**; kleinste tekst 14 px (`text-xs` verboden) | shadcn blijft kloppen |
| P-4 | Kleurfamilies | groen gezond · amber beperkt · rood storing/R3 · grijs+stippel onbekend · blauw interactie/info · **violet goedkeuring** · neutraal-sterk gate/R1 | één betekenis per familie |
| P-5 | Grafiekpalet | **5 slots** + Overig; statuskleuren buiten de grafiekband, ΔE ≥ 15 (harde controle) | dataviz-regel "status gereserveerd" |
| P-6 | Navigatiewissel | `lg` = **1024 px** | ADR-0009 |
| P-7 | Alleen tokenkleuren | `@theme { --color-*: initial }` + lintregel tegen hex in componenten | geen sluipende statuskleuren |
| P-8 | Diff | **geen rood/groen**: was neutraal doorgestreept, wordt info vet, tekens `~ + −` | verwijderd ≠ storing |

## 4. Gevolgen

**Positief**

- Eén vocabulaire en één beeldtaal over Hub, statuspagina's en de drie bestaande apps; de gemeten fouten (1,42:1-randen,
  3,18:1-gevaarknop, groen "actueel", rode gate, groene "gemeten", rode "onzeker") verdwijnen.
- Toetsbaar: `contrast.json` (154 paren per thema, 0 fouten, ΔE-scheiding, paletvalidatie), `controle/` (axe 0, geen
  overloop, geen bedekte focus) en een referentie-implementatie van de testmatrix.
- Fase 1 per bestaande app is één CSS-bestand (aliaslaag) zonder componentwijziging; tests op testids en klassen blijven
  groen.

**Negatief / risico's**

- MediaGuard/MSO verliezen wat dichtheid (44 px op desktop, 14 px minimum) en hun donkere standaard; dichtheid komt uit
  compacte tabellen en master-detail.
- Het systeemfont oogt per besturingssysteem iets anders (alleen met Noto Sans en Chromium getest).
- Het palet heeft 5 slots; grafieken met meer reeksen moeten vouwen naar Overig of small multiples.
- Doc-afdrukken moeten opnieuw: MediaGuard 56, MSO 64, Hub 29 (+ nieuw voor my_house).
- O-03 (laatst bekend bewaren) vraagt een gedragswijziging in de Hub-code; die kan pas na de overdracht.
- Niet getest: echte schermlezer (NVDA/TalkBack), echte Android-telefoon in fel licht, tv/speler.
- **Ontwerpreview 1.0.0 → 1.1.0** (`../REVIEW.md`): het statuspagina-sjabloon volgde nog contract 1.1 (ontkoppelcode,
  geen vingerafdrukveld, schrijfsleutel in het koppelformulier, "Gekoppeld" groen) en is naar contract 1.2 gebracht; de
  Hub-aliaslaag maakte `--info-bg`/`--unknown-bg` ongeldig (cyclus); `z-nav` e.d. bestonden niet als utility; het
  boilervlak was niet van "Gezond" te onderscheiden en is neutraal; de MediaGuard/MSO-aliaslaag miste correcties
  (noodstop 2,0:1, diff/zekerheid/kwaliteit in statuskleuren). Geen van deze herstellingen verandert een besluit uit dit ADR.

**Vervolg**

1. Na "OVERDRACHT VOLTOOID": `ONTWERPTAAL.md` naar `ai-server-manager/docs/platform-v2/ONTWERPTAAL.md` en de rest van
   `definitief/` naar `ai-server-manager/design/ontwerptaal/` (paden uit contract §13; 1.0.0 noemde `design/peil/`) + dit
   ADR; Hub als eerste (referentie-implementatie, ONTWERPTAAL §13.1).
2. Statuspagina's: sjabloon + CSS per repo als gepinde kopie (O-14).
3. MediaGuard → MSO → my_house: fase 1 aliaslaag, daarna fase 2/3 per app (ONTWERPTAAL §13.3–13.5).
4. Besluiten van de gebruiker: O-05, O-07, O-09, O-11, tabelweergave in bestaande grafieken, native apps.

## Besluiten van de gebruiker na oplevering (2026-11-09, via keuzemenu)

- **O-07 productnaam:** "TrueNAS Home Platform" (linksboven in de Hub-UI; vervangt de plaatshouder "Hub · Thuisplatform").
- **O-05 rol kind:** Home · Kijken · Zoeken · Ik (geen server, apps, huis of instellingen).
- **Functionele toevoegingen in de bestaande apps** (alle vier toegestaan):
  - O-11: link "Terug naar de Hub" in MediaGuard, MSO en my_house (adres uit de configuratie; verborgen als onbekend).
  - O-09: themaknop Systeem/Licht/Donker in my_house.
  - Tabelweergave + toetsenbordbediening bij de grafieken van MediaGuard en MSO.
  - Nieuwe reikwijdte: ook de native Android-apps van MediaGuard en my_house in Peil-stijl.
- **my_house fase 2:** Tailwind 4 + shadcn/ui in de React 18-variant (forwardRef), geen React 19-upgrade.
- Eerder (2026-11-09): design-agent in de workflows; eigen ontwerptaal op Tailwind 4 + shadcn/ui + lucide; reikwijdte Hub-UI,
  statuspagina's en het uiterlijk van MediaGuard/MSO/my_house; Jelly Leon web/clients niet; zelfstandig, achteraf tonen.

## Plaats en verspreiding

- Bron van waarheid (contract 1.2 §13): `docs/platform-v2/ONTWERPTAAL.md` + `design/ontwerptaal/` in deze repo.
- Bouwen en controleren: `cd design/ontwerptaal/bouw && npm ci && cd .. && node bouw/alles.mjs --controle` (optioneel `--shots`;
  `DATAVIZ_VALIDATOR=<pad>` om de paletcontrole opnieuw te meten, anders blijft de vorige meting staan).
- Andere repos krijgen een versie-gepinde kopie met herkomstkop en sha256 (O-14).
- Vervangt punt 1 van ADR-0009 ("geen CSS-framework, geen iconenbibliotheek").

## Peil 1.1.1 (2026-11-10)

**Context.** Na 1.1.0 zijn de Hub-UI, MediaGuard, MSO, my_house en de vijf statuspagina's in Peil gebouwd en elk
onafhankelijk gereviewd (`docs/ontwerp/PEIL_REVIEW.md` per repo). De verbeterpunten voor de ontwerptaal zelf staan
ontdubbeld in de groepen **(e)** E01–E16 van `docs/platform-v2/CONTRACT_1.3_KANDIDATEN.md`; contract 1.3 §13 verwijst
ze naar "Peil 1.1.1". Wijzigingslog: `ONTWERPTAAL.md` §16; handoff: `COMPONENTEN.md` §29.

**Besluiten** (de ontwerp-agent, binnen het mandaat; geen besluit van de gebruiker of uit dit ADR verandert):

| # | Besluit | Waarom / afgewezen alternatief |
|---|---|---|
| 1.1.1-a | **Patchversie, geen nieuw token.** Alle verbeteringen zijn regels, sjabloonplekken, klassen en tokenwaarden zonder betekeniswijziging. | §14: patch = waarde zonder betekeniswijziging, minor = nieuw token. De opdracht vraagt 1.1.1. |
| 1.1.1-b | **Rand van een gekozen domeinknop = `domain-*-ink`** (regel), geen randtoken (X-30). | Een nieuw token zou minor zijn; `-ink` haalt ≥ 7:1 en my_house Android deed het al (ADR-0010 punt 5). |
| 1.1.1-c | **`domain-heat-soft` / `domain-cool-soft` krijgen nieuwe waarden** zodat `-ink` erop ≥ 7:1 haalt (licht `#FFC3A4`/`#AAD2FF`, donker `#802300`/`#064186`; was 4,93–6,22:1) en dat paar is nu een harde AAA-eis in `contrast.mjs`. | my_house zet labels in de schemaband. Alternatief "tekst alleen op `-bg`" vroeg een markupwijziging in my_house; een waarde aanpassen werkt voor elke kopie. Zelfde OKLCH-tint, alleen de lichtheid verschoven (dataviz-skill: grafiekpalet ongewijzigd, validator opnieuw PASS). |
| 1.1.1-d | **Onleesbare koppelstatus = pil Storing "Onleesbaar"**, geen formulier (E01). | Contract 1.3 §3.5: fail-closed na een gemeten fout (geen geldig bestand); dat is gemeten falen, niet "we weten het niet". De reviews noemden Storing of Onbekend; Storing is de veiligste lezing (actie nodig). |
| 1.1.1-e | **Koppelen bezig = vlag naast de stabiele toestand** (E02), neutrale zandloper; het formulier verdwijnt in het sjabloon zelf (`NIET:koppelen_bezig` binnen `koppelformulier`). | Contract 1.3 §3.7 laat de weergave aan Peil; de regel in het sjabloon voorkomt dat een dienst het formulier vergeet te verbergen. |
| 1.1.1-f | **Totaal van een statuspagina**: verse Storing/Beperkt in het dienstblok hooguit Beperkt; niet ingesteld, beleid, feit en verouderd tellen niet; `niet-beschikbaar` telt als Onbekend (E10). | Anders wordt een pagina met één niet ingestelde functie nooit groen, of verbergt een ontbrekende functie zich achter "Alles gezond". |
| 1.1.1-g | **Offline/verouderd**: laatst bekende Gezond (en Gepauzeerd) → Verouderd; Beperkt/Storing blijven met de tijd (E15, my_house ADR-0011). | De veiligste lezing van §4.4: de waarschuwing blijft, de geruststelling verdwijnt. Alles Onbekend maken zou een bekende storing verbergen. |
| 1.1.1-h | **Ondernavigatie op labelbreedte, actief zonder extra gewicht** (E16). | Vaste kolommen van 64 px waren te smal voor "Problemen" (ook niet-vet 4 px over); vet maakte het erger. Gemeten met Noto Sans én DejaVu Sans. |
| 1.1.1-i | **Renderer in één doorgang**, met een Python-referentie die byte-gelijk getest wordt (E07). | Een waarde met `{{x}}` werd in 1.1.0 opnieuw ingevuld (bewezen in `bouw/test-sjabloon.mjs`). Waar/onwaar vastgelegd (lege lijst = onwaar) zodat JS en Python gelijk zijn. |
| 1.1.1-j | **Driftregel**: pin versie + commit-SHA; falen bij afwijking of zelfde versie met andere inhoud; nieuwere bronversie = waarschuwing (E08). | Wat drie diensten al deden; falen bij elke nieuwe versie blokkeerde CI zonder dat de kopie fout was. |
| 1.1.1-k | **Reflow ook met DejaVu Sans** (E12; contract 1.3 §13). De prototypecontrole kreeg `PEIL_LETTER` en de meting "inhoud buiten zijn element". | Die meting vond in 1.1.0-prototypes drie fouten die de paginaoverloop niet zag: de kindschil stond ≥ 1024 px in een kolom van 256 px, de kopbalk liep op 1440 px 22 px over, een lichttegel 6 px. Alle drie hersteld. |

**Kopieën in deze repo (buiten `design/ontwerptaal`, alleen tests):** de Hub (`apps/web/tests/unit/peil-tokens.test.ts`)
en de executor (`apps/executor/tests/test_statuspagina.py`) vergeleken hun kopie byte voor byte met de werkversie van de
bron; elke nieuwe Peil-versie maakte de CI van deze repo dus rood. Beide tests volgen nu de pinregel 1.1.1-j: de kopie
moet ongewijzigd zijn (sha256 van de gepinde inhoud), dezelfde bronversie moet byte-gelijk zijn, een nieuwere
bronversie geeft een waarschuwing. De Hub-UI en de executor-statuspagina zelf blijven op **1.1.0** tot hun bouwer
overstapt (kopie, renderer in één doorgang, nieuwe plekken, tests) — geen productcode gewijzigd. Buiten deze repo:
jelly-leon-platform vergelijkt in CI met de werkbranch en faalt bewust bij elke nieuwe versie
(`tests/repo/test_peil_kopie.py::test_no_drift_with_the_current_source`); die test wordt rood tot jelly-leon-platform
de regel 1.1.1-j overneemt of naar 1.1.1 overstapt.

**Niet overgenomen:** apart randtoken (zie 1.1.1-b); lettermaten 12–13 → 14 px in de my_house-aliaslaag (vraagt een
meting in de app; fase 2 van my_house regelt het); vlag van `.en-col.is-incomplete` in koelen-blauw (domeinbetekenis);
`<wbr>` in sjabloonwaarden (de renderer escapet alleen).

**Gevolgen.** Visueel verandert: de `-soft`-banden in my_house, de ondernavigatie (Hub, MSO, my_house), de
statuspagina's (rijopbouw, tonen, meldingsrol, "Hub openen" als knop) en de kindschil ≥ 1024 px. De vijf diensten
nemen de nieuwe sjabloonkopie en de één-doorgang-renderer over; tests die `role="alert"` bij een bestaande toestand of
"Verstuurd" verwachten, gaan mee. Gemeten op de 20 prototypes (6 breedtes × 2 thema's, met Noto Sans én DejaVu Sans):
axe 0, contrast AA (tekst ≥ 4,5:1; beide tekstniveaus ≥ 7:1 behalve de bewust gedempte laatst bekende waarde),
`contrast.json` 0 harde fouten. **Niet gevalideerd:** echte schermlezer, echte telefoon/tv, de statuspagina's op een
echte NAS (NIET GEVALIDEERD; alles Sim-getest in Chromium).

