# Gebruikershandleiding — Media Source Optimizer

## 1. Eerste start

1. Open `http://<truenas>:38081`. Bij een lege installatie verschijnt **Setup**.
2. **Beheerder aanmaken** (≥ 10 tekens, hoofd/kleine letters of cijfers).
3. **Koppelingen**: vul per dienst de URL in (LAN-adres of containernaam, bijv. `http://192.168.1.10:9696`),
   daarna de sleutel/het wachtwoord. Klik **Test** (laat versie en mogelijkheden zien) en **Bewaar**.
   Na opslaan zie je alleen nog `ingesteld` — geheimen worden nooit meer getoond.
   - Prowlarr: API-sleutel (Settings → General).
   - SABnzbd: API-sleutel (Config → General).
   - qBittorrent: API-sleutel (≥ 5.2) of gebruikersnaam + wachtwoord.
   - MediaGuard (optioneel): bij voorkeur een **API-sleutel** met rol *operator* (MediaGuard → Instellingen → API-sleutels;
     zie `docs/MEDIAGUARD_API_KEYS.md`), anders een eigen MediaGuard-gebruiker met rol *operator* of *viewer*.
4. **Modus**: laat **Alleen-lezen** staan tot je de adviezen hebt bekeken. In alleen-lezen schrijft MSO
   niets naar je diensten.
5. Afronden. Achtergrondtaken halen nu historie, indexers en torrents op (eerste resultaten binnen enkele minuten).

## 2. Het verschil tussen advies, automatisch veilig en toestemming nodig

| Label | Betekenis |
|---|---|
| **Advies** | alleen uitleg/voorstel; nooit automatisch |
| **Automatisch veilig** | kleine, omkeerbare wijziging binnen grenzen; mag door automatisering worden toegepast als je dat gebied aanzet (Automatisering) én beheermodus actief is én automatisering niet gepauzeerd is |
| **Toestemming nodig** | eerst goedkeuren door een beheerder, daarna toepassen |

Elke wijziging toont een **voorbeeld** (oud → nieuw) en kan worden **teruggedraaid**. Heeft een ander systeem
de waarde intussen veranderd (bijv. MediaGuard), dan weigert MSO toe te passen en vraagt om een nieuw voorstel.
De knop **Automatisering pauzeren** in de bovenbalk stopt direct alle automatische acties.

## 3. Dagelijks gebruik

- **Overzicht**: gezondheid, actieve downloads, mislukt (7 d) en waar het misgaat, kosten per maand,
  open adviezen en wijzigingen. Elke kaart klikt door.
- **Zoeken**: zoek in alle bronnen tegelijk; kies categorie (Series, Anime-series, Films, Animefilms).
  Elke bron toont of hij resultaten gaf, leeg was, een time-out of een categoriefout had. Vergelijk
  kandidaten en verstuur er één naar SABnzbd/qBittorrent.
- **Usenet-providers**: status per provider (inloggen, verlopen, limiet), beschikbaarheid van artikelen uit
  je eigen downloads en de betrouwbaarheid van die meting. Artikeltests kosten verbindingen/bandbreedte en
  draaien alleen na bevestiging.
- **Torrentclient / Opslag & seeding**: diagnose per torrent, verbinding/poort/VPN, tuningvoorstellen,
  seedverplichtingen per tracker (stel ze in onder *Trackerregels*), vrije ruimte en hardlinks.
- **Ontbrekend & herstel**: waarom downloads mislukken (Source Baseline), kandidaten die aan de
  MediaGuard-kwaliteitseisen voldoen, en *Vraag MediaGuard opnieuw te zoeken* (met quotum).
- **Kosten & besparing**: vul je abonnementen in. *Wat als ik dit uitschakel?* kijkt alleen terug naar wat
  MSO heeft waargenomen en toont een bandbreedte met dekking — het is geen garantie. MSO zegt nooit iets op.

## 4. Problemen oplossen (vanaf 1.4.0)

MSO meldt problemen niet alleen, maar maakt er een **voorstel** van dat je met één goedkeuring uitvoert. Alles wat
iets verwijdert of uitschakelt heeft altijd risico **Toestemming nodig**: voorbeeld → goedkeuren → toepassen →
controle → audit. Het wordt nooit automatisch toegepast, ook niet in Beheermodus met automatisering aan. In
Alleen-lezen wordt niets toegepast.

| Probleem | Waar | Wat het voorstel doet | Terugdraaien |
|---|---|---|---|
| **Verdachte torrents** (`.exe`, `.scr`, `.zipx` … in een media-download) | Torrentclient / Downloads → *Opruimvoorstel maken*, of het advies accepteren | Per torrent of als bundel: (1) de release op de blokkeerlijst van Sonarr/Radarr via MediaGuard (als hij nog in hun wachtrij staat), (2) op MSO's eigen blokkeerlijst, (3) torrent **met bestanden** verwijderen in qBittorrent. Daarna controleert MSO dat hij echt weg is. | **Nee**. Het voorbeeld zegt dat expliciet. Wijzigt de lijst tussen voorbeeld en toepassen, dan weigert MSO de hele bundel. |
| **Kapotte Usenet-servers** (inloggen geweigerd, toegang geweigerd, account verlopen) | Usenet-providers → Serverprioriteit → *Voorstel: N kapotte servers uitschakelen* | Zet die servers in SABnzbd uit (`enable=0`) en leest de instelling terug ter controle | Ja (servers weer aan) |
| **Mislukte download** | Ontbrekend & herstel → *Fallback na mislukte download* | Zet de mislukte release op de blokkeerlijst en stelt de beste kandidaat voor die aan de MediaGuard-kwaliteitseisen voldoet: *Versturen naar SABnzbd/qBittorrent*. Na versturen controleert MSO dat de client hem heeft. | Nee (versturen is niet terug te draaien) |
| **Extreme qBittorrent-waarden** | Torrentclient → *Voorstel berekenen* | Eén bundel die alle waarden buiten de veilige grens in één keer op de grens zet (geen meetperiode). Daarna verfijnt de tuning per waarde. | Ja, als geheel |

Herinneringen: een server met *Account verlopen*, of die langer dan `usenet.server_error_reminder_days` (standaard 3)
fouten geeft, krijgt een advies. *Automatisch versturen* van fallback-kandidaten (`recovery.auto_send_fallback`) staat
standaard **uit**. Voor MediaGuard-items komt er pas een voorstel als `herstel.allow_manual_send_connected` aan staat;
anders blijft MediaGuard de enige die verstuurt.

## 5. Providers en beschikbaarheid

SABnzbd geeft wachtwoorden van servers niet door. Wil je dat MSO zelf meet welke provider jouw artikelen heeft, vul dan
op de providerkaart eenmalig gebruikersnaam en wachtwoord in. Ze worden versleuteld opgeslagen (AES-256-GCM) en nooit
getoond of gelogd.

Na elke NZB die MSO zelf verstuurt, doet MSO een kleine **STAT-steekproef** (standaard 25 artikelen) bij elke provider
waarvan het inloggegevens heeft: 1 verbinding per provider, binnen een instelbaar dag- en maandbudget (verbindingen,
commando's, kB). De tabel *Beschikbaarheid en overlap* vult zich daarmee (gecontroleerd, beschikbaar, uniek, dekking,
vertrouwen). Dat geldt alleen voor jouw eigen artikelen en is geen garantie voor retentie.

## 6. Kosten

Providers (uit SABnzbd) en indexers (uit Prowlarr) verschijnen automatisch als dienst met *Prijs ontbreekt*. Je vult
alleen prijs, periode en verlengdatum in. Daarna zie je per dienst de kosten per geslaagde GB en per geslaagde download,
en bij providers welk deel van de gemeten artikelen alleen die provider had. MSO belooft geen besparing en zegt nooit
iets op.

## 7. Controle na een wijziging

Na het toepassen meet MSO tijdens een meetperiode. Daarbij gelden:
- standaard 24 uur, of de periode van het voorstel;
- vergeleken wordt met een basislijn van even lang ervóór;
- alleen verschillen groter dan de hysterese (standaard 10 %) tellen.

Wat er gemeten wordt:

| Doel | Maatstaven |
|---|---|
| qBittorrent | downloadsnelheid van actieve torrents, tijd tot voltooiing |
| SABnzbd | faalratio, tijd tot voltooiing |

Bij Automatisering → **Controle** staat per wijziging de uitkomst: *beter*, *gelijk*, *slechter*, *onvoldoende data* of
*niet van toepassing* (verwijderen of uitschakelen wordt niet gemeten), met basislijn naast het meetresultaat.

Bij *slechter* draait MSO de wijziging automatisch terug als de bijbehorende instelling aan staat
(`torrent.autotune_auto_rollback` voor qBittorrent, standaard aan; `automation.auto_rollback` voor de rest, standaard
uit). In de audit staat dat als `change.auto_rolled_back`, met de reden. Staat de instelling uit, dan krijg je een
voorstel om terug te draaien.

## 8. Prowlarr: MSO als indexer

Onder **NZB-indexers** / **Torrent-indexers** staat het paneel *Newznab/Torznab-endpoint* met de URL en de
API-sleutel (tonen/roteren door beheerder). *Registreer in Prowlarr* maakt een voorstel (toestemming nodig);
na goedkeuren en toepassen staat MSO als indexer in Prowlarr. MSO voorkomt dat Prowlarr ↔ MSO elkaar
eindeloos bevragen. Zet `MSO_PUBLIC_URL` als Prowlarr MSO via een ander adres bereikt dan je browser.

## 9. AI-adviseur (optioneel)

Standaard uit en budget € 0. Aanzetten onder **Automatisering → AI-adviseur**: API-sleutel, model,
maandbudget. De adviseur legt alleen uit en doet suggesties (risico *advies*); hij voert nooit iets uit.
Zonder AI krijg je dezelfde uitleg van de ingebouwde regels.

## 10. Back-up en herstel

**Back-up & updates**: maak een back-up (standaard zonder geheimen), of herstel er een (typ de
bestandsnaam ter bevestiging). Vóór elk herstel maakt MSO automatisch een vangnet-back-up. Een beschadigde
back-up wordt geweigerd en de database blijft dan ongewijzigd.

## 11. Rollen

*Kijker* leest alles; *operator* test, verstuurt handmatig en past toe/draait terug; *beheerder* beheert
koppelingen, instellingen, gebruikers, modus, goedkeuringen en back-ups.
