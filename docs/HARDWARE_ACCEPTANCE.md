# Echte-hardware acceptatiechecklist

Deze checklist beschrijft wat op de **echte TrueNAS SCALE-server** met de **echte** Prowlarr, SABnzbd,
qBittorrent, MediaGuard Ultimate, Usenet-providers en trackers geverifieerd moet worden voordat Media Source
Optimizer (MSO) als productierijp geldt.

> **Status van elk punt: NIET GEVERIFIEERD.**
> Tijdens de ontwikkeling is alleen getest in een Linux-sandbox/CI met Docker en met mock-diensten
> (`apps/mocks/msomocks`, container-stack `tests/stack`). Er is géén TrueNAS-hardware, géén echte
> Prowlarr/SABnzbd/qBittorrent/MediaGuard-installatie en zijn géén echte provider- of trackeraccounts gebruikt.
> Vink een punt pas af nadat je het zelf op je eigen systeem hebt uitgevoerd, en noteer datum, versie en
> eventuele afwijkingen.

> **Let op: hoofdstuk 0–1 beschrijven nog de oude installatie met *Install via YAML* en terminalcommando's.** Dat is
> geen installatiemethode meer (contract §2.3 regel 4). Installeer voor de acceptatie als standaard Custom App volgens
> [TRUENAS_CUSTOM_APP.md](TRUENAS_CUSTOM_APP.md) en sla de YAML-stappen over. Deze checklist wordt in P3-05
> herschreven naar de Custom App (`docs/platform-v2/DELTA_MATRIX.md`).

Legenda kolom *Resultaat*: vul in `OK`, `AFWIJKING: …` of `N.v.t.`.

## 0. Voorbereiding

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 0.1 | Noteer TrueNAS-versie (`System → General`), pool-naam, CPU, RAM. | TrueNAS SCALE 24.10 of nieuwer (Docker-apps met "Install via YAML"). | NIET GEVERIFIEERD |
| 0.2 | Maak een ZFS-snapshot van de datasets van MediaGuard, Prowlarr, SABnzbd en qBittorrent. | Snapshots bestaan; terugvalpunt aanwezig. | NIET GEVERIFIEERD |
| 0.3 | Noteer in de MediaGuard-repo/installatie `git rev-parse HEAD` en `git status --porcelain` (of de image-tags). | Referentie voor criterium "MediaGuard bit-voor-bit ongewijzigd". | NIET GEVERIFIEERD |

## 1. Installatie op TrueNAS (docs/TRUENAS_INSTALL.md)

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 1.1 | Datasets `apps/media-source-optimizer` en `apps/media-source-optimizer-work` aanmaken, quota op WORK, eigenaar 568:568. | `ls -ln /mnt/<pool>/apps` toont `568 568`; `zfs get quota` toont de quota. | NIET GEVERIFIEERD |
| 1.2 | Apps → Discover Apps → ⋮ → Install via YAML, `deploy/truenas/compose.yaml` plakken (paden aangepast). | App wordt "Running"; geen foutmelding in de TrueNAS-UI. | NIET GEVERIFIEERD |
| 1.3 | `sudo docker ps -a --format '{{.Names}} {{.Status}}'` | `init` en `migrate`: `Exited (0)`; `postgres`, `api`, `worker`, `web`: `Up … (healthy)`. | NIET GEVERIFIEERD |
| 1.4 | `ls -ln` op APPDATA na eerste start. | Mappen `postgres/ db/ secrets/ backups/` van 568:568; `db/password` modus `-r--------`. | NIET GEVERIFIEERD |
| 1.5 | Controleer dat `init` op deze dataset-ACL de eigenaar mocht zetten (logs van de init-container). | Geen regel `LET OP: … controleer de ACL`. | NIET GEVERIFIEERD |
| 1.6 | `sudo docker inspect` op elke container: `HostConfig.Privileged=false`, `CapDrop=[ALL]`, `ReadonlyRootfs=true` (behalve init), geen `/var/run/docker.sock` mount, `NetworkMode` ≠ `host`. | Zoals beschreven. | NIET GEVERIFIEERD |
| 1.7 | Vanaf een ander LAN-apparaat `http://<truenas-ip>:38081` openen. | Setupwizard verschijnt. | NIET GEVERIFIEERD |
| 1.8 | Vanaf buiten het LAN (bijv. mobiel netwerk) poort 38081 proberen. | Niet bereikbaar (geen port-forward op de router). | NIET GEVERIFIEERD |

## 2. Eerste start, login, persistentie

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 2.1 | Setupwizard: beheerder aanmaken. | Ingelogd; tweede setup-poging geweigerd. | NIET GEVERIFIEERD |
| 2.2 | Uitloggen en opnieuw inloggen; fout wachtwoord proberen. | Login werkt; fout wachtwoord → melding, na te veel pogingen rate limit. | NIET GEVERIFIEERD |
| 2.3 | App in TrueNAS stoppen en starten. | Login werkt nog; instellingen en koppelingen behouden. | NIET GEVERIFIEERD |
| 2.4 | TrueNAS-host herstarten. | App komt vanzelf op (restart `unless-stopped`), alle containers healthy. | NIET GEVERIFIEERD |
| 2.5 | Achter je reverse proxy (TLS) met `MSO_SECURE_COOKIES=true`. | Login werkt via https; cookie heeft `Secure`; geen mixed-content. | NIET GEVERIFIEERD |

## 3. Prowlarr (echt)

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 3.1 | Koppeling Prowlarr: URL (bijv. `http://<truenas-ip>:9696`) + API-sleutel in de wizard, *Test*. | Status gezond, versie getoond; API-sleutel nergens zichtbaar (UI, logs). | NIET GEVERIFIEERD |
| 3.2 | Indexerlijst, statistieken en indexerstatus uitlezen. | Komen overeen met de Prowlarr-UI. | NIET GEVERIFIEERD |
| 3.3 | MSO als *Generic Newznab* in Prowlarr toevoegen (`http://<truenas-ip>:38081/newznab`, API-pad `/api`, sleutel uit MSO). Prowlarr *Test*. | Test slaagt; caps tonen de vier categorieën (tv, tv-anime, movies, movies-anime). | NIET GEVERIFIEERD |
| 3.4 | Idem *Generic Torznab* (`…/torznab`). | Test slaagt. | NIET GEVERIFIEERD |
| 3.5 | Zoeken vanuit Prowlarr op de MSO-indexer terwijl MSO zelf Prowlarr als bron gebruikt. | Geen zoeklus: MSO weigert de cyclische hop (reason code in MSO-log/trace), Prowlarr-log toont één verzoek per zoekopdracht. | NIET GEVERIFIEERD |
| 3.6 | Downloadlink uit een MSO-resultaat via Prowlarr naar SABnzbd/qBittorrent sturen. | Link wijst naar `MSO_PUBLIC_URL`/`newznab.public_url` (niet naar `http://api:8200`) en werkt. | NIET GEVERIFIEERD |

## 4. SABnzbd en Usenet-providers (echt)

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 4.1 | Koppeling SABnzbd (URL + API-sleutel), *Test*. | Gezond; versie en categorieën zichtbaar. | NIET GEVERIFIEERD |
| 4.2 | Serverlijst en per-server statistieken uitlezen. | Komen overeen met SABnzbd → Status/Servers. | NIET GEVERIFIEERD |
| 4.3 | Prioriteitswijziging als wijzigingsvoorstel: preview, toepassen in beheermodus, terugdraaien. | SABnzbd-config precies zoals voor de wijziging na rollback. | NIET GEVERIFIEERD |
| 4.4 | Provider met verlopen account / foutief wachtwoord (indien beschikbaar). | Duidelijke status (auth/expired), geen wachtwoord in logs. | NIET GEVERIFIEERD |
| 4.5 | Echte NZB naar SABnzbd sturen vanuit MSO. | Komt in de juiste categorie; uitkomst verschijnt later in MSO-statistieken. | NIET GEVERIFIEERD |
| 4.6 | Retentie/beschikbaarheidsinschatting per provider. | Wordt als schatting met dekking/onzekerheid getoond, nooit als gegarandeerde unieke retentie. | NIET GEVERIFIEERD |

## 5. qBittorrent en trackers (echt)

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 5.1 | Koppeling qBittorrent (URL + gebruiker/wachtwoord of API-sleutel bij ≥ 5.2), *Test*. | Gezond; versie en WebAPI-versie zichtbaar. | NIET GEVERIFIEERD |
| 5.2 | Publieke én private torrents bekijken. | Private torrents gemarkeerd; MSO stelt nooit publieke trackers/DHT/PeX voor op private torrents. | NIET GEVERIFIEERD |
| 5.3 | Snelheids-/seedingvoorstel toepassen en terugdraaien. | Waarden in qBittorrent exact hersteld; geen torrent verwijderd/verplaatst; ratio/seed-time-regels van de tracker niet geschonden. | NIET GEVERIFIEERD |
| 5.4 | Tracker met fout (bijv. tijdelijk onbereikbaar). | Tracker-status zichtbaar; geen automatische ingreep die dataverlies veroorzaakt. | NIET GEVERIFIEERD |
| 5.5 | Schijfdruk (vrije ruimte onder drempel). | Waarschuwing; geen bestanden verwijderd. | NIET GEVERIFIEERD |

## 6. MediaGuard Ultimate (echt)

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 6.1 | Koppeling MediaGuard (bij voorkeur een gebruiker met rol *operator*), *Test*. | Versie/capabilities gedetecteerd. | NIET GEVERIFIEERD |
| 6.2 | MediaGuard stoppen; MSO-UI bekijken. | MSO meldt MediaGuard offline en werkt zelfstandig verder; geen dubbele downloads. | NIET GEVERIFIEERD |
| 6.3 | Ontbrekende items (wanted) uitlezen en één zoekopdracht via MSO laten aanvragen. | Alleen `POST /api/v1/wanted/{id}/search` wordt aangeroepen (MediaGuard-audit/log); quota gerespecteerd. | NIET GEVERIFIEERD |
| 6.4 | Na alle tests: MediaGuard-repo/installatie vergelijken met stap 0.3. | `git status` schoon, zelfde HEAD/images, geen gewijzigde config/database door MSO. | NIET GEVERIFIEERD |

## 7. Back-up, herstel, update en rollback

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 7.1 | Back-up maken in de UI. | Bestand `APPDATA/backups/mso-*.dump` + `.sha256`. | NIET GEVERIFIEERD |
| 7.2 | Instelling wijzigen, daarna de back-up herstellen. | Wijziging teruggedraaid; opnieuw inloggen nodig; koppelingsgeheimen intact. | NIET GEVERIFIEERD |
| 7.3 | Een kopie van een back-up beschadigen (bytes wijzigen) en herstel proberen. | Herstel geweigerd ("Checksum klopt niet"), database ongewijzigd. | NIET GEVERIFIEERD |
| 7.4 | Update naar een nieuwere image-tag volgens de handleiding. | `migrate` exit 0, nieuwe versie in Systeem. | NIET GEVERIFIEERD |
| 7.5 | Rollback met `mso migrate --down <versie>` en oude tag. | Oude versie start, data bruikbaar. | NIET GEVERIFIEERD |
| 7.6 | ZFS-snapshot-rollback van APPDATA (app gestopt). | App start op de toestand van de snapshot. | NIET GEVERIFIEERD |
| 7.7 | Worker-container hard stoppen (`docker kill`) tijdens een lopende taak, daarna starten. | Taak wordt na maximaal ~2 minuten (lease) hervat of opnieuw uitgevoerd, niet dubbel. | NIET GEVERIFIEERD |

## 8. Resources en opslag

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 8.1 | `sudo docker stats --no-stream` in rust en tijdens zoekacties. | Binnen de limieten uit compose.yaml; vergelijk met de sandbox-meting in docs/OPERATIONS.md. | NIET GEVERIFIEERD |
| 8.2 | WORK-quota bereiken (bijv. tijdelijk kleine quota). | MSO meldt schijfdruk in Systeem; geen crash-loop; andere apps onaangetast. | NIET GEVERIFIEERD |
| 8.3 | Logrotatie: `docker inspect … LogConfig`. | json-file, max 20 MB × 5 per container. | NIET GEVERIFIEERD |

## 9. UI op echte apparaten

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 9.1 | Desktop en telefoon, licht en donker thema. | Leesbaar, geen horizontale scroll, toetsenbordbediening werkt. | NIET GEVERIFIEERD |
| 9.2 | Schermopnamen maken van elk hoofdscherm. | Toevoegen aan TEST_RESULTS.md. | NIET GEVERIFIEERD |

## 10. Nieuw in 1.6.0

1.6.0 is getest in een Linux/Docker-sandbox met mock-diensten; het SABnzbd-gedrag is nagekeken in de broncode van
SABnzbd 5.1.3. Deze punten laten zien of het op jouw installatie net zo werkt. Doe ze in de eerste dagen na de update.

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 10.1 | Update van 1.5.0 naar 1.6.0 (Custom App: Edit → Tag `1.6.0` → Update). Bekijk daarna de app-log en in MSO de pagina Back-up & updates (kaart MSO-core en de back-uplijst). | Log: `back-up vóór upgrade gemaakt: …-pre-upgrade.dump (…; schema 501 → 607)`. De back-uplijst toont een nieuwe "Vóór upgrade"; Schemaversie 607; audit `backup.pre_upgrade`. App start gewoon. | NIET GEVERIFIEERD |
| 10.2 | Usenet-providers → Serverprioriteit: kolom "Artikelen gevonden" voor Eweka. Vergelijk met SABnzbd → Config → Servers, regel 'Article availability' bij die server. | Eweka rond 99 % (was "0,4 %"), ongeveer hetzelfde percentage als SABnzbd daar toont (SABnzbd rekent over de periode die je daar kiest). | NIET GEVERIFIEERD |
| 10.3 | Downloads → Usenet, bovenaan de historie. | Regel "Laatst opnieuw beoordeeld …: X van Y kregen een andere oorzaak" (automatisch na de upgrade); audit `usenet.reclassified`. | NIET GEVERIFIEERD |
| 10.4 | Kies 3–5 echt mislukte downloads. Vergelijk per download de regel in Downloads → Usenet met de details in SABnzbd → Geschiedenis (het verwerkingslog per fase). | Dezelfde feiten: "Data compleet · uitpakken mislukt: …" of "N artikelen ontbraken", met het juiste probleem. Bij complete data géén kapotte server als oorzaak. Geen volledige paden of setnamen in MSO. Werkt ook als SABnzbd in het Nederlands staat. | NIET GEVERIFIEERD |
| 10.5 | Komt dezelfde uitpakfout door SABnzbd of de opslag vaker voor (bijv. schijf vol, geen rechten)? | Eén melding "SABnzbd kan downloads niet uitpakken" in "Wacht op jou" met de regel en voorbeelden; na het oplossen verdwijnt hij na 3 rustige dagen. Komt het niet voor: `N.v.t.` | NIET GEVERIFIEERD |
| 10.6 | Usenet-providers → Serverprioriteit → **Testen** bij elke server, ook bij de servers die kapot zijn (fout wachtwoord, verlopen account zoals xsnews). Druk daarna in SABnzbd zelf op 'Test server' bij dezelfde servers. | Werkende server: melding "…: SABnzbd kan inloggen" (bij de server daarna "Test geslaagd"). Fout wachtwoord: "Inloggen geweigerd" met de tekst van de provider (bijv. `502 Authentication Failed`). Verlopen: "Account verlopen". Zelfde uitkomst als in SABnzbd. Geen gebruikersnaam of wachtwoord in de UI, de audit of de logs (`sudo docker logs <container> 2>&1 \| grep -i <gebruikersnaam>` geeft niets). | NIET GEVERIFIEERD |
| 10.7 | In Autopilot: Usenet-providers → Autopilot bij een server die MSO uitzette. | "Volgende test" binnen 6 uur (verlopen: binnen een week); geen blinde proef-aanzet meer. Repareer je het account en klik je **Testen**, dan gaat de server weer aan; "werkt weer" pas nadat SABnzbd hem gebruikte. | NIET GEVERIFIEERD |
| 10.8 | Vul in SABnzbd (Config → Servers) bij één server een vervaldatum over ongeveer 10 dagen in; laat de verlengdatum van die dienst onder Kosten leeg. Haal de datum na de controle weer weg. | Herinnering in "Wacht op jou" ("verloopt over 10 dagen"); onder Kosten staat de verlengdatum ingevuld. Een eigen datum onder Kosten blijft staan. | NIET GEVERIFIEERD |
| 10.9 | Usenet-providers → SABnzbd-categorieën met MediaGuard gekoppeld. Vergelijk met MediaGuard (Inrichting → Downloadclients) en SABnzbd (Config → Categories). Bekijk ook MediaGuard-koppeling → Compatibiliteit. | Het oordeel (a: SABnzbd kent de categorie niet / b: geen categorie / c: andere bron) klopt met wat je daar ziet. De vier leesroutes staan als aanwezig. De MediaGuard-log toont voor instanties, downloadclients en inrichting alleen GET-verzoeken van MSO (de enige schrijfacties van MSO blijven herzoeken en, binnen een goedgekeurde opruiming, de blokkeerlijst). Bij (a): één voorstel in "Wacht op jou"; na goedkeuren staat de categorie met een eigen map in SABnzbd en daalt het aantal '*'-downloads per dag. | NIET GEVERIFIEERD |
| 10.10 | Overzicht → doelenkaart. Vergelijk met: Torrentclient (verdachte torrents), SABnzbd (servers die aanstaan maar fouten geven), je eigen telling van mislukte downloads in 7 dagen. | Getallen kloppen; "dag X van 28" past bij de dag dat Autopilot voor het eerst aanging; uitgezette servers staan apart; dagen volgen je eigen tijdzone. | NIET GEVERIFIEERD |
| 10.11 | Overzicht → Wacht op jou en de topbalk (ook op de telefoon). | Hetzelfde getal overal; het opruimvoorstel voor de nep-downloads staat er één keer. | NIET GEVERIFIEERD |
| 10.12 | Automatisering → Voetafdruk. Zet daarna in qBittorrent één waarde die MSO had ingesteld zelf terug, wacht een uur (of **Nu controleren**). Zet hem daarna weer zoals je wilt. | De lijst klopt met de echte waarden (origineel / MSO / nu). De teruggezette waarde staat op "Buiten MSO gewijzigd", er is één melding ter info, en Autopilot zet hem niet opnieuw. | NIET GEVERIFIEERD |
| 10.13 | Stop qBittorrent in TrueNAS langer dan 15 minuten en start hem weer. | Overzicht → Koppelingen: "qBittorrent onbereikbaar sinds …" in plaats van een stapel mislukte taken; na de start binnen een kwartier weer gezond (zolang hij weg is, probeert MSO het elk kwartier), daarna haalt MSO meteen alles bij. Geen melding in "Wacht op jou" (die komt pas na 24 uur). | NIET GEVERIFIEERD |
| 10.14 | Na 24 uur: Systeem → Onderhoud en Systeem → Database. | Een geslaagde opruimronde, termijnen zoals ingesteld, databasegrootte zichtbaar. Het auditlogboek is niet ingekort. | NIET GEVERIFIEERD |
| 10.15 | Zoeken op een titel waarvan nep-releases (met `.exe` in de naam) bestaan. | Rode badge "Nep-release (uitvoerbaar bestand)", onderaan, knop Verstuur uitgeschakeld. Komt het niet voor: `N.v.t.` | NIET GEVERIFIEERD |
| 10.16 | (Optioneel, alleen als je terug wilt) Terug naar 1.5.0 volgens `docs/MIGRATION.md` → *Terug naar 1.5.0*. | `mso migrate --down 501` meldt de teruggedraaide migraties; 1.5.0 start met schemaversie 501. | NIET GEVERIFIEERD |
