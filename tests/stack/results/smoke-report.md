# MSO container-stack smoke test — 2026-11-10T07:38:33Z

- Resultaat: **19 ok, 0 mislukt, 0 overgeslagen**
- Basis-URL: `http://127.0.0.1:48081` (via web-proxy); MSO_FEATURES: `(alle)`
- Host: Linux-7.0.0-42-generic-x86_64-with-glibc2.43, 22 CPU's (sandbox/CI — géén TrueNAS-hardware)

| # | Check | Status | Duur | Detail |
|---|---|---|---|---|
| 1 | stack: alle containers gezond, init + migrate exit 0 | ok | 6.58s | api=running/healthy/0, init=exited/-/0, migrate=exited/-/0, mock-mediaguard=running/-/0, mock-newznab=running/-/0, mock-nntp=running/-/0, mock-prowlarr=running/-/0, mock-qbittorrent=running/-/0, mock-sabnzbd=running/-/0, mock-spotweb=running/-/0, postgres=running/healthy/0, web=running/healthy/0, worker=running/healthy/0 |
| 2 | web: UI en same-origin proxy naar core | ok | 0.1s | /login 200, /api/v1 via proxy 200, /newznab/api?t=caps → 200, /torznab/api?t=caps → 200 |
| 3 | setup: eerste beheerder aanmaken, tweede keer geweigerd | ok | 0.11s | beheerder 'stackadmin' aangemaakt; tweede setup → 409 |
| 4 | auth: uitloggen, foutief wachtwoord 401, inloggen, /auth/me | ok | 0.2s | logout → me 401; fout wachtwoord 401; login → me admin |
| 5 | security: CSRF-token verplicht bij mutaties | ok | 0.05s | zonder token 403, fout token 403, met token 200 |
| 6 | security: rollen (viewer mag geen config/gebruikers wijzigen) | ok | 0.22s | viewer: lezen 200; settings/users/backups 403; anoniem 401 |
| 7 | koppelingen: elke connector naar zijn mock + verbindingstest | ok | 0.87s | mediaguard: healthy; prowlarr: degraded; qbittorrent: healthy; sabnzbd: healthy |
| 8 | security: SSRF-weigeringen (metadata, schema, credentials in URL) | ok | 0.05s | 6 onveilige URL's → 422 (connector mediaguard); test naar 169.254.169.254 geweigerd |
| 9 | API: lees-endpoints en OpenAPI | ok | 0.26s | 11 endpoints 200, OpenAPI 185 paden, schema v607, health=degraded |
| 10 | back-up: maken via worker-job, checksum, bestand aanwezig | ok | 1.03s | mso-20261110T073552Z-manual.dump (154862 B), zonder geheimen |
| 11 | herstel: restore zet DB terug, sessies vervallen, koppelingsgeheimen blijven | ok | 3.31s | hersteld mso-20261110T073552Z-manual.dump (vangnet mso-20261110T073553Z-pre-restore.dump); onbekende back-up → 422 |
| 12 | herstel: beschadigde en ontbrekende back-up geweigerd, database ongewijzigd | ok | 2.47s | beschadigd → 422 (Checksum klopt niet: back-up is beschadigd); ontbrekend → 422 (Back-upbestand ontbreekt) |
| 13 | jobs: onderbroken job (verlopen lease) wordt na worker-start hervat | ok | 26.93s | job 16 running(crash) → done; samenvatting: ' · hervat na herstart' |
| 14 | jobs: worker-proces SIGKILL → restart policy herstart, worker weer gezond en functioneel | ok | 8.13s | pid 7 gedood; RestartCount 0→1; nieuwe job done |
| 15 | persistentie: docker compose restart (alle services) — data en login blijven | ok | 29.17s | 2 gebruikers, 4 koppelingen, 6 back-ups ongewijzigd |
| 16 | persistentie: docker compose down + up (containers opnieuw aangemaakt) | ok | 57.07s | gebruikers, koppelingen (incl. versleutelde geheimen) en back-ups intact; login werkt |
| 17 | resources: docker stats in rust en onder belasting | ok | 30.2s | 480 verzoeken in 2.3s (0 fouten), p95 34.5 ms; api idle 61.3 MiB / load 63.5 MiB, postgres idle 35.2 MiB / load 51.9 MiB, web idle 40.1 MiB / load 45.0 MiB, worker idle 59.2 MiB / load 69.1 MiB |
| 18 | logs: geen geheimen in de logs van MSO-containers | ok | 1.32s | 9 geheimen (beheer-/viewer-wachtwoord, DB-wachtwoord, koppelingsgeheimen) niet gevonden in 43183 bytes log van init/postgres/migrate/api/worker/web; mock-toegangslogs met sleutel (geen MSO): ['mock-sabnzbd'] |
| 19 | logs: 0 foutmeldingen (ERROR/CRITICAL/Traceback/FATAL/PANIC) | ok | 1.31s | geen foutregels in de logs van alle containers; 4 verwachte PostgreSQL-weigering(en) binnen 2 bewuste herstartvenster(s) |

## Resources — rust (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 0.13 | 61.3 | 11 | 768MiB |
| mock-mediaguard | 0.13 | 36.0 | 5 | 256MiB |
| mock-newznab | 0.12 | 34.5 | 5 | 256MiB |
| mock-nntp | 0.01 | 8.0 | 1 | 256MiB |
| mock-prowlarr | 0.1 | 38.1 | 5 | 256MiB |
| mock-qbittorrent | 0.13 | 42.3 | 5 | 256MiB |
| mock-sabnzbd | 0.14 | 35.0 | 5 | 256MiB |
| mock-spotweb | 0.12 | 34.6 | 5 | 256MiB |
| postgres | 0.09 | 35.2 | 10 | 1GiB |
| web | 0.03 | 40.1 | 11 | 512MiB |
| worker | 0.1 | 59.2 | 7 | 768MiB |

## Resources — onder belasting (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 34.5 | 63.5 | 18 | 768MiB |
| mock-mediaguard | 0.09 | 36.0 | 5 | 256MiB |
| mock-newznab | 0.1 | 34.5 | 5 | 256MiB |
| mock-nntp | 0.01 | 8.0 | 1 | 256MiB |
| mock-prowlarr | 0.11 | 38.1 | 5 | 256MiB |
| mock-qbittorrent | 0.11 | 42.3 | 5 | 256MiB |
| mock-sabnzbd | 0.11 | 35.0 | 5 | 256MiB |
| mock-spotweb | 0.1 | 34.6 | 5 | 256MiB |
| postgres | 17.84 | 51.9 | 16 | 1GiB |
| web | 8.22 | 45.0 | 11 | 512MiB |
| worker | 2.9 | 69.1 | 7 | 768MiB |

Belasting: 480 API-verzoeken (8 parallel) + 1 back-up-job in 2.3 s (209.6 req/s, p50 19.7 ms, p95 34.5 ms, 0 fouten).

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
