# Delta-matrix — Media Source Optimizer (B05) — fase P0

Peildatum **2026-11-09** · repo `lvos04/media-source-optimizer` · branch `ccr-c133d7a0-kuz1cv` · code-HEAD **`018b119`**
(= tag `v1.6.0` op GitHub). Bron van de eisen: `02_BOUWPLANNEN/B05_MSO_DELTA.md`, `EXCEL_TAKEN.json` (B05-001…010),
`03_INSTALLATIE/I04`, `I17`, `I18`, `04_BESTAANDE_APPS/A13`, `A14`, `A19`, `05_TESTS/INTEGRALE_E2E_TESTMATRIX.md` en
`PLATFORM_CONTRACTEN.md` **1.1**. Feitenbasis: `INVENTARIS.md` (zelfde map); bestand:regel-verwijzingen gelden voor
`018b119`, paden in de code zijn relatief aan `apps/core/mso/` tenzij anders vermeld.

**Legenda.** **Bestaand** = in code + geautomatiseerde test aanwezig (met mocks/simulators — **nooit** op de echte NAS
gevalideerd). **Onvolledig** = deels aanwezig; wat mist staat erbij. **Ontbrekend** = niet aanwezig. **Geblokkeerd** =
alleen uit te voeren op de echte NAS / met echte accounts (D01–D18 onbekend). **N.v.t.** = hoort niet bij MSO.
"Niet doen" = grens uit `02_DATA_EIGENAARSCHAP` / `INTEGRATION_MEDIAGUARD.md` §1 / contract §12.

Er is in fase P0 **geen productcode** gewijzigd. Alle "minimale wijzigingen" hieronder zijn voorstellen voor P3.

---

## 0. Samenvatting

- MSO 1.6.0 dekt functioneel het grootste deel van B05-002…B05-008 en B05-010 al af (code + 789 backend-tests, deze
  sessie opnieuw 789/789 geslaagd — zie `INVENTARIS.md` §15). Wat daar overblijft is vooral **echte validatie op de
  NAS** (geblokkeerd) en een handvol kleine hiaten.
- **B05-009 (Hub-integratie) ontbreekt grotendeels**: geen machine-authenticatie, geen `/connector/v1/*`, geen
  `Idempotency-Key`, geen event-envelope, geen `/readyz`. Het goedkeuringsmechanisme zelf (change sets met atomaire
  statusovergang + advisory lock) bestaat wel en moet hergebruikt worden, niet nagebouwd.
- **B05-001 (releasegate)**: het pakket ging uit van "VERSION 1.5.0, 1.6.0 alleen op HEAD". Dat is achterhaald:
  `release/VERSION` = 1.6.0, tag `v1.6.0` → `018b119` en de alles-in-één-image `:1.6.0` staan op GitHub/GHCR, de
  release-workflow slaagde. Wat nog ontbreekt voor een **echte** (productie-)release staat in §8.
- **Installatie als standaard TrueNAS Custom App (alles-in-één): ja, bestaat al** (`deploy/allinone`,
  `docs/TRUENAS_CUSTOM_APP.md`), maar met contractafwijkingen (§7): base-images zonder digest, YAML nog als alternatief
  genoemd, geen `deploy/truenas/CUSTOM_APP.md`, geen manifest, geen `.env.example`.
- Totaal over de B05-taken (§1): 10 taken → 0 op de NAS gevalideerd; 5 functioneel bestaand met alleen live-validatie
  open (B05-002, B05-003, B05-005, B05-007, B05-008); 4 onvolledig met bouwwerk (B05-001, B05-004, B05-006,
  B05-010); 1 grotendeels ontbrekend (B05-009).

---

## 1. B05-taken (Excel) — per eis

### B05-001 — Releasegate v1.6.0 (F0, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging (P3) | Niet doen |
|---|---|---|---|---|
| Vergelijk code-HEAD met gedocumenteerde v1.5.0; image/pakket kan achterlopen | **Bestaand** (P0 uitgevoerd) | `INVENTARIS.md` §1: VERSION 1.6.0, `pyproject.toml:3` 1.6.0, `config.py:9` 1.6.0, `deploy/allinone/Dockerfile:54` 1.6.0, tags v1.0.0–v1.6.0 (`git ls-remote`), GHCR `:1.6.0` = `sha256:9bcdec69…3da`, `:1.5.0` = `sha256:7ee5cb5e…6be2` | — | Niet zelf taggen/publiceren of `release/VERSION` wijzigen (een push daarvan start `release.yaml` op elke branch) |
| Inspecteer release/VERSION, changelog, tags, GHCR-image, CI | **Bestaand** (P0) | `CHANGELOG.md:5` `[1.6.0]`; run `42575858717` success (jobs `test`, `publish`, incl. stap "End-to-end test of the all-in-one image"); `ci.yaml` heeft **nooit** gedraaid (trigger `main`/`v*`/PR, `.github/workflows/ci.yaml:12-15`; repo heeft geen `main`) | CI laten draaien: trigger uitbreiden met `push` op `ccr-*`-branches + `workflow_dispatch` | — |
| Inspecteer migratie 0600 | **Bestaand** | `migrations/0600_v160_base.sql` (up+down); `tests/test_foundation.py::test_migrations_down_and_up` (alles up→down→up); `tests/stack/allinone_test.py` scenario 10 (schema 501→607 met pre-upgrade-back-up, in release-run geslaagd) | Scenario 10 draait `migrate --down 501` **in de 1.6.0-image**; een upgrade vanaf de **echte 1.5.0-image met data** ontbreekt → P3-03 | — |
| Inspecteer daadwerkelijke API | **Bestaand** (P0) | `docs/openapi/media-source-optimizer.openapi.json` (185 paden, 210 operaties, `info.version` 1.6.0) + `INVENTARIS.md` §3 (211 routes bestand:regel, AST-gecontroleerd) | Geen CI-driftcontrole → P3-14 | — |
| Scherm: "Release checker toont 'development kandidaat' tot officiële tag/test bevestigd" | **Ontbrekend** | `GET /api/v1/system/info` (`api/foundation.py:386`) geeft alleen `version`, `schema_version`, migraties; geen kanaal, commit, image-digest of acceptatiestatus | P3-02: build-metadata (`version`, `git_sha`, `build_time`, `channel`) als build-args in de image; `/system/info` + `/connector/v1/discover` tonen ze; Systeem-scherm toont "CI-gebouwd · hardwareacceptatie: NIET GEVALIDEERD" (de app kan echte acceptatie niet zelf weten; de Hub houdt die status bij via EVIDENCE/Excel). Geen migratie | Nooit "stable/geslaagd" tonen op basis van CI alleen |
| Acceptatie: geen 1.6.0 als stable zonder build, migratietest en hardwareacceptatie | **Onvolledig / Geblokkeerd** | build ✔ (release-run); migratietest ✔ in sim (zie boven); hardwareacceptatie ✘ — `docs/HARDWARE_ACCEPTANCE.md`: alle punten "NIET GEVERIFIEERD" | (1) P3-03 echte upgradetest 1.5.0-image → nieuwe image + tag-only-rollbacktest (EX15); (2) P3-01 startweigering bij nieuwer schema; (3) hardwareacceptatie = **Geblokkeerd** (echte NAS, D01/D02/D03/D07) | Geen 1.6.0 als "stable" in manifest/Hub markeren; `validation_status` in het manifest blijft "NIET GEVALIDEERD (echte NAS)" |
| Foutafhandeling/bewijs (scenario, image-versie, screenshots, geredigeerde request/response, tijden, audit/rollback, incidentlink) | **Onvolledig** | release-run, `docs/test-evidence/*`, 64 screenshots (1440/390) — allemaal sim | Incidentlink bestaat pas met de Hub (P3-07 events); echt bewijs op NAS geblokkeerd | — |

### B05-002 — Actuele oorzaak per mislukking (F1, P0)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Echte SABnzbd failure/log/categorie parsen; alleen zinvolle retries | **Bestaand** | `features/usenet/stagelog.py` (verwerkingslog, NL-teksten), `failures.py`, `unpack.py`, `retry.py`, `coverage.py`; tests `test_usenet_stagelog.py` (o.a. `test_complete_data_failure_reasons`, `test_verification_stopped_by_password_or_disk_is_not_a_data_failure`, `test_crc_error_with_complete_data_is_a_damaged_archive_not_missing_articles`, `test_dutch_sabnzbd_texts`), `test_usenet_cause.py::test_retry_only_retryable_causes_failed_before_the_gain`, `::test_brake_pauses_after_futile_retries_and_resumes_by_button` | — | Geen retry voor wachtwoord/uitpak/omgevingsfouten (bestaand gedrag niet versoepelen) |
| `outcome_at`/`failed_at`, genormaliseerde redenen, stale-detectie | **Bestaand** | `domain/outcomes.py:32-84` (`outcome_at()`, `failed_at` bij overgang naar failed), migratie `0604_v160_goals`; stale: `tests/test_usenet_stale_mso_test.py` (5 tests, o.a. `test_mso_test_expires_after_24h`), `jobs.py:94` `recover_stale` | — | — |
| Tests op live SABnzbd met niet-gevoelige data | **Geblokkeerd** | mock: `apps/mocks/msomocks/sabnzbd.py` | Echte SABnzbd-versie/API (D07) op de NAS; testplan in `docs/HARDWARE_ACCEPTANCE.md` §4 | Geen echte NZB's of providergegevens in repo/evidence |
| Scherm: per download oorzaak, bewijsbron, laatste poging, veilige vervolgstap | **Bestaand** | Downloads → Usenet (`apps/web/components/mso/downloads/*`), `GET /api/v1/usenet/sab/history` (`features/usenet/api.py:115`), `::test_history_view_shows_facts_per_failed_download` | — | — |
| Acceptatie: failure is niet meteen "retention" bij password mismatch of unpack error | **Bestaand (Sim-getest)** | `test_usenet_stagelog.py::test_complete_data_failure_reasons` (encrypted/unpack/crc/disk/permission → nooit `missing_articles`), `::test_unpack_problem_kinds`, `::test_server_context_only_for_missing_articles` | Live bevestiging geblokkeerd | — |

### B05-003 — Categorie `*`-diagnose (F1, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Optionele MediaGuard-routes voor arr-clients en categorieën lezen | **Bestaand** | `connectors/mediaguard.py` (veld-whitelist), `features/usenet/categories.py:1-40`; `test_usenet_categories.py::test_mediaguard_reads_keep_only_whitelisted_fields`; statische controle: alle 4 routes bestaan in MediaGuard **v1.2.2** (`INVENTARIS.md` §16) | — | Geen MediaGuard-configuratie wijzigen (provisioning is van MediaGuard) |
| Alleen SABnzbd-categoriefix voorstellen na goedkeuring; idempotent | **Bestaand** | `categories.py:555` (risico `toestemming_nodig`, ook in Autopilot), `changes.py:81` (`rollback_deletes`); tests `::test_proposal_preview_approval_apply_verify_and_guarded_rollback`, `::test_periodic_check_keeps_one_proposal_and_retires_it`, `::test_applied_category_is_never_rolled_back_automatically`, `::test_only_an_admin_may_roll_back_a_category_and_never_via_alles_terugzetten` | — | Nooit automatisch aanmaken; nooit automatisch terugdraaien (zou verwijderen) |
| Strikt toegestane GET-routes + goedgekeurde SAB-write | **Bestaand** | `connectors/mediaguard.py:42` `ALLOWED_WRITES`, `:206` weigering vóór I/O; `test_mediaguard_integration.py::test_client_refuses_forbidden_write_routes_before_io` | — | `ALLOWED_WRITES` nooit uitbreiden zonder ADR + MediaGuard-eigenaar |
| Scherm: categoriekaart met oorzaak + schaduwpreview van mapconsequenties | **Bestaand** | `GET /api/v1/usenet/categories/diagnosis` (`usenet/api.py:259`), mapvoorbeeld `categories.py:431-437, :525-537, :613`; schermen Downloads/Usenet-providers | — | — |
| Acceptatie: geen Sonarr/Radarr-instellingen overschrijven, MediaGuard-cleanup niet onbedoeld beïnvloeden (EX39) | **Bestaand (Sim-getest)** | `::test_case_b_no_category_fix_in_mediaguard_or_arr`, `::test_without_mediaguard_or_routes_degrades_gracefully`, `::test_periodic_check_respects_the_owners_decisions`; `INTEGRATION_MEDIAGUARD.md` §4a | Live bevestiging tegen echte SAB + MediaGuard geblokkeerd | — |

### B05-004 — Overzicht Voetafdruk (F2, P1)

Let op de naam: in MSO 1.6.0 betekent **"Voetafdruk"** (`/automatisering/voetafdruk`) *wat MSO bij de bronnen heeft
veranderd* (bronkant). B05-004 bedoelt de **resource-voetafdruk** van MSO zelf (verbindingen, retentie, opslag,
logvolume, DB). Veiligste interpretatie (ADR-0003): bestaande Voetafdruk ongemoeid laten; de resource-voetafdruk komt
onder Systeem → "Opslag en groei" en via de Hub.

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| DB-tabellen/opslag meten | **Onvolledig** | `retention.py:247-255` `database_usage()` (DB-grootte + top-6 tabellen), `GET /api/v1/system/maintenance` (`retention.py:257`), back-upruimte `backups.space_status`; `test_maintenance.py::test_maintenance_api` | Alleen momentopname, geen verloop → P3-10: dagelijkse steekproef in nieuwe tabel `resource_samples` (migratie `0609`, bewaartermijn 400 d), `growth` in `/system/maintenance` | Bestaande retentie/VACUUM niet dupliceren; audit nooit opruimen |
| Logretentie | **Bestaand** | `retention.py:1-20` (jobs 14/60 d, observations 30 d → uur-rollup 400 d, automation_log 180 d, login_attempts 90 d, audit nooit); `::test_retention_job_cleans_logs_vacuums_and_never_touches_audit`, `::test_observations_rollup_hourly_and_idempotent` | — | — |
| Logvolume | **Ontbrekend (deels N.v.t.)** | MSO logt naar stdout (Docker `json-file`); niet vanuit de app meetbaar | `unknown` tonen met uitleg; echte meting hoort bij de Hub (TrueNAS-API/Beszel) | Geen eigen logcollector bouwen |
| Verbindingen per bron, per-bron-metrics, begrensde steekproef | **Onvolledig** | koppelingsstatus `connectors` (status, `status_since`, `last_ok_at`), `observations`/`observations_hourly`, `indexer_usage`, `usenet_server_stats`; begrensd door retentie | Aantal open/uitgaande verbindingen niet gemeten → in P3-10 alleen DB-verbindingen (`pg_stat_activity`, gemeten) + "onbekend" voor de rest | — |
| Privacylek vermijden | **Bestaand** | redactie `security.py`; `test_foundation.py::test_redaction_in_logs`; geheimen nooit in API/back-up (`::test_vault_encrypts_and_never_exposes`) | — | — |
| Scherm: voetafdruk met echt/gemeten/geschat | **Ontbrekend** | Systeem-scherm toont onderhoudscijfers zonder herkomstlabel | P3-10: per waarde `provenance: gemeten|geschat|onbekend` + "laatst bijgewerkt" | Bestaand Voetafdruk-scherm niet hernoemen/ombouwen |
| Acceptatie: na 30 dagen retentieregels getest, DB groeit gecontroleerd | **Onvolledig / Geblokkeerd** | retentie getest met teruggedateerde rijen (sim) | P3-10 test met 30 dagen gesimuleerde samples; echte 30-daagse soak = Geblokkeerd (NAS) | — |

### B05-005 — Heldere Autopilot-grenzen (F2, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Modus alleen-lezen/beheer/Autopilot verwijdert geen bestanden; respecteert menselijke keuzes | **Bestaand** | `state.py:41-72`; `settings_registry.py:166-206` (vergrendeld: `integrity.never_delete_files`, …); `changes.py:243-256`; `features/automation/overrides.py`; tests `test_autopilot.py::test_read_only_blocks_everything`, `test_foundation.py::test_locked_integrity_settings_cannot_change`, `::test_destructive_ops_need_permission_never_automatic_and_irreversible_not_rolled_back`, `test_change_safety.py::test_owner_override_blocks_automatic_reapply`, `test_footprint.py::test_automatic_reapply_keeps_the_owner_choice` | — | Grenzen nooit versoepelen of instelbaar maken |
| Alle bron-side-effects auditen | **Bestaand** | `audit_log` (nooit opgeruimd), `automation_log`, Voetafdruk (`features/automation/footprint.py`, migratie `0607`) | — | — |
| Stoppen bij brondrift | **Bestaand** | rollback/apply met driftcontrole (`changes.py:391`); `test_change_safety.py::test_rollback_skips_values_changed_outside_mso`, `::test_rollback_mixed_drift_and_already_original`, `test_usenet_categories.py::test_apply_detects_drift_and_failed_writes`, `test_torrent_cleanup.py::test_drift_refuses_whole_bundle` | — | — |
| Vergelijken met 1.6 override-tests | **Bestaand** | `test_change_safety.py` (noodrem `::test_noodrem_after_failures_and_hervatten`, `::test_noodrem_after_three_automatic_rollbacks`, `::test_failed_automatic_apply_cools_down_then_parks`) | — | — |
| Scherm: Wat MSO deed / Wacht op jou / taakstatus met rollback-uitleg | **Bestaand** | `GET /api/v1/overview/autopilot` (`features/overview/autopilot.py:678`), `/overview/waiting` (`:684`), `/api/v1/jobs`, `/automation/undo` (`features/automation/api.py:380-391`) | — | — |
| Acceptatie: kapotte provider alleen automatisch tijdelijk uit als de laatste werkende blijft; geen torrent-delete (EX40) | **Bestaand (Sim-getest)** | `features/usenet/autoheal.py:847-856` (kandidaten = alleen bevestigd falende servers; laatste ingeschakelde blijft altijd aan); `test_usenet_autopilot_servers.py::test_never_switches_off_the_last_enabled_server`; torrent: `test_torrent_cleanup.py::test_non_suspicious_never_deletable`, delete alleen via goedgekeurde malware-opruiming | Live geblokkeerd | Geen automatische torrent-/bestandsverwijdering, ook niet op AI-advies |

### B05-006 — Live MediaGuard-compatibiliteit (F3, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| Mock-only-garantie vervangen door echte read- en search-flowtests tegen v1.2.2 | **Onvolledig** | alleen mock (`apps/mocks/msomocks/mediaguard.py`); **statisch** gecontroleerd dat alle 18 MediaGuard-routes die MSO gebruikt in MediaGuard v1.2.2 bestaan met de verwachte rol (`INVENTARIS.md` §16); image `ghcr.io/lvos04/mediaguard-core:1.2.2` = `sha256:a8daf269…bc2b` is publiek | P3-09: contracttest in CI die de **echte** MediaGuard-core 1.2.2-image (op digest) in een wegwerpstack start (met nep-Sonarr/Radarr) en MSO's client ertegen draait: reads, `X-Api-Key` viewer/operator, 401/403/404-degradatie, weigering van verboden writes. Resultaat = Sim-getest (echte code, nepdata) | Niets in de MediaGuard-repo wijzigen; geen MediaGuard-DB lezen |
| Contracttests incl. optionele 1.6-leesroutes, negatieve unauthorized-tests, quotum, noodstop, backoff | **Bestaand (mock)** | `test_mediaguard_integration.py` (19 tests, o.a. `::test_auth_failure_is_degraded`, `::test_old_version_capability_detection`, `::test_emergency_stop_blocks_search_requests`, `::test_mediaguard_quota_never_exceeded`, `::test_quota_reserve_left_for_mediaguard_backfill`, `::test_failed_requests_back_off_exponentially`, `::test_viewer_role_cannot_request`), `test_mediaguard_apikey.py::test_invalid_or_revoked_key_is_degraded` | Zie vorige rij | — |
| Scherm: compatibiliteitsmatrix met herkomst en ontbrekende route | **Bestaand** | `GET /api/v1/mediaguard/compat` (`features/mediaguard/api.py:48`, met `source` + `checked_at`); `test_usenet_categories.py::test_compat_matrix_lists_the_optional_read_routes` | — | — |
| Acceptatie: 403/404 op optionele read-route degradeert; alleen officiële writes | **Bestaand (Sim-getest)** | `test_usenet_categories.py::test_without_mediaguard_or_routes_degrades_gracefully` (`route_ontbreekt`/`geen_toegang`), `test_mediaguard_integration.py::test_client_refuses_forbidden_write_routes_before_io` | Live tegen echte MediaGuard op NAS = Geblokkeerd | — |

### B05-007 — SAB/qBit/Prowlarr live-acceptatie (F3, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist | Niet doen |
|---|---|---|---|---|
| Echte API-versies op NAS, categorieën, missing-file-excludes, retentiechecks, seed/hardlink | **Geblokkeerd** | sim: `test_sabnzbd_connector.py`, `test_qbittorrent_connector.py`, `test_indexers_prowlarr.py`, `test_torrent_engine.py::test_storage_hardlink_scan_read_only`; checklist `docs/HARDWARE_ACCEPTANCE.md` §3-§5 | Echte NAS + diensten (D01, D02, D03, D07); toestemming gebruiker | Nooit blind productieconfig aanpassen |
| Staging-dataset, test-torrent en NZB uit toegestane bron, snapshots voor/na, opruimen na goedkeuring | **Geblokkeerd** | — | Staging-dataset (D02), toegestane testbronnen | Geen echte downloads vanuit de bouwomgeving |
| Scherm: resultaten met echte dienstversie en testlogs | **Onvolledig** | versie per koppeling in `GET /api/v1/connectors`; geen apart acceptatierapport | Komt uit de echte run; geen bouwwerk nodig vóór de run | — |
| Acceptatie: geen duplicate loop in Prowlarr (EX42); private-torrent-guardrails blijven (EX41) | **Bestaand (Sim-getest)**, live **Geblokkeerd** | `test_search_origin.py::test_no_loop_when_prowlarr_calls_mso_back`, `::test_inbound_from_prowlarr_never_queries_prowlarr`, `::test_start_refuses_cycles_and_hop_limit`; `test_torrent_engine.py::test_private_torrent_dht_and_tracker_injection_refused`, `::test_private_torrent_without_rule_gets_no_share_limits` | — | — |

### B05-008 — Kostenoptimalisatie (F4, P1)

| Eis | Klasse | Bewijs | Wat mist | Niet doen |
|---|---|---|---|---|
| Unieke bijdrage per provider, overlap, kosten per succesvolle GB/download met bias en onzekerheid | **Bestaand** | `features/costs/analysis.py`, `GET /api/v1/costs/services/{id}/analysis` (`costs/api.py:245`), `/simulate` (`:255`); `test_costs_analysis.py::test_usenet_simulation_ranges_and_coverage`, `::test_indexer_simulation_release_overlap`, `test_costs_import.py::test_cost_per_successful_gb_and_download_with_unique_share` | — | — |
| Bonnetjes handmatig, dagelijkse metrics, gevoeligheidsscenario's; nooit automatisch opzeggen | **Bestaand** | handmatige diensten/prijzen (`costs/api.py:182-231`), scenario's (`:284-291`); vergrendeld `integrity.no_auto_cancel_subscriptions` (`settings_registry.py`) | — | Nooit opzeggen/aanpassen bij een provider |
| Scherm: simulatie "wat als ik provider X stop" met betrouwbaarheidsinterval | **Bestaand** | scherm Kosten; `::test_scenarios_no_invented_backbone` | — | — |
| Acceptatie: provider zonder stats toont `unknown`; geen gegarandeerde besparing | **Bestaand (Sim-getest)** | `costs/analysis.py:45-47, :143, :177-191` ("onbekend"), `costs/__init__.py:5`; `::test_simulation_unlinked_service_is_unavailable`, `::test_block_account_normalised_by_observed_usage` | Echte facturen = handmatige invoer door gebruiker | — |

### B05-009 — Integratie met Hub (F4, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging (P3) | Niet doen |
|---|---|---|---|---|
| MSO-incidents, actievoorstellen en meetgegevens in de Hub | **Onvolledig** | leesbaar via bestaande routes (zie §6): `/overview`, `/overview/goals`, `/overview/waiting`, `/changes`, `/audit`, `/system/health` | Machine-auth (P3-06) en connectorlaag (P3-07) | Hub krijgt geen eigen schrijfpad naar SAB/qBit/Prowlarr |
| Admin-goedkeuringen blijven gekoppeld aan de MSO-change-set | **Onvolledig** | change sets met status `preview→approved→applying→applied` (`changes.py:211-231`), voorvertoning onveranderlijk na aanmaken (ops worden alleen tijdens/na toepassen bijgewerkt: `changes.py:146, :324-362`) | P3-08: `preview_hash` (sha256 over canonieke ops+target+risico) per change set; `approve`/`apply` accepteren `expected_preview_hash` en weigeren bij afwijking (409 `preview_changed`) | Geen tweede goedkeuringstabel in MSO naast `change_sets` |
| Gescopeerde API-adapter + event-envelope | **Ontbrekend** | geen `/connector/v1/*`, geen outbox/envelope (`INVENTARIS.md` §6) | P3-07: dunne laag `/connector/v1/{discover,health,items,events,evidence/{id},propose,apply,rollback}` bovenop bestaande modules; events uit `audit_log`/`automation_log` met oplopende cursor → envelope §7 (pollen; geen push in v1) | Geen exact-once-aanname; geen gesimuleerd succes |
| Action ownership + expliciete idempotentie | **Onvolledig** | intern: `jobs.enqueue(idempotency_key=…)` (`jobs.py:75-90`), MediaGuard-zoekverzoek idempotent (`::test_request_search_idempotent_and_audited`) | `Idempotency-Key`-header op `/connector/v1/apply|rollback` + tabel `connector_idempotency` (migratie `0608`) | — |
| Hub keurt goed via de door MSO ondersteunde flow | **Onvolledig** | `POST /api/v1/changes/{id}/approve` (admin, `api/foundation.py:491`), `/apply` (operator, `:496`) — alleen met sessiecookie + CSRF | P3-06: Hub-JWT (EdDSA, `aud=media-source-optimizer`, `exp ≤ 120 s`, rollen → viewer/operator/admin) verifiëren via Hub-JWKS; `Principal.machine` bestaat al (`auth.py:36`) maar wordt nergens gezet | Rollen nooit verhogen; AI-actor nooit admin |
| Scherm: één probleem-inbox en open-in-MSO-deeplinks | **Onvolledig (MSO-kant bestaand)** | deeplinks bestaan: `/automatisering?tab=…&change=<id>&rec=<id>` (`apps/web/app/(app)/automatisering/page.tsx:32-33`), `/ontbrekend?item=…` (`ontbrekend/page.tsx:53`), `/downloads?tab=…`, `/logs?action=…` | Inbox zelf = Hub (`ai-server-manager`); MSO levert `deep_link` per item in `/connector/v1/items` | — |
| Acceptatie: geen dubbele actie als in Hub én MSO tegelijk op goedkeuren wordt gedrukt | **Onvolledig** | mechanisme: atomaire `UPDATE … WHERE status = ANY(…)` (`changes.py:211-216`), per-change advisory lock + hercontrole onder lock (`changes.py:126-143, :295-297`); sequentiële dubbele apply → 409 (`tests/test_autopilot.py:51`, `test_torrent_cleanup.py:153`) | **Geen paralleltest** → P3-08: twee gelijktijdige approve/apply-verzoeken (threads, echte PostgreSQL, mock-SAB) → precies één schrijfactie bij de bron + één auditregel; plus `Idempotency-Key`-herhaling | — |

### B05-010 — Back-up en migratie (F5, P0, risico hoog)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging | Niet doen |
|---|---|---|---|---|
| PostgreSQL-back-up consistent | **Bestaand** | `backups.py:179-229` (`pg_dump -Fc` + SHA-256), verificatie `:231`; `test_foundation.py::test_backup_and_restore_roundtrip`, `::test_restore_refuses_corrupt_backup` | — | Back-up/herstel niet herbouwen in de Hub; Hub roept MSO's eigen route aan |
| 0600-migratie aantoonbaar | **Bestaand (Sim-getest)** | zie B05-001 | P3-03 echte image-upgrade | — |
| Terugkeer met bijpassende snapshot + image | **Onvolledig** | oudere dump: down → restore → up (`backups.py:246-335`, `test_maintenance.py::test_restore_of_an_older_dump_migrates_down_first_and_up_after`), nieuwere dump geweigerd (`::test_restore_of_a_newer_dump_is_refused`); schema terug naar 1.5.0 alleen via terminal (`docs/TRUENAS_CUSTOM_APP.md:88-98`) | P3-01 startweigering bij nieuwer schema (EX15); P3-13 docs: terugdraaien via TrueNAS-snapshot (web-UI) + oude tag, terminal alleen als expertoptie | Geen "tag-only rollback" aanbieden (manifest `tag_only_rollback_safe: false`) |
| Migreren op gekopieerde DB + retentiecontrole; herstel in wegwerpstack | **Onvolledig** | `test_maintenance.py::test_pre_upgrade_backup_before_pending_migrations`, `::test_failing_upgrade_in_a_restart_loop_keeps_the_first_pre_upgrade_dump`; alles-in-één scenario 3 (back-up+herstel) en 10 | P3-03 (echte 1.5.0-image + data → nieuwe image, daarna herstel in nieuwe wegwerpcontainer) | — |
| Scherm: migratierapport en rollback-gate | **Onvolledig** | Systeem → Back-up & updates (pre-upgrade-back-ups, `GET /api/v1/system/info` migraties) | Rapport "schema X → Y, back-up Z, terugweg" in `/system/info` (P3-02, zelfde wijziging) | — |
| Acceptatie: na terugzet audit, index en credentials aanwezig; jobs niet dubbel hervat | **Onvolledig** | audit ✔ (`test_foundation.py::test_restore_keeps_newer_audit_and_cancels_old_jobs`), jobs ✔ (zelfde test + `jobs.recover_stale`), credentials ✔ (`backups.py:272-322` houdt huidige versleutelde `secrets` vast), modus/noodrem ✔ (`test_maintenance.py::test_restore_keeps_mode_pause_and_noodrem_and_settles_applying`); **header-index**: `usenet_hi_headers` zit bewust niet in de back-up (`backups.py:63` REFETCHABLE) en wordt niet expliciet na herstel herbouwd (alleen `mediaguard.sync` en `torrent.ingest` in `RESYNC_JOBS`, `backups.py:77-81`) | P3-12: test (en zo nodig `usenet.headerindex_run` in `RESYNC_JOBS`) dat de index na herstel opnieuw opgebouwd wordt; documenteren als "cache, wordt herbouwd" | Index niet in de back-up opnemen (bewuste keuze: groot en herhaalbaar) |

---

## 2. Algemene B05-eisen (§1-§3, §5-§7 van het bouwplan)

| Eis | Klasse | Bewijs | Wat mist → minimale wijziging |
|---|---|---|---|
| §1 bestaande schermen (Overzicht, Zoeken, Usenet, Torrents, Indexers, Herstel, Kosten, Automatisering, Logs, Back-ups) + Voetafdruk + categorieanalyse | **Bestaand** | 16 schermen `apps/web/app/(app)/*`; `apps/web/e2e/screens.spec.ts` | — |
| §1 responsief 360/390/768/1440 | **Onvolledig** | Playwright-projecten alleen `desktop-1440` en `mobile-390` (`apps/web/playwright.config.ts:25-26`) | P3-11: projecten 360 en 768 toevoegen; alleen bij gevonden overflow UI aanpassen |
| §1 leeg/laden/storing/offline | **Onvolledig** | componenten `StateView`/`ErrorState`/`ResourceView` (`apps/web/components/mg/states.tsx:25-93`, klassen `state--error/offline/unavailable`); E2E controleert alleen de gezonde staat (`screens.spec.ts:90`) | P3-11: E2E met uitgevallen mock-koppeling (offline/storing) en lege database |
| §1 navigatie, rolcontrole, NL-fouten, toetsenbord, auditspoor | **Bestaand** | `apps/web/components/shell/nav.ts`; `require()` op 204 van 211 routes (`INVENTARIS.md` §3); NL `detail`; axe + toetsenbordtests (`screens.spec.ts`); Logs & audit | — |
| §1 zoek/filter/paginering | **Onvolledig** | audit: filters + `before_id` (`api/foundation.py:402-425`); overige lijsten alleen `limit` | Cursor alleen waar de Hub het nodig heeft (`/connector/v1/items`, `/events`) |
| §1 datum laatste update + bronherkomst per kaart | **Onvolledig** | overzicht met `stale_s` per bron (`features/overview/api.py:50-115`), compat met `checked_at` | Hub-kaarten krijgen `observed_at` + `source` uit `/connector/v1/items` (P3-07) |
| §2 datamodel: mso_provider, torrent_tracker, usenet_failure, category_drift, suggested_change, canary_outcome, source_metric, footprint_sample, action_audit | **Onvolledig** | `usenet_providers`, `torrent_tracker_rules`, `download_outcomes` (+ redenen), categorie-drift live berekend + `change_sets`, `change_sets`/`recommendations`, evaluaties (`automation/evaluations`, `torrent_tuning_runs`), `observations(_hourly)`/`usenet_server_stats`/`indexer_usage`, `change_footprint` (bron-voetafdruk), `audit_log`/`automation_log` | `footprint_sample` (resource) ontbreekt → P3-10 `resource_samples`. Geen tabellen hernoemen |
| §2 stabiele ID, versie, eigenaar, timestamps, provenance | **Onvolledig** | ID's zijn `bigserial`, timestamps aanwezig; geen versie/ETag per object | Alleen in de connectorlaag: `resource_etag` = `preview_hash`/`updated_at`-hash (P3-07/08); geen UUID-migratie van bestaande tabellen |
| §2 forward-schema-tests, retentie/aggregatie, geheimen als vault-referentie | **Bestaand** (forward-test sim) | `test_migrations_down_and_up`, migratiechecksum (`db.py:126-152`); rollup (`retention.py`); geheimen versleuteld in `secrets` + `vault.key` (`vault.py`) | P3-03 voor de echte image-upgrade |
| §3 MediaGuard read-only + beperkte writes; Hub consumeert MSO-API met versie/capability-probe | **Onvolledig** | MediaGuard-kant bestaand (B05-006); Hub-probe: `GET /api/v1/setup/status` (zonder auth: versie), `/api/openapi.json` (zonder auth) | `/connector/v1/discover` (P3-07) |
| §3 per endpoint: auth, rol, inputvalidatie, rate limit, time-outs, foutcodes | **Bestaand** | `auth.py`, pydantic, `app.py:93-117` rate limit, uitgaand 5 s connect / 15 s read (`connectors/base.py:243`, `sabnzbd.py:58`), fout `{detail, code, correlation_id}` (`app.py:87-90`) | Contractformaat `{code,message,details,trace_id}` alleen in `/connector/v1/*` (ADR-0002) |
| §3 idempotentie voor mutaties | **Ontbrekend** (extern) | zie B05-009 | `Idempotency-Key` op connector-mutaties (P3-07) |
| §5 releasestatus bevestigen vóór tagwijziging; ownermatrix `INTEGRATION_MEDIAGUARD.md` blijft gelden | **Bestaand** | `INTEGRATION_MEDIAGUARD.md` §1; deze P0 | — |
| §6 oplevering (1) code+tests, (8) releasenotes, (9) handleiding, (10) screenshots | **Bestaand** | `apps/*`, `CHANGELOG.md`, `docs/USER_GUIDE.md`, `docs/screenshots/` (64 png, 1440/390) | Screenshots 360/768 met P3-11 |
| §6 (2) migratiescripts + versiecompatibiliteit | **Onvolledig** | migraties met down; geen weigering nieuwer schema | P3-01 |
| §6 (3) TrueNAS-appmanifest met gepinde images | **Ontbrekend** | geen `deploy/manifest/`; compose zonder digests voor eigen images | P3-05 (manifest §9, `install.method: custom_app`) |
| §6 (4) `.env.example` | **Ontbrekend** | — | P3-05 |
| §6 (5) OpenAPI/connector-schema's | **Onvolledig** | OpenAPI geëxporteerd (P0) | connector-schema met P3-07; driftcheck P3-14 |
| §6 (6) test- en auditrapport, (7) herstelbewijs | **Onvolledig** | `TEST_RESULTS.md`, `docs/test-evidence/*` (sim) | echt herstelbewijs = Geblokkeerd (NAS) |
| §7 veilig degraderen bij stroomuitval, uitval afhankelijke app, NAS-herstart, gewijzigde credentials, incompatibele versie | **Onvolledig** | SIGKILL/herstart (alles-in-één scenario 4-5), MediaGuard offline (`test_mediaguard_integration.py::test_offline_falls_back_to_standalone`), credentials (`test_mediaguard_apikey.py::test_invalid_or_revoked_key_is_degraded`), oude versie (`::test_old_version_capability_detection`) | NAS-herstart = Geblokkeerd; incompatibel *eigen* schema → P3-01 |
| §7 geen directe writes in DB van andere app | **Bestaand** | `connectors/mediaguard.py:1-20` (alleen API), `INTEGRATION_MEDIAGUARD.md` §1 | — |
| Fase-exit: code review, back-up/rollback-gate, mobiele gebruikersreis, negatieve permissietest | **Onvolledig** | rolweigeringen getest (o.a. `test_mediaguard_integration.py::test_viewer_role_cannot_request`), mobiel 390 E2E | Per P3-fase opnieuw; echte mobiele test = Geblokkeerd |

---

## 3. Installatie- en bestaande-app-stappen (MSO-kant)

De Excel-ID's I04/I17/I18 en EX* worden in `jelly-leon-platform/docs/platform-v2/EVIDENCE.json` bijgehouden (eigenaar
volgens de Excel-verdeling); hieronder staat alleen wat MSO ervoor levert.

| Stap | Klasse (MSO-kant) | Bewijs / wat MSO levert | Wat mist |
|---|---|---|---|
| I04-04-01 Installed tag vastleggen | **Geblokkeerd** (NAS) / MSO-kant **onvolledig** | repo/GHCR/CI vastgelegd in `INVENTARIS.md` §1; draaiende versie (D07) onbekend | P3-02 (`/system/info` toont build-metadata, zodat de Hub de draaiende digest kan teruglezen) |
| I04-04-02 Image en dataset (pakket: "1.5.0 als werkelijke release") | **Geblokkeerd** | alles-in-één Custom App bestaat; 1.5.0 én 1.6.0 zijn getagd en publiek | Keuze 1.5.0 vs 1.6.0 = gebruikersbesluit (D07), zie ADR-0001 |
| I04-04-03 Koppeling MediaGuard (scoped `X-Api-Key`, noodstop, zelfstandig bij offline) | **Bestaand (sim)** / live **Geblokkeerd** | `test_mediaguard_apikey.py`, `test_mediaguard_integration.py::test_offline_falls_back_to_standalone`, `::test_emergency_stop_blocks_search_requests` | — |
| I04-04-04 Prowlarr en clients live (geen loops, private rules) | **Bestaand (sim)** / live **Geblokkeerd** | zie B05-007 | — |
| I04-04-05 Autopilot-policies (read-only baseline, laatste server houden) | **Bestaand (sim)** / live **Geblokkeerd** | zie B05-005 | — |
| I04-04-06 1.6.0-veranderingen accepteren tegen echte diensten | **Geblokkeerd** | — | echte diensten |
| I04-04-07 Back-up/migratie + hersteltest op aparte stack | **Onvolledig** | zie B05-010 | P3-03 |
| I04-04-08 Hub koppelen (scoped connector, geen directe SAB/qBit-writes, geen race) | **Ontbrekend** | zie B05-009 | P3-06/07/08 |
| I17-17-01 Categorie-inventaris (MediaGuard-categorieën + `mso-*`) | **Bestaand (sim)** | `GET /api/v1/usenet/sab/categories` (`usenet/api.py:202`), categorie-diagnose | live geblokkeerd |
| I17-17-02 Auth/retentie/uitpak/missende artikelen onderscheiden | **Bestaand (sim)** | B05-002 | live geblokkeerd |
| I17-17-03 Serverprioriteit via preview/readback/rollback; laatste server beschermd | **Bestaand (sim)** | change sets + `autoheal.py:847-856` | live geblokkeerd |
| I17-17-04 Padmappings/hardlinks | **N.v.t. voor MSO-bouw** | MSO leest alleen (optioneel `/media:ro`, `test_torrent_engine.py::test_storage_hardlink_scan_read_only`) | TrueNAS/MediaGuard-configuratie |
| I17-17-05 Back-up SAB-config | **N.v.t.** (eigenaar SABnzbd/TrueNAS) | MSO bewaart SAB-geheimen alleen versleuteld | — |
| I17-17-06 Health/update, geen blinde retries | **Bestaand (sim)** | retry-rem (`test_usenet_cause.py::test_brake_*`) | live geblokkeerd |
| I18-18-01 qBit-inventaris (versie, paden, categorieën, share limits) | **Bestaand (sim)** | `GET /api/v1/torrent/clients`, `/torrent/overview`, `/torrent/obligations` | live geblokkeerd |
| I18-18-02 Read-only eerst, veilige tuning, hardlink-bewust, private guard | **Bestaand (sim)** | `features/torrent/policy.py`, `tuning.py`; `test_torrent_engine.py::test_writes_refused_in_read_only_mode` | live geblokkeerd |
| I18-18-03 Exclude-executables tegen echte qBit-versie | **Bestaand (sim)** | `GET/POST /api/v1/torrent/prevention*` (`torrent/api.py:360-366`) | live geblokkeerd |
| I18-18-04 Netwerk/VPN-binding | **N.v.t.** (qBittorrent/netwerk) | — | — |
| I18-18-05 Seeding en AV1-overlap | **Bestaand** (grens) | `integrity.never_rewrite_seeding` (vergrendeld) | live geblokkeerd |
| I18-18-06 Back-up/update qBit | **N.v.t.** | — | — |
| A19 A0 meten (draaiende image/digest, poorten, datasets, afhankelijkheden, gebruik) | **Geblokkeerd** | D01/D02/D03/D07 onbekend | — |
| A19 A1 niet-mutatief in Hub (catalogusitem, status, deeplink) | **Onvolledig** | `/healthz`, `/api/v1/setup/status`, deeplinks bestaan | manifest (P3-05), discover/health (P3-07) |
| A19 A2 alleen ontbrekende functies via data-eigenaar | **In uitvoering** | deze matrix | P3-lijst §9 |
| A19 A3/A4 proefstop/decommission | **N.v.t.** (MSO blijft) | — | — |
| A13/A14 SABnzbd/qBittorrent behouden; geen wildcard-misrouting; geen automatische verwijdering | **Bestaand (sim)** MSO-kant | EX39/B05-003; `test_torrent_cleanup.py::test_non_suspicious_never_deletable` | A0 (meten) geblokkeerd |

---

## 4. Integrale E2E-scenario's met een MSO-kant (eigenaar Excel: `jelly-leon-platform`)

| EX | MSO-kant | Bewijs (sim) | Wat mist |
|---|---|---|---|
| EX01 MediaGuard offline | **Bestaand** | `test_mediaguard_integration.py::test_offline_falls_back_to_standalone`; jobs idempotent | Hub-incident `unknown` = Hub |
| EX02 MSO offline | **N.v.t. in MSO** (MediaGuard werkt zelfstandig) | — | integrale stack |
| EX06 MediaGuard-noodstop | **Bestaand** | `::test_emergency_stop_blocks_search_requests` | — |
| EX15 DB-schema terug, oude image | **Ontbrekend** | oudere image start stil op nieuwer schema (`db.py:126-152` slaat onbekende versies over) | P3-01 + P3-03 |
| EX16 credential verlopen | **Bestaand** (richting MediaGuard/SAB) / **Ontbrekend** (Hub → MSO) | `test_mediaguard_apikey.py::test_invalid_or_revoked_key_is_degraded` | P3-06: verlopen/ingetrokken Hub-JWT → 401 `unauthorized` |
| EX19 approval drift | **Onvolledig** | voorvertoning onveranderlijk; drift bij bron → 409 | P3-08 `preview_hash` |
| EX20 approval replay | **Onvolledig** | tweede apply → 409 (sequentieel) | P3-08 paralleltest + `Idempotency-Key` |
| EX39 categorie `*` | **Bestaand** | B05-003 | live |
| EX40 provider-uitval | **Bestaand** | `::test_never_switches_off_the_last_enabled_server` | live |
| EX41 private torrent | **Bestaand** | `::test_private_torrent_dht_and_tracker_injection_refused` | live |
| EX42 Prowlarr-cyclus | **Bestaand** | `test_search_origin.py::test_no_loop_when_prowlarr_calls_mso_back` | live |

---

## 5. Conformiteit met `PLATFORM_CONTRACTEN.md` 1.1

| § | Eis | Klasse | Bewijs | Minimale wijziging / besluit |
|---|---|---|---|---|
| §1 | Installatie als standaard Custom App, nooit YAML/compose | **Onvolledig** | alles-in-één via formulier: `docs/TRUENAS_CUSTOM_APP.md:40-60`; maar `deploy/truenas/compose.allinone.yaml:1` ("voor … Install via YAML"), `docs/TRUENAS_CUSTOM_APP.md:111-115` ("Alternatieven: Install via YAML"), `docs/TRUENAS_INSTALL.md`, `docs/HARDWARE_ACCEPTANCE.md:20,29` | P3-05: compose-bestanden expliciet "alleen ontwikkeling/CI/E2E"; YAML-installatie uit de gebruikersdocs; acceptatiechecklist naar Custom App |
| §1 | Eén alles-in-één image, PostgreSQL alleen unix-socket, `/appdata`, één poort, root alleen voor eigenaarschap, daarna 568 | **Bestaand** | `deploy/allinone/Dockerfile:1-85`, `supervisor.py:30-99`; `docs/test-evidence/allinone-report.md` scenario 1, 7, 8 (sim) | — |
| §1 | Geen `latest`; base-images op `@sha256` | **Onvolledig** | geen `latest`; base-images zonder digest: `apps/core/Dockerfile:12-13`, `apps/web/Dockerfile:5`, `apps/mocks/Dockerfile:7`, `deploy/allinone/Dockerfile:11-13`; `node:22-bookworm-slim` is bovendien een zwevende major-tag | P3-04 (actuele digests in `INVENTARIS.md` §11) |
| §1 | Geen shell/root voor AI, geen Docker-socket | **Bestaand** | AI-adviseur standaard uit, alleen advies (`features/ai/__init__.py:12-30`); geen socket (`deploy/truenas/compose.yaml:37-46`) | — |
| §1 | Geen voorbeeldwaarden als echte waarden | **Onvolledig** | `/mnt/tank/…` als standaard in `deploy/truenas/compose.allinone.yaml:13` en `compose.yaml:63-68`; docs gebruiken "bijv." | P3-05: placeholders `<POOL>`/datasetkiezer; geen pool-default |
| §2 | Eigen app = één Custom App, poort via dienstenregister | **Bestaand** (poort 38081, buiten de contracttabel) | `deploy/allinone/Dockerfile:79-81` | Geen poortwijziging (bestaande installaties); Hub registreert de echte hostpoort (D03) |
| §3 | Geheimen bij eerste start in `/appdata/secrets` | **Bestaand** | `vault.py` (sleutel `APPDATA/secrets/vault.key`, 0600), `supervisor.py:80-84` | — |
| §3 | Enrollment bij de Hub (`HUB_URL` + eenmalig token, of koppelcode op statuspagina) | **Ontbrekend** | — | P3-06 (afhankelijk van `ai-server-manager` B01/B02: `/api/v1/services/enroll`, JWKS) |
| §4 | Hub-JWT voor gebruikerscontext; service-tokens; 401 `unauthorized` | **Ontbrekend** | alleen lokale sessies (`auth.py:92-126`); 401-code bestaat (`app.py:136-140`) | P3-06. Lokale accounts blijven (scrypt, geen gedwongen migratie naar argon2id — ADR-0003) |
| §5 | Foutformaat `{code,message,details,trace_id}` | **Afwijkend** | `{detail,code,correlation_id}` (`app.py:87-90`) | Alleen in `/connector/v1/*` contractformaat; bestaande UI-routes ongewijzigd (ADR-0002) |
| §5 | `/healthz` + `/readyz` zonder auth | **Onvolledig** | `/healthz` (`app.py:156-161`) | `/readyz` (DB + worker-hartslag + vrije ruimte; geen geheimen) in P3-07 |
| §5 | OpenAPI-export + CI-drift | **Onvolledig** | export P0 (`docs/openapi/media-source-optimizer.openapi.json`) | P3-14 driftcheck in `ci.yaml` |
| §5 | Idempotency-Key, ETag, cursor | **Ontbrekend** | — | alleen connectorlaag (P3-07/08) |
| §6 | Connectorcontract | **Ontbrekend** | — | §6 hieronder, P3-07 |
| §7 | Event-envelope, outbox | **Ontbrekend** | auditlog als bron aanwezig | polling via `/connector/v1/events` (P3-07); push/outbox later |
| §8 | Executor/ondertekende ExecutionRequest | **N.v.t. in MSO** voor app-acties (executor = `ai-server-manager`); MSO-acties lopen via MSO's eigen change sets | — | Hub-handtekening bij `/connector/v1/apply`: zie ADR-0002 (JWT van de Hub + `preview_hash` + `Idempotency-Key` volstaat voor v1; Ed25519-verificatie van een volledige ExecutionRequest = vervolgstap zodra de Hub hem levert) |
| §9 | Manifest | **Ontbrekend** | — | P3-05 |
| §10 | Repo-indeling (`deploy/truenas/CUSTOM_APP.md`, `deploy/manifest/`, `.env.example`, `docs/adr/`, `release/digests.json`, SBOM, pip-audit, mypy) | **Onvolledig** | ADR's aangemaakt in P0 (`docs/adr/`); rest ontbreekt | P3-04/05/14; mypy = vervolgpunt (grote bestaande codebase, geen regressierisico nemen) |
| §11 | EVIDENCE.json | **Bestaand (P0)** | `docs/platform-v2/EVIDENCE.json` | per fase bijwerken |
| §12 | Bestaande integriteit/rollback/monitoring niet dubbel bouwen | **Nageleefd** | `INVENTARIS.md` §13 | — |

---

## 6. Hub-adapter: welke bestaande routes de Hub kan gebruiken (connectorcontract §6, enrollment §3)

Voorwaarde voor **alle** onderstaande Hub-aanroepen behalve `/healthz`, `/api/v1/setup/status` en `/api/openapi.json`:
machine-authenticatie (P3-06). Zonder die laag kan de Hub alleen met een gewone MSO-gebruikerssessie + CSRF werken, wat
het contract niet toestaat (geheimen van een gebruiker in de Hub).

| Contract-endpoint | Bestaande MSO-route(s) die de adapter kan gebruiken | Echt ontbrekend |
|---|---|---|
| `GET /connector/v1/discover` | `GET /api/v1/setup/status` (geen auth: versie, koppelingen), `GET /api/v1/system/info` (viewer: versie, schema, modus), `GET /api/openapi.json` (geen auth: capabilities) | capabilities/permissions/limits-object, build-metadata (P3-02/07) |
| `GET /connector/v1/health` | `GET /healthz` (geen auth), `GET /api/v1/system/health` (viewer; componenten `healthy/degraded/offline` + koppelingsstatus `healthy/degraded/offline/auth_failed/unknown`, `connectors/base.py:38`) | mapping `offline→down`, `auth_failed→degraded` (+ reason_code), geen data → `unknown`; `/readyz` |
| `GET /connector/v1/items` | `/api/v1/overview`, `/overview/goals`, `/overview/waiting`, `/overview/autopilot`, `/usenet/failure-reasons`, `/usenet/categories/diagnosis`, `/mediaguard/compat`, `/costs/summary`, `/automation/footprint`, `/system/maintenance`, `/changes?status=preview` | uniforme `type`/`cursor`/`limit` + `observed_at`/`source`/`deep_link` per item |
| `POST /connector/v1/propose` | voorstellen ontstaan in MSO's eigen engines (`/usenet/categories/propose`, `/torrent/tuning/propose`, `/usenet/retries/propose`, …); de Hub leest ze via `/changes` | generiek "propose" vanuit de Hub → `501 capability_unavailable` (MSO is eigenaar van de beslislogica) |
| `POST /connector/v1/apply` | `POST /api/v1/changes/{id}/approve` (admin) + `/apply` (operator), incl. modus-, noodrem- en driftcontrole | `Idempotency-Key`, `expected_preview_hash`, contract-fout, readback-samenvatting (P3-07/08) |
| `POST /connector/v1/rollback` | `POST /api/v1/changes/{id}/rollback`, `/automation/undo`, `/automation/footprint/restore` (met bestaande admin-grenzen) | idem; categorie-rollback blijft admin-only en nooit automatisch |
| `GET /connector/v1/evidence/{id}` | `GET /api/v1/changes/{id}`, `/automation/evaluations/{id}`, `/audit?target=…` | geredigeerde bundel per id |
| `GET /connector/v1/events` | `GET /api/v1/audit` (filters, alleen `before_id` = nieuw→oud) | oplopende cursor (`after_id`) + envelope §7 |
| Enrollment §3 | — | koppelcode-invoer op MSO's Systeem-pagina, `POST {HUB_URL}/api/v1/services/enroll`, opslag service-token + JWKS in vault (P3-06) |
| Back-up via Hub | `GET/POST /api/v1/backups`, `POST /api/v1/backups/{id}/restore` (admin, getypte bevestiging) | geen nieuwe route; Hub-gate leest `/system/maintenance` (back-upruimte) |

---

## 7. Custom App-controle (alles-in-één)

| Vraag | Antwoord | Bewijs |
|---|---|---|
| Is MSO als standaard TrueNAS Custom App te installeren (image+tag, poort, opslag)? | **Ja, in opzet** — één image, één poort `38081`, één map `/appdata`, geen verplichte env, geen geheimen in het formulier | `docs/TRUENAS_CUSTOM_APP.md:40-60`; `deploy/allinone/Dockerfile` |
| Is de image openbaar op te halen? | **Ja** voor `ghcr.io/lvos04/media-source-optimizer:1.6.0` en `:1.5.0` (anoniem `imagetools inspect` slaagt); de multi-container-images `-core`/`-web` zijn **privé** (401) | `INVENTARIS.md` §1 |
| Getest? | **Sim-getest**: 10/10 scenario's in de release-run (root/568, herstart, kill, back-up/herstel, rechten, upgrade). Het TrueNAS-formulier zelf is **NIET GEVALIDEERD (echte NAS)** | `docs/test-evidence/allinone-report.md`; run `42575858717` |
| Afwijkingen t.o.v. contract 1.1 | base-images zonder digest; YAML als alternatief in docs; geen `deploy/truenas/CUSTOM_APP.md`, manifest, `.env.example`; `/mnt/tank`-default in compose; geen `no-new-privileges` af te dwingen vanuit het formulier (NIET GEVALIDEERD) | §5 |

---

## 8. Releasegate v1.6.0 — wat ontbreekt voor een echte release (zonder zelf te taggen of te publiceren)

Aanwezig: `release/VERSION` 1.6.0 · `CHANGELOG [1.6.0]` · tag `v1.6.0` → `018b119` · all-in-one `:1.6.0`
(`sha256:9bcdec69…3da`, publiek) · core/web `:1.6.0` (privé) · release-run 42575858717 geslaagd (ruff, pytest,
alles-in-één 10/10) · deze sessie: ruff schoon, pytest 789/789.

Ontbreekt (in volgorde van belang):
1. **Hardwareacceptatie op de echte NAS** — Geblokkeerd (D01, D02, D03, D07; `docs/HARDWARE_ACCEPTANCE.md` alle punten
   open). Tot dan is 1.6.0 een *CI-geteste release-kandidaat*, geen "stable" (ADR-0001).
2. **Echte upgrade-migratietest**: gepubliceerde 1.5.0-image met data → 1.6.0-image op dezelfde `/appdata`, plus de
   omgekeerde tag-only-poging (EX15) → P3-03. Nu alleen gesimuleerd binnen de 1.6.0-image.
3. **Startweigering bij nieuwer schema** (anders start een oudere image stil op een nieuwer schema) → P3-01; geldt pas
   vanaf de eerste versie die het bevat.
4. **CI-workflow `ci.yaml` heeft nooit gedraaid** (web-build, containerstack, Playwright niet op GitHub bewezen) →
   trigger repareren (P3-04).
5. **Digest-pinning en herleidbaarheid**: base-images op digest, `release/digests.json`, SBOM, GitHub Release-object met
   notities (nu alleen tags) → P3-04.
6. **Manifest + Custom App-documentatie volgens contract** (`validation_status: NIET GEVALIDEERD (echte NAS)`) → P3-05.
7. **Releasestatus zichtbaar in de app** ("CI-gebouwd · hardwareacceptatie open") → P3-02.

---

## 9. Geprioriteerde bouwlijst fase P3 (alleen hiaten)

| # | Prio | Excel | Wat (minimaal) | Module / route / migratie | Test (sim) | Afhankelijk van |
|---|---|---|---|---|---|---|
| P3-01 | P0 | B05-001, B05-010, EX15 | Weiger starten/migreren als `max(schema_migrations) > hoogste bekende migratie`, met NL-melding "database is van een nieuwere MSO-versie; zet de bijpassende snapshot/back-up terug" | `db.py` `migrate_up` (+ `__main__` CLI), geen migratie | unit + alles-in-één-scenario | — |
| P3-02 | P0 | B05-001, B05-010, I04-04-01 | Build-metadata (`version`, `git_sha`, `build_time`, `channel`) via build-args; `/api/v1/system/info` + Systeem-scherm "Releasestatus" + migratierapport | `config.py`, `api/foundation.py:386`, `deploy/allinone/Dockerfile`, `release.yaml`, web Systeem | pytest + Playwright | — |
| P3-03 | P0 | B05-001, B05-010, I04-04-07, EX15 | Echte upgradetest 1.5.0-image (`@sha256:7ee5cb5e…`) met data → nieuwe image; herstel in wegwerpcontainer; tag-only-terugweg wordt geweigerd | `tests/stack/allinone_test.py` scenario 11-12, `release.yaml` | container-E2E | P3-01 |
| P3-04 | P0 | B05-001, contract §1/§10 | Base-images op exacte versie + `@sha256`; `ci.yaml`-trigger (`ccr-*`, `workflow_dispatch`); `release/digests.json` + SBOM + pip-audit in `release.yaml` | Dockerfiles, workflows | CI-run | — |
| P3-05 | P0 | contract §1/§9, A19-A1 | `deploy/truenas/CUSTOM_APP.md` (formuliervelden, `<POOL>`-placeholders), `deploy/manifest/media-source-optimizer.manifest.yaml` (`install.method: custom_app`, `tag_only_rollback_safe: false`, `decisions_required: [D01, D02, D03, D07]`), `.env.example`; compose = alleen ontwikkeling; YAML uit gebruikersdocs | alleen deploy/docs | manifest-schemacheck | B08-001 (manifestschema, `appstore-en-fork`) |
| P3-06 | P0 | B05-009, EX16 | Machine-auth: Hub-JWT (EdDSA, `aud`, `exp ≤ 120 s`, rolmapping) + enrollment/koppelcode; service-token en JWKS in vault | `auth.py` (`Principal.machine`), nieuw `mso/hub/` (enroll), Systeem-scherm | unit + integratie met nep-Hub | `ai-server-manager` B01/B02 (enroll + JWKS) |
| P3-07 | P0 | B05-009, I04-04-08 | `/connector/v1/*` (discover, health, items, events, evidence, propose→501, apply/rollback via change sets) + `/readyz`; contractfoutformaat alleen hier | nieuw `mso/connector_api/`; migratie `0608_connector_idempotency` | contracttests + E2E met nep-Hub | P3-06 |
| P3-08 | P0 | B05-009, EX19, EX20 | `preview_hash` per change set + `expected_preview_hash`; paralleltest gelijktijdig goedkeuren Hub + MSO → één uitvoering | `changes.py` (+ kolom in `0608`) | paralleltest echte PostgreSQL | P3-07 |
| P3-09 | P1 | B05-006, I04-04-03 | Contracttest tegen de echte MediaGuard-core 1.2.2-image (digest) met nep-arr | `tests/stack/mediaguard_contract.py`, CI-job | container-E2E (Sim-getest) | — |
| P3-10 | P1 | B05-004 | Resource-voetafdruk: dagelijkse `resource_samples` (DB, tabellen, back-ups, `/appdata`-ruimte, DB-verbindingen), `growth` + `provenance` in `/system/maintenance`, sectie "Opslag en groei" | `retention.py`, migratie `0609_resource_samples`, web Systeem | pytest (30 gesimuleerde dagen) + Playwright | — |
| P3-11 | P1 | B05 §1 | Playwright 360/768 + E2E van offline/storing/leeg | `apps/web/playwright.config.ts`, `e2e/*` | Playwright | — |
| P3-12 | P1 | B05-010 | Header-index na herstel herbouwd (test; zo nodig in `RESYNC_JOBS`) | `backups.py:77-81` | pytest | — |
| P3-13 | P2 | B05-010, I04 §4 | Terugdraaien zonder terminal: TrueNAS-snapshot (web-UI) + oude tag als hoofdroute; `docker exec` alleen expert | `docs/TRUENAS_CUSTOM_APP.md`, `docs/MIGRATION.md` | — | — |
| P3-14 | P2 | contract §5/§10 | OpenAPI-driftcheck in CI; getrackte `apps/core/mso_core.egg-info` uit git + `.gitignore`; verwijzing naar ontbrekend `THIRD_PARTY.md` oplossen | `ci.yaml`, repo-hygiëne | CI | — |

Niet in P3 (bewust): MSO-UI naar de Hub verplaatsen (MSO houdt eigen UI; de Hub toont kaarten + deeplinks), tabellen
naar UUID migreren, scrypt → argon2id forceren, eigen push-outbox (polling volstaat voor v1), mypy over de hele
codebase (vervolgpunt), een eigen logcollector.

## 10. Wat in P3 nooit mag (samengevat)

- Geen tweede change-set-, back-up-, retentie-, noodrem-, override-, koppelingsbewakings- of anti-loopmechanisme naast
  het bestaande (`INVENTARIS.md` §13); de connectorlaag roept de bestaande functies aan.
- Geen uitbreiding van `ALLOWED_WRITES` richting MediaGuard; geen MediaGuard-, Prowlarr-, Sonarr/Radarr-configuratie
  wijzigen; geen directe database-toegang tot andere apps.
- Geen schrijfpad van de Hub rechtstreeks naar SABnzbd/qBittorrent/Prowlarr; alles via MSO-change-sets.
- Geen automatische verwijdering van torrents/bestanden/categorieën; vergrendelde integriteitsgrenzen blijven
  vergrendeld; AI-adviseur blijft standaard uit en zonder betaalde overage.
- Niet zelf taggen of `release/VERSION` wijzigen zonder expliciete releasebeslissing (start `release.yaml`).
