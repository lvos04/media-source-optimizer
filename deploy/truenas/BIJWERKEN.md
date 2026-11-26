# Een bestaande Media Source Optimizer bijwerken naar 1.6.1

**NIET GEVALIDEERD (echte NAS).** Begin met het herkennen van de draaiende installatie. Wacht met wijzigen tot 1.6.1 is vrijgegeven voor installatie en bij de GitHub Release `v1.6.1` de bijlage **digests.json** staat. Bewaar de oude installatie en haar herstelpunt tot de controles na de update kloppen.

## 1. Lees af wat er draait

1. Selecteer MSO bij **Apps → Installed**. Noteer de appnaam. Kijk bij **Workloads** hoeveel containers er zijn, welke images ze gebruiken en welke webpoort is toegewezen. Open per container **Volume Mounts**. Noteer per koppeling het type opslag, het hostpad of volume, het pad binnen de container en of de koppeling schrijfbaar is. Neem ook onderliggende datasets en afzonderlijke werk-, back-up- of databasedatasets mee.
2. Open **Edit** zonder op te slaan. Een standaard Custom App toont **Image Configuration → Repository** en **Tag**. Noteer beide, de huidige registerdigest, poorten en afwijkende formulierinstellingen. Een teksteditor bij **Edit** wijst op een YAML-app. Sluit die zonder te wijzigen. Gebruik **Workloads** voor de inventarisatie. Bij **Apps → Configuration → Manage Container Images** kun je de lokaal aanwezige imagetags terugzoeken. Een Image ID is geen registerdigest. Bewaar de vorige versie met de bijbehorende verifieerbare registerdigest voor herstel. [TrueNAS-appschermen](https://www.truenas.com/docs/scale/24.10/scaleuireference/apps/)
3. Meld je aan bij de bestaande MSO. Noteer de software- en schemaversie onder **Systeem** of **Back-up & updates**, de bedrijfsmodus, een herkenbare instelling, een bestaande download of auditregel en de namen en status van de ingestelde downloaders. Gebruik herkenningspunten zonder wachtwoorden of sleutels over te nemen.

| Wat zie je? | Vervolg |
|---|---|
| Standaard Custom App, één bekende MSO-alles-in-één-image, de volledige bestaande opslag op `/appdata` en de interne PostgreSQL-database | Volg stap 2, 3 en 4. Bij een afwijkende repository moet vaststaan dat de draaiende image dezelfde MSO-opslagindeling gebruikt. |
| Aparte core-, web- en PostgreSQL-containers, of een YAML-app | Volg stap 2 om de gegevens veilig te stellen. Lees daarna **Overstap vanuit meerdere containers of YAML**. |
| Andere app, externe database, onbekende herkomst, onduidelijke mount of ontbrekende oude imagepin | **STOP.** Laat de huidige app en haar opslag intact totdat de database, opslagindeling en oude image eenduidig zijn vastgesteld. |

## 2. Maak twee herstelpunten

1. Kies een onderhoudsvenster. Zet MSO in **Alleen-lezen**, pauzeer automatische wijzigingen en laat lopende wijzigingen afronden.
2. Kies als bestaande beheerder in **Systeem** of **Back-up & updates → Back-ups → Back-up maken**. Wacht tot de taak is voltooid en een nieuwe **Handmatig**-back-up met actueel tijdstip en een grootte boven nul in de lijst staat. Noteer bestandsnaam en tijdstip. De standaard databaseback-up gebruikt PostgreSQL en laat koppelingsgeheimen weg. Ook met de optie voor geheimen blijft het kluissleutelbestand buiten de dump. Bij een fout of ontbrekende back-up hier stoppen.
3. Stop de hele MSO-app via **Apps → Stop**. Controleer dat ook worker en PostgreSQL gestopt zijn. Stop geen gedeelde database of andere app. Als de database niet samen met MSO gestopt kan worden, is dit herstelpad ongeschikt.
4. Kies voor elke betrokken dataset **Datasets → Data Protection → Create Snapshot**. Gebruik hetzelfde herkenbare herstelmoment en neem onderliggende datasets mee via **Recursive** of afzonderlijke snapshots. Controleer bij **Manage Snapshots** de datasetnamen en het tijdstip. Het herstelpunt moet de volledige database, `secrets/vault.key`, de bijpassende versleutelde gegevens, back-ups en eventuele afzonderlijke werkopslag omvatten. Controleer dat er ruimte is voor het upgrade-archief en wijzigingen na de snapshot. Verifieer de herstelbaarheid op een geïsoleerde kopie en bewaar een beschermde kopie buiten de oorspronkelijke dataset. Ga alleen verder met een volledig herstelpunt. [TrueNAS-datasetbeheer](https://www.truenas.com/docs/scale/24.10/scaleuireference/datasets/)

## 3. Wijzig de bestaande alles-in-één Custom App

1. Open **digests.json** bij de vrijgegeven versie. Gebruik de waarde bij de exacte sleutel `ghcr.io/lvos04/media-source-optimizer:1.6.1`. Deze waarde begint met `sha256:`.
2. Open de gestopte app onder **Apps → Edit**. Behoud de bestaande opslagkoppelingen, inclusief de bron van `/appdata`, de webpoort en gebruikersinstellingen. Laat **Repository** op `ghcr.io/lvos04/media-source-optimizer` staan of wijzig deze naar die waarde als je de herkomst van de oude alles-in-één-image hebt vastgesteld. Vul bij **Tag** `1.6.1@sha256:<digest>` in met de 64 tekens uit de bijlage. Accepteert het formulier uitsluitend een versietag, gebruik dan `1.6.1` en controleer de werkelijk opgehaalde registerdigest voordat je het gebruik hervat.
3. Kies **Update** of **Save**. Start MSO als de app gestopt blijft. Controleer in de appdetails de gebruikte image en vergelijk de registerdigest van de lokaal opgehaalde image met de bijlage. Gebruik de teruggelezen digest van deze installatie. Toont de webinterface alleen een Image ID of een tag en kun je de actuele registerdigest nergens aflezen, stop dan hier en houd MSO gepauzeerd. [TrueNAS-formuliervelden](https://www.truenas.com/docs/scale/24.10/scaleuireference/apps/installcustomappscreens/)

### Overstap vanuit meerdere containers of YAML

**STOP vóór het wijzigen van de installatie.** Het standaardformulier wijzigt één image. De geleverde meervoudige opzet gebruikt `media-source-optimizer-core`, `media-source-optimizer-web` en een afzonderlijke PostgreSQL 16 op Alpine. Daar krijgt PostgreSQL de submap `postgres` van APPDATA en hebben core en worker een afzonderlijk gekoppelde `/work`. De kandidaat start zijn eigen PostgreSQL 16 op Debian in `/appdata/postgres` en gebruikt standaard `/appdata/work`. Bij een lege databasemap maakt hij een nieuwe database aan.

Alleen een andere tag invullen, een lege dataset kiezen of de oude en nieuwe database tegelijk op dezelfde opslag starten kan gegevens onbereikbaar maken of beschadigen. Een gedeeld PostgreSQL-hoofdversienummer bewijst geen compatibiliteit van de twee clusters. Er is geen beproefde omzetting via deze webformulieren die ook de database, de kluissleutel, werkopslag en eventuele afwijkende mounts meeneemt. Dat geldt ook voor een YAML-app waarvan de precieze indeling nog moet worden vastgesteld.

Laat de bestaande appdefinitie en alle opslag staan. Heb je de app uitsluitend voor de snapshots gestopt, dan kun je dezelfde ongewijzigde app met haar oorspronkelijke opslag weer starten. Een overstap naar de standaard Custom App vereist eerst een geïsoleerde proef met een kopie van **deze** indeling, inclusief werkende terugweg en gegevenscontrole uit stap 4. De lokale upgradeproef met de 1.3.0-alles-in-één-image dekt die overstap niet.

## 4. Controleer de bijgewerkte app

| Controle | Verwachte uitkomst |
|---|---|
| **Apps → Workloads → View Logs** en appstatus | De container wordt gezond. Bij een bestaand ouder schema verschijnt eerst de back-up vóór de upgrade en daarna de eerste nieuwe migratie. De bronstand controleert SHA-256 en de leesbaarheid van het archief. Een fout houdt de normale diensten tegen. |
| Portal en **Back-up & updates** | De portal opent, de bestaande beheerder kan inloggen en de versie is 1.6.1. Het doelschema is 613. Versie 1.3.0 begint op 500. Van daaruit worden ook ontbrekende migraties met lagere nummers toegepast. Controleer de vermelding **Vóór upgrade** als het schema is bijgewerkt. |
| Instellingen, historie en koppelingen | Vergelijk de instelling, gebruikers, downloadhistorie en auditregel uit stap 1. Controleer bij de al ingestelde SABnzbd- en qBittorrent-koppelingen hun status en gebruik de verbindingstest. Bevestig dat bestaande wachtrij of historie leesbaar is. |
| Hervatten | Hervat de oorspronkelijke bedrijfsmodus en automatisering pas na akkoord op alle controles. Verschijnt de eerste-installatiewizard, maak dan geen nieuwe beheerder aan. Stop MSO en controleer eerst de opslagbron. |

## Terugweg

1. Pauzeer het gebruik en stop de bijgewerkte app. Bewaar de actuele logs en een afzonderlijke beschermde kopie van de huidige datasets. Zo blijven ook wijzigingen na het herstelpunt beschikbaar.
2. Herstel **alle** datasets van hetzelfde stilstaande herstelmoment. Via **Datasets → Manage Snapshots → Clone to New Dataset** kun je de oude toestand op eigen hersteldatasets beschikbaar maken en de huidige toestand bewaren. Herstel elke onderliggende dataset afzonderlijk met de juiste onderlinge mountpaden. Gebruik **Rollback** op de oorspronkelijke dataset uitsluitend na het veiligstellen van latere gegevens en snapshots, en alleen als die dataset exclusief van deze app is. Stop wanneer een veiligheidscontrole een conflict meldt. [TrueNAS-snapshotbeheer](https://www.truenas.com/docs/scale/24.10/scaleuireference/datasets/snapshotsscreens/)
3. Kies in **Apps → Edit** de oude repository, tag en registerdigest. Koppel bij gebruik van klonen de bijpassende herstelde datasets aan de oorspronkelijke containerpaden. Controleer dit vóór **Save** of **Update**, omdat opslaan de app kan starten. Start alleen de oude app op deze oude database en met haar eigen kluissleutel. Alleen de tag terugzetten op het gemigreerde schema is onveilig.
4. Controleer de oude schemaversie en herhaal de aanmelding, gegevens- en downloadercontroles uit stap 4. Beoordeel na een terugval ook ingetrokken toegangsrechten en sleutels sinds de snapshot voordat je toegang heropent. De lokale proef gebruikt een kopie van gestopte APPDATA. De ZFS-bediening, de werkelijk geïnstalleerde versie en de terugweg op jouw NAS zijn **NIET GEVALIDEERD (echte NAS)**.
