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
De bronnaam onderscheidt gelijke rijnummers. De oorspronkelijke rijtijd
onderscheidt gebeurtenissen als een teruggezette database een rijnummer opnieuw
uitgeeft. Een korte gedeelde
PostgreSQL-tabelvergrendeling laat een poll alleen door als er geen schrijvende
transactie op deze logboeken openstaat. Daardoor kan de poll geen lager
rijnummer overslaan dat later wordt vastgelegd. Bij een bezet logboek geeft de
dienst 503. Een gat door een teruggedraaide transactie vraagt geen herstart.

Retentie bewaart in dezelfde transactie een revisie en het hoogste verwijderde
rijnummer uit het automatiseringslog. Een gewijzigd of verdwenen anker of mogelijke
overlap met intussen verwijderde gebeurtenissen geeft 409. De controle kiest
bij twijfel voor opnieuw beginnen. De bronnen en retentiestand gebruiken het
bestaande databaseschema.

## Leescontract

`discover` adverteert alleen `read`. De drie POST-routes geven 501 aan een
geldig aangemelde sessie met CSRF. Machines hebben uitsluitend toegang tot de
vijf leesroutes met scope `read`.

De versheidsgrenzen staan onder `discover.limits.max_age_s`. Bij koppelingen en
de worker is `observed_at` de laatste bronmeting. De back-upcheck meet bij elke
aanvraag de leeftijd van de laatste back-up. Die brontijd staat in
`detail.laatste_backup`. Een back-up ouder dan twee dagen maakt de check
`degraded`. Het antwoord `/readyz` toetst lokale afhankelijkheden en vereist
geen Hub-koppeling.

De itemtypes `connector` en `change_set` hebben een bron en een relatieve
deeplink. Zonder typefilter toont `items` eerst de koppelingen en vervolgens de
wijzigingsvoorstellen. Op de laatste pagina is `next_cursor` leeg.

De projectie voor events en bewijs gebruikt rijnummers, bron, waarnemingstijd en
relatieve deeplink. Gebruik `payload.id` van een event als padparameter voor
`GET /connector/v1/evidence/{id}`.
