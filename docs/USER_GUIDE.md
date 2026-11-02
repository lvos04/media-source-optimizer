# Gebruikershandleiding — Media Source Optimizer

## 1. Eerste start

1. Open `http://<truenas>:30081`. Bij een lege installatie verschijnt **Setup**.
2. **Beheerder aanmaken** (≥ 10 tekens, hoofd/kleine letters of cijfers).
3. **Koppelingen**: vul per dienst de URL in (LAN-adres of containernaam, bijv. `http://192.168.1.10:9696`),
   daarna de sleutel/het wachtwoord. Klik **Test** (laat versie en mogelijkheden zien) en **Bewaar**.
   Na opslaan zie je alleen nog `ingesteld` — geheimen worden nooit meer getoond.
   - Prowlarr: API-sleutel (Settings → General).
   - SABnzbd: API-sleutel (Config → General).
   - qBittorrent: API-sleutel (≥ 5.2) of gebruikersnaam + wachtwoord.
   - MediaGuard (optioneel): een eigen MediaGuard-gebruiker met rol *operator* (aanbevolen) of *viewer*.
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

## 4. Prowlarr: MSO als indexer

Onder **NZB-indexers** / **Torrent-indexers** staat het paneel *Newznab/Torznab-endpoint* met de URL en de
API-sleutel (tonen/roteren door beheerder). *Registreer in Prowlarr* maakt een voorstel (toestemming nodig);
na goedkeuren en toepassen staat MSO als indexer in Prowlarr. MSO voorkomt dat Prowlarr ↔ MSO elkaar
eindeloos bevragen. Zet `MSO_PUBLIC_URL` als Prowlarr MSO via een ander adres bereikt dan je browser.

## 5. AI-adviseur (optioneel)

Standaard uit en budget € 0. Aanzetten onder **Automatisering → AI-adviseur**: API-sleutel, model,
maandbudget. De adviseur legt alleen uit en doet suggesties (risico *advies*); hij voert nooit iets uit.
Zonder AI krijg je dezelfde uitleg van de ingebouwde regels.

## 6. Back-up en herstel

**Back-up & updates**: maak een back-up (standaard zonder geheimen), of herstel er een (typ de
bestandsnaam ter bevestiging). Vóór elk herstel maakt MSO automatisch een vangnet-back-up. Een beschadigde
back-up wordt geweigerd en de database blijft dan ongewijzigd.

## 7. Rollen

*Kijker* leest alles; *operator* test, verstuurt handmatig en past toe/draait terug; *beheerder* beheert
koppelingen, instellingen, gebruikers, modus, goedkeuringen en back-ups.
