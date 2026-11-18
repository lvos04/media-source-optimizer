# Machine-sleutels: bouwbewijs S-P3-220

Status: Bezig; gemeten op 18 november 2026, uitsluitend met eigen wegwerpdatabases en lokale simulators. Geen echte NAS, Emby, Hub of dienstaccounts gebruikt. Dit document is bouwbewijs; de onafhankelijke reviews en echte acceptatie volgen afzonderlijk.

## Reikwijdte

Basis: `acaf0b34a4162f3bb959a1dfc243f19585ce98cb`; kern `8b44a814e64a70ea0b69ccf0d9209c925755cfb8`; web `88a76c716e611e3b577bb2790b1193156c9b7ffc`. De definitieve bereikgrenzen inclusief deze bewijscommit staan in het ketenrapport. De machine heeft geen rol. `read` opent 34 expliciete GET-routes; `changes:approve` opent in deze stap niets. Alle 111 schrijfoperaties uit de export worden geweigerd, inclusief publieke ingangen en sleutelbeheer. Onbekende routes, aliassen, websockets, corrupte scopes en `force` blijven gesloten.

## Herstelproeven

De fixtures in `apps/core/tests/fixtures` zijn vastgelegd vanuit de uitgepakte basis
`acaf0b34a4162f3bb959a1dfc243f19585ce98cb` met dezelfde testclients en scenario’s.
`test_machine_key_sessions.py` vergelijkt 210 bestaande operaties voor vier identiteiten.
De matrix gebruikt geldige CSRF bij sessies en meet status, inhoudstype, foutcode en foutvelden
met minimale invoer. Afzonderlijke proeven gebruiken geldige invoer bij bestaande aanmeldingen
en geslaagde schrijfhandelingen van kijker, operator en beheerder. Die vergelijken de sessie-,
gebruikers- en systeemstand en de auditregels per handeling. De publieke proeven vergelijken
`healthz`, publieke routes en de menselijke en algemene begrenzing. `test_machine_key_feeds.py`
vergelijkt caps en zoeken met geldige en ongeldige consumentensleutels in de kop en de query op
beide feeds, inclusief voorrang bij gelijktijdig gebruik van beide ingangen.

`test_machine_key_restore.py` onderbreekt het echte lokale `pg_restore` met procesuitval en
`KeyboardInterrupt`, en laat ook een schema-update vanaf 0607 falen. De proeven meten
herstel van de huidige scopes en intrekkingen bij de start, en de menselijke aanmelding tijdens
de sluiting. Een aparte terugzetproef op databaseniveau liet zien dat een oude sleutel weer
toegang kreeg en zijn intrekking uit het auditlog verdween. De TrueNAS-snapshot zelf blijft
NIET GEVALIDEERD (echte NAS).

De mutatieproeven in `scripts/test-machine-key-mutaties.py` bewaken ook de
API-grenzen: beheerdersrol, opmaak van bestaande fouten, routecontrole, terugzetfouten,
achterwege gelaten auditregels en behoud van de bytes van alle bestaande tabellen bij
migratie 0608. De machinegrens voor schrijfoperaties wordt daarnaast bewaakt door de
negatieve OpenAPI-matrix in `test_machine_keys.py`.

## Bouwronde op commit a3429c9

- Volledige core-suite: `1051 passed in 738.43s (0:12:18)`, nul fouten en nul overgeslagen volgens JUnit. Inclusief scopegrenzen, sleutelintrekking tijdens verzoeken, twee processen, begrenzing, geheimopslag, gebruikstijd, migratie heen/terug, herstel zonder heractivering en weigering van een nieuwer schema.
- OpenAPI opnieuw gegenereerd en gecontroleerd: `3 passed in 1.92s`; de export bleef gelijk aan de eerder gecontroleerde eindstand.
- Web: 120 unittests; Peil-controle, typecheck, eslint en productiebuild exitcode 0. De Engelse bestanden zijn de in het ontwerp voorgeschreven overdrachtscatalogi; MSO blijft Nederlands als runtime-taal.
- Ruff en offline/online controle van gepinde contract- en Peil-kopieën exitcode 0.
- Vier eindimages gebouwd met opnieuw gemeten basisdigests: Python `sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72`, PostgreSQL `sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9` en Node `sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee`. Bestaande Dockerfile-standaarden zonder digest zijn hiermee niet als opgelost aangemerkt.
- Alles-in-één lokaal: acht scenario’s groen, waaronder de upgrade van het gevulde 1.5.0-schema naar 608 met voorafgaande back-up en behoud van gebruiker/aanmelding. Daarna stopt het ongewijzigde testbestand bij scenario 7 op `os.chown`: de lokale zandbak kan UID 568 niet toekennen (`EINVAL`). Scenario’s 7 en 8 zijn lokaal niet voltooid. Geen producttest gewijzigd en geen herhaling van deze omgevingsfout; de volledige proef wordt in CI beoordeeld.
- De twaalf tests voor de rooktest-logcontrole zijn groen.

- Volledige stackproef: 19 groen, nul mislukt, nul overgeslagen; inclusief behoud van gegevens, versleutelde koppelingsgeheimen, gebruikers en aanmelding na herstart en opnieuw aanmaken van containers.
- Volledige browserproef: `118 passed (3.2m)`; `Strikte E2E: 118 test(s) gevonden, 0 overgeslagen (uitslag: passed)`. Alle vier nieuwe machine-sleuteltests draaien samen met de bestaande tests. De nieuwe tests gebruiken paginafixtures; 64 afdrukken (aanmaken, eenmalige waarde, lijst en intrekken, twee lettertypen, vier breedtes, twee thema’s) zijn buiten de repo bewaard. Twee afdrukken zijn aanvullend bekeken; dit is geen onafhankelijke ontwerpreview.
- Werkelijke lokale webproxy/API/databaseketen zonder beheerfixtures: maken 200, lezen 200, schrijven 403, na intrekken 401, geen sleutel in latere antwoorden; aanmaakrespons heeft `no-store`. Dit draait apart op een verse eigen stack zodat de browserproeven geen gedeelde toestand veranderen.

De eerste startcontrole vóór de browserrun weigerde een tijdelijk niet-bindbare testpoort; daarbij zijn geen containers of tests gestart. Na hercontrole met `ss` en opnieuw binden startte dezelfde proef zonder productwijziging. De eerder genoemde UID-mappingfout van de alles-in-één-proef is niet opnieuw geprobeerd.

CI op de bouw- en herstelcommits wordt met run-ID en jobaantallen in het ketenrapport vastgelegd. Eigen proefcontainers en netwerken zijn na iedere proef verwijderd. De gevolgde bestaande stackrapporten en bestaande schermbeelden zijn na het bewaren van deze eigen uitvoer teruggezet; ruwe uitvoer hoort niet in deze wijziging.

## Tegenproeven van de bouwronde

`scripts/test-machine-key-mutaties.py` eindigt met exitcode 0 wanneer iedere gerichte verzwakking aantoonbaar rood wordt. Letterlijke regels van deze uitvoering:

```text
centrale_scopegrens: exitcode 1; 1 failed in 4.28s
corrupte_scopes: exitcode 1; 1 failed, 5 passed in 13.73s
rolvrij: exitcode 1; 1 failed in 4.54s
geheimopslag: exitcode 1; 1 failed in 4.30s
intrek_wedloop: exitcode 1; 1 failed in 3.84s
gebruikstijd: exitcode 1; 1 failed in 4.52s
begrenzing: exitcode 1; 1 failed in 5.51s
migratiebehoud: exitcode 1; 1 failed in 4.43s
sessiebehoud: exitcode 1; 1 failed in 3.65s
neveneffect_get: exitcode 1; 1 failed in 3.18s
redactielaag: exitcode 1; 1 failed in 4.43s
hashvergelijking: exitcode 1; 1 failed in 3.47s
interne_beheerweg: exitcode 1; 1 failed in 3.69s
nieuwer_schema: exitcode 1; 1 failed in 3.10s
intrek_binding: exitcode 1; 1 failed in 3.71s
herstel_grens: exitcode 1; 1 failed in 6.32s
herstel_wedloop: exitcode 1; 1 failed in 6.21s
herstel_leesvenster: exitcode 1; 1 failed in 3.59s
18 mutaties rood bevestigd; productbestanden ongewijzigd
```

De productbestanden bleven ongewijzigd. Een afzonderlijke basisproef gebruikt een uitgepakte kopie van de ongewijzigde basis op een eigen database met schema 0608. `migrate`, `api` en `worker` eindigen elk met exitcode 79 en ongewijzigde tabelinhoud. De oude auth-functie herkent een losse machineheader niet. Slotregel:

```text
Basisproef: nieuw schema geweigerd; machine-sleutel werkt niet; niets verloren
```

## Herhalen en grenzen

De lokale commando’s, exitcodes en letterlijke slotregels staan in het ketenrapport; tijdelijke bestanden, caches, browserafdrukken en ruwe proefuitvoer blijven buiten de commit. De normale stack- en browserproeven gebruiken de ongewijzigde ingangen `tests/stack/run.sh` en `make e2e`. Lokale alles-in-één-proeven gebruiken het voorgeschreven hulppad rond het ongewijzigde `tests/stack/allinone_test.py`; de CI draait dat bestand op de runner.

B05-009 en C1.3-MSO-04 blijven Bezig met Unit-getest en NIET GEVALIDEERD (echte NAS). EX16 betreft uitsluitend het getoetste MSO-deel. S-P3-230, S-P3-240 en de Hub-kant moeten nog worden gebouwd. Daarom is dit geen integrale Hub-acceptatie en is er meer vervolgwerk dan alleen NAS-acceptatie. Browserfixtures en kleur-/toegankelijkheidsemulatie vervangen geen echte schermlezer- of hoogcontrastacceptatie. De ontwerpreview meet de overige ontwerpcriteria onafhankelijk.
