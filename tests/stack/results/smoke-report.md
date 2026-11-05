# MSO container-stack smoke test — 2026-11-05T12:00:44Z

- Resultaat: **19 ok, 0 mislukt, 0 overgeslagen**
- Basis-URL: `http://127.0.0.1:48081` (via web-proxy); MSO_FEATURES: `(alle)`
- Host: Linux-6.18.44-fc-v236-x86_64-with-glibc2.39, 4 CPU's (sandbox/CI — géén TrueNAS-hardware)

| # | Check | Status | Duur | Detail |
|---|---|---|---|---|
| 1 | stack: alle containers gezond, init + migrate exit 0 | ok | 11.54s | api=running/healthy/0, init=exited/-/0, migrate=exited/-/0, mock-mediaguard=running/-/0, mock-newznab=running/-/0, mock-nntp=running/-/0, mock-prowlarr=running/-/0, mock-qbittorrent=running/-/0, mock-sabnzbd=running/-/0, mock-spotweb=running/-/0, postgres=running/healthy/0, web=running/healthy/0, worker=running/healthy/0 |
| 2 | web: UI en same-origin proxy naar core | ok | 0.17s | /login 200, /api/v1 via proxy 200, /newznab/api?t=caps → 200, /torznab/api?t=caps → 200 |
| 3 | setup: eerste beheerder aanmaken, tweede keer geweigerd | ok | 0.13s | beheerder 'stackadmin' aangemaakt; tweede setup → 409 |
| 4 | auth: uitloggen, foutief wachtwoord 401, inloggen, /auth/me | ok | 0.28s | logout → me 401; fout wachtwoord 401; login → me admin |
| 5 | security: CSRF-token verplicht bij mutaties | ok | 0.04s | zonder token 403, fout token 403, met token 200 |
| 6 | security: rollen (viewer mag geen config/gebruikers wijzigen) | ok | 0.33s | viewer: lezen 200; settings/users/backups 403; anoniem 401 |
| 7 | koppelingen: elke connector naar zijn mock + verbindingstest | ok | 0.95s | mediaguard: healthy; prowlarr: degraded; qbittorrent: healthy; sabnzbd: healthy |
| 8 | security: SSRF-weigeringen (metadata, schema, credentials in URL) | ok | 0.09s | 6 onveilige URL's → 422 (connector mediaguard); test naar 169.254.169.254 geweigerd |
| 9 | API: lees-endpoints en OpenAPI | ok | 0.41s | 11 endpoints 200, OpenAPI 158 paden, schema v501, health=degraded |
| 10 | back-up: maken via worker-job, checksum, bestand aanwezig | ok | 1.04s | mso-20261105T115900Z-manual.dump (137079 B), zonder geheimen |
| 11 | herstel: restore zet DB terug, sessies vervallen, koppelingsgeheimen blijven | ok | 1.22s | hersteld mso-20261105T115900Z-manual.dump (vangnet mso-20261105T115901Z-pre-restore.dump); onbekende back-up → 422 |
| 12 | herstel: beschadigde en ontbrekende back-up geweigerd, database ongewijzigd | ok | 1.36s | beschadigd → 422 (Checksum klopt niet: back-up is beschadigd); ontbrekend → 422 (Back-upbestand ontbreekt) |
| 13 | jobs: onderbroken job (verlopen lease) wordt na worker-start hervat | ok | 5.54s | job 9 running(crash) → done; samenvatting: ' · hervat na herstart' |
| 14 | jobs: worker-proces SIGKILL → restart policy herstart, worker weer gezond en functioneel | ok | 8.32s | pid 7 gedood; RestartCount 0→1; nieuwe job done |
| 15 | persistentie: docker compose restart (alle services) — data en login blijven | ok | 17.63s | 2 gebruikers, 4 koppelingen, 6 back-ups ongewijzigd |
| 16 | persistentie: docker compose down + up (containers opnieuw aangemaakt) | ok | 34.95s | gebruikers, koppelingen (incl. versleutelde geheimen) en back-ups intact; login werkt |
| 17 | resources: docker stats in rust en onder belasting | ok | 30.32s | 480 verzoeken in 3.0s (0 fouten), p95 84.4 ms; api idle 59.8 MiB / load 61.7 MiB, postgres idle 32.2 MiB / load 49.5 MiB, web idle 36.8 MiB / load 43.7 MiB, worker idle 57.4 MiB / load 57.5 MiB |
| 18 | logs: geen geheimen in de logs van MSO-containers | ok | 1.87s | 9 geheimen (beheer-/viewer-wachtwoord, DB-wachtwoord, koppelingsgeheimen) niet gevonden in 23339 bytes log van init/postgres/migrate/api/worker/web; mock-toegangslogs met sleutel (geen MSO): ['mock-sabnzbd'] |
| 19 | logs: 0 foutmeldingen (ERROR/CRITICAL/Traceback/FATAL/PANIC) | ok | 1.86s | geen foutregels in de logs van alle containers |

## Resources — rust (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 0.21 | 59.8 | 11 | 768MiB |
| mock-mediaguard | 0.2 | 35.7 | 5 | 256MiB |
| mock-newznab | 0.18 | 34.1 | 5 | 256MiB |
| mock-nntp | 0.02 | 7.8 | 1 | 256MiB |
| mock-prowlarr | 0.16 | 38.0 | 5 | 256MiB |
| mock-qbittorrent | 0.19 | 42.3 | 5 | 256MiB |
| mock-sabnzbd | 0.21 | 35.2 | 5 | 256MiB |
| mock-spotweb | 0.19 | 34.4 | 5 | 256MiB |
| postgres | 2.99 | 32.2 | 10 | 1GiB |
| web | 0.06 | 36.8 | 11 | 512MiB |
| worker | 5.62 | 57.4 | 7 | 768MiB |

## Resources — onder belasting (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 117.88 | 61.7 | 18 | 768MiB |
| mock-mediaguard | 0.16 | 35.7 | 5 | 256MiB |
| mock-newznab | 0.17 | 34.1 | 5 | 256MiB |
| mock-nntp | 0.02 | 7.8 | 1 | 256MiB |
| mock-prowlarr | 0.16 | 38.0 | 5 | 256MiB |
| mock-qbittorrent | 0.19 | 42.3 | 5 | 256MiB |
| mock-sabnzbd | 0.21 | 35.2 | 5 | 256MiB |
| mock-spotweb | 0.16 | 34.4 | 5 | 256MiB |
| postgres | 23.71 | 49.5 | 16 | 1GiB |
| web | 21.4 | 43.7 | 17 | 512MiB |
| worker | 0.09 | 57.5 | 7 | 768MiB |

Belasting: 480 API-verzoeken (8 parallel) + 1 back-up-job in 3.0 s (162.1 req/s, p50 42.6 ms, p95 84.4 ms, 0 fouten).

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
