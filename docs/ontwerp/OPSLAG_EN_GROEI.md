# MSO · Systeem · Opslag en groei

**Status:** ontwerp voor S-P3-270 (B05-004), 19 november 2026.
**Ontwerptaal:** Peil 1.1.1, [versie en herkomst](../../design/ontwerptaal/HERKOMST.md).
**Bronnen:** [Peil-migratie](PEIL_MIGRATIE.md), [ADR-0003](../adr/ADR-0003-contractafwijkingen-bestaande-repo.md) en [deltamatrix §11.5](../platform-v2/DELTA_MATRIX.md).

## 0. Samenvatting

De kijker ziet onder Systeem wat MSO aan opslag en databaseverbindingen gebruikt, waar de cijfers vandaan komen
en hoeveel ze gemiddeld per kalenderdag veranderen. De kaart sluit aan op Onderhoud en Database en gebruikt
hetzelfde leesverzoek. Metingen, trend en bron staan in tekst. De bestaande kaarten behouden hun eigen functie.

## 1. Schermopbouw

| Onderdeel | Weergave |
|---|---|
| Opslag en groei | Bestaande `Card` als h2 met een decoratief lucide-icoon. De kaart gebruikt de volle breedte en staat na de onderhouds- en databasekaarten. |
| Laatste meting | Datum en tijd boven de tabel, met datum, waarde en eenheid per meetreeks. Kolommen voor waarde, bron en kwaliteit en verandering per dag. Bron en kwaliteit staan in tekst. |
| Dagmetingen | De bestaande gestapelde tabelweergave is uitklapbaar en toont per UTC-dag de database, back-ups, vrije ruimte in de app-gegevensmap en databaseverbindingen. De nieuwste dag staat bovenaan. |
| Trend | Gemiddeld verschil tussen de eerste en laatste beschikbare meting per reeks, gedeeld door de verstreken UTC-kalenderdagen in de laatste 30 dagen. De trend toont de gemeten periode per reeks. |

De kaart gebruikt de Peil-tokens en bestaande tabelprimitieven. Op een smal scherm blijven waarde, eenheid, bron en
trend bij het meetlabel staan. Tabelkoppen hebben een expliciet bereik. De tabelregio is bereikbaar met het toetsenbord
en heeft een zichtbare focusring. Interactieve doelen zijn minstens 44 px. Tijden gebruiken de i18n-opmaak van MSO.

## 2. Toestanden en kernteksten

| Toestand | Component en tekst |
|---|---|
| Bezig | `ResourceView` met de bestaande laadweergave. |
| Leeg | `StateView` met “Nog geen metingen” en “MSO meet dagelijks.” |
| Storing | `ResourceView` met de bestaande storingsmelding en de actie om opnieuw te proberen. |
| Eén dag | De meetwaarden zijn zichtbaar. `InlineState` toont “Te weinig metingen voor een trend.” |
| Ontbrekende dag of bron | “Geen meting” op die dag, “Onbekend” met reden bij een bronwaarde die ontbreekt. |
| Meerdere dagen | De actuele dagwaarden en de verandering per dag in dezelfde eenheid. “Gemeten” en “Geschat” hebben een neutrale tekstkleur. |

## 3. Herkomst en grenzen

PostgreSQL levert de klok, de database- en tabelgrootte en het aantal verbindingen van deze database. Voor de
app-gegevensmap gebruikt MSO dezelfde schijfruimtefunctie als Systeem → Gezondheid. De back-upadministratie levert
de omvang van geregistreerde back-ups en `backups.space_status()` levert de beschikbare ruimte voor back-ups.
De header-index levert een gemeten fysieke grootte en een geschatte logische grootte via `usage()`. De bestaande
momentopname uit het torrent-opslagrapport levert de gemeten vrije ruimte als die beschikbaar en vers is.
Achtergrondtaken registreren numerieke meetwaarden en bronmomenten. Namen van bestanden en paden komen niet mee
in de nieuwe meetreeks. Containerlogvolume heeft de waarde “Onbekend” met de reden dat de containerlog buiten MSO valt.

De nieuwe dagmetingen hebben een bewaartermijn van 400 UTC-dagen via het bestaande onderhoud. De twaalf tabellen
uit de deltamatrix houden hun huidige bewaarbeleid. De migratie gebruikt de bestaande pre-upgradeback-up en de
terugweg verwijdert alleen de nieuw toegevoegde tabel. De integrale herstelproef start vanaf de gepubliceerde image.

## 4. Controle tijdens bouw en review

De kerntest gebruikt 30 gesimuleerde dagen met een ontbrekende dag, een teruglopende klok en een gegevensherstelproef.
De browserproef controleert leeg, bezig, storing, één meting, een gat en de bronlabels. De bestaande Peil-matrix
op Systeem meet beide thema’s op 320, 360, 390, 768, 1024 en 1440 px. De gecombineerde review beoordeelt het ontwerp.
