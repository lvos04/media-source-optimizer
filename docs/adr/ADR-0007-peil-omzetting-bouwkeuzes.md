# ADR-0007 — Omzetting van de MSO-web-UI naar Peil: bouwkeuzes (stap 3)

Status: **Aanvaard (gebouwd)** · Datum: 2026-11-09 · Excel: geen eigen ID (contract 1.2 §13, conformiteit MS6)
Hoort bij: `docs/ontwerp/PEIL_MIGRATIE.md` (ontwerp) en ADR-0006 (interpretaties). Ontwerptaal: Peil 1.1.0.

## Context

Stap 3 voert het omzettingsontwerp uit: fase 1 (aliaslaag + woorden), fase 2 (Tailwind 4 + shadcn/ui + lucide) en de
twee toegestane toevoegingen. Vaste regels: alleen uiterlijk, OpenAPI byte-gelijk, `data-testid`'s en rollen blijven,
geen versie-ophoging. Tijdens het bouwen kwamen keuzes naar boven die het ontwerp niet (precies) vastlegt.

## Besluiten

1. **Legacy-CSS in een cascadelaag.** `globals.css` declareert `@layer theme, base, legacy, components, utilities`
   en importeert `mg.css`, `mso.css`, de gedeelde aliaslaag en `peil-mso.css` met `layer(legacy)`. Zonder laag wint
   ongelaagde `mg.css` altijd van elke Tailwind-utility; met de laag winnen de shadcn/ui-componenten zonder
   `!important`. Fase 1a blijft daarbinnen ongewijzigd (zelfde volgorde). `@source not inline("table")` voorkomt dat
   Tailwind de MSO-klasse `.table` als `display:table`-utility genereert (die brak de gestapelde tabellen). De
   MediaGuard-klasse `.grid` (12 kolommen) botst met de utility `grid`; nieuwe code gebruikt daarom `inline-grid` of
   flex. Regels van omgezette onderdelen zijn uit `mg.css`/`mso.css` gehaald; die bestanden zijn nu MSO-eigendom
   (PROVENANCE).
   *Verworpen:* `!important` in de componenten; `mg.css` in één keer weggooien (te veel inline styles hangen er nog
   aan, zie punt 7).
2. **Bestaande API's van de primitives blijven.** `StatusChip`, `Button`, `Card`, `Modal`, `Tabs`, `Segmented`, … houden
   hun props; ze zijn opnieuw gebouwd op `components/ui` (cva) en Radix Dialog. Zo veranderen de ±90 aanroepende
   bestanden niet en blijven testids, rollen en namen gelijk. Hookklassen (`.btn`, `.chip`, `.skeleton`,
   `.state--error`, `.topbar`, `.bottomnav`, `.drawer`) blijven op de elementen voor tests en scripts.
3. **Groen alleen voor gemeten gezondheid: aparte toon `healthy`.** De bestaande toon `success` (136 plekken: aan,
   gelukt, afgerond, ingesteld, …) wordt centraal een neutraal label met vinkje; alleen echte metingen (componenten,
   connectoren, provider- en servertests, trackers, doelen "Gehaald") krijgen `healthy`. Nieuwe tonen: `unknown`
   (stippel, `circle-help`), `paused`, `linked`/`unlinked`, `approval`. Een neutrale chip heeft geen icoon meer (nooit
   het pauze-icoon).
4. **Dialogen op Radix, focus terug naar de opener.** Radix zet de focus na sluiten alleen terug op een
   `Dialog.Trigger`; MSO opent dialogen vanuit gewone knoppen. De Modal onthoudt daarom zelf de opener. Bevestigingen
   houden `role="alertdialog"`. DangerDialog: bevestigknop `aria-disabled` met zichtbare reden tot de naam klopt
   (blijft een dialoog, ADR-0006 punt 2).
5. **Kaartenlijst per kaartbreedte (container query).** De schil wisselt nu op 1024 px (Peil `lg`, was 860 px). Daardoor
   is de inhoud naast de zijbalk op 1024 px smaller dan vroeger en liepen brede tabellen over. Tabellen met
   `.table--stack` worden een kaartenlijst zodra hun kaart smaller is dan 56 rem (`@container` op `.card`), niet
   alleen op een smal scherm. Peil: nooit horizontale scroll voor kerninformatie.
6. **Grafieken: één gedeeld kader.** `components/mso/charts/chart-frame.tsx` levert figure/figcaption, plotvak met
   `tabindex=0`, ←/→/Home/End/Esc, aanwijzer-uitlezing en één zichtbare `aria-live`-regel, plus `details` "Toon als
   tabel". De twee segmentbalken (oorzaakverdeling, onzekerheidsbalk) zijn `aria-hidden` en krijgen alleen de
   tabeltweeling (ontwerp §7: "rij-voor-rij via de tabel"); de toetsenbordbediening loopt daar via de tabel.
   De doel-trendlijn is in de testdata nooit zichtbaar (pas vanaf twee dagmetingen) en is daarom alleen met de code en
   de unit-tests van de onderdelen gecontroleerd, niet in de e2e (eerlijk: niet visueel gevalideerd).
7. **Fase 2d gedeeltelijk.** Gedaan: systeemfont (Inter/JetBrains Mono en fontsource weg), `recharts` weg, alle tekst
   ≥ 14 px (249 inline lettermaten weg, `mg.css`-maten naar 0,875 rem), lopende tekst 16 px, geen kapitalen-labels.
   **Niet** gedaan: de overige 569 inline `style={{…}}` (van de 736 bij de nulmeting; vooral layout: gap/margin/flex) naar utilities en daarmee het verwijderen
   van `mg.css`, `mso.css` en de aliaslaag. Dat is mechanisch werk zonder zichtbaar verschil en staat als vervolgpunt in
   de CHANGELOG.
8. **"Terug naar de Hub" leest `GET /api/v1/settings`.** De schil haalt de instellingen één keer op (kijker heeft
   leesrecht) en toont de link alleen bij een waarde die de UI-validator (`lib/hub.ts`) én de core-validator
   accepteren; dezelfde testgevallen staan in pytest en vitest. Zonder geldige waarde staat er niets in de DOM.
9. **Body 16 px, knoppen en velden 44 px op elke breedte.** Peil vraagt dit; de UI wordt minder dicht. De topbalk krijgt
   onder 640 px twee rijen (zoekveld eronder) zodat alle bedieningen 44 px houden zonder overloop op 320 px.
10. **Unit-tests zonder DOM-emulatie.** Vitest draait in Node; componenten worden met `react-dom/server` gerenderd.
    jsdom 31 eist Node ≥ 22.22.2 (deze machine heeft 22.22.1); gedrag (toetsenbord, focus) zit in de Playwright-e2e.

## Gevolgen

- OpenAPI byte-gelijk (sha256 `bb551ee1…0471b`); één nieuwe instellingsdefinitie (`systeem.hub_url`) zonder route- of
  schemawijziging; installatie en poorten ongewijzigd.
- E2E: 109 tests (78 bestaande + 31 Peil-tests in `e2e/peil.spec.ts`, alleen in het desktopproject); unit: 45 vitest.
- Niet gevalideerd: echte telefoon, schermlezer (NVDA/TalkBack), forced colors en 200 %-zoom op MSO zelf; de
  doel-trendlijn met echte meerdaagse data.
