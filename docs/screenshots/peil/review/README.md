# Peil-review (stap 5): metingen en schermafdrukken van de MSO-web-UI

Dit is de onafhankelijke ontwerpreview van de **echte app** op de teststack uit `tests/stack/run.sh`. De stack draaide
met dezelfde mocks als stap 2 en 3 en was voorbereid zoals `make e2e`. Na elke reparatie is het web-image opnieuw
gebouwd. Alle afdrukken en metingen in deze map komen van de eind-HEAD van de review: **code `e7e88e2`**. De toestanden inloggen, eerste start en laden zijn opnieuw gemeten op `8571054`; alleen die drie verandert die commit. De
toelichting staat in [`docs/ontwerp/PEIL_REVIEW.md`](../../../ontwerp/PEIL_REVIEW.md) en de keuzes in ADR-0008.

| Bestand of map | Inhoud |
|---|---|
| `review.mjs` | Meetscript, los van de bouwstap geschreven. Gebruikt Playwright 1.65 en axe-core 4.14 uit `.lokaal`. Meet axe op alle impacts, contrast in de DOM, UI-grenzen, overloop, aanraakdoelen, tekstgrootte, ellips, structuur, Peil-semantiek en woorden, plus een Tab-doorloop. |
| `toevoegingen.mjs` | Test de toevoegingen. "Terug naar de Hub" gaat via het **echte** `GET/PUT /api/v1/settings`: 6 ongeldige adressen moeten 422 geven, een geldig adres moet de link tonen, een lege waarde moet hem weghalen. Daarna wordt de oorspronkelijke waarde teruggezet. Ook getest per grafiek: tabelweergave en toetsenbord. |
| `extra.mjs` | 200 %-zoom, forced colors en reduced motion, **alleen in Chromium-emulatie**. |
| `verklein.py` | Zet de PNG's om naar een palet van 256 kleuren. Meet daarom nooit contrast in de PNG's. |
| `metingen.json` | Metingen per scherm × breedte × thema. Groepen: `schermen` (16 × 320/360/390/768/1024/1440 × 2), `tabs` (12 × 320/390/1440 × 2), `beheer` (16 × 390/1440 × 2, Beheermodus nagebootst), `toestanden` (22 toestanden en dialogen) en `toetsenbord` (16 × 390/1440 × 2). |
| `metingen-voor-reparatie.json` | Dezelfde metingen op HEAD `c3dc933`, vóór de reparaties. Gemaakt met de eerste versie van het script. Twee meetfouten zijn daarna verbeterd: legendablokjes telden als groen, en een verloop onder de merkknop telde mee als achtergrond. Zie PEIL_REVIEW.md §1. |
| `toevoegingen.json`, `extra.json` | Uitkomsten van de twee aanvullende scripts. |
| `schermen/` | Afdrukken van de volledige pagina: 16 schermen × 360/390/768/1440 × licht/donker (128). 320 en 1024 zijn alleen gemeten. |
| `toestanden/` | Toestanden en dialogen op 390/1440 × licht/donker. Nagebootst (door een API-antwoord in de browser te onderscheppen): eerste start, laden, fout, niet ingesteld, offline, gepauzeerd, verouderd, Beheermodus voor de pauze- en de terugzetdialoog, en `hub-link` (het adres is een gereserveerde `.test`-naam). In `metingen.json` staat bij zo'n afdruk `nagebootst: true`. |
| `forced-colors/` | Vier schermen × 390/1440 in geëmuleerde forced colors. |

**Let op bij `toestanden/grafiek-*-tabel-*`:** in deze afdrukken staat de vaste kopbalk midden in het beeld. Om de
tabellen te openen scrollt het script de pagina eerst, en Playwright legt een sticky kopbalk vast op de
scrollpositie van dat moment. Dat komt dus door de manier van afdrukken en is geen fout in de app.

**Niet gedaan:** een echte telefoon, een echte schermlezer en een echt Windows-contrastthema. MSO heeft geen
Android-app en geen en-US-teksten.
