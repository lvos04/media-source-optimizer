# MSO container-stack smoke test — 2026-11-10T09:30:21Z

- Resultaat: **19 ok, 0 mislukt, 0 overgeslagen**
- Basis-URL: `http://127.0.0.1:48081` (via web-proxy); MSO_FEATURES: `(alle)`
- Host: Linux-7.0.0-42-generic-x86_64-with-glibc2.43, 22 CPU's (sandbox/CI — géén TrueNAS-hardware)

| # | Check | Status | Duur | Detail |
|---|---|---|---|---|
| 1 | stack: alle containers gezond, init + migrate exit 0 | ok | 6.45s | api=running/healthy/0, init=exited/-/0, migrate=exited/-/0, mock-mediaguard=running/-/0, mock-newznab=running/-/0, mock-nntp=running/-/0, mock-prowlarr=running/-/0, mock-qbittorrent=running/-/0, mock-sabnzbd=running/-/0, mock-spotweb=running/-/0, postgres=running/healthy/0, web=running/healthy/0, worker=running/healthy/0 |
| 2 | web: UI en same-origin proxy naar core | ok | 0.09s | /login 200, /api/v1 via proxy 200, /newznab/api?t=caps → 200, /torznab/api?t=caps → 200 |
| 3 | setup: eerste beheerder aanmaken, tweede keer geweigerd | ok | 0.12s | beheerder 'stackadmin' aangemaakt; tweede setup → 409 |
| 4 | auth: uitloggen, foutief wachtwoord 401, inloggen, /auth/me | ok | 0.21s | logout → me 401; fout wachtwoord 401; login → me admin |
| 5 | security: CSRF-token verplicht bij mutaties | ok | 0.05s | zonder token 403, fout token 403, met token 200 |
| 6 | security: rollen (viewer mag geen config/gebruikers wijzigen) | ok | 0.2s | viewer: lezen 200; settings/users/backups 403; anoniem 401 |
| 7 | koppelingen: elke connector naar zijn mock + verbindingstest | ok | 0.71s | mediaguard: healthy; prowlarr: degraded; qbittorrent: healthy; sabnzbd: healthy |
| 8 | security: SSRF-weigeringen (metadata, schema, credentials in URL) | ok | 0.05s | 6 onveilige URL's → 422 (connector mediaguard); test naar 169.254.169.254 geweigerd |
| 9 | API: lees-endpoints en OpenAPI | ok | 0.27s | 11 endpoints 200, OpenAPI 185 paden, schema v607, health=degraded |
| 10 | back-up: maken via worker-job, checksum, bestand aanwezig | ok | 1.03s | mso-20261110T092729Z-manual.dump (154857 B), zonder geheimen |
| 11 | herstel: restore zet DB terug, sessies vervallen, koppelingsgeheimen blijven | ok | 3.33s | hersteld mso-20261110T092729Z-manual.dump (vangnet mso-20261110T092730Z-pre-restore.dump); onbekende back-up → 422 |
| 12 | herstel: beschadigde en ontbrekende back-up geweigerd, database ongewijzigd | ok | 2.23s | beschadigd → 422 (Checksum klopt niet: back-up is beschadigd); ontbrekend → 422 (Back-upbestand ontbreekt) |
| 13 | jobs: onderbroken job (verlopen lease) wordt na worker-start hervat | ok | 45.76s | job 27 running(crash) → done; samenvatting: ' · hervat na herstart' |
| 14 | jobs: worker-proces SIGKILL → restart policy herstart, worker weer gezond en functioneel | ok | 8.03s | pid 7 gedood; RestartCount 0→1; nieuwe job done |
| 15 | persistentie: docker compose restart (alle services) — data en login blijven | ok | 25.61s | 2 gebruikers, 4 koppelingen, 6 back-ups ongewijzigd |
| 16 | persistentie: docker compose down + up (containers opnieuw aangemaakt) | ok | 55.02s | gebruikers, koppelingen (incl. versleutelde geheimen) en back-ups intact; login werkt |
| 17 | resources: docker stats in rust en onder belasting | ok | 28.15s | 480 verzoeken in 1.3s (0 fouten), p95 37.5 ms; api idle 62.1 MiB / load 65.3 MiB, postgres idle 34.3 MiB / load 52.2 MiB, web idle 39.7 MiB / load 44.6 MiB, worker idle 61.3 MiB / load 60.2 MiB |
| 18 | logs: geen geheimen in de logs van MSO-containers | ok | 1.22s | 9 geheimen (beheer-/viewer-wachtwoord, DB-wachtwoord, koppelingsgeheimen) niet gevonden in 46356 bytes log van init/postgres/migrate/api/worker/web; mock-toegangslogs met sleutel (geen MSO): ['mock-sabnzbd'] |
| 19 | logs: 0 foutmeldingen (ERROR/CRITICAL/Traceback/FATAL/PANIC) | ok | 1.18s | geen foutregels in de logs van alle containers; 5 verwachte PostgreSQL-weigering(en) binnen 2 bewuste herstartvenster(s) |

## Resources — rust (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 0.12 | 62.1 | 11 | 768MiB |
| mock-mediaguard | 0.1 | 36.0 | 5 | 256MiB |
| mock-newznab | 0.11 | 34.6 | 5 | 256MiB |
| mock-nntp | 0.01 | 8.0 | 1 | 256MiB |
| mock-prowlarr | 0.1 | 38.1 | 5 | 256MiB |
| mock-qbittorrent | 0.11 | 42.0 | 5 | 256MiB |
| mock-sabnzbd | 0.1 | 35.2 | 5 | 256MiB |
| mock-spotweb | 0.14 | 34.6 | 5 | 256MiB |
| postgres | 2.68 | 34.3 | 10 | 1GiB |
| web | 0.03 | 39.7 | 11 | 512MiB |
| worker | 8.84 | 61.3 | 7 | 768MiB |

## Resources — onder belasting (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 39.17 | 65.3 | 19 | 768MiB |
| mock-mediaguard | 0.1 | 36.0 | 5 | 256MiB |
| mock-newznab | 0.08 | 34.6 | 5 | 256MiB |
| mock-nntp | 0.01 | 8.0 | 1 | 256MiB |
| mock-prowlarr | 0.08 | 38.1 | 5 | 256MiB |
| mock-qbittorrent | 0.11 | 41.7 | 5 | 256MiB |
| mock-sabnzbd | 0.1 | 35.0 | 5 | 256MiB |
| mock-spotweb | 0.09 | 34.6 | 5 | 256MiB |
| postgres | 10.84 | 52.2 | 16 | 1GiB |
| web | 9.12 | 44.6 | 11 | 512MiB |
| worker | 0.04 | 60.2 | 7 | 768MiB |

Belasting: 480 API-verzoeken (8 parallel) + 1 back-up-job in 1.3 s (362.7 req/s, p50 19.4 ms, p95 37.5 ms, 0 fouten).

## Images

- api: `media-source-optimizer-core:stack`
- init: `media-source-optimizer-core:stack`
- migrate: `media-source-optimizer-core:stack`
- mock-mediaguard: `mso-mocks:stack`
- mock-newznab: `mso-mocks:stack`
- mock-nntp: `mso-mocks:stack`
- mock-prowlarr: `mso-mocks:stack`
- mock-qbittorrent: `mso-mocks:stack`
- mock-sabnzbd: `mso-mocks:stack`
- mock-spotweb: `mso-mocks:stack`
- postgres: `mirror.gcr.io/library/postgres:16.15-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab`
- web: `media-source-optimizer-web:stack`
- worker: `media-source-optimizer-core:stack`
