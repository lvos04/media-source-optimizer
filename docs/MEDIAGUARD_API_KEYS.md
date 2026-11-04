# MediaGuard API-sleutels — contract en bouwprompt

Media Source Optimizer (MSO) kan vanaf versie 1.2.0 bij MediaGuard inloggen met een **API-sleutel**.
Dat is veiliger en eenvoudiger dan de interne token (die is altijd admin) of een gebruikersaccount (sessie +
CSRF). MediaGuard zelf moet die API-sleutels nog krijgen; MSO past MediaGuard nooit zelf aan. Hieronder staat
het contract waar beide apps zich aan houden, en de prompt die je aan een Claude-sessie in de
MediaGuard-repository geeft.

## 1. Contract (beide kanten)

| Onderdeel | Afspraak |
|---|---|
| Header | `X-Api-Key: <sleutel>` op elk verzoek. Geen cookie, geen CSRF-token nodig. |
| Sleutelformaat | `mgk_` + 43 tekens base64url (`secrets.token_urlsafe(32)`), dus 47 tekens totaal. Het prefix `mgk_` maakt sleutels herkenbaar voor secret-scanning en redactie. |
| Rol | Elke sleutel heeft een eigen rol: `viewer` of `operator` (**nooit** `admin`). |
| Identiteit | `GET /api/v1/auth/me` geeft voor een sleutel `{"username": "apikey:<naam>", "role": "<rol>", "csrf_token": null, …}`. MSO leest hier de rol uit. |
| Ongeldig of ingetrokken | HTTP `401` met `{"detail": "Ongeldige of ingetrokken API-sleutel"}`. |
| Opslag in MediaGuard | Alleen de SHA-256-hash en de eerste 8 tekens (voor herkenning) staan in de database. De volledige sleutel wordt **één keer** getoond bij het aanmaken. |
| Beheer | Alleen `admin`, via Instellingen → API-sleutels: aanmaken (naam + rol, optioneel vervaldatum), lijst (naam, rol, prefix, aangemaakt, laatst gebruikt, verloopt), intrekken. |
| Audit | Aanmaken, intrekken en elke geweigerde sleutel worden gelogd. Gebruik door een sleutel staat in de audit als actor `apikey:<naam>`. De sleutel zelf komt nooit in logs. |
| Wat MSO aanroept | Alleen GET-routes (`/health`, `/auth/me`, `/status`, `/openapi.json`, `/wanted…`, `/policy/effective`) en `POST /api/v1/wanted/{id}/search` (rol operator). Zie `INTEGRATION_MEDIAGUARD.md` §3. |

### Wat MSO al doet (versie 1.2.0)

- Bij de MediaGuard-koppeling staat het veld **"MediaGuard-API-sleutel (aanbevolen)"**, en op het
  MediaGuard-scherm de keuze *API-sleutel*.
- Is er een sleutel ingevuld, dan stuurt MSO alleen `X-Api-Key`: geen login en geen sessie.
- De rol komt uit `/auth/me`. Bij `viewer` is "Vraag MediaGuard opnieuw te zoeken" uitgeschakeld; bij `admin`
  waarschuwt MSO dat dat te ruim is.
- Bij `401` meldt MSO: *"MediaGuard weigert de API-sleutel: ongeldig of ingetrokken, of deze MediaGuard-versie
  ondersteunt nog geen API-sleutels"*. Zolang MediaGuard nog geen sleutels kent, gebruik je de gebruikersnaam
  en het wachtwoord.
- De sleutel wordt versleuteld opgeslagen en komt nooit terug in de API, de audit of de logs (getest in
  `apps/core/tests/test_mediaguard_apikey.py`).

## 2. Prompt voor de MediaGuard-sessie

Kopieer alles tussen de lijnen naar Claude Code in de repository `lvos04/media-guard-ultimate`.

---

> **Opdracht: voeg API-sleutels toe aan MediaGuard Ultimate (machine-toegang met eigen rol).**
>
> **Waarom.** Media Source Optimizer (MSO, `lvos04/media-source-optimizer`) koppelt met MediaGuard via de
> gedocumenteerde API. Nu kan dat alleen via een gebruikersaccount (sessiecookie + CSRF) of via
> `X-MediaGuard-Token`, en die interne token geeft altijd **admin**-rechten. MSO heeft genoeg aan `operator`.
> Daarom wil ik API-sleutels met een eigen rol die een beheerder kan aanmaken en intrekken. MSO ondersteunt
> dit contract al aan zijn kant; houd je er exact aan.
>
> **Contract (niet van afwijken).**
> 1. Header `X-Api-Key: <sleutel>`. Sleutelformaat `mgk_` + `secrets.token_urlsafe(32)`.
> 2. Een sleutel heeft rol `viewer` of `operator`. **Nooit `admin`**: de API moet een admin-rol voor sleutels weigeren.
> 3. `principal_from_request` in `apps/core/mediaguard/auth.py`: controleer `X-Api-Key` **na** de bestaande
>    `X-MediaGuard-Token`- en hook-key-controles en **vóór** de sessiecookie. Bij een geldige sleutel:
>    `Principal(username=f"apikey:{naam}", role=<rol>, machine=True)` zonder csrf_token. Machine-principals
>    zijn al vrijgesteld van CSRF: controleer dat dit zo blijft. Bij een onbekende, ingetrokken of verlopen
>    sleutel: HTTP 401 `{"detail": "Ongeldige of ingetrokken API-sleutel"}`. Val dan **niet** terug op de
>    cookie, want dat maskeert fouten.
> 4. `GET /api/v1/auth/me` geeft voor een sleutel `{"username": "apikey:<naam>", "role": "<rol>", "csrf_token": null, …}`
>    (bestaande vorm, `preferences` leeg).
> 5. Opslag: nieuwe migratie (volg de nummering en stijl in `apps/core/mediaguard/migrations/`) met tabel
>    `api_keys(id, name UNIQUE, prefix, key_hash UNIQUE, role CHECK IN ('viewer','operator'), created_by,
>    created_at, last_used_at, expires_at NULL, revoked_at NULL)`.
>    - Sla alleen `sha256(sleutel)` op plus de eerste 8 tekens als `prefix`. Vergelijk met `hmac.compare_digest`.
>    - Werk `last_used_at` hooguit één keer per minuut per sleutel bij, om schrijfdruk te beperken.
> 6. API (alleen `admin`, met CSRF zoals de rest van de UI):
>    - `GET /api/v1/auth/api-keys` → `{items:[{id,name,prefix,role,created_by,created_at,last_used_at,expires_at,revoked_at}]}`.
>      Nooit de hash, nooit de sleutel.
>    - `POST /api/v1/auth/api-keys {name, role, expires_in_days?}` → `{id, name, role, key, prefix, …}`. Alleen
>      **dit** antwoord bevat `key` (één keer tonen).
>    - `POST /api/v1/auth/api-keys/{id}/revoke` → ingetrokken sleutel. Daarna geeft elk verzoek met die sleutel direct 401.
> 7. Beveiliging:
>    - Rate limit op mislukte sleutelpogingen per IP, zoals bij login.
>    - De `RedactingFilter`/logging moet `X-Api-Key`-headers en alles wat met `mgk_` begint maskeren. Voeg een test toe.
>    - Audit-acties `apikey.created`, `apikey.revoked`, `apikey.rejected`. Gebruik door de sleutel staat in de
>      audit als actor `apikey:<naam>`.
>    - Een sleutel geeft geen toegang tot `/api/v1/auth/api-keys` zelf, de gebruikersroutes of de geheimen,
>      want die vereisen admin.
> 8. UI (zelfde MediaGuard-huisstijl, Nederlands): nieuw tabblad of pagina **Instellingen → API-sleutels**, naast
>    *Gebruikers* en *Geheimen*.
>    - Tabel met naam, rol-chip, prefix (mono), aangemaakt, laatst gebruikt, verloopt en status.
>    - Knop *Nieuwe sleutel*: dialoog met naam, rol (segmented viewer/operator, operator vooraf gekozen met de
>      uitleg "voor Media Source Optimizer") en optionele vervaldatum.
>    - Na aanmaken: een dialoog die de sleutel één keer toont, met kopieerknop en de waarschuwing
>      "Deze sleutel wordt niet opnieuw getoond".
>    - *Intrekken* via de bestaande DangerDialog.
>    - Alle staten (loading/empty/error) zoals in de rest van de app. Geen dode knoppen.
> 9. Documentatie: werk `docs/` bij (beveiliging en integraties) met de header, de rollen en het beheer. Voeg
>    API-sleutels toe aan de bestaande auth-uitleg en het OpenAPI-schema (`securitySchemes` apiKey in header `X-Api-Key`).
> 10. Tests (pytest + bestaande e2e-stijl):
>     - Geldige operator-sleutel kan `GET /api/v1/wanted` en `POST /api/v1/wanted/{id}/search` doen **zonder** CSRF.
>     - Viewer-sleutel krijgt 403 op die POST.
>     - Admin-rol voor een sleutel wordt geweigerd (422).
>     - Ingetrokken en verlopen sleutels geven 401.
>     - Onbekende sleutel: 401 en geen terugval op een geldige cookie in hetzelfde verzoek.
>     - Hash-opslag: de sleutel staat niet in de DB.
>     - De sleutel staat niet in logs of audit.
>     - `/auth/me`-vorm klopt.
>     - Rate limit werkt.
>     - Bestaande `X-MediaGuard-Token`-, hook-key- en sessietests blijven groen.
>     - Playwright: aanmaken, eenmalig tonen, intrekken (licht en donker, 1440 en 390).
>
> **Grenzen.** Verander niets aan het gedrag van bestaande routes, de rollen viewer/operator/admin of de
> interne token. Houd sessiecookie + CSRF voor mensen ongewijzigd. Draai alle bestaande tests. Commit en push
> naar je eigen werkbranch volgens de afspraken in de repo, met een korte samenvatting in het CHANGELOG.
>
> **Klaar als:** een beheerder in de UI een operator-sleutel kan aanmaken, die sleutel in MSO (veld
> "MediaGuard-API-sleutel") de status *Verbonden · rol operator* geeft, en intrekken MSO direct
> op *Verstoord · sleutel geweigerd* zet.

---

## 3. Na de MediaGuard-update

1. Update MediaGuard en maak in **Instellingen → API-sleutels** een sleutel met naam `mso` en rol **operator**.
2. In MSO: **MediaGuard-koppeling** → veld *MediaGuard-API-sleutel* → plak de sleutel → **Test** → **Bewaar**.
   Maak gebruikersnaam en wachtwoord daarna leeg.
3. De status moet *Verbonden* tonen met rol *operator* en methode *API-sleutel*.
