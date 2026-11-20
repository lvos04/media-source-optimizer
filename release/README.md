# Bewijs bij een MSO-release

Bij een wijziging van een basisimage geeft `docker buildx imagetools inspect
<image>:<versie>` de digest van de image-index. Neem de exacte tag en die digest
samen op in de Dockerfiles die de basisimage gebruiken. De CI controleert de
digests met `python scripts/check_dockerfile_digests.py`.

De releaseworkflow maakt van de drie release-images vóór de push een SBOM in
SPDX-JSON. `pip-audit` controleert de daadwerkelijk geïnstalleerde
Python-afhankelijkheden in alles-in-één en core. Een mislukte audit blokkeert
de push naar het register. De web-image bevat geen Python-applicatie.

Na de push van de drie images schrijft de workflow `release/digests.json`.
Elke sleutel is de volledige imagereferentie onder `git.medialeon.nl/leon`, inclusief versietag. De
waarde is de SHA-256-digest die het register voor die push bevestigt. Het
workflowartefact `release-bewijs-<versie>` bevat het digestbestand, de drie
SBOM's en de auditrapporten. Het testimage met mocks dient voor de lokale
containerproef.

Voor de release van deze kandidaat blijft BLOK-06 open. Het installatiemanifest
van S-P3-290 gebruikt tot die release `digest_pending_release`.
