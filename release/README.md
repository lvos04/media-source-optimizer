# Bewijs bij een MSO-release

Bij een wijziging van een basisimage geeft `docker buildx imagetools inspect
<image>:<versie>` de digest van de image-index. Neem de exacte tag en die digest
samen op in de Dockerfiles die de basisimage gebruiken. De CI controleert de
digests met `python scripts/check_dockerfile_digests.py`.

De releaseworkflow maakt van de drie release-images vóór de push een SBOM in
SPDX-JSON. `pip-audit` controleert de daadwerkelijk geïnstalleerde
Python-afhankelijkheden in alles-in-één en core. Een mislukte audit blokkeert
de push naar het register. De web-image bevat geen Python-applicatie.

## Publiceren op GitHub

Zet uitsluitend op de beoogde broncommit een nieuwe tag die exact gelijk is aan `v` gevolgd door
`release/VERSION`. Voor deze kandidaat is dat `v1.6.1`. De workflow **release**
(`.github/workflows/release.yaml`) test de software en de upgrade vóór publicatie. De gewone GitHub-CI draait bij branchwijzigingen en pull requests. Een bestaande GitHub Release, bezette imagetag of onduidelijk registerantwoord blokkeert
de publicatie. Bij een onderbroken push kunnen enkele tags al bestaan. Controleer dan welke images en
digests bevestigd zijn en gebruik voor een nieuwe bouw een nieuw versienummer. Verplaats geen oude git-tag.

De release publiceert exact de versietag op `ghcr.io/lvos04/media-source-optimizer` (alles-in-één),
`ghcr.io/lvos04/media-source-optimizer-core` en `ghcr.io/lvos04/media-source-optimizer-web`.
De workflow schrijft `release/digests.json` en voegt dat bestand als **digests.json** met de SBOM's
en auditrapporten toe aan de GitHub Release. Elke JSON-sleutel is de volledige imagenaam met versietag.
De waarde is de SHA-256-registerdigest die de push bevestigde. Het workflowartefact
`release-bewijs-<versie>-<poging>` bevat dezelfde bewijsbestanden.

Als de job `publish` groen is en de job `release` faalt, kies in dezelfde run **Re-run failed jobs**.
De releasejob haalt het bewijs op via het artefact-ID van de voltooide push. Zo kunnen de bijlagen
bij dezelfde tag worden gepubliceerd zonder een tweede imagepush.

Controleer na publicatie dat de drie versies publiek op digest kunnen worden opgehaald. Vul in het
[installatiemanifest](../deploy/manifest/media-source-optimizer.manifest.yaml) de digest van de
**alles-in-één-image** in bij de exacte tag en verwijder de bijbehorende `digest_pending_release`-uitzondering.
Voor een bestaande installatie geldt [Bijwerken via de webinterface](../deploy/truenas/BIJWERKEN.md).
