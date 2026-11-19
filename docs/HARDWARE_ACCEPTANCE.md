# Echte-hardware acceptatiechecklist

> **P-INSTALLATIE staat open.** De installatie en alle acceptatiestappen op TrueNAS in dit document
> niet uitvoeren tot de poort is gehaald. Na vrijgave geldt
> [TRUENAS_CUSTOM_APP.md](TRUENAS_CUSTOM_APP.md) voor installatie via het standaard Custom App-formulier.

Deze checklist beschrijft wat op TrueNAS SCALE met Prowlarr, SABnzbd, qBittorrent, MediaGuard Ultimate,
Usenet-providers en trackers geverifieerd moet worden voordat Media Source Optimizer (MSO) als productierijp geldt.

> **Status van elk punt: NIET GEVERIFIEERD.**
> Tijdens de ontwikkeling is getest in een Linux/Docker-sandbox en op CI met simulatiediensten
> (`apps/mocks/msomocks`, container-stack `tests/stack`). Noteer na vrijgave per uitgevoerde
> praktijkproef de datum, versie en eventuele afwijkingen.

Legenda kolom *Resultaat*: vul in `OK`, `AFWIJKING: …` of `N.v.t.`.

## 0. Voorbereiding

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 0.1 | Noteer de TrueNAS-versie (`System → General`), CPU en RAM. | TrueNAS SCALE 24.10 of nieuwer met het standaard Custom App-formulier. | NIET GEVERIFIEERD |
| 0.2 | Leg de bestaande versie en digest, formulierwaarden en APPDATA-dataset vast. Verifieer bij een upgrade de snapshot van de gestopte app. | Herstelbare APPDATA-snapshot en exacte vorige imagepin beschikbaar. | NIET GEVERIFIEERD |
| 0.3 | Noteer in de MediaGuard-repo/installatie `git rev-parse HEAD` en `git status --porcelain` (of de image-tags). | Referentie voor criterium "MediaGuard bit-voor-bit ongewijzigd". | NIET GEVERIFIEERD |

## 1. Installatie op TrueNAS na vrijgave

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 1.1 | Controleer de opslagvelden vóór installatie. | Eén dataset is gekoppeld aan `/appdata`. | NIET GEVERIFIEERD |
| 1.2 | Volg na vrijgave het [standaard Custom App-formulier](../deploy/truenas/CUSTOM_APP.md). | Eén alles-in-één-image draait met de vrijgegeven tag en gecontroleerde digest. | NIET GEVERIFIEERD |
| 1.3 | Bekijk de appstatus en het opstartlog. | App meldt Running en vervolgens Healthy. | NIET GEVERIFIEERD |
| 1.4 | Controleer de mappen die MSO heeft aangemaakt. | `postgres/`, `secrets/`, `backups/` en `work/` zijn van 568:568. | NIET GEVERIFIEERD |
| 1.5 | Controleer de app-log op fouten bij de rechtenwissel en de schrijftest. | De rechtenwissel en de schrijftest zijn afgerond zonder foutmelding. | NIET GEVERIFIEERD |
| 1.6 | Controleer de instellingen van de Custom App. | Privileged en Host Network staan uit. De enige gepubliceerde poort is de webpoort. | NIET GEVERIFIEERD |
| 1.7 | Open de app vanaf een ander LAN-apparaat. | Setupwizard verschijnt. | NIET GEVERIFIEERD |
| 1.8 | Controleer bereikbaarheid vanaf buiten het LAN. | De app blijft onbereikbaar vanaf internet. | NIET GEVERIFIEERD |

## 2. Eerste start, login, persistentie

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 2.1 | Setupwizard: beheerder aanmaken. | Ingelogd; tweede setup-poging geweigerd. | NIET GEVERIFIEERD |
| 2.2 | Uitloggen en opnieuw inloggen; fout wachtwoord proberen. | Login werkt; fout wachtwoord → melding, na te veel pogingen rate limit. | NIET GEVERIFIEERD |
| 2.3 | App in TrueNAS stoppen en starten. | Login werkt nog; instellingen en koppelingen behouden. | NIET GEVERIFIEERD |
| 2.4 | TrueNAS-host herstarten. | De Custom App komt vanzelf op en meldt Healthy. | NIET GEVERIFIEERD |
| 2.5 | Achter je reverse proxy (TLS) met `MSO_SECURE_COOKIES=true`. | Login werkt via https; cookie heeft `Secure`; geen mixed-content. | NIET GEVERIFIEERD |

## 3. Prowlarr (echt)

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 3.1 | Koppeling Prowlarr: bevestigde koppelings-URL + API-sleutel in de wizard, *Test*. | Status gezond, versie getoond; API-sleutel nergens zichtbaar (UI, logs). | NIET GEVERIFIEERD |
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
| 7.4 | Voer in de proefinstallatie de [upgrade via het Custom App-formulier](MIGRATION.md#updaten-via-truenas) uit na een herstelbare volledige APPDATA-snapshot. | De log toont de back-up vóór de migratie en daarna de afgeronde migratie. Gebruikers, instellingen, audit en koppelingen zijn behouden. | NIET GEVERIFIEERD |
| 7.5 | Bewaar de bijgewerkte APPDATA-toestand apart. Zet via de TrueNAS-interface de volledige snapshot van vóór de upgrade terug en kies daarna de vorige tag met digest. | De vorige image start op het bijpassende schema en de vooraf gecontroleerde gegevens zijn bruikbaar. | NIET GEVERIFIEERD |
| 7.6 | Controleer na de terugval de aanmelding, koppelingsstatus, instellingen, audit en taken. | De versleutelde kluis en sleutel horen bij elkaar. De voor-en-na-vergelijking toont de verwachte stand. | NIET GEVERIFIEERD |
| 7.7 | Onderbreek in de testinstallatie het workerproces tijdens een lopende taak en wacht tot de bewaker de worker herstart. | Taak wordt na maximaal ~2 minuten (lease) hervat of opnieuw uitgevoerd, niet dubbel. | NIET GEVERIFIEERD |

## 8. Resources en opslag

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 8.1 | Bekijk het appverbruik in TrueNAS in rust en tijdens zoekacties. | Binnen de limieten uit het Custom App-formulier; vergelijk met de sandbox-meting in docs/OPERATIONS.md. | NIET GEVERIFIEERD |
| 8.2 | Benader het APPDATA-quotum in de geïsoleerde proefinstallatie. | MSO meldt schijfdruk in Systeem; geen crash-loop; andere apps onaangetast. | NIET GEVERIFIEERD |
| 8.3 | Logrotatie: `docker inspect … LogConfig`. | De ingestelde logrotatie begrenst het ruimtegebruik. | NIET GEVERIFIEERD |

## 9. UI op echte apparaten

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 9.1 | Desktop en telefoon, licht en donker thema. | Leesbaar, geen horizontale scroll, toetsenbordbediening werkt. | NIET GEVERIFIEERD |
| 9.2 | Schermopnamen maken van elk hoofdscherm. | Toevoegen aan TEST_RESULTS.md. | NIET GEVERIFIEERD |

## 10. Nieuw in 1.6.0

1.6.0 is getest in een Linux/Docker-sandbox met mock-diensten; het SABnzbd-gedrag is nagekeken in de broncode van
SABnzbd 5.1.3. Deze punten laten zien of het op jouw installatie net zo werkt. Deze controles vallen onder de vrijgave bovenaan dit document. Gebruik voor dit historische versiepad een afzonderlijke proefinstallatie.

| # | Stap | Verwacht resultaat | Resultaat |
|---|---|---|---|
| 10.1 | Update van 1.5.0 naar 1.6.0 (na volledige APPDATA-snapshot, met de bijbehorende imagepin in het Custom App-formulier). Bekijk daarna de app-log en in MSO de pagina Back-up & updates (kaart MSO-core en de back-uplijst). | Log: `back-up vóór upgrade gemaakt: …-pre-upgrade.dump (…; schema 501 → 607)`. De back-uplijst toont een nieuwe "Vóór upgrade"; Schemaversie 607; audit `backup.pre_upgrade`. App start gewoon. | NIET GEVERIFIEERD |
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
| 10.16 | (Optioneel, alleen als je terug wilt) Terug naar 1.5.0 volgens `docs/MIGRATION.md` → *Terug naar 1.5.0*. | Na APPDATA-rollback en keuze van de vorige tag met digest start 1.5.0 met schemaversie 501. | NIET GEVERIFIEERD |
