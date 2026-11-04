# Testprompt voor Claude Cowork — Media Source Optimizer op de echte TrueNAS

Plak alles tussen de lijnen in Claude Cowork op de computer van waaruit je je TrueNAS en je apps in de browser
kunt openen. Vul eerst de vier `<…>`-waarden in. Het rapport dat Cowork maakt, stuur je terug naar de
ontwikkelsessie; daarmee worden de punten uit `docs/HARDWARE_ACCEPTANCE.md` afgevinkt of opgelost.

---

**Opdracht: test Media Source Optimizer (MSO) 1.2.0 grondig op mijn echte TrueNAS en maak een testrapport met
schermafbeeldingen.**

**Omgeving**
- TrueNAS-webinterface: `<http://IP-VAN-TRUENAS>`
- MSO: `http://<IP-VAN-TRUENAS>:38081` (Custom App, image `ghcr.io/lvos04/media-source-optimizer:1.2.0`)
- Andere apps (open ze vanuit TrueNAS → Apps, of via deze adressen): Prowlarr `<url>`, SABnzbd `<url>`,
  qBittorrent `<url>`, MediaGuard `<url, meestal :30080>`
- Documentatie (lees eerst): https://github.com/lvos04/media-source-optimizer/tree/ccr-17c40d68-3nxnlb/docs.
  Vooral `TRUENAS_CUSTOM_APP.md`, `USER_GUIDE.md`, `HARDWARE_ACCEPTANCE.md` en `MEDIAGUARD_API_KEYS.md`.

**Harde regels (altijd)**
1. **Niets verwijderen**: geen torrents, downloads, bestanden, datasets, apps of gebruikers. Wijzig niets aan
   MediaGuard zelf behalve: één MSO-gebruiker of API-sleutel aanmaken als ik dat hieronder vraag.
2. MSO blijft in **Alleen-lezen** modus, behalve bij test G. Zet daarna direct terug op Alleen-lezen.
3. Pas in MSO alleen wijzigingsvoorstellen toe die je **meteen terugdraait**, en hooguit één per systeem.
   Nooit iets met risico *Toestemming nodig* goedkeuren buiten test G.
4. **Geen geheimen in het rapport of in schermafbeeldingen.** Maak API-sleutels, wachtwoorden, passkeys en
   tokens onleesbaar (zwart balkje) of laat dat deel buiten beeld. Noteer hooguit "ingesteld".
5. Gaat iets mis of twijfel je of een stap veilig is: stop met die stap, noteer wat je zag, maak een
   schermafbeelding en ga door met de volgende test.
6. Ik heb vooraf een ZFS-snapshot gemaakt van de datasets van MSO, MediaGuard, Prowlarr, SABnzbd en
   qBittorrent. Is dat niet zo, vraag het mij dan eerst.

**Werkwijze per test**
- Voer de stappen uit en vergelijk met het verwachte resultaat.
- Geef per test een status: **GESLAAGD**, **MISLUKT**, **GEDEELTELIJK** of **GEBLOKKEERD** (met reden).
- Maak een schermafbeelding bij elke foutmelding, elke MISLUKT/GEDEELTELIJK, en bij de momenten die hieronder
  met 📷 zijn gemarkeerd. Geef ze namen als `A1-installatie.png`, `C3-prowlarr-test.png`.
- Noteer bij fouten de exacte meldingstekst. Noteer ook de laatste ~30 regels van de app-log van MSO
  (TrueNAS → Apps → media-source-optimizer → Logs), met geheimen gemaskeerd.

## Tests

**A. Installatie en opstart**
1. Controleer de app in TrueNAS: status *Running*, daarna *Healthy*. 📷 app-overzicht.
2. Lees de MSO-log van de eerste start. Verwacht: regels `[mso] …`, migraties toegepast, `start api/worker/web`,
   geen `FOUT:`. 📷 log.
3. Controleer de app-instellingen: image + tag `1.2.0`, poort 38081 → 38081, Host Path → `/appdata`,
   *Custom User* uit, Privileged uit, Host Network uit. 📷 instellingen (zonder geheimen).
4. Open MSO vanaf een tweede apparaat in het LAN (bijv. telefoon op wifi). Verwacht: login of setup verschijnt.
5. Probeer MSO vanaf buiten het LAN (telefoon op mobiel netwerk). Verwacht: **niet** bereikbaar.

**B. Eerste gebruik, login, persistentie**
1. Doorloop de setupwizard (beheerder aanmaken). Bestaat er al een beheerder, log dan in. 📷 wizard of overzicht.
2. Uitloggen, inloggen, één keer met een fout wachtwoord. Verwacht: duidelijke melding.
3. Stop de app in TrueNAS en start hem weer. Verwacht: inloggen werkt, instellingen en koppelingen staan er nog.
4. Wissel in de topbar tussen donker, licht en systeem. 📷 Overzicht in licht en in donker.

**C. Prowlarr**
1. *Instellingen → Verbindingen*: Prowlarr-URL (LAN-IP + poort, niet `localhost`) en API-sleutel → **Test**.
   Verwacht: gezond met versie. 📷 (sleutel gemaskeerd)
2. *NZB-indexers* en *Torrent-indexers*: komt de lijst overeen met Prowlarr (namen, aan/uit, privé)?
   Komen de tags anime/normal overeen? 📷 beide.
3. Op *NZB-indexers* het paneel Newznab/Torznab-endpoint: noteer de getoonde URL. Is het het adres op poort
   38081 en **niet** `api:8200`?
4. Voeg MSO in Prowlarr toe als *Generic Newznab*: URL uit stap 3, API-pad `/api`, sleutel uit MSO
   (*Tonen*, alleen als beheerder). Klik **Test** in Prowlarr. Doe hetzelfde voor *Generic Torznab*.
   Verwacht: beide tests slagen. 📷 Prowlarr-test.
5. Zoek in Prowlarr op een bekende titel, met de MSO-indexer aan. Verwacht: resultaten, geen eindeloze
   herhaling. In MSO bij *Logs & audit* of in de zoekbronnen staat hooguit een geweigerde cyclische hop
   (`cycle_detected`). Controleer in de Prowlarr-geschiedenis dat er per zoekactie één verzoek naar MSO ging.
6. Verwijder de MSO-indexers daarna **niet**; zet ze in Prowlarr alleen uit als ze je normale zoekgedrag storen.

**D. Zoeken in MSO**
1. *Zoeken*: zoek één gewone serie (categorie Series), één anime (Anime-series), één film en één animefilm.
   Verwacht: resultaten, en per bron een status (ok/leeg/time-out/fout). Anime en normaal blijven gescheiden.
   📷 elk van de vier.
2. Open *Vergelijk kandidaten* voor twee resultaten. 📷
3. Verstuur **niets** naar een downloadclient, tenzij ik een testtitel opgeef: `<optioneel: testtitel of 'niet versturen'>`.

**E. SABnzbd en Usenet-providers**
1. Koppeling SABnzbd → **Test**. Verwacht: gezond, versie en categorieën.
2. *Downloads → Usenet*: wachtrij en historie. Komen ze overeen met SABnzbd? Hebben mislukte items een
   begrijpelijke oorzaak? 📷
3. *Usenet-providers*: worden de servers uit SABnzbd getoond (Eweka/Newshosting/blockaccount)? Klik
   *Importeer uit SABnzbd* als die er nog niet staan. Druk alleen op **Test** bij een provider als ik dat goed
   vind: `<ja/nee>`. Die test gebruikt één NNTP-verbinding. 📷
4. Bekijk de beschikbaarheid/bijdrage per provider. Verwacht: met dekking en betrouwbaarheid, en geen claim
   van "gegarandeerde unieke retentie". 📷

**F. qBittorrent en opslag**
1. Koppeling qBittorrent → **Test**. Verwacht: versie, WebAPI-versie en authenticatiemethode. 📷
2. *Torrentclient*: verbinding/poort/VPN-diagnose en knelpunten. Kloppen ze met wat qBittorrent zelf laat zien? 📷
3. *Downloads → Torrents*: zijn private torrents als privé gemarkeerd? Staan er onlogische diagnoses?
4. *Opslag & seeding*: vrije ruimte, reserve, seedverplichtingen. 📷

**G. Eén wijziging toepassen en terugdraaien (alleen dit ene voorbeeld)**
1. Zet MSO in **Beheermodus** (Instellingen → Systeem).
2. Maak op *Torrentclient* één klein voorstel, bijvoorbeeld de globale uploadlimiet een fractie lager. Of
   gebruik een open voorstel met risico *Automatisch veilig* op *Automatisering*. Noteer de oude waarde
   in qBittorrent. 📷 voorstel (preview).
3. **Toepassen** → controleer de nieuwe waarde in qBittorrent zelf. 📷
4. **Terugdraaien** → controleer dat qBittorrent weer exact de oude waarde heeft. 📷
5. Zet MSO terug op **Alleen-lezen**. Controleer in *Logs & audit* dat de stappen gelogd zijn. 📷

**H. MediaGuard**
1. *MediaGuard-koppeling*: URL van MediaGuard. Gebruik bij voorkeur een **API-sleutel**. Ondersteunt MediaGuard
   nog geen API-sleutels (zie `MEDIAGUARD_API_KEYS.md`), gebruik dan een MediaGuard-gebruiker met rol
   *operator*. Maak die zo nodig aan in MediaGuard → Instellingen → Gebruikers. **Test**. Verwacht: *Verbonden*,
   rol *operator*, versie, en een compatibiliteitstabel met overal "beschikbaar". 📷
2. *Ontbrekend & herstel*: worden ontbrekende items uit MediaGuard getoond, met diagnose? 📷
3. Vraag voor **één** item *Vraag MediaGuard opnieuw te zoeken* aan. Controleer in MediaGuard dat er één
   zoekopdracht kwam en dat het quotum klopt. 📷
4. Stop MediaGuard kort in TrueNAS. Verwacht: MSO toont *standalone/verstoord* en blijft werken. Start
   MediaGuard weer. Verwacht: na een minuut weer *Verbonden*. 📷 beide toestanden.

**I. Back-up en herstel**
1. *Back-up & updates* → **Back-up maken** (zonder geheimen). Verwacht: de back-up verschijnt in de lijst. 📷
2. Wijzig één onschuldige instelling (bijv. *Aantal back-ups bewaren*), herstel daarna de back-up (bestandsnaam
   typen). Verwacht: instelling terug, opnieuw inloggen nodig, koppelingen nog *ingesteld*. 📷

**J. Robuustheid**
1. Herstart de hele TrueNAS-host, alleen als dat nu kan: `<ja/nee>`. Verwacht: MSO komt vanzelf weer op
   *Healthy*, inloggen werkt.
2. Bekijk TrueNAS → Apps → MSO het CPU- en geheugengebruik in rust en tijdens een zoekactie. Noteer de getallen.

**K. Alle schermen (visueel)**
Open elk scherm op desktop **en** op een telefoonbreedte (telefoon of smal browservenster), in licht en donker:
Overzicht, Zoeken, Usenet-providers, NZB-indexers, Torrent-indexers, Torrentclient, Downloads,
Ontbrekend & herstel, MediaGuard-koppeling, Opslag & seeding, Kosten & besparing, Automatisering, Logs & audit,
Instellingen, Back-up & updates. Let op:
- tekst die niet leesbaar is;
- horizontaal scrollen op de telefoon;
- knoppen die niets doen;
- foutmeldingen;
- lege schermen zonder uitleg;
- Engelse of onbegrijpelijke teksten.
📷 desktop-donker en telefoon-licht van elk scherm, plus elk probleem.

## Rapport

Maak één document **`MSO-testrapport-<datum>.md`** (of .docx/.pdf als dat makkelijker is) met alle
schermafbeeldingen erin of ernaast in een map `screenshots/`. Opbouw:
1. **Samenvatting**: aantal tests per status, de 5 belangrijkste problemen, en of MSO veilig in gebruik kan blijven.
2. **Omgeving**:
   - TrueNAS-versie, CPU/RAM;
   - versies van MSO, MediaGuard, Prowlarr, SABnzbd en qBittorrent;
   - browser(s) en apparaten.
3. **Resultatentabel**: test-ID | omschrijving | status | wat je zag | schermafbeelding.
4. **Problemen**, elk met:
   - titel;
   - ernst (blokkerend/hoog/middel/laag);
   - stappen om het na te doen;
   - verwacht vs. werkelijk;
   - exacte foutmelding;
   - relevante logregels (gemaskeerd);
   - schermafbeelding.
5. **Visuele bevindingen** per scherm (desktop/telefoon, licht/donker).
6. **Terugzet-controle**:
   - MSO staat weer op Alleen-lezen;
   - de wijziging uit G is teruggedraaid;
   - MediaGuard draait weer;
   - niets verwijderd.
7. **Niet uitgevoerd** met reden.

Zet het rapport op mijn bureaublad en vertel me waar het staat.

---
