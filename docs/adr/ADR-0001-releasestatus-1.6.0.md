# ADR-0001 — Releasestatus van 1.6.0 binnen TrueNAS Home Platform V2

Status: **Aangenomen (fase P0)** · Datum: 2026-11-09 · Excel: B05-001, I04-04-01, I04-04-02

## Context

Het uitvoeringspakket (B05, I04 stap 2, A19) is geschreven toen `release/VERSION` nog 1.5.0 was. Het zegt: "1.6.0 niet
als productie-tag aannemen" en "aanbevolen image `…:1.5.0` als werkelijke release". In fase P0 is vastgesteld
(`docs/platform-v2/INVENTARIS.md` §1):

- `release/VERSION` = 1.6.0, tag `v1.6.0` → `018b119`, all-in-one-image `ghcr.io/lvos04/media-source-optimizer:1.6.0`
  (`sha256:9bcdec69…3da`) is publiek; de release-workflow (ruff, pytest, alles-in-één-test 10/10) slaagde.
- `ci.yaml` (web-build, containerstack, Playwright) heeft nooit op GitHub gedraaid.
- De echte-hardwarechecklist (`docs/HARDWARE_ACCEPTANCE.md`) is voor **geen enkele** versie uitgevoerd, ook niet voor
  1.5.0. De draaiende versie op de NAS is onbekend (D07).

De acceptatie van B05-001 luidt: "Geen 1.6.0 als stable deployen zonder build, migratietest en hardwareacceptatie."

## Besluit

1. 1.6.0 heet in alle platformdocumenten, het manifest en de Hub een **"CI-geteste release-kandidaat"**, niet "stable".
   `validation_status` = `NIET GEVALIDEERD (echte NAS)`. Hetzelfde geldt voor 1.5.0: ook die heeft geen
   hardwareacceptatie, dus "1.5.0 = werkelijke release" (I04 stap 2) is evenmin bewezen.
2. Welke versie op de NAS komt (1.5.0 of 1.6.0) is een **gebruikersbesluit** binnen D07, genomen met de informatie uit
   `DELTA_MATRIX.md` §8. De bouwer kiest dat niet. 1.6.0 bevat veiligheidsverbeteringen (back-up vóór upgrade,
   driftveilige rollback, noodrem); 1.5.0 heeft minder code maar ook geen live-bewijs.
3. Een versie mag pas "stable" heten na: (a) groene `ci.yaml` op GitHub, (b) echte upgradetest vanaf de vorige
   gepubliceerde image met data (P3-03), (c) startweigering bij een nieuwer schema (P3-01) en (d) uitgevoerde
   hardwareacceptatie met bewijs. (d) is in deze bouwomgeving onmogelijk en blijft **Geblokkeerd**.
4. De bouwer tagt of publiceert niets en wijzigt `release/VERSION` niet (een push daarvan start `release.yaml` op elke
   branch). Een nieuwe release is een expliciete beslissing van de gebruiker.

## Gevolgen

- EVIDENCE: B05-001 blijft `Bezig` met blokkade "hardwareacceptatie (echte NAS)"; nooit `Gereed`.
- P3-02 maakt de status zichtbaar in de app (`/api/v1/system/info`: `channel`, `git_sha`, `build_time`), zodat de Hub
  de draaiende build kan teruglezen in plaats van aan te nemen.
- Het pakket (I04 stap 2) is op dit punt achterhaald → gemeld als contractpunt.
