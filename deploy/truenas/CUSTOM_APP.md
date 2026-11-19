# Standaard Custom App voor Media Source Optimizer

> **P-INSTALLATIE en de release zijn vrijgavevoorwaarden.** De stappen hieronder vóór vrijgave niet uitvoeren.
> **NIET GEVALIDEERD (echte NAS).** Zie de [hardwarechecklist](../../docs/HARDWARE_ACCEPTANCE.md).

## Vooraf

- Bevestig de TrueNAS-versie en het formulier (D01), de dataset (D02) en een vrije hostpoort (D03).
  Het beschreven ix-app-formulier (1.4.8) vereist minimaal TrueNAS SCALE 24.10.2.2.
- Leg bij een upgrade de huidige installatie (D07) vast, inclusief image-tags, API-versies,
  configuratieback-ups, gezondheid en actief gebruik.
- Gebruik de doeltag en registerdigest uit het releasebewijs. De tagplaatshouder uit het
  [manifest](../manifest/media-source-optimizer.manifest.yaml) heeft nog geen registerdigest. Wacht op de vrijgegeven image.
- Laat de optionele wizardwaarden RPO (maximaal dataverlies) en RTO (maximale hersteltijd) open tot bevestiging (D16).
- Kies bij een eerste installatie in **Datasets → Add Dataset** een eigen dataset met de preset **Apps**.
  Behoud bij een upgrade de bestaande volledige APPDATA-dataset en de huidige formulierwaarden. Maak vooraf
  een herstelbare snapshot volgens de [upgrade- en terugvalroute](../../docs/TRUENAS_CUSTOM_APP.md#updaten-en-terugdraaien).
- Gebruik een vertrouwd lokaal netwerk. Maak de beheerder direct bij het eerste gebruik in de MSO-wizard aan.

## Formulier

Ga naar **Apps → Discover Apps → Custom App**. Open bij een upgrade de bestaande app met **Edit**.

| Sectie | Veld | Waarde |
|---|---|---|
| Application Name | | `media-source-optimizer` bij een eerste installatie. |
| Image Configuration | Repository | `ghcr.io/lvos04/media-source-optimizer` |
| | Tag | `<VERSIE>@sha256:<DIGEST>` uit het releasebewijs. Weigert het formulier het digestdeel, vul dan de exacte tag in. Voer in beide gevallen de [digestcontrole](#na-het-opslaan) uit. |
| | Pull Policy | *Pull the image if it is not already present on host*. |
| Container Configuration | Entrypoint / Command / Environment Variables | Leeg laten bij een eerste installatie. Laat bij een upgrade de bestaande instellingen staan. |
| | Restart Policy | *Unless Stopped*. |
| | Timezone | De door de beheerder bevestigde tijdzone. |
| Security Context | Privileged / Custom User | Beide uit. De image bereidt de eigen opslag voor en laat de processen draaien als 568:568. |
| | Capabilities | Voeg niets toe. |
| Network Configuration | Host Network | Uit. |
| | Ports → Add | Container Port `38081`, Host Port standaard `38081` (vrije poort bevestigen), Protocol TCP. |
| Portal Configuration | Add | Naam `MSO`, Protocol HTTP, Port de gekozen hostpoort, Path `/`. |
| Storage Configuration | Add → Type | **Host Path**, met **Read Only** uit. |
| | Mount Path | `/appdata` |
| | Host Path | Selecteer de eigen dataset met de datasetkiezer, bijvoorbeeld `/mnt/<POOL>/apps/media-source-optimizer`. Selecteer bij een upgrade exact de bestaande dataset. |
| Resources Configuration | Enable Resource Limits | Aan. |
| | CPUs / Memory | 2 CPU / 1536 MiB. |
| | GPU | Uit. |

Bij een register dat aanmelding vereist, configureer je de aanmelding uitsluitend in de
**TrueNAS-registerinstellingen**, met een toegangstoken waarmee je alleen pakketten mag lezen. Gebruik voor de
publieke image anonieme toegang. Registry-login is **NIET GEVALIDEERD (echte NAS)**.

## Na het opslaan

1. Voer na elke installatie en update de digestcontrole uit. Deze geldt voor beide tagroutes. Lees via de
   TrueNAS-API (`app.image.query`) de `repo_digests` van de lokaal opgehaalde MSO-image terug. Vergelijk de
   digest voor de opgegeven repository met het releasebewijs. Stop bij afwijking of als de teruglezing ontbreekt.
   Neem MSO pas in gebruik nadat de digest is bevestigd.
2. Controleer de appstatus en het opstartlog. **Running** en **Healthy** horen bij een geslaagde start.
3. Open de portal. Controleer op dezelfde webpoort dat `/readyz` HTTP 200 geeft. Het monitorplan gebruikt een
   time-out van 8 seconden en een interval van 30 seconden.
4. Rond bij een eerste installatie de setupwizard af in MSO. Bevestig dat de wizard sluit en dat opnieuw
   inloggen werkt. Configureer eventuele koppelingen in MSO en begin in **Alleen-lezen**.
5. Volg bij een upgrade de [controles na migratie](../../docs/MIGRATION.md#updaten-via-truenas) voordat je het
   gebruik hervat. Laat het herstelpunt beschikbaar.
