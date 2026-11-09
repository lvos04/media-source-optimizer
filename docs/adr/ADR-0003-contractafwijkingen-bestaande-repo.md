# ADR-0003 — Bewuste afwijkingen van het platformcontract voor de bestaande MSO-repo

Status: **Aangenomen (fase P0)** · Datum: 2026-11-09 · Excel: B05-004, B05 §2/§3

## Context

`PLATFORM_CONTRACTEN.md` 1.1 is geschreven voor **nieuwe** repos (§1: "Nieuwe repos: Python 3.12 + FastAPI +
SQLAlchemy 2 + Alembic …", §10 "Repo-indeling nieuwe Python-diensten"). MSO is een bestaande, geteste app (1.0.0–1.6.0)
die het contract voor bestaande apps volgt: "alleen hiaten bouwen; bestaande integriteit/rollback/monitoring nooit
dubbel bouwen of uitschakelen" (§12). Waar het contract en MSO verschillen, is de veiligste interpretatie gekozen:
geen regressie in een werkende app, wel conformiteit aan de buitenkant (installatie, Hub-koppeling, bewijs).

## Besluit

| Onderwerp | Contract | MSO houdt | Waarom |
|---|---|---|---|
| Taal/runtime | Python 3.12, SQLAlchemy 2, Alembic | Python 3.13, psycopg 3, eigen SQL-migraties met up/down (`db.py`) | Werkt en is getest (789 tests); omzetten levert alleen risico. `requires-python >=3.12` blijft. |
| Wachtwoordhash | argon2id (Hub) | scrypt n=2¹⁵ (`auth.py`) | Contract §4 gaat over Hub-accounts; geforceerde migratie zou bestaande logins breken. Hub-gebruikers komen via JWT binnen (ADR-0002). |
| Foutformaat | `{code, message, details, trace_id}` | `{detail, code, correlation_id}` op `/api/v1` | De eigen UI hangt ervan af; het contractformaat komt in `/connector/v1/*` (ADR-0002). |
| ID's/ETag/cursor | UUID, ETag, cursor overal | `bigserial`, geen ETag, meestal `limit` | Alleen in de connectorlaag (`resource_etag` = `preview_hash`, cursor op items/events). |
| UI | Alle nieuwe schermen in de Hub | MSO houdt zijn eigen 16 schermen | MSO is een bestaande app; de Hub toont kaarten + deeplinks (B05-009). Nieuwe MSO-functies krijgen een API voor de Hub en zo nodig een kleine sectie in MSO's eigen scherm. |
| Naam "Voetafdruk" | B05-004: resource-voetafdruk (DB, opslag, logs, verbindingen) | "Voetafdruk" = wat MSO bij de bronnen veranderde (1.6.0) | Bestaand scherm niet hernoemen; de resource-voetafdruk komt onder Systeem → "Opslag en groei" (P3-10) en in de Hub. |
| Poort | Contracttabel noemt MSO niet | 38081 (container en host) | Bestaande installaties; de Hub registreert de werkelijke hostpoort (D03). |
| mypy in CI | verplicht voor nieuwe repos | (nog) niet | Grote bestaande codebase; vervolgpunt, geen P3-blokkade. pip-audit en OpenAPI-drift komen wel (P3-04/14). |

## Gevolgen

- Deze afwijkingen staan als contractpunten in het eindrapport van fase P0 en in `DELTA_MATRIX.md` §5.
- Nieuwe code in MSO volgt de bestaande stijl van MSO; nieuwe *buitenkant* (connectorlaag, manifest, Custom App-doc,
  EVIDENCE) volgt het contract.
