# Architectuur — Media Source Optimizer

## 1. Overzicht

```
                 LAN (poort 38081)
                        │
                ┌───────▼────────┐  same-origin proxy: /api/v1 · /newznab · /torznab
                │ web (Next.js)  │  MediaGuard-design system (afgeleide kopie, zie apps/web/PROVENANCE.md)
                └───────┬────────┘
             netwerk app│
        ┌───────────────▼───────────────┐        ┌──────────────────────────┐
        │ api (FastAPI, uvicorn)        │        │ worker (zelfde codebase) │
        │ auth/RBAC/CSRF · REST /api/v1 │        │ persistente jobs, polling│
        │ Newznab/Torznab-server        │        │ health, tuning, back-ups │
        └───────────────┬───────────────┘        └────────────┬─────────────┘
                 netwerk db (intern)                          │
                        └──────────────┬──────────────────────┘
                               ┌───────▼────────┐
                               │ PostgreSQL 16  │ taken, metingen, index, audit, versleutelde geheimen
                               └────────────────┘
  Uitgaand (SSRF-gecontroleerd, alleen via API's): Prowlarr · SABnzbd · qBittorrent · MediaGuard (optioneel)
  · directe Newznab/Torznab/Spotweb-indexers · NNTP-providers (alleen voor tests/opt-in header-index)
```

Geen Docker-socket, geen host-network, geen bestandstoegang tot MediaGuard. Optioneel een read-only
mediamount (`MSO_MEDIA_DIR`) alleen voor hardlink-/vrije-ruimte-diagnose.

## 2. Codeorganisatie

| Pad | Inhoud |
|---|---|
| `apps/core/mso/` | foundation: `config`, `db` (pool + omkeerbare migraties), `auth`, `security` (redactie, SSRF, rate limit), `vault`, `audit`, `jobs`, `changes`, `state`, `settings_registry`, `backups`, `app` (auto-discovery) |
| `apps/core/mso/domain/` | gedeelde vocabulaire: 4 MediaGuard-categorieën, `NormalizedRelease` + titelparser + identiteit/dedupe + kwaliteitspoorten, download-uitkomsten |
| `apps/core/mso/connectors/` | `prowlarr`, `sabnzbd`, `qbittorrent`, `mediaguard` (+ `base` registry) |
| `apps/core/mso/features/indexers`, `search`, `newznab_server` | **Usenet/Indexer- en zoekzijde**: Prowlarr-sync, directe indexers/Spotweb, aggregator, origin graph, eigen Newznab/Torznab |
| `apps/core/mso/features/usenet` | **Usenet-engine**: SABnzbd-historie, providers, NNTP-client, artikelbeschikbaarheid, bijdrage-metingen, opt-in header-index |
| `apps/core/mso/features/torrent` | **Torrent-engine**: diagnose, beleid/guardrails, tuning met evaluatie+rollback, cross-seed, opslag |
| `apps/core/mso/features/mediaguard`, `recovery` | MediaGuard-koppeling (read-only + officiële zoekroute) en herstel (baseline, kandidaten, blacklist) |
| `apps/core/mso/features/costs`, `overview`, `automation`, `ai` | orchestrator-laag: kosten/simulatie, dashboard, automatisering, opt-in AI-adviseur |
| `apps/web/` | Next.js-UI, 15 schermen + login + setup |
| `apps/mocks/msomocks/` | testdubbels (Prowlarr, SABnzbd, qBittorrent, MediaGuard, Newznab/Torznab, Spotweb, NNTP) — nooit in productie |
| `deploy/truenas/` | `compose.yaml` (Install via YAML) + build-override |
| `tests/stack/` | containerstack + mocks + smoke/e2e |

De twee engines zijn onafhankelijk: Usenet (`features/usenet` + SABnzbd) en Torrent (`features/torrent` +
qBittorrent) delen alleen de `domain`-laag en de gedeelde tabellen. De orchestrator-laag (zoeken, herstel,
kosten, automatisering, audit) gebruikt beide via de tabellen en via vaste Python-contracten
(`SabClient.add_url/add_nzb`, `QbitClient.add_torrent`, `search.service.search`, `recovery.blacklist`).
Ontbreekt een engine of koppeling, dan toont de UI een eerlijke `niet beschikbaar`-staat.

## 3. Kernmechanismen

### 3.1 Change sets (preview → goedkeuring → toepassen → rollback)
Elke schrijfactie naar een extern systeem is een `change_sets`-record met per operatie `before`/`after`.
Risicoklassen: `advies` (alleen tonen), `automatisch_veilig` (mag door automatisering in beheermodus als het
gebied is ingeschakeld), `toestemming_nodig` (eerst goedkeuring door een beheerder). Bij toepassen: eerst
afwijkingsdetectie (is de huidige waarde nog wat de preview zag?), dan per operatie schrijven + teruglezen;
een fout herstelt automatisch wat al geschreven was. Rollback schrijft de geobserveerde `before` terug.
Appliers bestaan voor `sabnzbd` (serverprioriteit/enable/optional), `qbittorrent` (voorkeuren, limieten,
recheck/reannounce, cross-seed toevoegen) en `prowlarr` (alleen MSO zelf als indexer registreren).

### 3.2 Persistente, herstartveilige jobs
`jobs`-tabel met `FOR UPDATE SKIP LOCKED`, heartbeats en lease-verloop: na een crash of herstart worden
onderbroken jobs opnieuw ingepland en hervat. Idempotency-keys voorkomen dubbele uitvoering; periodieke
schema's gebruiken een sleutel per tijdslot. Fouten krijgen exponentiële backoff met jitter.

### 3.3 Anti-loop (origin graph)
Iedere zoekopdracht draagt `trace_id`, `hop_count` en de bezochte knopen (`X-MSO-Trace`). Een geconfigureerde
gerichte bron-graaf plus aanroeperdetectie (consumer-sleutel, Prowlarr-User-Agent, lopende uitgaande
Prowlarr-zoekopdracht) zorgt dat een verzoek dat van Prowlarr komt nooit terug naar Prowlarr gaat en dat MSO's
eigen registratie in Prowlarr wordt uitgesloten. Weigering met reden `cycle_detected` / `hop_limit`.
Regressietest: zonder bewaking 7 geneste zoekopdrachten, met bewaking precies 1.

### 3.4 Release-normalisatie
`NormalizedRelease` bevat protocol, bron, guid, NZB-id/message-id, infohash v1/v2, media-id's,
seizoen/aflevering/absolute (anime) nummering, categorie, grootte, codec/resolutie/HDR/DV, audio/ondertitels,
retentie, seeders, trackerstatus, confidence, TTL en false-positive-vlaggen. Dedupe gebeurt op
protocol-identiteit (`btih:`, `btmh:`, `mid:`, `nzb:`, of guid per bron) — nooit op titel.

### 3.5 Vier bibliotheekcategorieën
`tv`, `tv-anime`, `movies`, `movies-anime`, met Newznab-mapping gelijk aan MediaGuard
(`provisioning/templates.py`: anime = 5070, animefilms = films + 5070) en bron-scopes `normal`/`anime` die
overeenkomen met MediaGuard's Prowlarr-tags. Een bron met alleen `anime` bedient nooit `tv`/`movies`.

### 3.6 Modi
Standaard **alleen-lezen**. **Beheermodus** staat handmatig toepassen toe. **Automatisering** past alleen
`automatisch_veilig` toe, per gebied aan te zetten, met uurlimiet, en kan altijd gepauzeerd worden
(topbar-knop). Vergrendelde integriteitsgrenzen gelden altijd.

## 4. Data

Migraties `0001`–`0500` (elk met volledige `down`). Belangrijkste tabellen: `users`, `sessions`, `secrets`,
`connectors`, `audit_log`, `jobs`, `change_sets`, `backups`, `settings`, `system_state`; gedeeld:
`download_outcomes`, `sources`, `services`, `observations`, `recommendations`; per engine eigen tabellen
(`usenet_*`, `torrent_*`, `direct_indexers`, `newznab_consumers`, `release_cache`, `search_sends`,
`origin_edges`, `mg_*`, `recovery_*`, `automation_log`, `ai_*`).

Opslaggrenzen: header-index met harde MB-limiet + TTL-purge; samples/observaties met retentie-instellingen;
back-ups met rotatie (`backup.keep`).

## 5. API

OpenAPI op `/api/openapi.json` (UI: `/api/docs`, alleen via de API-container). Conventies: prefix
`/api/v1/<gebied>`, fouten `{detail, code, correlation_id}`, lijsten `{items: […]}`, rollen viewer < operator < admin.
Newznab/Torznab: `/newznab/api`, `/torznab/api` (`t=caps|search|tvsearch|movie|get`).
