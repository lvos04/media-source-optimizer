# Media Source Optimizer uitbreidingen — volledige bouwspecificatie

**Type/eigenaar:** Bestaand: lvos04/media-source-optimizer  
**Doel:** MSO 1.5.0 is gedocumenteerde releasebasis; 1.6.0 veranderingen staan op HEAD maar release/VERSION was nog 1.5.0.

**Afhankelijkheden:** MediaGuard v1.2.2, SABnzbd/Prowlarr/qBit, Hub read model.

**Bron:** https://github.com/lvos04/media-source-optimizer

## 1. Concrete schermen en gebruikersreizen
Bestaande Overzicht, Zoeken, Usenet, Torrents, Indexers, Herstel, Kosten, Automatisering, Logs, Backups; 1.6 kandidaat Voetafdruk en categorieanalyse

**Per scherm minimaal:** responsief (360/390/768/1440 px), status leeg/laden/storing/offline, gedocumenteerde navigatie, zoek/filter/paginering, rolcontrole, datum van laatste update, bronherkomst, fout in het Nederlands, toegankelijk toetsenbordgebruik en zichtbaar auditspoor voor wijzigingen.

## 2. Datamodel en levensduur
mso_provider, torrent_tracker, usenet_failure, category_drift, suggested_change, canary_outcome, source_metric, footprint_sample, action_audit; eigenaar blijft MSO

Dwing database-migraties met forward-schema-tests af; time-series met retentie/aggregatie; primaire gegevens alleen bij bron. Entiteiten krijgen stable ID, versie, eigenaar, timestamps en provenance waar toepasselijk. Secrets alleen als vault-reference.

## 3. API en integratie-contracten
Gelijkblijvend read-only MediaGuard connector en beperkte allowed writes; Hub consumeert MSO API (versie/capability probe).

Bij alle endpoints: authenticatie, rol, inputvalidatie, idempotency voor mutaties, paginering of cursors voor lijsten, rate limits, time-outs, versie/capability-detectie en machineleesbare foutcodes. De genoemde uitbreidingsroutes zijn ontwerpeisen, niet automatisch bestaande endpoints. API-contracten moet de bouwer in OpenAPI vastleggen en e2e verifiëren.

## 4. Bouwfasen (verplichte volgorde)

### F0 - Inventaris & onderzoek
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B05-001 — Releasegate v1.6.0
**Status bij start:** Controleren; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Vergelijk code HEAD op 9 nov met gedocumenteerde v1.5.0; versie van image en package release kan achterlopen.
**Implementatie in code/diensten:** Inspect release/VERSION, changelog, git tags, built GHCR image, CI, database migration 0600 en daadwerkelijke api.
**Schermen/bediening:** Release checker toont ‘development kandidaat’ tot officiële tag/test bevestigd.
**Acceptatie / concrete toets:** Geen 1.6.0 als stable deployen zonder build, migratietest en hardwareacceptatie.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F1 - Kern en contracten
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B05-002 — Actuele oorzaak per mislukking
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Gebruik geplande 1.6 parsing van echte SABnzbd failure/log/categorie en pas alleen zinvolle retries toe.
**Implementatie in code/diensten:** Read outcome_at/failed_at, normalized reasons, stale detection, tests on live actual SABnzbd with non-sensitive data.
**Schermen/bediening:** Per download oorzaak, bewijsbron, laatste poging en veilige vervolgstap.
**Acceptatie / concrete toets:** Een failure is niet meteen ‘retention’ wanneer password mismatch of unpack error.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B05-003 — Categorie * diagnose
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Lees optionele MediaGuard endpoints voor arr clients en categorieën, stel alleen SABnzbd categoriefix voor na approval.
**Implementatie in code/diensten:** Respect strict allowed GET routes + approved SAB write; idempotence; no managed MediaGuard config changes.
**Schermen/bediening:** Categoriekaart met oorzaak en schaduwpreview van mapconsequenties.
**Acceptatie / concrete toets:** MSO overschrijft geen Sonarr/Radarr instellingen en beïnvloedt MediaGuard cleanup niet onbedoeld.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F2 - Functionaliteit
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B05-004 — Overzicht Voetafdruk
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Verbindingen, metadataretentie, opslagraming, logvolume en DB footprint in 1.6 versie evalueren.
**Implementatie in code/diensten:** Measure DB tables/storage/log retention, per-source metrics, bounded sampling; avoid own privacy leak.
**Schermen/bediening:** Voetafdruk-scherm met echt/gemeten/geschat.
**Acceptatie / concrete toets:** Na 30 dagen logs zijn respectieve retentieregels getest en DB groeit gecontroleerd.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B05-005 — Heldere Autopilot grenzen
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Bestaande modus read-only/beheer/autopilot mag geen files verwijderen en respecteert human-approved changes.
**Implementatie in code/diensten:** Audit all source side effects, policy tests; stop action on source drift; compare changes with 1.6 override tests.
**Schermen/bediening:** Wat MSO deed / Wacht op jou / taak status met rollback uitleg.
**Acceptatie / concrete toets:** Kapotte provider automatisch tijdelijk uit alleen als laatste werkende blijft; geen torrentdelete.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F3 - Integraties en veiligheid
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B05-006 — Live MediaGuard compatibiliteit
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Vervang mock-only garantie met echte read API- en search-flowtesten tegen v1.2.2.
**Implementatie in code/diensten:** Contract tests incl optional 1.6 read routes, negative unauthorized path tests, quota, emergency stop, backoff.
**Schermen/bediening:** Compatibiliteitsmatrix in UI met provenance en ontbrekende route.
**Acceptatie / concrete toets:** 403/404 optioneel read-route degradeert, alleen officiële writes kunnen uitvoeren.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B05-007 — SAB/qBit/Prowlarr live acceptatie
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Test echte API versies op NAS, categories, missing file excludes, retentie checks, seed/hardlink.
**Implementatie in code/diensten:** Staging dataset, test-torrent and NZB with permitted sources, before/after snapshots and cleanup approved.
**Schermen/bediening:** Resultaten met echte service versie en testlogs.
**Acceptatie / concrete toets:** Geen duplicate loop in Prowlarr; private torrent guardrails blijven geldig.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F4 - Automatisering
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B05-008 — Kostenoptimalisatie
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Per-provider unieke bijdrage, overlap, kosten per succesvolle GB/download met bias en onzekerheid.
**Implementatie in code/diensten:** Read receipts handmatig, daily metrics and sensitivity scenarios; never auto cancel subscription.
**Schermen/bediening:** Simulatie ‘wat als ik provider X stop’ met betrouwbaarheidsinterval.
**Acceptatie / concrete toets:** Provider zonder stats toont unknown; geen gegarandeerde besparing claimen.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B05-009 — Integratie met Hub
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** MSO incidents, actievoorstellen en meetgegevens in Hub, admin approvals blijven gekoppeld aan MSO change set.
**Implementatie in code/diensten:** Scoped API adapter + event envelope, action ownership and explicit idempotency; Hub approves via MSO supported flow.
**Schermen/bediening:** Één probleem-inbox en open-in-MSO deep links.
**Acceptatie / concrete toets:** Geen dubbele actie als tegelijkertijd in Hub en MSO op goedkeuren gedrukt wordt.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F5 - Hardening & UX
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B05-010 — Back-up en migratie
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** PostgreSQL backup consistent, 0600 migratie aantoonbaar, terugkeer met bijpassende snapshot en image.
**Implementatie in code/diensten:** Migrate on copied DB + retention verification, restore in disposable stack.
**Schermen/bediening:** Migratie-rapport en rollback gate.
**Acceptatie / concrete toets:** Na terugzet blijven audit, index en credentials aanwezig en worden jobs niet dubbel hervat.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

## 5. Niet-regressie, randgevallen en verbodsbepalingen
Bevestig release-status voordat je image-tag aanpast; in repository stond release/VERSION nog op 1.5.0 terwijl 1.6.0 changes al gemerged zijn. Bestaande owner matrix uit INTEGRATION_MEDIAGUARD.md verplicht laten gelden.

## 6. Oplevering door bouwer
Opleveren: (1) broncode en tests, (2) migration scripts en versiecompatibiliteit, (3) Compose/TrueNAS app manifest met gepinde images, (4) .env.example zonder geheimen, (5) OpenAPI/connector schemas, (6) test- en auditrapport, (7) restore bewijs, (8) release notes, (9) gebruikershandleiding, (10) screenshots web/mobiel. Markeer niet-geteste hardware/echte accounts als `NIET GEVALIDEERD`.

## 7. Proof of Done
Alle acceptatiecriteria in Excel zijn gekoppeld aan testbewijs en codecommit; bij stroomonderbreking, uitval van afhankelijke app, NAS-herstart, gewijzigde credentials en incompatibele versie degradeert de service aantoonbaar veilig. Geen directe writes aan de database van een andere app.
