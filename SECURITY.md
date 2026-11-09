# Beveiliging — Media Source Optimizer

Dit document beschrijft het beveiligingsmodel zoals het **in de code** is geïmplementeerd en getest.
Verwijzingen naar tests staan tussen haakjes; de testresultaten staan in `TEST_RESULTS.md`.

## 1. Uitgangspunten

- **Alleen LAN.** De stack publiceert één poort (web, standaard `38081`). Geen host-network, geen
  `docker.sock`, geen publieke toegang of externe hosting standaard. Voor toegang van buitenaf: zelf een
  reverse proxy met TLS ervoor zetten en `MSO_SECURE_COOKIES=1` instellen (zie `docs/TRUENAS_INSTALL.md`).
- **Least privilege.** Containers draaien als uid/gid 568 (TrueNAS `apps`), `cap_drop: ALL`,
  `no-new-privileges`, read-only root filesystem waar mogelijk, expliciete CPU/RAM/PID-limieten.
- **Geen geheimen in YAML of images.** Alle API-sleutels/wachtwoorden worden via de setup-wizard ingevoerd
  en versleuteld opgeslagen. De Compose-stack bevat alleen paden en poorten.
- **Standaard alleen-lezen.** Na installatie staat de app in *alleen-lezen modus*: er wordt niets naar
  SABnzbd, qBittorrent of Prowlarr geschreven tot een beheerder bewust *beheermodus* kiest.

## 2. Authenticatie en sessies (`apps/core/mso/auth.py`)

| Maatregel | Implementatie | Test |
|---|---|---|
| Wachtwoordhash | scrypt (n=2¹⁵, r=8, p=1), 16-byte salt per gebruiker | `test_first_run_setup_creates_admin_once` |
| Wachtwoordbeleid | ≥ 10 tekens, gemengde hoofdletters of cijfers | `test_weak_password_rejected` |
| Sessie | willekeurig 256-bit token in cookie `mso_session` (HttpOnly, SameSite=Strict, Secure optioneel); alleen SHA-256 van het token in de DB; verloopt (standaard 12 u) | `test_login_cookie_is_httponly_strict`, `test_login_logout_and_cookie_flags` |
| CSRF | synchroniser token: elke muterende request met sessie moet `X-CSRF-Token` meesturen | `test_csrf_required_for_mutations` |
| Brute force | inlogpogingen begrensd per IP én per gebruikersnaam (standaard 10/min), pogingen gelogd | `test_wrong_password_and_rate_limit` |
| Eerste beheerder | `POST /api/v1/setup/admin` werkt alleen zolang er geen gebruikers zijn (tabel-lock tegen races) | `test_first_run_setup_creates_admin_once` |
| Laatste beheerder | kan niet worden gedegradeerd/uitgeschakeld | `test_last_admin_cannot_be_demoted` |

## 3. Rollen (RBAC)

| Rol | Mag |
|---|---|
| `viewer` | alles lezen (status, zoekresultaten, kosten, audit) |
| `operator` | + tests uitvoeren, handmatig naar downloadclient sturen, wijzigingen zonder toestemmingsplicht toepassen/terugdraaien, automatisering pauzeren |
| `admin` | + koppelingen/geheimen, instellingen, gebruikers, modus, goedkeuren van `toestemming nodig`, back-up/herstel |

Getest in `test_role_enforcement`, `test_unauthenticated_is_401` en per feature-module.

Regels die sinds 1.6.0 gelden:

| Actie | Rol | Modus |
|---|---|---|
| SABnzbd-servertest (**Testen**) | operator | elke modus, ook alleen-lezen: de test verandert niets (`test_server_test_endpoint_any_mode_roles_and_no_secrets`) |
| Mislukte downloads **Opnieuw beoordelen**; opnieuw proberen **Hervatten** | operator | elke modus (alleen MSO's eigen labels/stand) |
| Noodrem **Hervatten** | operator | elke modus (`test_noodrem_after_failures_and_hervatten`) |
| **Autopilot terugdraaien** | operator | niet in alleen-lezen; verwijderingen staan er nooit in (`test_undo_autopilot_window`) |
| Voetafdruk **Nu controleren** / **Alles terugzetten** | operator | controleren: elke modus (alleen lezen); terugzetten: niet in alleen-lezen. MSO's eigen Prowlarr-indexer verwijderen blijft `toestemming_nodig` (beheerder) |
| SABnzbd-categorie **voorstellen** en goedkeuren | admin | nooit automatisch toegepast, ook niet in Autopilot (MSO zet het voorstel wel zelf klaar in "Wacht op jou") |
| SABnzbd-categorie **terugdraaien** (= verwijderen) | admin | alleen met de hand; nooit door de reconciler, het herstel na een mislukte toepassing, automatisch terugdraaien of "Alles terugzetten"; operator krijgt 403 (`test_only_an_admin_may_roll_back_a_category_and_never_via_alles_terugzetten`) |
| Onderhoud **Nu opruimen** | admin | elke modus (alleen MSO's eigen rijen) |

In **alleen-lezen** wordt nog steeds niets naar SABnzbd, qBittorrent of Prowlarr geschreven. Wat in alleen-lezen wél
draait, leest alleen: de servertest (SABnzbd test de verbinding, er verandert niets), de voetafdrukcontrole, de
categoriediagnose en de synchronisaties. De reconciler voor onderbroken wijzigingen schrijft in alleen-lezen niets
(`test_reconcile_never_writes_in_read_only`). Een herstel van een back-up zet de modus nooit terug naar een oudere
stand (modus, pauze en noodrem blijven).

## 4. Geheimen (`apps/core/mso/vault.py`)

- AES-256-GCM; sleutel in `APPDATA/secrets/vault.key` (0600, map 0700), per geheim een eigen nonce en de
  geheimnaam als associated data (verwisselen van ciphertexts wordt gedetecteerd).
- De API geeft nooit een geheim terug: alleen `ingesteld` / `leeg` (`test_vault_encrypts_and_never_exposes`).
- Back-ups bevatten standaard **geen** geheimen (`--exclude-table-data=secrets`); alleen met expliciete
  keuze van een beheerder. De sleutel zelf zit nooit in een dump.
- **SABnzbd-servertest (1.6.0).** MSO stuurt SABnzbd de serverinstellingen uit `get_config` met het wachtwoord precies
  zoals SABnzbd het maskeert (`**********`) plus `server=<naam>`; SABnzbd vult dan zelf het opgeslagen wachtwoord in
  (nagekeken in `api.test_nntp_server_dict`, SABnzbd 5.1.3). MSO ziet of verstuurt nooit een serverwachtwoord. De
  gebruikersnaam staat alleen tijdens die ene aanroep in het geheugen (in de POST-body, nooit in de URL of een log),
  wordt uit SABnzbd's antwoord vervangen door `<gebruiker>` en wordt **nooit opgeslagen**. Om te zien dat je een server
  in SABnzbd aanpaste, bewaart MSO alleen een HMAC-vingerafdruk van host/poort/SSL/gebruikersnaam/vervaldatum/quotum,
  met een willekeurige sleutel in de kluis (`test_connector_test_server_uses_sabnzbds_stored_password`,
  `test_connector_servers_reads_account_fields_and_fingerprint`).
- **MediaGuard-leesroutes (1.6.0).** Uit instanties, downloadclients en inrichting houdt de connector alleen namen,
  types, aan/uit en categorieën over; URL's, host, poort, gebruikersnaam en (gemaskeerde) sleutels vallen weg vóór er
  iets wordt opgeslagen of getoond (`test_mediaguard_reads_keep_only_whitelisted_fields`).

## 5. Logs en audit

- Een logfilter (`RedactingFilter`) maskeert `apikey=`, `password=`, `token=`, `passkey=`, `Authorization`,
  cookies en inloggegevens in URL's in **elke** logregel (`test_redaction_in_logs`).
- Fouten van jobs worden geredigeerd opgeslagen (`test_job_retry_backoff_then_failed`).
- Audittrail (`audit_log`): wie, wanneer, wat, doel, oud → nieuw, gekoppelde wijziging. Details worden
  vóór opslag opgeschoond (sleutels met password/apikey/token/secret/passkey/cookie → `***`). Het auditlogboek wordt
  door de bewaartermijnen (1.6.0) nooit opgeruimd.
- **Regels uit het verwerkingslog van SABnzbd (1.6.0)** worden vóór opslag geredigeerd: geheimen gemaskeerd, de setnaam
  weggehaald en absolute paden (ook met spaties) ingekort tot `…/<laatste deel>`
  (`test_lines_are_redacted_and_signatures_ignore_names_and_paths`, `test_paths_with_spaces_and_short_paths_are_shortened`).
- `connectors.last_error` van de koppelingsbewaking is altijd geredigeerd: nooit een API-sleutel, wachtwoord of
  gebruikersnaam (`test_last_error_is_redacted`). Ook `pg_dump`/`pg_restore`-meldingen bij back-up en herstel worden
  geredigeerd.

## 6. Uitgaande verbindingen (SSRF) — `mso/security.py::check_url`

- Alleen `http`/`https`; geen inloggegevens in URL's.
- Altijd geblokkeerd: link-local/cloud-metadata (`169.254.0.0/16`, `fe80::/10`, `100.100.100.200`),
  multicast, `0.0.0.0/8`, broadcast. Loopback alleen met `MSO_ALLOW_LOOPBACK=1` (tests/dev).
- LAN-adressen (RFC1918) zijn toegestaan — de app moet SABnzbd/qBittorrent/Prowlarr op het LAN bereiken.
- DNS wordt geresolved en elk adres gecontroleerd; HTTP-redirects worden niet automatisch gevolgd.
- Getest in `test_ssrf_guard`, `test_ssrf_on_connector_save`, `test_loopback_blocked_unless_allowed`.

## 7. HTTP-hardening

- Security headers op elke respons (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`,
  `Cache-Control: no-store` voor API).
- CORS staat standaard uit (de webcontainer proxyt `/api/v1` same-origin). Alleen via `MSO_CORS_ORIGINS`.
- API-rate-limit per sessie/IP (instelbaar, standaard 600/min). Foutrespons altijd
  `{detail, code, correlation_id}` zonder stacktraces.
- Extra limieten per persoon op de nieuwe, dure routes van 1.6.0 (elk verzoek spreekt live SABnzbd, qBittorrent,
  Prowlarr of MediaGuard aan, of zet veel in gang):

  | Route | Limiet |
  |---|---|
  | `POST /api/v1/usenet/sab/servers/{server}/test` (servertest) | 10/min |
  | `POST /api/v1/usenet/sab/reclassify` (opnieuw beoordelen) | 3/min |
  | `GET /api/v1/usenet/categories/diagnosis?refresh=true` | 6/min |
  | `POST /api/v1/usenet/categories/propose` | 6/min |
  | `POST /api/v1/automation/footprint/refresh` | 6/min |
  | `GET /api/v1/automation/footprint/restore` (live lijst) | 12/min |
  | `POST /api/v1/automation/footprint/restore` (Alles terugzetten) | 6/min |
  | `POST /api/v1/automation/undo` (Autopilot terugdraaien) | 3/min |

## 8. Wijzigingen aan externe systemen

Elke schrijfactie naar SABnzbd/qBittorrent/Prowlarr is een **change set** (`mso/changes.py`):
voorbeeld → (goedkeuring) → toepassen → terugdraaien. Afwijkingsdetectie voorkomt dat MSO een waarde
overschrijft die intussen door een ander systeem (bijv. MediaGuard's eigen Prowlarr-reconcile) is gewijzigd.
Mislukt een deel, dan wordt de rest automatisch hersteld. Zie `test_change_*`.

Verwijderen of uitschakelen (op-vlaggen `destructive`/`irreversible`) kan alleen in een wijziging met risico
`toestemming_nodig` en wordt nooit automatisch toegepast (`test_destructive_ops_need_permission_never_automatic…`).
De enige verwijderroute is de opruiming van verdachte (uitvoerbare) torrents. Die controleert bij toepassen opnieuw
of elke torrent nog aanwezig, nog verdacht en precies dezelfde is als in het voorbeeld (`test_torrent_cleanup.py`).
(Uitzondering sinds 1.5.0: in Autopilot zet MSO een kapotte Usenet-server zelf uit; sinds 1.6.0 alleen na een
bevestigende SABnzbd-servertest, en nooit de laatste werkende.)

Sinds 1.6.0:
- **Een SABnzbd-categorie aanmaken** is altijd `toestemming_nodig` (MediaGuard ruimt downloads in die categorie daarna
  op). Terugdraaien zou de categorie verwijderen; zo'n op (`Applier.rollback_deletes`) wordt nooit onbeheerd
  teruggezet en alleen door een beheerder (zie §3).
- **Driftcontrole bij terugdraaien**: een waarde die intussen buiten MSO is veranderd, wordt overgeslagen en nooit
  overschreven; elke teruggezette waarde wordt teruggelezen (`test_rollback_skips_values_changed_outside_mso`).
- **Crash-veilig en atomair**: een advisory lock per wijziging en een journaal (`applying`) voorkomen dubbel toepassen
  en halve wijzigingen na een crash (`test_sab_crash_mid_apply_reconciled_over_http`).
- **Noodrem**: te veel automatische terugdraaiingen of mislukkingen in 24 uur zetten alle automatische schrijfacties
  stil tot een operator hervat.
- **Nep-releases**: vóór elke eigen verzending controleert MSO de inhoud. Een onleesbaar .torrent-bestand wordt
  geweigerd (fail-closed); NZB-bestanden worden met `defusedxml` gelezen (geen entity-expansie); in de weigerreden staan
  geen parserdetails, omdat die bytes uit het bestand (bijv. een passkey) kunnen citeren
  (`test_torrent_payload_v1_v2_hybrid_and_unreadable`, `test_recovery_send_magnet_and_unreadable_torrent`).
- **MediaGuard**: alleen nieuwe leesroutes; `ALLOWED_WRITES` is ongewijzigd (`test_compat_matrix_lists_the_optional_read_routes`).

Vergrendelde integriteitsgrenzen (niet uitschakelbaar, `test_locked_integrity_settings_cannot_change`):
nooit zelfstandig bestanden verwijderen; nooit seedende data herschrijven; geen publieke trackers/DHT op private
torrents; nooit automatisch abonnementen opzeggen; MediaGuard-kwaliteitseisen zijn leidend.

## 9. Melden van kwetsbaarheden

Dit is een zelf-gehoste applicatie zonder publieke dienst. Meld problemen via een privé issue in de
repository `lvos04/media-source-optimizer`.
