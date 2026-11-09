# Herkomst van `design/ontwerptaal/` (O-14)

Versie-gepinde kopie van de ontwerptaal **Peil 1.1.0** (9 november 2026, design-agent; goedgekeurd na de onafhankelijke
ontwerpreview 1.0.0 → 1.1.0). De bron van waarheid wordt na de overdracht
`ai-server-manager/docs/platform-v2/ONTWERPTAAL.md` + `ai-server-manager/design/ontwerptaal/` (contract 1.2 §13). Tot die
commit bestaat staat de bron in de map `definitief/` van de ontwerptaal (buiten de repos).

**Status in MSO:** alleen referentie. Niets hiervan wordt nu door de app geïmporteerd; de omzetting staat in
[`docs/ontwerp/PEIL_MIGRATIE.md`](../../docs/ontwerp/PEIL_MIGRATIE.md).

| Bestand | Gebruik in MSO | sha256 van het bronbestand |
|---|---|---|
| `aliaslagen/mediaguard-mso.css` | fase 1a: na `mg.css`/`mso.css` importeren (gedeeld met MediaGuard) | `b120cf9a41357dc3cc4981db24c08ae6f557a33faa4f62512ba8f45a27124a4c` |
| `ontwerptaal.css` | fase 1a: vóór `mg.css`; fase 2: Tailwind 4 `@theme` + shadcn-variabelen | `b384b3d074c7c892d8e5a8320b1bdcd3d8612d98b3a05daa9a9e6d12560d020e` |
| `tokens.json` | tokenbron (DTCG), voor controles | `9583506d8585ba39486f75d4c27f8e40f7878104674c27e229d10f92013a30e1` |
| `components.json` | fase 2: advies voor de shadcn-CLI (paden aanpassen: `css: "app/globals.css"`) | `831fdce06c6c25c7f67d23b737f27184bca9e4a0ff5c0a60175a6a58edbe4c7e` |
| `ONTWERPTAAL.md` | regels (bindend; MSO: §13.3 en §13.4) | `a957370b1eceac49ab9f47204c252a67d15967ebae240eb33440b8963898d33b` |
| `COMPONENTEN.md` | componentspecificaties (handoff) | `d0b0999d8f07537fcc4447d30ab5a3d45632d559487736082beae8a3c28b0154` |
| `ADR-ONTWERPTAAL.md` | het besluit en de alternatieven | `df302eb5a727b5649dafec1c0c0d469cf5bfc39fd32b3834c415aaa805db05ac` |

- `.css`- en `.md`-bestanden hebben een **herkomstkop van 2 regels** bovenaan; de rest is byte-gelijk aan de bron.
  `tokens.json` en `components.json` kunnen geen kop dragen en zijn byte-gelijk.
- Niet gekopieerd (te groot of niet nodig in deze repo): `prototypes/`, `shots/`, `bouw/`, `controle/`, `contrast.json`,
  `statuspagina.*` (MSO heeft geen platform-statuspagina) en de aliaslagen van my_house en de Hub.
- Zelfde indeling en koppen als de kopie in `my_house` (zelfde bronbestanden, zelfde sha256 voor de gedeelde
  bestanden).

## Controleren

```sh
cd design/ontwerptaal
for f in aliaslagen/mediaguard-mso.css ontwerptaal.css ONTWERPTAAL.md COMPONENTEN.md ADR-ONTWERPTAAL.md; do
  echo "$(tail -n +3 "$f" | sha256sum | cut -d' ' -f1)  $f"; done
sha256sum tokens.json components.json
```

De uitkomst moet gelijk zijn aan de tabel. Een CI-stap die dit vergelijkt met de versie in `ai-server-manager` komt
pas als die bron gecommit is (ONTWERPTAAL §14); tot dan is dit een handmatige controle.
