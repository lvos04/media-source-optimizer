# ADR-0004 — Interpretaties uit de tweede lezing van de delta-matrix

Status: **Aanvaard (fase P0, tweede lezing)** · Datum: 2026-11-09 · Excel: B05-004, B05-009, B05-010 · EX19
Zie `docs/platform-v2/DELTA_MATRIX.md` §11.

## Context

Bij de tweede, sceptische lezing van de delta-matrix bleken vier punten waar het pakket of het contract niet eenduidig
is. Steeds is de veiligste interpretatie gekozen, zonder bestaande integriteit, rollback of monitoring te vervangen.

## Besluiten

1. **`validatie` in `EVIDENCE.json` (contract §11).** Het contract noemt een lijst toegestane waarden; het voorbeeld
   bevat er één. Voor MSO gelden vaak twee tegelijk (simulator geslaagd én niet op de NAS gevalideerd).
   *Besluit:* `validatie` bevat alleen waarden uit die lijst, gescheiden door `"; "` (bijv. `Sim-getest; NIET
   GEVALIDEERD (echte NAS)`). Toelichting staat in `opmerking`, bewijs in `bewijs`. Zo kan vrije tekst nooit als claim
   gelezen worden. Het samenvoegscript moet zulke lijsten aankunnen (gemeld aan de Excel-eigenaar).

2. **B05-010 "na terugzet blijft de index aanwezig".** De Usenet-header-index is groot en volledig opnieuw op te halen;
   hij zit bewust niet in de back-up (`REFETCHABLE`, `apps/core/mso/backups.py:63`). Na herstel wordt de cursor
   teruggezet en bouwt de geplande job hem opnieuw op vanaf de nieuwste headers (`backups.py:377-394`, getest).
   *Besluit:* de acceptatie geldt voor de index als "wordt automatisch herbouwd (cache)", niet als "bit-voor-bit
   aanwezig". Geen opname in de back-up. Gevolg, eerlijk vermeld: direct na herstel is de index leeg tot de volgende
   geplande run, en headers buiten het opslagbudget komen niet terug. Eventueel (P2) de run direct na herstel inplannen.

3. **Eigen Ed25519-identiteit (contract §3) voor een bestaande app.** Het contract vraagt die voor "elke app"; MSO heeft
   hem niet. *Besluit:* MSO maakt hem in P3-06 bij de eerste start aan als hij ontbreekt (`/appdata/secrets/`, 0600,
   eigenaar 568, nooit overschrijven) en gebruikt hem alleen voor de Hub-koppeling. Lokale accounts en de bestaande kluis
   blijven ongewijzigd.
   *Bijgewerkt 2026-11-10 (ADR-0009 punt 6, 1.2-punt MS1):* zonder enrollment (contract §2.3 regel 2) is deze
   identiteit in P3-06 **niet** nodig; ze komt pas als MSO ooit ondertekende verzoeken verifieert of pusht.

4. **Vervaltermijn op goedkeuringen (07_API "approval record", EX19).** MSO-goedkeuringen vervallen nu nooit; drift bij
   de bron wordt wel geweigerd. *Besluit:* goedkeuringen die via de Hub binnenkomen (connectorlaag, P3-08) krijgen een
   vervaltermijn naast `preview_hash`; de termijn wordt in P3-08 met de Hub-bouwer vastgelegd. Goedkeuringen in de eigen
   MSO-UI houden hun huidige gedrag (de driftcontrole blijft), om geen regressie in een geteste app te veroorzaken.

## Gevolgen

- P3-12 vervalt als bouwwerk; P3-06 en P3-08 worden iets groter (zie `DELTA_MATRIX.md` §11.5).
- Geen wijziging aan productcode in deze fase.
