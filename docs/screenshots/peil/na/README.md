# Schermafdrukken "na" — MSO-web-UI na de omzetting naar Peil

Tegenhanger van [`../voor/`](../voor/README.md), gemaakt op 9 november 2026 tegen de **echte app** van stap 3
(code-HEAD `033373c`, web- en core-image uit die HEAD gebouwd): de teststack uit `tests/stack/run.sh` met dezelfde
mocks, voorbereid zoals `make e2e` (beheerder, koppelingen, één back-up, eerste-startwizard afgerond, modus
Alleen-lezen). Chromium van Playwright 1.63 (de versie van `apps/web`; "voor" gebruikte 1.65), `nl-NL`,
`Europe/Amsterdam`, schaal 1. Zelfde scripts als "voor" (zie de kop van `maak-afdrukken.mjs` voor de drie
aanpassingen: profielmenu-selector, Peil-assen in de toonmeting, extra toestand "hub-link").

| Map | Inhoud | Aantal |
|---|---|---|
| `schermen/` | 16 routes × 360/390/768/1440 × licht/donker, volledige pagina | 128 |
| `tabs/` | als "voor" | 48 |
| `toestanden/` | als "voor" + `hub-link` (390/1440 × 2) en `hub-link-meer` (390 × 2) | 80 |
| `metingen.json` | per afdruk dezelfde metingen als "voor", plus de Peil-assen (`gezond`, `beperkt`, `storing`, `onbekend`, `gepauzeerd`, `risico_r0…r2`, `pauze_icoon_bij_onbekend`), `grafieken.tabelweergave`, `hub_link` | 256 |
| `toetsenbord.json` | Tab-doorloop (40 stops) op 4 schermen × 390/1440 × licht/donker | 16 |

## Voor en na (uit `metingen.json` en `toetsenbord.json`)

| Meting | Voor (250 afdrukken) | Na (256 afdrukken) |
|---|---|---|
| Horizontale overloop t.o.v. de apparaatbreedte (> 1 px) | 34 afdrukken (tot 116 px) | **0** |
| axe-core 4.14 `wcag2a/2aa/21a/21aa/22aa`, serious/critical | 238 afdrukken (`label-content-name-mismatch`, `aria-prohibited-attr`) | **0** |
| Bedienbare elementen < 44 px | 7 696 van 8 928 | **0** van 9 311 |
| Tekstelementen < 14 px | 35 974 | **0** |
| Groene chips op de 16 schermen (licht, 1440) | 163 (`chip--success`) | 0 `chip--success`; 18 × **Gezond** (`data-status="healthy"`), alleen gemeten gezondheid |
| Onbekend met het pauze-icoon | ja (`TONE_ICON.neutral = Pause`) | **0** |
| Grafieken met tabelweergave | 0 | 16 (in alle afdrukken samen) |
| Focusstijl | 2 px één kleur; velden zonder outline | overal **3 px + halo** (Peil-ring) |
| Focus bedekt door kop/ondernavigatie | 16 stops | 2 (zie hieronder) |
| JavaScript-fouten | 0 | 0 |
| Inline `style`-attributen (16 schermen, licht, 1440) | 1 127 | 730 (vervolg: naar utilities) |

De 2 resterende "bedekt"-meldingen zijn op Ontbrekend (390, licht en donker) het scrollgebied `Tabel (horizontaal
scrollbaar)`: een element zo hoog als de hele tabel, waarvan de bovenkant zichtbaar is maar het **midden** (dat meet
dit script) achter de ondernavigatie valt. Het is dus niet volledig verborgen (WCAG 2.4.11 AA gehaald); de strengere
meting in `apps/web/e2e/peil.spec.ts` (bovenkant van het element) slaagt.

## Eerlijk over deze afdrukken

- **Nagebootste toestanden** zoals bij "voor": fout, offline, laden, niet ingesteld, gepauzeerd, beheermodus, eerste
  start, verouderd. Nieuw: `hub-link` en `hub-link-meer` — `GET /settings` wordt in de browser aangevuld met
  `systeem.hub_url = http://hub.thuis.test:30080` (gereserveerde `.test`-naam, geen echt adres; de link wordt alleen
  getoond, er wordt niet naar genavigeerd). In `metingen.json` staat per afdruk welke onderschepping is gebruikt.
- **De doel-trendlijn** (Overzicht → Doelen) staat niet in deze afdrukken: de testdata heeft pas één dagmeting en de
  lijn verschijnt vanaf twee. Hij is in code en unit-tests gecontroleerd, niet visueel met echte meerdaagse data.
- **Kleuren:** PNG's zijn met `verklein.py` naar 256 kleuren omgezet; meet nooit contrast in deze afdrukken.
- **Niet gedaan:** echte telefoon, echte schermlezer, 200 %-zoom, forced colors. MSO heeft geen Android-app.

## Opnieuw maken

Zoals in [`../voor/README.md`](../voor/README.md), met de scripts uit deze map:

```sh
export MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD="$MSO_STACK_ADMIN_PW"
export PEIL_PLAYWRIGHT=$PWD/apps/web/node_modules/playwright/index.mjs PEIL_AXE=$PWD/apps/web/node_modules/axe-core/axe.min.js
node docs/screenshots/peil/na/maak-afdrukken.mjs alles
node docs/screenshots/peil/na/toetsenbord.mjs
python3 docs/screenshots/peil/na/verklein.py
```
