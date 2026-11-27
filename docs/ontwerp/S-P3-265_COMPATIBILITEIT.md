# Ontwerpreview van de MediaGuard-compatibiliteitstabel

De review van 27 november 2026 betreft de OpenAPI-rij uit S-P3-265 (`c6f21d3`) en de weergave voor iemand met leesrechten. De beoordeelde tabel komt uit de oorspronkelijke bouwstap.

**De twee statuskolommen beantwoorden verschillende vragen.** In **Broncode** staat of de route in de referentiecode is bevestigd. In **Deze installatie** staat wat de verbinding met de huidige rol heeft vastgesteld. Bij een afgeschermde OpenAPI-route toont de tabel de neutrale chip **Alleen voor beheerder**. De tekst verklaart de toegangsgrens ook zonder kleur. Een rood signaal voor een ontbrekende route zou hier een verkeerde herstelactie suggereren.

**Op mobiel blijven de labels bij de waarden.** In de opname staat bij de vereiste MediaGuard-rol `admin`. De bronverificatie en de toegang tot de installatie hebben elk een eigen regel. De twee chips zijn ook op de smalle weergave afzonderlijk leesbaar. De opname komt uit de platformreview van 25 november, vastgelegd in de Hub op `d8e6ec3`. [Mobiele OpenAPI-rij](../screenshots/peil/review/s-p3-265-openapi.png)

**De component bewaart dezelfde betekenis op de desktop.** De kolomkoppen staan in een tabelkop. Bij stapeling neemt iedere cel haar eigen kolomlabel mee. De tabelregio heeft een toegankelijke naam en kan met het toetsenbord worden bereikt. De componentproef in `apps/web/tests/unit/schermtoestanden.test.tsx` voert de combinatie van een geverifieerde bron en beperkte toegang daadwerkelijk door de renderer. De bronproef in `apps/core/tests/test_mediaguard_probe.py` controleert welke routes een viewer en een operator werkelijk kunnen bevestigen.

Voor de OpenAPI-rij is geen tekst- of stijlwijziging nodig. De aparte vraag welke zoekmogelijkheden MediaGuard aan een leesrol kan bevestigen, ligt bij het API-contract. De aanvullende browseropnamen en metingen bij deze review horen bij het bouwrapport VV-MSO.
