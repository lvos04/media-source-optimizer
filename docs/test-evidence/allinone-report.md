# Alles-in-één-image — containerproef

S-P3-250 · 19 november 2026 · lokale Docker-proef met testgegevens en een SABnzbd-simulator.

Bronrevisie `7d9d065831d42bd0819a0ac716e34f5e928fbea2`. Met de lokale proefimage
`media-source-optimizer:s-p3-250-a-bouw` waren **16 van 16 scenario’s groen**, zonder overslagen.

CI op deze bronrevisie: job `allinone`, 16 van 16 scenario’s groen in het artefact `allinone-results`. [CI-run 44445669110](https://github.com/lvos04/media-source-optimizer/actions/runs/44445669110)

## Start en rechten

Bij de nulmeting draaide `dumb-init` als PID 1 op UID 0 met `NoNewPrivs=0` en een gevulde bounding set. De bewaker draait zelf als PID 1 en ruimt geadopteerde zombies op. Voor beheerde subprocessen verzamelt hij de exitstatus via hun bestaande procesobject. PostgreSQL start vóór de alleen-lezen schemacontrole. Bij stoppen is de volgorde web, worker, API en PostgreSQL. Na de alleen-lezen schemacontrole wordt de voorbereiding van de applicatiemappen afgerond en wisselt PID 1 naar 568:568. PostgreSQL draait vanaf zijn eerste exec met de verlaagde rechten.

Docker voert de healthcheck in exec-vorm uit. Bij de standaardstart kan Docker dit losse proces kort als root starten. Dat venster vóór de rechtenwissel blijft een restrisico. De healthcheck leegt dan de bounding set, wist de aanvullende groepen, wisselt naar 568:568 en zet `NoNewPrivs=1`. Mislukt een stap of wijkt het gemeten rechtenprofiel af, dan eindigt het proces vóór HTTP met exitcode 1. De GET-controles op `/healthz` van de API en `/login` van de webapp gebruiken loopback, ook als een proxy is ingesteld.

De gewone proeven gebruiken de standaard Docker-capabilities. Bij beide startmodi controleerde de rooktest de volledige boom van PID 1 en de aparte healthcheck via `--controle`. Voor alle gemeten processen waren de vier UID-velden 568, `CapEff`, `CapPrm` en `CapAmb` nul en `NoNewPrivs` 1. `CapBnd` was nul bij root-start en kwam overeen met de standaardset van Docker bij `--user 568:568`.

## Uitkomsten

| Scenario | Resultaat | Details |
|---|---|---|
| 1 root + lege map → gezond, eigenaar 568, processen als 568 | ok | health=healthy; 568:568 /d/postgres, 568:568 /d/secrets, 568:568 /d/backups; proces-uids (excl. PID 1)=[568] |
| 2 setup, login, CSRF, koppeling + verbindingstest | ok | admin 200; zonder CSRF 403; SABnzbd-test healthy v4.3.3 |
| 3 back-up via worker + herstel | ok | job done; 2 back-up(s); herstel 200; sleutel na herstel: ingesteld |
| 4 docker restart → data en login blijven | ok | health=healthy |
| 5 harde kill (SIGKILL) → opnieuw starten werkt | ok | postmaster.pid achtergebleven=True; health=healthy; melding=True |
| 6 netjes stoppen | ok | 0.7 s, exitcode 0 |
| 7 --user 568 op map van 568 → gezond | ok | health=healthy |
| 8 --user 568 op root-map → duidelijke foutmelding | ok | [mso] FOUT: geen schrijfrechten op /appdata/postgres. Geef gebruiker 568 (apps) schrijfrechten op de dataset, of laat de app als root starten zodat MSO dit zelf regelt. |
| 9 geen geheimen in de log | ok | 12613 bytes log gecontroleerd |
| 10 upgrade vanaf 1.5.0-schema → eerst back-up, dan migraties | ok | down rc=0; schema 609/609; pre-upgrade back-ups 0→1; melding=True; health=healthy |
| 11 buildgegevens uit de alles-in-één-image | ok | HTTP 200; overeenkomende velden: version, git_sha, build_time, channel |
| 12 connector en readiness via webpoort | ok | discover 401/200/200; onbekend 404; apply 501/413; readiness 200/503 |
| 13 procesboom en healthcheck bij beide startmodi | ok | root-start: 14 processen incl. PID 1 plus healthcheck; CapBnd=0000000000000000; gebruiker 568: 14 processen incl. PID 1 plus healthcheck; CapBnd=00000000a80425fb |
| 14 healthcheck zonder SETUID | ok | exitcode 1; fout bij rechtenwissel=True |
| 15 rechtenproef weigert overgeslagen wissel en afwijkende rechten | ok | diagnose afgekeurd op Uid, CapEff, CapPrm, CapBnd, NoNewPrivs; afzonderlijk afgekeurd: Uid, CapEff, CapPrm, CapAmb, CapBnd |
| 16 PID 1 ruimt overgenomen zombie op | ok | overgenomen door PID 1; opruimcontrole exitcode 0 |

**NIET GEVALIDEERD (echte NAS)**. Praktijkproef in het standaard Custom App-formulier volgens N-P6-05.

## Herstel S-P3-250

Bronrevisie `2c9c92488daac384ab8afadb73f3513eb05105fa`. De lokale proef met testgegevens en een SABnzbd-simulator doorliep
**16 van 16 scenario’s zonder overslagen**. De CI-job `allinone` op deze bronrevisie doorliep dezelfde
16 scenario’s. [CI-run 44462387172](https://github.com/lvos04/media-source-optimizer/actions/runs/44462387172)

Het ketenrapport `RAPPORT-A-HERSTEL.md` bevat de testcommando’s,
de werkelijke CI-uitkomst en de eindcommit.

Bij start als 568:568 zet en verifieert PID 1 `NoNewPrivs=1` vóór mapvoorbereiding en voordat
`initdb` start. Bij start als root blijft PostgreSQL vóór de alleen-lezen schemacontrole starten.
De bewaker verifieert zijn eigen rechtenwissel na de eigenaarschapsstappen. Bij een opstartfout ruimt hij
gestarte kinderen in omgekeerde volgorde op. De foutpauze van 30 seconden volgt alleen na een geverifieerde
rechtenwissel.

### Herleidbaarheid van de vier afzonderlijke opstartproeven

`RAPPORT-A-HERSTEL.md` noemt de aanroep van het tijdelijke script `containerproef.py` voor de gerapporteerde
basis `fca457106806a46712f7402b73c504cc2c3ebf7d` en het gerapporteerde herstel `2c9c92488daac384ab8afadb73f3513eb05105fa`.
Het oorspronkelijke script, het exacte commando per scenario en de ruwe meetuitvoer per scenario ontbreken.
Daarom tellen deze historische metingen niet mee als bewijs. De 16 hierboven genoemde scenario’s hebben hun eigen CI-run.

| Afzonderlijke lokale opstartproef | Bewijsstatus op basis en herstel |
|---|---|
| Start als 568:568, bij de eerste `initdb` | Niet herleidbaar |
| Start als 568:568 met een niet-schrijfbare map | Niet herleidbaar |
| Start zonder `SETUID` | Niet herleidbaar |
| Laatste rechtenwissel geweigerd bij vijf mappen van 568:568 | Niet herleidbaar |

P-INSTALLATIE staat open. De voormalige TrueNAS-installatiestappen met losse containers zijn uit de
installatiedocumentatie en de hardwarechecklist gehaald. De praktijkproef volgens N-P6-05 volgt na vrijgave.

## Upgrade en terugval S-P3-260

De lokale Docker-proef van 19 november 2026 doorliep **19 van 19 scenario’s zonder overslagen** met
testgegevens en een SABnzbd-simulator. De upgrade- en herstelbron was de openbare 1.5.0-image
`ghcr.io/lvos04/media-source-optimizer@sha256:7ee5cb5e3c37d8ca6e3c8445cd6ef24f8b175e2f3167ccd40541737564866be2`.

| Scenario | Meting |
|---|---|
| 17 Upgrade vanaf 1.5.0 | Schema 501 → 610, alle 25 migraties geregistreerd. In de samengevoegde logstroom stond de archiefbevestiging vóór de eerste migratie. Gebruiker, inlog, ingeschakelde proefkoppeling en wijzigingsvoorstel bleven behouden. Dezelfde geauthenticeerde SABnzbd-wachtrijlezing werkte vóór en na de upgrade. |
| 18 Gedeeltelijk archiefherstel en volledige terugval op 1.5.0 | Het SHA-256-gecontroleerde archief gaf gedeeltelijk herstel op schema 501. De wachtrijlezing werd geweigerd. Uit de gestopte APPDATA-snapshot kwamen vóór de appstart alle tabelinhoud en applicatiebestanden ongewijzigd terug, inclusief de versleutelde kluisrijen en de bijbehorende sleutel. Na de terugval lukten de inlog en dezelfde geauthenticeerde wachtrijlezing. |
| 19 Weigering van een nieuwer schema | De weigerimage kende schema 607 en weigerde schema 610 met de Nederlandse melding. De healthcheck gaf exitcode 1. De logische dump van de complete database en de inhoud en metadata van de applicatiebestanden waren vóór en na de proef gelijk. |

De gepubliceerde 1.5.0-image heeft geen schemaweigering. Daarom bouwt
`tests/stack/build-schema-guard.sh` de weigerimage vanaf S-P3-205-commit
`2512cd761ad30f42a4787935687388e9a3f0d428` met de vastgezette basisimages uit
`5e2c3af7cf01895ea526327ac8b6a21346c6d361`. De proef start de weigerimage op het lokale
image-ID via `MSO_SCHEMA_GUARD_IMAGE`. De databasevergelijking maakt aan beide kanten een dump uit een
wegwerpkopie van de gestopte database. PostgreSQL kan bij een start of stop zijn fysieke controle- en
WAL-bestanden bijwerken.

Bij een onbereikbare 1.5.0-digest meldt de suite de betreffende scenario’s als overgeslagen en geeft
zij exitcode 1. De CI-job `allinone` draait dezelfde suite en publiceert de resultaten per scenario.
De CI-uitkomst op de herstelcommit staat in `RAPPORT-A-HERSTEL.md` van keten S-P3-260.

**NIET GEVALIDEERD (echte NAS).** De Docker-proef simuleerde de APPDATA-snapshot met een kopie van de
gestopte testinstallatie en gebruikte een willekeurig aangemaakte proefsleutel. De ZFS-snapshot en het
formulier worden getoetst in de praktijkproef. Het integrale EX15-bewijs volgt in S-P6-20.
