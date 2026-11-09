<!-- HERKOMST — Peil 1.1.0 · bron: ontwerptaal Peil, definitief/ONTWERPTAAL.md (design-agent, 2026-11-09; wordt ai-server-manager/docs/platform-v2/ONTWERPTAAL.md en design/ontwerptaal/ na de overdracht) · sha256 van het bronbestand (zonder deze kop): a957370b1eceac49ab9f47204c252a67d15967ebae240eb33440b8963898d33b · versie-gepinde kopie (O-14), niet met de hand wijzigen. Relatieve links naar bestanden die hier niet zijn gekopieerd (prototypes/, shots/, bouw/, ../REVIEW.md) werken alleen in de bron. -->

# Peil — ontwerptaal van het TrueNAS Home Platform V2

**Status:** bindend voor bouwers · versie **1.1.0** (na onafhankelijke ontwerpreview, zie `../REVIEW.md`) · 9 november 2026 · eigenaar `lvos04/ai-server-manager` (bron van waarheid)
**Besluit:** [ADR-ONTWERPTAAL.md](ADR-ONTWERPTAAL.md) (vervangt ADR-0009 punt 1 van de Hub) · **Componentspecificaties:** [COMPONENTEN.md](COMPONENTEN.md)

> **Peil** — zoals een waterpeil: je ziet in één oogopslag hoe het ervoor staat, en je ziet ook eerlijk wanneer er
> geen meting is. *Op peil* betekent in orde; *geen peil op te trekken* betekent: we weten het niet, en dat zeggen we.

Peil is één ontwerptaal voor:

- de **Hub-UI** in `ai-server-manager` (Next.js 16 / React 19-PWA, alle schermen van alle nieuwe diensten);
- de **statuspagina's** (`GET /`, HTML zonder JS) van home-control-hub, appstore-en-fork, jelly-leon-platform,
  zoeken-en-persoonlijke-ai en de executor;
- het **uiterlijk** van MediaGuard, MSO en my_house. Hun functies, routes, schermstructuur en eigen teksten blijven.

Buiten de reikwijdte: Jelly Leon web en de Jelly Leon-clients, en de UI's van derden (Node-RED, Authentik, Uptime Kuma,
TrueNAS, …): de Hub verwijst daarnaar met een deep link.

Techniek (vast): **Tailwind CSS 4 + shadcn/ui (stijl new-york, CSS-variabelen) + lucide-react**. Geen CDN, geen
webfonts van internet. WCAG 2.1 AA is het minimum; beide tekstniveaus halen AAA (≥ 7:1). Uitzondering met opzet: de gedempte
laatst-bekende waarde (`--stale-ink`, ≈ 6:1 en ≥ 4,5:1 op de arcering) haalt AA.

---

## 0. Leeswijzer en bestanden

| Bestand (map `definitief/`) | Wat | Wie gebruikt het |
|---|---|---|
| `ONTWERPTAAL.md` | Dit document: regels, tokens, patronen, migratie. **Bindend.** | iedereen |
| `COMPONENTEN.md` | Handoff-specificatie per component (props, toestanden, a11y, randgevallen). | bouwers |
| `ADR-ONTWERPTAAL.md` | Het besluit, de alternatieven met juryscores en de gevolgen. | ADR-register Hub |
| `tokens.json` | **Tokenbron** in DTCG-formaat (licht = `$value`, donker in `$extensions["nl.peil"].modes.donker`). | tooling |
| `ontwerptaal.css` | Tailwind 4 `@theme` + shadcn-variabelen licht/donker + basislaag + utilities. Direct te importeren. | Hub, MediaGuard, MSO |
| `statuspagina.css` | Framework-vrije CSS voor de statuspagina's (6,7 KB, 2,3 KB gzip), uit dezelfde tokens. | 5 diensten |
| `statuspagina.html` | Sjabloon met plekken voor dienstnaam, versie, status, koppelstatus en koppelformulier (CSS en iconen inline). | 5 diensten |
| `components.json` | Advies-configuratie voor de shadcn-CLI (zie COMPONENTEN.md §1). | Hub, MediaGuard, MSO |
| `aliaslagen/*.css` | Migratiefase 1: oude variabelenamen → Peil (MediaGuard/MSO, my_house, Hub P0). | per app |
| `contrast.json` | Alle contrastparen, ΔE-scheiding, paletvalidatie en de my_house-paren — herberekend. | controle |
| `prototypes/*.html` | Elf referentieschermen (statische HTML met gecompileerde Tailwind, fictieve data), waaronder de statuspagina in twee toestanden. | referentie |
| `shots/*.png` | 88 schermafdrukken: 11 schermen × 360/390/768/1440 × licht/donker. | review |
| `controle/` | Uitkomst van de automatische controle (axe, contrast in de DOM, focus, overloop) en het gedragstest. | bewijs |
| `bouw/` | Bron (`tokens.mjs`) en alle scripts. `node bouw/alles.mjs --shots --controle` bouwt alles opnieuw. | onderhoud |

**Wijzigen gaat altijd zo:** waarde aanpassen in `bouw/tokens.mjs` → `node bouw/alles.mjs` → `contrast.json` moet
`harde_fouten: 0` geven → nieuwe versie (semver) → kopieën in de repos bijwerken (§14).

---

## 1. Principes

1. **Eerlijk boven geruststellend.** Groen betekent alleen *gemeten en goed*. Onbekend is nooit groen en nooit 0.
   Verouderde data is nooit gezond. "Verstuurd" is geen "klaar". "Ik weet het niet" is een geldige uitkomst.
2. **Status eerst.** Op elk scherm staat bovenaan hoe het ervoor staat en wat actie nodig heeft; uitleg komt daarna.
3. **Eén betekenis per kleurfamilie, en nooit alleen kleur.** Elke status heeft icoon + woord + vorm. Elke as
   (gezondheid, ernst, risico, validatie, zekerheid, gate) heeft een eigen vorm.
4. **Alleen uitzonderingen vallen op.** *Gezond* heeft geen vlak; afwijkingen krijgen een getint vlak en een rand.
5. **Leesbaar in de zon en in het donker.** Twee tekstniveaus, allebei ≥ 7:1 op elk vlak; minimaal 14 px; randen in
   plaats van schaduwen; tweekleurige focusring.
6. **Elk getal heeft eenheid, bron en tijd** — en een meetkwaliteit (gemeten / afgeleid / geschat / niet beschikbaar).
7. **Veiligheid is zichtbaar.** Vóór elke actie: preview, risico volgens het beleid, diff, back-up, controle na
   uitvoering. Wat nooit mag (boiler schakelen, data auto-verwijderen) is zichtbaar afwezig, met de reden.
8. **Consistent over alle oppervlakken.** Hetzelfde begrip heeft overal hetzelfde woord, icoon en uiterlijk: Hub,
   statuspagina's, MediaGuard, MSO en my_house (WCAG 3.2.4).

---

## 2. Grondslag en herkomst

Peil is de synthese van drie ontwerprichtingen (zie ADR). De basis is **richting C "Helder en toegankelijk eerst"**
(hoogste juryscore: 22,5 van 30), hersteld op alle blokkerende punten en aangevuld met de sterkste ideeën uit A en B:

| Uit | Overgenomen |
|---|---|
| **C** | neutrale koele basis + diep blauw; twee tekstniveaus ≥ 7:1; tweekleurige focus; statusdrieluik (icoon/vlak/inkt); vijf assen, vijf vormen; treden-glyph voor risico; diff zonder rood/groen; echte tabs die op mobiel omlopen; z-lagen en spacingrollen; aliastabel voor `--mg-*`; semantische statuspagina-klassen in hex |
| **A** | statusnamen = connectorcontract (`healthy/degraded/down/unknown`); *Gezond* zonder vlak; `--color-*: initial`; intacte Tailwind-typeschaal; grafiekpalet zonder status-tinten + "domeinkleur = grafiekslot"; statuspagina-CSS gegenereerd uit dezelfde tokens; eerlijkste statuspagina-kop ("Niet gereed — deels onbekend"); preview met vervaltijd en stappen |
| **B** | 44 px overal (ook desktop) en 320 px in de testmatrix; kaartmetadata als `dl` met `<time>`; kinderschil (Home · Kijken · Zoeken · Ik) met "Details voor een volwassene"; "Ik weet het niet" als kaart; "Dit is geen storing; er wacht een voorwaarde"; "Data verwijderen (R3) staat hier bewust niet"; kopieer-terugval zonder Ctrl+C |

Hersteld (blokkerend bij de jury): R1 is niet meer violet (R1 loopt automatisch); het grafiekpalet bevat geen groen,
rood of violet meer; statuskleuren liggen aantoonbaar ≥ 15 ΔE van elke grafiek- en domeinkleur; de my_house-aliaslaag
geeft `.light-tile.on` 8,5:1 in plaats van 1:1; focus verdwijnt nooit meer achter vaste balken (`scroll-padding`);
geen overloop op 320 px; groen wordt nergens voor "live/verbonden/klaar" gebruikt; diff zonder status-rood/groen.

---

## 3. Tokens

### 3.1 Architectuur en naamgeving

```
bouw/tokens.mjs ──► tokens.json (DTCG, bron voor tooling)
                ├─► ontwerptaal.css (Tailwind 4 + shadcn, licht :root · donker .dark/[data-theme=dark])
                ├─► statuspagina.css + statuspagina.html (hex, prefers-color-scheme)
                └─► aliaslagen/*.css (oude namen → Peil) en contrast.json (controle)
```

Regels voor namen (bindend):

- **shadcn-namen met shadcn-betekenis.** `--muted` is een *achtergrond*, `--muted-foreground` de secundaire tekst,
  `--accent` het hover-/selectievlak, `--border` decoratief, `--input` de veldrand, `--ring` de focus.
- **Status = connectorcontract.** `healthy`, `degraded`, `down`, `unknown` (+ `stale`, `info`, `paused`). API-waarde,
  tokennaam en utility zijn één op één: `status: "degraded"` → `text-degraded-ink bg-degraded-bg border-degraded`.
- **Drieluik per status:** `--<naam>` = icoon en rand (≥ 4,5:1 op vlak en kaart), `--<naam>-bg` = vlak,
  `--<naam>-ink` = tekst (≥ 7:1).
- **Risico regelmatig:** `--risk-N`, `-bg`, `-ink`, `-solid`, `-on-solid` voor N = 0…3; `--approval*` = R2.
- **Voorvoegsels:** `--chart-*` voor alles wat in een grafiek zit (ook `chart-seq-*`, `chart-div-*`,
  `chart-meter-track`), `--domain-*` voor domeinrollen. Zo botst niets met my_house (`--heat`) of MediaGuard (`--mg-*`).
- **Alleen tokenkleuren compileren.** `ontwerptaal.css` zet `--color-*: initial`; `text-red-500` en dergelijke
  bestaan niet. Wit en zwart blijven (`bg-black/50` voor overlays van shadcn).
- Geen hardgecodeerde kleuren in componenten (lintregel: geen `#hex`, `rgb(`, `oklch(` buiten het tokenbestand).

### 3.2 Kleurtokens (gegenereerd uit `bouw/tokens.mjs`)

<!-- BEGIN:tokens -->

**Oppervlakken**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--background` | `#EEF1F5` | `#0A0D12` | Paginavlak (achter kaarten). |
| `--card` | `#FFFFFF` | `#141920` | Kaart, paneel, tabel, grafiekvlak. Grafieken staan altijd op card. |
| `--popover` | `#FFFFFF` | `#1A2029` | Popover, menu, dialoog, sheet. |
| `--muted` | `#E4E8EE` | `#1E252E` | Verzonken vlak: tabelkop, codeblok, inactieve tab. (shadcn: achtergrond, géén tekstkleur.) |
| `--secondary` | `#E4E8EE` | `#232B35` | Vlak van de secundaire knop. |
| `--accent` | `#DCE3EC` | `#26303B` | Hover- en selectievlak (shadcn-betekenis), actief zijbalkitem. |
| `--sidebar` | `#FFFFFF` | `#10141A` | Zijbalk (desktop). |

**Tekst**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--foreground` | `#0B0F14` | `#F2F5F8` | Tekstniveau 1: koppen, waarden, lopende tekst. |
| `--muted-foreground` | `#353E4A` | `#C2CAD5` | Tekstniveau 2: bron, tijd, hulptekst, meta. >= 7:1 op elk oppervlak. Er is geen derde (lichtere) tekstlaag. |
| `--disabled-foreground` | `#5C6675` | `#8D97A5` | Tekst van een uitgeschakeld element; altijd met een zichtbare reden ernaast. |

**Randen**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--border` | `#AEB8C5` | `#3A4451` | Decoratieve rand (kaart, scheidingslijn). Draagt nooit betekenis. |
| `--border-strong` | `#5B6575` | `#8E99A8` | Rand die informatie draagt: knoprand, stippelrand van Onbekend/Verouderd, tabkop. >= 3:1. |
| `--input` | `#5B6575` | `#8E99A8` | Veldrand (input, select, checkbox, radio). >= 3:1 (lost Hub 1,42:1 op). |

**Merk en interactie**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--primary` | `#123DA3` | `#8DB6FF` | Primaire actie, actief navigatie-item, selectie. Betekent nooit "gezond". |
| `--primary-hover` | `#0C2F80` | `#B0CCFF` | Hover van de primaire knop. |
| `--primary-foreground` | `#FFFFFF` | `#06101F` | Tekst op primary. |
| `--link` | `#123DA3` | `#9DC0FF` | Linktekst (altijd onderstreept). |
| `--ring` | `#123DA3` | `#8DB6FF` | Focusring, buitenste 3 px (tweekleurig, WCAG-techniek C40). |
| `--ring-halo` | `#FFFFFF` | `#0A0D12` | Focusring, binnenste 2 px halo in de spleet. |
| `--destructive` | `#A8111A` | `#FF9B9B` | Gevaarlijke (R3-)knop en "Kritiek". Gelijk aan risk-3-solid. |
| `--destructive-hover` | `#8A0D15` | `#FFB8B8` | Hover van de gevaarknop. |
| `--destructive-foreground` | `#FFFFFF` | `#1F0405` | Tekst op destructive (in donker donkere tekst op lichtrood). |

**Gezondheid en info (gereserveerd)**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--healthy` | `#0F6B33` | `#6FD394` | Gezond: icoon (circle-check). Alleen "gemeten en goed" — nooit voor actueel, verbonden, nieuw of aanbevolen. |
| `--healthy-bg` | `#DFF2E5` | `#0F2B1B` | Gezond: vlak (alleen in grote statusblokken; de StatusBadge Gezond heeft géén vlak). |
| `--healthy-ink` | `#0A4A22` | `#A6EBBF` | Gezond: tekst. |
| `--degraded` | `#8F4B00` | `#F7C26A` | Beperkt: icoon (triangle-alert) en rand. |
| `--degraded-bg` | `#FCEFD6` | `#33230A` | Beperkt: vlak. |
| `--degraded-ink` | `#5E3300` | `#FBD596` | Beperkt: tekst. |
| `--down` | `#B3141B` | `#FF9B9B` | Storing: icoon (octagon-x) en rand. Alleen gemeten falen of gevaar. |
| `--down-bg` | `#FBE4E4` | `#3A1215` | Storing: vlak. |
| `--down-ink` | `#78090F` | `#FFC2C2` | Storing: tekst. |
| `--unknown` | `#566070` | `#A3ADBB` | Onbekend: icoon (circle-help) en gestippelde rand. Altijd met reden. Nooit groen, nooit 0. |
| `--unknown-bg` | `#E9EDF2` | `#222932` | Onbekend: vlak. |
| `--unknown-ink` | `#2A313B` | `#DCE2EA` | Onbekend: tekst. |
| `--info` | `#17469A` | `#8DB6FF` | Info en Gepauzeerd: icoon (info / circle-pause). |
| `--info-bg` | `#E3ECFA` | `#132440` | Info/Gepauzeerd: vlak; ook "wordt" in de diff. |
| `--info-ink` | `#0B3780` | `#C4DAFF` | Info/Gepauzeerd: tekst. |

**Versheid**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--stale` | `#566070` | `#A3ADBB` | Verouderd: icoon (clock-alert) en gestippelde rand. |
| `--stale-bg` | `#F1F3F6` | `#1A2028` | Verouderd: waardevak (onder de arcering). |
| `--stale-hatch` | `#D0D6DF` | `#2E3743` | Verouderd: arceerlijnen (135°). |
| `--stale-ink` | `#535D6C` | `#9EA8B6` | Laatst bekende waarde, bewust gedempt (≈ 6:1; >= 4,5:1 op de donkerste arceerpixel). Nooit opacity. |

**Goedkeuring en risico R0–R3**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--approval` | `#4F2BA6` | `#C4B0FF` | Menselijke goedkeuring nodig (R2, teller Goedkeuringen). Violet komt nergens anders voor, ook niet in grafieken. |
| `--approval-bg` | `#EFEAFB` | `#241B3D` | Goedkeuring: vlak. |
| `--approval-ink` | `#3B1F80` | `#D9CCFF` | Goedkeuring: tekst. |
| `--risk-0` | `#5B6575` | `#8E99A8` | R0 · Alleen lezen — rand/glyph (neutraal). |
| `--risk-0-bg` | `#F1F3F6` | `#1A2028` | R0: vlak van het woorddeel. |
| `--risk-0-ink` | `#2A313B` | `#DCE2EA` | R0: tekst. |
| `--risk-0-solid` | `#D5DBE3` | `#2E3743` | R0: codeblok. |
| `--risk-0-on-solid` | `#0B0F14` | `#F2F5F8` | R0: tekst op codeblok. |
| `--risk-1` | `#3B4452` | `#C2CAD5` | R1 · Omkeerbaar — rand/glyph (neutraal-sterk; R1 loopt automatisch binnen het beleid). |
| `--risk-1-bg` | `#E4E8EE` | `#222932` | R1: vlak van het woorddeel. |
| `--risk-1-ink` | `#0B0F14` | `#F2F5F8` | R1: tekst. |
| `--risk-1-solid` | `#3B4452` | `#C2CAD5` | R1: codeblok. |
| `--risk-1-on-solid` | `#FFFFFF` | `#0A0D12` | R1: tekst op codeblok. |
| `--risk-2` | `#4F2BA6` | `#C4B0FF` | R2 · Goedkeuring nodig — rand/glyph (= approval). |
| `--risk-2-bg` | `#EFEAFB` | `#241B3D` | R2: vlak van het woorddeel. |
| `--risk-2-ink` | `#3B1F80` | `#D9CCFF` | R2: tekst. |
| `--risk-2-solid` | `#4F2BA6` | `#C4B0FF` | R2: codeblok. |
| `--risk-2-on-solid` | `#FFFFFF` | `#170A38` | R2: tekst op codeblok. |
| `--risk-3` | `#A8111A` | `#FF9B9B` | R3 · Destructief — rand/glyph (rol gevaar). |
| `--risk-3-bg` | `#FBE4E4` | `#3A1215` | R3: vlak van het woorddeel. |
| `--risk-3-ink` | `#78090F` | `#FFC2C2` | R3: tekst. |
| `--risk-3-solid` | `#A8111A` | `#FF9B9B` | R3: codeblok. |
| `--risk-3-on-solid` | `#FFFFFF` | `#1F0405` | R3: tekst op codeblok. |

**Gate, validatie en diff**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--gate` | `#2A313B` | `#DCE2EA` | Gate dicht (wacht op voorwaarde): rand en slot-icoon. Neutraal-sterk, nooit foutrood (T-14). |
| `--gate-bg` | `#F1F3F6` | `#1A2028` | Gate dicht: vlak. |
| `--gate-ink` | `#0B0F14` | `#F2F5F8` | Gate dicht: tekst. |
| `--validation` | `#5B6575` | `#8E99A8` | Validatielabel: gestippelde rand en kolf-icoon. Nooit de stijl van "geslaagd". |
| `--validation-bg` | `#EEF1F5` | `#1E252E` | Validatielabel: vlak. |
| `--validation-ink` | `#2A313B` | `#DCE2EA` | Validatielabel: tekst (mono). |
| `--diff-old-bg` | `#E4E8EE` | `#1E252E` | Diff "was": neutraal vlak, tekst doorgestreept. |
| `--diff-old-ink` | `#353E4A` | `#C2CAD5` | Diff "was": tekst. |
| `--diff-new-bg` | `#E3ECFA` | `#132440` | Diff "wordt": info-vlak, tekst vet. Geen rood/groen. |
| `--diff-new-ink` | `#0B3780` | `#C4DAFF` | Diff "wordt": tekst. |

**Grafiek**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--chart-1` | `#2a78d6` | `#3987e5` | Reeks 1 — blauw (accentfamilie). |
| `--chart-2` | `#eb6834` | `#d95926` | Reeks 2 — oranje. |
| `--chart-3` | `#00a672` | `#199e70` | Reeks 3 — aqua. |
| `--chart-4` | `#c78600` | `#c98500` | Reeks 4 — geel/oker. |
| `--chart-5` | `#db7099` | `#d55181` | Reeks 5 — magenta. Een 6e reeks bestaat niet: vouw naar Overig of small multiples. |
| `--chart-other` | `#7D8593` | `#7A8391` | Overig — grijs, altijd met arcering en label. |
| `--chart-grid` | `#E3E6EB` | `#262E38` | Rasterlijn: haarlijn, solide, recessief. |
| `--chart-axis` | `#9AA3AF` | `#4A5462` | As/basislijn: haarlijn. |
| `--chart-label` | `#353E4A` | `#C2CAD5` | As-, legenda- en labeltekst (nooit de reekskleur). |
| `--chart-limit` | `#0B0F14` | `#F2F5F8` | Grenslijn (bijv. geheugenlimiet), met direct label. |
| `--chart-gap` | `#C9D0D9` | `#333C48` | Arcering van een gat ("Geen meting") of verouderd stuk in een grafiek. |
| `--chart-meter-track` | `#CDE2FB` | `#0D366B` | Spoor van een meter: lichtere stap van dezelfde blauwe trap (vulling/spoor >= 3:1). |
| `--chart-seq-100` | `#cde2fb` | `#0d366b` | Sequentieel blauw, stap 100 ("bijna nul"; in donker het dichtst bij het vlak). |
| `--chart-seq-200` | `#9ec5f4` | `#104281` | Sequentieel blauw, stap 200. |
| `--chart-seq-300` | `#6da7ec` | `#184f95` | Sequentieel blauw, stap 300 (ordinaal: lichtste toegestane stap in licht is 250/300). |
| `--chart-seq-400` | `#3987e5` | `#256abf` | Sequentieel blauw, stap 400. |
| `--chart-seq-500` | `#256abf` | `#3987e5` | Sequentieel blauw, stap 500. |
| `--chart-seq-600` | `#184f95` | `#6da7ec` | Sequentieel blauw, stap 600. |
| `--chart-seq-700` | `#0d366b` | `#9ec5f4` | Sequentieel blauw, stap 700 (maximum). |
| `--chart-div-neg-3` | `#1c5cab` | `#9ec5f4` | Divergerend, blauwe arm, sterkst. |
| `--chart-div-neg-2` | `#3987e5` | `#3987e5` | Divergerend, blauwe arm, midden. |
| `--chart-div-neg-1` | `#86b6ef` | `#184f95` | Divergerend, blauwe arm, zwakst. |
| `--chart-div-mid` | `#E4E8EE` | `#2A313B` | Divergerend, neutraal grijs midden ("niets"). |
| `--chart-div-pos-1` | `#f49775` | `#8a2e00` | Divergerend, oranje arm, zwakst. |
| `--chart-div-pos-2` | `#da5821` | `#da5821` | Divergerend, oranje arm, midden. |
| `--chart-div-pos-3` | `#9f3600` | `#ffa98a` | Divergerend, oranje arm, sterkst. |

**Domeinrollen (bestaande apps)**

| Token (CSS / Tailwind) | Licht | Donker | Betekenis en gebruik |
|---|---|---|---|
| `--domain-heat` | `#eb6834` | `#d95926` | Verwarmen (my_house) — icoon flame; = chart-2. |
| `--domain-heat-bg` | `#FDEBDD` | `#3A1F10` | Verwarmen: vlak (modusknop actief, chip). |
| `--domain-heat-ink` | `#6E2405` | `#FFD3B5` | Verwarmen: tekst. |
| `--domain-heat-soft` | `#F4A57E` | `#9C3D14` | Verwarmen: decoratieve band/dial (draagt geen informatie). |
| `--domain-cool` | `#2a78d6` | `#3987e5` | Koelen (my_house) — icoon snowflake; = chart-1. |
| `--domain-cool-bg` | `#E0EEFB` | `#0F2740` | Koelen: vlak. |
| `--domain-cool-ink` | `#0B3B73` | `#C2DDFA` | Koelen: tekst. |
| `--domain-cool-soft` | `#9ec5f4` | `#184f95` | Koelen: decoratieve band/dial. |
| `--domain-boiler` | `#00a672` | `#199e70` | Boiler (alleen meten) — icoon shower-head; = chart-3. |
| `--domain-boiler-bg` | `#E4E8EE` | `#1E252E` | Boiler: vlak — bewust NEUTRAAL (review 1.1.0): een aqua/mint vlak was niet te onderscheiden van healthy-bg (ΔE 0,6). De boiler is alleen meten; identiteit = icoon shower-head + woord. |
| `--domain-boiler-ink` | `#0B0F14` | `#F2F5F8` | Boiler: tekst — neutraal (groenige inkt was ΔE 2,5 van healthy-ink). |
| `--domain-light-on` | `#c78600` | `#c98500` | Licht aan — icoon lightbulb; = chart-4. |
| `--domain-light-on-bg` | `#FDF6CF` | `#3A2E0E` | Licht aan: tegelvlak (my_house .light-tile.on). |
| `--domain-light-on-ink` | `#5C4300` | `#F7DC94` | Licht aan: tekst op het tegelvlak. |
| `--domain-lane-gpu` | `#2a78d6` | `#3987e5` | MediaGuard GPU-lane (AV1); = chart-1. |
| `--domain-lane-cpu` | `#eb6834` | `#d95926` | MediaGuard CPU-lane; = chart-2. |
| `--domain-integrity` | `#0B0F14` | `#F2F5F8` | MediaGuard integriteitsslot (inkt, icoon lock). |

**Aliassen** (shadcn-afgeleiden en Gepauzeerd)

| Token | Verwijst naar | Betekenis |
|---|---|---|
| `--card-foreground` | `--foreground` | shadcn: tekst op card. |
| `--popover-foreground` | `--foreground` | shadcn: tekst op popover. |
| `--secondary-foreground` | `--foreground` | shadcn: tekst op secondary. |
| `--accent-foreground` | `--foreground` | shadcn: tekst op accent. |
| `--sidebar-foreground` | `--foreground` | shadcn sidebar: tekst. |
| `--sidebar-primary` | `--primary` | shadcn sidebar: actief item. |
| `--sidebar-primary-foreground` | `--primary-foreground` | shadcn sidebar: tekst op actief item. |
| `--sidebar-accent` | `--accent` | shadcn sidebar: hover. |
| `--sidebar-accent-foreground` | `--foreground` | shadcn sidebar: tekst op hover. |
| `--sidebar-border` | `--border` | shadcn sidebar: rand. |
| `--sidebar-ring` | `--ring` | shadcn sidebar: focus. |
| `--paused` | `--info` | Gepauzeerd (T-17): icoon circle-pause. Bewuste pauze, geen storing. |
| `--paused-bg` | `--info-bg` | Gepauzeerd: vlak. |
| `--paused-ink` | `--info-ink` | Gepauzeerd: tekst. |

**Maten, lagen en beweging** (gelijk in licht en donker)

| Token | Waarde | Betekenis |
|---|---|---|
| `--radius` | `0.5rem` | Basisradius 8 px: kaart, knop, veld. sm 4 (risicotag, code), md 6, lg 8, xl 12 (dialoog/sheet), statuspil volledig rond. |
| `--touch` | `2.75rem` | Aanraakdoel 44 px: minimum voor elk bedienbaar element op elke breedte en elke aanwijzer. |
| `--header-h` | `4rem` | Hoogte kopbalk (64 px). |
| `--bottomnav-h` | `4rem` | Hoogte ondernavigatie (64 px) zonder safe area. |
| `--sidebar-w` | `16rem` | Breedte zijbalk (256 px) vanaf lg (1024 px). |
| `--content-max` | `76rem` | Maximale inhoudsbreedte (1216 px). |
| `--prose-max` | `65ch` | Maximale regellengte lopende tekst (65 tekens; gelijk aan Tailwind max-w-prose — review 1.1.0: was 40rem, maar max-w-prose compileert altijd naar 65ch). |
| `--focus-width` | `3px` | Focusring buitenrand. |
| `--focus-offset` | `2px` | Focusring spleet (gevuld met ring-halo). |
| `--z-sticky` | `20` | Sticky actiebalk, tabelkop. |
| `--z-nav` | `30` | Kopbalk, ondernavigatie. |
| `--z-overlay` | `40` | Dialoog, sheet, popover. |
| `--z-toast` | `50` | Toast (Sonner). |
| `--duration-fast` | `150ms` | Kleur/hover, chevron. |
| `--duration-medium` | `200ms` | Sheet/dialoog in- en uitschuiven. |
| `--ease-standard` | `cubic-bezier(0.2, 0, 0, 1)` | Standaard easing. |
| `--ease-exit` | `cubic-bezier(0.4, 0, 1, 1)` | Uitgaande beweging. |

<!-- END:tokens -->

### 3.3 Contrast (herberekend, `contrast.json`)

<!-- BEGIN:contrast -->
| | Licht | Donker |
|---|---|---|
| Gemeten paren | 154 | 154 |
| Tekstparen ≥ 7:1 (AAA) | 59 van 59 | 59 van 59 |
| Laagste AAA-tekstpaar | 7.31:1 | 7.28:1 |
| UI- en icoonparen gehaald | 68 van 68 | 68 van 68 |
| Laagste ΔE grafiek/domein ↔ status/risico (eis ≥ 15) | 16.1 | 16.1 |
| Laagste ΔE status onderling (eis ≥ 10) | 10.8 | 11.1 |
| Harde fouten | 0 | |
| Brede matrix (review 1.1.0): paren per thema, harde fouten | 367 · 0 | 367 · 0 |
| Brede matrix: laagste tekstpaar | stale-ink op stale-hatch 4.56 | domain-heat-ink op domain-heat-soft 4.93 |

<!-- END:contrast -->

Daarnaast meet `bouw/controle.cjs` het contrast **in de gerenderde DOM** (met opacity, color-mix en de donkerste
arceerpixel) op alle elf prototypes, 6 breedtes × 2 thema's: 0 tekstfouten; laagste tekst 4,56:1 licht en 5,01:1
donker — dat is de bewust gedempte laatst-bekende waarde op de arcering (`--stale-ink`, eis ≥ 4,5:1).

---

## 4. Kleursemantiek

### 4.1 Betekenisregels (gelden overal, ook in statuspagina's en de bestaande apps)

1. **Groen (`healthy`) = gemeten en goed.** Nooit voor actueel, live, verbonden, klaar, nieuw, aanbevolen, beschikbaar,
   gemeten (kwaliteit) of "stap klaar".
2. **Rood (`down`, `risk-3`, `destructive`) = gemeten falen of gevaar.** Nooit voor "nog niet ingevuld", "wacht op
   voorwaarde" (dat is de gate), "verwijderd" in een diff of een teller.
3. **Amber (`degraded`) = beperkt.** Alleen gezondheid (en de meter boven de drempel). Niet voor risico, niet voor ernst.
4. **Grijs + stippel (`unknown`, `stale`) = onbekend of verouderd, altijd met reden.**
5. **Blauw (`primary`, `info`) = interactie en informatie** (knoppen, links, focus, selectie, Gepauzeerd, "wordt" in
   de diff). Blauw betekent nooit gezond.
6. **Violet (`approval`, `risk-2`) = een mens moet goedkeuren.** Komt nergens anders voor, ook niet in grafieken.
7. **Neutraal-sterk (`gate`, `risk-1`) = voorwaarde of beleid.** Een dichte gate is een slot op neutraal, geen fout.
8. **Statuskleuren zijn nooit een grafiekreeks**, en grafiek-/domeinkleuren nooit een status. Gemeten scheiding: ΔE ≥ 15.
9. **Een tint (`-bg`) draagt nooit alleen betekenis** (pastels lijken op elkaar): altijd icoon + woord + vorm. Gemeten
   (review 1.1.0): `domain-heat-bg` ligt ΔE 1,6 van `degraded-bg`, `domain-light-on-bg` 2,3, `domain-cool-bg` 0,6 van
   `info-bg`. Daarom heeft `domain-boiler-bg/-ink` sinds 1.1.0 een **neutrale** waarde (de mintgroene versie was ΔE 0,6
   van `healthy-bg`: een boilerchip zag eruit als "Gezond", terwijl de boiler alleen meten is).
10. **Nooit opacity op tekst** om iets te dempen. Gedempt = `--stale-ink` of `--disabled-foreground`.
11. **Domein- en grafiekkleuren zijn nooit tekst en nooit een icoon op hun eigen vlak.** Op `card`/`popover` halen ze
    ≥ 3:1 (icoon, lijn, staaf, legendablokje); op het eigen `-bg`-vlak niet (licht 2,7–2,8:1). Een icoon of tekst op
    `domain-*-bg` krijgt `domain-*-ink`. Tekst naast een reekskleur staat in de teksttokens.
12. **Formulierfouten zijn rood, "nog niet ingevuld" niet.** Een veldfout ná een verzendpoging (`aria-invalid`,
    foutsamenvatting) gebruikt `down`/`octagon-x`. Een besluit of instelling die nog open staat, is een neutrale toestand
    (BesluitStatusBadge "Open") — nooit rood, ook niet in een lijst of teller.

### 4.2 Vijf assen, vijf vormen

| As | Vorm | Woorden | Tokens | Component |
|---|---|---|---|---|
| **Gezondheid** (T-01) | **pil** (volledig rond), 2 px rand, icoon links | Gezond · Beperkt · Storing · Onbekend (+ reden) | `healthy/degraded/down/unknown` | StatusBadge |
| **Versheid** (T-02) | pil met **arcering** + stippelrand; waarde in gearceerd vak | Verouderd · laatst bekend 10:12 | `stale*` | Versheid, StatKaart |
| **Risico** (T-04) | **hoekige tag** (4 px): codeblok met **treden-glyph** + woord | R0 · Alleen lezen · R1 · Omkeerbaar · R2 · Goedkeuring nodig · R3 · Destructief — extra bevestiging | `risk-0…3` | RisicoBadge |
| **Ernst** (T-20) | rechthoek met **dikke linkerbalk** (6 px), alleen tekst | Kritiek · Hoog · Middel · Laag · Info | `destructive`, `down`, `foreground`, `unknown`, `info` | ErnstLabel |
| **AI-zekerheid** (T-24) | ovaal met **3 stippen** (gevuld = zekerder), inkt | Zeker · Waarschijnlijk · Onzeker · Ik weet het niet | `foreground` | ZekerheidLabel |
| **Validatie** (T-07) | **gestippelde rechthoek, mono**, kolf-icoon | Sim-getest · Unit-getest · NIET GEVALIDEERD (echte NAS) … | `validation*` | Validatielabel |
| **Gate / beleid** (T-14, T-15) | rechthoek met **inktrand** + slot (gate) of verbodsteken (beleid) | Gate dicht · Alleen meten | `gate*` | GateBlokkade, BeleidsSlot |

### 4.3 Gezondheid (T-01) en totaalstatus (T-01a)

| Status | API | Icoon (lucide) | Vorm | Tekst | Vlak | Rand |
|---|---|---|---|---|---|---|
| Gezond | `healthy` | `circle-check` | pil **zonder vlak** | `healthy-ink` | — | transparant |
| Beperkt | `degraded` | `triangle-alert` | pil | `degraded-ink` | `degraded-bg` | `degraded` 2 px |
| Storing | `down` | `octagon-x` | pil | `down-ink` | `down-bg` | `down` 2 px |
| Onbekend | `unknown` | `circle-help` | pil, **stippelrand** | `unknown-ink` | `unknown-bg` | `unknown` 2 px gestippeld |
| Verouderd | (stale → status `unknown`) | `clock-alert` | pil, stippel + arcering | `unknown-ink` | arcering | `stale` 2 px gestippeld |
| Gepauzeerd | (bewuste pauze) | `circle-pause` | pil | `paused-ink` | `paused-bg` | `paused` 2 px |
| Niet gekoppeld | — | `link-2-off` | als Onbekend | | | |
| Gekoppeld | — | `link-2` | pil, **neutraal** (verbonden is geen gezondheid) | `foreground` | — | `border-strong` |
| Niet beschikbaar | `capability_unavailable` | `circle-slash` | als Onbekend, altijd met bron en code | `unknown-ink` | `unknown-bg` | `unknown` 2 px gestippeld |
| Nog niet beschikbaar | (fase) | `hourglass` | pil, neutraal | `foreground` | `muted` | `border-strong` |

**Totaalstatus** = slechtste onderdeel: Storing > Beperkt > (Deels) Onbekend > Gezond. *Alles gezond* alleen als
**alles vers én gezond** is. Gepauzeerd en Nog niet beschikbaar verslechteren de totaalstatus niet, maar staan wel in de
telling ("1 gepauzeerd · 1 nog niet beschikbaar"). Een 200-antwoord zonder afgeronde taken is **Beperkt** (E-15).
De rand van het SysteemStatusPaneel volgt de totaalstatus (2 px, gestippeld bij onbekend).

### 4.4 Versheid (T-02) en meetkwaliteit (T-03)

- Elke waarde heeft **Bron** + **Bijgewerkt** (absolute tijd + relatief, `<time datetime>`), en bij een mislukte
  ophaalpoging ook **Geprobeerd** ("11:56 (geen antwoord)"). Versheid zelf is **nooit groen** en nooit een badge
  "Actueel": een verse waarde heeft gewoon een recente tijd.
- **Verouderd** (ouder dan de TTL): de laatst bekende waarde blijft staan in een **gearceerd waardevak** met
  stippelrand, label **"Laatst bekend · 10:12"** direct boven de waarde, waarde in `--stale-ink` (≈ 6:1) en gewoon
  gewicht, sr-only "(laatst bekende waarde, verouderd)". Status wordt **Onbekend**, bediening gaat uit met de reden
  ernaast, de kaartrand wordt gestippeld. Grafieken tonen een **gat** na het laatste meetpunt.
- **Afgeleide kaarten erven de versheid van hun bron** (energie uit my_house is verouderd als my_house dat is).
- **Nog nooit gemeten**: "Onbekend — nog nooit gemeten"; geen waarde, geen 0, geen "—".
- Meetkwaliteit staat **inline naast het getal**: `· gemeten`, `· afgeleid`, `≈ 30 s · geschat`, `· niet beschikbaar`.
  De "≈" hoort bij de waarde; schermlezer: "ongeveer". In grafieken: geschat = stippellijn / 45°-arcering.

### 4.5 Risico R0–R3 (T-04)

| Klasse | Woord (vast) | Codeblok | Woorddeel | Gedrag (contract §8) |
|---|---|---|---|---|
| **R0** | Alleen lezen | lichtgrijs + inkt, 1 trede | `risk-0-bg` | geen bevestiging |
| **R1** | Omkeerbaar | donkergrijs + wit, 2 treden | `risk-1-bg` | automatisch binnen allowlist/cooldown; handmatig = AlertDialog |
| **R2** | Goedkeuring nodig | **violet** + wit, 3 treden | `risk-2-bg` | goedkeuring per gebonden diff (≤ 15 min) |
| **R3** | Destructief — extra bevestiging | **rood** + wit, 4 treden | `risk-3-bg` | goedkeuring + aparte tweede stap met getypte bevestiging |

- Het woord staat er **altijd** bij (nooit los "R1"); in krappe tabellen mag het codeblok alleen met het woord in de
  toegankelijke naam en een uitlegpopover — niet als enige plek.
- **De policy bepaalt de klasse.** Wijkt de AI-claim af, dan staat in "Waarom R2?": *"AI stelde R1 voor; het beleid
  bepaalt R2."* met `rule_ids` en `policy_hash`.
- Een dichte gate maakt een R2/R3-actie zichtbaar-maar-geblokkeerd (knop `aria-disabled` + slot + reden).

### 4.6 Overige assen

- **Gate dicht (T-14):** slot + neutraal-sterk; tekst "Geblokkeerd door de <gate> — nodig: …" + werkwoordknop; zin
  *"Dit is geen storing; er wacht een voorwaarde."* **Geen "toch doorgaan".** Dicht door gemeten falen (T-14a): rol
  `down` + link naar het incident. Gate onbekend (T-14b): rol `unknown`; onbekend opent nooit.
- **Beleid / altijd geblokkeerd (T-15):** verbodsteken `ban` + "Alleen meten — schakelen is altijd uitgeschakeld
  (beleid)". Het element staat waar de gebruiker de knop zou zoeken, maar **er is geen schakelaar in de DOM**.
- **Validatie (T-07):** gestippelde mono-tag met exact de strings uit §11 van het contract. Tonen in "Over deze Hub" en
  bij hardwaregevoelige functies en uitvoerders (bijv. in de actie-preview: "Executor 0.1.0 [NIET GEVALIDEERD (echte
  NAS)]"), niet op elk scherm (O-12).
- **Diff (C-20):** **geen rood/groen**. *Was* = neutraal vlak, doorgestreept; *wordt* = info-vlak, vet; tekens
  `~ + −` in een kader + legenda; schermlezer: "Gewijzigd/Toegevoegd/Verwijderd".
- **Verbinding:** live-indicator is **neutraal** (`radio`, "Live · 11:56"); onderbroken = stippelpil `unknown`
  ("Onderbroken · stand van 11:56"); offline = banner `unknown` + mutaties uit. Nooit groen.
- **Tellers:** Problemen = inktpil; Goedkeuringen = violet. Nooit rood; altijd met naam in de toegankelijke tekst.
  Mobiel (< 1024) staat de goedkeuringsteller op **Meer** (eerste item van de Meer-sheet: Goedkeuringen), niet op AI:
  goedkeuringen komen ook van gebruikers en flows. Op de goedkeuringspagina is Meer het huidige item.

---

## 5. Typografie

- **Lettertype: systeemfont** (`system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", …`) en **systeem-mono**.
  0 KB, CSP-veilig, identiek bruikbaar op de statuspagina's. Inter, Space Grotesk en JetBrains Mono vervallen in de
  bestaande apps (de fontsource-pakketten kunnen weg in fase 3). Op Android (hoofdapparaat) is dit Roboto.
- **Schaal: Tailwind-standaard, ongewijzigd** (shadcn-documentatie blijft kloppen). Rollen:

| Rol | Utility | Maat / regel | Gewicht | Gebruik |
|---|---|---|---|---|
| meta | `text-sm` | 14/20 | 400 | bron, tijd, hulptekst, tabelcel, badge — **kleinste tekst** |
| body | `text-base` | 16/24 | 400 | lopende tekst, knop, veld (≥ 16 px: geen iOS-zoom) |
| titel | `text-lg font-semibold` | 18/28 | 600 | kaarttitel, h3 |
| sectie | `text-xl font-bold` | 20/28 | 700 | h2 |
| pagina | `text-2xl font-bold` | 24/32 | 700 | h1, één per pagina |
| waarde | `text-3xl font-semibold` | 30/36 | 600 | kaartwaarde (proportionele cijfers) |
| held | `text-5xl font-semibold` | 48 | 600 | één heldwaarde per scherm (zelden) |

- `text-xs` (12 px) is **verboden** voor inhoud (lintregel). shadcn-componenten met `text-xs` worden `text-sm`.
- **Cijfers:** `cijfers` (tabular-nums) voor kolommen, tijden in lijsten, aftellers en assen; grote losse waarden
  houden proportionele cijfers (dataviz-regel).
- Getal en eenheid met een **vaste spatie** (`42&nbsp;°C`, `1,2&nbsp;TiB`); eenheid in `text-muted-foreground`.
- Geen kapitalen-labels (behalve de vaste validatiestrings). Koppen `text-wrap: balance`, tekst `pretty`.
  Regellengte lopende tekst ≤ `max-w-prose` (Tailwind-standaard **65ch**; `--prose-max` is sinds 1.1.0 ook 65ch).
- Codes, `action_id`, trace-ID, digests en paden in mono; ze breken waar nodig (`overflow-wrap: anywhere`) en worden
  **nooit** afgekapt met een ellips. Paden: `<wbr>` na elke `/`.

---

## 6. Ruimte, raster, breekpunten en vorm

- **Ruimte:** Tailwind-basis 4 px. Kaartpadding `p-4` (16), gaten tussen kaarten `gap-4`, tussen secties `gap-6`/`mb-6`.
  Benoemde maten: `touch` 44 px, `header` 64, `bottomnav` 64, `sidebar` 256, `content` 1216 (max inhoud), `prose` 65ch (`max-w-prose`).
- **Aanraakdoelen: 44 × 44 px voor elk bedienbaar element op elke breedte en elke aanwijzer** (`min-h-touch`,
  `size-touch`; knoppen `h-11`). Uitzondering alleen voor inline links in lopende tekst (WCAG 2.5.8). Een radio of
  checkbox is 20 px zichtbaar binnen een label-rij van ≥ 44 px. Tussenruimte ≥ 8 px.
- **Radius:** `--radius` 8 px (kaart, knop, veld) · `sm` 4 (risicotag, code, kbd) · `md` 6 · `xl` 12 (dialoog, sheet)
  · statuspil volledig rond. Status (rond) en risico (hoekig) verschillen ook in vorm.
- **Randen in plaats van schaduwen.** Kaart: 1 px `border`. Betekenisdragende randen 2 px (`border-strong`, status).
  Schaduw (`shadow-md/lg`) alleen voor zwevende lagen (popover, dialoog, sheet, toast).
- **Z-lagen:** sticky 20 (actiebalk, tabelkop) · nav 30 (kopbalk, ondernavigatie) · overlay 40 · toast 50. Als utility:
  `z-sticky`, `z-nav`, `z-overlay`, `z-toast` (sinds 1.1.0 via `--z-index-*` in `@theme`; in 1.0.0 bestonden ze niet).
- **Tailwind 4-valkuil:** `translate-*`/`-translate-*` zetten de CSS-eigenschap `translate`, niet `transform`. Corrigeer
  een positie dus met `translate:` (of een andere utility), nooit met inline `transform:` — dat telt dan op (gevonden in
  de grafieklabels van 1.0.0).
- **Breekpunten:** Tailwind-standaard (sm 640, md 768, lg 1024, xl 1280, 2xl 1536). Testbreedtes: **320, 360, 390,
  768, 1024, 1440**.

| Breedte | Schil | Indeling |
|---|---|---|
| < 640 | ondernavigatie 5 items + live-regel onder de kop | 1 kolom; statusrijen: naam + badge op één regel, reden eronder; knoppen volle breedte; tabs lopen om; diff "Was/Wordt" onder elkaar; vaste actiebalk boven de ondernavigatie (wizard) |
| 640–1023 | ondernavigatie | 2 kolommen kaarten vanaf `md`; tabellen ≤ 4 kolommen, anders kaartenlijst; tabs als rij met onderstreping vanaf `md` |
| 1024–1279 | zijbalk 9 items + kopbalk (zoekknop als icoon, live-regel onder de kop) | 2 kolommen |
| ≥ 1280 | zijbalk + kopbalk met Zoeken (Ctrl K), live, Problemen, Goedkeuringen, AI, gebruiker | 3 kolommen kaarten; master-detail (Problemen 24 rem + detail); systeemstatus en "Wat actie nodig heeft" naast elkaar; preview met zijkolom |

- **Scroll-padding** (in `ontwerptaal.css`): boven = kopbalk + 12 px, onder = ondernavigatie (+ actiebalk via
  `[data-actiebalk]`) + safe area + 12 px. Daardoor verdwijnt focus nooit achter een vaste balk (WCAG 2.4.11).
- **Beweging:** 150 ms kleur/hover, 200 ms sheet/dialoog, `--ease-standard`; geen decoratieve animatie; aftellers en
  live-grafieken bewegen niet; alles ≈ 0 bij `prefers-reduced-motion`. De focusring wordt nooit geanimeerd.

---

## 7. Iconografie (lucide-react)

- Maat 20 px in knoppen en titels (`size-5`), 18 px in badges, 16 px inline, 24 px in de ondernavigatie; stroke 2.
  Iconen naast tekst zijn `aria-hidden`; een icoonknop heeft een `aria-label` dat overeenkomt met het zichtbare doel.
- Statuspagina's gebruiken **exact dezelfde lucide-paden** als inline `<symbol>` (`statuspagina.html`).
- **Vaste koppeling** (één icoon per betekenis, nergens anders voor gebruiken):

| Betekenis | Icoon | | Betekenis | Icoon |
|---|---|---|---|---|
| Gezond | `circle-check` | | Gate dicht | `lock` |
| Beperkt | `triangle-alert` | | Beleid (altijd geblokkeerd) | `ban` |
| Storing / fout | `octagon-x` | | Niet beschikbaar (capability) | `circle-slash` |
| Onbekend | `circle-help` | | Validatie | `flask-conical` |
| Verouderd | `clock-alert` | | Back-up / herstelpunt | `database-backup` |
| Gepauzeerd | `circle-pause` | | Terugzetten | `rotate-ccw` |
| Info | `info` | | Controle na uitvoering | `list-checks` |
| Niet gekoppeld | `link-2-off` | | Diff / voorstel bekijken | `file-diff` |
| Nog niet beschikbaar | `hourglass` | | Goedkeuring(en) | `clipboard-check` |
| Live (neutraal) | `radio` | | AI / actor AI | `bot` |
| Offline / onderbroken | `wifi-off` | | Systeem (actor) | `server` |
| Handmatig (override) | `hand` | | Gebruiker (actor) | `user` |
| Deep link (nieuw tabblad) | `square-arrow-out-up-right` | | Flow (actor) | `workflow` |
| Kopiëren | `copy` | | Bewijs | `file-search` |
| Verwarmen | `flame` | | Koelen | `snowflake` |
| Boiler | `shower-head` | | Licht aan / uit | `lightbulb` / `lightbulb-off` |
| Energie | `zap` | | Temperatuur | `thermometer` |
| Integriteit (MediaGuard) | `file-lock` | | GPU / CPU | `microchip` / `cpu` |

- Navigatie: Home `house`, Server `server`, Media `clapperboard`, Huis `sofa`, Netwerk `network`, Automatisering
  `workflow`, Backups `database-backup`, Kosten `wallet`, Instellingen `settings`; mobiel Apps `layout-grid`,
  Problemen `inbox`, AI `bot`, Meer `menu`; kind: Kijken `monitor-play`, Ik `smile`.
- **Nooit** `circle-check` voor een openstaande taak (MediaGuard "Wacht op jou"), **nooit** `pause` voor onbekend.

---

## 8. Componentenbibliotheek

### 8.1 shadcn/ui-basis (new-york, met Peil-aanpassingen)

Te installeren (Hub eerst): Button, Badge, Card, Alert, AlertDialog, Dialog, Sheet, Tabs, Table, Input, Textarea,
Select, RadioGroup, Checkbox, Switch, Label, Form-patroon, InputOTP, Tooltip, Popover, HoverCard, DropdownMenu, Command,
Sidebar (block), Breadcrumb, Skeleton, Progress, Sonner, Accordion, Collapsible, ScrollArea, Pagination, ToggleGroup,
Separator, Kbd, Chart (recharts). De verplichte aanpassingen (codemod) staan in COMPONENTEN.md §1:

- focus: `outline-none` + `ring-ring/50` vervangen door de tweekleurige Peil-ring (de shadcn-ring haalt 2,3–3,2:1);
- maten: alles ≥ 44 px (`h-11`/`size-11`); `text-xs` → `text-sm`;
- `destructive`: `text-white` → `text-destructive-foreground` (donker thema: donkere tekst op lichtrood);
- `dark:bg-input/30` en andere opacity-trucs op vlakken verwijderen; velden `border-input` (≥ 3:1);
- R2/R3-goedkeuring **nooit** in een (kleine) Dialog: een eigen pagina (mobiel én desktop).

### 8.2 Platformcomponenten

Volledige specificatie (props, varianten, toestanden, a11y, randgevallen, beweging) in **COMPONENTEN.md**. Overzicht:

| Component | ID | Kern |
|---|---|---|
| **StatusBadge** | C-03 | gezondheid als pil: icoon + woord; Gezond zonder vlak; Onbekend gestippeld + altijd reden ernaast |
| **SysteemStatusPaneel** | C-04 | totaalstatus (slechtste), telling, rijen als één link met zichtbare reden op elke breedte |
| **StatKaart** | C-05 | label · waarde + eenheid · meetkwaliteit · status · bron · bijgewerkt · (geprobeerd) · één vervolglink |
| **Versheid / LaatstBijgewerkt** | C-06 | `dl` met Bron / Bijgewerkt / Geprobeerd, `<time>`; verouderd-vak |
| **Meetkwaliteit** | C-07 | `· gemeten` · `≈ … · geschat` |
| **RisicoBadge** | C-08 | hoekige tag, treden-glyph, codeblok + woord, "Waarom Rx?" |
| **Validatielabel** | C-09 | gestippelde mono-tag + kolf |
| **GateBlokkade** | C-11 | slot, voorwaarden, werkwoordknop, nooit "toch doorgaan" |
| **NogNietBeschikbaar / CapabilityUnavailable** | C-12 | fase-paneel; `capability_unavailable` met code, bron, endpoint; knop uit met reden |
| **BeleidsSlot** | C-13 | "Alleen meten", geen schakelaar in de DOM |
| **Foutpaneel** | C-14 | wat + waarom + wat je kunt doen, code, kopieerbare trace-ID, reikwijdte, Opnieuw proberen, technische details |
| **KopieerVeld** | C-15 | mono, selecteerbaar, omlopend; clipboard alleen in secure context, anders selecteren + melding |
| **VerbindingsBanner** | C-16 | offline / Hub weg / live onderbroken / weer verbonden (`role=status`) |
| **Leeg / Onbekend / Laden** | C-17/18 | lege toestand met reden; skelet alleen eerste keer; verversen houdt de vorige weergave |
| **ActiePreview** | C-19 | samenvatting, diff, blast radius, back-up, terugzetten, controle na uitvoering, gebonden velden, vervaltijd, stappen |
| **DiffWeergave** | C-20 | `~ + −`, Was/Wordt, geen rood/groen |
| **GoedkeuringsPagina** | C-21 | R2 één stap, R3 twee stappen met getypte bevestiging, vervallen door tijd/drift |
| **Aftellen** | C-22 | zichtbaar elke seconde, aankondiging alleen op drempels |
| **UitvoeringsTijdlijn / Incidenttijdlijn** | C-23/25 | stappen of gebeurtenissen met tijd, actor, bron |
| **IncidentRij, ErnstLabel, IncidentStatus** | C-27 | inbox |
| **BewijsPaneel** | C-28 | "Toon bewijs (n)", logtekst als geciteerde data |
| **HypotheseKaart, ZekerheidLabel, IkWeetHetNiet** | C-29 | stippen + woord; "Ik weet het niet" als volwaardige kaart |
| **Grafiek, Meter, Sparkline, Segmentbalk** | C-32 | dataviz-regels (§10) |
| **ResponsieveLijst, DekkingsMatrix, FilterBalk** | C-33…35 | tabel ≥ 768, kaartenlijst < 768 |
| **Wizard / Wizardstap, SchemaFormulier, DatasetKiezer, GeheimVeld** | C-36…39 | stap n van m, foutsamenvatting, bron per veld, geen standaardantwoord |
| **Huis-set** (KamerKaart, ApparaatBediening, KlimaatZone, BoilerKaart, Energieverdeling) | C-43…46 | readback in twee fasen, conflict = gate, handmatig = hand |
| **AppSchil** (zijbalk, kopbalk, ondernavigatie, Meer-sheet, kindschil) | C-01 | §6 en COMPONENTEN.md |

---

## 9. Patronen per schermtype

| Schermtype | Schermen (EISEN §5) | Patroon | Prototype |
|---|---|---|---|
| **Overzicht / Home** | S-020, S-030, S-120, S-130 | PageHeader met bijgewerkt + Vernieuwen → SysteemStatusPaneel → "Wat actie nodig heeft" (storing > goedkeuring > gate > onboarding) → kaartraster (StatKaarten). Vernieuwen elke 60 s zonder skeletflits. | `home.html` |
| **Offline / bron weg** | S-007, T-10, T-10d | banner `unknown` bovenaan; elke kaart is verouderd: gestippelde rand, badge "Verouderd · stand van …" én de waarde in het gearceerde vak (§4.4), nooit een verse look; **lezen blijft** (bekijken, openen: "Toont de stand van 11:52"), **alles wat iets wijzigt of een verse server nodig heeft staat uit** (`aria-disabled` + reden: testen, wizard, vernieuwen, goedkeuren) — hetzelfde label heeft op één scherm altijd dezelfde toestand; nog nooit opgehaald = Onbekend, geen skelet; niets in een wachtrij; na herstel eerst ophalen, dan pas vers. Bron weg = per kaart, geen globale banner. | `offline.html` |
| **Lijst** | S-040, S-050, S-062, S-110, S-140 | FilterBalk (één rij, periode eerst; mobiel als sheet) + resultaatteller `aria-live` + ResponsieveLijst + cursor "Meer laden". Rij = één link; badges aan rij-einde. | `problemen.html` |
| **Master-detail** | S-050/S-051, S-062/S-063 | ≥ 1280: lijst 24 rem + detail; kleiner: aparte schermen (het statische prototype toont op mobiel lijst en detail onder elkaar, alleen om beide te laten zien). Geselecteerde rij `aria-current`, 2 px primaire rand. | `problemen.html` |
| **Incident** | S-051 | feiten (`dl`) → acties R0 + deep link → hypothese met zekerheid + "Toon bewijs" → "Ik weet het niet" → Al geprobeerd → stappenplan (runbook; genummerd, huidige stap) → tijdlijn → geschoonde gegevens kopiëren. | `problemen.html` |
| **Detail met tabs** | S-041…S-045, S-080 | kruimelpad (44 px), kop met status + reden, digest-kopieerveld, metadata (`dl`), deep link; vijf tabs; maximaal één tabrij per pagina. | `appdetail*.html` |
| **Metrieken** | S-031, S-042 | periodekiezer boven alles (één rij) → stattegels (één met capability_unavailable) → grafiek met gat, grens en tabel. | `appdetail-metrieken.html` |
| **Actie → goedkeuring → uitvoering** | S-061, S-063, S-064 | ActiePreview (preview, "Waarom R2?", diff, zijkolom veiligheid, stappen) → Goedkeuring aanvragen → GoedkeuringsPagina (R3: stap 2 met typen) → UitvoeringsTijdlijn → "Uitgevoerd en gecontroleerd" of "Gestopt: afwijking" + incident. | `appdetail.html`, `goedkeuring-r3.html` |
| **Wizard / onboarding** | S-010…S-016, S-047, S-125, S-143 | stepper (stap n van m, `aria-current="step"`) → h2 "Stap n van m: …" → foutsamenvatting (`role=alert`, links naar velden) → velden met bron-tag → vaste actiebalk (Vorige · Opslaan en later verder · Volgende: <stap>); < 640 px vervalt "Opslaan en later verder" uit de balk omdat waarden automatisch bewaard worden (de infomelding zegt dat). Gates als kaartenlijst op mobiel (K1). | `wizardstap.html` |
| **Formulier** | S-011, S-012, S-160… | label boven veld, hulptekst via `aria-describedby`, veldfout bij het veld + samenvatting; ja/nee zonder standaard; nullable met "onbekend"; geheimen nooit vooringevuld; plaatshouders nooit als voorbeeldwaarde. | `wizardstap.html` |
| **Dekking / matrix** | S-036, S-120, S-163 | echte tabel ≥ 768 (`th scope`), per rij een kaart < 768; cel = symbool + woord (ja / nee / onbekend / n.v.t.). | (spec) |
| **Huis / bediening** | S-090…S-096 | KamerKaart: waarden met kwaliteit, doel ± met `output`, readback "Verstuurd — wacht op bevestiging" → "Bevestigd" / "Niet bevestigd — er is niets gewijzigd"; verouderd = bediening uit; klimaatconflict = gate; handmatig = hand + Hervatten; boiler alleen meten. | `huis.html` |
| **AI-chat** | S-060 | bericht met citaten; zekerheid; "Ik weet het niet" + wat er nodig is; "Repareer" → ActiePreview; logtekst altijd als geciteerde data; tegoed-meter. | (spec) |
| **Kinderprofiel** | S-021, S-087 | eigen schil (Home · Kijken · Zoeken · Ik), kindertaal, geen codes in beeld; trace-ID achter "Details voor een volwassene". | `kind.html` |
| **Fout- en toegangspagina's** | S-005, S-006, S-007 | Foutpaneel op paginaniveau met terugweg; 403: "Je rol (*kijker*) kan dit scherm niet gebruiken."; 502: waar je anders kijkt (Uptime Kuma, Gotify, TrueNAS-logs). | (spec) |
| **Statuspagina** | §6 | sjabloon (§13.2). | `statuspagina.html` |

---

## 10. Grafieken (dataviz-procedure)

**Vorm eerst.** Eén huidige waarde → **StatKaart** (geen grafiek). Verhouding tot een grens → **meter**. Trend →
**lijn** (één y-as, nooit twee). Eén geheel in ≤ 6 delen naast de cijfers → **dunne segmentbalk + tabel**. Vergelijken
of rangschikken → **liggende staven**, één tint. Meer dan ±7 klassen → tabel.

**Categorisch palet** — vaste volgorde, nooit cyclisch, kleur volgt de entiteit (nooit de rangorde):

| Slot | Tint | Licht | Donker | Domein (kleur volgt de entiteit) |
|---|---|---|---|---|
| 1 | blauw | `#2a78d6` | `#3987e5` | koelen · GPU-lane |
| 2 | oranje | `#eb6834` | `#d95926` | verwarmen · CPU-lane |
| 3 | aqua | `#00a672` | `#199e70` | boiler |
| 4 | geel/oker | `#c78600` | `#c98500` | licht aan |
| 5 | magenta | `#db7099` | `#d55181` | — |
| Overig | grijs + arcering | `#7D8593` | `#7A8391` | rest / "Overig" |

- Gekozen volgens de skill: de gevalideerde referentietinten **zonder groen en rood** (status) en **zonder violet**
  (goedkeuring). In licht staan aqua, geel en magenta op dezelfde tint en chroma één stap donkerder (hoogste L met
  ≥ 3:1 op `--card`) — daardoor halen **alle vijf ≥ 3:1** en is geen reliefregel nodig. Donker: referentiewaarden.
- Volgorde: alle 24 volgordes met blauw eerst doorgerekend (`bouw/palet-orde.mjs`); twee halen alles in beide thema's;
  gekozen is blauw · oranje · aqua · geel · magenta (oranje op slot 2 = verwarmen; het alternatief zou verwarmen
  magenta maken).
- Een 6e reeks bestaat niet: vouw naar **Overig** (grijs + arcering + label) of maak small multiples. Voor
  scatter/small multiples maximaal de **eerste drie** slots (alle paren gevalideerd).

<!-- BEGIN:palet -->
| Controle (dataviz-validator) | Licht (op `--card`) | Donker (op `--card`) |
|---|---|---|
| Lichtheidsband | PASS · all 5 inside L 0.43–0.77 | PASS · all 5 inside L 0.48–0.67 |
| Chromavloer | PASS · all 5 >= 0.1 | PASS · all 5 >= 0.1 |
| CVD-scheiding (aangrenzend) | PASS · worst adjacent #c78600↔#00a672 ΔE 8.8 (protan) · tritan 4.3 | PASS · worst adjacent #c98500↔#199e70 ΔE 8.4 (protan) · tritan 8.7 |
| Normaal zicht (aangrenzend) | PASS · worst adjacent #db7099↔#c78600 ΔE 17.7 (normal) | PASS · worst adjacent #d55181↔#c98500 ΔE 19.3 (normal) |
| Contrast t.o.v. kaart | PASS · all 5 >= 3:1 | PASS · all 5 >= 3:1 |
| Eerste 3, alle paren: CVD | PASS · worst all-pairs #00a672↔#eb6834 ΔE 10.0 (protan) · tritan 7.3 | PASS · worst all-pairs #199e70↔#d95926 ΔE 9.4 (deutan) · tritan 4.0 |
| Ordinale blauwe trap 300–700 | PASS · #6da7ec at 2.50:1 vs surface | PASS · #184f95 at 2.18:1 vs surface |

<!-- END:palet -->

- **Sequentieel:** één blauwe trap `chart-seq-100…700`; in donker eigen stappen (bijna-nul ligt dicht bij het vlak).
  Ordinaal: licht vanaf 300, donker tot 600.
- **Divergerend:** blauw ↔ **oranje** met neutraal grijs midden (`chart-div-*`, symmetrisch in lichtheid). Nooit
  blauw ↔ rood (rood is status).

**Markeringen.** Lijn 2 px, ronde verbindingen; vlak 10 %; eindpunt ≥ 8 px met 2 px ring in de vlakkleur; staven
≤ 24 px, 4 px afgerond aan de data-kant; 2 px vlakkleur tussen segmenten; raster en as haarlijn, solide, recessief;
tekst in teksttokens (nooit de reekskleur); directe labels selectief (eindpunt, grens); legenda bij ≥ 2 reeksen.

**Eerlijkheid.**
- **Gat** bij ontbrekende meting: de lijn breekt (nooit doorgetrokken, nooit 0) en het gat krijgt de 135°-arcering
  `arcering-gat` met het label **"Geen meting"**.
- **Verouderd:** de lijn stopt bij het laatste punt; daarna arcering met "Verouderd sinds 10:12".
- **Geschat:** stippellijn (de enige toegestane stippel in een grafiek) of 45°-arcering in staven, plus "≈" in labels
  en tooltip. Gemeten en geschat staan nooit in dezelfde look.
- **Grenslijn** in `chart-limit` met direct label ("Limiet 512 MiB").

**Meter.** Vulling `chart-1` op spoor `chart-meter-track` (≥ 3:1 tussen vulling en spoor). Vanaf de drempel (standaard
90 %, per metriek in te stellen) wordt de vulling **Beperkt** (`degraded` op `degraded-bg`) met icoon en tekst
"97 % van de grens — boven de drempel van 90 %"; bij gemeten falen **Storing**. Nooit alleen kleur.

**Sparkline:** grijze lijn (`chart-axis`), huidig punt in `chart-1`, geen as; altijd naast de waarde, nooit alleen.

**Interactie.** Draadkruis + tooltip (waarde vet, tijd secundair) met aanwijzer **én** toetsenbord (plotvak
`tabindex=0`, pijltjes/Home/End/Esc, voorlezen via `aria-live="polite"`); doel ≥ 24 px; filters in één rij boven
alle grafieken, periode eerst (presets); bij verversen blijft de vorige weergave staan (geen skelet).
`<figure>` + `figcaption` met bron en tijd; **"Toon als tabel"** altijd (de tabel is de WCAG-tweeling).

**In code (Hub):** shadcn `Chart` op recharts. `chartConfig` gebruikt alleen `var(--chart-N)`; `isAnimationActive`
uit bij `prefers-reduced-motion`; `connectNulls={false}`; tooltip als eigen component met `textContent` (labels zijn
onbetrouwbare data). MediaGuard en MSO houden hun eigen SVG-grafieken (alleen uiterlijk) en krijgen de tokens.

---

## 11. Nederlandse microcopy

**Toon:** rustig, feitelijk, zonder alarmisme of uitroeptekens; risico's nooit verbloemen. Eerst wat er is, dan
waarom, dan wat je kunt doen. Knoppen beginnen met een werkwoord + object. Geen "OK", "Doorgaan" of "Weet je het zeker?".

### 11.1 Vaste woorden (overal gelijk, ook in statuspagina's en de bestaande apps)

| Begrip | Woord | Niet meer | MediaGuard en-US |
|---|---|---|---|
| healthy | **Gezond** | In orde, Actueel | Healthy |
| degraded | **Beperkt** | Verminderd (MG), Verstoord (MSO), Let op (my_house) | Degraded |
| down | **Storing** | Offline, Probleem, Niet gezond | Down |
| unknown | **Onbekend** + reden | Laden…, pauze-icoon | Unknown |
| stale | **Verouderd** · laatst bekend 10:12 | stale, groene "gemeten" | Stale · last known 10:12 |
| paused | **Gepauzeerd** | | Paused |
| totaal | **Alles gezond** · Beperkt · Storing · **Deels onbekend** | | All healthy · Partly unknown |
| fase | **Nog niet beschikbaar** — volgt in fase P3 | | Not available yet — coming in phase P3 |
| capability | **Niet beschikbaar** — <bron> ondersteunt dit niet | | Not available — <source> does not support this |
| risico | R0 · Alleen lezen · R1 · Omkeerbaar · R2 · Goedkeuring nodig · R3 · Destructief — extra bevestiging | "Automatisch veilig" (groen, MSO) | R0 · Read only … |
| uitvoering | **Uitgevoerd en gecontroleerd** | Klaar, Gelukt! | Done and verified |
| zekerheid | Zeker · Waarschijnlijk · Onzeker · **Ik weet het niet** | percentages zonder model | Certain · Likely · Unsure · I don't know |

Termen: app · dienst · Problemen / incident · goedkeuring · voorstel · herstelpunt · hersteltest · back-up (tekst) /
Backups (navigatie) · koppelen · koppelcode / setupcode / ontkoppelcode · beheerder / operator / kijker / kind ·
bijgewerkt · verouderd · controle na uitvoering (readback) · droogtest (dry-run) · **uitstellen / uitgesteld tot** (niet:
snoozen) · **veilige modus** (niet: safe mode) · **reikwijdte** (niet: blast radius) · **stappenplan** (niet: runbook) ·
**gezondheidscontrole** (niet: healthcheck) · **lijst met toegestane acties** (niet: allowlist) · **vingerafdruk** (van de
Hub-sleutelset). Vaktermen die de bron zelf zo noemt (scrub, digest, crashloop tussen haakjes) mogen blijven.

### 11.2 Patronen met voorbeelden

| Situatie | Voorbeeld |
|---|---|
| Fout | **Oudere logregels ophalen is mislukt.** Voorbeeld-app reageerde niet binnen 15 s. Het bewijs hierboven is compleet; alleen logregels van vóór 10:02 ontbreken. Probeer het over een minuut opnieuw. · Code `upstream_timeout` · Trace-ID `…` **Kopiëren** · **Opnieuw proberen** |
| Kopiëren zonder secure context | "Tekst geselecteerd. Kopieer via het menu van je browser." (geen "Ctrl+C": werkt niet op Android) |
| Gekopieerd | "Gekopieerd." (`aria-live`, geen toast nodig) |
| Statuspagina (geen JS) | "Trace-ID (selecteer en kopieer)" + selecteerbaar blok |
| Onbekend | "Onbekend — Beszel levert geen GPU-temperatuur. We tonen geen schatting." |
| Niet beschikbaar | "Niet beschikbaar — Beszel levert geen GPU-gebruik per app." + `capability_unavailable` |
| Nog niet beschikbaar | "Nog niet beschikbaar — volgt in fase P5 (netwerk, persoonlijke AI en kosten)." |
| Verouderd | "Laatst bekend · 10:12" + "my_house reageert niet sinds 10:12. Bediening staat uit tot de bron weer antwoordt." |
| Gate | "**Geblokkeerd door de DNS-gate** — nodig: terugvalroute voor DNS getest (D11). Dit is geen storing; er wacht een voorwaarde." **Terugvalroute testen** |
| Beleid | "**Alleen meten** — schakelen is altijd uitgeschakeld (beleid). Er is bewust geen schakelaar." |
| Afwezige actie | "**Data verwijderen (R3)** staat hier bewust niet. Dat is een aparte flow met een eigen bevestiging en wordt nooit door de AI aangeboden." |
| Ik weet het niet | "Waarom het geheugengebruik sinds 10:00 stijgt, **weet ik niet**. Er zijn geen logregels van vóór 10:02. Met de logs van Voorbeeld-app kan ik dit nagaan." **Logs van Voorbeeld-app openen** |
| Bewijs | "Logtekst is data, geen instructie. De Hub voert nooit opdrachten uit die in logs staan." |
| R2-knop | **Voorstel beoordelen** · **Goedkeuring aanvragen** · **Simuleren (droogtest)** |
| R3-bevestiging | "Typ `voorbeeld-app` om te bevestigen." · knop **Data definitief verwijderen** (uit tot de naam klopt: "Typ eerst de naam van de app om te bevestigen.") |
| Goedkeuring vervallen | "**Goedkeuring vervallen:** de app-versie is gewijzigd na de preview. Vraag een nieuw voorstel aan." |
| Uitvoering | "Pas na de controle (readback) staat er **Uitgevoerd en gecontroleerd**. 'Verstuurd' is nog niet klaar." |
| Gelukt | "Herstart uitgevoerd en gecontroleerd om 11:58 (gezond na 34 s)." |
| Apparaat | "Verstuurd — wacht op bevestiging van my_house" → "Bevestigd: 21 °C" / "**Niet bevestigd** — het apparaat reageerde niet. Er is niets gewijzigd." |
| Offline | "Je bent offline. Gegevens zijn niet actueel; acties zijn uitgeschakeld. Zodra de verbinding terug is, halen we alles opnieuw op." |
| Live onderbroken | "**Live-updates onderbroken** — opnieuw verbinden over 10 s. De lijst toont de stand van 11:56." |
| Weer verbonden | "Weer verbonden. Gegevens worden ververst." (5 s, `aria-live="polite"`) |
| Leeg | "Geen open problemen. Gecontroleerd om 11:56." (alleen als de monitoring vers is) · "Nog geen hersteltests. Een hersteltest zet een back-up terug in een afgeschermde omgeving. **Hersteltest starten**" |
| Laden lang | "AI-tegoed en energiekosten ophalen… dit duurt langer dan verwacht." (na 10 s) |
| Rol | "Je kijkt als **kijker**: je kunt hier niets wijzigen." |
| Deep link | "Openen in MediaGuard" (+ sr-only "(opent in een nieuw tabblad)") |
| Kind | "Je kunt kijken." · "Een volwassene kijkt ernaar." · "Er ging iets mis. Vraag het aan een volwassene." + **Details voor een volwassene** |
| Statuspagina-veld | "Vorm: http(s)://<NAS-adres>:<poort>. Geen localhost." · "16 tekens in groepen van 4 (XXXX-XXXX-XXXX-XXXX). Staat in de Hub naast de koppelcode. Zo weet je zeker dat je met jouw Hub koppelt." · "Wordt versleuteld opgeslagen in deze dienst. Je ziet daarna alleen de laatste 4 tekens. De Hub ziet deze sleutel nooit." |

**Notatie:** `Intl` nl-NL (decimale komma, punt voor duizendtallen); 24-uurs tijd; "9 nov 2026, 11:56"; relatief
"2 min geleden"; vaste spatie tussen getal en eenheid; binaire eenheden zoals de bron ze levert; "42%"; aftellen
"14:32" (mm:ss, `cijfers`).

---

## 12. Toegankelijkheid en tests

**Norm:** WCAG 2.1 AA minimaal; Peil-eisen gaan verder: tekst ≥ 7:1 (beide niveaus), doelen 44 px overal, reflow op
320 px, focus nooit bedekt (2.4.11 uit 2.2), axe met WCAG 2.2 AA.

| Eis | Peil-oplossing |
|---|---|
| A-01 alternatieven | iconen `aria-hidden` naast tekst; icoonknoppen met `aria-label` = zichtbaar doel; grafiek `role=img` + samenvatting + tabel |
| A-02 structuur | landmarks, één h1, geen kopsprongen, `dl` voor feiten, `th scope`, `fieldset/legend`, `role=table` voor de diff |
| A-04 niet alleen kleur | vijf assen vijf vormen; diff-tekens; treden-glyph; stippel/arcering; legenda + tabel |
| A-05 contrast | §3.3; ook gerenderd gemeten (opacity, arcering) |
| A-06 reflow | 320 px zonder horizontale scroll; codes breken; tabs lopen om; tabellen → kaarten |
| A-07 hover/focus | tooltips nooit de enige bron; "Waarom R2?" als uitklapbaar blok |
| A-08 toetsenbord | echte tabs (pijltjes, Home/End, roving tabindex); grafiek met pijltjes; geen focusval |
| A-09 tijd | aftellen zichtbaar; aankondiging op 5 min / 1 min / verlopen; directe weg naar een nieuwe code |
| A-10 beweging | reduced motion = alles ≈ 0; geen bewegende aftellers |
| A-11 navigatie | skiplink als eerste tabstop; titel per route; tweekleurige ring ≥ 3 px; scroll-padding |
| A-12 aanwijzer | 44 px; geen vegen-only |
| A-15 invoerhulp | foutsamenvatting `role=alert` + links + `aria-invalid` + `aria-describedby`; R2/R3 preview + bevestiging |
| A-16 statusberichten | `role=status` voor verbinding (één per pagina), `aria-live` voor gekopieerd, resultaatteller, grafiek |
| A-17 routewissel | focus naar h1 en titel aankondigen (Next: in de layout) |
| A-18 forced colors | alleen `border-color: CanvasText` forceren; stippelstijl blijft; focus `Highlight` |
| A-19 kind | eenvoudige taal; codes achter "Details voor een volwassene" |

**Testmatrix (verplicht in elke app met Playwright):** breedtes **320, 360, 390, 768, 1024, 1440** × **licht/donker**:
geen horizontale overloop; geen doel < 44 px (radio/checkbox: het label telt); geen tekst < 14 px; geen ellips op
inhoud; **axe-core** tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` = 0; volledige Tab-doorloop op 390 en 1440:
elk element heeft een ring ≥ 3:1 en is **niet bedekt** door kop- of ondernavigatie; één h1, geen kopsprongen, geen
dubbele id's, geen kapotte `aria-*`-verwijzingen; `prefers-reduced-motion` = geen beweging; forced colors toont
stippelranden; schermafdrukken op 360/390/768/1440 × licht/donker bij elke release (geen pixel-baselines nodig).
Referentie-implementatie van deze controles: `bouw/controle.cjs` + `bouw/gedrag.cjs`.

**Uitkomst op de prototypes** (`controle/samenvatting.txt`, opnieuw gedraaid bij de review 1.1.0): 11 schermen × 6 breedtes × 2 thema's — axe 0 overtredingen (ook een onafhankelijke run met axe-core 4.14 incl. best-practice: 0),
0 tekstfouten, 0 overloop, 0 doelen < 44 px, 0 bedekte focus, ring ≥ 5,1:1, één h1 overal; gedragstest: tabs met
pijltjes/Home/End, grafiek leest "09:30: Geen meting" voor, R3-knop pas actief na de juiste naam, kopiëren valt terug
op selecteren, forced colors houdt de stippelrand, 200 %-zoom (640 px) zonder overloop.
**Niet gedaan:** echte schermlezer (NVDA/TalkBack), echte Android-telefoon in fel licht, tv/speler. Die horen bij de
acceptatie (08_UI) en blijven open.

---

## 13. Per oppervlak

**Regel voor alle bestaande apps: functies, routes, schermstructuur, API-koppeling, teksten met domeinbetekenis en
`data-testid`'s veranderen niet.** Wat verandert is de visuele laag: tokens, componentstijl, iconen, statuswoorden en
-symbolen, focus, dichtheid. Functionele toevoegingen (themaknop in my_house O-09, "Terug naar de Hub" O-11, tabelweergave
bij bestaande grafieken) vragen akkoord van de gebruiker en zijn géén onderdeel van de migratie.

### 13.1 Hub (`ai-server-manager/apps/web`, Next 16 / React 19) — referentie-implementatie

1. **ADR:** ADR-ONTWERPTAAL.md opnemen als ADR-00xx; vervangt ADR-0009 punt 1 ("geen CSS-framework, geen
   iconenbibliotheek"). De rest van ADR-0009 (navigatie, service worker, zero empty screens) blijft.
2. **Afhankelijkheden:** `tailwindcss`, `@tailwindcss/postcss`, `class-variance-authority`, `clsx`, `tailwind-merge`,
   `lucide-react`, Radix-pakketten via shadcn. `components.json` uit deze map.
3. **Themascript eerst** (vóór de eerste paint, vóór stap 4): zet **altijd** het opgeloste thema
   (`data-theme="dark|light"` én `.dark`), ook bij "Systeem" (luistert naar `matchMedia`). Keuze Systeem / Licht /
   Donker (ThemeSwitch, standaard Systeem). Zonder dit is Peil altijd licht: Peil kent bewust geen
   `prefers-color-scheme`-terugval in `ontwerptaal.css`.
4. **CSS:** `globals.css` = `@import "tailwindcss"; @import "./peil/ontwerptaal.css";` + tijdelijk
   `aliaslagen/hub-p0.css`. Vooraf: codemod `var(--muted)` → `var(--muted-foreground)` en de **drie** eigen kleurblokken
   van `globals.css` volledig weg (`:root`, `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])`,
   `:root[data-theme="dark"]`); anders wint het media-blok (specificiteit 0,2,0) en krijg je een half donker thema.
   De aliaslaag levert alle oude namen. (1.0.0 bevatte `--info-bg: var(--info-bg)` en `--unknown-bg: var(--unknown-bg)`:
   een cyclus die beide variabelen ongeldig maakte — in Chromium werden alle info- en onbekend-vlakken transparant;
   hersteld in 1.1.0, de generator weigert nu zelfverwijzingen.) Preflight: `ol.howto` krijgt `data-genummerd`.
5. **Componenten:** StatusBadge (lucide i.p.v. tekstsymbool), Freshness → Versheid (+ Geprobeerd, verouderd-vak),
   StatCard → StatKaart, ErrorPanel → Foutpaneel (+ KopieerVeld met terugval), NotYetAvailable, TableWrap →
   ResponsieveLijst, Loading → Laden (skelet). `worstTone()` blijft de bron van de totaalstatus.
6. **Gedrag dat de code moet leveren (O-03):** bij een fout de laatst bekende waarde **bewaren** en als verouderd tonen
   (nu wist P0 oude data) — kan pas na "OVERDRACHT VOLTOOID" (ai-server-manager is tot dan alleen-lezen).
7. **Tests:** `components.test.tsx` (controle op `aria-hidden">?</span>`) aanpassen naar het lucide-icoon;
   e2e-testids blijven; 44 px-test blijft geldig op 1440 (Peil houdt 44 px overal); axe blijft 0; de 29 afdrukken in
   `docs/screenshots/p0` opnieuw maken; matrix uitbreiden met 320 en 1024.

### 13.2 Statuspagina's (vijf diensten, FastAPI-HTML zonder JS)

- **Eén sjabloon:** `statuspagina.html` (CSS ± 6,8 KB en lucide-symbolen inline; geen JS, geen externe bron, past
  binnen `default-src 'none'; style-src 'unsafe-inline'`). Vaste opbouw: kop → (melding) → Status (totaal, controles,
  gereed, versie, bron, bijgewerkt "bij het laden") → Koppeling met de Hub → (Koppelen) → (executor: Schrijfsleutel,
  Ontkoppelen of sleutel vervangen) → dienstspecifiek blok → voet (`/healthz`, `/readyz`).
- **Koppelen volgens contract 1.2 §3.7/§3.9** (in 1.0.0 nog volgens 1.1 — hersteld): precies één koppeltoestand:
  `koppelformulier` (dienst is **nog nooit** gekoppeld, `first_linked_at` leeg) · `gekoppeld` · `ontkoppeld` ·
  `ingetrokken`. Het formulier heeft drie verplichte velden: **Hub-adres, koppelcode, Hub-vingerafdruk**
  (`XXXX-XXXX-XXXX-XXXX`); de geldigheid komt uit `{{koppelcode_geldig}}` (diensten 60 minuten, executor 30 minuten).
  Na de eerste koppeling verdwijnt het formulier voorgoed; ontkoppeld/ingetrokken tonen "Opnieuw koppelen gaat via de
  Hub". **Geen ontkoppelen en geen ontkoppelcode op de statuspagina** — behalve de executor (§3.9): na het koppelen het
  blok `schrijfsleutel_formulier` (TrueNAS-API-sleutel met schrijfrechten, live getest) en daarna `executor_beheer`
  (ontkoppelen of sleutel vervangen, alleen na het opnieuw invoeren van de geverifieerde schrijfsleutel).
  "Gekoppeld" is een **neutrale** pil (`link-2`), nooit groen.
- **Plekken:** `{{x}}` = tekst (altijd `html.escape`); `<!--ALS:x-->…<!--/ALS:x-->`, `<!--NIET:x-->…`,
  `<!--ELK:lijst-->…`. Toonwaarden alleen uit de vaste lijst `gezond | beperkt | storing | onbekend | gate`; meldingen
  uit de vaste lijst per dienst (zoals `NOTICES` in home-control-hub). Referentie-renderer: `bouw/sjabloon.mjs`
  (± 10 regels; in Python hetzelfde met `re.sub` + `html.escape`).
- **Eerste signaal is nooit groen als de dienst niet gereed is:** totaal = slechtste van gezondheid en gereedheid
  ("Niet gereed — deels onbekend, wacht op koppeling"). Trace-ID in een selecteerbaar, omlopend blok.
- **Verspreiding (O-14):** elke repo krijgt `statuspagina.css`/`.html` als versie-gepinde kopie met de herkomstkop
  (`Peil x.y.z · sha256:…`); een CI-stap vergelijkt de checksum met de versie in `ai-server-manager` en faalt bij drift.
- Bestaande pagina's: CSRF, throttle en de vaste meldingen blijven; de HTML/CSS gaat naar het sjabloon. **Let op:**
  home-control-hub (`status_page.py`) heeft nog het 1.1-gedrag (lokale ontkoppelcode, geen vingerafdrukveld). Dat is een
  contractwijziging (1.2), geen ontwerpkeuze; de ombouw naar het sjabloon past die mee toe en de tests op
  `ontkoppelcode_onjuist`/`ontkoppeld` vervallen dan. Overige tests op teksten uit `NOTICES` blijven gelden.

### 13.3 MediaGuard (Next 16, Tailwind 4 alleen preflight, `mg.css` + 823 inline styles)

- **Wat verandert:** systeemfont i.p.v. Inter/JetBrains Mono; standaardthema **Systeem** (donker één klik weg; het
  bestaande themascript zet bij "Systeem" het opgeloste `data-theme="dark|light"`, want zonder attribuut is Peil licht);
  "Verminderd" → **Beperkt**; onbekend = `circle-help` + stippelpil (niet `Pause`, niet "Laden…"); "Wacht op jou" krijgt
  per soort een icoon (niet `CircleCheck`); `DangerDialog`-knop `destructive` (7,6 / 9,7:1 i.p.v. 3,18:1); tellers
  neutraal; KPI-kaarten krijgen bron + bijgewerkt; correlation-id wordt kopieerbare Trace-ID; lettergrootte ≥ 14 px;
  grafiekpalet Peil (geen status als reeks; "onzeker" niet rood); GPU/CPU-lanes = `domain-lane-gpu/cpu` (alleen het
  icoon; de lane-tekst is inkt). Correcties in de aliaslaag sinds 1.1.0: noodstop-knop ingedrukt (wit op lichtrood was
  2,0:1 in donker), pauze = Gepauzeerd, diff zonder rood/groen, AI-zekerheidsbalk inkt i.p.v. groen,
  kwaliteitslabels DV/HDR/AV1 neutraal (waren violet/amber/groen), fasebalk "controle" inkt en "verouderd" gearceerd
  i.p.v. opacity.
- **Wat blijft:** routes, IA, S1–S21, sneltoetsen, i18n (elke nieuwe term ook en-US, §11.1), `data-theme` +
  `mg.theme`, StateView-set, DangerDialog/DiffViewer/Stepper/Timeline (krijgen Peil-stijl), integriteitsslot
  (icoon `file-lock`), dichtheid via compacte tabellen (niet via kleine tekst).
- **Migratiepad:** **fase 1** `@import "./peil/ontwerptaal.css"` vóór `mg.css` en `aliaslagen/mediaguard-mso.css` erna
  → alle **kleuren**, radii en lettertypes van de 980 regels en inline styles volgen direct. **Niet** in fase 1: de
  harde lettermaten onder 14 px (mg.css ± 120 declaraties, MSO ± 98; inline `fontSize` ± 189 in MediaGuard en ± 254 in
  MSO) en vaste hoogtes onder 44 px op desktop. De ondergrens 14 px geldt pas na fase 2/3; tot dan alleen voor regels
  die `--mg-fs-11…13` gebruiken. De episodekaart (`.heat-cell--*`) blijft in fase 1 kleur-alleen: in fase 2 een
  textuur per toestand en een tabelweergave (WCAG 1.4.1). **Fase 2** primitieven → shadcn (Button, StatusChip→StatusBadge,
  Card, Modal→Dialog/AlertDialog, Tabs, Switch, Segmented→ToggleGroup, Toast→Sonner) met behoud van API, testids en de
  hook-klassen (`.btn`, `.sidebar`, `.nav-item`, `.bottomnav`, `.state--error`, `.tech-details`, …). **Fase 3** inline
  styles → utilities, `mg.css` en fontsource weg, `components.json` baseColor `neutral`.
- **Tests:** geen snapshot-baselines. Raakt: `werk-r030` (`main .btn` 44 px — blijft geldig, nu ook desktop),
  `mediaguard.spec`/`werk-ia`/`werk-apikeys` (thema via `data-theme` blijft werken), axe 0 in licht én donker, 56
  afdrukken `docs/design/after` opnieuw. Eventuele tekstasserties op "Verminderd" → "Beperkt".

### 13.4 MSO (kopie van MediaGuard `e92c2acc`, 192 regels achter)

- Als MediaGuard, plus: de **44 px-regels** komen terug (aliaslaag + knoprecepten; nu 34/28 px); groene chips voor
  gewone waarden ("2m 41s", "0") worden neutraal; `RiskChip` → **RisicoBadge**: `advies` → R0, `automatisch_veilig` →
  R1 (neutraal, niet groen), `toestemming_nodig` → R2 (violet) — de MSO-niveaus blijven, alleen de weergave wordt R-klasse;
  `.estop--pause` wordt Gepauzeerd (info), niet amber; eigen SVG-grafieken (`goals-card`, `usenet/categories`,
  `Sparkline`) krijgen `chart-*`; ongebruikte `recharts` mag blijven of weg (geen functie).
- MSO kopieert daarna **niet meer uit MediaGuard** maar uit `ai-server-manager/design/ontwerptaal` (PROVENANCE.md bijwerken).
- **Tests:** `screens.spec.ts` (axe per scherm × thema × 1440/390; `.skeleton`-telling; `.state--error`; `data-theme`)
  blijft; 64 afdrukken opnieuw.

### 13.5 my_house (Vite 5, React 18, géén Tailwind)

- **Wat verandert:** warm beige → koel neutraal; Space Grotesk weg (systeemfont); radius 18 → 8 px; primaire knop
  blauw i.p.v. bijna-zwart; knoppen- en randcontrast ≥ 3:1 (was 1,5:1); de groene "gemeten"-badge wordt een neutraal
  kwaliteitslabel; onbereikbare lichttegel houdt volle tekst met stippelrand (geen `opacity .6`); offline-banner
  neutraal-onbekend (niet amber); dimmen met opacity wordt arcering; "stap klaar" niet groen; ondergrens 14 px.
- **Wat blijft:** routes, schermen, offline-regel (3 mislukkingen of 15 s), snapshotgedrag, meetkwaliteit per waarde,
  domeinbetekenissen **verwarmen/koelen/boiler/licht-aan/Overig** (nu `domain-*` = grafiekslot), eigen
  variabelenamen, vitest/e2e-klassen.
- **Migratiepad:** **fase 1** `aliaslagen/my_house.css` na `styles.css` importeren: zelfstandige hex-waarden voor
  licht en donker (`prefers-color-scheme` + klaar voor `data-theme`), dezelfde variabelenamen, plus een kleine set
  selectorcorrecties (gemeten: 50 paren, 0 fouten; `.light-tile.on` 8,5:1). Geen Tailwind nodig (O-10).
  **Fase 2** (optioneel): lucide i.p.v. de 18 eigen iconen (nieuwe afhankelijkheid, alleen uiterlijk). Tailwind/shadcn
  alleen na een apart besluit; op React 18 met forwardRef-varianten, of na een aparte React 19-upgrade (functioneel risico).
- **Tests:** unit-tests op klassen (`toHaveClass('note','warn')`, `.q`, `.en-seg-rest`, …) en e2e (`.state-line.ok`,
  `.lt-conflict`, `.c-dial`, `.big-num`, `.shell.is-offline`) blijven groen: er verandert geen klasse. Aanbevolen:
  axe + afdrukken toevoegen (zoals de andere apps).

---

## 14. Verspreiding, versies en drift

- Bron (zoals contract §13 voorschrijft): `ai-server-manager/docs/platform-v2/ONTWERPTAAL.md` (dit document) +
  `ai-server-manager/design/ontwerptaal/` (de rest van deze map), na "OVERDRACHT VOLTOOID" te committen. (1.0.0 noemde
  `design/peil/`; dat week af van het contract.) Versie in `tokens.mjs`
  (semver: patch = waarde zonder betekeniswijziging, minor = nieuw token, major = hernoemd/verwijderd token).
- Kopieën: `ontwerptaal.css` (Hub, MediaGuard, MSO), `aliaslagen/<app>.css`, `statuspagina.css` + `.html` (5 diensten).
  Elke kopie houdt de herkomstkop met versie en sha256. CI: checksum gelijk aan de gepinde versie, anders falen.
- Geen npm-registry: de builds blijven offline en zonder geheimen werken.

---

## 15. Open punten (besluit van de gebruiker nodig)

1. **O-07 productnaam** in de schil: prototypes gebruiken "Hub · Thuisplatform" als plaatshouder.
2. **O-09 themaknop in my_house** en **O-11 "Terug naar de Hub"** in de bestaande apps: functionele toevoegingen.
3. **Tabelweergave en toetsenbord bij bestaande grafieken** in MediaGuard/MSO: verbetert toegankelijkheid maar is een
   UI-toevoeging.
4. **Native Android-apps** van MediaGuard en my_house vallen buiten de reikwijdte en gaan visueel afwijken.
5. **O-05 rechten van de rol kind** (prototype: Home · Kijken · Zoeken · Ik) — bevestigen met de API-bouwer.
6. **O-03** vraagt een codewijziging in de Hub (laatst bekend bewaren) — pas na de overdracht.
7. Echte schermlezer-, telefoon- en tv-test vóór afmelding (08_UI).
