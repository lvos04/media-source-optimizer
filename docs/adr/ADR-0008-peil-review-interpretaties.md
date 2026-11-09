# ADR-0008 — Peil-review van de MSO-web-UI: interpretaties bij de reparaties (stap 5)

Status: **Aanvaard (gebouwd)** · Datum: 2026-11-09 · Excel: geen eigen ID (contract 1.2 §13, conformiteit MS6)
Hoort bij: `docs/ontwerp/PEIL_REVIEW.md` (review en metingen), ADR-0006 (interpretaties) en ADR-0007 (bouwkeuzes).
Ontwerptaal: Peil 1.1.0.

## Context

De onafhankelijke ontwerpreview (stap 5) heeft de omgezette app gemeten tegen Peil en de gevonden afwijkingen
gerepareerd. De regels van deze ronde gelden onverkort: alleen uiterlijk, teksten en toegankelijkheid; functies,
API-routes, foutformaat, datamodel, poorten en installatie blijven gelijk; `data-testid`'s en rollen blijven. Bij een
paar reparaties zegt Peil niet precies wat de juiste weergave is. Hier staat welke keuze is gemaakt.

## Besluiten

1. **Amber alleen voor gezondheid, ook in meldingen.** Peil §4.1 regel 3 ("Amber = beperkt, alleen gezondheid")
   geldt ook voor `InlineState`, niet alleen voor statusbadges. Een voorwaarde of rol ("Automatisering niet
   toegestaan", "Alleen beheerders") wordt **neutraal met slot**. Een ontbrekende of onbetrouwbare waarde ("Prijs
   ontbreekt", "ETA onbetrouwbaar", "lijn niet ingesteld", onbekende diagnose) wordt **Onbekend** (stippel +
   circle-help). Verouderde gegevens worden **Verouderd** (stippel + clock-alert). Daarvoor kreeg `InlineState` twee
   tonen: `unknown` en `stale`. Waarschuwingen die over gemeten gezondheid of een echt risico gaan, blijven amber
   (bijvoorbeeld weinig back-upruimte, buiten de grenzen). Die zijn per melding beoordeeld en niet in één keer omgezet.
   *Verworpen:* alle amber meldingen in één keer neutraal maken. Dan verliezen ook echte, gemeten problemen hun
   signaal.
2. **Ernst van een afstemmingsbevinding is een eigen as (T-20).** Prowlarr-afstemmingsbevindingen hadden een
   gezondheidspil met "Conflict", "Let op" of "Info". Nu is dat een **ErnstLabel** (rechthoek met een linkerbalk van
   6 px, COMPONENTEN §9): `error` wordt **Hoog**, `warning` wordt **Middel** en `info` blijft **Info**. "Kritiek"
   gebruiken we niet: het beleid van de core kent geen hoger niveau dan `error`. Het API-veld `severity` is niet
   veranderd.
3. **Status nooit alleen kleur, ook niet als stip.** `Dot` is niet langer een gekleurd rondje. Het tekent nu het
   Peil-icoon in de statuskleur: circle-check, triangle-alert, octagon-x of circle-help. Een neutrale stip blijft
   grijs. De API van de component is gelijk gebleven.
4. **circle-check alleen voor Gezond (Peil §7).** Op andere plekken wordt het een gewoon vinkje: kandidaat
   voldoet, MediaGuard-endpoint aanwezig, preventie actief, stap afgerond. Een afgewezen kandidaat of release is geen
   storing. Die wordt **neutraal met x** en de redenen staan er in gewone tekst naast.
5. **Statuspil met radius 1rem in plaats van `rounded-full`.** Op één regel ziet de pil er hetzelfde uit: de radius
   is dan minstens de halve hoogte. Loopt een pil om (op een smal scherm of in een smalle tabelkolom), dan sneed de
   ronde rand de tekst af. Met 1rem gebeurt dat niet meer.
6. **Ondernavigatie op tekstbreedte.** Vijf even brede kolommen braken op 320–390 px midden in een woord
   ("Downlo-ads"). Nu krijgt elk item zijn eigen tekstbreedte (`flex-auto`, `min-width: min-content`). Afbreken mag
   alleen op een zacht afbreekstreepje (Over·zicht, Down·loads, Ont·bre·kend). Gemeten: vanaf 320 px past alles op
   één regel. De toegankelijke naam blijft hetzelfde woord, want een zacht afbreekstreepje wordt niet voorgelezen.
7. **Oude statuswoorden uit de core.** De core schrijft "Verstoord" en "Offline" in `overview.health.issues` en in het
   bericht per component. De UI toont daar nu het Peil-woord (`peilStatusText`) of laat een los statuswoord naast de
   badge weg. Dat volgt ADR-0006 punt 3. Het **logboek** (audit) toont de opgeslagen tekst bewust letterlijk: het is
   een vastgelegd verslag ("Test Prowlarr: Verstoord"). Een reparatie hoort daarom in de core
   (`CONNECTOR_STATUS_LABELS`). Dat valt buiten deze ronde, want het verandert API-teksten. Zie de vervolgpunten in
   PEIL_REVIEW.md.
8. **Grafiektabel scrollbaar met het toetsenbord.** Een tabeltweeling die op een smal scherm horizontaal scrolt, krijgt
   alleen dán `tabindex=0`, `role=region` en een naam (ResizeObserver), en verder geen extra tabstop. Een
   kaartenlijst voor de tabeltweeling is bewust niet gekozen. De tabel is de WCAG-tweeling van de grafiek en een
   datatabel mag volgens WCAG 1.4.10 scrollen.
9. **Forced colors: selectie zichtbaar houden.** Achtergronden vallen weg in forced colors. Daarom krijgen het
   gekozen segment, het actieve tabblad en het huidige navigatie-item een rand of onderstreping in `Highlight`.
   Daarbij is alleen de randkleur geforceerd, zoals Peil A-18 voorschrijft. Dit is gemeten in Chromium-emulatie, niet
   met een echt Windows-contrastthema.

## Gevolgen

- Geen wijziging aan core, API, OpenAPI-export (byte-gelijk, sha256 `bb551ee1…0471b`), datamodel, poorten of
  installatie. Er zijn wel nieuwe UI-teksten (NL; MSO heeft geen en-US): `ernst.*`, `automation.jobNoFailures`,
  `idx.kpi.noReliability` en `chart.tableScrolls`. Twee teksten zijn aangepast: "Laatste zoekopdracht geslaagd"
  (was "… OK"), en de ernstwoorden vervangen "Conflict" en "Let op".
- Tests: vitest 45 → 55. De e2e-suite (109 tests) slaagt; de axe-eis is aangescherpt naar 0 op alle impacts
  (Peil §12). Tot nu toe telde die alleen serious/critical. De review-scripts staan in `docs/screenshots/peil/review/`
  en horen niet bij de app.
- Twee kleine tekst- en uiterlijkkeuzes: de schermlezertekst bij een skelet is "Gegevens ophalen…" (Peil §11.2) en
  de zijkolom van de inlogpagina heeft geen decoratief verloop meer, zodat beide tekstniveaus ≥ 7:1 halen.
