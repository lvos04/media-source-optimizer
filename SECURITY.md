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

## 4. Geheimen (`apps/core/mso/vault.py`)

- AES-256-GCM; sleutel in `APPDATA/secrets/vault.key` (0600, map 0700), per geheim een eigen nonce en de
  geheimnaam als associated data (verwisselen van ciphertexts wordt gedetecteerd).
- De API geeft nooit een geheim terug: alleen `ingesteld` / `leeg` (`test_vault_encrypts_and_never_exposes`).
- Back-ups bevatten standaard **geen** geheimen (`--exclude-table-data=secrets`); alleen met expliciete
  keuze van een beheerder. De sleutel zelf zit nooit in een dump.

## 5. Logs en audit

- Een logfilter (`RedactingFilter`) maskeert `apikey=`, `password=`, `token=`, `passkey=`, `Authorization`,
  cookies en inloggegevens in URL's in **elke** logregel (`test_redaction_in_logs`).
- Fouten van jobs worden geredigeerd opgeslagen (`test_job_retry_backoff_then_failed`).
- Audittrail (`audit_log`): wie, wanneer, wat, doel, oud → nieuw, gekoppelde wijziging. Details worden
  vóór opslag opgeschoond (sleutels met password/apikey/token/secret/passkey/cookie → `***`).

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

## 8. Wijzigingen aan externe systemen

Elke schrijfactie naar SABnzbd/qBittorrent/Prowlarr is een **change set** (`mso/changes.py`):
voorbeeld → (goedkeuring) → toepassen → terugdraaien. Afwijkingsdetectie voorkomt dat MSO een waarde
overschrijft die intussen door een ander systeem (bijv. MediaGuard's eigen Prowlarr-reconcile) is gewijzigd.
Mislukt een deel, dan wordt de rest automatisch hersteld. Zie `test_change_*`.

Verwijderen of uitschakelen (op-vlaggen `destructive`/`irreversible`) kan alleen in een wijziging met risico
`toestemming_nodig` en wordt nooit automatisch toegepast (`test_destructive_ops_need_permission_never_automatic…`).
De enige verwijderroute is de opruiming van verdachte (uitvoerbare) torrents. Die controleert bij toepassen opnieuw
of elke torrent nog aanwezig, nog verdacht en precies dezelfde is als in het voorbeeld (`test_torrent_cleanup.py`).

Vergrendelde integriteitsgrenzen (niet uitschakelbaar, `test_locked_integrity_settings_cannot_change`):
nooit zelfstandig bestanden verwijderen; nooit seedende data herschrijven; geen publieke trackers/DHT op private
torrents; nooit automatisch abonnementen opzeggen; MediaGuard-kwaliteitseisen zijn leidend.

## 9. Melden van kwetsbaarheden

Dit is een zelf-gehoste applicatie zonder publieke dienst. Meld problemen via een privé issue in de
repository `lvos04/media-source-optimizer`.
