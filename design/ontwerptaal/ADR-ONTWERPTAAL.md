<!-- HERKOMST — Peil 1.1.0 · bron: ontwerptaal Peil, definitief/ADR-ONTWERPTAAL.md (design-agent, 2026-11-09; wordt ai-server-manager/design/ontwerptaal/ na de overdracht) · sha256 van het bronbestand (zonder deze kop): df302eb5a727b5649dafec1c0c0d469cf5bfc39fd32b3834c415aaa805db05ac · versie-gepinde kopie (O-14), niet met de hand wijzigen. Relatieve links naar bestanden die hier niet zijn gekopieerd (prototypes/, shots/, bouw/, ../REVIEW.md) werken alleen in de bron. -->

# ADR — Eén ontwerptaal "Peil" voor het TrueNAS Home Platform V2

- **Status:** Aangenomen door de ontwerp-agent (mandaat van de gebruiker: "de ontwerp-agent beslist zelfstandig binnen
  deze kaders en legt keuzes vast"). In te nemen in `ai-server-manager/docs/adr/` als volgend ADR-nummer zodra
  ai-server-manager na "OVERDRACHT VOLTOOID" weer beschrijfbaar is.
- **Datum:** 9 november 2026 · **Versie ontwerptaal:** 1.1.0 (1.0.0 + herstellingen uit de ontwerpreview)
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
