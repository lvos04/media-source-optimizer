<!-- HERKOMST — Peil 1.1.0 · bron: ontwerptaal Peil, definitief/COMPONENTEN.md (design-agent, 2026-11-09; wordt ai-server-manager/design/ontwerptaal/ na de overdracht) · sha256 van het bronbestand (zonder deze kop): d0b0999d8f07537fcc4447d30ab5a3d45632d559487736082beae8a3c28b0154 · versie-gepinde kopie (O-14), niet met de hand wijzigen. Relatieve links naar bestanden die hier niet zijn gekopieerd (prototypes/, shots/, bouw/, ../REVIEW.md) werken alleen in de bron. -->

# Peil — componentspecificaties (handoff)

Versie 1.1.0 (na ontwerpreview, `../REVIEW.md`) · 9 november 2026 · hoort bij [ONTWERPTAAL.md](ONTWERPTAAL.md). Gemaakt met `design:design-handoff` en
`design:design-system`. Elk component noemt de **tokens** (geen losse waarden), **props**, **varianten**, **toestanden**,
**responsief gedrag**, **randgevallen**, **beweging** en **toegankelijkheid**. De HTML-referentie staat in
`prototypes/` (genoemd per component); de klasserecepten in `bouw/componenten.css` zijn de bron voor de cva-varianten.

Algemene regels voor elk component:

- Alle maten uit tokens/Tailwind-schaal; geen `text-xs`; bedienbare delen ≥ 44 px (`min-h-touch`/`size-touch`).
- Kleuren alleen via tokens (§3 ONTWERPTAAL). Status nooit alleen kleur: icoon (`aria-hidden`) + woord.
- Tekst in NL uit de vaste woordenlijst (ONTWERPTAAL §11). MediaGuard: elke string ook als en-US-sleutel.
- Focus: de globale tweekleurige ring (`:focus-visible`, 3 px `--ring` + 2 px `--ring-halo`) of `focus-ring`.
- Beweging alleen `transition-colors` (150 ms); niets bij `prefers-reduced-motion`.
- Testhaken: bestaande `data-testid`'s blijven; nieuwe componenten krijgen `data-testid` in kebab-case NL.

---

## 1. shadcn/ui: installatie en verplichte aanpassingen

**`components.json`** (in deze map; voor de Hub `css: "src/app/globals.css"`; MediaGuard/MSO hun eigen pad,
baseColor `neutral` — de basiskleur doet er niet toe omdat `ontwerptaal.css` alle variabelen levert; laat `shadcn init`
`globals.css` **niet** overschrijven: draai `init` eenmaal en vervang het gegenereerde variabelenblok door
`@import "./peil/ontwerptaal.css"`). my_house: geen shadcn in fase 1 (zie ONTWERPTAAL §13.5).

**Codemod na elke `npx shadcn add`** (handmatig of met een eigen script; in de review controleren):

| Zoeken (new-york v4) | Vervangen door | Waarom |
|---|---|---|
| `outline-none` en `focus-visible:ring-ring/50 focus-visible:ring-[3px] focus-visible:border-ring` | weglaten (globale ring) of `focus-ring` | shadcn-ring haalt 2,3–3,2:1; Peil-ring ≥ 5:1 en zichtbaar op elk vlak |
| `h-9`, `h-8`, `size-9`, `size-8` op bedienbare elementen | `h-11` / `size-11` (`min-h-touch`) | 44 px overal |
| `text-xs` | `text-sm` | ondergrens 14 px |
| `bg-destructive text-white` | `bg-destructive text-destructive-foreground hover:bg-destructive-hover` | donker thema heeft donkere tekst op lichtrood |
| `dark:bg-input/30`, `dark:bg-destructive/60`, `bg-primary/90` e.d. | `bg-card`, `bg-destructive`, `hover:bg-primary-hover` | geen opacity-trucs op vlakken (contrast onvoorspelbaar) |
| `aria-invalid:ring-destructive/20 aria-invalid:border-destructive` | `aria-invalid:border-2 aria-invalid:border-down` | veldfout = rol storing, rand ≥ 3:1 |
| `border-input` op Input/Select/Textarea | blijft (`--input` ≥ 3:1) | — |
| `rounded-md` op Badge | per variant (statuspil `rounded-full`, risico `rounded-sm`) | vorm per as |
| `data-[state=active]:shadow-sm` (Tabs) | Peil-tabs (§21) | actieve tab zichtbaar zonder schaduw |
| `@custom-variant dark (&:is(.dark *));` en het `:root`/`.dark`-variabelenblok in de door `shadcn init` geschreven `globals.css` | weg; `ontwerptaal.css` levert beide | Peils variant dekt `.dark` én `[data-theme="dark"]`; twee varianten naast elkaar geven verschil tussen `dark:`-utilities en tokens |
| `z-50` (Dialog/Sheet/Popover/Tooltip), eigen `z-10/z-20` op kop en balken | `z-overlay` / `z-toast` / `z-nav` / `z-sticky` | vaste lagen (bestaan sinds 1.1.0 als utility) |

Componentvarianten (cva) — namen zijn stabiel en worden in code gebruikt:

```ts
// Button
variant: "primair" | "secundair" | "stil" | "gevaar" | "link"   // shadcn: default | outline | ghost | destructive | link
size:    "standaard" | "klein" | "icoon"                        // allemaal min-h 44 px; klein = text-sm px-3
// Badge-basis voor StatusBadge/RisicoBadge/Validatielabel/ErnstLabel: zie hieronder; nooit losse kleurvarianten.
```

**Button-toestanden:** standaard · hover (`*-hover` / `bg-accent`) · focus (ring) · ingedrukt (`aria-pressed=true`
= primair) · bezig (`aria-busy`, `loader-circle`, label blijft: "Opslaan…") · **uitgeschakeld = `aria-disabled="true"`
met gestippelde rand, `bg-muted`, `text-disabled-foreground` en de reden ernaast** (via `aria-describedby`; nooit
alleen grijs, nooit `disabled` zonder reden — `disabled` haalt het element uit de tabvolgorde en verbergt de uitleg).

---

## 2. AppSchil en navigatie (C-01)

**Doel:** vaste navigatie volgens 08_UI; zijbalk ≥ 1024 px, ondernavigatie < 1024 px; kindschil apart.
**Prototype:** elke pagina; `kind.html` voor de kindschil.

| Deel | Specificatie |
|---|---|
| Skiplink | eerste tabstop, "Naar inhoud" (op een foutpagina: "Naar de koppelcode (fout)"), `knop-primair`, zichtbaar bij focus |
| Zijbalk (lg+) | `w-sidebar` (256), `bg-sidebar`, rand rechts `border`; logo 40 px; **exact 9 items** Home · Server · Media · Huis · Netwerk · Automatisering · Backups · Kosten · Instellingen; item `min-h-touch px-3 rounded-lg`, icoon 20 `text-muted-foreground`; actief: `bg-accent font-bold` + inset-balk 4 px `--primary` + `aria-current="page"`; ouder van een subpagina (Server boven Apps) `font-semibold` zonder `aria-current`; subnavigatie ingesprongen met 2 px lijn; voet: versie + validatiestatus |
| Kopbalk | `sticky top-0 z-nav h-16 bg-card border-b`; ≥ 1280: Zoeken (knop, Ctrl K, `aria-keyshortcuts`) · Live-indicator · **Problemen** (inktteller) · **Goedkeuringen** (violette teller) · **AI** · gebruikersmenu (account, thema, uitloggen); 1024–1279: zoekknop als icoon, live-regel onder de kop; < 1024: logo · zoeken · gebruikersmenu + live-regel |
| Live-indicator | neutraal `radio` + "Live · 11:56"; onderbroken/offline = stippelpil `unknown`; **één** `role="status"` per pagina (de zichtbare) |
| Ondernavigatie (< lg) | `sticky bottom-0 z-nav h-16 + safe area`; 5 items Home · Apps · Problemen · AI · Meer; icoon 24 + label 14; actief = inset-balk 3 px boven + vet + `aria-current`; tellers `aria-hidden` met sr-only ", 3 open" (op Problemen) / ", 1 goedkeuring wacht" (op **Meer**, niet op AI; Meer-sheet begint met Goedkeuringen; op de goedkeuringspagina is Meer `aria-current`) — review 1.1.0 |
| Meer-sheet | Sheet van onder (mobiel) met Server, Media, Huis, Netwerk, Automatisering, Backups, Kosten, Instellingen, Zoeken, Onboarding; focusval, Esc sluit, focus terug naar "Meer" |
| Kindschil | eigen kop (logo "Thuis" + gebruikersmenu) en ondernavigatie **Home · Kijken · Zoeken · Ik**; geen zijbalk, geen tellers, geen beheer |
| Inhoud | `main#inhoud tabindex=-1`, `max-w-content px-4 sm:px-6 lg:px-8 pt-6 pb-10`; bij routewissel focus naar de h1 en `document.title` = "<pagina> — Hub" |

**A11y:** landmarks `header`, `nav[aria-label="Hoofdnavigatie"]`, `main`, `aside` alleen voor echte zijinhoud.
Scroll-padding (ONTWERPTAAL §6) voorkomt bedekte focus. **Randgevallen:** lange paginatitel loopt om (geen ellips);
teller > 99 = "99+" met exact getal in de sr-tekst; safe area onderin (PWA).

---

## 3. PageHeader en Kruimelpad (C-02)

```ts
PageHeader { titel: string; omschrijving?: string; bijgewerkt?: Versheid; acties?: ReactNode; kruimel?: Kruimel[] }
```
h1 `text-2xl font-bold`; omschrijving `max-w-prose text-muted-foreground`; meta-regel `text-sm` ("Bijgewerkt 11:56
(zojuist) · vernieuwt elke 60 s"); acties rechts (md+) of eronder; Vernieuwen = secundaire knop met icoon én label.
Kruimelpad: `nav[aria-label=Kruimelpad] > ol`, elk deel `min-h-touch min-w-touch`, scheiding `chevron-right`
(`aria-hidden`), laatste deel `aria-current="page"` zonder link.

---

## 4. StatusBadge (C-03) — gezondheid

```ts
StatusBadge {
  status: "healthy" | "degraded" | "down" | "unknown" | "stale" | "paused" | "unlinked" | "not_yet";
  label?: string;          // standaard het vaste woord; nooit vrije varianten ("Verstoord", "In orde")
  grootte?: "standaard" | "groot";   // groot = systeemstatus (min-h-10, text-base, icoon 20)
}
```
| Status | Woord | Icoon | Klassen (recept `.status-*`) |
|---|---|---|---|
| healthy | Gezond | `circle-check` | `text-healthy-ink`, icoon `text-healthy`, **geen vlak**, rand transparant |
| degraded | Beperkt | `triangle-alert` | `bg-degraded-bg border-2 border-degraded text-degraded-ink` |
| down | Storing | `octagon-x` | `bg-down-bg border-2 border-down text-down-ink` |
| unknown | Onbekend | `circle-help` | `bg-unknown-bg border-2 border-dashed border-unknown text-unknown-ink` |
| stale | Verouderd | `clock-alert` | `arcering border-2 border-dashed border-stale text-unknown-ink` |
| paused | Gepauzeerd | `circle-pause` | `bg-paused-bg border-2 border-paused text-paused-ink` |
| unlinked | Niet gekoppeld | `link-2-off` | als unknown |
| not_yet | Nog niet beschikbaar | `hourglass` | `bg-muted border-2 border-border-strong text-foreground` |

Maat: `min-h-8 px-2.5 rounded-full text-sm font-semibold gap-1.5`, icoon 18 px; `w-fit max-w-full`; tekst mag omlopen
(nooit `whitespace-nowrap`: overloop op 320 px). **Plaats:** vast — direct onder de kaarttitel of aan het rij-einde,
nooit midden in een zin. **Bij Onbekend staat de reden altijd zichtbaar ernaast** (ook op 360 px; nooit `hidden`,
nooit afgekapt). `data-status="<status>"` (forced colors). **Niet doen:** groen voor "Actueel/Live/Klaar"; een badge
zonder woord; een statuskleur voor iets dat geen gezondheid is.

---

## 5. SysteemStatusPaneel (C-04)

```ts
SysteemStatusPaneel { onderdelen: { naam: string; status: Status; reden: string; href: string }[]; bijgewerkt: Versheid }
```
- Kop: h2 "Systeemstatus" + StatusBadge `groot` met de **totaalstatus** (`worstTone`: storing > beperkt > onbekend >
  gezond; gepauzeerd en nog-niet tellen niet mee). Bij onbekend door offline: "Onbekend — offline" + "Laatst bekend om
  11:52: Storing".
- Telling in één zin: "**1 storing · 1 beperkt · 1 onbekend** · 3 gezond · 1 gepauzeerd · 1 nog niet beschikbaar".
- Rijen: `ul` met `divide-y`; elke rij is **één link** (`min-h-touch`) "naam · badge · reden · chevron"; < 640: naam
  links en badge rechts op één regel, reden eronder; ≥ 640: naam-kolom 8 rem.
- Rand van het paneel: 2 px in de totaalstatus (`border-down`, `border-degraded`, gestippeld `border-unknown`; gezond =
  1 px `border`). ≥ 1280 naast "Wat actie nodig heeft".
- **Wat actie nodig heeft** (zelfde paneelstijl): geordende lijst storing > goedkeuring > gate > onboarding; per item
  as-label (ErnstLabel / RisicoBadge / Gate-label / Onboarding-label) + titel + uitleg + één werkwoordknop (mobiel volle
  breedte). Leeg: "Er wacht niets op je. Gecontroleerd om 11:56." (alleen als alle bronnen vers zijn).

---

## 6. StatKaart (C-05) — kaartcontract

```ts
StatKaart {
  id: string; titel: string; icoon: LucideIcon;
  status?: Status;                              // badge onder de titel
  waarde?: string | number; eenheid?: string;   // geformatteerd met Intl nl-NL; eenheid met vaste spatie
  kwaliteit?: "gemeten" | "afgeleid" | "geschat" | "niet_beschikbaar";
  toestand: "vers" | "verouderd" | "nooit_gemeten" | "onbekend" | "niet_beschikbaar" | "fout" | "laden" | "verversen";
  reden?: string;                               // verplicht bij onbekend/verouderd/niet_beschikbaar/fout
  versheid: { bron: string; bijgewerkt?: Date; geprobeerd?: { op: Date; uitkomst: string } };
  vervolg: { label: string; href: string };     // precies één vervolghandeling
  extra?: ReactNode;                            // sparkline, meter, korte lijst
}
```
**Anatomie:** `article.kaart[aria-labelledby]` (`bg-card border rounded-lg p-4 gap-3`) → h3 titel (icoon 20
`text-muted-foreground`, `text-lg font-semibold`) → badge(s) → waarde (`text-3xl font-semibold`) + eenheid
(`text-lg font-semibold text-muted-foreground`) + `· gemeten` → reden/extra → Versheid (`dl`) → vervolglink
(`knop-secundair knop-blok mt-auto`, label + `chevron-right`). De hele kaart is **geen** knop.

| Toestand | Weergave |
|---|---|
| vers | waarde + kwaliteit; badge volgens status |
| verouderd | kaartrand 2 px gestippeld `border-stale`; badges **Onbekend** + **Verouderd**; waardevak `waardevak-verouderd` met "Laatst bekend · 10:12" erboven, waarde in `text-stale-ink` gewoon gewicht, sr-only "(laatst bekende waarde, verouderd)"; reden; Versheid met **Geprobeerd**; bediening uit |
| nooit_gemeten / onbekend | kaartrand gestippeld `border-unknown`; badge Onbekend; **geen waarde** (geen 0, geen "—"); reden ("Niet gemeten: …") |
| niet_beschikbaar | badge Onbekend "Niet beschikbaar"; reden + `capability_unavailable` + bron + endpoint (§12) |
| fout | Foutpaneel `inline` (§13) in de kaart; vorige waarde als verouderd als die er is |
| laden | `aria-busy="true"`, sr-only "Laden…", skelet in de vorm van de inhoud (`skelet`), na 10 s "…dit duurt langer dan verwacht" |
| verversen | vorige weergave blijft staan; meta "Bijwerken…"; geen skelet |

Raster: 1 kolom < 768, 2 kolommen `md`, 3 kolommen `xl`; kaarten in een rij even hoog, vervolglink onderaan.
**Randgevallen:** waarde > 7 tekens → kleinere schaal niet toegestaan; laat de eenheid omlopen; afgeleide kaart erft de
versheid van de bron; bedragen alleen met meter ("≈ € 1,02 · geschat", nooit besparing in euro zonder meter).
**A11y:** waarde + eenheid lezen als één zin (geen losse spans zonder spatie: "4 downloads actief"); `dl` met
`dt`-labels Bron / Bijgewerkt / Geprobeerd; `<time datetime>`.

---

## 7. Versheid / LaatstBijgewerkt (C-06) en Meetkwaliteit (C-07)

```ts
Versheid { bron: string; bijgewerkt?: Date; ttl: number; geprobeerd?: { op: Date; uitkomst: string }; extra?: [string, ReactNode][] }
Meetkwaliteit { kwaliteit: "gemeten" | "afgeleid" | "geschat" | "niet_beschikbaar" }
```
- `dl.meta` (`grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-sm text-muted-foreground`, `dt font-semibold`).
- Bijgewerkt: "11:55 (1 min geleden)"; ouder dan een dag: "8 nov 2026, 22:10"; nooit gemeten: "Nog nooit gemeten".
- Relatieve tijd ververst elke minuut, **zonder** live-aankondiging.
- Versheid is **geen** badge en **nooit** groen. Boven de TTL schakelt de eigenaar-component naar `verouderd`.
- Meetkwaliteit: inline `text-sm font-semibold text-muted-foreground` "· gemeten"; geschat: de waarde krijgt "≈"
  (sr "ongeveer") en het label "· geschat". Afwijkingen (meterreset, zomertijd) als uitleg met `info`-icoon.

---

## 8. RisicoBadge (C-08)

```ts
RisicoBadge { klasse: 0 | 1 | 2 | 3; woord?: string /* standaard vast */; uitleg?: { regelIds: string[]; policyHash: string; aiClaim?: 0|1|2|3 } }
```
Anatomie: `span.risico` (`inline-flex w-fit rounded-sm border-2 text-sm font-semibold`) → codeblok `.risico-code`
(`font-mono px-1.5`, **treden-glyph** 19×16: vier staafjes van 5/8/11/14 px, gevuld t/m het niveau, `aria-hidden`) +
"R2" → woorddeel `.risico-woord` (`px-2 py-1`, sr-only " · " ervoor). Kleuren: §4.5 ONTWERPTAAL (`risk-N`,
`-solid`, `-on-solid`, `-bg`, `-ink`). `data-risico="R2"`.
**Uitleg:** nooit tooltip-only; in de ActiePreview een `details` "Waarom R2?" met regel, policy-hash en — als ze
afwijkt — "AI stelde R1 voor; het beleid bepaalt R2." **Niet doen:** R-klasse in statuskleur (amber/groen); los "R1"
zonder woord; R1 violet.

---

## 9. ErnstLabel, IncidentStatus, ZekerheidLabel, ActorLabel

| Component | Props | Weergave | A11y |
|---|---|---|---|
| **ErnstLabel** (T-20) | `ernst: "kritiek"\|"hoog"\|"middel"\|"laag"\|"info"` | rechthoek, linkerbalk 6 px, `text-sm font-semibold`: Kritiek `bg-destructive text-destructive-foreground` balk `down-ink`; Hoog `bg-down-bg text-down-ink` balk `down`; Middel `bg-muted` balk `foreground`; Laag `bg-unknown-bg` balk `unknown`; Info `bg-info-bg` balk `info` | sr-only "Ernst: " |
| **IncidentStatus** (T-20a) | `"open"\|"erkend"\|"uitgesteld"\|"opgelost"` + `tot?` | tekst + icoon (`circle-dot`, `eye`, `timer`, `check`), `text-sm font-semibold`, geen kleur | sr-only "Status: " |
| **ZekerheidLabel** (T-24) | `"zeker"\|"waarschijnlijk"\|"onzeker"\|"weet_niet"` | ovaal `border-2 border-border-strong`, drie stippen 10 px (gevuld = `bg-foreground`), woord | sr-only "Zekerheid: " |
| **ActorLabel** (C-26) | `"systeem"\|"gebruiker"\|"ai"\|"flow"\|"spraak"` + naam | `rounded-sm border border-border-strong px-1.5 text-sm font-semibold` + icoon (`server`/`user`/`bot`/`workflow`/`mic`) | tekst |

---

## 10. Validatielabel (C-09)

```ts
Validatielabel { status: "Sim-getest" | "Unit-getest" | "NIET GEVALIDEERD (echte NAS)" | "NIET GEVALIDEERD (hardware)" | "NIET GEVALIDEERD (account)" | "NIET GEVALIDEERD (Mac/Xcode)" | "Gereed" | "Geslaagd" }
```
`.validatie`: `border-2 border-dashed border-validation bg-validation-bg text-validation-ink font-mono text-sm
rounded-sm`, icoon `flask-conical` 16; exacte strings (geen vertaling, geen hoofdletterwijziging). Ook "Gereed /
Geslaagd" krijgen **dezelfde** neutrale vorm — nooit de stijl van Gezond. Plaatsing: "Over deze Hub", de voet van de
zijbalk, en bij een uitvoerder/functie zonder hardwarevalidatie (O-12). sr-only "Validatie: ".

---

## 11. GateBlokkade (C-11) en BeleidsSlot (C-13)

```ts
GateBlokkade { gate: string; soort: "voorwaarde" | "gemeten_falen" | "onbekend"; voorwaarden: { tekst: string; besluit?: "D01"…; href: string }[]; actie: { label: string; href: string }; incidentHref?: string }
BeleidsSlot  { tekst: string /* "Alleen meten" */; uitleg: string }
```
- **voorwaarde:** `melding-gate` (`border-2 border-gate bg-gate-bg text-gate-ink rounded-lg p-4`, icoon `lock`) →
  "**Geblokkeerd door de <gate>**" → "Nodig: <voorwaarde> (Dxx)" → "Dit is geen storing; er wacht een voorwaarde.
  Doorgaan zonder kan niet." → werkwoordknop rechts (mobiel eronder). Inline label: `Gate dicht` met slot
  (`rounded-sm border-2 border-gate`).
- **gemeten_falen** (T-14a): `melding-fout` + slot + link "Incident bekijken". **onbekend** (T-14b): `melding-onbekend`.
- Geblokkeerde actie in een lijst: blijft zichtbaar; knop `aria-disabled` "Geblokkeerd" met slot + reden in de rij.
- **BeleidsSlot:** label `ban` + "Alleen meten" (`border-2 border-gate rounded-sm`) en een `melding-beleid` met de
  uitleg. **Er staat geen Switch/Button in de DOM** (test: `getByRole('switch')` in de BoilerKaart = 0).

---

## 12. NogNietBeschikbaar en CapabilityUnavailable (C-12)

```ts
NogNietBeschikbaar { fase: "P1"…"P6"; wat: string; bron: string }        // T-08a, hele pagina of sectie
CapabilityUnavailable { bron: string; endpoint?: string; uitleg: string; variant: "tegel" | "inline" | "knop" }  // T-08
```
- NogNietBeschikbaar: rustig paneel (`bg-muted border-2 border-border-strong`, `hourglass`): "Nog niet beschikbaar —
  volgt in fase P5 (netwerk, persoonlijke AI en kosten)." + wat er komt + bron. Geen nepdata, geen lege grafiek.
- CapabilityUnavailable tegel: StatKaart in toestand `niet_beschikbaar` (gestippelde rand, badge "Niet beschikbaar",
  reden, `code capability_unavailable`, bron + endpoint). Inline: `melding-onbekend` met `circle-slash`. Knop:
  `aria-disabled` + reden ernaast (focusbaar en leesbaar, geen tooltip-only). **Nooit gesimuleerd succes.**

---

## 13. Foutpaneel (C-14) en KopieerVeld (C-15)

```ts
Foutpaneel { titel: string; waarom?: string; doen: string; reikwijdte?: string; code: string; traceId: string; details?: string; opnieuw?: () => void; niveau: "veld" | "sectie" | "pagina"; kop?: "h2"|"h3"|"h4" }
KopieerVeld { label: string; waarde: string; ingekort?: string }
```
**Foutpaneel** (`melding-fout`: `border-2 border-down bg-down-bg text-down-ink rounded-lg p-4`, icoon `octagon-x`):
kop (wat er gebeurde) → zin "waarom + wat je kunt doen" (+ reikwijdte: "alleen logregels van vóór 10:02 ontbreken") →
`dl` Code (`code-inline`) → KopieerVeld "Trace-ID" → knoppen "Opnieuw proberen" + `details` "Technische details"
(HTTP, pad; geschoond). Mobiel: icoon boven de tekst. Veldfout: `veldfout` onder het veld (`octagon-x` 16 +
`text-down-ink font-semibold text-sm`) + `aria-invalid` + `aria-describedby`; formulierniveau: samenvatting bovenaan
met `role="alert"`, `tabindex=-1` (focus erheen na verzenden) en links naar de velden (`min-h-touch`).
Paginaniveau (403/404/502): h1 + paneel + terugweg ("Naar Home") + waar je anders kijkt. `role="alert"` alleen bij een
fout die net is ontstaan; bij laden van een pagina in fouttoestand geen alert-spam. Kind: "Er ging iets mis. Vraag het
aan een volwassene." + `details` "Details voor een volwassene" met code en trace-ID.

**KopieerVeld:** label (`text-sm font-semibold`) → `code.code-veld` (`border-2 border-dashed border-border-strong
font-mono text-sm break-all select-all`) + knop "Kopiëren" (`copy`, sr-only met het label) → `p[aria-live=polite]`.
Gedrag: `isSecureContext && navigator.clipboard` → schrijven → "Gekopieerd."; anders (Hub via http, E-34) tekst
selecteren → "Tekst geselecteerd. Kopieer via het menu van je browser." Lange waarden lopen om; een ingekorte digest
toont "sha256:91c4…7d02" maar kopieert de volledige waarde. Nooit een geheim (alleen laatste 4 tekens + datum).

---

## 14. Leeg, Onbekend, Laden, Offline (C-16, C-17, C-18)

| Component | Props | Weergave | A11y |
|---|---|---|---|
| **Leeg** (T-12) | `wat, waarom, actie?, gecontroleerd?` | kop + zin "wat + waarom + hoe begin je" + werkwoordknop; "Geen problemen" alleen met verse monitoring en tijd | gewone tekst |
| **NietGekoppeld** (T-12a) | `bron, actie` | `melding-onbekend` + `link-2-off` + "Nog niet gekoppeld. *MediaGuard koppelen*" | |
| **Onbekend** (T-13) | `reden: "niet_gemeten"\|"verouderd"\|"bron_onbereikbaar"\|"geen_toegang"\|"niet_ondersteund"`, `toelichting` | gestippeld blok `unknown`, `circle-help`, "Onbekend — <reden>" | reden altijd in tekst |
| **Laden** (T-11/11a) | `vorm`, `sinds` | skelet (`bg-muted`, puls 1,6 s, uit bij reduced motion) in de vorm van de inhoud; na 10 s "dit duurt langer dan verwacht"; verversen = vorige weergave + "Bijwerken…" | `aria-busy`, sr-only "Laden…" |
| **VerbindingsBanner** (T-10/a/b/c) | `toestand: "offline"\|"hub_weg"\|"onderbroken"\|"hersteld"`, `opnieuwOver?` | volle breedte onder de kop, `border-b-2 border-dashed border-unknown bg-unknown-bg` + `wifi-off`; hub_weg = Foutpaneel met trace-ID; hersteld = `info`, 5 s zichtbaar | `role="status"`, steelt geen focus |

**Offline-gedrag (verplicht):** alle waarden worden "Verouderd · stand van <tijd>"; de systeemstatus wordt "Onbekend —
offline" met de laatst bekende stand; elke mutatieknop `aria-disabled` + "Uit: je bent offline"; niets in een
wachtrij; na herstel eerst opnieuw ophalen, pas daarna vers tonen. Navigeren blijft mogelijk. (`offline.html`)

---

## 15. ActiePreview (C-19) en DiffWeergave (C-20)

```ts
ActiePreview {
  actionId: string; titel: string; aanvrager: Actor; risico: { klasse: 0|1|2|3; regelIds: string[]; policyHash: string; aiClaim?: 0|1|2|3 };
  geldigTot: Date; doel: string; uitvoerder: { naam: string; versie: string; validatie?: Validatie };
  blastRadius: string; onderbreking?: { waarde: string; kwaliteit: Kwaliteit };
  diff: DiffRegel[]; backup: { vereist: boolean; plan: string; herstelpuntId?: string };
  terugzetten: string; readback: string; gebonden: ("preview_hash" | "resource_etag" | "policy_hash")[];
  blokkade?: GateBlokkade | BeleidsSlot; dryRun?: Resultaat;
}
DiffRegel { soort: "gewijzigd" | "toegevoegd" | "verwijderd"; veld: string; was?: string; wordt?: string }
```
**Anatomie** (`section.kaart border-2 border-risk-<n>`): meta-regel "Voorstel · `app.update` · aangevraagd door AI"
→ h3 titel → RisicoBadge + "Preview geldig tot 12:13 (nog 14:32)" (Aftellen) → `details` "Waarom R2?" →
hoofdkolom: **Samenvatting** (`dl`: Doel, Uitvoerder + Validatielabel, Reikwijdte (`blastRadius`), Onderbreking ≈ · geschat,
Verantwoordelijke) → **Wat verandert er** (DiffWeergave) → **Zo verloopt het** (stappen Back-up → Uitvoeren → Controle
na uitvoering → Klaar of incident + zin "Pas na de controle (readback) staat er *Uitgevoerd en gecontroleerd*") →
**zijkolom** `aside[aria-label="Veiligheid van dit voorstel"]` (`bg-muted rounded-lg p-4`): Back-up vereist ·
Terugzetten · Controle na uitvoering · Gebonden aan deze preview → actiebalk (rand boven): Annuleren (stil) ·
Simuleren (droogtest) · **Goedkeuring aanvragen** (R2/R3, primair) of **Uitvoeren** (R1, primair).
**Responsief:** zijkolom naast de hoofdkolom vanaf `xl` (20 rem), daaronder eronder; knoppen omgekeerd gestapeld op
mobiel (primair bovenaan, volle breedte). **Toestanden:** geldig · bijna verlopen (< 1 min: "Verloopt over 0:59", eens
aangekondigd) · **vervallen (tijd/drift)** → `melding-beperkt` "Goedkeuring vervallen: <veld> is gewijzigd na de preview.
Vraag een nieuw voorstel aan." + knoppen uit · geblokkeerd (gate/beleid: blokkade bovenaan, uitvoerknoppen
`aria-disabled`) · droogtest bezig/klaar (resultaat als lijst, "Er is niets gewijzigd") · onbekende `action_id` →
Foutpaneel 422 "Deze actie staat niet in de lijst met toegestane acties".
**DiffWeergave:** `div[role=table]` met kop Veld/Was/Wordt (md+); rij: teken `~ + −` in `diff-teken` (`aria-hidden`
+ sr-only soort), veld (mono, vet), *Was* `diff-was` (doorgestreept, neutraal), *Wordt* `diff-wordt` (vet, info);
toegevoegd: Was = "(nieuw)", verwijderd: Wordt = "(verwijderd)". < 768: per veld "Was" en "Wordt" onder elkaar.
Legenda boven de diff. Lange waarden breken (`break-all`); grote diffs in een ScrollArea met toetsenbordbediening.
**Installatiepreview** (S-047 stap 4) gebruikt dezelfde secties plus: alle hostpaden, poorten, netwerk, GPU, privileges
(+ reden), image + digest, resources, geheimen (alleen namen), back-up en dataretentie.

---

## 16. GoedkeuringsPagina (C-21), Aftellen (C-22), UitvoeringsTijdlijn (C-23)

**GoedkeuringsPagina** — altijd een eigen pagina (mobiel én desktop), nooit een kleine dialoog. Prototype
`goedkeuring-r3.html`.
```ts
GoedkeuringsPagina { voorstel: ActiePreview; gebonden: Record<string,string>; herstelpuntId?: string; gate: GateStatus; magGoedkeuren: boolean /* server-gestuurd, O-06 */; stap: 1 | 2 }
```
- R2: één stap — ActiePreview + gebonden velden + **Goedkeuren** / **Weigeren…** (met reden).
- **R3: twee stappen** (stepper "Voorstel controleren ✓ · Bevestigen"): stap 2 toont "Wat er gebeurt" (opsomming met
  gevolgen, wat níet geraakt wordt), herstelpunt-ID (KopieerVeld), `details` "Gebonden velden", invoer "Typ
  `<naam>` om te bevestigen" (`font-mono`, `autocomplete=off`, `spellcheck=false`), status onder het veld
  (`aria-live`), knop **"<Werkwoord> definitief"** (`knop-gevaar`, `aria-disabled` tot de naam exact klopt; klikken
  zonder match zet de focus in het veld). Annuleren staat in DOM-volgorde vóór de gevaarknop; geen autofocus.
- Dubbel drukken = één uitvoering (idempotency-key; knop na klik `aria-busy`).
- `magGoedkeuren=false`: geen knoppen, RolMelding "Alleen een beheerder kan dit goedkeuren."

**Aftellen:** `mm:ss` in `cijfers font-semibold`, elke seconde visueel; aankondiging (`aria-live=polite`) alleen bij
5 min, 1 min en verlopen; verlopen: "**Verlopen** — maak een nieuwe code / vraag een nieuw voorstel aan" + directe knop.
Geen animatie.

**UitvoeringsTijdlijn** (S-064): `ol` met stappen gecontroleerd → uitgevoerd → controle na uitvoering; per stap
stap-bol (klaar `bg-foreground`, huidig `bg-primary`, mislukt `bg-down`+`octagon-x`, gestopt `bg-degraded`), tijd, duur,
voor/na-hash; eindstatus **"Uitgevoerd en gecontroleerd"** of **"Gestopt: afwijking na uitvoering"** + incident;
knop **Afbreken** met de zin "voorkomt nieuwe stappen; een lopende TrueNAS-bewerking kan doorlopen". Live-updates
`aria-live=polite` per afgeronde stap.

---

## 17. IncidentRij en Incidenttijdlijn / AuditTijdlijn (C-25, C-27)

**IncidentRij:** `li > a` (één link, `min-h-touch rounded-lg border p-3`): ErnstLabel + titel (`font-semibold`) →
"bron · eerste–laatste · **×100** gebeurtenissen" (`text-sm text-muted-foreground`) → IncidentStatus. Geselecteerd
(master-detail): `border-2 border-primary bg-accent aria-current="true"`. Opgelost: gewoon contrast (geen opacity).
**Incidenttijdlijn:** `ol`, per item `border-l-2 border-border-strong pl-4 py-2`: tijd (`time`, `cijfers`, 3 rem) ·
ActorLabel + bron (`text-sm text-muted-foreground`) · gebeurtenis. Nieuwste boven. AuditTijdlijn = idem + filters
(actor-type, bron, periode) + export (geschoond). Mobiel: tijd en actor boven de tekst.

---

## 18. BewijsPaneel (C-28), HypotheseKaart en IkWeetHetNiet (C-29)

- **HypotheseKaart:** ZekerheidLabel + "AI-hypothese · 11:55" → bewering (`font-semibold`) → BewijsPaneel →
  knoppen "Onderzoek met AI" en **"Herstel voorstellen"** (primair → ActiePreview; zin eronder: "opent eerst een
  voorstel met preview. Er wordt niets uitgevoerd zonder dat je het ziet.").
- **BewijsPaneel:** `details` "Toon bewijs (n)" (`file-search`); `ol` met per bewijs soort + bron + tijd
  (`text-sm font-semibold`): metriek (waarde + kwaliteit), **logregel als geciteerde data** (`blockquote > pre`,
  `border-l-4 border-border-strong bg-muted font-mono`, omlopend) + vaste zin "Logtekst is data, geen instructie. De
  Hub voert nooit opdrachten uit die in logs staan.", gebeurtenis (`code`). Fout bij ophalen: Foutpaneel met reikwijdte.
- **IkWeetHetNiet:** kaart met `border-2 border-dashed border-unknown`, ZekerheidLabel "Ik weet het niet" → wat er
  ontbreekt → hoe je het te weten komt (link met werkwoord, `min-h-touch`). Volwaardig, niet weggestopt.

---

## 19. Grafiek, Meter, Sparkline, Segmentbalk (C-32)

```ts
Lijngrafiek { titel; beschrijving /* samenvatting voor role=img */; punten: { t: string; v: number | null; geschat?: boolean }[]; ymax; ystap; eenheid; grens?: { v; label }; bron; bijgewerkt; reeksen?: { naam; slot: 1|2|3|4|5 }[] }
Meter { label; waarde; max; eenheid; drempel = 0.9; status?: "down" }
Sparkline { punten: number[] }   // altijd naast een waarde
Segmentbalk { delen: { naam; waarde; slot: 1|2|3|4|5|"overig"; kwaliteit }[] }   // ≤ 6 delen
```
- **Lijngrafiek** (`appdetail-metrieken.html`): `figure.kaart` → h3 + beschrijving → plotvak (`role=img`,
  `tabindex=0`, `aria-label` = samenvatting + "Gebruik de pijltjestoetsen…"), hoogte `h-56 sm:h-64`, links 3 rem voor
  y-labels, onder 1,75 rem voor x-labels; **labels als HTML** (vaste 14 px, schalen niet mee), plot als SVG met
  `vector-effect: non-scaling-stroke`; raster `chart-grid`, nullijn `chart-axis`; lijn `chart-N` 2 px; vlak 10 %;
  eindpunt 12 px met `ring-2 ring-card` + direct label (rechts uitgelijnd bij > 50 %); grens `chart-limit` 1,5 px +
  label; **gat** `arcering-gat` + "Geen meting"; x-labels: eerste links, laatste rechts uitgelijnd, tussenlabels pas
  vanaf `sm`. Draadkruis 1 px `foreground` + tooltip (`popover`, waarde vet, reeks · tijd secundair) via aanwijzer én
  pijltjes/Home/End/Esc; voorlezen via `p[aria-live=polite]` ("09:30: Geen meting"). `figcaption`: bron · bijgewerkt
  · "een gat betekent dat er geen meting was (nooit 0)". `details` **"Toon als tabel"** (`table` met `caption`,
  `th scope`, "Geen meting" als tekst; scrollregio `tabindex=0` met naam).
- **Meter:** label + waarde ("498 MiB van 512 MiB"); spoor `h-3 rounded-full bg-chart-meter-track`, vulling
  `bg-chart-1`; ≥ drempel: spoor `bg-degraded-bg ring-1 ring-degraded`, vulling `bg-degraded`, regel met
  `triangle-alert` "97% van de grens — boven de drempel van 90%"; `role=meter` + `aria-valuetext`.
- **Sparkline:** 12 punten, lijn `chart-axis` 1,5 px, laatste punt `chart-1` 6 px; `aria-hidden` (de waarde ernaast
  draagt de informatie).
- **Segmentbalk** (dun, `huis.html`): `h-2 rounded-full`, 2 px vlakkleur tussen delen, Overig met `arcering-geschat`;
  daaronder **een tabel** met swatch · naam · waarde · aandeel · kwaliteit (de tabel draagt de cijfers; de balk is
  `aria-hidden`). Geen percentages in de balk zelf (tekstcontrast in een vlak is niet te garanderen).
- **recharts (Hub):** `ChartContainer config={{ geheugen: { label: "Geheugen", color: "var(--chart-1)" } }}`;
  `<Line dot={false} strokeWidth={2} connectNulls={false} isAnimationActive={!reduce} />`; `CartesianGrid
  vertical={false} stroke="var(--chart-grid)"`; tooltip eigen component; geen tweede `YAxis`.

---

## 20. Wizard / Wizardstap (C-36), SchemaFormulier (C-37), DatasetKiezer (C-38), GeheimVeld (C-39)

**Prototype:** `wizardstap.html` (S-047, stap 2 van 7).
```ts
Wizard { stappen: { titel; toestand: "klaar" | "huidig" | "open" | "geblokkeerd" | "fout" }[]; huidig: number; opslaan: () => void }
Veld  { naam; label; verplicht; hulp?; bron: "wizard" | "datasetkiezer" | "geheim" | "gegenereerd" | "standaard"; fout?: string }
```
- **Stepper:** `nav[aria-label="Stappen van de installatie"] > ol`; stap-bol 32 px (klaar `bg-foreground` + `check`,
  huidig `bg-primary` + `aria-current="step"`, geblokkeerd gestippeld `gate` + slot, fout `down`); ≥ lg met titels en
  verbindingslijn, < lg alleen bollen (titels sr-only). Daaronder **h2 "Stap 2 van 7: Parameters"** (één keer).
- **Foutsamenvatting** na verzenden: `melding-fout[role=alert][tabindex=-1]` met "2 velden hebben aandacht nodig" en
  links naar de velden; focus erheen.
- **Veld:** label (`font-semibold`, "(verplicht)" in muted) → hulp (`text-sm text-muted-foreground`, via
  `aria-describedby`) + **bron-tag** (`bron: datasetkiezer`, mono, `border-border-strong`) → invoer (`invoer`: 44 px,
  1 px `border-input`, `text-base`) → veldfout. Fout: 2 px `border-down`, `aria-invalid`.
- **Ja/nee zonder standaard:** `fieldset/legend` + twee `keuze`-rijen (44 px, radio 20 px), geen `checked`; fout =
  2 px rode rand op beide + veldfout.
- **DatasetKiezer:** radio-lijst (of boom) met pad (mono, `<wbr>` na `/`), rol en vrije ruimte; verboden of
  ongeschikte paden **zichtbaar maar uitgeschakeld met reden** ("Niet te kiezen: geen dataset (beleid weigert hostpad
  /mnt).") — gestippelde rand, `aria-describedby` naar de reden.
- **GeheimVeld:** invoer `type=password`, nooit vooringevuld, geen "toon"-knop voor opgeslagen waarden; opgeslagen:
  "••••1a2b · 9 nov 2026" + Testen / Roteren; gegenereerd: alleen uitleg ("Je ziet de waarde nooit").
- **Actiebalk:** `data-actiebalk`; < lg vast boven de ondernavigatie (`fixed bottom-[calc(4rem+safe-area)] bg-card
  border-t p-3`), ≥ lg in de flow; Vorige (stil) · Opslaan en later verder (secundair, vanaf sm) · **Volgende:
  <volgende stap>** (primair). De basislaag verhoogt dan `scroll-padding-bottom`.
- **Plaatshouders** zijn nooit een voorbeeldwaarde (`x.x.x.x`, `<POOL>` worden geweigerd); formulieren starten leeg.

---

## 21. Tabs (shadcn Tabs)

`div[role=tablist][aria-label]`; tab = `button[role=tab]` met icoon + label, `aria-selected`, `aria-controls`,
roving `tabindex`; pijltjes links/rechts, Home/End, automatische activatie. **< 768: omlopende knoppen**
(`flex-wrap gap-2`, tab `min-h-touch rounded-lg border border-border-strong`, actief `bg-foreground text-background`)
zodat de actieve tab nooit buiten beeld valt. **≥ 768:** rij met onderstreping (`border-b-4`, actief
`border-primary`), hover `bg-accent`. Panelen `role=tabpanel` zonder `tabindex` (ze bevatten focusbare inhoud).
Maximaal één tabrij per pagina. Periodekiezer is **geen** tab maar een ToggleGroup (`aria-pressed`).

---

## 22. ResponsieveLijst (C-34), FilterBalk (C-35), DekkingsMatrix (C-33)

- **ResponsieveLijst:** ≥ 768 een echte `table` (kop `bg-muted`, `th scope=col`, rij `min-h-touch`, rijacties rechts
  met naam in de toegankelijke tekst: "Bekijk voorstel: Herstarten"); < 768 een kaartenlijst (één kaart per rij,
  hoofdveld als titel, overige velden als `dl`). **Nooit** horizontale scroll of een geknepen kolom voor kerninformatie
  (K1). Cursor-paginering: "Meer laden" + teller.
- **FilterBalk:** één rij boven de inhoud: zoekveld (label, placeholder alleen als hint), filterknop met teller
  ("Filters 2 actief"), chips; mobiel filters in een Sheet met expliciete "Toepassen"-knop (geen contextwissel bij
  invoer); resultaatteller `aria-live=polite` ("4 problemen · filter: …").
- **DekkingsMatrix:** cel = symbool + woord (`check` Ja · `x` Nee · `circle-help` Onbekend · `minus` N.v.t.), geen
  kleurvlakken; < 768 per rij een kaart.

---

## 23. Huis-set (C-43…C-46) — `huis.html`

| Component | Specificatie |
|---|---|
| **KamerKaart** | StatKaart-variant: titel (kamer) + StatusBadge onder de titel; raster 2 kolommen met Temperatuur / Luchtvochtigheid (label `text-sm font-semibold muted`, waarde `text-3xl`, kwaliteit); doelblok (border, p-3): "Doel" + domeinchip (Verwarmen `domain-heat-bg/-ink` + `flame`, Koelen `domain-cool-*` + `snowflake`) + stappers − / + (`knop-icoon`, `aria-label="Doel 0,5 graad hoger"`) rond een `output[aria-live]` ("21,0 °C") + readback-regel; verlichting als raster van tegels; Versheid |
| **ApparaatBediening (readback)** | na een opdracht: `role=status` "Verstuurd — wacht op bevestiging van my_house" (`loader-circle`, statisch bij reduced motion) → "Bevestigd: 21 °C" of "**Niet bevestigd** — het apparaat reageerde niet. Er is niets gewijzigd."; nooit "klaar" zonder readback |
| **LichtTegel** | `button[aria-pressed]` 44+ px: aan = `border-2 border-domain-light-on bg-domain-light-on-bg text-domain-light-on-ink` + `lightbulb` (in de inkt, niet in `domain-light-on`: 2,8:1 op het vlak) + "Aan · 80%"; uit = `border-border-strong` + `lightbulb-off` + "Uit"; onbereikbaar = gestippelde `unknown`-rand, volle tekst, "Onbekend — niet bereikbaar" (nooit opacity) |
| **Verouderde kamer** | kaartrand gestippeld, Onbekend + Verouderd, waardevak "Laatst bekend · 06:40", reden, knop "Doel aanpassen" `aria-disabled` + "Bediening uit: zonder verse meting stuurt de Hub niets aan." |
| **KlimaatZone** | per zone: naam + modus-chip; modusknoppen (actief = **domeinstijl** van de modus: `border-2 border-domain-heat bg-domain-heat-bg text-domain-heat-ink` + `check`, `aria-pressed="true"`; niet primair-blauw, want blauw is ook "koelen" — review 1.1.0); **conflict** verwarmen/koelen = knop `aria-disabled` + `melding-gate` (slot) "Koelen geblokkeerd. De gedeelde buitenunit verwarmt al…"; **handmatig** = label `hand` "Handmatig uit" + "Blijft uit tot je hervat; de AI en automatisering slaan deze zone over." + knop Hervatten; adapter niet ondersteund = CapabilityUnavailable "Bediening niet ondersteund" (nooit schijnbediening) |
| **BoilerKaart** | titel met `shower-head` in `domain-boiler`; label `ban` "Alleen meten"; Vermogen nu / Vandaag (gemeten); kosten "≈ € 1,02 · geschat (vast tarief)"; `melding-beleid` "Schakelen is altijd uitgeschakeld (beleid)…"; **geen schakelaar in de DOM** |
| **Energieverdeling** | totaal (P1) + dunne segmentbalk + tabel (Boiler `chart-3` gemeten, Airco `chart-1` gemeten 1-fasemeter, Overig `chart-other` + arcering afgeleid) + uitleg meterreset/zomertijd; geen verbruik per binnenunit |

---

## 24. Kinderschil (S-021, S-087) — `kind.html`

Begroeting h1 ("Hoi Sam") + één zin; secties **Verder kijken** (rij = link 44+ px met poster-tegel, titel, "nog 12
minuten", sr-only "(opent Jelly Leon in een nieuw tabblad)"), **Wat je hebt gevraagd** ("Je kunt kijken." met `play`,
"Een volwassene kijkt ernaar." met `hourglass` — nooit groen), **Even niet** (melding Onbekend in kindertaal + `details`
"Details voor een volwassene" met code en trace-ID). Geen systeemstatus, geen tellers, geen beheertermen, geen
geblokkeerde titels of thumbnails in resultaten (EX36).

---

## 25. Statuspagina-sjabloon (C-55) — `statuspagina.html`

Zie ONTWERPTAAL §13.2 (koppelen volgens contract 1.2: velden Hub-adres + koppelcode + **Hub-vingerafdruk**, formulier
alleen zolang nooit gekoppeld, geen ontkoppelcode; executor: schrijfsleutel ná het koppelen en ontkoppelen alleen met de
geverifieerde schrijfsleutel). Klassen (uit `statuspagina.css`): `.wrap` (max 760 px), `.kop`, `.blok`, `.pil-gezond/
-beperkt/-storing/-onbekend/-gate/-neutraal` (neutraal = "Gekoppeld", `link-2`), `.knoppen`, `.totaal`, `.checks` (`.naam`, `.tijd`, `.reden`), `.feiten`, `.melding-fout/-info`,
`.selecteer` (trace-ID), `.stappen`, `.veld` (+ `.hulp`, `.veldfout`, `input[aria-invalid]`), `.knop`,
`.knop-secundair`, `.voet`, `.spring` (skiplink), `.sr`. Plekken en vaste waarden staan in de kopcommentaar van het
sjabloon. Paginatitel per toestand ("Executor — niet gekoppeld (koppelen mislukt)"); skiplink naar het foute veld; de
melding bovenaan met `role=alert` en een link naar het veld; `autocomplete=off` voor codes; schrijfsleutel
`type=password` met uitleg; ingevoerde waarden worden na een fout **niet** teruggezet; geen geheimen, accountnamen of
privé-aantallen.

---

## 26. Overige componenten uit EISEN §8.2 (kort)

| Component | Peil-specificatie |
|---|---|
| C-10 BesluitStatusBadge | tekstlabel + icoon zonder statuskleur: Open (`circle-dot`) · Ingevuld (`square-pen`) · Getest (`flask-conical`) · Geblokkeerd (`lock`, gate-stijl) · N.v.t. (`minus`) + waardebron |
| C-24 BevestigFlow | R0 geen · R1 AlertDialog (titel "App *X* herstarten?", knoppen **App herstarten** / Annuleren, focus op Annuleren) · R2/R3 GoedkeuringsPagina |
| C-30 Chat-set | bericht gebruiker (rechts, `bg-accent`) / AI (links, `bg-card`); citaatchip (`rounded-sm border`, bron + tijd); ZekerheidLabel; actiechip → ActiePreview; tegoed-Meter; privacy-indicator (lokaal/cloud, neutraal); nieuwe AI-berichten `aria-live=polite`; Enter verstuurt, Shift+Enter nieuwe regel; harde stop: `melding-gate` "Cloudmodel uit (harde stop). Regels zonder AI blijven werken." |
| C-31 Voice-set | ingedrukt-houden-knop 56 px (ook spatiebalk), opnamestatus zichtbaar én aangekondigd ("Luistert…"), transcript eerst ter bevestiging; geen toestemming → getypte invoer |
| C-40 CodeWeergave | koppelcode in groepen van 4 (mono `text-2xl`, geen 0/O/1/I), Aftellen, KopieerVeld; verlopen → "Verlopen — maak een nieuwe code." + knop |
| C-41 DienstRij | naam · versie · adres (mono) · koppelstatus (StatusBadge Niet gekoppeld / Gezond / Storing) · "geen contact sinds …" |
| C-42 DeepLink | knop secundair met `square-arrow-out-up-right` + "Openen in <app>" + sr-only "(opent in een nieuw tabblad)"; app onbereikbaar = `aria-disabled` + reden |
| C-48 PausedBar | volle breedte onder de kop, `bg-paused-bg border-b-2 border-paused`, `circle-pause` "**Gepauzeerd** door de MediaGuard-noodstop sinds 21:04 (door <naam>)", `role=status` |
| C-49 RolMelding | één per pagina, `melding-info`: "Je kijkt als **kijker**: je kunt hier niets wijzigen."; mutatieknoppen verborgen |
| C-50 ThemaKeuze | ToggleGroup Systeem / Licht / Donker met tekst (standaard Systeem); script zet altijd het opgeloste thema |
| C-51 GlobaalZoeken | shadcn Command (`/` en Ctrl K), resultaten gegroepeerd (Schermen, Apps, Incidenten), toetsenbord-only bruikbaar |
| C-52 LogWeergave | mono `text-sm`, omlopen aan/uit, zoeken, "Supportbundel kopiëren (gemaskeerd)", ScrollArea met toetsenbord |
| C-53 FlowKaart / NodeVereiste | status (actief / Gepauzeerd / Storing), trigger, laatste uitvoering, RisicoBadge per node; verboden node = BeleidsSlot |
| C-54 RunLog | als LogWeergave + regel "Er zijn geen echte apparaten gewijzigd." bij droogtest |
