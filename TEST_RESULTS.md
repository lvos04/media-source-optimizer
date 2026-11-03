# Testresultaten — Media Source Optimizer 1.0.0

Datum: 2 november 2026. Alles hieronder is **echt uitgevoerd** in een Linux-sandbox (4 vCPU, 15 GB RAM,
Docker 29.9, PostgreSQL 16.15) tegen mock-diensten die de publieke API's van Prowlarr, SABnzbd, qBittorrent,
MediaGuard, Newznab/Torznab, Spotweb en NNTP nabootsen. Ruwe bewijsstukken: `docs/test-evidence/`.

**Niet uitgevoerd** (geen hardware/credentials beschikbaar): installatie op echte TrueNAS SCALE, echte
Prowlarr/SABnzbd/qBittorrent/MediaGuard-instanties, echte Usenet-providers, echte trackers, CI op GitHub.
Zie [docs/HARDWARE_ACCEPTANCE.md](docs/HARDWARE_ACCEPTANCE.md).

## Samenvatting

| Suite | Resultaat | Bewijs |
|---|---|---|
| Backend unit/contract/integratie (pytest, echte PostgreSQL, mocks over echte HTTP) | **310 / 310 geslaagd**, 0 overgeslagen (219 s) | `docs/test-evidence/pytest-junit.xml` |
| Lint (ruff: core, mocks, stacktests) | 0 meldingen | — |
| Web typecheck + eslint + `next build` | 0 fouten (build via web-image) | image `media-source-optimizer-web` |
| Containerstack (gebouwde images + 7 mocks) smoke/e2e | **19 / 19 geslaagd** | `docs/test-evidence/stack-smoke-report.md` |
| Playwright (15 schermen × donker/licht × 1440/390 + toetsenbord, alleen-lezen, login) | **68 / 68 geslaagd** | `docs/test-evidence/playwright.log`, `docs/screenshots/` (60 png) |

## 0. Aanvulling 1.1.0 — alles-in-één image (3 nov 2026)

`tests/stack/allinone_test.py` tegen de gebouwde image: **9 / 9 scenario's geslaagd**
(`docs/test-evidence/allinone-report.md`): start als root op lege map (eigenaar → 568, alle processen behalve
dumb-init als 568), setup/login/CSRF + SABnzbd-koppeling, back-up + herstel, `docker restart`, harde kill (SIGKILL) met
achtergebleven `postmaster.pid`, netjes stoppen (0,5 s), start als `--user 568`, duidelijke foutmelding bij een
root-map, geen geheimen in de log. Backend opnieuw: 311 tests geslaagd (incl. nieuwe socket-URL-test).
Niet geverifieerd: het TrueNAS Custom App-formulier op echte hardware.

## 1. MediaGuard bit-voor-bit ongewijzigd (acceptatiecriterium 1)

- Lokale referentiekloon `lvos04/media-guard-ultimate@e92c2acc`: HEAD gelijk, `git status --porcelain --ignored`
  leeg, SHA-256 over index én over alle werkbestanden identiek aan de meting vóór de bouw.
- Deze sessie had uitsluitend **leestoegang** op MediaGuard; er is niets gepusht, geen PR, geen branch.
- De remote van MediaGuard kreeg tijdens de bouw 3 nieuwe commits (`2a2d794`, `f76ad67`, `202dccc`) vanuit
  een **andere** MediaGuard-werksessie (encoder, migratiewizard, release-workflow). Geen van de bestanden waar
  MSO op leunt (wanted-API, auth, policy, Prowlarr-categorieën/tags, `mg.css`) is daarin gewijzigd.
- Testcode controleert dat MSO-broncode geen MediaGuard-DB/paden/tabellen gebruikt
  (`test_mediaguard_integration.py`).

## 2. Acceptatiecriteria (plan §0) — status

| # | Criterium | Status | Bewijs |
|---|---|---|---|
| 1 | MediaGuard ongewijzigd | ✅ | §1 |
| 2 | Zelfstandig met werkende login | ✅ | stack: setup/login/logout/rollen; Playwright login via formulier |
| 3 | Verbindingstest + uitlezing van 3 bestaande systemen | ✅ tegen mocks, ⚠ echt niet geverifieerd | stack `koppelingen`: Prowlarr, SABnzbd, qBittorrent (+ MediaGuard) getest en uitgelezen |
| 4 | Effectieve, cyclusvrije Newznab/Torznab | ✅ | `test_newznab_server.py`, `test_search_origin.py` (zonder bewaking 7 geneste zoekopdrachten, met bewaking 1); `/newznab/api?t=caps` en `/torznab/api?t=caps` via de webpoort |
| 5 | Vier bibliotheekcategorieën correct | ✅ | `test_domain.py`, categorie-matrix in `test_search_service.py`, `test_newznab_server.py`, `test_recovery_quality.py` |
| 6 | Privacy/seeding/AV1-grenzen gerespecteerd | ✅ | vergrendelde instellingen; `test_torrent_engine.py` (geen DHT/trackers op private, geen delete-aanroep, seedverplichtingen), hardlink-rapportage read-only |
| 7 | Rollback en herstartveilige jobs | ✅ | change-set-tests, migraties down/up, stack: crash-job hervat, worker SIGKILL, restart/down-up, back-up/herstel |
| 8 | Visueel en toegankelijk als MediaGuard | ✅ | gedeelde tokens/componenten (PROVENANCE), axe WCAG 2.2 AA 0 serious/critical op alle schermen/thema's/breedtes, screenshots naast MediaGuard-referentie vergeleken |
| 9 | Bewijs uit CI, testlogs, schermopnamen | ✅ lokaal, ⚠ CI-workflow aanwezig maar niet op GitHub gedraaid | `docs/test-evidence/`, `docs/screenshots/`, `.github/workflows/ci.yaml` |
| 10 | Eén complete TrueNAS-installatiehandleiding | ✅ (niet op hardware geverifieerd) | `docs/TRUENAS_INSTALL.md` |
| 11 | Reële, begrensde kosten/benefit-analyse | ✅ | `test_costs_analysis.py` (geen garantieteksten, onzekerheidsmarges, dekking) |

## 3. Plan §7 — testmatrix

| Plan §7 | Waar getest | Resultaat |
|---|---|---|
| 1 MediaGuard git status voor/na | §1 | ✅ |
| 2 Standalone stack, persistentie, login/logout | stack-smoke 1–6, 15–16 | ✅ (TLS/reverse proxy: alleen gedocumenteerd) |
| 3 Prowlarr: stats, test, indexerstatus, tag mapping, geen query loop | `test_indexers_prowlarr.py`, `test_search_origin.py` | ✅ mock; echte sandbox-Prowlarr niet beschikbaar |
| 4 SABnzbd: verlopen account, auth, artikel ontbreekt, succes; per server; prioriteit read-only/dry-run/apply/revert | `test_sabnzbd_*.py`, `test_usenet_*.py` | ✅ |
| 5 qBittorrent: public/private, rate policies, ratio/seedtime, stalled, tracker fail, disk pressure, auto-tune rollback, geen dataverlies | `test_torrent_engine.py`, `test_qbittorrent_connector.py` | ✅ |
| 6 Newznab/Torznab XML/caps/TV/movie/search, paginering, categorie | `test_newznab_server.py` | ✅ |
| 7 MediaGuard online/offline, capability-detectie, geen dubbele agents | `test_mediaguard_integration.py` | ✅ |
| 8 Matrix tv/anime/films/animefilms × audio/HDR, geen policy bypass | `test_recovery_quality.py` | ✅ |
| 9 Playwright responsive, donker/licht, toetsenbord, contrast, geen overflow, geen mock-knoppen | `apps/web/e2e/screens.spec.ts` | ✅ 68/68 |
| 10 Restore corrupt/ontbrekend, job restart, rechten, redactie, SSRF, CSRF, rollen | `test_foundation.py` + stack 7–14, 18 | ✅ |
| 11 QA-rapport met beperkingen | dit document | ✅ |

## 4. Fault injection (uitgevoerd)

Prowlarr: auth, time-out, categoriefout, 500, malformed XML. Newznab: auth, time-out, malformed, 500,
categoriefout, leeg, rate limit, geen tvsearch. SABnzbd: verlopen account, auth, ontbrekende artikelen.
NNTP: verlopen, auth geweigerd, te veel verbindingen, block op, 502-greeting, onbereikbaar, TLS-fout, time-out.
qBittorrent: auth, sessieverloop (403 → herlogin), oude versie zonder endpoints, firewalled, schijfdruk.
MediaGuard: offline (503/gesloten poort), auth, ontbrekende routes, geen OpenAPI, quotum op, noodstop.
Stack: worker SIGKILL, verlopen job-lease, beschadigde/ontbrekende back-up, volledige herstart.

## 5. Gemeten resources (sandbox, géén TrueNAS-meting)

| Container | Geheugen rust | Geheugen belasting | CPU max belasting |
|---|---|---|---|
| postgres | 29 MiB | 47 MiB | — |
| api | 59 MiB | 61 MiB | 108 % (1 core) |
| worker | 54 MiB | 54 MiB | < 1 % |
| web | 37 MiB | 41 MiB | — |

Belasting: 480 API-verzoeken via de webproxy, 8 parallel: 0 fouten, p95 99 ms. Afgeleide ondergrens
(gedocumenteerd in OPERATIONS.md): 1 GB RAM vrij, 2 CPU-threads.

## 6. Bekende beperkingen

- Alle externe diensten zijn mocks gebaseerd op publieke API-documentatie/referentiebron; echte gedragsdetails
  (SABnzbd-foutteksten bij verlopen accounts, qBittorrent-versiegrenzen voor API-keys en trackerstatussen 5/6)
  zijn niet tegen echte software geverifieerd.
- SABnzbd levert NZB's niet via de API terug: artikeltests werken voor door MSO verstuurde of geüploade NZB's.
- SABnzbd-historie geeft geen bytes per server per job; bijdrage per provider is passief per dag en
  expliciet per test (met dekking/betrouwbaarheid), niet per download.
- Indexer-uitschakelsimulatie is vaak "onbekend" tot er genoeg overlapgegevens zijn verzameld.
- Torrent-tuning maakt elk uur hoogstens één *voorstel* (ook als auto-tune uit staat); toepassen gebeurt niet automatisch.
- Mobiel getest op 390 px; 360/1280 alleen via CSS-breakpoints, niet apart gescreenshot.
- CI-workflow is aanwezig maar niet op GitHub gedraaid; images op ghcr.io bestaan pas na een `v*`-tag.
