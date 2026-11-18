# ADR-0010 — Pollingcursor van de MSO-connector

## Besluit

S-P3-230 gebruikt optie A uit het bouwbesluit. Een verlopen cursor op
`GET /connector/v1/events` geeft **HTTP 409 `cursor_expired`** in het
foutformaat van contract §5, met `details.restart: true`. De poller begint
expliciet opnieuw met een verzoek zonder cursor. Dit volgt S-P3-620. Een
syntactisch ongeldige cursor geeft 422 `validation_error`.

## Polling

Behandel `next_cursor` als een waarde van de dienst. Sla haar op nadat de
gebeurtenissen van de pagina zijn verwerkt. Een lege pagina geeft nog steeds
een cursor. Een tijdelijke 503 draagt `Retry-After`. Hervat dan met dezelfde
cursor. Dubbele levering is mogelijk. Dedupliceer volgens contract §7.

Het auditlog en het automatiseringslog hebben elk een eigen oplopende positie.
De bronnaam onderscheidt gelijke rijnummers. Sinds migratie 0609 krijgt elke
nieuwe automatiseringsregel bij invoer een eigen UUID. Die wordt ook in de
back-up bewaard. Bestaande regels en het append-only auditlog behouden hun
event-ID op basis van bron, nummer en oorspronkelijke waarnemingstijd.
Een korte gedeelde PostgreSQL-tabelvergrendeling laat een poll alleen door als er geen schrijvende
transactie op deze logboeken openstaat. Daardoor kan de poll geen lager
rijnummer overslaan dat later wordt vastgelegd. Bij een bezet logboek geeft de
dienst 503. Een gat door een teruggedraaide transactie vraagt geen herstart.

Retentie bewaart in dezelfde transactie een revisie en het hoogste verwijderde
rijnummer uit het automatiseringslog. Een gewijzigd of verdwenen anker of mogelijke
overlap met intussen verwijderde gebeurtenissen geeft 409. De controle kiest
bij twijfel voor opnieuw beginnen. Bij native back-upherstel schrijft MSO een nieuwe
UUID voor de eventhistorie in dezelfde transactie die de leesroutes weer vrijgeeft.
Dat gebeurt ook bij de afhandeling van een onderbroken herstel. Tijdens herstel
geven polling en bewijs 503. Daarna geeft iedere cursor uit de vorige historie
409, ook als beide ankerrijen nog bestaan.

## Leescontract

`discover` adverteert alleen `read`. De drie POST-routes geven 501 aan een
geldig aangemelde sessie met CSRF. Machines hebben uitsluitend toegang tot de
vijf leesroutes met scope `read`.

De versheidsgrenzen staan onder `discover.limits.max_age_s`. Bij koppelingen en
de worker is `observed_at` de laatste bronmeting. De back-upcheck meet bij elke
aanvraag de leeftijd van de laatste back-up. Die brontijd staat in
`detail.laatste_backup`. Een back-up ouder dan twee dagen maakt de check
`degraded`. Bovenaan `health` staat de oudste beschikbare meettijd over de
checks. Een toekomstige bronmeting telt als `unknown`. Het antwoord `/readyz` toetst lokale afhankelijkheden en vereist
geen Hub-koppeling.

De itemtypes `connector` en `change_set` hebben een bron en een relatieve
deeplink. Zonder typefilter toont `items` eerst de koppelingen en vervolgens de
wijzigingsvoorstellen. Op de laatste pagina is `next_cursor` leeg.

Gebruik `payload.id` ongewijzigd als padparameter voor `GET /connector/v1/evidence/{id}`.
Het ID bevat de bron, het rijnummer en de gebeurtenis-UUID. Als een
teruggezette database een nummer opnieuw uitgeeft, geeft de oude bewijslink
404. Bewijs met een UUID blijft leesbaar zolang de bijbehorende gebeurtenis
bestaat. Oudere links met alleen bron en nummer zijn geldig tot het eerste
native herstel. Vindt de upgrade al een herstel in het auditlog, dan vervallen
ook de oude cursors en links met alleen een rijnummer. De bewijsprojectie bevat ook het eventtype en
het stabiele onderwerp. Voor back-ups hebben mislukking en herstel hetzelfde onderwerp.
