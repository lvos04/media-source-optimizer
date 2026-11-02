# MSO container-stack smoke test — 2026-11-02T15:22:28Z

- Resultaat: **19 ok, 0 mislukt, 0 overgeslagen**
- Basis-URL: `http://127.0.0.1:38081` (via web-proxy); MSO_FEATURES: `(alle)`
- Host: Linux-6.18.44-fc-v185-x86_64-with-glibc2.39, 4 CPU's (sandbox/CI — géén TrueNAS-hardware)

| # | Check | Status | Duur | Detail |
|---|---|---|---|---|
| 1 | stack: alle containers gezond, init + migrate exit 0 | ok | 7.52s | api=running/healthy/0, init=exited/-/0, migrate=exited/-/0, mock-mediaguard=running/-/0, mock-newznab=running/-/0, mock-nntp=running/-/0, mock-prowlarr=running/-/0, mock-qbittorrent=running/-/0, mock-sabnzbd=running/-/0, mock-spotweb=running/-/0, postgres=running/healthy/0, web=running/healthy/0, worker=running/healthy/0 |
| 2 | web: UI en same-origin proxy naar core | ok | 0.23s | /login 200, /api/v1 via proxy 200, /newznab/api?t=caps → 200, /torznab/api?t=caps → 200 |
| 3 | setup: eerste beheerder aanmaken, tweede keer geweigerd | ok | 0.21s | beheerder 'stackadmin' aangemaakt; tweede setup → 409 |
| 4 | auth: uitloggen, foutief wachtwoord 401, inloggen, /auth/me | ok | 0.34s | logout → me 401; fout wachtwoord 401; login → me admin |
| 5 | security: CSRF-token verplicht bij mutaties | ok | 0.06s | zonder token 403, fout token 403, met token 200 |
| 6 | security: rollen (viewer mag geen config/gebruikers wijzigen) | ok | 0.38s | viewer: lezen 200; settings/users/backups 403; anoniem 401 |
| 7 | koppelingen: elke connector naar zijn mock + verbindingstest | ok | 1.48s | mediaguard: healthy; prowlarr: degraded; qbittorrent: healthy; sabnzbd: healthy |
| 8 | security: SSRF-weigeringen (metadata, schema, credentials in URL) | ok | 0.17s | 6 onveilige URL's → 422 (connector mediaguard); test naar 169.254.169.254 geweigerd |
| 9 | API: lees-endpoints en OpenAPI | ok | 0.55s | 11 endpoints 200, OpenAPI 147 paden, schema v500, health=degraded |
| 10 | back-up: maken via worker-job, checksum, bestand aanwezig | ok | 1.05s | mso-20261102T152034Z-manual.dump (124816 B), zonder geheimen |
| 11 | herstel: restore zet DB terug, sessies vervallen, koppelingsgeheimen blijven | ok | 1.86s | hersteld mso-20261102T152034Z-manual.dump (vangnet mso-20261102T152035Z-pre-restore.dump); onbekende back-up → 422 |
| 12 | herstel: beschadigde en ontbrekende back-up geweigerd, database ongewijzigd | ok | 2.56s | beschadigd → 422 (Checksum klopt niet: back-up is beschadigd); ontbrekend → 422 (Back-upbestand ontbreekt) |
| 13 | jobs: onderbroken job (verlopen lease) wordt na worker-start hervat | ok | 6.06s | job 15 running(crash) → done; samenvatting: ' · hervat na herstart' |
| 14 | jobs: worker-proces SIGKILL → restart policy herstart, worker weer gezond en functioneel | ok | 9.22s | pid 7 gedood; RestartCount 0→1; nieuwe job done |
| 15 | persistentie: docker compose restart (alle services) — data en login blijven | ok | 18.83s | 2 gebruikers, 4 koppelingen, 6 back-ups ongewijzigd |
| 16 | persistentie: docker compose down + up (containers opnieuw aangemaakt) | ok | 38.48s | gebruikers, koppelingen (incl. versleutelde geheimen) en back-ups intact; login werkt |
| 17 | resources: docker stats in rust en onder belasting | ok | 30.42s | 480 verzoeken in 3.3s (0 fouten), p95 90.5 ms; api idle 60.6 MiB / load 61.0 MiB, postgres idle 30.5 MiB / load 48.0 MiB, web idle 37.3 MiB / load 42.4 MiB, worker idle 54.9 MiB / load 55.2 MiB |
| 18 | logs: geen geheimen in de logs van MSO-containers | ok | 2.59s | 9 geheimen (beheer-/viewer-wachtwoord, DB-wachtwoord, koppelingsgeheimen) niet gevonden in 20424 bytes log van init/postgres/migrate/api/worker/web; mock-toegangslogs met sleutel (geen MSO): ['mock-sabnzbd'] |
| 19 | logs: 0 foutmeldingen (ERROR/CRITICAL/Traceback/FATAL/PANIC) | ok | 2.65s | geen foutregels in de logs van alle containers |

## Resources — rust (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 1.39 | 60.6 | 17 | 768MiB |
| mock-mediaguard | 0.16 | 35.9 | 5 | 256MiB |
| mock-newznab | 0.17 | 34.6 | 5 | 256MiB |
| mock-nntp | 0.02 | 7.8 | 1 | 256MiB |
| mock-prowlarr | 0.19 | 38.2 | 5 | 256MiB |
| mock-qbittorrent | 0.18 | 41.7 | 5 | 256MiB |
| mock-sabnzbd | 0.21 | 34.7 | 5 | 256MiB |
| mock-spotweb | 0.19 | 34.7 | 5 | 256MiB |
| postgres | 3.65 | 30.5 | 10 | 1GiB |
| web | 0.0 | 37.3 | 11 | 512MiB |
| worker | 0.11 | 54.9 | 7 | 768MiB |

## Resources — onder belasting (max van samples)

| Container | CPU % max | Geheugen max (MiB) | PIDs max | Limiet |
|---|---|---|---|---|
| api | 93.03 | 61.0 | 18 | 768MiB |
| mock-mediaguard | 0.16 | 35.9 | 5 | 256MiB |
| mock-newznab | 0.16 | 34.6 | 5 | 256MiB |
| mock-nntp | 0.02 | 7.8 | 1 | 256MiB |
| mock-prowlarr | 0.15 | 38.0 | 5 | 256MiB |
| mock-qbittorrent | 0.18 | 41.5 | 5 | 256MiB |
| mock-sabnzbd | 0.17 | 34.7 | 5 | 256MiB |
| mock-spotweb | 0.16 | 34.4 | 5 | 256MiB |
| postgres | 30.74 | 48.0 | 16 | 1GiB |
| web | 37.1 | 42.4 | 11 | 512MiB |
| worker | 0.15 | 55.2 | 7 | 768MiB |

Belasting: 480 API-verzoeken (8 parallel) + 1 back-up-job in 3.3 s (147.5 req/s, p50 47.7 ms, p95 90.5 ms, 0 fouten).

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
