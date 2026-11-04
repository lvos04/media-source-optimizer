# Changelog

Alle noemenswaardige wijzigingen. Versies volgen SemVer; databaseschema-versies staan in `docs/MIGRATION.md`.

## [1.2.0] — 2026-11-04

### Toegevoegd
- **MediaGuard-API-sleutels** als aanbevolen inlogmethode (header `X-Api-Key`, eigen rol, geen sessie of CSRF):
  - nieuw veld bij de koppeling en keuze *API-sleutel* op het MediaGuard-scherm;
  - rolcontrole via `/auth/me`;
  - duidelijke melding bij een ongeldige of ingetrokken sleutel, of bij een MediaGuard zonder sleutels;
  - `mgk_`-sleutels worden overal in logs gemaskeerd.
- `docs/MEDIAGUARD_API_KEYS.md`: contract en kant-en-klare bouwprompt om API-sleutels aan MediaGuard toe te voegen.
- Mock-MediaGuard ondersteunt API-sleutels (operator, viewer, ingetrokken, oude versie). Er zijn 8 nieuwe tests.

## [1.1.1] — 2026-11-04

### Gewijzigd
- Standaardpoort van **30081** naar **38081** (image, Custom App-handleiding, beide compose-bestanden, docs).
  30000–31000 is het bereik dat TrueNAS-catalogus-apps standaard gebruiken; 30081 kon daardoor botsen.
  Bestaande installaties: alleen de poort in de app-instellingen aanpassen; data blijft behouden.

## [1.1.0] — 2026-11-03

### Toegevoegd
- **Alles-in-één image** `ghcr.io/lvos04/media-source-optimizer` (database, API, worker, web in één container),
  installeerbaar als gewone TrueNAS **Custom App** zonder YAML, wachtwoorden of variabelen
  (`docs/TRUENAS_CUSTOM_APP.md`, ook als `deploy/truenas/compose.allinone.yaml`).
- Supervisor: zet bij start als root eenmalig de eigenaar van `/appdata` op 568 en draait daarna alles als 568;
  duidelijke Nederlandse foutmelding bij verkeerde maprechten; ruimt een achtergebleven `postmaster.pid` op na een
  harde stop; herstart gecrashte onderdelen met backoff; stopt netjes (< 1 s gemeten).
- PostgreSQL alleen via een unix-socket in de container: geen databasepoort en geen databasewachtwoord.
- End-to-end test `tests/stack/allinone_test.py` (9 scenario's), draait in de release-workflow vóórdat er iets
  wordt gepubliceerd.

### Opgelost
- Back-up/herstel werkt nu ook met een unix-socket-databaseadres (`?host=/pad`).

## [1.0.0] — 2026-11-02

Eerste volledige versie.

### Platform
- FastAPI-core + worker op één codebase, PostgreSQL 16, Next.js-UI; TrueNAS SCALE *Install via YAML*-stack
  (`deploy/truenas/compose.yaml`) met init/migrate-containers, non-root (568), read-only rootfs,
  `cap_drop: ALL`, limieten, healthchecks, gepinde images.
- Login, rollen admin/operator/viewer, CSRF, brute-force- en API-rate-limits, SSRF-afscherming,
  AES-256-GCM-geheimenkluis, redactie van geheimen in logs, audittrail.
- Change sets met preview, goedkeuring, afwijkingsdetectie, automatische terugrol bij gedeeltelijke fout en
  rollback; standaard alleen-lezen; pauzeknop voor automatisering; vergrendelde integriteitsgrenzen.
- Herstartveilige jobs (lease + heartbeat), idempotente schema's, back-up/herstel met checksum en vangnet.
- Omkeerbare migraties 0001, 0002, 0100, 0200, 0300, 0400, 0500.

### Usenet-engine
- SABnzbd-connector, historie-ingest met faaloorzaak + zekerheid, per-server statistiek.
- Providers (Eweka, Newshosting, blockaccounts), eigen NNTP-client, statuscontrole met foutclassificatie.
- Artikelbeschikbaarheid op daadwerkelijk aangevraagde artikelen, bijdrage/overlap met dekking.
- Prioriteitsadvies via change set; opt-in begrensde header-index met obfuscatiedetectie.

### Torrent-engine
- qBittorrent-connector (API-key/sessie, capability-detectie), knelpuntdiagnose, valse ETA, recheck-advies.
- Guardrails voor private trackers en seedverplichtingen, uploadplafond, wachtrijbeleid.
- Tuning één parameter tegelijk met evaluatievenster, hysterese en rollback; opt-in cross-seed op payload.
- Opslag, reserve, hardlink-bewustzijn (read-only).

### Zoeken & indexers
- Prowlarr-connector + sync naar bronnen met anime/normal-scopes; directe Newznab/Torznab/Spotweb-indexers.
- Zoekaggregator met bronrapport, dedupe op identiteit, betrouwbaarheidsscore.
- Eigen Newznab- en Torznab-endpoints met sleutelbeheer, quota, paginering en foutcodes.
- Origin graph + aanroeperdetectie tegen zoeklussen; conflictdetectie met MediaGuard's Prowlarr-reconcile.

### MediaGuard & herstel
- Optionele MediaGuard-koppeling met capability-detectie en compatibiliteitsmatrix; standalone fallback.
- Herzoek uitsluitend via `POST /api/v1/wanted/{id}/search` met quotum, idempotency en backoff.
- Source Baseline, fallback-kandidaten met kwaliteitspoorten, blacklist met TTL.

### Kosten, overzicht, automatisering, AI
- Kosten per dienst, analyse, *wat als ik dit uitschakel*-simulatie met onzekerheid, scenario's.
- Overzichtsdashboard; automatisering per gebied; evaluatiehaken.
- Opt-in AI-adviseur met maandbudget en deterministische fallback (standaard uit).

### UI
- 15 schermen + login + setup-wizard in MediaGuard-huisstijl (afgeleide kopie, zie `apps/web/PROVENANCE.md`),
  donker/licht/systeem, desktop en mobiel, WCAG 2.2 AA (axe) gecontroleerd.
