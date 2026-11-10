# ADR-0009 — Contract 1.3 en Peil 1.1.1 in MSO: kopieën, drifttests, Hub-adres en ondernavigatie

**Status:** aangenomen · 2026-11-10 · conformiteitsronde contract 1.3 (alleen de punten die
`docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md` §7 voor MSO als **NU** markeert) · vervangt op één punt ADR-0002
("Auth (P3-06)", zie punt 6) en verfijnt ADR-0006 punt 1 (adresregels).

## Context

Contract 1.3 (`lvos04/ai-server-manager@0a7143b`, inclusief de reviewcorrecties RV1–RV13) en Peil 1.1.1 zijn
vastgesteld. De conformiteitslijst noemt voor MSO:

| ID | Wat | Moment | Deze ronde |
|---|---|---|---|
| C1.3-MSO-01 | kopie 1.3 + drifttest; EVIDENCE-test volgens §11 | NU | gebouwd |
| C1.3-MSO-02 | `systeem.hub_url` valideren volgens §3.2 (ongeldig of leeg → geen link) | NU (nalopen) | gebouwd |
| C1.3-MSO-03 | healthcheck naar 568 + no_new_privs; bewaker legt de bounding set; rooktest | P3 | open (EVIDENCE) |
| C1.3-MSO-04 | connectorlaag P3-07 volgens 1.3; DELTA_MATRIX P3-06 noemt nog enrollment | P3 | open; doc-correctie (punt 6) |
| C1.3-MSO-05 | terugkeerpad na inloggen (§4.1) | P3 | open (EVIDENCE) |
| C1.3-MSO-06 | manifest (nieuw) | P3 | open (EVIDENCE) |
| C1.3-ALG-05 | Peil-verbeterpunten alleen via Peil 1.1.1 | na Peil 1.1.1 (= nu) | kopieën + E14/E16 (punt 5) |

De **HARDE EIS** (contract §2.3 regel 7): geen wijziging mag kijkgeschiedenis of MediaGuard-voortgang raken. Deze ronde
verandert niets aan connectors, MediaGuard-routes, jobs, migraties of de database: alleen documentkopieën, een
validatie van één instelling, CSS/markup van de ondernavigatie, tests en CI. OpenAPI is byte-gelijk.

## Besluiten

1. **Eén gereedschap voor alle versie-gepinde kopieën: `scripts/bronkopie.py`** (alleen standaardbibliotheek), met per
   soort een pinbestand: `docs/platform-v2/HERKOMST_CONTRACT.json` (contract) en `design/ontwerptaal/HERKOMST.json`
   (Peil). Daar staan versie, bronrepo, branch, **volledige bron-commit** en de sha256 per bestand; nergens anders.
   `controleer` (offline) draait in `make lint`, in CI (job python) en in de unit-tests; `vergelijk --online` draait in
   CI tegen GitHub; `vergelijk --bron <checkout>` lokaal. Een onbereikbare bron geeft uitgang 3 (**onbekend**, nooit
   "in orde"). *Alternatief:* de sha256 alleen als constante in een test (zoals home-control-hub) — blijft daarnaast
   bestaan in `test_contract_kopie.py`, maar vergelijkt niet met de bron. *Alternatief:* vergelijken met de werkboom
   van de naastliggende bron — verworpen: dan faalt de test lokaal bij elke wijziging van een andere bouwer.
2. **Driftregel per soort.** Peil volgt ONTWERPTAAL §14 (E08) letterlijk: afwijking van de gepinde inhoud = fout,
   **dezelfde versie met andere inhoud in de bron = fout**, nieuwere versie = waarschuwing. Voor het contract zegt 1.3
   (kop) alleen "sha256 van beide tegen de bron (vaste bron-commit)" en "een nieuwe kopie overnemen is een stap van de
   orkestratie". Gekozen (veiligste lezing zonder dat andermans commit deze CI rood maakt): afwijking van de pin of een
   pin die niet klopt met de gepinde commit = **fout**; andere inhoud of een nieuwere versie op de bronbranch =
   **waarschuwing** met de melding dat de orkestratie een nieuwe kopie moet overnemen. Dit is relevant omdat
   `CONTRACT_1.3_CONFORMITEIT.md` tijdens de ronde nog kan veranderen zonder dat de contractversie verandert.
3. **Hub-adres volgens §3.2 (C1.3-MSO-02).** Kern van het contract: IPv4 alleen als `a.b.c.d` zonder voorloopnul
   (`127.1`, `2130706433`, `0x7f000001`, `010.0.0.1`, `0` ongeldig), IPv6 alleen tussen `[]`, één punt aan het eind
   telt niet mee, nooit loopback, link-local of `0.0.0.0`/`::`. Strenger dan de letter van het contract, omdat de
   waarde alleen een link wordt en een browser de strengere gevallen anders zou lezen of niet kan openen:
   - een naam waarvan het **laatste label een getal** is (`nas.123`, `nas.0x10`) telt als IP-vorm (WHATWG "ends in a
     number"; een browser opent zo'n adres niet);
   - ook `0.0.0.0/8`, multicast, `255.255.255.255`, IPv4-mapped varianten (`[::ffff:127.0.0.1]`) en een zone-ID
     (`%25eth0`) zijn geweigerd;
   - hostnamen alleen als LDH-labels (plus `_`); geen `%`-codering of niet-ASCII (gebruik dan het IP-adres).
   De regel "verboden bereiken ook na DNS-resolutie, bij elke verbinding" heeft in MSO niets te controleren: MSO maakt
   **nooit** een verbinding met dit adres (het is alleen de `href` van "Terug naar de Hub"; `HUB_URL` start in een
   bestaande app geen enrollment, §2.3 regel 5). IPv6 wordt genormaliseerd zoals een browser het schrijft
   (`::ffff:a00:1`), zodat core (`apps/core/mso/settings_registry.py`) en UI (`apps/web/lib/hub.ts`) exact gelijk
   normaliseren; gedeelde testvectoren in `tests/vectoren/hub_url.json`. Een waarde die onder 1.2 is opgeslagen en nu
   ongeldig is, wordt **niet** stil gewijzigd of gewist (de beheerder ziet hem in Instellingen); de UI toont dan geen
   link en opnieuw opslaan geeft 422 met uitleg. Geen migratie.
4. **EVIDENCE-test (C1.3-ALG-02).** `apps/core/tests/test_evidence_contract.py` volgt §11 en is op drie punten strikter
   dan het voorbeeld van de Hub: `Sim-getest`/`Unit-getest` niet bij Testmatrix-ID's (de tabel van §11 noemt daar
   `Sim-geslaagd`/`Sim-mislukt`); `Niet getest` nooit naast een getest-waarde; een `C1.3-…`-ID moet echt in de
   gekopieerde conformiteitslijst staan. Excel-ID's van deze repo moeten in `EXCEL_TAKEN.json` staan.
5. **Peil 1.1.1 overnemen (C1.3-ALG-05).** De kopieën (`design/ontwerptaal/`, `apps/web/styles/peil/`) gaan naar 1.1.1
   met kop `HERKOMST — Peil 1.1.1 · bron: lvos04/ai-server-manager@<commit> <pad> · sha256 …`; `check-peil.mjs` eist
   versie én commit. Wat in MSO verandert:
   - **E14:** de aliaslaag zet focusring en scroll-padding zelf terug; de eigen kopie van de focusring in
     `styles/peil-mso.css` (a) is weg (de randkleur bij focus blijft). De MSO-eigen scroll-padding voor de tweede
     kopbalkrij (< 640 px) blijft, omdat de aliaslaag die niet kent.
   - **E16 (COMPONENTEN C-01):** in de ondernavigatie had het actieve item alleen een andere kleur (`text-primary`).
     Nu: balk van 3 px in `--primary` bovenaan + inkt, zonder extra gewicht, als `::before` (een `box-shadow` zou met
     de focusring botsen, die ook `box-shadow` gebruikt). Labels stonden al op tekstbreedte (review stap 5).
   - Basislaag: `:focus-within`-ring op de datumvelden van Kosten, onderstreping van `aria-current`/`aria-pressed` in
     forced colors, `overflow-wrap` op `main` — zonder codewijziging.
   **Niet overgenomen, met reden:** "Verzonden" (E13) is het readback-woord van bediening; MSO's "Verstuurd" is de
   status van een verzoek aan SABnzbd/qBittorrent/MediaGuard en valt onder "Peil-woorden in de core" (repowerk C10, P3);
   ZekerheidLabel met drempels (E11) pas als de API een zekerheid levert; offline/verouderd (E15) vraagt in bestaande
   apps een functionele ronde; `data-schil="kind"` en het statuspaginasjabloon bestaan in MSO niet.
6. **Correctie op ADR-0002 (1.2-punt MS1, C1.3-MSO-04).** ADR-0002 en `DELTA_MATRIX.md` (P3-06, §5 §3, §6) noemen een
   Hub-JWT via JWKS uit enrollment/koppelcode. Contract 1.2/1.3 §2.3 regel 2: **geen enrollment in v1**; P3-06 wordt
   een **machine-sleutel met scopes** (`read`; later `changes:approve` met `expected_preview_hash` +
   `Idempotency-Key`), aangemaakt in de MSO-UI en ingevoerd in de Hub-UI. Dat bouwen is P3; hier alleen vastgelegd
   (DELTA_MATRIX §12) zodat niemand in P3 het verkeerde bouwt.

## Gevolgen

- CI (job python) vergelijkt elke run met de bron op GitHub; een nieuwe Peil-versie geeft een waarschuwing, een stille
  wijziging binnen dezelfde Peil-versie een rode run. Overnemen: `scripts/bronkopie.py bijwerken --soort peil|contract`
  + `PEIL_VERSION`/`PEIL_COMMIT` in `apps/web/scripts/check-peil.mjs` + de constanten in `test_contract_kopie.py`.
- De e2e-suite (`make e2e`) controleert de UI met de 1.1.1-kopieën: axe (wcag2a/aa, 21a/aa, 22aa) = 0 op alle
  schermen, licht en donker (1440 en 390 px; 320, 768 en 1024 px licht), plus de nieuwe ondernavigatietest op 320 en
  390 px. Echte telefoon, schermlezer en NAS: **NIET GEVALIDEERD**.
- P3-punten C1.3-MSO-03…06 staan als `Niet gestart` in `EVIDENCE.json`; niets ervan is gesimuleerd.
