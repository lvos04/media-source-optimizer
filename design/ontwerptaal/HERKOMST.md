# Herkomst van `design/ontwerptaal/` (O-14, ONTWERPTAAL §14)

Versie-gepinde kopie van de ontwerptaal **Peil 1.1.1** (10 november 2026) uit de bron van waarheid
`lvos04/ai-server-manager` (contract 1.3 §13), gepind op **versie én bron-commit**
`0a7143bc0a94ed5317a9655fa785e73a1fb07ba1` (branch `ccr-c133d7a0-kuz1cv`; Peil staat op de werkbranch, niet op `main`).
De pin met de sha256 per bestand staat in [`HERKOMST.json`](HERKOMST.json); dat is de enige plek waar versie, commit en
sha256 vastliggen. Vorige versie: Peil 1.1.0 (9 november 2026, uit de map `definitief/` van de design-agent).

**Status in MSO:** sinds fase 1a gebruikt de web-UI `ontwerptaal.css` en `aliaslagen/mediaguard-mso.css`. Omdat de
Docker-context van het web-image `apps/web` is, staat daar een tweede, byte-gelijke kopie:
`apps/web/styles/peil/ontwerptaal.css` en `apps/web/styles/peil/mediaguard-mso.css` (eigen toelichting in de kop,
zelfde versie, commit en sha256). De omzetting staat in `docs/ontwerp/PEIL_MIGRATIE.md`; de overstap naar 1.1.1 in
`docs/adr/ADR-0009-contract-1-3-en-peil-1-1-1.md`.

| Kopie | Bron in `lvos04/ai-server-manager` | Gebruik in MSO |
|---|---|---|
| `ontwerptaal.css` | `design/ontwerptaal/ontwerptaal.css` | tokens + basislaag (Tailwind 4 `@theme`, shadcn-variabelen) |
| `aliaslagen/mediaguard-mso.css` | `design/ontwerptaal/aliaslagen/mediaguard-mso.css` | na `mg.css`/`mso.css` (gedeeld met MediaGuard); zet sinds 1.1.1 ook focusring en scroll-padding terug (E14) |
| `tokens.json` | `design/ontwerptaal/tokens.json` | tokenbron (DTCG), voor controles |
| `components.json` | `design/ontwerptaal/components.json` | advies voor de shadcn-CLI |
| `ONTWERPTAAL.md` | `docs/platform-v2/ONTWERPTAAL.md` | regels (bindend; MSO: §13.3, §13.4; wijzigingslog §16) |
| `COMPONENTEN.md` | `design/ontwerptaal/COMPONENTEN.md` | componentspecificaties (handoff; 1.1.1: §29) |
| `ADR-ONTWERPTAAL.md` | `docs/adr/ADR-0015-ontwerptaal-peil.md` | het besluit en de alternatieven (1.1.1-a t/m k) |
| `apps/web/styles/peil/ontwerptaal.css` | `design/ontwerptaal/ontwerptaal.css` | wat de app importeert (`app/globals.css`) |
| `apps/web/styles/peil/mediaguard-mso.css` | `design/ontwerptaal/aliaslagen/mediaguard-mso.css` | idem |

- `.css`- en `.md`-kopieën hebben een **herkomstkop van precies 2 regels** (versie, `bron@commit pad`, sha256 van het
  bronbestand, toelichting); alles daaronder is byte-gelijk aan de bron. `tokens.json` en `components.json` kunnen geen
  kop dragen en zijn byte-gelijk.
- Niet gekopieerd (te groot of niet nodig): `prototypes/`, `shots/`, `bouw/`, `controle/`, `onderzoek/`,
  `contrast.json`, `REVIEW.md`, `BESLUITEN_GEBRUIKER.md`, `statuspagina.*` (MSO heeft geen platform-statuspagina) en de
  aliaslagen van my_house en de Hub. Relatieve links in de kopieën naar die bestanden werken alleen in de bron.

## Controleren en bijwerken

```sh
python3 scripts/bronkopie.py controleer                       # offline: koppen, inhoud en pins (CI, job python)
python3 scripts/bronkopie.py vergelijk --online               # tegen GitHub (CI, job python)
python3 scripts/bronkopie.py vergelijk --bron ../ai-server-manager   # tegen een checkout ernaast
(cd apps/web && npm run check:peil)                           # de kopieën in de Docker-context (CI, job web)
```

Driftregel (ONTWERPTAAL §14, E08): CI **faalt** bij een afwijking van de gepinde inhoud en bij **dezelfde versie met
andere inhoud** in de bron; een **nieuwere** bronversie geeft alleen een **waarschuwing**. Bijwerken is een bewuste stap:

```sh
python3 scripts/bronkopie.py bijwerken --soort peil --bron ../ai-server-manager --commit <40 hex>
# daarna PEIL_VERSION/PEIL_COMMIT in apps/web/scripts/check-peil.mjs, de eigen tests en make e2e (axe = 0)
```

Hetzelfde gereedschap bewaakt de byte-gelijke contractkopie (`docs/platform-v2/HERKOMST_CONTRACT.json`).
