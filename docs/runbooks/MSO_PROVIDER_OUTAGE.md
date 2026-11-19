# MSO provider outage

## 1. Observeren

Open **Overzicht → Koppelingen** en **Usenet-providers → Serverprioriteit**. Noteer wanneer de storing begon,
of één provider of de hele SABnzbd-koppeling geraakt is en of de UI een time-out, verbindingsfout of geweigerde
aanmelding meldt. Vergelijk dit met de serverstatus in SABnzbd.

## 2. Isoleren

Controleer onder **Systeem → Gezondheid** of MSO en zijn database gezond zijn. Bekijk bij een gezonde
SABnzbd-koppeling de melding van de getroffen server. Zet bij twijfel MSO op **Alleen-lezen** of pauzeer
Autopilot. Houd minstens één bekende werkende server beschikbaar.

## 3. Mitigeren

Controleer of de provider een storing meldt, of het account nog geldig is en of SABnzbd de server kan
bereiken. Los de oorzaak bij de provider of in SABnzbd op. Gebruik daarna **Usenet-providers →
Serverprioriteit → Testen** en controleer of SABnzbd de bron daadwerkelijk weer gebruikt. Houd
automatisering gepauzeerd tot de leescontroles stabiel zijn.

## 4. Terugdraaien

Bekijk onder **Wijzigingen** welk voorstel vlak vóór de storing is toegepast. Gebruik het terugdraaivoorstel
voor die wijziging en controleer de oude en huidige waarden vóór goedkeuring. Laat een waarde die buiten
MSO is veranderd eerst door de beheerder beoordelen. Volg na een mislukte MSO-upgrade het
[terugvalpad met APPDATA-snapshot](../MIGRATION.md#terugdraaien-via-truenas).

## 5. Bewijs

Leg het begin, de diagnose, het wijzigings-ID, de uitgevoerde maatregel en het herstelmoment vast.
Bewaar de relevante, afgeschermde auditregels. Controleer na herstel koppelingsstatus, serverstatus en
de wachtrij. Noteer of de bron weer downloads afhandelt en wanneer automatisering is hervat.

## 6. Oorzaak

Leg vast of de oorzaak bij de provider, de verbinding, het account, SABnzbd of een MSO-wijziging lag.
Benoem wat herhaling voorkomt en wie dat opvolgt. Laat de oorzaak open als de waarnemingen onvoldoende zijn.
