# Machine-sleutels: ontwerp vóór de bouw (S-P3-220)

**Status:** ontwerp vóór de bouw, 18 november 2026; basis `2512cd761ad30f42a4787935687388e9a3f0d428`. Geen gebouwd of gemeten scherm.
**Doel:** de beheerder maakt een sleutel voor een koppeling, kiest diens rechten en kan de sleutel definitief intrekken.
**Plaats:** `apps/web/app/(app)/instellingen/page.tsx`, onder het gebruikersbeheer in de bestaande tab Gebruikers; terugvindbaar via `/instellingen?tab=gebruikers#machine-sleutels`. Geen extra tabrij.

## 1. Bronnen en onderdelen

De bestaande ontwerpmap is `docs/ontwerp/`, met hoofdletters en underscores (`PEIL_MIGRATIE.md`, `PEIL_REVIEW.md`). `graphify query "Waar staat de bron van Peil, de ontwerptaal en de lokale Peil-kopie?"` wijst naar `design/ontwerptaal/ONTWERPTAAL.md` en `scripts/bronkopie.py`. Volgens `design/ontwerptaal/HERKOMST.md` is de bron `ai-server-manager/docs/platform-v2/ONTWERPTAAL.md` plus `ai-server-manager/design/ontwerptaal/`; de lokale kopie pint Peil 1.1.1. Bindend voor dit ontwerp: ONTWERPTAAL, koppen Kleursemantiek, Ruimte, Nederlandse microcopy en Toegankelijkheid; COMPONENTEN, koppen Foutpaneel en KopieerVeld, Tabs, ResponsieveLijst en Toegankelijkheid. Ook gelezen: de bestaande MSO-ontwerpen, ADR-0009 besluit 6 en het vergelijkbare MediaGuard-ontwerp `docs/ontwerp/API_SLEUTEL_SCOPES_ONTWERP.md` met ketenrapport `S-P3-600/RAPPORT-R-ONTWERP-2.md` (alleen als les, niet als MSO-bewijs).

| Bestaand Peil-onderdeel | MSO-onderdeel en toepassing |
|---|---|
| Card, Button, Input/Label/Form-patroon | `components/mg/primitives.tsx`: `Card`, `Button`, `Field`, `.input`; sectiekop, naam en acties |
| Switch | `primitives.tsx`: `Switch`, twee afzonderlijke, standaard uitgeschakelde scopeschakelaars |
| Dialog / AlertDialog | `primitives.tsx`: `Modal`; `components/mg/domain.tsx`: `DangerDialog` voor intrekken |
| KopieerVeld (C-15) | `primitives.tsx`: `CopyField`, uitsluitend de eenmalige aanmaakrespons; nooit een opgeslagen geheim terughalen |
| Label / Onbekend / melding | `StatusChip`, `InlineState`: rechten en levenscyclus neutraal, onbekend gestippeld, verzoekfout rood |
| Leeg / Laden / Foutpaneel / Versheid | `components/mg/states.tsx`: `StateView`, `ErrorState`, `ResourceView`, `StaleMark` |
| ResponsieveLijst (C-34) | bestaande `.table--stack` met kaart-containerquery in `styles/peil-mso.css` |

Alleen bestaande tokens: `card`, `popover`, `foreground`, `muted-foreground`, `muted`, `border`, `border-strong`, `input`, `primary`, `primary-foreground`, `destructive`, `destructive-foreground`, `unknown*`, `stale*`, `down*`, `ring`, `ring-halo`, `touch`, `z-overlay`. Ruimte `p-4`, `gap-2/3/4/6`; systeemfont en systeem-mono; tekst minimaal `text-sm` (14 px), invoer `text-base` (16 px). Iconen lucide: `KeyRound`, `Plus`, `Copy`, `Lock`, `Info`, `OctagonX`, `CircleHelp`, `ClockAlert`. Geen eigen token, kleur of nieuw componentpatroon. Eenmalige geheimonthulling is de expliciete functionele opdracht van S-P3-220; C-15's verbod op het terugtonen van opgeslagen geheimen blijft gelden.

**Peil-wijziging nodig: nee.** De hieronder gevraagde beschrijvingskoppeling, onmiddellijke focus en geforceerde-kleurenweergave zijn naleving van bestaande Peil-eisen in de MSO-implementatie. De gepinde Peil-bestanden worden niet aangepast. De vier in het draaiboek genoemde `design:*`-skills zijn in deze sessie niet beschikbaar; geen uitvoering daarvan geclaimd.

## 2. Sectie, rechten en lijst

Alleen een vastgestelde menselijke beheerder (`can("admin")`) krijgt de kaart en beheerverzoeken. Tijdens sessieladen blijft die verborgen. Operator en kijker zien in Gebruikers de bestaande melding `users.adminOnly`; geen machinekaart, namen, aantallen, laatste tekens of verzoeken. Ook de directe ankerlink onthult niets. De server weigert niet-beheerders en iedere machine-identiteit onafhankelijk van de UI. Een machine krijgt **geen rol**, ook geen impliciete kijkerrol. Geen enrollment, Hub-JWT of JWKS.

Kaartvolgorde: h2 Machine-sleutels → korte uitleg → Nieuwe machine-sleutel → bijgewerkt-tijd en Verversen → lijst of toestand. De hoofdactie blijft ook in de lege toestand aanwezig, één keer. Er is geen koppelingstest tegen de Hub.

De lijst toont actieve én ingetrokken sleutels, nieuwste aanmaak eerst (gelijke tijd: vaste ID-volgorde). Vier kolommen: **Naam** (naam + label Laatste vier tekens + suffix in mono), **Rechten (scopes)** (omlopende labels met gewone naam en code), **Datums** (Aangemaakt, Laatst gebruikt en zo nodig Ingetrokken op), **Status** (Actief of Ingetrokken + actie Intrekken). Tabel heeft een caption en `th scope="col"`; gestapelde rijen behouden alle veldnamen en tabelsemantiek. Geen verborgen kernvelden of horizontale scroller. Tijd: bestaande `date` + `time`, met jaar en 24-uurs tijd, `<time datetime>` en expliciete tijdzone bij de lijst. Een bevestigde lege laatst-gebruiktwaarde heet Nooit; ontbrekend/ongeldig veld heet Onbekend, nooit Nooit. Alleen de server bepaalt de laatste vier tekens; de lijst ontvangt nooit een hash of volledige sleutel. Geen prefix als vervanger voor de suffix.

Actief en Ingetrokken zijn neutrale labels met tekst, geen gezondheidsmeting. Ingetrokken rijen houden hun historie en hebben geen intrekknop. Bij onleesbare, ontbrekende of onbekende scopes: Onbekende rechten met uitleg; **de gehele sleutel staat dicht**, ook als daarnaast `read` voorkomt. Geen ruwe onbekende databasewaarde in de tekst. Een geldige lege scopelijst heet Geen rechten en geeft nergens toegang. Ontbrekende intrekstatus heet Onbekend, nooit impliciet Actief; de beheerder kan bij geldige sleutel-ID nog intrekken.

## 3. Aanmaken, eenmalig bekijken en intrekken

**Aanmaken:** `Modal`, titel Nieuwe machine-sleutel. Eerst een lege Naam (verplicht), daarna `fieldset` met `legend` Rechten (scopes). Naam na trimmen 1–80 tekens, geen besturingstekens; uniciteit na trimmen, hoofdletterongevoelig, ook ten opzichte van ingetrokken namen. Eén zichtbare uitleg per scope, code apart in mono. Beide schakelaars starten uit; minstens één keuze is vereist bij aanmaken. `changes:approve` staat nu al in de keuzes, onafhankelijk van `read`; het voegt geen leesrecht toe. De uitleg zegt dat het pas in een volgende stap wordt gebruikt, zonder nu goedkeuren of uitvoeren te beloven. Altijd zichtbare beperking: geen gebruikers-/sleutelbeheer, kluis, instellingen, herstel of force. Voet: Annuleren · Sleutel aanmaken. Elke heropening start met lege naam en beide schakelaars uit. Geen rolveld, vervaldatum of verborgen standaardrechten.

Na verzenden: eenmaal versturen; formulier en mutatieacties geblokkeerd, knop behoudt label en meldt bezig. Veldfouten houden invoer en keuzes vast. Tijdens een lopend verzoek mag sluiten via Annuleren, Escape of buitenklik: een eventuele late respons opent niets opnieuw en wordt niet bewaard. Dit annuleert geen al ontvangen serveropdracht; de voortgangstekst legt dat uit. Bij een netwerkfout/onzekere uitkomst geen automatische herhaling: eerst lijst verversen en het resultaat controleren.

**Eenmalige waarde:** alleen na bevestigde aanmaak vervangt de inhoud van dezelfde `Modal` het formulier: titel Machine-sleutel aangemaakt → naam + gekozen rechten → zichtbare eenmaligheidswaarschuwing → `CopyField` Machine-sleutel → Sluiten en waarde verbergen. Focus naar de titel (`tabindex=-1`), vervolgens kan de gebruiker zelf naar de kopieerknop. Geen volledige sleutel in een live-regio, toast, URL, audit, fout, opslag, lijstcache of browserherstel. Alleen tijdelijk geheugen en de zichtbare weergave. Aanmaakrespons niet cachen (`no-store`); opruimen bij sluiten, tab-/routewissel, afmelden en `pagehide`; terug/vooruit of verversen onthult niets opnieuw. Kopiëren gebeurt alleen op verzoek; bij geweigerd/onbeschikbaar klembord wordt de tekst geselecteerd met de bestaande browsermenu-uitleg. Een klik op Kopiëren sluit niet en bewijst niet dat invoeren in de Hub is gelukt. Het klembord wordt bij sluiten niet ongemerkt gewist.

Sluiten, Escape, buitenklik en wegnavigeren verbergen de waarde zonder extra bevestigingsdialoog; de waarschuwing staat vóór de waarde. De sleutel blijft actief. Bij verlies: intrekken en een nieuwe maken. Het document gebruikt uitsluitend het onbruikbare nepvoorbeeld **DIT-IS-GEEN-SLEUTEL**; nooit een voorbeeld met een echte sleutelvorm.

**Intrekken:** `DangerDialog`, titel Machine-sleutel intrekken. Feiten Naam, Rechten (scopes), Laatste vier tekens; omschrijving noemt onmiddellijke, definitieve intrekking en de koppeling die daarna niet meer met deze sleutel kan werken. Annuleren krijgt beginfocus; voet Annuleren · Sleutel definitief intrekken. Geen standaardbevestiging op Enter vanuit de dialoog. Na bevestigde respons: rij bijwerken, melding Sleutel ingetrokken, focus terug naar de kaartkop als de oorspronkelijke knop verdween. Herstellen is geen actie. Bij fout blijft de dialoog open; geen lokale intrekking veinzen. Is hij elders al ingetrokken, ververs de rij en toon de aparte melding. Tijdens verzenden kan sluiten; een late respons werkt alleen de lijst bij, nooit een andere rij of dialoog.

## 4. Toestanden en serverfouten

| Toestand | Weergave en gedrag |
|---|---|
| Eerste keer laden / langer dan 10 s | skelet, `aria-busy`, laadtekst; na 10 s langere-duurtekst, geen lege lijst |
| Vers en leeg | Nog geen machine-sleutels + uitleg; alleen op bevestigde lege respons |
| Verversen | vorige lijst behouden, Bijwerken; focus en scrollpositie blijven staan |
| Ophalen mislukt zonder data | foutpaneel met veilige code/trace-ID en Opnieuw proberen; geen sleutellijst of verzonnen aantallen |
| Offline / ophalen mislukt met data | Verouderd + laatst bekende tijd, ook bij een eerder lege lijst; oude metadata blijven leesbaar, aanmaken/intrekken uit met zichtbare reden; na herstel eerst verversen |
| Onbekend / onleesbaar | gestippeld label + reden; nooit groen of impliciet Actief; intrekken blijft mogelijk zodra de server weer bereikbaar is |
| Aanmaken / intrekken bezig | `aria-busy`, geen dubbel verzoek; labels behouden, voortgangstekst; geen succes tot bevestiging |
| Onbekende scope / dubbele naam / ongeldige invoer | vaste Nederlandse serverstring in foutsamenvatting, veldkoppeling waar toepasselijk; dialoog blijft open |
| Te veel mislukte pogingen (429) | vaste serverstring; Retry-After respecteren indien geleverd, geen goktijd of automatisch verzoek; bij ontbrekende duur handmatig opnieuw proberen; dit is geen bewijs dat de beheerder zelf machinepogingen deed |
| 401 / 403 na rol- of sessiewijziging | geheime waarde en metadata wissen; bestaande inlogweg of melding Alleen beheerders; geen blijvende beheerkaart |

Nieuwe foutresponsen hebben een Nederlandse `detail`-**string**, geen ongecontroleerde validatielijst. Onbekende scopes worden niet stil verwijderd. Naam, aangeboden scope en sleutel worden nooit in foutteksten teruggekaatst. Voorgestelde codes/HTTP voor A-BOUW (in OpenAPI vastleggen): `invalid_name`/422, `scope_required`/422, `unknown_scope`/422, `duplicate_name`/409, `rate_limited`/429, `admin_required`/403. De letterlijke `detail` is de NL-kolom van de overeenkomstige `machineKeys.error.*` hieronder; `fetch` en `uncertain` zijn veilige UI-terugvallen. Bestaande sessiefouten blijven de bestaande inlogweg volgen. Begrenzing op mislukte machine-authenticatie wordt in de kern getest, niet door echte koppelingen vanaf deze kaart.

## 5. Exacte teksten en vertaalsleutels

Vastgesteld: `apps/web/lib/i18n/messages/nl/settings.ts` bevat instellingen en gebruikers; `nl/common.ts` de gedeelde acties/kopieerteksten; `nl/logs.ts` het logboek. `messages/index.ts` voegt uitsluitend NL samen; `lib/i18n/index.tsx` en `SessionProvider` ondersteunen alleen `nl-NL`. Er bestaat **geen Engelse catalogus**. A-BOUW voegt de nieuwe NL-sleutels toe aan `nl/settings.ts` (audit aan `nl/logs.ts`) en bewaart de EN-paren onder dezelfde sleutels in nieuwe `messages/en/settings.ts`, `en/logs.ts` en, voor de hieronder hergebruikte teksten, `en/common.ts`. Dat zijn nieuwe overdrachtscatalogi; geen taalkeuzescherm of vertaling van heel MSO in deze stap. Engelse catalogusdekking en tekstinterpolatie afzonderlijk toetsen; geen bestaande Engelse runtime claimen.

Alle sleutels in de volgende tabel hebben prefix `machineKeys.`. Variabelen zijn tekst, nooit HTML; `{name}` alleen in de toegankelijke intrekknopnaam, `{time}` een opgemaakte tijd. De scopecodes blijven exact `read` en `changes:approve` in beide talen.

| Sleutel na prefix | Nederlands | Engels |
|---|---|---|
| title | Machine-sleutels | Machine keys |
| intro | Maak een sleutel voor een koppeling, zoals de Hub. Voer de sleutel zelf in die koppeling in. | Create a key for an integration, such as the Hub. Enter the key in that integration yourself. |
| add | Nieuwe machine-sleutel | New machine key |
| form.title | Nieuwe machine-sleutel | New machine key |
| form.name | Naam (verplicht) | Name (required) |
| form.nameHint | Kies een unieke naam van 1 tot 80 tekens. Gebruik geen geheimen in de naam. | Choose a unique name of 1 to 80 characters. Do not put secrets in the name. |
| form.scopes | Rechten (scopes) | Permissions (scopes) |
| form.scopesHint | Kies minstens één recht. Zet alleen aan wat de koppeling nodig heeft. | Choose at least one permission. Only enable what the integration needs. |
| scope.read | Alleen lezen | Read only |
| scope.readHint | Mag toegestane gegevens bekijken, maar niets wijzigen en geen gebruikers, sleutels of kluis lezen. | May view permitted information, but cannot change anything or read users, keys or the vault. |
| scope.approve | Wijzigingen goedkeuren | Approve changes |
| scope.approveHint | Bedoeld voor het goedkeuren en toepassen van precies een vooraf gecontroleerde wijziging. Wordt pas in een volgende stap gebruikt. Geeft geen leesrecht. | Intended to approve and apply one specific change checked in advance. Only used in a later step. Does not grant read access. |
| form.limits | Deze sleutel krijgt geen rol. Gebruikers en sleutels beheren, de kluis lezen, instellingen wijzigen, back-ups terugzetten en force gebruiken zijn nooit toegestaan. | This key has no role. Managing users and keys, reading the vault, changing settings, restoring backups and using force are never allowed. |
| form.create | Sleutel aanmaken | Create key |
| form.creating | Sleutel aanmaken bezig. Sluiten stopt de serveropdracht niet. Controleer daarna de lijst. | Creating key. Closing does not stop the server operation. Check the list afterwards. |
| created.title | Machine-sleutel aangemaakt | Machine key created |
| created.warning | Je ziet deze waarde alleen nu. Kopieer hem en voer hem in de koppeling in. Bij sluiten, verversen of wegnavigeren verdwijnt de waarde. De sleutel blijft actief. Ben je de waarde kwijt? Trek de sleutel in en maak een nieuwe. | You can only see this value now. Copy it and enter it in the integration. Closing, refreshing or navigating away hides the value. The key stays active. Lost the value? Revoke the key and create a new one. |
| created.value | Machine-sleutel | Machine key |
| created.close | Sluiten en waarde verbergen | Close and hide value |
| list.caption | Machine-sleutels en hun rechten | Machine keys and their permissions |
| list.name | Naam | Name |
| list.scopes | Rechten (scopes) | Permissions (scopes) |
| list.dates | Datums | Dates |
| list.status | Status | Status |
| list.created | Aangemaakt | Created |
| list.lastUsed | Laatst gebruikt | Last used |
| list.never | Nooit | Never |
| list.lastFour | Laatste vier tekens | Last four characters |
| list.active | Actief | Active |
| list.revoked | Ingetrokken | Revoked |
| list.revokedAt | Ingetrokken op | Revoked at |
| list.noScopes | Geen rechten | No permissions |
| list.unknown | Onbekend | Unknown |
| list.unknownScopes | Onbekende rechten | Unknown permissions |
| list.unknownHint | De rechten zijn onbekend of onleesbaar. Deze sleutel kan niets. Trek hem in en maak zo nodig een nieuwe. | The permissions are unknown or unreadable. This key cannot do anything. Revoke it and create a new one if needed. |
| list.updated | Bijgewerkt: {time} | Updated: {time} |
| list.timeZone | Tijdzone: {zone} | Time zone: {zone} |
| list.refreshing | Machine-sleutels bijwerken… | Updating machine keys… |
| empty.title | Nog geen machine-sleutels | No machine keys yet |
| empty.body | Maak een sleutel als je een koppeling toegang wilt geven. MSO werkt ook zonder de Hub. | Create a key to give an integration access. MSO also works without the Hub. |
| loading | Machine-sleutels ophalen… | Loading machine keys… |
| loadingLong | Machine-sleutels ophalen duurt langer dan verwacht. | Loading machine keys is taking longer than expected. |
| stale | Verouderd. Laatst bekend: {time}. Ververs de lijst voordat je sleutels beheert. | Stale. Last known: {time}. Refresh the list before managing keys. |
| unavailable | Beheer staat uit: er is geen actuele verbinding met MSO. Ververs de lijst zodra de verbinding terug is. | Management is disabled: there is no current connection to MSO. Refresh the list when the connection returns. |
| revoke | Intrekken | Revoke |
| revokeName | Intrekken: {name} | Revoke: {name} |
| revokeTitle | Machine-sleutel intrekken | Revoke machine key |
| revokeBody | Intrekken is definitief en werkt direct. De koppeling kan daarna niet meer met deze sleutel werken. Maak zo nodig eerst een nieuwe sleutel. | Revocation is permanent and takes effect immediately. The integration can no longer use this key afterwards. Create a new key first if needed. |
| revokeConfirm | Sleutel definitief intrekken | Permanently revoke key |
| revoking | Sleutel intrekken bezig. Sluiten stopt de serveropdracht niet. | Revoking key. Closing does not stop the server operation. |
| revoked | Sleutel ingetrokken. | Key revoked. |
| alreadyRevoked | Deze sleutel was al ingetrokken. De lijst is bijgewerkt. | This key was already revoked. The list has been updated. |
| error.name | Kies een naam van 1 tot 80 tekens zonder besturingstekens. | Choose a name of 1 to 80 characters without control characters. |
| error.scopeRequired | Kies minstens één recht (scope). | Choose at least one permission (scope). |
| error.unknownScope | Onbekend recht (scope). Toegestaan: read, changes:approve. | Unknown permission (scope). Allowed: read, changes:approve. |
| error.duplicate | Er bestaat al een machine-sleutel met deze naam. Kies een andere naam. | A machine key with this name already exists. Choose another name. |
| error.rateLimited | Te veel mislukte pogingen. Wacht even en probeer het opnieuw. | Too many failed attempts. Wait a while and try again. |
| error.retryAt | Opnieuw proberen kan vanaf {time}. | You can retry from {time}. |
| error.forbidden | Alleen beheerders kunnen machine-sleutels beheren. | Only administrators can manage machine keys. |
| error.fetch | Machine-sleutels ophalen is mislukt. Probeer het opnieuw. | Loading machine keys failed. Try again. |
| error.uncertain | De uitkomst is onbekend. Ververs de lijst en controleer de sleutel voordat je opnieuw probeert. | The outcome is unknown. Refresh the list and check the key before trying again. |

Hergebruikte sleutels, exact: `action.cancel` = Annuleren / Cancel; `action.retry` = Opnieuw proberen / Try again; `action.reload` = Verversen / Refresh; `copy.button` = Kopiëren / Copy; `copy.done` = Gekopieerd. / Copied.; `copy.selected` = Tekst geselecteerd. Kopieer via het menu van je browser. / Text selected. Copy using your browser menu.; `users.adminOnly` = Alleen beheerders kunnen gebruikers beheren. / Only administrators can manage users. De kopieerknop heeft als schermlezernaam Kopiëren Machine-sleutel / Copy Machine key, samengesteld uit `copy.button` en `machineKeys.created.value`. Geen nieuwe hardgecodeerde tekst voor gedeelde foutcode- en trace-ID-labels: gebruik de bestaande `state.*`-sleutels uit `nl/common.ts`.

Nieuwe auditacties krijgen **elk een eigen** vertaalsleutel in `nl/logs.ts` en `en/logs.ts`; geen generieke zin voor meerdere acties. Onderstaande vaste NL-samenvattingen zonder naam of sleutel zijn ook de servertekst. A-BOUW koppelt actie + sleutel expliciet in de bestaande logboekweergave (`lib/api/insights.ts`, `localizeAuditSummary`); niet alleen de ruwe samenvatting tonen. Ook detail en gerelateerde regels gebruiken die vertaling. Het bestaande NL-only scherm blijft Nederlands; toets de EN-catalogus apart.

| Voorgestelde auditactie | Vertaalsleutel | Nederlandse samenvatting | Engelse samenvatting |
|---|---|---|---|
| `machine_keys.list` | `logs.machineKeys.list` | Machine-sleutellijst bekeken. | Machine key list viewed. |
| `machine_keys.create` | `logs.machineKeys.create` | Machine-sleutel aangemaakt. | Machine key created. |
| `machine_keys.revoke` | `logs.machineKeys.revoke` | Machine-sleutel ingetrokken. | Machine key revoked. |
| `machine_keys.auth_failed` | `logs.machineKeys.authFailed` | Aanmelden met machine-sleutel geweigerd. | Machine key authentication denied. |
| `machine_keys.rate_limited` | `logs.machineKeys.rateLimited` | Machine-aanmelding tijdelijk begrensd. | Machine authentication temporarily rate limited. |

Audit bevat actor, actie, tijd en interne doel-ID, met uitsluitend noodzakelijke veilige metadata. Geen waarde, hash, verzoekkop, responsebody of door de aanvrager aangeboden onbekende scope. Kopiëren en sluiten sturen geen geheim of extra auditverzoek. Kiest A-BOUW aanvullende audit-/logmeldingen, dan zijn vooraf eigen NL/EN-sleutels vereist.

## 6. Breedtes, toegankelijkheid en overdracht

| Breedte | Licht én donker, dezelfde inhoud en volgorde |
|---|---|
| 320 | bestaande ondernavigatie; kaart één kolom; hoofd- en voetknoppen volle breedte en omlopend; lijst als gestapelde kaarten; dialoog onderaan, scrollbare inhoud, voet altijd bereikbaar; scopetekst links en 44-px-bediening rechts, codes mogen breken |
| 390 | zelfde opbouw als 320, meer ruimte voor uitleg; geen velden verbergen |
| 768 | bestaande ondernavigatie; gecentreerde `Modal` volgens bestaande breedte; machinekaart blijft volle breedte; lijst blijft gestapeld door de bestaande containergrens van 56 rem, niet blind een achtkolomstabel afdwingen |
| 1440 | bestaande zijbalk en kopbalk; kaart volle inhoudsbreedte; vierkolomstabel als de kaart breder is dan 56 rem, anders gestapeld; gecentreerde `Modal`, acties rechts; geen kleinere letters om velden passend te maken |

Namen, codes en labels lopen om, zonder ellips. Test ook een naam van 80 tekens en DejaVu Sans. Alleen tokens wisselen tussen licht/donker. De kaart is geen extra `h1`; gebruik h2 voor de sectie en geldige dialoogtitels. De stabiele anker-ID staat op een sectiewrapper (niet alleen op de h2).

Naam krijgt echt label, `required`, `noValidate`, hint en veldfout. Scopes krijgen `fieldset/legend`; elke schakelaar heeft een klikbaar label met dezelfde schermlezernaam en `aria-checked`. Koppel beide eigen uitleg-ID's met `aria-describedby` (de huidige MSO-`Switch` ondersteunt die prop nog niet: A-BOUW moet bestaande Peil-semantiek correct doorgeven). Geen `Field`-label voor een niet-labelbare `div`. Extra uitleg is zichtbaar, niet alleen een tooltip.

Toetsenbord: bestaande skiplink → schil → Gebruikers-tab → bestaand gebruikersbeheer → Nieuwe machine-sleutel → Verversen → rijacties. Modal: beginfocus Naam; Tab Naam → Alleen lezen → Wijzigingen goedkeuren → Annuleren → Sleutel aanmaken, terug met Shift+Tab. Spatie/Enter schakelt; Enter in Naam verzendt eenmaal na validatie. Foutsamenvatting krijgt focus, links gaan naar het veld/de scopegroep; maak die groep daarvoor programmatisch focusbaar. Eenmalige weergave: titel → Kopiëren Machine-sleutel → Sluiten en waarde verbergen. Intrekken: Annuleren → Sleutel definitief intrekken. Dialogen begrenzen focus; Escape sluit; sluiten brengt focus terug naar de opener, of de kaartkop als de opener verdween. Routewissel volgt bestaande routefocus. Geen positieve `tabindex`.

Focus heeft onmiddellijk de Peil-ring (buiten 3 px + halo 2 px), contrast ≥ 3:1 tegen beide aangrenzende vlakken, nooit achter kopbalk, dialoogvoet of ondernavigatie. **Geen animatie van outline of box-shadow, ook niet zonder reduced motion.** De huidige `buttonVariants` gebruikt `transition-colors`; A-BOUW moet op de geraakte bediening de focus van de overgang uitsluiten en onmiddellijke focus meten. Alle doelen ≥ 44 × 44 px, tussenruimte ≥ 8 px; tekst ≥ 7:1 (laatst bekende waarde ≥ 4,5:1), informatiegrenzen ≥ 3:1; nooit opacity op tekst.

Geforceerde kleuren: systeemkleuren `Canvas`, `CanvasText`, `Highlight` waar nodig, normale kleuraanpassing behouden; geen volledige opt-out. Elke schakelaar houdt een zichtbare trackrand en knop, met uit/aan onderscheiden door positie, ook zonder achtergrondkleur. Labels houden tekst en betekenisdragende rand; onbekend/verouderd houden stippel, verouderd ook reden/tijd; actieve tab houdt onderstreping. Focus `Highlight`, zonder overgang. Dit is te meten op geschilderde systeemkleuren; een voorafgaande transitie afwachten is geen oplossing voor focus. `role=alert` alleen voor een nieuwe verzoekfout; voortgang en kopieerfeedback beleefd, nooit de sleutel zelf. Reduced motion stopt schuiven, puls en draaien.

## 7. Genummerde meetpunten voor R-ONTWERP en e2e

Alle visuele toestanden op **320, 390, 768, 1440 × licht/donker** met uitsluitend fictieve gegevens; aanvullend de Peil-regressiebreedtes 360/1024. Elke uitkomst krijgt afdruk en waar passend DOM-meting; ontwerpregels zijn nog geen metingen.

1. Beheerder ziet de kaart onder Gebruikers, ook via ankerlink; operator/kijker en sessieladen krijgen geen kaart, metadata of sleutelbeheerrequest. Server weigert beheer door lagere rol en machines.
2. Nieuw formulier: lege naam, beide scopes uit, geen rolveld; labels/hints gekoppeld. Minstens één scope vereist; alleen `changes:approve` geeft geen impliciet `read`; elke heropening wist keuzes. Payload bevat uitsluitend expliciet gekozen bekende scopes.
3. Beide scope-uitleggen en beperkingen staan zichtbaar; `changes:approve` is kiesbaar maar belooft geen huidige uitvoeractie. Onbekende scope geeft 422 met exact de NL-string; een gemengde bekende/onbekende of onleesbare DB-waarde opent niets.
4. Leeg/laden/na 10 s/bezig/vers/verouderd/offline/onbekend zijn afzonderlijk herkenbaar; een ophaalfout maakt nooit een lege, actuele lijst. Oude metadata blijven leesbaar, mutaties geblokkeerd met reden; na herstel eerst verversen.
5. Dubbele naam (ook hoofdletterverschil en ingetrokken naam), ongeldige naam, lege scopekeuze, 429 en 403 tonen de vastgelegde teksten en focus-/invoerafhandeling. Retry-After wordt gevolgd zonder automatische herhaling; ontbrekende duur verzint geen afteller.
6. Aanmaken verstuurt eenmaal, succes toont de waarde alleen in dezelfde dialoog. Kopiëren werkt met klembord én bij weigering/onveilige context via selectie; naam en feedback kloppen; de waarde komt niet in live-regio of toast.
7. Sluiten, Escape, buitenklik, tab-/routewissel, afmelden, verversen en terug/vooruit verbergen de waarde blijvend. Sleutel blijft actief. Laat ontvangen respons opent niets opnieuw; onzekere mutatie-uitkomst vraagt eerst lijstcontrole. Geen waarde in opslag, URL, lijstrespons, database, audit of logs (apart kerntestbewijs).
8. Lijst toont alle afgesproken velden, suffix exact vier tekens en nooit prefix/hash/volledige waarde; Nooit alleen bij bevestigde null. Naam van 80 tekens, beide scopes en onbekende/ingetrokken rijen passen; historie blijft zichtbaar.
9. Intrekken vereist afzonderlijke bevestiging met juiste feiten en beginfocus Annuleren. Annuleren verandert niets; bevestiging werkt direct en definitief, fout veinst geen intrekking, elders ingetrokken verversen, geen herstelknop. Focus keert na verdwenen rijactie terug naar kaartkop.
10. Elke nieuwe auditactie heeft eigen NL- én EN-sleutel. Controleer NL-lijst, detail en gerelateerde regels en toets EN-catalogus/interpolatie apart. Serverfouten zijn letterlijk Nederlandse strings; geen ruwe scope of geheim in nieuwe audit-/fouttekst.
11. Geen paginaoverloop, inhoud buiten eigen element, ellips, verborgen kernveld, tekst < 14 px of doel < 44 × 44 px; normale systeemfont én DejaVu Sans. 200%-zoom reflowt; 768 gebruikt de bestaande containerregel en 1440 de passende tabel/kaartvorm.
12. Volledige Tab/Shift+Tab-doorloop op 390 en 1440 plus tabpijlen/Home/End; geen verloren/bedekte focus. Ring en halo ≥ 3:1 **vanaf het eerste getekende focusmoment**; geen focusovergang in computed styles of lopende animaties. Tekst- en randcontrast numeriek meten in beide thema's.
13. Geforceerde kleuren: elke schakelaar uit én aan, onbekende/verouderde/neutrale labels, actieve tab, veldfout en gevaarknop behouden herkenbare vorm, rand en leesbare tekst; schakelknoppositie en focus zijn zichtbaar. Reduced motion: geen schuif-, puls- of draai-animatie.
14. Axe (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`) nul overtredingen op kaart, aanmaken, eenmalige waarde en intrekken; één h1, geen dubbele ID's/kapotte aria-verwijzingen, namen gelijk aan zichtbare labels. Echte schermlezer/hoogcontrastomgeving later afzonderlijk valideren; emulatie als zodanig rapporteren.

## 8. Open punten voor A-BOUW

Leg routes, verzoekkop en stabiele fout-/auditcodes vast in OpenAPI en USER_GUIDE; dit ontwerp schrijft geen API-implementatie voor. Bouw de uitsluitend op scopes gebaseerde, fail-closed controle, hashing, definitieve intrekking en begrenzing met de kern-/migratie-/sessie-/wedlooptests uit S-P3-220 en het dossier. Laat `changes:approve` in deze stap geen schrijfrechten openen. Lever de beschrijvingskoppeling van `Switch`, focus zonder overgang en geforceerde kleuren binnen MSO; neem ze niet als al bewezen over uit MediaGuard. Voeg de NL-teksten, EN-overdrachtscatalogi en actiegebonden auditvertaling toe en toets ook foutpaden. Er is geen open gebruikersbesluit of Peil-uitbreiding nodig. Geen echte NAS, Emby, Hub-sleutel of accounts in deze ontwerpstap; geen wijziging aan EVIDENCE-status. R-ONTWERP meet pas na de bouw.
