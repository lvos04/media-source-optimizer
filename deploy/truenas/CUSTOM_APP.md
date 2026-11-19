# Standaard Custom App voor Media Source Optimizer

> **P-INSTALLATIE en de release zijn vrijgavevoorwaarden.** De stappen hieronder vóór vrijgave niet uitvoeren.
> **NIET GEVALIDEERD (echte NAS).** Zie de [hardwarechecklist](../../docs/HARDWARE_ACCEPTANCE.md).

## Vooraf

- Bevestig de TrueNAS-versie en het formulier (D01), de dataset (D02), een vrije hostpoort (D03) en de
  doelversie uit het releasebewijs (D07). De tagplaatshouder uit het [manifest](../manifest/media-source-optimizer.manifest.yaml)
  heeft nog geen registerdigest. Wacht op de vrijgegeven image met gecontroleerde tag én digest.
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
| | Tag | `<VERSIE>@sha256:<DIGEST>` uit het releasebewijs. Als het formulier het digestdeel weigert, vul de exacte tag in en controleer na het ophalen de registerdigest vóór ingebruikname. Stop bij een afwijking of als de digest oncontroleerbaar is. |
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
| Resources Configuration | CPUs / Memory | 2 CPU / 1536 MiB. |
| | GPU | Uit. |

## Na het opslaan

1. Controleer de appstatus en het opstartlog. **Running** en **Healthy** horen bij een geslaagde start.
2. Open de portal. Controleer op dezelfde webpoort dat `/readyz` HTTP 200 geeft. Het monitorplan gebruikt een
   time-out van 8 seconden en een interval van 30 seconden.
3. Rond bij een eerste installatie de setupwizard af in MSO. Bevestig dat de wizard sluit en dat opnieuw
   inloggen werkt. Configureer eventuele koppelingen in MSO en begin in **Alleen-lezen**.
4. Volg bij een upgrade de [controles na migratie](../../docs/MIGRATION.md#updaten-via-truenas) voordat je het
   gebruik hervat. Laat het herstelpunt beschikbaar.
