# TrueNAS Home Platform V2 — documentatie voor `media-source-optimizer`

Bron: uitvoeringspakket `TrueNAS_Home_Platform_V2_VOLLEDIG_2026-11-09.zip` (peildatum 9 november 2026).

**Rol van deze repo (vast besluit):** B05 delta op 1.6.0-HEAD: alleen ontbrekende punten.

Hier staan alleen de eigen B-, I-, A- en promptdocumenten plus de gedeelde architectuur (`00_START`, `01_ARCHITECTUUR`, `05_TESTS`). Het volledige pakket en de Excel staan in `lvos04/ai-server-manager` onder `docs/platform-v2/`.

Onbekende NAS-gegevens (D01–D18) worden nooit met voorbeeldwaarden ingevuld; ze worden via de onboarding-wizard in de web-UI vastgelegd.

## Bestanden

- `00_START/BESLUITEN_EN_ONBEKENDEN.md`
- `00_START/INHOUDSOPGAVE.md`
- `00_START/INHOUD_CONTROLE.json`
- `00_START/LEES_MIJ_EERST.md`
- `00_START/SHA256SUMS.txt`
- `01_ARCHITECTUUR/01_PROJECTCHARTER.md`
- `01_ARCHITECTUUR/02_DATA_EIGENAARSCHAP.md`
- `01_ARCHITECTUUR/03_RISICO_AUTONOMIE.md`
- `01_ARCHITECTUUR/04_GEGEVENS_EN_STORAGE.md`
- `01_ARCHITECTUUR/05_OPERATIES_EN_UPDATES.md`
- `01_ARCHITECTUUR/06_FASERING_EN_GATES.md`
- `01_ARCHITECTUUR/07_API_EN_EVENTSCHEMA.md`
- `01_ARCHITECTUUR/08_UI_EN_INTERACTIES.md`
- `02_BOUWPLANNEN/B05_MSO_DELTA.md`
- `03_INSTALLATIE/I04_MEDIA_SOURCE_OPTIMIZER.md`
- `03_INSTALLATIE/I17_SABNZBD.md`
- `03_INSTALLATIE/I18_QBITTORRENT.md`
- `04_BESTAANDE_APPS/A13_SABNZBD.md`
- `04_BESTAANDE_APPS/A14_QBITTORRENT.md`
- `04_BESTAANDE_APPS/A19_MEDIA_SOURCE_OPTIMIZER.md`
- `05_TESTS/GEBRUIKERSWENSEN_TRACEERBAARHEID.md`
- `05_TESTS/INTEGRALE_E2E_TESTMATRIX.md`
- `06_TEMPLATES/PROMPTS_PER_APP/PROMPT_B05_MSO_DELTA.md`

## Eigen werkstukken van deze repo (fase P0)

- `INVENTARIS.md` — inventaris van MSO 1.6.0 (modules, 211 API-routes met bestand:regel, migraties, auth, events,
  back-up/rollback, monitoring, veiligheidsinvarianten, CI, images, releasestatus, baselinetestrun).
- `DELTA_MATRIX.md` — B05-eisen, I04/I17/I18, A13/A14/A19 en contract 1.1: bestaand / onvolledig / ontbrekend, met
  bewijs en de geprioriteerde bouwlijst voor P3; §12: de stand tegenover contract 1.3.
- `EVIDENCE.json` — Excel-status en validatie voor B05-001 … B05-010 en de contractpunten `C1.3-…` van deze repo
  (contract 1.3 §11; bewaakt door `apps/core/tests/test_evidence_contract.py`).
- Besluiten: `docs/adr/ADR-0001` … `ADR-0009` (contract 1.3 en Peil 1.1.1: ADR-0009); OpenAPI-momentopname:
  `docs/openapi/media-source-optimizer.openapi.json` (bewaakt door `apps/core/tests/test_openapi_export.py`).

## Platformcontract (byte-gelijke kopie, niet lokaal aanpassen)

- `PLATFORM_CONTRACTEN.md` (`contracts 1.3`) en `CONTRACT_1.3_CONFORMITEIT.md` (wat elke repo moet veranderen; MSO:
  §7) zijn byte-gelijk aan `lvos04/ai-server-manager`, gepind op een vaste bron-commit in `HERKOMST_CONTRACT.json`.
- Drifttest: `apps/core/tests/test_contract_kopie.py` (sha256) en `scripts/bronkopie.py controleer` / `vergelijk
  --online` (CI, job python). Een nieuwe kopie overnemen is een stap van de orkestratie:
  `python3 scripts/bronkopie.py bijwerken --soort contract --bron <checkout van ai-server-manager> --commit <40 hex>`.
- De ontwerptaal Peil (1.1.1) staat gepind in `design/ontwerptaal/` (`HERKOMST.md`, `HERKOMST.json`).
