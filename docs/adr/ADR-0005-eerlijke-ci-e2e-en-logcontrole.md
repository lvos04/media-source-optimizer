# ADR-0005 — Eerlijke CI: volledige E2E, logcontrole met herstartvenster, pg-client 16

Status: **Aanvaard (testfoutreparatie vóór de ontwerpronde)** · Datum: 2026-11-09 · Excel: B05-001 (P3-04: ci.yaml
laten draaien)
Aanleiding: controle van deze machine (`.lokaal/testcontrole/BEVINDINGEN.md`, rapport en controle
media-source-optimizer).

## Context

De controleurs draaiden de CI-jobs van `.github/workflows/ci.yaml` letterlijk na en vonden drie fouten in de
testdefinitie, niet in de app:

1. **e2e-job vrijwel leeg.** `make e2e` startte een verse stack zonder beheerder en gaf geen `MSO_E2E_USER` en
   `MSO_E2E_PASSWORD` door. 76 van de 78 Playwright-tests sloegen zichzelf over (`test.skip(!USER, …)`) en de job kon
   groen worden zonder echte UI-dekking. Met alleen inloggegevens faalden er 70: `smoke.py` maakt wel een beheerder
   aan, maar rondt de eerste-startwizard nooit af (`POST /api/v1/setup/complete`), dus de UI bleef in `/setup`. De
   gecommitte 78/78 vroeg een extra stap die nergens in de repo stond.
2. **Race in `smoke.spec.ts`.** De test keek direct na `goto("/login")` of de URL op `/setup` eindigde. De redirect
   komt pas na de asynchrone `GET /api/v1/setup/status` van de inlogpagina. Soms won de test de race, soms niet.
3. **Logcontrole hangt af van I/O.** De stack-check "logs: 0 foutmeldingen" faalde op gewone schijf op
   `FATAL: the database system is shutting down` / `… is starting up`. Die regels ontstaan als een client
   (healthcheck, api, worker) verbinding zoekt terwijl PostgreSQL bij een bewuste herstart nog afsluit of opstart. Hoe
   vaak dat gebeurt, hangt af van de duur van het afsluit-checkpoint (0,9–1,9 s fsync onder load; op tmpfs 0,003 s).

Daarnaast zette de python-job van `ci.yaml`, anders dan `release.yaml`, geen `MSO_PG_BIN`.

## Besluiten

1. **Voorbereiding in de stack-tooling, niet in Playwright.** `smoke.py --e2e-prepare` (via
   `MSO_STACK_UP_ONLY=1 MSO_STACK_E2E_PREPARE=1 tests/stack/run.sh`) maakt op een verse stack de beheerder
   `stackadmin` aan, koppelt elke connector aan zijn mock, maakt één back-up en rondt de wizard af zonder modus (de
   standaard `read_only` blijft). Elke stap moet `ok` zijn; een overgeslagen stap is hier een fout. Rapport:
   `tests/stack/results/e2e-stack/e2e-prepare.json`.
   *Waarom niet een Playwright-globalSetup:* de geheimen van de mocks bestaan alleen binnen `run.sh`, en een
   globalSetup die een beheerder aanmaakt op wat er onder `MSO_E2E_BASE_URL` draait, zou per ongeluk een echte, nog
   niet ingerichte installatie kunnen "inrichten". De stack-tooling raakt alleen het testproject `msostack`.
2. **Wachtwoord per run.** `make e2e` maakt een willekeurig wachtwoord, geeft het alleen via de omgeving door aan
   `run.sh` (`MSO_STACK_ADMIN_PW`) en Playwright (`MSO_E2E_PASSWORD`) en print het nergens. De stack wordt na afloop
   altijd opgeruimd (`make down`), ook als de voorbereiding faalt.
3. **Strikte modus: overslaan = falen.** `apps/web/e2e/strict-reporter.ts` laat de run falen (exitcode 1) zodra er
   met `MSO_E2E_STRICT=1` ook maar één test is overgeslagen, of als er minder dan `MSO_E2E_MIN_TESTS` tests gevonden
   zijn (`make e2e`: 78). Zonder `MSO_E2E_STRICT` verandert er niets, zodat één spec lokaal draaien blijft werken.
   De bestaande `test.skip`-regels blijven staan; ze worden in CI alleen niet meer stil geaccepteerd.
4. **Race weg door op de probe te wachten.** `smoke.spec.ts` wacht eerst op het antwoord van
   `GET /api/v1/setup/status` en kiest op basis van dat antwoord welke pagina hij moet zien (`/setup` of het
   inlogformulier, met URL-controle). Geen vaste wachttijd.
5. **Herstartweigeringen alleen binnen het herstartvenster.** `smoke.py` legt voor `docker compose restart` en
   `down`/`up` het venster vast (van het commando tot de stack weer gezond is, ±2 s) en leest de logs met de
   tijdstempels van de Docker-daemon (`--timestamps`). Alleen in de log van `postgres`, alleen de twee meldingen
   "the database system is shutting down" en "the database system is starting up", en alleen binnen zo'n venster,
   worden getolereerd; ze worden geteld in het rapport. Buiten een venster, in een andere dienst, zonder tijdstempel
   of elke andere FATAL/PANIC/ERROR/Traceback blijft het een fout. Unit-test: `tests/stack/test_smoke_logcheck.py`
   (ook als stap in de stack-job). De bestaande uitzondering "terminating connection due to administrator command"
   blijft zoals hij was.
6. **pg-client 16 in de python-job,** precies als in `release.yaml` (pgdg-pakket `postgresql-client-16`,
   `MSO_PG_BIN=/usr/lib/postgresql/16/bin`). De productcode (`apps/core/mso/backups.py`) blijft ongewijzigd: die raakt
   integriteit van back-up/herstel en valt buiten deze reparatie.
7. **CI laten draaien zonder de triggers te veranderen.** `ci.yaml` reageert op push naar `main`, tags, PR's en
   `workflow_dispatch`. De werkbranches heten `ccr-…` en de standaardbranch is ook geen `main`, dus een push start
   hem niet. Besluit: de run op de werkbranch starten met `gh workflow run ci --ref <branch>` (bestaande
   `workflow_dispatch`, `release_version` leeg = alleen bouwen, niets publiceren). De triggers aanpassen is een
   aparte keuze voor de eigenaar (zie gevolgen).

## Gevolgen

- De e2e-job toetst nu echt 78 tests (16 schermen × donker/licht × 1440/390 met axe, overflow en toegankelijke namen,
  plus login, toetsenbord, alleen-lezen en de dialoogtests). Hij duurt een paar minuten langer door de voorbereiding.
- Nieuwe tests kunnen erbij zonder `E2E_MIN_TESTS` aan te passen; minder tests dan 78 laat de job falen.
- Geen wijziging aan API, OpenAPI, datamodel, poorten, installatie of productcode.
- Open voor de eigenaar: `ci.yaml` ook bij push op de werkbranches laten draaien (of de standaardbranch hernoemen naar
  `main`). Tot dan start iemand de CI-run handmatig.
