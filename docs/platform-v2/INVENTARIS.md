# Inventaris — Media Source Optimizer (MSO) — fase P0 / B05-001

Peildatum **2026-11-09** · repo `lvos04/media-source-optimizer` · branch `ccr-c133d7a0-kuz1cv`
· code-HEAD **`018b119`** ("1.6.0: release …"; de twee commits daarna bevatten alleen `docs/platform-v2/`).

Dit document beschrijft wat er **nu in de repository en in GitHub/GHCR** staat. Het zegt niets over de echte NAS:
welke MSO-versie daar draait, met welk image, welke datasets en welke koppelingen (D01, D02, D03, D07) is **onbekend**
en is in deze bouwomgeving niet te meten. Alles wat hieronder als "getest" staat, is getest met mocks/simulators of
alleen statisch nagelezen — **NIET GEVALIDEERD (echte NAS)**.

Methode: statische inspectie van de code (AST-extractie van alle FastAPI-routes, `grep` op bestand:regel),
alleen-lezen controles op GitHub (`git ls-remote`, `gh api repos/…`) en GHCR (`docker buildx imagetools inspect`),
een OpenAPI-export van HEAD en een echte testrun van de bestaande suite (§15). Er is in deze fase **geen productcode**
gewijzigd.

Controle van deze versie (2026-11-09): een eerder, niet-gecommit concept van dit document is in deze sessie regel voor
regel nagelopen. De routetabel (§3) is met een eigen AST-extractie vergeleken: 211 van 211 operaties met hetzelfde
bestand:regel en dezelfde minimale rol; alle genoemde testnamen bestaan; tags, release-run, GHCR-digests en
base-image-digests zijn opnieuw opgevraagd; de testsuite en de OpenAPI-export zijn opnieuw gedraaid (§15).

Legenda bij elk onderdeel: **[INTEGRITEIT]**, **[ROLLBACK]**, **[MONITORING]** = levert die functie al; mag in het
V2-programma **nooit dubbel gebouwd, vervangen of uitgeschakeld** worden (contract §12, 03_RISICO_AUTONOMIE). De Hub
leest deze onderdelen alleen uit of delegeert naar hun bestaande routes.

---

## 1. Versie- en releasestatus (kern van B05-001)

| Wat | Gevonden | Bewijs |
|---|---|---|
| `release/VERSION` | `1.6.0` (gewijzigd in `018b119`, 2026-11-09 06:53 UTC) | `release/VERSION:1`; `git log -- release/VERSION` |
| Pakketversie core | `1.6.0` | `apps/core/pyproject.toml:3` |
| CHANGELOG | sectie `[1.6.0] — 2026-11-09` aanwezig | `CHANGELOG.md:5` (1.5.0 op `:173`) |
| Lokale git-tags in deze checkout | **geen** (checkout zonder tags) | `git tag` → leeg |
| Git-tags op GitHub | `v1.0.0` … `v1.5.0` **en `v1.6.0` → `018b119`** | `git ls-remote --tags origin` |
| GitHub Releases (release-objecten) | **geen** (alleen tags) | `gh api repos/lvos04/media-source-optimizer/releases` → `[]` |
| Release-workflow 1.6.0 | run `42575858717`, push op `ccr-17c40d68-3nxnlb`, **success** (jobs `test` en `publish`; alle stappen success, incl. "End-to-end test of the all-in-one image" en "Tag release commit") | `gh api …/actions/runs/42575858717/jobs` |
| CI-workflow (`ci.yaml`: web-build, stack, Playwright) | **nooit gedraaid op GitHub**: triggert alleen op `main`, `v*`-tags en PR's; de repo heeft geen `main` (standaardbranch `ccr-17c40d68-3nxnlb`) en een tag die door `GITHUB_TOKEN` wordt gepusht start geen workflow | `.github/workflows/ci.yaml:12-15`; `gh api …/actions/runs` → alleen `release`-runs |
| GHCR all-in-one | `ghcr.io/lvos04/media-source-optimizer:1.6.0` = `sha256:9bcdec69f7af0bc1747c42326d65f70eda28e6d00d2559d884e00a58ebfbf3da` (publiek); `:1.5.0` = `sha256:7ee5cb5e3c37d8ca6e3c8445cd6ef24f8b175e2f3167ccd40541737564866be2` | `docker buildx imagetools inspect` |
| GHCR core/web (multi-container) | `media-source-optimizer-core:1.6.0` en `-web:1.6.0` zijn volgens de release-run gepusht, maar **anoniem niet op te halen (401)** → privé-pakket | idem; `docs/TRUENAS_CUSTOM_APP.md:20` (pakket handmatig openbaar maken) |
| Branchbescherming | geen (`protected=false` op beide branches) | `gh api …/branches` |
| Draaiende versie op de NAS | **onbekend (D07)** | — |

**Verschil met het pakket.** B05/I04/A19 gaan uit van "release/VERSION = 1.5.0, 1.6.0 alleen op HEAD". Dat klopt niet
meer: VERSION is 1.6.0, tag `v1.6.0` en de all-in-one-image bestaan. Wat wél nog ontbreekt voor een échte
productierelease volgens de B05-001-acceptatie ("geen 1.6.0 als stable zonder build, migratietest en
hardwareacceptatie"): de **hardwareacceptatie** (`docs/HARDWARE_ACCEPTANCE.md` §10: alle 16 punten "NIET GEVERIFIEERD"),
een gedraaide `ci.yaml` (web-build, containerstack, Playwright) op GitHub, en een releasestatus in de UI. Zie
`DELTA_MATRIX.md` §1 (B05-001) en §8, en `docs/adr/ADR-0001-releasestatus-1.6.0.md`.

---

## 2. Modules en diensten

### 2.1 Containers / processen

| Dienst | Image | Rol | Bewijs |
|---|---|---|---|
| `init` | core | eenmalig root met alleen CHOWN/DAC_OVERRIDE/FOWNER, `network_mode: none`; mappen + eigenaar 568 + DB-wachtwoord | `deploy/truenas/compose.yaml:71-90` |
| `postgres` | `postgres:16.15-alpine@sha256:0cef…31ab` | eigen PostgreSQL 16, intern netwerk `db` | `compose.yaml:91-119` |
| `migrate` | core | eenmalig `mso migrate` (met back-up vóór upgrade) | `compose.yaml:120-135` |
| `api` | core | FastAPI op 8200 (niet gepubliceerd), Newznab/Torznab-feeds | `compose.yaml:136-161` |
| `worker` | core | persistente jobs, polling, health, tuning, back-ups | `compose.yaml:162-183` |
| `web` | web | Next.js-UI, **enige** gepubliceerde poort `38081`, proxyt `/api/v1`, `/newznab`, `/torznab` | `compose.yaml:184-208` |
| alles-in-één | `ghcr.io/lvos04/media-source-optimizer:1.6.0` | postgres + api + worker + web onder een supervisor in één container | `deploy/allinone/Dockerfile`, `deploy/allinone/supervisor.py`, `deploy/truenas/compose.allinone.yaml` |
| mocks | `mso-mocks` | testdubbels Prowlarr/SABnzbd/qBittorrent/MediaGuard/Newznab/Torznab/Spotweb/NNTP — **nooit productie** | `apps/mocks/msomocks/*` |

### 2.2 Code-modules (`apps/core/mso/`)

| Module | Inhoud | Markering |
|---|---|---|
| `config.py`, `db.py` | instellingen; DB-pool, migraties up/down met advisory lock 727001, checksumcontrole | [ROLLBACK] (`db.py:126` `migrate_up`, `:155` `migrate_down`) |
| `auth.py` | lokale gebruikers (scrypt), sessiecookie + CSRF, rollen viewer/operator/admin | |
| `security.py` | redactiefilter, SSRF-controle `check_url`, rate limiter | [INTEGRITEIT] |
| `vault.py` | AES-256-GCM-geheimen, sleutel `APPDATA/secrets/vault.key` | [INTEGRITEIT] |
| `audit.py` | `audit_log` (wordt nooit opgeruimd) | [INTEGRITEIT] |
| `jobs.py` | `jobs`-tabel, `FOR UPDATE SKIP LOCKED`, heartbeat, lease-verloop, idempotency-key per job | [MONITORING]/[ROLLBACK] |
| `changes.py` | change sets: preview → goedkeuring → toepassen (journaal `applying`, advisory lock 727101) → teruglezen → rollback met driftcontrole; reconciler | [INTEGRITEIT] [ROLLBACK] |
| `state.py` | modus (alleen-lezen/beheer/automatisering/Autopilot), pauze, noodrem (`autopilot_hold`) | [INTEGRITEIT] |
| `settings_registry.py` | instellingen + **vergrendelde integriteitsgrenzen** (`:166-206`) | [INTEGRITEIT] |
| `backups.py` | `pg_dump -Fc` + SHA-256, verify, restore (oudere dump: down → restore → up; nieuwere geweigerd), back-up vóór upgrade, vrije-ruimtecontrole, opruimen | [ROLLBACK] |
| `retention.py` | dagelijkse bewaartermijnen, uursamenvatting metingen, `VACUUM`, `GET /api/v1/system/maintenance` (DB-grootte, top-6 tabellen) | [MONITORING] |
| `connectors/` | `prowlarr`, `sabnzbd`, `qbittorrent`, `mediaguard` (+ `base` registry, `monitor` koppelingsbewaking) | [MONITORING] (`monitor.py`) |
| `domain/` | 4 MediaGuard-categorieën, `NormalizedRelease`, `outcomes` (`failed_at`, `outcome_at`, `item_key`), `releasegate` (= **nep-releasecontrole**, niet de B05-releasegate) | [INTEGRITEIT] (`releasegate`) |
| `features/usenet` | SABnzbd-historie, `stagelog`/`unpack`/`failures` (oorzaak), `retry`/`coverage`, `servertest`/`autoheal`/`accounts`, `categories` (categorie '*'), providers, NNTP, header-index | [MONITORING] (`servertest`, `autoheal`) |
| `features/torrent` | diagnose, beleid/guardrails (private trackers), tuning met evaluatie + rollback, cross-seed, opslag/hardlinks, malware-opruiming (alleen met goedkeuring) | [INTEGRITEIT] (`policy.py`) |
| `features/indexers`, `search`, `newznab_server` | Prowlarr-sync, directe indexers, aggregator, **origin graph** (anti-loop), eigen Newznab/Torznab | [INTEGRITEIT] (`search/origin.py`) |
| `features/mediaguard`, `recovery` | MediaGuard-koppeling (read-only + officiële zoekroute, `ALLOWED_WRITES`), herstel/fallback/blacklist | [INTEGRITEIT] |
| `features/costs` | handmatige kosten, kosten per succes/GB, simulatie met onzekerheid | |
| `features/overview` | dashboard, doelenkaart, "Wacht op jou", "Wat MSO deed" | [MONITORING] |
| `features/automation` | evaluators, Autopilot-zelfbewaking (afkoeling, noodrem), eigen-keuze-overrides, **Voetafdruk** (= wat MSO bij bronnen veranderde) | [INTEGRITEIT] [ROLLBACK] |
| `features/ai` | optionele AI-adviseur: **standaard uit**, maandbudget standaard €0, regelgebaseerde terugval | |

### 2.3 Web (`apps/web/`)

Next.js; 16 schermen + login + setup: Overzicht `/`, Zoeken `/zoeken`, NZB-indexers, Torrent-indexers,
Usenet-providers, Torrentclient, Opslag & seeding `/opslag`, Downloads, Ontbrekend & herstel `/ontbrekend`,
MediaGuard-koppeling, Kosten, Automatisering (+ `/automatisering/voetafdruk`), Logs & audit, Instellingen, Systeem
(Back-up & updates), catch-all. Navigatie: `apps/web/components/shell/nav.ts`. UI-basis is een geversioneerde afgeleide
van MediaGuard (`apps/web/PROVENANCE.md`).

---

## 3. API-routes

- **210 operaties in de OpenAPI** + `/healthz` (niet in de OpenAPI). Momentopname van HEAD:
  `docs/openapi/media-source-optimizer.openapi.json` (185 paden; geëxporteerd 2026-11-09, zie §15). Er is **nog geen
  CI-driftcontrole** op dat bestand (gepland P3).
- Conventies (bestaand): prefix `/api/v1/<gebied>`; fout `{detail, code, correlation_id}` (`apps/core/mso/app.py:87-90`);
  lijsten `{items: […]}`; rollen viewer < operator < admin (`auth.py:26`); CSRF op elke muterende sessie-aanroep
  (`auth.py:105-114`); rate limit per sessie/IP (`app.py:93-117`) + extra limieten op dure 1.6.0-routes (SECURITY.md §7).
- Niet aanwezig (relevant voor de Hub): `/readyz`, `/connector/v1/*`, `Idempotency-Key`-header, `ETag`/`If-Match`,
  cursor-paginering, Bearer-/service-token- of JWT-authenticatie, event-outbox. Zie DELTA_MATRIX §3 en §6.

Volledige lijst (methode · pad · minimale rol · bestand:regel), automatisch uit de code gehaald:

#### `ai` (9)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| POST | `/api/v1/ai/advise` | operator | `apps/core/mso/features/ai/api.py:125` |
| PUT | `/api/v1/ai/config` | admin | `apps/core/mso/features/ai/api.py:34` |
| DELETE | `/api/v1/ai/key` | admin | `apps/core/mso/features/ai/api.py:60` |
| PUT | `/api/v1/ai/key` | admin | `apps/core/mso/features/ai/api.py:53` |
| GET | `/api/v1/ai/prices` | viewer | `apps/core/mso/features/ai/api.py:67` |
| PUT | `/api/v1/ai/prices/{model}` | admin | `apps/core/mso/features/ai/api.py:82` |
| GET | `/api/v1/ai/status` | viewer | `apps/core/mso/features/ai/api.py:21` |
| POST | `/api/v1/ai/test` | admin | `apps/core/mso/features/ai/api.py:103` |
| GET | `/api/v1/ai/usage` | viewer | `apps/core/mso/features/ai/api.py:91` |

#### `audit`, `auth`, `setup`, `users`, `settings` (13)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/audit` | viewer | `apps/core/mso/api/foundation.py:402` |
| POST | `/api/v1/auth/login` | geen (rate limit) | `apps/core/mso/api/foundation.py:120` |
| POST | `/api/v1/auth/logout` | viewer | `apps/core/mso/api/foundation.py:144` |
| GET | `/api/v1/auth/me` | sessie | `apps/core/mso/api/foundation.py:154` |
| POST | `/api/v1/auth/password` | viewer | `apps/core/mso/api/foundation.py:167` |
| GET | `/api/v1/setup/status` | geen | `apps/core/mso/api/foundation.py:70` |
| POST | `/api/v1/setup/admin` | geen (alleen zonder gebruikers) | `apps/core/mso/api/foundation.py:85` |
| POST | `/api/v1/setup/complete` | admin | `apps/core/mso/api/foundation.py:110` |
| GET | `/api/v1/users` | admin | `apps/core/mso/api/foundation.py:190` |
| POST | `/api/v1/users` | admin | `apps/core/mso/api/foundation.py:195` |
| PATCH | `/api/v1/users/{user_id}` | admin | `apps/core/mso/api/foundation.py:209` |
| GET | `/api/v1/settings` | viewer | `apps/core/mso/api/foundation.py:244` |
| PUT | `/api/v1/settings` | admin | `apps/core/mso/api/foundation.py:249` |

#### `system`, `jobs`, `changes`, `connectors`, `backups`, `healthz` (25)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/healthz` | geen | `apps/core/mso/app.py:156` |
| GET | `/api/v1/system/health` | viewer | `apps/core/mso/api/foundation.py:329` |
| GET | `/api/v1/system/info` | viewer | `apps/core/mso/api/foundation.py:386` |
| GET | `/api/v1/system/maintenance` | viewer | `apps/core/mso/retention.py:257` |
| POST | `/api/v1/system/maintenance/run` | admin | `apps/core/mso/retention.py:280` |
| GET | `/api/v1/system/mode` | viewer | `apps/core/mso/api/foundation.py:270` |
| PUT | `/api/v1/system/mode` | admin | `apps/core/mso/api/foundation.py:276` |
| POST | `/api/v1/system/pause` | operator | `apps/core/mso/api/foundation.py:293` |
| POST | `/api/v1/system/resume` | operator | `apps/core/mso/api/foundation.py:300` |
| GET | `/api/v1/jobs` | viewer | `apps/core/mso/api/foundation.py:430` |
| GET | `/api/v1/jobs/{job_id}` | viewer | `apps/core/mso/api/foundation.py:451` |
| POST | `/api/v1/jobs/{job_id}/cancel` | operator | `apps/core/mso/api/foundation.py:459` |
| POST | `/api/v1/jobs/{job_id}/retry` | operator | `apps/core/mso/api/foundation.py:467` |
| GET | `/api/v1/changes` | viewer | `apps/core/mso/api/foundation.py:476` |
| GET | `/api/v1/changes/{change_id}` | viewer | `apps/core/mso/api/foundation.py:486` |
| POST | `/api/v1/changes/{change_id}/approve` | admin | `apps/core/mso/api/foundation.py:491` |
| POST | `/api/v1/changes/{change_id}/apply` | operator (admin bij `toestemming_nodig` zonder goedkeuring) | `apps/core/mso/api/foundation.py:496` |
| POST | `/api/v1/changes/{change_id}/rollback` | operator (verwijderen: admin) | `apps/core/mso/api/foundation.py:508` |
| POST | `/api/v1/changes/{change_id}/discard` | operator | `apps/core/mso/api/foundation.py:516` |
| GET | `/api/v1/connectors` | viewer | `apps/core/mso/api/foundation.py:535` |
| PUT | `/api/v1/connectors/{cid}` | admin | `apps/core/mso/api/foundation.py:549` |
| POST | `/api/v1/connectors/{cid}/test` | operator | `apps/core/mso/api/foundation.py:587` |
| GET | `/api/v1/backups` | admin | `apps/core/mso/api/foundation.py:627` |
| POST | `/api/v1/backups` | admin | `apps/core/mso/api/foundation.py:632` |
| POST | `/api/v1/backups/{backup_id}/restore` | admin (getypte bevestiging) | `apps/core/mso/api/foundation.py:645` |

#### `overview` (4) en `automation` (19)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/overview` | viewer | `apps/core/mso/features/overview/api.py:409` |
| GET | `/api/v1/overview/autopilot` | viewer | `apps/core/mso/features/overview/autopilot.py:678` |
| GET | `/api/v1/overview/goals` | viewer | `apps/core/mso/features/overview/goals.py:694` |
| GET | `/api/v1/overview/waiting` | viewer | `apps/core/mso/features/overview/autopilot.py:684` |
| GET | `/api/v1/automation/evaluations` | viewer | `apps/core/mso/features/automation/api.py:254` |
| GET | `/api/v1/automation/evaluations/{change_id}` | viewer | `apps/core/mso/features/automation/api.py:269` |
| POST | `/api/v1/automation/failures/reset` | operator | `apps/core/mso/features/automation/api.py:314` |
| GET | `/api/v1/automation/footprint` | viewer | `apps/core/mso/features/automation/footprint_api.py:34` |
| POST | `/api/v1/automation/footprint/refresh` | operator | `apps/core/mso/features/automation/footprint_api.py:39` |
| GET | `/api/v1/automation/footprint/restore` | viewer | `apps/core/mso/features/automation/footprint_api.py:46` |
| POST | `/api/v1/automation/footprint/restore` | operator | `apps/core/mso/features/automation/footprint_api.py:56` |
| GET | `/api/v1/automation/hold` | viewer | `apps/core/mso/features/automation/api.py:296` |
| POST | `/api/v1/automation/hold/resume` | operator | `apps/core/mso/features/automation/api.py:302` |
| GET | `/api/v1/automation/recommendations` | viewer | `apps/core/mso/features/automation/api.py:37` |
| POST | `/api/v1/automation/recommendations/{rec_id}/accept` | operator | `apps/core/mso/features/automation/api.py:113` |
| POST | `/api/v1/automation/recommendations/{rec_id}/dismiss` | operator | `apps/core/mso/features/automation/api.py:143` |
| POST | `/api/v1/automation/run` | operator | `apps/core/mso/features/automation/api.py:237` |
| GET | `/api/v1/automation/safety` | viewer | `apps/core/mso/features/automation/api.py:278` |
| GET | `/api/v1/automation/schedules` | viewer | `apps/core/mso/features/automation/api.py:477` |
| GET | `/api/v1/automation/status` | viewer | `apps/core/mso/features/automation/api.py:161` |
| PUT | `/api/v1/automation/toggles` | admin | `apps/core/mso/features/automation/api.py:220` |
| GET | `/api/v1/automation/undo` | viewer | `apps/core/mso/features/automation/api.py:380` |
| POST | `/api/v1/automation/undo` | operator | `apps/core/mso/features/automation/api.py:391` |

#### `usenet` (43)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/usenet/overview` | viewer | `apps/core/mso/features/usenet/api.py:54` |
| GET | `/api/v1/usenet/sab/queue` | viewer | `apps/core/mso/features/usenet/api.py:108` |
| GET | `/api/v1/usenet/sab/history` | viewer | `apps/core/mso/features/usenet/api.py:115` |
| GET | `/api/v1/usenet/sab/servers` | viewer | `apps/core/mso/features/usenet/api.py:122` |
| GET | `/api/v1/usenet/sab/categories` | viewer | `apps/core/mso/features/usenet/api.py:202` |
| PUT | `/api/v1/usenet/sab/category-map` | admin | `apps/core/mso/features/usenet/api.py:233` |
| GET | `/api/v1/usenet/categories/diagnosis` | viewer | `apps/core/mso/features/usenet/api.py:259` |
| POST | `/api/v1/usenet/categories/propose` | admin | `apps/core/mso/features/usenet/api.py:270` |
| POST | `/api/v1/usenet/sab/refresh` | operator | `apps/core/mso/features/usenet/api.py:282` |
| GET | `/api/v1/usenet/sab/server-status` | viewer | `apps/core/mso/features/usenet/api.py:293` |
| GET | `/api/v1/usenet/failure-reasons` | viewer | `apps/core/mso/features/usenet/api.py:309` |
| GET | `/api/v1/usenet/providers` | viewer | `apps/core/mso/features/usenet/api.py:352` |
| GET | `/api/v1/usenet/providers/presets` | viewer | `apps/core/mso/features/usenet/api.py:363` |
| POST | `/api/v1/usenet/providers` | admin | `apps/core/mso/features/usenet/api.py:368` |
| PUT | `/api/v1/usenet/providers/{slug}` | admin | `apps/core/mso/features/usenet/api.py:375` |
| DELETE | `/api/v1/usenet/providers/{slug}` | admin | `apps/core/mso/features/usenet/api.py:387` |
| POST | `/api/v1/usenet/providers/{slug}/test` | operator | `apps/core/mso/features/usenet/api.py:393` |
| POST | `/api/v1/usenet/providers/import-sab` | admin | `apps/core/mso/features/usenet/api.py:403` |
| GET | `/api/v1/usenet/contribution` | viewer | `apps/core/mso/features/usenet/api.py:417` |
| GET | `/api/v1/usenet/nzbs` | viewer | `apps/core/mso/features/usenet/api.py:424` |
| POST | `/api/v1/usenet/nzbs` | operator | `apps/core/mso/features/usenet/api.py:437` |
| POST | `/api/v1/usenet/article-tests` | operator | `apps/core/mso/features/usenet/api.py:457` |
| GET | `/api/v1/usenet/article-tests` | viewer | `apps/core/mso/features/usenet/api.py:473` |
| GET | `/api/v1/usenet/article-tests/{test_id}` | viewer | `apps/core/mso/features/usenet/api.py:480` |
| GET | `/api/v1/usenet/recommendations` | viewer | `apps/core/mso/features/usenet/api.py:486` |
| POST | `/api/v1/usenet/recommendations/priority` | admin | `apps/core/mso/features/usenet/api.py:503` |
| POST | `/api/v1/usenet/recommendations/disable-failing` | admin | `apps/core/mso/features/usenet/api.py:512` |
| GET | `/api/v1/usenet/autopilot` | viewer | `apps/core/mso/features/usenet/api.py:519` |
| POST | `/api/v1/usenet/autopilot/run` | operator | `apps/core/mso/features/usenet/api.py:525` |
| POST | `/api/v1/usenet/sab/servers/{server}/retry` | operator | `apps/core/mso/features/usenet/api.py:534` |
| POST | `/api/v1/usenet/retries/propose` | operator | `apps/core/mso/features/usenet/api.py:543` |
| POST | `/api/v1/usenet/retries/resume` | operator | `apps/core/mso/features/usenet/api.py:554` |
| GET | `/api/v1/usenet/sab/reclassify` | viewer | `apps/core/mso/features/usenet/api.py:565` |
| POST | `/api/v1/usenet/sab/reclassify` | operator | `apps/core/mso/features/usenet/api.py:571` |
| GET | `/api/v1/usenet/stat-budget` | viewer | `apps/core/mso/features/usenet/api.py:579` |
| GET | `/api/v1/usenet/headerindex` | viewer | `apps/core/mso/features/usenet/api.py:586` |
| PUT | `/api/v1/usenet/headerindex/config` | admin | `apps/core/mso/features/usenet/api.py:604` |
| POST | `/api/v1/usenet/headerindex/run` | operator | `apps/core/mso/features/usenet/api.py:635` |
| POST | `/api/v1/usenet/headerindex/purge` | admin | `apps/core/mso/features/usenet/api.py:654` |
| GET | `/api/v1/usenet/headerindex/search` | viewer | `apps/core/mso/features/usenet/api.py:659` |
| GET | `/api/v1/usenet/sab/accounts` | viewer | `apps/core/mso/features/usenet/accounts.py:339` |
| POST | `/api/v1/usenet/sab/servers/{server}/test` | operator (elke modus) | `apps/core/mso/features/usenet/servertest.py:238` |
| GET | `/api/v1/usenet/sab/server-tests` | viewer | `apps/core/mso/features/usenet/servertest.py:256` |

#### `torrent` (29)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/torrent/overview` | viewer | `apps/core/mso/features/torrent/api.py:102` |
| GET | `/api/v1/torrent/torrents` | viewer | `apps/core/mso/features/torrent/api.py:165` |
| GET | `/api/v1/torrent/torrents/{h}` | viewer | `apps/core/mso/features/torrent/api.py:207` |
| POST | `/api/v1/torrent/refresh` | operator | `apps/core/mso/features/torrent/api.py:222` |
| POST | `/api/v1/torrent/torrents/{h}/recheck-preview` | operator | `apps/core/mso/features/torrent/api.py:288` |
| POST | `/api/v1/torrent/torrents/{h}/reannounce-preview` | operator | `apps/core/mso/features/torrent/api.py:293` |
| GET | `/api/v1/torrent/suspicious` | viewer | `apps/core/mso/features/torrent/api.py:305` |
| POST | `/api/v1/torrent/suspicious/cleanup-preview` | operator | `apps/core/mso/features/torrent/api.py:336` |
| GET | `/api/v1/torrent/prevention` | viewer | `apps/core/mso/features/torrent/api.py:360` |
| POST | `/api/v1/torrent/prevention/preview` | operator | `apps/core/mso/features/torrent/api.py:366` |
| GET | `/api/v1/torrent/preferences` | viewer | `apps/core/mso/features/torrent/api.py:376` |
| GET | `/api/v1/torrent/policy` | viewer | `apps/core/mso/features/torrent/api.py:416` |
| POST | `/api/v1/torrent/preferences/preview` | operator | `apps/core/mso/features/torrent/api.py:464` |
| GET | `/api/v1/torrent/tracker-rules` | viewer | `apps/core/mso/features/torrent/api.py:527` |
| POST | `/api/v1/torrent/tracker-rules` | admin | `apps/core/mso/features/torrent/api.py:543` |
| PUT | `/api/v1/torrent/tracker-rules/{rid}` | admin | `apps/core/mso/features/torrent/api.py:570` |
| DELETE | `/api/v1/torrent/tracker-rules/{rid}` | admin | `apps/core/mso/features/torrent/api.py:587` |
| GET | `/api/v1/torrent/obligations` | viewer | `apps/core/mso/features/torrent/api.py:597` |
| GET | `/api/v1/torrent/tuning` | viewer | `apps/core/mso/features/torrent/api.py:604` |
| POST | `/api/v1/torrent/tuning/propose` | operator | `apps/core/mso/features/torrent/api.py:631` |
| POST | `/api/v1/torrent/tuning/evaluate` | operator | `apps/core/mso/features/torrent/api.py:638` |
| GET | `/api/v1/torrent/storage` | viewer | `apps/core/mso/features/torrent/api.py:644` |
| POST | `/api/v1/torrent/storage/scan` | operator | `apps/core/mso/features/torrent/api.py:649` |
| GET | `/api/v1/torrent/cross-seed` | viewer | `apps/core/mso/features/torrent/api.py:662` |
| POST | `/api/v1/torrent/cross-seed/scan` | operator | `apps/core/mso/features/torrent/api.py:681` |
| POST | `/api/v1/torrent/cross-seed/candidates` | operator | `apps/core/mso/features/torrent/api.py:702` |
| POST | `/api/v1/torrent/cross-seed/{cid}/preview` | operator | `apps/core/mso/features/torrent/api.py:724` |
| POST | `/api/v1/torrent/cross-seed/{cid}/reject` | operator | `apps/core/mso/features/torrent/api.py:732` |
| GET | `/api/v1/torrent/clients` | viewer | `apps/core/mso/features/torrent/api.py:745` |

#### `indexers`, `search`, `newznab` (31)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/indexers` | viewer | `apps/core/mso/features/indexers/api.py:57` |
| GET | `/api/v1/indexers/direct` | viewer | `apps/core/mso/features/indexers/api.py:171` |
| POST | `/api/v1/indexers/direct` | admin | `apps/core/mso/features/indexers/api.py:176` |
| PATCH | `/api/v1/indexers/direct/{slug}` | admin | `apps/core/mso/features/indexers/api.py:201` |
| DELETE | `/api/v1/indexers/direct/{slug}` | admin | `apps/core/mso/features/indexers/api.py:222` |
| POST | `/api/v1/indexers/direct/{slug}/test` | operator | `apps/core/mso/features/indexers/api.py:242` |
| POST | `/api/v1/indexers/direct/test` | admin | `apps/core/mso/features/indexers/api.py:282` |
| POST | `/api/v1/indexers/prowlarr/sync` | operator | `apps/core/mso/features/indexers/api.py:307` |
| GET | `/api/v1/indexers/prowlarr/view` | viewer | `apps/core/mso/features/indexers/api.py:323` |
| GET | `/api/v1/indexers/prowlarr/health` | viewer | `apps/core/mso/features/indexers/api.py:331` |
| POST | `/api/v1/indexers/prowlarr/register` | admin | `apps/core/mso/features/indexers/api.py:352` |
| GET | `/api/v1/indexers/recommendations` | viewer | `apps/core/mso/features/indexers/api.py:364` |
| POST | `/api/v1/indexers/reliability/recompute` | operator | `apps/core/mso/features/indexers/api.py:377` |
| GET | `/api/v1/search` | viewer | `apps/core/mso/features/search/api.py:42` |
| POST | `/api/v1/search/compare` | viewer | `apps/core/mso/features/search/api.py:168` |
| POST | `/api/v1/search/flag` | operator | `apps/core/mso/features/search/api.py:225` |
| GET | `/api/v1/search/send-targets` | viewer | `apps/core/mso/features/search/api.py:260` |
| POST | `/api/v1/search/send` | operator | `apps/core/mso/features/search/api.py:332` |
| GET | `/api/v1/search/sends` | viewer | `apps/core/mso/features/search/api.py:425` |
| GET | `/api/v1/search/origin-graph` | viewer | `apps/core/mso/features/search/api.py:433` |
| POST | `/api/v1/search/origin-graph/edges` | admin | `apps/core/mso/features/search/api.py:464` |
| DELETE | `/api/v1/search/origin-graph/edges` | admin | `apps/core/mso/features/search/api.py:482` |
| GET | `/newznab/api` | consumer-apikey | `apps/core/mso/features/newznab_server/api.py:272` |
| GET | `/torznab/api` | consumer-apikey | `apps/core/mso/features/newznab_server/api.py:277` |
| GET | `/api/v1/newznab/endpoint` | viewer | `apps/core/mso/features/newznab_server/api.py:294` |
| GET | `/api/v1/newznab/consumers` | admin | `apps/core/mso/features/newznab_server/api.py:326` |
| POST | `/api/v1/newznab/consumers` | admin | `apps/core/mso/features/newznab_server/api.py:345` |
| PATCH | `/api/v1/newznab/consumers/{cid}` | admin | `apps/core/mso/features/newznab_server/api.py:371` |
| POST | `/api/v1/newznab/consumers/{cid}/reveal` | admin | `apps/core/mso/features/newznab_server/api.py:392` |
| POST | `/api/v1/newznab/consumers/{cid}/rotate` | admin | `apps/core/mso/features/newznab_server/api.py:401` |
| DELETE | `/api/v1/newznab/consumers/{cid}` | admin | `apps/core/mso/features/newznab_server/api.py:419` |

#### `mediaguard` (11) en `recovery` (14)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/mediaguard/status` | viewer | `apps/core/mso/features/mediaguard/api.py:42` |
| GET | `/api/v1/mediaguard/compat` | viewer | `apps/core/mso/features/mediaguard/api.py:48` |
| POST | `/api/v1/mediaguard/probe` | operator | `apps/core/mso/features/mediaguard/api.py:58` |
| POST | `/api/v1/mediaguard/sync` | operator | `apps/core/mso/features/mediaguard/api.py:63` |
| GET | `/api/v1/mediaguard/wanted` | viewer | `apps/core/mso/features/mediaguard/api.py:73` |
| GET | `/api/v1/mediaguard/wanted/{mg_id}` | viewer | `apps/core/mso/features/mediaguard/api.py:87` |
| GET | `/api/v1/mediaguard/kpis` | viewer | `apps/core/mso/features/mediaguard/api.py:109` |
| GET | `/api/v1/mediaguard/reasons` | viewer | `apps/core/mso/features/mediaguard/api.py:115` |
| GET | `/api/v1/mediaguard/quota` | viewer | `apps/core/mso/features/mediaguard/api.py:121` |
| POST | `/api/v1/mediaguard/wanted/{mg_id}/search` | operator | `apps/core/mso/features/mediaguard/api.py:127` |
| GET | `/api/v1/mediaguard/search-requests` | viewer | `apps/core/mso/features/mediaguard/api.py:133` |
| GET | `/api/v1/recovery/baseline` | viewer | `apps/core/mso/features/recovery/api.py:73` |
| GET | `/api/v1/recovery/wanted` | viewer | `apps/core/mso/features/recovery/api.py:81` |
| POST | `/api/v1/recovery/wanted` | operator | `apps/core/mso/features/recovery/api.py:185` |
| DELETE | `/api/v1/recovery/wanted/{item_id}` | operator | `apps/core/mso/features/recovery/api.py:226` |
| GET | `/api/v1/recovery/items/{media_ref}` | viewer | `apps/core/mso/features/recovery/api.py:239` |
| POST | `/api/v1/recovery/items/{media_ref}/candidates` | operator | `apps/core/mso/features/recovery/api.py:291` |
| POST | `/api/v1/recovery/items/{media_ref}/send` | operator | `apps/core/mso/features/recovery/api.py:306` |
| POST | `/api/v1/recovery/items/{media_ref}/request-search` | operator | `apps/core/mso/features/recovery/api.py:311` |
| GET | `/api/v1/recovery/sends` | viewer | `apps/core/mso/features/recovery/api.py:321` |
| GET | `/api/v1/recovery/fallbacks` | viewer | `apps/core/mso/features/recovery/api.py:329` |
| GET | `/api/v1/recovery/blacklist` | viewer | `apps/core/mso/features/recovery/api.py:355` |
| POST | `/api/v1/recovery/blacklist` | operator | `apps/core/mso/features/recovery/api.py:364` |
| DELETE | `/api/v1/recovery/blacklist/{entry_id}` | admin | `apps/core/mso/features/recovery/api.py:371` |
| POST | `/api/v1/recovery/analyze` | operator | `apps/core/mso/features/recovery/api.py:379` |

#### `costs` (13)

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/costs/services` | viewer | `apps/core/mso/features/costs/api.py:101` |
| POST | `/api/v1/costs/services/import` | admin | `apps/core/mso/features/costs/api.py:130` |
| PATCH | `/api/v1/costs/services/{service_id}/price` | admin | `apps/core/mso/features/costs/api.py:146` |
| GET | `/api/v1/costs/sources` | viewer | `apps/core/mso/features/costs/api.py:176` |
| POST | `/api/v1/costs/services` | admin | `apps/core/mso/features/costs/api.py:182` |
| GET | `/api/v1/costs/services/{service_id}` | viewer | `apps/core/mso/features/costs/api.py:199` |
| PUT | `/api/v1/costs/services/{service_id}` | admin | `apps/core/mso/features/costs/api.py:205` |
| DELETE | `/api/v1/costs/services/{service_id}` | admin | `apps/core/mso/features/costs/api.py:231` |
| GET | `/api/v1/costs/services/{service_id}/analysis` | viewer | `apps/core/mso/features/costs/api.py:245` |
| GET | `/api/v1/costs/services/{service_id}/simulate` | viewer | `apps/core/mso/features/costs/api.py:255` |
| GET | `/api/v1/costs/scenarios` | viewer | `apps/core/mso/features/costs/api.py:284` |
| POST | `/api/v1/costs/scenarios` | viewer | `apps/core/mso/features/costs/api.py:291` |
| GET | `/api/v1/costs/summary` | viewer | `apps/core/mso/features/costs/api.py:296` |

Totaal: 211 operaties (210 in de OpenAPI + `/healthz`). Extractiescript: AST over `apps/core/mso` (zie §15).

---

## 4. Database-migraties

Mechanisme: `apps/core/mso/db.py` — SQL-bestanden met `-- migrate:up` / `-- migrate:down`, elk in een eigen transactie,
tabel `schema_migrations` (versie, naam, checksum), advisory lock 727001, schemaversie = hoogste toegepaste nummer.
Alle 22 migraties hebben een down-sectie. **[ROLLBACK]**

| Migratie | Nieuwe tabellen |
|---|---|
| `0001_foundation` | users, sessions, settings, secrets, connectors, audit_log, jobs, change_sets, login_attempts, backups, system_state |
| `0002_shared_domain` | download_outcomes, sources, services, observations, recommendations |
| `0100_indexers_search` | direct_indexers, indexer_usage, newznab_consumers, release_cache, search_sends, origin_edges |
| `0200_usenet` | usenet_providers, usenet_ingest_state, usenet_nzbs, usenet_server_stats, usenet_article_tests, usenet_article_results, usenet_hi_groups, usenet_hi_headers, usenet_hi_runs |
| `0201_usenet_sab_server_status` | usenet_sab_servers, usenet_sab_server_events |
| `0202_usenet_missing_credentials` | (kolommen) |
| `0203_usenet_auto_stat` | usenet_stat_usage |
| `0204_usenet_autopilot` | usenet_server_autoheal, usenet_retries |
| `0300_torrent` | torrent_tracker_rules, torrent_state, torrent_samples, torrent_client_snapshot, torrent_tuning_runs, torrent_crossseed_candidates, torrent_actions, torrent_storage_scan |
| `0301_torrent_cleanup` | torrent_cleanup_previews |
| `0400_mediaguard_recovery` | mg_wanted_cache, mg_snapshots, mg_search_requests, mg_search_backoff, recovery_blacklist, recovery_media_backoff, recovery_manual_wanted, recovery_candidates, recovery_sends, recovery_outcome_seen |
| `0401_recovery_fallback` | recovery_fallbacks |
| `0402_recovery_autopilot` | (kolommen) |
| `0500_costs_automation_ai` | automation_log, ai_model_prices, ai_usage |
| `0501_costs_import` | cost_import_skips |
| **`0600_v160_base`** | change_overrides (+ kolommen serverbewaking) |
| `0601_v160_usenet_cause` | usenet_server_coverage (+ correctie artikeltellers) |
| `0602_v160_server_tests` | usenet_server_tests, usenet_server_accounts |
| `0603` | **bestaat niet** (bewust; categoriediagnose had geen schema nodig — `docs/MIGRATION.md`) |
| `0604_v160_goals` | goal_snapshots (+ `download_outcomes.failed_at`) |
| `0605_v160_change_safety` | automation_failures (+ status `applying`) |
| `0606_v160_maintenance` | observations_hourly (+ connectorstatuskolommen) |
| `0607_footprint` | change_footprint |

Migratietests (bestaand): `tests/test_foundation.py::test_migrations_down_and_up` (alles up → down → up),
`tests/test_usenet_stagelog.py::test_migration_0601_corrects_stored_counters_and_rolls_back`,
`tests/test_overview_goals.py::test_migration_backfill_and_down_up`,
`tests/test_maintenance.py::test_pre_upgrade_backup_before_pending_migrations`,
`::test_restore_of_an_older_dump_migrates_down_first_and_up_after`, `::test_restore_of_a_newer_dump_is_refused`;
containerniveau: `tests/stack/allinone_test.py` scenario 10 (upgrade vanaf 1.5.0-schema 501 → 607 met back-up vooraf;
gedraaid in de release-run). **Niet aanwezig:** een weigering om te starten als de database een **nieuwer** schema
heeft dan het image kent (`db.py:126-152` slaat onbekende toegepaste versies stil over) — relevant voor EX15.

---

## 5. Authenticatie, rollen en sleutels

| Richting | Mechanisme | Bewijs |
|---|---|---|
| Gebruiker → MSO | lokaal account, scrypt (n=2¹⁵), sessiecookie `mso_session` (HttpOnly, SameSite=Strict, Secure optioneel), CSRF-synchronisertoken, brute-forcelimiet | `apps/core/mso/auth.py:1-126`; SECURITY.md §2 |
| Rollen | `viewer` < `operator` < `admin` (`ROLE_RANK`) | `auth.py:26`, `require()` `auth.py:117` |
| Prowlarr/Sonarr/Radarr → MSO-feeds | Newznab/Torznab consumer-API-sleutels (per consumer, roteerbaar) | `features/newznab_server/keys.py`, routes `/api/v1/newznab/consumers*` |
| Machine → MSO-API (Hub) | **geen** (geen Bearer/service-token, geen JWT/JWKS); alleen een gewone gebruikerssessie + CSRF | `auth.py:92-114` |
| MSO → MediaGuard | `X-Api-Key` (aanbevolen, rol operator/viewer), of eigen gebruiker (sessie in geheugen), of `X-MediaGuard-Token` (noodoplossing, UI waarschuwt) | `connectors/mediaguard.py:8, :185`; INTEGRATION_MEDIAGUARD.md §2 |
| MSO → SAB/qBit/Prowlarr | API-sleutels/sessies uit de kluis | `vault.py`; `connectors/*` |
| Geheimen | AES-256-GCM, sleutel `APPDATA/secrets/vault.key` (0600), geheimnaam als AAD, nooit in API-antwoord of back-up (tenzij expliciet) | `vault.py`; `tests/test_foundation.py::test_vault_encrypts_and_never_exposes` |

Afwijkingen t.o.v. het platformcontract §4 (argon2id, JWT van de Hub, service-tokens): zie DELTA_MATRIX §6.

## 6. Events en notificaties

- **Auditlog** `audit_log` (wie/wanneer/wat/doel/oud→nieuw, geredigeerd, nooit opgeruimd): `apps/core/mso/audit.py`,
  `GET /api/v1/audit`. **[INTEGRITEIT]**
- **Automatiseringslog** `automation_log` (180 dagen), **"Wacht op jou"**-inbox = `recommendations` +
  `overview/autopilot.waiting()` (`GET /api/v1/overview/waiting`), **"Wat MSO deed"** (`GET /api/v1/overview/autopilot`).
- Koppelingsstatus per connector (`connectors.status`, `status_since`, `last_ok_at`, `failure_kind`).
- **Niet aanwezig:** push-notificaties (Gotify e.d.), webhooks, event-outbox volgens contract §7, `event_id`-envelope.
  MSO is voor meldingen volledig in-app.

## 7. Back-up, herstel en rollback  **[ROLLBACK]**

| Functie | Waar | Test (bestaand) |
|---|---|---|
| Dagelijkse + handmatige back-up (`pg_dump -Fc` + `.sha256`), rotatie `backup.keep` | `backups.py:179-229`, job `backup.create` | `test_foundation.py::test_backup_and_restore_roundtrip` |
| Verificatie vóór herstel (bestaat, niet leeg, SHA-256, geldig archief) | `backups.py:231` | `::test_restore_refuses_corrupt_backup` |
| Herstel met getypte bevestiging, vangnet-back-up `pre-restore`, oudere dump: down → restore → up; nieuwere geweigerd | `backups.py:246-335` | `test_maintenance.py::test_restore_of_an_older_dump_migrates_down_first_and_up_after`, `::test_restore_of_a_newer_dump_is_refused` |
| Modus/pauze/noodrem blijven bij herstel; `applying` → `failed` met één "Wacht op jou"-item | `backups.py:72, :343-376` | `::test_restore_keeps_mode_pause_and_noodrem_and_settles_applying` |
| Audit van ná de dump blijft; oude jobs geannuleerd | `backups.py:65` | `test_foundation.py::test_restore_keeps_newer_audit_and_cancels_old_jobs` |
| Refetchbare tabellen zonder data + resync na herstel | `backups.py:63, :377` | `test_maintenance.py::test_backup_excludes_refetchable_data_and_resyncs_after_restore` |
| Back-up vóór upgrade (`pre-upgrade`, nieuwste 3) | `backups.py:462-560`; `app.py:75-80` | `test_maintenance.py::test_pre_upgrade_*` |
| Change-set-rollback met driftcontrole per op, teruglezen | `changes.py:391` | `test_change_safety.py::test_rollback_*` |
| Autopilot terugdraaien (24 u / 7 d), Voetafdruk "Alles terugzetten" | `features/automation/api.py:380-391`, `footprint_api.py:46-56` | `test_change_safety.py::test_undo_autopilot_window`, `test_footprint.py::test_restore_all_preview_roles_policy_and_flow` |
| Schema-rollback naar oudere versie | **alleen via terminal** (`docker exec … mso migrate --down 501`) | `docs/MIGRATION.md` "Terug naar 1.5.0"; `docs/TRUENAS_CUSTOM_APP.md:81-97` |

## 8. Monitoring en health  **[MONITORING]**

| Wat | Waar |
|---|---|
| `GET /healthz` (DB-ping, geen auth) | `app.py:156-161` |
| `GET /api/v1/system/health` (DB, worker-hartslag, vrije ruimte, koppelingen; `healthy/degraded`) | `api/foundation.py:329` |
| Container-healthchecks (`mso-healthcheck api|worker`, `pg_isready`, web `/login`) | `apps/core/docker/healthcheck.py`; `compose.yaml` |
| Worker-hartslag in `system_state` (< 90 s) en job-heartbeats/lease | `jobs.py:59-120` |
| Koppelingsbewaking (één uitspraak per koppeling per job, backoff 15 min bij down) | `connectors/monitor.py`; `tests/test_connector_monitoring.py` |
| SABnzbd-serverbewaking via SABnzbd's eigen servertest, autoheal | `features/usenet/servertest.py`, `autoheal.py` |
| MediaGuard-status/capabilities/noodstop | `features/mediaguard/service.py:98, :126-144` |
| Doelenkaart (faalratio, verdachte torrents, falende servers) | `features/overview/goals.py` |
| Onderhoud: DB-grootte, top-6 tabellen, bewaartermijnen, back-upruimte | `retention.py:244-278` |
| **Niet aanwezig:** `/readyz`, `/connector/v1/health` met `healthy|degraded|down|unknown`, metrics-export | — |

## 9. Noodstop en veiligheidsinvarianten  **[INTEGRITEIT]**

| Invariant | Waar | Test |
|---|---|---|
| Standaard **alleen-lezen**; schrijven alleen in beheermodus/automatisering/Autopilot | `state.py:41-72` | `test_autopilot.py::test_read_only_blocks_everything`, `test_torrent_engine.py::test_writes_refused_in_read_only_mode` |
| Pauzeknop (operator) | `api/foundation.py:293-300` | — |
| **Noodrem** Autopilot (≥3 auto-rollbacks of ≥5 mislukkingen/24 u) + Hervatten | `features/automation/autopilot.py`, `state.py:21, :49` | `test_change_safety.py::test_noodrem_*` |
| Afkoeling, uurlimiet, eigen-keuze-override (30 d) | `autopilot.py`, `overrides.py` | `test_change_safety.py::test_failed_automatic_apply_cools_down_then_parks`, `::test_owner_override_blocks_automatic_reapply` |
| Vergrendelde grenzen: nooit bestanden verwijderen, nooit seedende data herschrijven, geen publieke trackers/DHT op private, nooit abonnementen opzeggen, MediaGuard-kwaliteit leidend | `settings_registry.py:166-206` | `test_foundation.py::test_locked_integrity_settings_cannot_change` |
| Verwijderen/uitschakelen nooit automatisch (uitschakelen alleen in Autopilot), verwijderen alleen met goedkeuring | `changes.py:243-256` | `test_foundation.py::test_destructive_ops_need_permission_never_automatic_and_irreversible_not_rolled_back` |
| Nooit de laatste ingeschakelde Usenet-server uit | `features/usenet/autoheal.py:848` | `test_usenet_autopilot_servers.py::test_never_switches_off_the_last_enabled_server` |
| Torrent-delete alleen in goedgekeurde malware-opruiming, hercontrole bij toepassen | `features/torrent/cleanup.py` | `test_torrent_cleanup.py::test_non_suspicious_never_deletable`, `::test_drift_refuses_whole_bundle` |
| MediaGuard: alleen GET + `POST wanted/{id}/search` + blokkeerlijst-`DELETE` met vaste parameters; client weigert de rest vóór I/O | `connectors/mediaguard.py:42, :206` | `test_mediaguard_integration.py::test_client_refuses_forbidden_write_routes_before_io` |
| MediaGuard-noodstop → geen zoekaanvragen; quotum + reserve + backoff | `features/mediaguard/service.py:98, :615` | `test_mediaguard_integration.py::test_emergency_stop_blocks_search_requests`, `::test_quota_reserve_left_for_mediaguard_backfill`, `::test_failed_requests_back_off_exponentially` |
| SABnzbd-categorie aanmaken altijd `toestemming_nodig`; nooit automatisch terugdraaien (zou verwijderen) | `features/usenet/categories.py:555`, `changes.py:81` | `test_usenet_categories.py::test_applied_category_is_never_rolled_back_automatically` |
| Anti-loop Prowlarr ↔ MSO (origin graph, hop-limiet) | `features/search/origin.py` | `test_search_origin.py::test_no_loop_when_prowlarr_calls_mso_back` |
| Nep-releasecontrole vóór elke eigen verzending | `domain/releasegate.py` | `test_release_gate.py` |
| SSRF, redactie, rate limits, security headers | `security.py`, `app.py:93-117` | `test_foundation.py::test_ssrf_guard`, `::test_redaction_in_logs` |
| Geen Docker-socket, geen host-network, geen privileged, `cap_drop: ALL`, `no-new-privileges`, read-only rootfs, UID 568 | `deploy/truenas/compose.yaml:37-46` | stack-smoke |
| AI-adviseur standaard uit, budget €0, regelgebaseerde terugval | `features/ai/__init__.py:12-30`, `budget.py:92` | `test_ai_advisor.py` |

## 10. CI, E2E en testbewijs

| Onderdeel | Inhoud | Status |
|---|---|---|
| `.github/workflows/ci.yaml` | jobs `python` (ruff + pytest met postgres:16.15-alpine-service), `web` (typecheck, eslint, `next build`), `images` (core/web/mocks; push alleen bij `v*`), `stack` (`tests/stack/run.sh`), `e2e` (stack + Playwright) | **nooit gedraaid op GitHub** (trigger `main`/`v*`/PR; zie §1) |
| `.github/workflows/release.yaml` | trigger: push die `release/VERSION` wijzigt (**op elke branch**) of handmatig; `test` (ruff + pytest) → `publish` (all-in-one bouwen + `allinone_test.py` → push all-in-one, core, web → tag) | 9 runs, alle success (1.0.0 … 1.6.0) |
| Backend | `apps/core/tests/` 59 bestanden, 662 testfuncties (789 testgevallen incl. parametrisatie) | eigen rapport 789/789 (TEST_RESULTS.md); **deze sessie opnieuw 789/789** (§15) |
| Containerstack | `tests/stack/smoke.py` 19 checks | eigen rapport 19/19 (`docs/test-evidence/stack-smoke-report.md`) |
| Alles-in-één | `tests/stack/allinone_test.py` 10 scenario's | 10/10 lokaal én in release-run 42575858717 |
| Playwright | `apps/web/e2e/{screens,dialogs,smoke}.spec.ts`, projecten **1440 en 390** (geen 360/768) | eigen rapport 78/78 (`docs/test-evidence/playwright.log`, 64 screenshots) |
| Ontbrekend t.o.v. contract §10 | mypy, pip-audit, OpenAPI-drift, SBOM, `release/digests.json`, Playwright 360/768 | — |

## 11. Images en tags in `deploy/` en Dockerfiles

| Referentie | Tag | Digest | Contract (§1/§9) |
|---|---|---|---|
| `deploy/truenas/compose.allinone.yaml:8` `ghcr.io/lvos04/media-source-optimizer` | `1.6.0` | niet in bestand (GHCR: `sha256:9bcdec69…3da`) | eigen image: digest door release-CI in te vullen → **ontbreekt** |
| `deploy/truenas/compose.yaml:72,122,138,164` `…-core` | `1.6.0` | geen | idem; bovendien **privé** op GHCR |
| `deploy/truenas/compose.yaml:186` `…-web` | `1.6.0` | geen | idem; **privé** |
| `deploy/truenas/compose.yaml:93` `postgres` | `16.15-alpine` | `sha256:0cefaabd…31ab` (nog op te halen; huidige tag-digest is inmiddels `aa6cb33c…9139`) | ✔ |
| `apps/core/Dockerfile:12`, `deploy/allinone/Dockerfile:11`, `apps/mocks/Dockerfile:7` `python` | `3.13.18-slim-bookworm` | **geen** (actueel `sha256:a5a7017e…5c72`) | base-image zonder digest → ✘ |
| `apps/core/Dockerfile:13`, `deploy/allinone/Dockerfile:12` `postgres` | `16.15-bookworm` | **geen** (actueel `sha256:5264710b…a8d9`) | ✘ |
| `apps/web/Dockerfile:5`, `deploy/allinone/Dockerfile:13` `node` | **`22-bookworm-slim` (zwevende major-tag)** | **geen** (actueel `sha256:5ec23b7c…6d5e`) | ✘ (geen exacte versie) |
| CI-service `postgres:16.15-alpine` (`ci.yaml`, `release.yaml`) | tag | geen | CI-only |

Geen `latest`-tags gevonden. Voorbeeldwaarden die als standaard in deploy-bestanden staan (in strijd met "nooit
voorbeeldwaarden als echte waarden"): poolnaam **`/mnt/tank/…`** (`compose.yaml:63-68, :85-86, :104-105`;
`compose.allinone.yaml:13, :15`), `TZ: Europe/Amsterdam` (redelijke standaard, wel instelbaar). Er is **geen
`.env.example`** en **geen manifest** volgens contract §9 (`deploy/manifest/media-source-optimizer.manifest.yaml`).

## 12. Releasebestanden

`release/VERSION` · `CHANGELOG.md` · `TEST_RESULTS.md` · `docs/test-evidence/` (pytest-junit, stack-, allinone-rapport,
playwright.log) · `docs/screenshots/` (64 png) · `docs/HARDWARE_ACCEPTANCE.md` (live-checklist, alles NIET
GEVERIFIEERD) · `docs/MIGRATION.md` · `scripts/build-images.sh` · `.github/workflows/release.yaml`.
Ontbreekt: `release/digests.json`, SBOM, GitHub Release-object met notities.

## 13. Wat nooit dubbel gebouwd of uitgeschakeld mag worden (samenvatting)

1. **Change sets** (`changes.py`): preview, goedkeuring, journaal, advisory lock, teruglezen, driftcontrole, reconciler,
   `rollback_deletes`-bescherming. De Hub keurt goed **via** `POST /api/v1/changes/{id}/approve|apply` (of een dunne
   connector-laag daarbovenop), nooit met een eigen schrijfpad naar SABnzbd/qBittorrent/Prowlarr.
2. **Modi, pauze, noodrem, afkoeling, eigen-keuze-overrides** (`state.py`, `features/automation/*`).
3. **Vergrendelde integriteitsgrenzen** (`settings_registry.py:166-206`).
4. **Back-up/herstel/pre-upgrade-back-up/migratie-rollback** (`backups.py`, `db.py`).
5. **Koppelingsbewaking, servertest/autoheal, doelenkaart, onderhoud** (`connectors/monitor.py`, `features/usenet/*`,
   `features/overview/goals.py`, `retention.py`).
6. **MediaGuard-schrijfgrens** `ALLOWED_WRITES` en noodstop/quotumrespect (`connectors/mediaguard.py`).
7. **Anti-loop origin graph** en **nep-releasecontrole**.
8. **Auditlog** (nooit opruimen) en redactie.

## 14. Overige bevindingen (huishouding)

- `apps/core/mso_core.egg-info/` is **in git opgenomen** en verouderd (`PKG-INFO` zegt versie 1.0.0); een
  `pip install -e` wijzigt deze getrackte bestanden. Voorstel P3: uit git halen + in `.gitignore`.
- `apps/core/pyproject.toml` verwijst naar `THIRD_PARTY.md`, dat niet bestaat.
- CI/Dockerfiles gebruiken Python **3.13**; het contract noemt 3.12 voor *nieuwe* repos. Voor deze bestaande repo is
  3.13 behouden (geen regressierisico nemen); vastgelegd in ADR-0003.
- Eerste beheerder: `POST /api/v1/setup/admin` (`api/foundation.py:85`) werkt zonder authenticatie zolang er nog geen
  gebruiker bestaat (met tabelslot en rate limit). Wie MSO na installatie als eerste bereikt, wordt beheerder. Het
  contract eist een setupcode alleen voor de Hub; voor MSO genoteerd als vervolgpunt (geen P3-blokkade).
- Poort `38081` (LAN, buiten TrueNAS-catalogusbereik 30000–31000) — conflict met andere apps op de NAS onbekend (D03).
- `docs/MIGRATION.md`, `docs/OPERATIONS.md` en `docs/TRUENAS_CUSTOM_APP.md` gebruiken shell-commando's
  (`sudo docker exec …`) voor terugdraaien en handmatig herstel; strijdig met "geen terminal" (contract §1).

## 15. Baselinetestrun (2026-11-09, deze sessie)

Omgeving: Python 3.13.18 (zelfde minor als CI `PYTHON_VERSION: "3.13"`), venv `.venv` (niet gecommit, staat in
`.gitignore`), gedeelde PostgreSQL 16.15 op 127.0.0.1:5432 (de suite maakt zelf een database `mso_test_<hex>` en
verwijdert die weer; na afloop gecontroleerd: geen `mso_*`-database achtergebleven), `pg_dump`/`pg_restore` 16.15.
Zelfde vlaggen als de CI-job `python` in `.github/workflows/ci.yaml` (alleen de database-URL verschilt).

| # | Commando (vanuit de repo-root tenzij anders) | Uitkomst |
|---|---|---|
| 1 | `.venv/bin/ruff check apps/core apps/mocks tests/stack` (= CI-stap "Ruff", ruff 0.17.3) | **All checks passed!** (exit 0) |
| 2 | `cd apps/core && MSO_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/postgres nice -n 10 ../../.venv/bin/python -m pytest -q -p no:warnings --junitxml=<scratch>/pytest-full.xml` (= CI-stap "Pytest", volledige backend-suite) | **789 geslaagd, 0 mislukt, 0 fouten, 0 overgeslagen** — 59 testbestanden, 587 s (09:03:38Z–09:13:27Z), exit 0. Gelijk aan het eigen 1.6.0-rapport (`TEST_RESULTS.md`: 789/789) |
| 3 | OpenAPI-export van HEAD: `create_app().openapi()` op een tijdelijke database `mso_p0_openapi` (daarna verwijderd), weggeschreven met `json.dumps(spec, indent=1, ensure_ascii=False, sort_keys=True)` → `docs/openapi/media-source-optimizer.openapi.json` | 185 paden, 210 operaties, OpenAPI 3.1.0, `info.version` 1.6.0; migraties 0001…0607 toegepast (0603 bestaat niet). Inhoudelijk identiek aan het concept uit de afgebroken poging |
| 4 | Route-extractie (eigen AST-script, alleen lezen) over `apps/core/mso` | 211 operaties = 210 in de OpenAPI + `/healthz`; 7 zonder `require()` (setup/status, setup/admin, auth/login, auth/me, healthz, newznab/api, torznab/api) |

**Niet gedraaid (bewust, met reden):** web (`npm ci && npm run typecheck && npm run lint && npm run build`: zware
`node_modules`-installatie op een gedeelde machine, geen wijziging aan de web-code in P0), containerstack
(`tests/stack/run.sh`: bouwt 3 images en start 8 containers), Playwright (`make e2e`) en de alles-in-één-test
(`tests/stack/allinone_test.py`: vereist root en een image-build). Die horen bij P3 bij de eerste codewijziging; de
alles-in-één-test is in release-run 42575858717 op GitHub geslaagd (stap "End-to-end test of the all-in-one image" =
success). **Niets** hiervan is een test op echte TrueNAS-hardware of tegen echte diensten.

## 16. MediaGuard v1.2.2 — statische compatibiliteitscontrole (B05-006)

Alleen lezen in de naastliggende repo `media-guard-ultimate`: tag `v1.2.2` (`30f7415`) is voorouder van daar HEAD
`60b50ab`, en `apps/core` is sinds de tag ongewijzigd (alleen `docs/platform-v2`). Met dezelfde AST-extractie
(410 routes) is nagegaan of elke MediaGuard-route uit `INTEGRATION_MEDIAGUARD.md` §3 bestaat:

| Route die MSO gebruikt | In MediaGuard v1.2.2 | Rol daar |
|---|---|---|
| `GET /api/v1/health`, `GET /api/v1/status` | `api/system.py:19`, `:25` | — / viewer |
| `POST /api/v1/auth/login`, `GET /api/v1/auth/me` | `api/auth.py:78`, `:151` | — / sessie |
| `GET /api/v1/wanted`, `/wanted/kpis`, `/wanted/reasons`, `/wanted/backfill/quota`, `/wanted/{id}`, `/wanted/{id}/diagnosis` | `wanted/api.py:229`, `:248`, `:257`, `:301`, `:354`, `:364` | viewer |
| `POST /api/v1/wanted/{id}/search` | `wanted/api.py:394` | operator |
| `GET /api/v1/policy/effective` | `policy/api.py:119` | viewer |
| `GET /api/v1/instances` | `instances/api.py:140` | viewer |
| `GET /api/v1/instances/{id}/queue`, `DELETE …/queue/{queue_id}` (`blocklist`, `remove_from_client`) | `instances/manage.py:862`, `:870` | viewer / operator |
| `GET /api/v1/instances/{id}/download-clients` | `instances/manage.py:565` (gegenereerd per segment) | viewer |
| `GET /api/v1/instances/{id}/health` | `instances/manage.py:921` | viewer |
| `GET /api/v1/provisioning/config` | `provisioning/api.py:357` | viewer |
| `X-Api-Key`-authenticatie | `apikeys.py:32` | per sleutel |

(paden relatief aan `media-guard-ultimate/apps/core/mediaguard/`). Conclusie: alle 18 routes bestaan met de rol die
MSO verwacht. Dit is een **statische** controle: of de antwoordvelden overeenkomen met MSO's mock is niet getest —
dat is P3-09 (contracttest tegen de publieke image `ghcr.io/lvos04/mediaguard-core:1.2.2`,
`sha256:a8daf269e93285cf71b6e9a18e2f75f7b52fa07da230c679dd71b14bc85bbc2b`). Live tegen MediaGuard op de NAS:
**NIET GEVALIDEERD (echte NAS)**.
