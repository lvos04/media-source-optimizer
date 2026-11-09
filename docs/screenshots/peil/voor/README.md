# Schermafdrukken "voor" — MSO-web-UI vóór de omzetting naar Peil

Nulmeting bij [`docs/ontwerp/PEIL_MIGRATIE.md`](../../../ontwerp/PEIL_MIGRATIE.md). Gemaakt op 9 november 2026 tegen de
**echte app** (code-HEAD `b581fbb`, versie 1.6.0): de teststack uit `tests/stack/run.sh` met mocks voor SABnzbd,
qBittorrent, Prowlarr, Newznab, Spotweb, NNTP en MediaGuard, voorbereid zoals `make e2e` (beheerder, koppelingen,
één back-up, eerste-startwizard afgerond, modus Alleen-lezen). Chromium van Playwright 1.65, `nl-NL`,
`Europe/Amsterdam`, schaal 1.

| Map | Inhoud | Aantal |
|---|---|---|
| `schermen/` | de 16 routes van `apps/web/e2e/screens.spec.ts` × 360/390/768/1440 × licht/donker, **volledige pagina** | 128 |
| `tabs/` | Automatisering (6 tabs naast de standaardtab), Downloads → Torrent, Instellingen → Verbindingen, Gebruikers, Usenet, Torrent, Automatisering × 390/1440 × licht/donker | 48 |
| `toestanden/` | inloggen, eerste start, niet gevonden, laden, fout, verbinding niet ingesteld, offline, gepauzeerd, beheermodus, verouderd (lijst met data, lege lijst, Overzicht), zoekresultaten, vergelijken, pauze-dialoog, terugzet-dialoog, profielmenu, sneltoetsen (1440), Meer-lade (360/390) | 74 |
| `metingen.json` | per afdruk: overloop t.o.v. de apparaatbreedte, doelen < 44 px, tekst < 14 px (histogram), inline styles, statuschips per toon, grafiekelementen, axe-core 4.14 (`wcag2a/aa/21a/21aa/22aa`), JS-fouten | 250 |
| `toetsenbord.json` | Tab-doorloop (40 stops) op Overzicht, Downloads, Instellingen, Ontbrekend × 390/1440 × licht/donker: focusstijl en focus bedekt door kopbalk/ondernavigatie | 16 |

Bestandsnaam: `<scherm of toestand>-<licht|donker>-<breedte>.png`. Telefoonbreedtes (360, 390) zijn geëmuleerd met
`isMobile` + touch, 768 met touch, 1440 met muis.

## Eerlijk over deze afdrukken

- **Nagebootste toestanden.** Fout, offline, laden, verbinding niet ingesteld, gepauzeerd, beheermodus, eerste start en
  verouderd komen niet vanzelf voor in de testdata. Daarvoor onderschept het script één API-antwoord in de browser
  (bijvoorbeeld `GET /costs/*` → 500 met de echte foutvorm `{detail, code, correlation_id}` van de core). Welke
  onderschepping per afdruk is gebruikt, staat in `metingen.json` onder `nagebootst`. De app zelf is niet aangepast.
- **Overloop op de telefoon.** Met `isMobile` rekt Chromium het layout-viewport op tot de breedste inhoud (zoals een
  telefoon die uitzoomt). `scrollWidth − innerWidth` is dan altijd 0 — ook in de bestaande `screens.spec.ts`. Daarom meet
  `metingen.json` de overloop **t.o.v. de apparaatbreedte** (`overloop_apparaat_px`). Voor afdrukken van vóór die
  meetcorrectie is de waarde afgeleid uit de PNG-breedte (`overloop_bron`); bij een afdruk van de volledige pagina is dat
  precies de layoutbreedte. Viewport-afdrukken (dialogen, menu's) van vóór de correctie hebben `null`.
- **Kleuren.** De PNG's zijn na het maken omgezet naar een 256-kleurenpalet (± 3× kleiner, `verklein.py`). Vlakken en
  tekst zien er gelijk uit, maar meet **nooit** contrast in deze afdrukken; contrast komt uit de browser (axe) en uit
  `design/ontwerptaal/` (Peil `contrast.json` in de bron).
- **Niet gedaan:** echte telefoon, echte schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors. MSO heeft **geen
  Android-app**; Android-afdrukken zijn hier niet van toepassing.

## Opnieuw maken

```sh
# 1. kloon buiten de repo (run.sh schrijft in tests/stack/results), images bouwen onder het machinebrede slot
git clone -q file://$PWD /var/tmp/mso-peil && cd /var/tmp/mso-peil
flock /home/transcode/truenas-v2/.lokaal/docker-build.lock bash -c \
  'for i in core mocks web; do MSO_IMAGE_TAG=stack nice -n 10 scripts/build-images.sh $i; done'
# 2. stack starten en voorbereiden (wachtwoord alleen in de omgeving)
export MSO_STACK_ADMIN_PW="E2e-$(openssl rand -hex 16)"
flock /home/transcode/truenas-v2/.lokaal/stack.lock env MSO_STACK_NO_BUILD=1 MSO_STACK_UP_ONLY=1 MSO_STACK_E2E_PREPARE=1 \
  MSO_STACK_PYTHON=/pad/naar/venv-met-httpx/bin/python tests/stack/run.sh
# 3. afdrukken + nulmeting (vanuit de repo)
export MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD="$MSO_STACK_ADMIN_PW"
export PEIL_PLAYWRIGHT=/home/transcode/truenas-v2/.lokaal/ontwerp-tools/node_modules/playwright/index.mjs
export PEIL_AXE=/pad/naar/axe-core/axe.min.js
node docs/screenshots/peil/voor/maak-afdrukken.mjs alles     # of: schermen | tabs | toestanden; PEIL_ALLEEN=<regex>
node docs/screenshots/peil/voor/toetsenbord.mjs
python3 docs/screenshots/peil/voor/verklein.py                # Pillow; palet + aanvulling overloop
# 4. opruimen: (cd /var/tmp/mso-peil && make down) en de kloon verwijderen
```

De scripts zijn alleen-lezen hulpmiddelen voor deze documentatie; ze horen niet bij de app en worden niet gebouwd.
