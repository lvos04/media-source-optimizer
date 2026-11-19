# Installeren als standaard TrueNAS Custom App

> **P-INSTALLATIE en de release zijn vrijgavevoorwaarden.** De stappen hieronder vóór vrijgave niet uitvoeren.
> **NIET GEVALIDEERD (echte NAS).** De [hardwarechecklist](HARDWARE_ACCEPTANCE.md) beschrijft de praktijkproef.

Installeer Media Source Optimizer (MSO) via het [standaard Custom App-formulier](../deploy/truenas/CUSTOM_APP.md).
De alles-in-één-image bevat PostgreSQL, API, worker en webinterface. De app gebruikt één webpoort en één
APPDATA-dataset. Kies bij een eerste installatie een eigen dataset en open MSO op de poort die je in het formulier
hebt bevestigd. Maak de beheerder aan in de MSO-wizard, test de koppelingen vanuit de app en begin in
**Alleen-lezen**.

Het formulier gebruikt de tag en registerdigest van de vrijgegeven image. Controleer de digest vóór ingebruikname.
De eerder gepubliceerde tags zijn ouder dan de bronstand van het manifest. Het
[containerbewijs](test-evidence/allinone-report.md) hoort bij de bronrevisies die daarin zijn genoemd.

## Updaten en terugdraaien

Doorloop eerst [Updaten via TrueNAS](#updaten-via-truenas). Gebruik het terugvalpad als de applicatie,
migratie of gegevenscontrole faalt.

## Updaten via TrueNAS

1. Plan een onderhoudsvenster. Leg de huidige formulierwaarden, imageversie met digest, schemaversie en de
   stand van gebruikers, instellingen, koppelingen, taken en audit vast. Leg geen geheime waarden vast.
2. Pauzeer automatisering, stop MSO via **Apps → Stop** en maak via **Datasets → Snapshots** een snapshot
   van de volledige APPDATA-dataset. Deze bevat onder meer PostgreSQL, de versleutelde kluis met het bijbehorende
   sleutelbestand, audit, back-ups en werkbestanden. Verifieer het herstelpunt vóór de update en bewaar een
   beschermde kopie buiten de oorspronkelijke dataset. Stop als de snapshot of de vorige imagepin ontbreekt.
3. Open de bestaande app met **Edit**. Behoud de huidige formulierwaarden en de bestaande koppeling van APPDATA
   aan `/appdata`. Kies de vrijgegeven versie met bijbehorende digest, sla op met **Update** en start MSO.
4. Voer de [digestcontrole](../deploy/truenas/CUSTOM_APP.md#na-het-opslaan) uit. Controleer bij schemamigraties
   het app-log voor de back-up **Vóór upgrade** en de migraties volgens het
   [gedrag van de gekozen image](MIGRATION.md#gedrag-per-imageversie).
   Pauzeer het gebruik als de back-up of migratie faalt en beoordeel het herstelpunt.
   Controleer daarna de schemaversie, aanmelding, instellingen, koppelingsstatus en de stand van taken en audit
   aan de hand van stap 1. Hervat automatisering na akkoord op de voor-en-na-vergelijking.

## Terugdraaien via TrueNAS

1. Pauzeer automatisering en kies **Apps → Stop**. Bewaar de actuele APPDATA-toestand en de logs apart,
   zodat ook wijzigingen sinds de upgrade bewaard blijven. Een snapshot terugzetten brengt de actieve app
   terug naar de stand van vóór de update.
2. Open **Datasets → Snapshots** en kies **Rollback** bij de vooraf gemaakte volledige APPDATA-snapshot.
   Deze zet de versleutelde kluis en de bijbehorende sleutel samen terug. Stop bij twijfel over het herstelpunt.
3. Open de app via **Apps** met **Edit**. Kies de vorige imageversie met digest en bewaar de bestaande mount.
   Sla op met **Update** en start MSO pas nadat de snapshot is teruggezet. Alleen de tag wijzigen laat de
   bijgewerkte database achter en is onveilig.
4. Controleer aanmelding, schemaversie, instellingen, koppelingen, taken en audit tegen de stand van vóór
   de upgrade. Houd rekening met intrekkingen van machine-sleutels sinds de snapshot. Trek die sleutels opnieuw
   in vóór je toegang voor afnemers herstelt. Hervat automatisering na akkoord op de gegevenscontrole.

## Problemen

| Melding of probleem | Oplossing |
|---|---|
| `FOUT: /appdata bestaat niet` | Controleer **Storage Configuration** en de mount op `/appdata`. |
| `FOUT: geen schrijfrechten op /appdata/…` | Controleer **Custom User** en of de eigen dataset toegankelijk is voor gebruiker 568. |
| Poort bezet | Bevestig een andere vrije **Host Port**. De **Container Port** blijft 38081. |
| Image kan niet worden opgehaald | Controleer de vrijgegeven repository, tag en digest. Houd bij een upgrade het herstelpunt intact. |
| Koppeling weigert een loopback-adres | Gebruik het adres waarop de betreffende dienst vanaf MSO bereikbaar is. |

Zie [Schemamigraties, upgrades en rollback](MIGRATION.md) voor het versievoorbeeld met schema 501 en de
expertprocedures met `docker exec`.
