# Gebruikershandleiding — Media Source Optimizer

## 1. Eerste start

1. Open `http://<truenas>:38081`. Bij een lege installatie verschijnt **Setup**.
2. **Beheerder aanmaken** (≥ 10 tekens, hoofd/kleine letters of cijfers).
3. **Koppelingen**: vul per dienst de URL in (LAN-adres of containernaam, bijv. `http://192.168.1.10:9696`),
   daarna de sleutel/het wachtwoord. Klik **Test** (laat versie en mogelijkheden zien) en **Bewaar**.
   Na opslaan zie je alleen nog `ingesteld` — geheimen worden nooit meer getoond.
   - Prowlarr: API-sleutel (Settings → General).
   - SABnzbd: API-sleutel (Config → General). Gebruik de gewone API-sleutel, niet de NZB-sleutel: de servertest
     (§6.3) en de categorieën (§6.5) hebben hem nodig.
   - qBittorrent: API-sleutel (≥ 5.2) of gebruikersnaam + wachtwoord.
   - MediaGuard (optioneel): bij voorkeur een **API-sleutel** met rol *operator* (MediaGuard → Instellingen → API-sleutels;
     zie `docs/MEDIAGUARD_API_KEYS.md`), anders een eigen MediaGuard-gebruiker met rol *operator* of *viewer*.
4. **Modus**: laat **Alleen-lezen** staan tot je de adviezen hebt bekeken. In alleen-lezen schrijft MSO
   niets naar je diensten. Kies je **Autopilot**, dan begint op die dag ook de proefperiode van de doelenkaart (§3.1).
5. Afronden. Achtergrondtaken halen nu historie, indexers en torrents op (eerste resultaten binnen enkele minuten).

## 2. Het verschil tussen advies, automatisch veilig en toestemming nodig

| Label | Betekenis |
|---|---|
| **Advies** | alleen uitleg/voorstel; nooit automatisch |
| **Automatisch veilig** | kleine, omkeerbare wijziging binnen grenzen; mag door automatisering worden toegepast als je dat gebied aanzet (Automatisering) én beheermodus actief is én automatisering niet gepauzeerd is |
| **Toestemming nodig** | eerst goedkeuren door een beheerder, daarna toepassen |

Elke wijziging toont een **voorbeeld** (oud → nieuw) en kan worden **teruggedraaid**. Heeft een ander systeem
de waarde intussen veranderd (bijv. MediaGuard), dan weigert MSO toe te passen en vraagt om een nieuw voorstel.
Bij terugdraaien kijkt MSO ook eerst: staat er intussen een andere waarde, dan laat MSO die staan (§4.5).
De knop **Automatisering pauzeren** in de bovenbalk stopt direct alle automatische acties.

## 3. Dagelijks gebruik

- **Overzicht**: bovenaan de **doelenkaart** (§3.1), daarna **Wacht op jou** (§3.2) en **Wat MSO deed** (§3.3).
  Daaronder gezondheid, koppelingen, actieve downloads, mislukt (7 d) en waar het misgaat, kosten per maand. Elke kaart
  klikt door.
- **Zoeken**: zoek in alle bronnen tegelijk; kies categorie (Series, Anime-series, Films, Animefilms).
  Elke bron toont of hij resultaten gaf, leeg was, een time-out of een categoriefout had. Vergelijk
  kandidaten en verstuur er één naar SABnzbd/qBittorrent. Nep-releases krijgen een rode badge (§13).
- **Usenet-providers**: status per provider (inloggen, verlopen, limiet), de SABnzbd-servertest (§6.3), vervaldata en
  blokken (§6.4), de SABnzbd-categorieën (§6.5), beschikbaarheid van artikelen uit je eigen downloads en de
  betrouwbaarheid van die meting. Artikeltests kosten verbindingen/bandbreedte en draaien alleen na bevestiging.
- **Downloads → Usenet**: per mislukte download wat SABnzbd zelf vastlegde (§6.1).
- **Torrentclient / Opslag & seeding**: diagnose per torrent, verbinding/poort/VPN, tuningvoorstellen,
  seedverplichtingen per tracker (stel ze in onder *Trackerregels*), vrije ruimte en hardlinks.
- **Ontbrekend & herstel**: waarom downloads mislukken (Source Baseline), kandidaten die aan de
  MediaGuard-kwaliteitseisen voldoen, en *Vraag MediaGuard opnieuw te zoeken* (met quotum).
- **Kosten & besparing**: vul je abonnementen in. *Wat als ik dit uitschakel?* kijkt alleen terug naar wat
  MSO heeft waargenomen en toont een bandbreedte met dekking — het is geen garantie. MSO zegt nooit iets op.

### 3.1 Doelenkaart (Overzicht)

Bovenaan Overzicht staat de kaart **Doelen (proefperiode)**. Hij beantwoordt één vraag: haalt MSO jouw drie doelen?

- **Faalratio** (doel: onder 10 %) over de laatste 7 dagen. *Netto* telt per aflevering of film: wat eerst mislukte
  maar later alsnog binnenkwam, telt als gelukt. *Bruto* telt elke downloadpoging. Beide staan er met het aantal
  (n); onder de 20 zegt de kaart eerlijk "te weinig data". Het doel volgt netto.
- **Verdachte torrents** (doel: 0): torrents met een programma of script (bijv. `.exe`) in een media-download. Je ziet
  hoeveel er nieuw zijn in 7 dagen (werkt de preventie?) en hoeveel er in een opruimvoorstel op jouw goedkeuring
  wachten. Verwijderen gebeurt nooit automatisch.
- **Falende servers** (doel: 0): SABnzbd-servers die aanstaan maar niet werken. Servers die MSO zelf uitzette omdat ze
  niet werken tellen niet mee; ze staan apart ("4 uitgezet omdat ze niet werken; herstellen of verwijderen in SABnzbd
  is jouw keuze").

Per doel zie je de startwaarde, de waarde nu, of het beter of slechter gaat, het verloop per dag (de stand aan het
eind van elke dag; de stippellijn is het doel; lichtere punten zijn afgeleid uit de geschiedenis omdat er die dag geen
dagmeting was) en onder **Wat houdt dit tegen** de ene zin met de actie die nodig is, bijvoorbeeld "41 wachten op jouw
goedkeuring (opruimvoorstel)" of "Meeste mislukkingen: uitpakken mislukt". De link brengt je naar die actie.

De **startwaarde** is de stand in het eerste uur van de proefperiode (faalratio: de 7 dagen ervoor). Wat MSO daarna
doet — een server uitzetten, een opruiming na jouw goedkeuring — verandert de startwaarde dus niet; zo zie je eerlijk
hoeveel er sindsdien veranderd is. Tijdens dat eerste uur staat er "voorlopig"; daarna ligt de startwaarde vast.

De proefperiode duurt 28 dagen en begint standaard op de dag dat je Autopilot voor het eerst aanzette (anders op de
installatiedag). Een andere startdatum stel je in onder **Instellingen → Automatisering → Start proefperiode
(doelenkaart)** (JJJJ-MM-DD; leeg = automatisch). Dagen zijn lokale dagen (tijdzone van de container, standaard
Europe/Amsterdam).

### 3.2 Wacht op jou

Direct onder de doelenkaart staat wat jouw beslissing vraagt. Elke regel is één beslissing: het opruimvoorstel voor
nep-downloads staat er één keer, met het aantal en hoeveel er sinds gisteren bij kwamen. Wacht er niets, dan zie je
één regel "Niets wacht op jou ✓". Wacht er wel iets, dan zie je dat ook op andere pagina's: in de topbalk (op de
telefoon als icoon met een getal), als badge bij Automatisering en als "(1)" in de titel van het browsertabblad.
Meldingen "ter info" (bijvoorbeeld dat je zelf iets hebt teruggezet) vragen geen actie en tellen niet mee. De kaarten
Aanbevelingen en Wijzigingen verderop tonen alleen wat hier niet al staat.

### 3.3 Wat MSO deed

Deze kaart telt alleen echte acties op je systemen. Synchronisaties en controles staan samen op één regel: "op
schema", of hoeveel er mislukt zijn, met een link naar Systeem → Automatische taken. Voor elke wijziging zie je:
- **Wat**: bijvoorbeeld "newshosting uitgezet in SABnzbd".
- **Waarom**: bijvoorbeeld "inloggen geweigerd".
- **Effect**: de meting na de wijziging, of hoe lang de meting nog loopt.

Met **Terugdraaien** zet je een omkeerbare wijziging meteen terug (operator of beheerder, niet in Alleen-lezen).
Automatisering → *Samenvatting* toont hetzelfde per dag.

## 4. Autopilot — set it and forget it (vanaf 1.5.0)

Kies onder **Instellingen → Systeem → Bedrijfsmodus** (of in de setupwizard) **Autopilot**. MSO lost dan zelf alles op
wat terug te draaien is, controleert elke wijziging bij de bron, meet het effect en draait terug bij verslechtering:

| Wat | Wanneer |
|---|---|
| qBittorrent: uitvoerbare bestanden (`.exe`, `.scr`, `.zipx` …) nooit downloaden | direct (je eigen patronen blijven staan) |
| qBittorrent: extreme waarden naar de veilige grens, daarna tuning per waarde met meting | direct / per meetperiode |
| qBittorrent: tracker opnieuw aanmelden bij trackerfouten; recheck bij ontbrekende bestanden | begrensd per uur |
| SABnzbd: server uit bij *account verlopen* (direct) of *inloggen/toegang geweigerd, blok op* (na 6 u), maar alleen als SABnzbd's eigen servertest het bevestigt — nooit de laatste werkende | daarna test SABnzbd hem elke 6 u (na een week dagelijks, verlopen account wekelijks); weer aan alleen na een geslaagde test (§6.3) |
| SABnzbd: mislukte downloads opnieuw proberen | alleen bij ontbrekende artikelen (of een serverfout die SABnzbd noemt) én nadat de dekking verbeterde: een uitgezette server werkt weer of er kwam een server bij (max 20/u, 100/dag; pauzeert zichzelf als < 30 % lukt) (§6.2) |
| Mislukte downloads: MediaGuard vragen opnieuw te zoeken (MediaGuard blijft eigenaar) | binnen MSO- en MediaGuard-quota |
| Synchronisaties, kosten-import, back-ups, onderhoud, voetafdruk-controle | volgens schema (Systeem → *Automatische taken*) |

**Nooit automatisch:** verwijderen. De opruiming van verdachte torrents (met bestanden) staat altijd kant-en-klaar onder
**Overzicht → Wacht op jou** en kost één klik. Ook een SABnzbd-categorie aanmaken (§6.5) gebeurt alleen na jouw
goedkeuring. Op het Overzicht zie je onder **Wat MSO deed** wat Autopilot de laatste 24 uur of 7 dagen heeft gedaan
(§3.3). Met **Automatisering pauzeren** (bovenbalk) stop je alles direct.

Autopilot bewaakt zichzelf (Automatisering → **Veiligheid**):

### 4.1 Noodrem en Hervatten

Gaat er te veel mis (standaard 3 wijzigingen die automatisch zijn teruggedraaid omdat ze slechter bleken, of 5
automatische wijzigingen die niet lukten, binnen 24 uur), dan pauzeert MSO zijn eigen automatische wijzigingen.
Bovenaan elke pagina staat dan een rode banner "Autopilot heeft zichzelf gepauzeerd". Synchronisaties, controles en
het klaarzetten van voorstellen gaan gewoon door, en je kunt zelf wijzigingen toepassen. Wat te doen: klik op
**Bekijken**, kijk onder Automatisering → Controle en Wijzigingen wat er misging, en klik op **Hervatten** (operator of
beheerder). Een andere modus kiezen heft de noodrem niet op; alleen Hervatten doet dat. De grenzen stel je in onder
Instellingen → Automatisering.

### 4.2 Afkoeling na mislukte pogingen

Lukt een automatische wijziging niet, dan probeert MSO het pas na 1 uur, daarna na 4 uur opnieuw. Na 3 keer staat er
één melding in "Wacht op jou"; MSO probeert het dan nog hooguit één keer per dag. Pas de wijziging zelf toe, verwerp
haar, of klik op **Opnieuw automatisch proberen**. Mislukte pogingen tellen mee voor de uurlimiet.

### 4.3 Autopilot terugdraaien

Kies "Laatste 24 uur" of "Laatste 7 dagen": je ziet precies welke automatische wijzigingen terug te zetten zijn en naar
welke waarde. "zet uit" betekent dat terugdraaien iets uitzet dat MSO had aangezet (bijvoorbeeld een server die na een
geslaagde test weer aan stond). Na bevestiging zet MSO ze één voor één terug en controleert elke waarde. Wat intussen
buiten MSO is gewijzigd, wordt overgeslagen. Wat je zo terugzet, past MSO 30 dagen niet opnieuw automatisch toe
(voorstellen blijven mogelijk). Niet beschikbaar in alleen-lezen; verwijderingen staan hier nooit in.

### 4.4 Onderbroken wijzigingen

Werd MSO herstart tijdens een wijziging, dan staat die even op "Bezig met toepassen". Binnen enkele minuten kijkt MSO bij
de bron wat er al was ingesteld en rondt de wijziging af of zet de al geschreven waarden terug. Is er iets onduidelijk,
dan verschijnt een melding in "Wacht op jou". In Alleen-lezen schrijft MSO ook dan niets: het wacht tot je weer
beheermodus of Autopilot kiest.

### 4.5 Je eigen keuze telt

Zet je een waarde die MSO had ingesteld zelf terug (in SABnzbd, qBittorrent of Prowlarr, of met Terugdraaien), dan
past MSO die niet opnieuw automatisch toe: standaard 30 dagen, instelbaar onder Instellingen → Automatisering → "Eigen
keuze respecteren". Je krijgt één melding ter info, en het blijft hooguit een voorstel. Zet je zelf een server uit
of aan in SABnzbd, dan geldt dat ook voor de serverbewaking. Wat MSO nu nog veranderd heeft, zie je op de Voetafdruk
(§14).

## 5. Problemen oplossen met één goedkeuring

MSO meldt problemen niet alleen, maar maakt er een **voorstel** van dat je met één goedkeuring uitvoert. Alles wat
iets verwijdert of uitschakelt heeft altijd risico **Toestemming nodig**: voorbeeld → goedkeuren → toepassen →
controle → audit. Het wordt nooit automatisch toegepast, ook niet in Beheermodus met automatisering aan. In
Alleen-lezen wordt niets toegepast. (Uitzondering: in Autopilot zet MSO kapotte Usenet-servers zelf uit na een
bevestigende servertest, §4.)

| Probleem | Waar | Wat het voorstel doet | Terugdraaien |
|---|---|---|---|
| **Verdachte torrents** (`.exe`, `.scr`, `.zipx` … in een media-download) | Torrentclient / Downloads → *Opruimvoorstel maken*, of het advies accepteren | Per torrent of als bundel: (1) de release op de blokkeerlijst van Sonarr/Radarr via MediaGuard (als hij nog in hun wachtrij staat), (2) op MSO's eigen blokkeerlijst, (3) torrent **met bestanden** verwijderen in qBittorrent. Daarna controleert MSO dat hij echt weg is. | **Nee**. Het voorbeeld zegt dat expliciet. Wijzigt de lijst tussen voorbeeld en toepassen, dan weigert MSO de hele bundel. |
| **Kapotte Usenet-servers** (inloggen geweigerd, toegang geweigerd, account verlopen) | Usenet-providers → Serverprioriteit → *Voorstel: N kapotte servers uitschakelen* | Zet die servers in SABnzbd uit (`enable=0`) en leest de instelling terug ter controle | Ja (servers weer aan) |
| **Downloads in categorie '*'** omdat SABnzbd de categorie van Sonarr/Radarr niet kent | Usenet-providers → SABnzbd-categorieën → *Voorstel maken* (beheerder), of "Wacht op jou" | Maakt de ontbrekende categorie in SABnzbd aan, met een eigen map met dezelfde naam, en leest SABnzbd terug ter controle (§6.5) | Alleen met de hand door een beheerder, en alleen als de categorie nog precies is zoals MSO hem maakte en geen download hem gebruikt. Wat MediaGuard intussen opruimde, komt niet terug. |
| **Mislukte download** | Ontbrekend & herstel → *Fallback na mislukte download* | Zet de mislukte release op de blokkeerlijst en stelt de beste kandidaat voor die aan de MediaGuard-kwaliteitseisen voldoet: *Versturen naar SABnzbd/qBittorrent*. Vóór versturen controleert MSO de inhoud op nep (§13); daarna dat de client hem heeft. | Nee (versturen is niet terug te draaien) |
| **Extreme qBittorrent-waarden** | Torrentclient → *Voorstel berekenen* | Eén bundel die alle waarden buiten de veilige grens in één keer op de grens zet (geen meetperiode). Daarna verfijnt de tuning per waarde. | Ja, als geheel |

Herinneringen: een server met *Account verlopen*, of die langer dan `usenet.server_error_reminder_days` (standaard 3)
fouten geeft, krijgt een advies; een account met een vervaldatum in SABnzbd krijgt een herinnering 14, 7 en 1 dag
vooraf (§6.4). *Automatisch versturen* van fallback-kandidaten (`recovery.auto_send_fallback`) staat standaard **uit**.
Voor MediaGuard-items komt er pas een voorstel als `herstel.allow_manual_send_connected` aan staat; anders blijft
MediaGuard de enige die verstuurt.

## 6. Usenet: mislukte downloads, servers en providers

### 6.1 Waarom mislukt een download?

MSO leest per mislukte download het verwerkingslog van SABnzbd: wat SABnzbd zelf over die download vastlegde. Onder
**Downloads → Usenet** staat per download wat er gebeurde, met een korte regel over de oorzaak. Een kapotte server
wordt alleen nog als waarschijnlijke oorzaak genoemd als er echt artikelen ontbraken.

| Je ziet | Wat het betekent | Wat je doet |
|---|---|---|
| **Data compleet · uitpakken mislukt: …** met *wachtwoord vereist*, *beschadigd archief* of *uitvoerbaar bestand* | De servers leverden alles; het ligt aan deze release (vaak een nep-release). | Niets. Een andere release kan wel lukken; Sonarr/MediaGuard (of MSO's herstel) zoeken er een. |
| **Data compleet · uitpakken mislukt: …** met *schijf vol*, *geen schrijfrechten*, *bestand te groot* of *verplaatsen mislukt* | De servers leverden alles; het ligt aan SABnzbd of de opslag. Elke release zou zo mislukken. | Maak ruimte vrij of controleer in TrueNAS de rechten op de map van SABnzbd. MSO verandert hier zelf niets aan en zoekt er geen andere release voor. |
| **Data compleet · uitpakken mislukt: Unpacking failed, see logfile** | Het uitpakprogramma noemt zelf geen oorzaak: "Oorzaak niet bekend; zie het SABnzbd-logbestand". | Kijk in het logbestand van SABnzbd. Komt dezelfde regel bij drie verschillende releases terug, dan behandelt MSO het als een probleem van SABnzbd of de opslag (zie hieronder). |
| **312 artikelen ontbraken** (eventueel: *herstel mislukt, 12 blokken tekort*) | Een dekkingsvraag: je servers hadden niet alle stukken. | Een andere server of backbone kan helpen. MSO probeert de download opnieuw zodra de dekking verbetert (§6.2). |

Onder de regel staat ook hoe oud de post was en hoeveel elke server leverde.

**SABnzbd kan downloads niet uitpakken.** Mislukt het uitpakken steeds op dezelfde manier door SABnzbd of de opslag
(3 keer in 7 dagen; bij "see logfile": bij 3 verschillende releases), dan krijg je één melding in "Wacht op jou" met de
precieze regel, het aantal en voorbeelden. Hij verdwijnt vanzelf als de fout 3 dagen niet meer voorkomt.

**Opnieuw beoordelen.** Direct na de upgrade naar 1.6.0 beoordeelt MSO alle mislukte downloads in de SABnzbd-historie
(ook het archief) opnieuw. Met de knop **Opnieuw beoordelen** (Downloads → Usenet, operator) doe je dat zelf; erboven
staat hoeveel downloads een andere oorzaak kregen. Downloads die niet meer in SABnzbd staan, houden hun oude oorzaak.

De kolom "Artikelen gevonden" (Serverprioriteit) en de passieve beschikbaarheid tonen sinds 1.6.0 het echte
percentage gevonden artikelen. Eerder stond daar door een telfout het percentage *mislukte* artikelen (Eweka "0,4 %"
was in werkelijkheid 99,6 %).

### 6.2 Opnieuw proberen

Opnieuw proberen helpt alleen als SABnzbd nu meer artikelen kan bereiken dan toen de download mislukte. MSO probeert
daarom alleen downloads met ontbrekende artikelen (of een serverfout die SABnzbd zelf noemt) opnieuw, en pas nadat een
server hersteld is of er een nieuwe bij kwam. Een server uitzetten is geen reden. Op **Usenet-providers → Autopilot**
zie je "Laatste 14 dagen: opnieuw geprobeerd N · gelukt X %". Lukt minder dan 30 % van minstens 10 pogingen in 14
dagen, dan pauzeert MSO dit zelf, met uitleg. Het hervat vanzelf bij betere dekking, of met **Hervatten** (operator; ook
in Alleen-lezen, want het schrijft niets naar SABnzbd).

### 6.3 Servers testen in plaats van blind aanzetten

In Autopilot zet MSO een SABnzbd-server met een accountprobleem (inloggen geweigerd, toegang geweigerd, blok op) na
6 uur uit, een verlopen account meteen — maar alleen als SABnzbd's eigen servertest het probleem bevestigt. Die test is
dezelfde als de knop 'Test server' in SABnzbd: SABnzbd logt in met het wachtwoord dat daar staat; MSO ziet dat
wachtwoord nooit en bewaart ook je gebruikersnaam niet. De laatste werkende server zet MSO nooit uit.

Een uitgezette server laat MSO elke 6 uur testen (na een week dagelijks, een verlopen account wekelijks; MSO geeft
nooit op). Pas als de test slaagt, zet MSO de server weer aan. Ben je niet in Autopilot, dan staat er een voorstel klaar
in "Wacht op jou". Een server telt pas als "werkt weer" als SABnzbd hem echt gebruikt zonder nieuwe fouten.

- **Wachtwoord in SABnzbd gerepareerd?** Klik op **Testen** bij de server (Usenet-providers → Serverprioriteit, Autopilot
  of de providerkaart). Dat werkt in elke modus, ook zonder inloggegevens in MSO, en verandert niets. Pas je in SABnzbd
  het adres, de poort, de gebruikersnaam, de vervaldatum of het quotum aan, dan test MSO meteen; een nieuw wachtwoord
  ziet MSO pas bij de volgende test, of meteen met **Testen**.
- **Te veel verbindingen.** Meldt de provider "te veel verbindingen", dan werkt het account wel: MSO zet de server niet
  uit. Wisselen de testresultaten, dan krijg je het advies het aantal verbindingen in SABnzbd te verlagen of te
  controleren of iemand anders het account gebruikt.
- **Verlopen account.** Geen proeven, één rustige melding in "Wacht op jou": verlengen of de server in SABnzbd
  verwijderen is jouw keuze. Verleng je, dan ziet MSO dat bij de volgende test (of meteen als de vervaldatum in SABnzbd
  verandert).
- **Voorstel verworpen?** Verwerp je het voorstel om een server weer in te schakelen, dan komt het niet bij elke
  geslaagde test terug. MSO stelt het pas opnieuw voor na een mislukte test, een wijziging van de server in SABnzbd of
  als je zelf op **Testen** klikt.
- **Configuratie vergrendeld in SABnzbd?** Dan kan MSO niet testen of omschakelen; je krijgt één rustige melding en MSO
  kijkt dagelijks opnieuw.
- Biedt je SABnzbd de test niet aan, dan werkt MSO zoals in 1.5.0 (een proef-aanzet met oplopende wachttijd).

De interval stel je in onder Instellingen → Usenet ("Uitgeschakelde server testen elke", standaard 6 uur).

### 6.4 Vervaldatum en blok

Vul in SABnzbd (Config → Servers) bij een server de vervaldatum en/of het quotum in. MSO herinnert je dan 14, 7 en 1 dag
vóór het verlopen, toont bij de provider wat er van het blok over is en wanneer het bij het huidige verbruik op is, en
vult een lege verlengdatum onder Kosten in (een datum die je zelf invulde blijft staan). Zonder quotum in SABnzbd telt
het blokverbruik vanaf het moment dat je de blokgrootte bij de provider invult. Had je de blokgrootte al vóór 1.6.0
ingevuld, sla hem dan één keer opnieuw op (bij een deels gebruikt blok: het restant); tot dan toont MSO het verbruik
zonder voorspelling of herinnering.

### 6.5 Waarom staan downloads in '*'?

SABnzbd zet een download in zijn standaardcategorie '*' als hij geen categorie meekrijgt of de meegestuurde
categorie niet kent. Sonarr/Radarr zien zulke downloads dan niet terug: geen automatische import en geen afhandeling
van mislukte downloads (blokkeren en opnieuw zoeken). MediaGuard levert en ruimt ze ook niet op.

Onder **Usenet-providers → SABnzbd-categorieën** zie je bovenaan wat er aan de hand is:
- **SABnzbd kent een categorie niet** die Sonarr/Radarr meesturen (vaak `tv-anime` of `movies-anime`). Met
  **Voorstel maken** (beheerder) zet MSO een wijziging klaar die de categorie in SABnzbd aanmaakt, met een eigen map
  met dezelfde naam. Je ziet vooraf de nieuwe map en wat er gebeurt; het wordt pas toegepast na goedkeuring en
  nooit automatisch. Daarna ruimt MediaGuard downloads in die categorie op volgens zijn eigen regels; dat deel kan
  MSO niet terugdraaien. MSO zet dit voorstel ook zelf klaar in **Wacht op jou** als het zeker is. Verwerp je het (of
  draai je het terug), dan zet MSO het niet opnieuw klaar, tenzij het probleem eerst opgelost was en later terugkomt.
  Liever niet? Kies dan in MediaGuard (Inrichting → Downloadclients) een categorie die SABnzbd al kent.
- **Er wordt geen categorie meegestuurd.** Beheert MediaGuard die downloadclient, los het dan op in MediaGuard
  (Inrichting → Downloadclients, categorie per instantie) en pas de inrichting toe; anders in Sonarr/Radarr
  (Instellingen → Download Clients → SABnzbd → Category). Een wijziging in Sonarr zelf ziet MediaGuard als
  afwijking.
- **De categorieën kloppen** maar er komen toch downloads in '*': die komen van een andere bron (handmatig, RSS in
  SABnzbd, een andere app). MSO's eigen verzendingen met `mso-tv` enz. komen ook in '*' zolang SABnzbd die
  categorie niet kent; dat is onschuldig (ze worden bewust niet automatisch geïmporteerd).

Daaronder: het aantal '*'-downloads per dag (zo zie je of een oplossing werkt) en per Sonarr/Radarr-instantie welke
categorie hij stuurt, of SABnzbd die kent, wie de downloadclient beheert en de meldingen van Sonarr/Radarr zelf. Voor
dit alles leest MSO MediaGuard alleen (rol viewer volstaat); zonder MediaGuard zie je alleen de tellingen en het
advies om het in Sonarr/Radarr na te kijken.

### 6.6 Beschikbaarheid meten

SABnzbd geeft wachtwoorden van servers niet door. Wil je dat MSO zelf meet welke provider jouw artikelen heeft, vul dan
op de providerkaart eenmalig gebruikersnaam en wachtwoord in. Ze worden versleuteld opgeslagen (AES-256-GCM) en nooit
getoond of gelogd. (Voor de servertest van §6.3 is dat niet nodig.)

Na elke NZB die MSO zelf verstuurt, doet MSO een kleine **STAT-steekproef** (standaard 25 artikelen) bij elke provider
waarvan het inloggegevens heeft: 1 verbinding per provider, binnen een instelbaar dag- en maandbudget (verbindingen,
commando's, kB). De tabel *Beschikbaarheid en overlap* vult zich daarmee (gecontroleerd, beschikbaar, uniek, dekking,
vertrouwen). Dat geldt alleen voor jouw eigen artikelen en is geen garantie voor retentie.

## 7. Kosten

Providers (uit SABnzbd) en indexers (uit Prowlarr) verschijnen automatisch als dienst met *Prijs ontbreekt*. Je vult
alleen prijs, periode en verlengdatum in; staat er in SABnzbd een vervaldatum bij de server, dan vult MSO een lege
verlengdatum zelf in (§6.4). Daarna zie je per dienst de kosten per geslaagde GB en per geslaagde download, en bij
providers welk deel van de gemeten artikelen alleen die provider had. MSO belooft geen besparing en zegt nooit iets op.

## 8. Controle na een wijziging

Na het toepassen meet MSO tijdens een meetperiode. Daarbij gelden:
- standaard 24 uur, of de periode van het voorstel;
- vergeleken wordt met een basislijn van even lang ervóór;
- alleen verschillen groter dan de hysterese (standaard 10 %) tellen.

Wat er gemeten wordt:

| Doel | Maatstaven |
|---|---|
| qBittorrent | downloadsnelheid van actieve torrents, tijd tot voltooiing |
| SABnzbd | faalratio, tijd tot voltooiing; een weer ingeschakelde server: zijn eigen status (werkt/faalt) |

Een mislukte download telt op het moment dat hij mislukte; een torrent die al weken vastzit, telt dus niet elke dag
opnieuw mee.

Bij Automatisering → **Controle** staat per wijziging de uitkomst: *beter*, *gelijk*, *slechter*, *onvoldoende data* of
*niet van toepassing* (verwijderen of uitschakelen wordt niet gemeten), met basislijn naast het meetresultaat.

Bij *slechter* draait MSO de wijziging automatisch terug als de bijbehorende instelling aan staat
(`torrent.autotune_auto_rollback` voor qBittorrent, standaard aan; `automation.auto_rollback` voor de rest, standaard
uit). In de audit staat dat als `change.auto_rolled_back`, met de reden. Staat de instelling uit, dan krijg je een
voorstel om terug te draaien. Voor wijzigingen die niet terug te draaien zijn, krijg je geen terugdraaivoorstel. Een
weer ingeschakelde server zet de controle nooit zelf uit (dat doet de serverbewaking van §6.3, na een test), en een
SABnzbd-categorie die MSO aanmaakte wordt nooit automatisch teruggedraaid.

## 9. Prowlarr: MSO als indexer

Onder **NZB-indexers** / **Torrent-indexers** staat het paneel *Newznab/Torznab-endpoint* met de URL en de
API-sleutel (tonen/roteren door beheerder). *Registreer in Prowlarr* maakt een voorstel (toestemming nodig);
na goedkeuren en toepassen staat MSO als indexer in Prowlarr. MSO voorkomt dat Prowlarr ↔ MSO elkaar
eindeloos bevragen. Zet `MSO_PUBLIC_URL` als Prowlarr MSO via een ander adres bereikt dan je browser.

## 10. AI-adviseur (optioneel)

Standaard uit en budget € 0. Aanzetten onder **Automatisering → AI-adviseur**: API-sleutel, model,
maandbudget. De adviseur legt alleen uit en doet suggesties (risico *advies*); hij voert nooit iets uit.
Zonder AI krijg je dezelfde uitleg van de ingebouwde regels.

## 11. Back-up, herstel en onderhoud

**Back-up & updates**: maak een back-up (standaard zonder geheimen), of herstel er een (typ de
bestandsnaam ter bevestiging). Vóór elk herstel maakt MSO automatisch een vangnet-back-up. Een beschadigde
back-up wordt geweigerd en de database blijft dan ongewijzigd.

**Wat zit erin.** Een back-up bevat alles wat MSO niet zelf opnieuw kan ophalen. Caches (zoekresultaten, MediaGuard,
header-index, torrent-metingen van de laatste uren) gaan niet mee; na terugzetten haalt MSO ze zelf op. Vóór elke
back-up controleert MSO of er genoeg ruimte is (twee keer de vorige back-up plus de reserve). Is dat niet zo, dan
wordt er niets weggeschreven en verschijnt er een melding in "Wacht op jou". Vangnet-back-ups die MSO vóór een
terugzetting maakt, verdwijnen na 30 dagen (de nieuwste twee blijven).

**Herstellen.** Een back-up van een oudere versie (bijvoorbeeld de back-up "Vóór upgrade") kun je gewoon herstellen:
MSO zet het schema eerst terug naar die versie, herstelt de back-up en werkt het schema daarna meteen weer bij. Een
back-up van een nieuwere versie weigert MSO. Modus, pauze en noodrem blijven bij een herstel zoals ze waren.

**Upgraden.** Bij een nieuwe versie met databasewijzigingen maakt MSO vóór de migratie automatisch een back-up
("Vóór upgrade"; de nieuwste drie blijven bewaard). Een ZFS-snapshot blijft verstandig, maar is niet meer nodig om
terug te kunnen. Terug naar een vorige versie: zie `docs/MIGRATION.md`.

**Onderhoud (Systeem → Onderhoud).** MSO houdt zichzelf klein: elke nacht verdwijnen afgeronde achtergrondtaken
ouder dan 14 dagen (van elke soort blijven de laatste 50 staan), mislukte taken ouder dan 60 dagen, losse metingen
ouder dan 30 dagen (die worden eerst per uur samengevat en dan 400 dagen bewaard), het automatiseringslogboek na 180
dagen en inlogpogingen na 90 dagen. Het auditlogboek blijft altijd. Kijkt een evaluatie van een automatische wijziging
verder terug dan 30 dagen, dan bewaart MSO de losse metingen vanzelf langer. Je kunt de termijnen aanpassen onder
Instellingen → Systeem; een beheerder kan het opruimen met "Nu opruimen" meteen starten.

**Koppelingen bewaken.** De achtergrondtaken merken zelf wanneer SABnzbd, qBittorrent of Prowlarr niet reageert of
de API-sleutel weigert. Overzicht → Koppelingen laat dan zien sinds wanneer ("Onbereikbaar sinds 2 d 4 u", "Sleutel
geweigerd sinds 20 min"). MSO probeert het elk kwartier opnieuw en haalt alles meteen bij zodra de koppeling terug is.
Pas als een sleutel langer dan 15 minuten geweigerd wordt, of een koppeling langer dan een dag onbereikbaar is, vraagt
MSO je in "Wacht op jou" om iets te doen (sleutel vervangen of de dienst/het adres controleren onder Instellingen →
Verbindingen).

## 12. Rollen

*Kijker* leest alles; *operator* test, verstuurt handmatig en past toe/draait terug; *beheerder* beheert
koppelingen, instellingen, gebruikers, modus, goedkeuringen en back-ups.

Een paar regels in detail:
- **Testen** van een Usenet-server en **Opnieuw beoordelen** van mislukte downloads: operator, in elke modus (ze
  veranderen niets aan je diensten).
- **Hervatten** na de noodrem en **Autopilot terugdraaien**: operator of beheerder; terugdraaien niet in Alleen-lezen.
- **Alles terugzetten** (Voetafdruk): operator of beheerder, niet in Alleen-lezen. MSO's eigen indexer uit Prowlarr
  halen is verwijderen en vraagt dus goedkeuring door een beheerder.
- Een **SABnzbd-categorie** voorstellen en het voorstel goedkeuren: beheerder. Een aangemaakte categorie terugdraaien
  (dus verwijderen): alleen een beheerder, en nooit automatisch.

## 13. Nep-releases tegenhouden

Nep-releases kopiëren echte titels en groepsnamen ("Criminal Minds S19E06 1080p WEB h264-GRACE.exe"); groepen
blokkeren helpt daarom niet. MSO kijkt naar de inhoud: een echte aflevering of film bevat nooit een programma. Telkens
als MSO zelf iets verstuurt — vanuit Zoeken, vanuit Ontbrekend & herstel, via een voorstel of automatisch door
Autopilot — haalt het eerst het .torrent- of NZB-bestand op en controleert het de bestanden. Bij een programma, een
vermomde extensie (.mkv.exe) of een beveiligd archief met wachtwoordhint stuurt MSO niets door; je ziet "Beleid weigert
deze release: …" en de release komt op de blacklist (Ontbrekend & herstel → "Blacklist mislukte releases"; alleen een
beheerder kan hem weghalen). Bij een magnetlink is de bestandenlijst pas na het starten bekend; MSO controleert dan de
titel en de naam in de link. Kan MSO het torrentbestand niet lezen, dan verstuurt het niets ("de bestanden zijn niet te
controleren").

In Zoeken krijgen resultaten met een programma in de naam de rode badge "Nep-release (uitvoerbaar bestand)"; ze staan
onderaan en de knop Verstuur is uitgeschakeld. Bij vervangers in Ontbrekend & herstel zie je onder "Toon bewijs" de rij
"Echte release": een programma in de naam of een onwaarschijnlijk kleine grootte (≥ 720p-aflevering onder 50 MB,
≥ 720p-film onder 200 MB) betekent afgewezen. MSO's eigen Newznab/Torznab-feed laat zulke resultaten helemaal weg, zodat
Sonarr/Radarr ze via Prowlarr nooit van MSO krijgen.

Torrents die niet via MSO binnenkomen (bijv. van Sonarr) controleert MSO ná het downloaden, zoals voorheen
(Downloads → "Verdacht: uitvoerbaar bestand" en het opruimvoorstel in "Wacht op jou").

## 14. Voetafdruk en MSO verwijderen

**Voetafdruk (Automatisering → Voetafdruk).** Hier zie je per systeem (SABnzbd, qBittorrent, Prowlarr) wat MSO nu
veranderd heeft: de oorspronkelijke waarde, de waarde van MSO en de waarde van dit moment. MSO controleert dat elk uur
bij de bron. Dat is alleen lezen en gebeurt in elke modus; met **Nu controleren** doe je het meteen. De status is een
van:
- **Nog actief**: MSO's waarde staat er nog.
- **Buiten MSO gewijzigd**: je hebt het zelf in SABnzbd, qBittorrent of Prowlarr veranderd. MSO respecteert dat en
  past die waarde een tijd niet opnieuw automatisch toe (§4.5). Je krijgt één melding ter info.
- **Bron onbereikbaar**: het systeem was niet bereikbaar; de laatst bekende waarde blijft staan.
- **Niet meer aanwezig**: de torrent of server bestaat niet meer.

Onderaan staat wat niet terug te zetten is, zoals verwijderde nep-torrents en opnieuw geprobeerde downloads.

**Alles terugzetten** zet de oorspronkelijke waarden terug, per systeem met de gewone controle (eerst kijken of de
waarde nog die van MSO is, dan schrijven, dan teruglezen). Je ziet eerst de lijst en bevestigt dan. Overgeslagen, met
uitleg: wat je zelf veranderde, wat niet bereikbaar is, wat er niet meer is, een oorspronkelijke waarde buiten de
veiligheidsgrenzen van MSO (bijv. "onbeperkt"), en SABnzbd-categorieën (die verwijdert alleen een beheerder met de hand,
§6.5). Van de uitsluitlijst in qBittorrent haalt MSO alleen zijn eigen patronen weg. Wat je zo terugzet, past Autopilot
daarna niet meteen opnieuw toe.

**MSO verwijderen**
1. Ga naar Automatisering → Voetafdruk en kies **Alles terugzetten**. Je ziet eerst precies wat er teruggezet wordt
   en wat blijft staan. Wil je MSO's wijzigingen houden, dan sla je deze stap over.
2. Zet MSO op **Alleen-lezen** (Instellingen → Systeem), zodat het niets meer verandert.
3. Verwijder daarna de app (zie TRUENAS_INSTALL.md → Verwijderen). Heb je MSO's indexer in Prowlarr geregistreerd,
   dan haalt "Alles terugzetten" die ook weg; als operator vraagt dat eerst goedkeuring door een beheerder.
