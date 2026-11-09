# ADR-0002 — Hub-koppeling via bestaande routes plus een dunne connectorlaag

Status: **Voorgesteld (fase P0; uitvoering P3)** · Datum: 2026-11-09 · Excel: B05-009, I04-04-08 · EX16, EX19, EX20

## Context

Contract 1.1 §6 zegt: bestaande apps krijgen in de Hub een adapter die hun **bestaande** routes gebruikt; nieuwe routes
alleen als de delta-matrix ze als ontbrekend markeert. B05-009 eist: "Hub keurt goed via de door MSO ondersteunde flow"
en "geen dubbele actie als in Hub en MSO tegelijk op goedkeuren wordt gedrukt".

MSO heeft (zie `INVENTARIS.md` §2, §5, §13):
- een volwassen change-set-mechanisme (preview → goedkeuring → toepassen onder advisory lock → teruglezen → rollback met
  driftcontrole), modi, noodrem, overrides en audit — dit mag niet dubbel gebouwd worden;
- alleen sessie-authenticatie (cookie + CSRF); geen machine-identiteit (`Principal.machine` bestaat maar wordt niet
  gebruikt); foutformaat `{detail, code, correlation_id}` dat de eigen UI gebruikt.

## Opties

1. **Hub praat met de bestaande `/api/v1`-routes met een MSO-gebruikersaccount.** Afgewezen: de Hub zou een wachtwoord
   en CSRF-sessie van een mens moeten bewaren; geen idempotentie; strijdig met contract §3/§4.
2. **Volledige herbouw van MSO naar het platformcontract** (foutformaat, UUID's, ETag overal). Afgewezen: grote
   regressiekans in een bestaande, geteste app; geen gebruikerswaarde.
3. **Dunne connectorlaag `/connector/v1/*` + machine-auth, die de bestaande modules aanroept.** Gekozen.

## Besluit

- **Auth (P3-06):** MSO verifieert door de Hub uitgegeven JWT's (EdDSA, `iss=hub`, `aud=media-source-optimizer`,
  `exp ≤ 120 s`) tegen de Hub-JWKS die bij enrollment wordt opgehaald. Rollen worden 1-op-1 gemapt op
  viewer/operator/admin; een onbekende rol of een AI-actor krijgt nooit meer dan operator. Ongeldig/verlopen →
  401 `unauthorized` en de Hub zet de connector op `unknown` (EX16). Lokale MSO-accounts blijven bestaan.
- **Connectorlaag (P3-07):** `discover`, `health`, `items`, `events`, `evidence/{id}`, `rollback` en `apply` roepen de
  bestaande functies aan (`changes.approve/apply/rollback`, overzichts- en diagnosemodules). `propose` vanuit de Hub
  geeft `501 capability_unavailable`: MSO is eigenaar van de beslislogica en maakt zelf voorstellen. Alleen in deze
  laag geldt het contractfoutformaat `{code, message, details, trace_id}` en `Idempotency-Key` (verplicht op
  mutaties, opslag in `connector_idempotency`). Events worden gepold uit `audit_log`/`automation_log` met een
  oplopende cursor en als envelope §7 geleverd; geen exact-once-aanname.
- **Binding aan de diff (P3-08):** elke change set krijgt een `preview_hash` (sha256 over canonieke `target`, `risk`,
  `ops`). `apply`/`approve` via de connectorlaag vereisen `expected_preview_hash`; afwijking → 409 `preview_changed`
  (EX19). Gelijktijdig goedkeuren in Hub en MSO wordt al afgevangen door de atomaire statusovergang en de per-change
  advisory lock (`changes.py:126-143, :211-231`); P3-08 voegt de ontbrekende paralleltest toe (EX20).
- **Ondertekende ExecutionRequest (contract §8):** de executor-allowlist gaat over TrueNAS-app-acties en is niet van
  toepassing op MSO-bronwijzigingen. Voor v1 volstaat Hub-JWT + `preview_hash` + `Idempotency-Key`. Volledige
  Ed25519-verificatie van een ExecutionRequest in MSO volgt zodra de Hub dat formaat levert (vervolgpunt).

## Gevolgen

- Geen wijziging aan bestaande `/api/v1`-routes, de MSO-UI of het foutformaat daarvan.
- Eén nieuwe migratie (`0608`: `connector_idempotency` + kolom `preview_hash`).
- Afhankelijk van `ai-server-manager` (B01/B02): `POST /api/v1/services/enroll` en `GET /api/v1/auth/jwks`.
